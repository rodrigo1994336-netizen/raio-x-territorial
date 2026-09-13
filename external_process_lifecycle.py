from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from typing import Any, Callable, Sequence

_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[int, subprocess.Popen[bytes]] = {}
_SERVER_STOPPING = threading.Event()


class ManagedProcessCancelled(RuntimeError):
    pass


class RequestDisconnected(RuntimeError):
    pass


class ManagedOperationTimeout(RuntimeError):
    pass


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return _SERVER_STOPPING.is_set() or bool(cancel_event and cancel_event.is_set())


def active_child_pids() -> list[int]:
    with _ACTIVE_LOCK:
        return sorted(pid for pid, proc in _ACTIVE.items() if proc.poll() is None)

def _stop_process(proc: subprocess.Popen[bytes], grace_seconds: float = 0.35) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace_seconds)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace_seconds)
    except Exception:
        pass


def terminate_active_processes() -> list[int]:
    _SERVER_STOPPING.set()
    with _ACTIVE_LOCK:
        items = list(_ACTIVE.items())
    killed: list[int] = []
    for pid, proc in items:
        if proc.poll() is None:
            killed.append(pid)
            _stop_process(proc)
    return killed

def run_managed_process(
    args: Sequence[str],
    *,
    timeout_seconds: float | None,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if _is_cancelled(cancel_event):
        raise ManagedProcessCancelled("operation_cancelled_before_spawn")
    proc = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with _ACTIVE_LOCK:
        _ACTIVE[proc.pid] = proc
    deadline = None if timeout_seconds is None else time.monotonic() + max(0.05, float(timeout_seconds))
    try:
        while True:
            if _is_cancelled(cancel_event):
                raise ManagedProcessCancelled("operation_cancelled")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(list(args), timeout_seconds)
            try:
                stdout, stderr = proc.communicate(timeout=0.10 if remaining is None else min(0.10, remaining))
                return subprocess.CompletedProcess(list(args), proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if proc.poll() is None:
            _stop_process(proc)
        with _ACTIVE_LOCK:
            _ACTIVE.pop(proc.pid, None)


async def _drain_thread(task: asyncio.Task[Any], timeout_seconds: float = 2.0) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except BaseException:
        pass

async def run_sync_with_request_lifecycle(
    request: Any,
    func: Callable[..., Any],
    *args: Any,
    timeout_seconds: float | None,
    **kwargs: Any,
) -> Any:
    cancel_event = threading.Event()
    task = asyncio.create_task(
        asyncio.to_thread(func, *args, cancel_event=cancel_event, **kwargs)
    )
    deadline = None if timeout_seconds is None else time.monotonic() + max(0.05, float(timeout_seconds))
    try:
        while not task.done():
            if request is not None and await request.is_disconnected():
                cancel_event.set()
                await _drain_thread(task)
                raise RequestDisconnected("client_disconnected")
            if deadline is not None and time.monotonic() >= deadline:
                cancel_event.set()
                await _drain_thread(task)
                raise ManagedOperationTimeout("operation_timeout")
            await asyncio.wait({task}, timeout=0.10)
        return await task
    except asyncio.CancelledError:
        cancel_event.set()
        await _drain_thread(task)
        raise


def install_shutdown_cleanup(app: Any) -> None:
    if getattr(app.state, "rx_external_process_cleanup_installed", False):
        return
    app.state.rx_external_process_cleanup_installed = True

    async def _shutdown_cleanup() -> None:
        killed = terminate_active_processes()
        if killed:
            print(f"RX_EXTERNAL_PROCESS_SHUTDOWN_KILLED={len(killed)}", flush=True)

    # Starlette 1.x removed add_event_handler; router.on_shutdown works on 0.x and 1.x.
    app.router.on_shutdown.append(_shutdown_cleanup)

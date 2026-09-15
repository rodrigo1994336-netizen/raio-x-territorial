from __future__ import annotations

import asyncio
import contextvars
import os
import subprocess
import threading
import time
from typing import Any, Awaitable, Callable, Sequence

_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[int, subprocess.Popen[bytes]] = {}
_SERVER_STOPPING = threading.Event()
# M1: escopos de cancelamento herdados pelo contexto. asyncio.to_thread e create_task copiam o contexto, então
# um processo gerenciado nascido numa thread abandonada por um prazo (wait_for sobre to_thread) enxerga o
# cancelamento do escopo mesmo quando quem chamou não repassou cancel_event.
_SCOPES: contextvars.ContextVar[tuple[threading.Event, ...]] = contextvars.ContextVar("rx_external_process_scopes", default=())


class ManagedProcessCancelled(RuntimeError):
    pass


class RequestDisconnected(RuntimeError):
    pass


class ManagedOperationTimeout(RuntimeError):
    pass


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    if _SERVER_STOPPING.is_set() or bool(cancel_event and cancel_event.is_set()):
        return True
    return any(scope.is_set() for scope in _SCOPES.get())


def _open_scope(event: threading.Event) -> contextvars.Token:
    return _SCOPES.set(_SCOPES.get() + (event,))


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
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if _is_cancelled(cancel_event):
        raise ManagedProcessCancelled("operation_cancelled_before_spawn")
    # input_bytes: entrada padrão pequena (cabeçalhos da ponte, br_bridge); sem ela, o processo
    # nasce exatamente como antes (sem pipe de entrada).
    proc = subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE if input_bytes is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with _ACTIVE_LOCK:
        _ACTIVE[proc.pid] = proc
    if input_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_bytes)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.stdin = None
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
        if os.name == "posix":
            # M1: communicate() interrompido por prazo ou cancelamento deixa os pipes abertos enquanto alguém
            # segurar o processo (uma exceção guardada em log, estado ou tarefa segura o quadro que o segura).
            # Fecha aqui, na hora, sem depender de quem guarda o quê nem do coletor de ciclos.
            # (No Windows as threads leitoras do communicate fecham os próprios pipes.)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
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
    token = _open_scope(cancel_event)
    try:
        # a tarefa copia o contexto agora: processos gerenciados de chamadas internas sem cancel_event também caem
        task = asyncio.create_task(
            asyncio.to_thread(func, *args, cancel_event=cancel_event, **kwargs)
        )
    finally:
        _SCOPES.reset(token)
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


async def wait_for_cancelling_processes(aw: Awaitable[Any], timeout: float | None) -> Any:
    """asyncio.wait_for que, ao estourar o prazo, ser cancelado ou falhar, cancela os processos externos
    gerenciados nascidos dentro dele.

    O wait_for sozinho abandona viva a thread de asyncio.to_thread, e com ela o curl filho até o limite
    próprio (até 45 s). Passe a corrotina ainda não iniciada (asyncio.to_thread(...) ou chamada async): a
    tarefa é criada aqui, dentro do escopo, para herdar o cancelamento. Devolve no prazo, como o wait_for;
    o filho cai em até ~0,1 s + a carência de parada, na thread que o criou."""
    event = threading.Event()
    token = _open_scope(event)
    try:
        task = asyncio.ensure_future(aw)
    finally:
        _SCOPES.reset(token)
    try:
        return await asyncio.wait_for(task, timeout)
    except BaseException:
        event.set()
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

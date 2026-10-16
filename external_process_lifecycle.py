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


def scope_cancelled(cancel_event: threading.Event | None = None) -> bool:
    """A consulta em curso já foi abandonada (prazo, desistência do cliente ou desligamento)?

    É o mesmo teste que o run_managed_process faz antes de nascer um filho, exposto para quem NÃO abre
    processo: a thread abandonada continua viva depois do prazo, e o que ela faz depois disso — gravar num
    cache de processo, abrir a próxima consulta, segurar uma trava — chega ao cliente SEGUINTE. Tentativa
    cancelada não é resposta: quem escreve em estado compartilhado pergunta isto antes de escrever."""
    return _is_cancelled(cancel_event)


def _open_scope(event: threading.Event) -> contextvars.Token:
    return _SCOPES.set(_SCOPES.get() + (event,))


def in_current_scope(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Embrulha `fn` para o trabalhador de um pool enxergar o escopo de cancelamento de quem submeteu.

    `ThreadPoolExecutor.submit`, `.map` e `loop.run_in_executor` NÃO copiam o contexto (só
    `asyncio.to_thread` e `create_task` copiam): sem isto o curl do trabalhador nasce fora do escopo e
    sobrevive ao prazo e à desistência do cliente. O contexto é lido AQUI, na thread de quem submete; o
    trabalhador só põe e tira o escopo no contexto da própria thread (uma cópia de Context não serve para
    `.map`: o mesmo Context não pode ser entrado por dois trabalhadores ao mesmo tempo)."""
    scopes = _SCOPES.get()

    def runner(*args: Any, **kwargs: Any) -> Any:
        token = _SCOPES.set(scopes)
        try:
            return fn(*args, **kwargs)
        finally:
            _SCOPES.reset(token)

    return runner


def active_child_pids() -> list[int]:
    with _ACTIVE_LOCK:
        return sorted(pid for pid, proc in _ACTIVE.items() if proc.poll() is None)

# Carência de parada de um filho: terminate + espera, e se não morrer, kill + espera. Quem deriva teto de
# tempo (car_resilient.WORST_CASE_SECONDS) soma isto, porque o filho ainda custa depois de o prazo estourar.
STOP_GRACE_SECONDS = 0.35
STOP_GRACE_TOTAL_SECONDS = STOP_GRACE_SECONDS * 2
# Passo do laço de communicate(): o prazo e o cancelamento são vistos com esta granularidade.
PROCESS_POLL_SECONDS = 0.10
# O que cada processo gerenciado pode custar ALÉM do prazo pedido, no pior caso.
STOP_OVERHEAD_SECONDS = STOP_GRACE_TOTAL_SECONDS + PROCESS_POLL_SECONDS


def _stop_process(proc: subprocess.Popen[bytes], grace_seconds: float = STOP_GRACE_SECONDS) -> None:
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
                stdout, stderr = proc.communicate(
                    timeout=PROCESS_POLL_SECONDS if remaining is None else min(PROCESS_POLL_SECONDS, remaining))
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


DISCONNECT_POLL_S = 0.10
WATCH_FAILURES: dict[str, int] = {}


async def _watch_disconnect(request: Any, task: "asyncio.Future[Any]", event: threading.Event,
                            seen: list[bool]) -> None:
    """Vigia a desistência do cliente enquanto a consulta corre.

    Medido em 15/09 com uvicorn: uma rota `async def` SEM o parâmetro `request` roda até o fim depois de o
    cliente fechar a conexão (o servidor não cancela a tarefa do handler sozinho); com `request.is_disconnected()`
    a desistência aparece em ~0,1 s. Sem esta vigia, "o cliente desistiu" nunca chegava à cadeia.

    ⚠️ SÓ EM ROTA QUE NÃO VAI LER O CORPO DA REQUISIÇÃO DEPOIS. `is_disconnected()` faz um `receive()` no
    canal ASGI e CONSOME a mensagem que estiver esperando: numa rota que ainda fosse ler o corpo (POST com
    leitura dentro do handler), um pedaço de `http.request` seria descartado em silêncio. Nas rotas GET de
    hoje, e em rota cujo corpo o FastAPI já leu por inteiro antes do handler, só resta `http.disconnect`."""
    try:
        while not task.done():
            if await request.is_disconnected():
                seen.append(True)
                event.set()          # derruba os processos gerenciados já nascidos
                task.cancel()        # e impede que a cadeia abra a consulta seguinte
                return
            await asyncio.sleep(DISCONNECT_POLL_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        # A vigia nunca pode derrubar a consulta: sem ela o comportamento volta a ser o de antes (só prazo).
        # Mas morrer calada esconde a degradação de quem opera: conta e registra (só o tipo, nada do cliente).
        name = type(exc).__name__
        WATCH_FAILURES[name] = WATCH_FAILURES.get(name, 0) + 1
        print(f"RX_DISCONNECT_WATCH_FAILED={name}:{WATCH_FAILURES[name]}", flush=True)
        return


def _outer_cancel_requested() -> bool:
    """O cancelamento veio DE FORA (o servidor cancelou este handler), e não da vigia?

    `Task.cancelling()` (3.11+) conta os cancelamentos pedidos à tarefa atual e ainda não desfeitos. O
    `wait_for` desfaz o dele antes de levantar TimeoutError, então aqui só resta cancelamento de terceiros."""
    try:
        task = asyncio.current_task()
        return bool(task is not None and task.cancelling())
    except Exception:  # noqa: BLE001 - Python sem cancelling(): trata como cancelamento da vigia, como antes
        return False


async def wait_for_cancelling_processes(aw: Awaitable[Any], timeout: float | None, *, request: Any = None) -> Any:
    """asyncio.wait_for que, ao estourar o prazo, ser cancelado ou falhar, cancela os processos externos
    gerenciados nascidos dentro dele.

    Com `request`, a desistência do cliente entra no mesmo escopo: os filhos vivos caem e a cadeia não abre
    consulta nova (levanta RequestDisconnected). Sem `request`, o comportamento é exatamente o de antes.

    O wait_for sozinho abandona viva a thread de asyncio.to_thread, e com ela a cadeia inteira de curl que
    ela ainda roda: no fetch_car_live_resilient com o SICAR travado são 10 curls de até 11 s em sequência
    (~110 s). Devolve no prazo, como o wait_for; o filho cai em até ~0,1 s + a carência de parada, na
    thread que o criou, e a thread não cria outro.

    Só aceita corrotina ainda não iniciada (asyncio.to_thread(...) ou chamada async): a tarefa é criada
    aqui, dentro do escopo, e herda o cancelamento. Tarefa ou Future criada antes já copiou o contexto sem
    o escopo e passaria sem proteção, em silêncio — por isso é recusada com TypeError.

    O escopo viaja pelo contexto (asyncio.to_thread e create_task o copiam). Não atravessa
    ThreadPoolExecutor.submit/.map, loop.run_in_executor nem threading.Thread: quem usa pool embrulha cada
    trabalho em `in_current_scope(...)` (conferido pela regra estática `copia_de_escopo_no_pool`), e quem
    abre thread própria repassa cancel_event até o run_managed_process. Processo criado fora do
    run_managed_process (subprocess.run direto) não é alcançado: fica limitado só pelo timeout dele."""
    if asyncio.isfuture(aw):
        raise TypeError("wait_for_cancelling_processes: passe a corrotina, não tarefa/Future já criada (ela não herda o escopo)")
    event = threading.Event()
    token = _open_scope(event)
    try:
        task = asyncio.ensure_future(aw)
    finally:
        _SCOPES.reset(token)
    seen: list[bool] = []
    watcher = None if request is None else asyncio.ensure_future(_watch_disconnect(request, task, event, seen))
    try:
        return await asyncio.wait_for(task, timeout)
    except asyncio.CancelledError as exc:
        event.set()
        # A vigia cancela a tarefa quando o cliente desiste: aí este cancelamento É a desistência.
        # Cancelamento pedido de fora (desligamento do servidor) continua sendo cancelamento — convertê-lo
        # deixaria o handler vivo levantando HTTPException em vez de morrer. E a original viaja junto
        # ("from exc"), para o log guardar a causa.
        if seen and not _outer_cancel_requested():
            raise RequestDisconnected("client_disconnected") from exc
        raise
    except BaseException:
        # Erro de verdade continua sendo o erro de verdade, mesmo que o cliente tenha desistido no mesmo
        # instante: convertê-lo em 499 apagaria a causa do log.
        event.set()
        raise
    finally:
        if watcher is not None and not watcher.done():
            watcher.cancel()


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

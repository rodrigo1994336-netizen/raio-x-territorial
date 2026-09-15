"""Gate M1 · processos pendurados: nenhum filho sobra depois de consulta abandonada ou prazo estourado, e a
medição RX_PROC_STATS diz o número de verdade (ou "indisponivel"), sem dado sensível.

Substitui o f1_process_lifecycle_gate (preservado fora do repo em 13/09, com corrida no cenário de prazo).
Causa da corrida: o prazo de 0,30 s era contado desde a chamada, antes de o filho existir, e o teste procurava
o filho por amostragem de 20 ms no registro. Com >= ~0,3 s entre a chamada e o nascimento do filho (runner
carregado, antivírus), o cancelamento chegava antes do nascimento (ou o filho vivia menos que a amostragem)
e o gate reprovava com "managed child did not start" sem haver filho vivo — o produto estava certo, o teste
confundia "ainda não nasceu" com "não nasceu". Reproduzido injetando atraso no nascimento: 0,27 s passa,
0,32 s reprova. Aqui o prazo só anda quando o gate já viu o filho vivo no sistema: o relógio do módulo fica
parado até lá (regra prazo_estourado) e o wait_for do escopo só dispara depois de o filho existir.

O que conta como "vivo" é o sistema operacional: /proc (processo com ppid = este gate, inclusive zumbi),
não o registro do próprio módulo. Por isso as regras de processo exigem Linux; no CI o gate roda com
--exigir-linux e reprova se não houver /proc. No Windows roda o que dá e termina PARCIAL (nunca PASS).

Segunda corrida da mesma família (revisão de 15/09): no /proc o filho aparece desde o fork, mas só entra no
registro depois que o Popen retorna. Afirmar "está no registro" ou "o desligamento o mirou" logo que o /proc
mostra o filho reprovava com o produto certo (reproduzido com 50 ms e 0,3 s entre nascimento e registro).
Agora a espera é pelas duas coisas (born_registered), e a regra corrida_nascimento_registro injeta atraso
nas duas passagens (Popen -> registro e registro -> laço de cancelamento) e tem de PASSAR, com a prova de
que o instrumento viu a janela.

Regras
  abandono_desconecta        cliente desconecta com o filho vivo -> o filho some (e sai do registro).
  prazo_estourado            prazo do servidor com o filho vivo (relógio controlado) -> o filho some.
  cancelado_antes_de_nascer  cancelamento anterior não chega a chamar o Popen.
  desligamento               com a thread da consulta ocupada, o desligamento mata o filho registrado antes
                             de voltar; e depois dele nenhum processo novo nasce.
  corrida_nascimento_registro atraso de 0,05 s e 0,3 s entre Popen e registro e entre registro e laço: abandono
                             e desligamento continuam certos (a janela foi vista pelo instrumento).
  descritores                cancelar e estourar prazo não deixam pipe aberto, mesmo com a exceção guardada por
                             quem chamou (coletor de ciclos desligado).
  escopo_da_rota             função da rota que não repassa cancel_event: o escopo derruba o filho mesmo assim.
  escopo_prazo_to_thread     wait_for_cancelling_processes sobre asyncio.to_thread: prazo estourado derruba o
                             filho da thread abandonada; a prova do defeito (asyncio.wait_for puro deixa o
                             filho vivo); e tarefa/Future criada antes é recusada com TypeError.
  cadeia_car_desconecta      cadeia real do SICAR (car_resilient -> deploy_app._curl -> br_bridge ->
                             run_managed_process) com curl falso travado: desconexão -> nenhum curl sobra.
  cadeia_car_prazo           os quatro handlers reais com prazo sobre o SICAR (quick_analysis_v24,
                             quick_analysis, embargos_detail, critical_minerals_v34) com curl falso travado e
                             prazo controlado: o curl do prazo some; sem fallback, nenhum outro nasce.
  cadeia_slots_relatorio     _timed do relatório (extras) e _bounded (nova tentativa) com curl travado.
  inventario_processos       todo subprocess.run/call/check_* dos módulos do servidor tem timeout; Popen só
                             no módulo de ciclo de vida; sem os.system/os.popen/fork/exec/spawn/posix_spawn/
                             getoutput/subprocess_exec/multiprocessing; lista declarada dos módulos com
                             chamada direta (inclusive quem passa br_bridge.subprocess_runner); nenhum
                             asyncio.wait_for puro sobre função que chega a run_managed_process (fecho
                             transitivo por nome, não lista à mão); os pontos com escopo contados.
  medicao_indisponivel       sem /proc do próprio processo, todo número do sistema é "indisponivel", nunca 0;
                             idade negativa, descendente vivo sem memória legível e entrada ilegível do /proc
                             viram "indisponivel"; sem descendentes, memória deles é null.
  medicao_real               filhos, zumbis, idade, nomes, gerenciados, threads, descritores, memória e tarefas
                             batem com provas independentes.
  sem_dado_sensivel          rota e log sem argumento, variável de ambiente ou caminho; só os campos declarados
                             (sem PID), com tipos fixos e nomes de executável no padrão curto.
  rota_protegida             sem RX_DIAG_TOKEN, com token curto, sem cabeçalho, com cabeçalho errado ou com o
                             token na URL: o mesmo 404 de endereço inexistente; com o segredo: 200 sem cache e
                             medição reaproveitada por 5 s.
  config_intervalo           RX_PROC_STATS_MIN: piso de 1 min, 0 desliga, inválido/negativo/nan/inf -> padrão
                             com aviso RX_PROC_STATS_CONFIG.
  log_arranque_periodico     linha no arranque e a cada intervalo; a repetição para no desligamento.
  servicos_ligados           o app servido (report_api, que o portal importa) tem rota, log e limpeza.
  ci_roda_o_gate             o quality-gate roda este gate em Linux com --exigir-linux.

Controles positivos (Linux): cada mutação é aplicada no ARQUIVO, num processo novo que importa a cópia mutada
antes da original, e tem de reprovar pela regra dela e pelo motivo dela.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import gc
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTANT_DIR = os.environ.get("RX_M1_MUTANT_DIR")
for entry in ([str(ROOT)] + ([MUTANT_DIR] if MUTANT_DIR else [])):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)  # a cópia mutada (se houver) fica antes da original
os.environ.setdefault("RX_RELEASE", "OFF")
os.environ.setdefault("RX_RASTERIO_RUNTIME_INSTALL", "off")

LINUX = sys.platform.startswith("linux") and Path("/proc/self/stat").exists()
GRACE_S = 3.0
CAR = "MG-3120904-00000000000000000000000000000001"
RULES: dict[str, tuple[object, bool]] = {}


def regra(name: str, linux: bool = False):
    def deco(fn):
        RULES[name] = (fn, linux)
        return fn
    return deco


# ------------------------------------------------------------------ instrumento independente (não usa rx_proc_stats)
def os_children() -> dict[int, tuple[str, str]]:
    """Filhos deste processo segundo o /proc: pid -> (nome, estado). Zumbi conta (estado Z)."""
    me = os.getpid()
    out: dict[int, tuple[str, str]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            text = Path(f"/proc/{name}/stat").read_text()
        except OSError:
            continue
        left, right = text.find("("), text.rfind(")")
        rest = text[right + 2:].split()
        if len(rest) > 1 and int(rest[1]) == me:
            out[int(name)] = (text[left + 1:right], rest[0])
    return out


def os_alive(pid: int) -> bool:
    if LINUX:
        return Path(f"/proc/{pid}").exists()
    import ctypes  # Windows (parcial local)
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259


def wait_until(pred, timeout: float, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


async def await_until(pred, timeout: float, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return bool(pred())


def sleep_cmd() -> list[str]:
    return ["sleep", "300"] if LINUX else [sys.executable, "-c", "import time; time.sleep(300)"]


def epl():
    import external_process_lifecycle
    return external_process_lifecycle


def registered() -> list[int]:
    return epl().active_child_pids()


FAKE_CURL_STOP: list[Path] = []  # arquivo que faz o curl falso sair na hora (só numa reprovação)
WINDOW = {"fora_do_registro": 0}  # vezes em que o instrumento viu o filho no sistema e ainda fora do registro


def kill_leftovers(pids=()) -> None:
    """Numa reprovação: mata o que sobrou (senão a thread abandonada prende o processo do gate por minutos)."""
    for stop in FAKE_CURL_STOP:  # uma cadeia mutada que cria curl sem dono não cria outro travado
        with contextlib.suppress(OSError):
            stop.touch()
    epl().terminate_active_processes()  # também faz as threads em curso desistirem de criar outro
    for pid in pids:
        with contextlib.suppress(Exception):
            if os_alive(pid):
                if LINUX:
                    os.kill(pid, 9)
                    wait_until(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid, 2.0)
                else:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)


def fail(msg: str, pids=()) -> None:
    kill_leftovers(pids)
    raise AssertionError(msg)


def assert_gone(pid: int, context: str) -> float:
    t0 = time.monotonic()
    wait_until(lambda: not os_alive(pid) and pid not in registered(), GRACE_S)
    if os_alive(pid):
        state = os_children().get(pid) if LINUX else None
        fail(f"{context}: filho {pid} ainda vivo no sistema {state or ''} após {GRACE_S} s", [pid])
    if pid in registered():
        fail(f"{context}: filho {pid} ainda no registro", [pid])
    return round(time.monotonic() - t0, 3)


async def assert_none_left(before: set[int], context: str) -> None:
    end = time.monotonic() + GRACE_S
    while time.monotonic() < end and (children_now() - before):
        await asyncio.sleep(0.01)
    left = children_now() - before
    if left:
        fail(f"{context}: ainda vivo {sorted(left)} após {GRACE_S} s", left)


def assert_no_children(context: str) -> None:
    if LINUX:
        kids = os_children()
        assert not kids, f"{context}: sobrou filho vivo {kids}"
    assert not registered(), f"{context}: sobrou filho no registro {registered()}"


def new_child(before: set[int], comm: str | None = None) -> int | None:
    """Primeiro filho novo (Linux: pelo /proc; Windows: pelo registro)."""
    if LINUX:
        for pid, (name, state) in os_children().items():
            if pid not in before and state != "Z" and (comm is None or name == comm):
                return pid
        return None
    for pid in registered():
        if pid not in before:
            return pid
    return None


def born_registered(before: set[int], comm: str | None = None) -> int | None:
    """Filho novo que já está no sistema E no registro. No Linux o /proc mostra o filho desde o fork, antes de
    o Popen voltar e o módulo registrar: afirmar algo sobre o registro antes das duas coisas é corrida."""
    pid = new_child(before, comm)
    if pid is None:
        return None
    if pid in registered():
        return pid
    WINDOW["fora_do_registro"] += 1
    return None


def children_now() -> set[int]:
    return set(os_children()) if LINUX else set(registered())


@contextlib.contextmanager
def patched_popen(factory):
    """Troca só o Popen que o módulo de ciclo de vida enxerga: factory(popen_real) -> popen_substituto."""
    mod = epl()
    real = subprocess.Popen
    fake = types.SimpleNamespace(**{k: getattr(subprocess, k) for k in dir(subprocess) if not k.startswith("__")})
    fake.Popen = factory(real)
    mod.subprocess = fake
    try:
        yield
    finally:
        mod.subprocess = subprocess


class _SlowLock:
    """RLock do registro que, nas threads de trabalho, espera `delay` depois de soltar: atraso entre o
    registro (visível para o gate) e a primeira olhada da thread no cancelamento."""

    def __init__(self, real, delay: float):
        self.real, self.delay = real, delay

    def __enter__(self):
        self.real.acquire()
        return self

    def __exit__(self, *exc):
        self.real.release()
        if threading.current_thread() is not threading.main_thread():
            time.sleep(self.delay)
        return False


class Request:
    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


@contextlib.contextmanager
def fake_curl():
    """curl falso que trava (exec sleep: um processo só, como o curl real), no PATH deste processo."""
    import br_bridge
    for key in (br_bridge.ENV_URL, br_bridge.ENV_TOKEN):
        os.environ.pop(key, None)
    assert br_bridge.config() is None, "ponte ligada no ambiente do gate"
    with tempfile.TemporaryDirectory(prefix="rx_m1_curl_") as td:
        exe = Path(td) / "curl"
        stop = Path(td) / "parar"
        exe.write_text(f"#!/bin/sh\n[ -e '{stop}' ] && exit 7\nexec sleep 300\n", encoding="utf-8")
        exe.chmod(0o755)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = td + os.pathsep + old
        FAKE_CURL_STOP.append(stop)
        try:
            yield
        finally:
            FAKE_CURL_STOP.remove(stop)
            os.environ["PATH"] = old


@contextlib.contextmanager
def gated_wait_for(pred, extra_modules=()):
    """O asyncio.wait_for do escopo (e o dos módulos extras, para pegar um site revertido ao wait_for puro) só
    dispara (prazo 0) depois que pred() é verdade: prazo determinístico."""
    modules = (epl(), *extra_modules)
    real = asyncio.wait_for

    async def gated(aw, timeout):
        # o wait_for real começa a corrotina na hora; esperar o filho antes de começá-la seria esperar para sempre
        # (é o caso de um site revertido para asyncio.wait_for, que recebe a corrotina ainda não iniciada)
        task = asyncio.ensure_future(aw)
        assert await await_until(pred, 15.0), "o filho não nasceu em 15 s (instrumento, não o prazo)"
        return await real(task, 0)

    fake = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    fake.wait_for = gated
    for module in modules:
        module.asyncio = fake
    try:
        yield
    finally:
        for module in modules:
            module.asyncio = asyncio


def blocking_query(*, cancel_event: threading.Event):
    try:
        epl().run_managed_process(sleep_cmd(), timeout_seconds=300, cancel_event=cancel_event)
    except epl().ManagedProcessCancelled:
        return "cancelled"
    return "finished"


# ------------------------------------------------------------------ ciclo de vida (primitivas)
@regra("abandono_desconecta")
def r_abandono():
    async def scenario():
        before = children_now()
        request = Request()
        task = asyncio.create_task(epl().run_sync_with_request_lifecycle(request, blocking_query, timeout_seconds=None))
        # espera o filho no sistema E no registro: entre o fork e o registro há uma janela legítima
        assert await await_until(lambda: born_registered(before) is not None, 15.0), \
            "filho vivo fora do registro por 15 s (ou não nasceu)"
        pid = born_registered(before)
        request.disconnected = True
        try:
            await task
            raise AssertionError("a desconexão não abortou a consulta")
        except epl().RequestDisconnected:
            pass
        return pid, assert_gone(pid, "abandono")
    pid, took = asyncio.run(scenario())
    return f"filho {pid} some em {took} s"


@regra("prazo_estourado")
def r_prazo():
    mod = epl()
    now = [1000.0]
    mod.time = types.SimpleNamespace(monotonic=lambda: now[0])  # relógio do prazo parado até o gate ver o filho

    async def scenario():
        before = children_now()
        task = asyncio.create_task(mod.run_sync_with_request_lifecycle(Request(), blocking_query, timeout_seconds=0.30))
        assert await await_until(lambda: new_child(before) is not None, 15.0), "o filho não nasceu em 15 s"
        pid = new_child(before)
        await asyncio.sleep(0.25)
        assert not task.done(), "a operação terminou com o relógio parado (o prazo não é o do módulo)"
        assert os_alive(pid), "o filho morreu antes do prazo"
        now[0] += 1.0  # o prazo passa agora, com o filho vivo
        try:
            await task
            raise AssertionError("o prazo do servidor não abortou a consulta")
        except mod.ManagedOperationTimeout:
            pass
        return pid, assert_gone(pid, "prazo")
    try:
        pid, took = asyncio.run(scenario())
    finally:
        mod.time = time
    return f"filho {pid} some em {took} s depois do prazo"


@regra("cancelado_antes_de_nascer", linux=True)
def r_antes():
    calls: list[object] = []

    def counting(real):
        def popen(*a, **kw):
            calls.append(a)
            return real(*a, **kw)
        return popen

    with tempfile.TemporaryDirectory(prefix="rx_m1_marca_") as td, patched_popen(counting):
        marker = Path(td) / "nasceu"
        event = threading.Event()
        event.set()
        before = children_now()
        try:
            epl().run_managed_process(["sh", "-c", f"touch '{marker}'; exec sleep 300"], timeout_seconds=300, cancel_event=event)
            raise AssertionError("cancelamento prévio não impediu a execução")
        except epl().ManagedProcessCancelled:
            pass
        time.sleep(0.4)
        # a contagem do Popen é a prova determinística; a marca no disco é a do sistema (pode perder a corrida
        # se o processo for morto antes do touch, por isso não é a única)
        assert not calls, f"o processo chegou a nascer (Popen chamado {len(calls)}x) com o cancelamento já pedido"
        assert not marker.exists(), "o processo chegou a nascer com o cancelamento já pedido (marca no disco)"
        assert children_now() == before, "sobrou filho"
    return "Popen nem chamado"


@regra("desligamento")
def r_desligamento():
    mod = epl()
    before = children_now()
    result: list[str] = []
    busy, release = threading.Event(), threading.Event()
    workers: list[threading.Thread] = []
    real_is_cancelled = mod._is_cancelled

    def paused_is_cancelled(cancel_event):
        # a thread da consulta fica ocupada (sem olhar o cancelamento) quando o desligamento chega: quem mata
        # o filho tem de ser o próprio desligamento, não a próxima olhada da thread
        if workers and threading.current_thread() is workers[0] and busy_requested[0] and not release.is_set():
            busy.set()
            release.wait(15.0)
        return real_is_cancelled(cancel_event)

    busy_requested = [False]
    worker = threading.Thread(target=lambda: result.append(blocking_query(cancel_event=threading.Event())))
    workers.append(worker)
    mod._is_cancelled = paused_is_cancelled
    worker.start()
    try:
        # o desligamento só é chamado com o filho no sistema E no registro (senão não há o que mirar ainda)
        assert wait_until(lambda: born_registered(before) is not None, 15.0), "filho vivo fora do registro por 15 s (ou não nasceu)"
        pid = born_registered(before)
        busy_requested[0] = True
        assert busy.wait(5.0), "instrumento: a thread da consulta não parou no ponto combinado"
        killed = mod.terminate_active_processes()
        dead_on_return = not os_alive(pid)
        release.set()
        worker.join(timeout=GRACE_S)
        assert pid in killed, f"o desligamento não mirou o filho {pid}"
        if not dead_on_return:
            fail(f"o desligamento voltou com o filho {pid} ainda vivo (deixou para a thread)", [pid])
        assert not worker.is_alive(), "a thread da consulta sobreviveu ao desligamento"
        took = assert_gone(pid, "desligamento")
        # depois do desligamento, nenhuma consulta cria processo novo
        mod._is_cancelled = real_is_cancelled
        late: list[str] = []
        after = threading.Thread(target=lambda: late.append(blocking_query(cancel_event=threading.Event())))
        after.start()
        wait_until(lambda: bool(late) or bool(children_now() - before), 2.0)
        born_late = children_now() - before
        if born_late:
            fail(f"o desligamento não impediu novo processo: {sorted(born_late)}", born_late)
        after.join(GRACE_S)
        assert late == ["cancelled"], f"consulta depois do desligamento: {late}"
    finally:
        release.set()
        mod._is_cancelled = real_is_cancelled
        mod._SERVER_STOPPING.clear()  # só para as próximas regras deste processo
    return f"filho {pid} morto pelo próprio desligamento ({took} s); nenhum processo novo depois"


@regra("corrida_nascimento_registro", linux=True)
def r_corrida():
    """Controle permanente da corrida corrigida: atraso injetado em cada passagem entre o nascimento no sistema
    e a primeira olhada da thread no cancelamento. Tem de PASSAR; e com 0,3 s entre Popen e registro o
    instrumento tem de ter visto o filho fora do registro (senão a regra não exercitou a janela)."""
    mod = epl()
    notes: list[str] = []
    for delay in (0.05, 0.3):
        def slow(real, delay=delay):
            def popen(*a, **kw):
                proc = real(*a, **kw)
                time.sleep(delay)  # nasceu no sistema, ainda não registrado
                return proc
            return popen

        WINDOW["fora_do_registro"] = 0
        with patched_popen(slow):
            r_abandono()
            r_desligamento()
        seen = WINDOW["fora_do_registro"]
        if delay >= 0.3:
            assert seen > 0, f"instrumento: com {delay} s entre Popen e registro a janela não foi vista"
        real_lock = mod._ACTIVE_LOCK
        mod._ACTIVE_LOCK = _SlowLock(real_lock, delay)
        try:
            r_abandono()
            r_desligamento()
        finally:
            mod._ACTIVE_LOCK = real_lock
        notes.append(f"{delay}s: janela vista {seen}x")
    return "abandono e desligamento certos com atraso nas duas passagens (" + "; ".join(notes) + ")"


@regra("descritores", linux=True)
def r_descritores():
    """Pipe do filho fecha no próprio ciclo de vida, mesmo se quem chamou guardar a exceção (log, estado, tarefa)."""
    mod = epl()

    def fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    kept: list[BaseException] = []

    def timed_out() -> None:
        try:
            mod.run_managed_process(sleep_cmd(), timeout_seconds=0.2)
        except subprocess.TimeoutExpired as exc:
            kept.append(exc)

    def cancelled() -> None:
        event = threading.Event()
        timer = threading.Timer(0.2, event.set)
        timer.start()
        try:
            mod.run_managed_process(sleep_cmd(), timeout_seconds=300, cancel_event=event)
        except mod.ManagedProcessCancelled as exc:
            kept.append(exc)
        timer.join()

    timed_out()  # aquecimento (importações e primeiro uso do subprocess)
    r_abandono()
    gc.collect()
    gc.disable()  # sem o coletor de ciclos: o pipe só fecha se o código fechar
    try:
        base = fds()
        for _ in range(3):
            timed_out()
            cancelled()
        r_abandono()
        r_prazo()
        leaked = fds() - base
    finally:
        kept.clear()
        gc.enable()
        gc.collect()
    assert leaked <= 0, f"descritores abertos a mais depois de 3 prazos, 3 cancelamentos, abandono e prazo da rota: {leaked}"
    return f"delta de descritores {leaked} com as exceções guardadas"


# ------------------------------------------------------------------ escopo
@regra("escopo_da_rota")
def r_escopo_rota():
    mod = epl()

    def route_body(*, cancel_event):  # chamada interna que não repassa cancel_event
        return mod.run_managed_process(sleep_cmd(), timeout_seconds=300)

    async def scenario():
        before = children_now()
        request = Request()
        task = asyncio.create_task(mod.run_sync_with_request_lifecycle(request, route_body, timeout_seconds=None))
        assert await await_until(lambda: new_child(before) is not None, 15.0), "o filho não nasceu em 15 s"
        pid = new_child(before)
        request.disconnected = True
        with contextlib.suppress(mod.RequestDisconnected):
            await task
        return pid, assert_gone(pid, "escopo da rota")
    pid, took = asyncio.run(scenario())
    return f"filho {pid} (sem cancel_event) some em {took} s"


@regra("escopo_prazo_to_thread")
def r_escopo_prazo():
    mod = epl()

    def work():
        with contextlib.suppress(mod.ManagedProcessCancelled):
            mod.run_managed_process(sleep_cmd(), timeout_seconds=300)

    async def fixed():
        before = children_now()
        with gated_wait_for(lambda: new_child(before) is not None):
            try:
                await mod.wait_for_cancelling_processes(asyncio.to_thread(work), 30)
                raise AssertionError("o prazo não estourou")
            except TimeoutError:
                pass
        await assert_none_left(before, "prazo com escopo: filho da thread abandonada")

    async def defect_proof():
        before = children_now()
        scope = threading.Event()
        token = mod._open_scope(scope)  # só para limpar depois da prova
        try:
            task = asyncio.ensure_future(asyncio.to_thread(work))
        finally:
            mod._SCOPES.reset(token)
        assert await await_until(lambda: new_child(before) is not None, 15.0), "o filho não nasceu em 15 s"
        pid = new_child(before)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task, 0)  # o jeito antigo
        await asyncio.sleep(1.0)
        still = os_alive(pid)
        scope.set()
        await asyncio.to_thread(assert_gone, pid, "limpeza da prova")
        return still

    async def ready_task_refused():
        # tarefa criada antes copiou o contexto sem o escopo: aceitar seria proteção falsa, em silêncio
        before = children_now()
        started: list[bool] = []

        async def never_runs():
            started.append(True)

        job = asyncio.ensure_future(never_runs())
        try:
            await mod.wait_for_cancelling_processes(job, 5)
            refused = False
        except TypeError:
            refused = True
        finally:
            await asyncio.sleep(0)
            job.cancel()
            with contextlib.suppress(BaseException):
                await job
        assert refused, "tarefa/Future criada antes aceita sem escopo (tem de ser recusada com TypeError)"
        assert children_now() == before

    asyncio.run(fixed())
    assert asyncio.run(defect_proof()), "instrumento: o wait_for puro deveria deixar o filho vivo e o gate não viu"
    asyncio.run(ready_task_refused())
    return "com escopo nenhum filho sobra; sem escopo o filho fica vivo (defeito provado e pego); tarefa pronta recusada"


# ------------------------------------------------------------------ cadeia real com curl travado (Linux)
@regra("cadeia_car_desconecta", linux=True)
def r_cadeia_desconecta():
    import car_resilient
    mod = epl()

    async def scenario():
        before = children_now()
        request = Request()
        task = asyncio.create_task(mod.run_sync_with_request_lifecycle(request, car_resilient.fetch_car_live_resilient, CAR,
                                                                       timeout_seconds=None))
        assert await await_until(lambda: new_child(before, "sleep") is not None, 15.0), "o curl falso não nasceu em 15 s"
        pid = new_child(before, "sleep")
        request.disconnected = True
        with contextlib.suppress(mod.RequestDisconnected):
            await task
        took = assert_gone(pid, "cadeia do SICAR (desconexão)")
        await asyncio.sleep(0.5)
        if children_now() - before:
            fail(f"a cadeia criou outro curl depois da desconexão: {children_now() - before}", children_now() - before)
        return took
    with fake_curl():
        took = asyncio.run(scenario())
    return f"curl some em {took} s e nenhum outro nasce"


# (módulo, handler, tem fallback que consulta o SICAR de novo sem prazo depois do estouro)
PRAZO_HANDLERS = (
    ("report_quick_v22", "quick_analysis_v24", False),
    ("report_v9_patch", "quick_analysis", True),
    ("portal_property_tabs", "embargos_detail", True),
    ("portal_mining_resilience_v34", "critical_minerals_v34", False),
)


@regra("cadeia_car_prazo", linux=True)
def r_cadeia_prazo():
    """Os handlers reais, não uma cópia do padrão: um site revertido para asyncio.wait_for (o prazo do módulo
    do handler também é controlado) ou uma tarefa criada fora do escopo reprovam aqui."""
    import importlib
    import car_resilient  # noqa: F401 - instala a busca resiliente do SICAR em deploy_app/report_api
    mod = epl()
    notes: list[str] = []

    async def scenario(module, name: str, fallback: bool) -> str:
        handler = getattr(module, name)
        before = children_now()
        first: dict[str, int] = {}

        def first_curl() -> bool:
            if "pid" not in first:
                pid = born_registered(before, "sleep")
                if pid is None:
                    return False
                first["pid"] = pid
            return True

        with gated_wait_for(first_curl, extra_modules=(module,)):
            task = asyncio.create_task(handler(CAR))
            if not await await_until(lambda: "pid" in first or task.done(), 20.0):
                fail(f"{name}: o curl falso não nasceu em 20 s", children_now() - before)
            if task.done() and not task.cancelled() and task.exception() is not None \
                    and getattr(task.exception(), "status_code", None) != 504:
                fail(f"{name}: falhou antes de o curl do prazo cair: {task.exception()!r}"[:300], children_now() - before)
            if "pid" not in first:
                fail(f"{name}: terminou sem consultar o SICAR ({task.result() if task.done() else 'em curso'!r})"[:300])
            pid = first["pid"]
            took = await asyncio.to_thread(assert_gone, pid, f"{name}: curl do prazo")
        if fallback:
            # o fallback consulta de novo, fora do prazo (fora do escopo desta regra): encerra e limpa
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            mod.terminate_active_processes()
            return f"{name} {took}s"
        try:
            outcome = await asyncio.wait_for(task, 10)
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        if isinstance(outcome, TypeError) or not (isinstance(outcome, dict) or getattr(outcome, "status_code", None) == 504):
            fail(f"{name}: resposta inesperada depois do prazo: {outcome!r}"[:300])
        await asyncio.sleep(0.5)
        await assert_none_left(before, f"{name}: outro curl depois do prazo")
        return f"{name} {took}s"

    with fake_curl():
        for module_name, name, fallback in PRAZO_HANDLERS:
            module = importlib.import_module(module_name)
            try:
                notes.append(asyncio.run(scenario(module, name, fallback)))  # o fim do laço espera as threads
            finally:
                mod._SERVER_STOPPING.clear()
            assert_no_children(f"depois de {name}")
    return "curl do prazo some nos quatro handlers: " + ", ".join(notes)


@regra("cadeia_slots_relatorio", linux=True)
def r_slots():
    import deploy_app
    import report_extras_perf_v30 as extras
    import core_retry_fast_v29 as core_retry

    async def one(label, runner):
        before = children_now()
        # o prazo dos módulos dos slots também é controlado: um slot revertido ao wait_for puro reprova já
        with gated_wait_for(lambda: new_child(before, "sleep") is not None, extra_modules=(extras, core_retry)):
            value = await runner(asyncio.to_thread(deploy_app._curl, deploy_app.SICAR + "?m1=1", True))
        assert isinstance(value, dict) and value.get("ok") is False, (label, value)
        await assert_none_left(before, f"{label}: curl")

    async def scenario():
        await one("extras _timed", lambda coro: extras._timed("m1_gate", coro, 5))
        await one("nova tentativa _bounded", lambda coro: core_retry._bounded("M1_GATE", coro, 5))
    with fake_curl():
        asyncio.run(scenario())
    return "slot estourado não deixa curl vivo (extras e nova tentativa)"


# ------------------------------------------------------------------ inventário estático
DIRECT_DECLARED = {
    # módulo: processo criado fora do run_managed_process (subprocess.run direto ou br_bridge.subprocess_runner
    # passado como executor), todos com timeout — sobra limitada pelo timeout, não cancelável
    "aerodromes_anac.py", "anm_fast_v29.py", "anm_resilient.py", "br_bridge.py", "climate_nasa.py",
    "climate_normal_f2.py", "ide_catalog.py", "ide_layer_probe.py", "incra_snci_public_v42.py",
    "jwt_runtime_bootstrap.py", "pivots_ana.py", "postgres_runtime_bootstrap.py", "prodes_image_platform_f2.py",
    "rasterio_runtime_bootstrap.py", "redis_runtime_bootstrap.py", "sicar_detail_sources.py",
    "sicar_detail_sources_v2.py", "soilgrids_wcs.py", "terrain_srtm.py", "water_mg.py",
}
# Quem chega a um processo gerenciado é calculado (fecho transitivo por nome a partir destas sementes), não
# mantido à mão: por nome é conservador (duas funções com o mesmo nome contam juntas).
PROCESS_SEEDS = {"run_managed_process"}
# Chamadas "await wait_for_cancelling_processes(" por arquivo. A do ANM (portal_mining_resilience_v34, prazo de
# 10 s do query_anm_curl_exact) está contada, mas SEM EFEITO até a conversão: aquela função chama
# subprocess.run direto, fora do alcance do escopo (limitada só pelo timeout de 60 s dela).
SCOPED_SITES = {
    "portal_mining_resilience_v34.py": 2, "report_quick_v22.py": 1, "report_v9_patch.py": 1,
    "portal_property_tabs.py": 1, "core_retry_fast_v29.py": 1, "report_extras_perf_v30.py": 1,
    "portal_advanced_name_v40.py": 1,
}
SCOPED_WITHOUT_EFFECT = 1
DIRECT_PROCESS_CALLS = {"os.system", "os.popen", "os.fork", "os.forkpty", "os.posix_spawn", "os.posix_spawnp",
                        "pty.fork", "pty.spawn", "subprocess.getoutput", "subprocess.getstatusoutput"}
DIRECT_PROCESS_PREFIXES = ("os.exec", "os.spawn", "asyncio.create_subprocess")
DIRECT_PROCESS_ATTRS = {"subprocess_exec", "subprocess_shell"}  # loop.subprocess_exec / subprocess_shell


def process_reaching(trees: dict[str, ast.AST]) -> set[str]:
    refs: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = refs.setdefault(node.name, set())
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
                    elif isinstance(sub, ast.Attribute):
                        names.add(sub.attr)
    reach = set(PROCESS_SEEDS)
    changed = True
    while changed:
        changed = False
        for name, names in refs.items():
            if name not in reach and names & reach:
                reach.add(name)
                changed = True
    return reach


def src(name: str) -> Path:
    """O arquivo que o Python importaria: a cópia mutada, se houver, antes da original."""
    if MUTANT_DIR and (Path(MUTANT_DIR) / name).exists():
        return Path(MUTANT_DIR) / name
    return ROOT / name


def runtime_modules() -> list[Path]:
    return [src(p.name) for p in sorted(ROOT.glob("*.py")) if p.is_file()]


@regra("inventario_processos")
def r_inventario():
    problems: list[str] = []
    direct: set[str] = set()
    trees = {path.name: ast.parse(path.read_text(encoding="utf-8"), filename=path.name) for path in runtime_modules()}
    reaching = process_reaching(trees)
    for name, tree in trees.items():
        path = Path(name)
        if name != "br_bridge.py" and any(
                (isinstance(n, ast.Name) and n.id == "subprocess_runner") or
                (isinstance(n, ast.Attribute) and n.attr == "subprocess_runner") for n in ast.walk(tree)):
            direct.add(name)  # passa o executor direto (subprocess.run) adiante: processo sem dono cancelável
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    aliases[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
                    if node.module in ("multiprocessing", "concurrent.futures") and a.name in ("Process", "Pool", "ProcessPoolExecutor"):
                        problems.append(f"{path.name}:{node.lineno} importa {node.module}.{a.name}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "multiprocessing" for a in node.names):
                problems.append(f"{path.name}:{node.lineno} importa multiprocessing")
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            dotted = None
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                dotted = f"{aliases.get(f.value.id, f.value.id)}.{f.attr}"
            elif isinstance(f, ast.Name):
                dotted = aliases.get(f.id, f.id)
            if isinstance(f, ast.Attribute) and f.attr in DIRECT_PROCESS_ATTRS:
                problems.append(f"{path.name}:{node.lineno} .{f.attr} (processo sem dono)")
                continue
            if not dotted:
                continue
            where = f"{path.name}:{node.lineno}"
            kw = {k.arg for k in node.keywords}
            if dotted in ("subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output"):
                direct.add(path.name)
                if "timeout" not in kw:
                    problems.append(f"{where} {dotted} sem timeout")
            elif dotted == "subprocess.Popen":
                if path.name != "external_process_lifecycle.py":
                    problems.append(f"{where} Popen fora do módulo de ciclo de vida")
            elif dotted in DIRECT_PROCESS_CALLS or dotted.startswith(DIRECT_PROCESS_PREFIXES):
                problems.append(f"{where} {dotted} (processo sem dono)")
            elif dotted.endswith("run_curl") or dotted == "br_bridge.run_curl":
                t = next((k.value for k in node.keywords if k.arg == "timeout_seconds"), None)
                if t is None or (isinstance(t, ast.Constant) and t.value is None):
                    problems.append(f"{where} run_curl sem timeout_seconds")
            elif dotted == "asyncio.wait_for" and node.args:
                names = {n.id for n in ast.walk(node.args[0]) if isinstance(n, ast.Name)} | \
                        {n.attr for n in ast.walk(node.args[0]) if isinstance(n, ast.Attribute)}
                hit = names & reaching
                if hit:
                    problems.append(f"{where} asyncio.wait_for puro sobre {sorted(hit)} (use wait_for_cancelling_processes)")
    for name, count in SCOPED_SITES.items():
        text = src(name).read_text(encoding="utf-8")
        got = text.count("await wait_for_cancelling_processes(")
        if got != count:
            problems.append(f"{name}: {got} chamada(s) de wait_for_cancelling_processes, esperado {count}")
    if direct != DIRECT_DECLARED:
        problems.append(f"lista de chamadas diretas mudou: novas {sorted(direct - DIRECT_DECLARED)} sumidas {sorted(DIRECT_DECLARED - direct)}")
    assert not problems, "inventário: " + " | ".join(problems)
    sites = sum(SCOPED_SITES.values())
    return (f"{len(direct)} módulos com processo direto limitado por timeout; {len(reaching)} funções chegam a processo "
            f"gerenciado; {sites} prazos com escopo ({sites - SCOPED_WITHOUT_EFFECT} com efeito, "
            f"{SCOPED_WITHOUT_EFFECT} sem efeito até a conversão do ANM)")


# ------------------------------------------------------------------ medição
OS_FIELDS = ("processo_idade_s", "e_pid_1", "filhos_vivos", "zumbis", "descendentes_vivos", "filho_mais_velho_s",
             "filhos_por_nome", "processos_no_conteiner", "zumbis_no_conteiner", "threads_so", "descritores_abertos",
             "rss_mb", "pico_rss_mb", "rss_descendentes_mb", "memoria_conteiner_mb", "limite_conteiner_mb")
ALL_FIELDS = {"servico", "medido_em", "leitura", "filhos_gerenciados", "threads_python", "tarefas_asyncio_pendentes",
              "medicao_ms", *OS_FIELDS}


def stats():
    import rx_proc_stats
    return rx_proc_stats


@regra("medicao_indisponivel")
def r_indisponivel():
    s = stats()
    with tempfile.TemporaryDirectory(prefix="rx_m1_proc_") as td:
        empty = s.snapshot(proc_root=td + "/nada", cgroup_root=td + "/nada")
        other = Path(td) / "proc"
        (other / "999999").mkdir(parents=True)  # um /proc que não é o deste processo
        (other / "999999" / "stat").write_text("999999 (x) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 5 0 0\n")
        foreign = s.snapshot(proc_root=str(other), cgroup_root=td + "/nada")
    for label, snap in (("sem /proc", empty), ("/proc de outro", foreign)):
        assert set(snap) == ALL_FIELDS, f"{label}: campos {sorted(set(snap) ^ ALL_FIELDS)}"
        wrong = {k: snap[k] for k in OS_FIELDS if snap[k] != s.INDISPONIVEL}
        assert not wrong, f"{label}: zero inventado ou valor sem medição {wrong}"
        assert snap["leitura"] == s.INDISPONIVEL, (label, snap["leitura"])
        line = s.log_line(snap, "teste")
        assert "filhos_vivos=indisponivel" in line and "zumbis=indisponivel" in line, line
    assert isinstance(empty["threads_python"], int) and isinstance(empty["filhos_gerenciados"], int)
    assert empty["tarefas_asyncio_pendentes"] == s.INDISPONIVEL, "fora do laço a contagem de tarefas não é medível"

    # /proc com o próprio processo, mas com leitura que não fecha: número nenhum pode sair zero por falha
    me = os.getpid()

    def stat_line(pid: int, ppid: int, start_ticks: int, state: str = "S", comm: str = "python3") -> str:
        return f"{pid} ({comm}) {state} {ppid} 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {start_ticks} 0 0\n"

    def own(root: Path, start_ticks: int, uptime: str) -> None:
        (root / str(me) / "fd").mkdir(parents=True)
        (root / str(me) / "stat").write_text(stat_line(me, 1, start_ticks))
        (root / str(me) / "status").write_text("State:\tS (sleeping)\nThreads:\t3\nVmRSS:\t2048 kB\nVmHWM:\t4096 kB\n")
        (root / "uptime").write_text(uptime)

    old_tck = s._clk_tck
    s._clk_tck = lambda: 100
    try:
        with tempfile.TemporaryDirectory(prefix="rx_m1_proc_falha_") as td:
            bad = Path(td) / "proc"
            own(bad, 5000, "10.00 0.00\n")  # começou em 50 s com o relógio em 10 s: a conta não fecha
            for pid, start, status in ((900001, 100, "State:\tS (sleeping)\nVmRSS:\t1024 kB\n"),
                                       (900002, 3000, "State:\tS (sleeping)\nVmRSS:\t1024 kB\n"),
                                       (900003, 200, None)):  # vivo, pasta existe, status ilegível
                (bad / str(pid)).mkdir()
                (bad / str(pid) / "stat").write_text(stat_line(pid, me, start, comm="curl"))
                if status is not None:
                    (bad / str(pid) / "status").write_text(status)
            (bad / "900009").mkdir()
            (bad / "900009" / "stat").write_text("lixo sem parenteses\n")  # entrada que existe e não se lê
            broken = s.snapshot(proc_root=str(bad), cgroup_root=td + "/nada")

            good = Path(td) / "proc_ok"
            own(good, 5000, "100.00 0.00\n")
            clean = s.snapshot(proc_root=str(good), cgroup_root=td + "/nada")
    finally:
        s._clk_tck = old_tck
    assert broken["leitura"] == "proc_linux" and broken["filhos_vivos"] == 3, broken
    assert broken["processo_idade_s"] == s.INDISPONIVEL, f"idade negativa virou número: processo_idade_s={broken['processo_idade_s']!r}"
    assert broken["filho_mais_velho_s"] == s.INDISPONIVEL, \
        f"filho com idade negativa virou número: filho_mais_velho_s={broken['filho_mais_velho_s']!r}"
    assert broken["rss_descendentes_mb"] == s.INDISPONIVEL, \
        f"descendente vivo sem memória legível somou zero: rss_descendentes_mb={broken['rss_descendentes_mb']!r}"
    assert broken["processos_no_conteiner"] == s.INDISPONIVEL and broken["zumbis_no_conteiner"] == s.INDISPONIVEL, \
        f"entrada ilegível do /proc e total medido: processos_no_conteiner={broken['processos_no_conteiner']!r}"
    assert "processo_idade_s=indisponivel" in s.log_line(broken, "teste")
    assert clean["processo_idade_s"] == 50.0 and clean["processos_no_conteiner"] == 1, clean
    assert clean["rss_descendentes_mb"] is None and clean["filho_mais_velho_s"] is None, \
        f"sem descendentes a memória deles é null, não zero: {clean['rss_descendentes_mb']!r}"
    assert "rss_descendentes_mb=sem_filhos" in s.log_line(clean, "teste")
    return "sem /proc do próprio processo tudo é indisponivel; idade negativa, memória e entrada ilegíveis também"


@regra("medicao_real", linux=True)
def r_medicao_real():
    s = stats()
    mod = epl()
    assert not os_children(), f"o gate começou a regra com filhos {os_children()}"
    base = s.snapshot()
    assert base["leitura"] == "proc_linux", base
    assert base["filhos_vivos"] == 0 and base["zumbis"] == 0 and base["filho_mais_velho_s"] is None, base
    assert base["filhos_por_nome"] == {} and base["rss_descendentes_mb"] is None, base
    assert isinstance(base["processo_idade_s"], float) and isinstance(base["processos_no_conteiner"], int), base

    # descritores: contagem independente por fstat
    def probe() -> int:
        n = 0
        for fd in range(0, 4096):
            try:
                os.fstat(fd)
                n += 1
            except OSError:
                pass
        return n
    files = [open(os.devnull, "rb") for _ in range(7)]
    try:
        snap = s.snapshot()
        independent = probe()
        assert snap["descritores_abertos"] == independent, f"descritores {snap['descritores_abertos']} != fstat {independent}"
        assert snap["descritores_abertos"] - base["descritores_abertos"] == 7, (base["descritores_abertos"], snap["descritores_abertos"])
    finally:
        for fh in files:
            fh.close()

    # filhos, idade, nomes, zumbis e gerenciados
    child = subprocess.Popen(["sleep", "300"])
    zombie = subprocess.Popen(["true"])
    stop = threading.Event()
    try:
        assert wait_until(lambda: os_children().get(zombie.pid, ("", ""))[1] == "Z", 5.0), "o zumbi não apareceu"
        time.sleep(1.1)
        worker = threading.Thread(target=blocking_query, kwargs={"cancel_event": stop})
        worker.start()
        assert wait_until(lambda: len(registered()) == 1, 15.0), "filho gerenciado não nasceu"
        snap = s.snapshot()
        kids = os_children()
        alive = sum(1 for _, st in kids.values() if st != "Z")
        zombies = sum(1 for _, st in kids.values() if st == "Z")
        assert snap["filhos_vivos"] == alive == 2, f"filhos_vivos {snap['filhos_vivos']} (sistema {alive}, esperado 2)"
        assert snap["zumbis"] == zombies == 1, f"zumbis {snap['zumbis']} (sistema {zombies}, esperado 1)"
        assert snap["descendentes_vivos"] == 2, f"descendentes_vivos {snap['descendentes_vivos']}"
        assert snap["filhos_gerenciados"] == 1, f"filhos_gerenciados {snap['filhos_gerenciados']}"
        assert snap["filhos_por_nome"] == {"sleep": 2, "true": 1}, f"filhos_por_nome {snap['filhos_por_nome']}"
        assert isinstance(snap["filho_mais_velho_s"], float) and snap["filho_mais_velho_s"] >= 1.0, \
            f"filho_mais_velho_s {snap['filho_mais_velho_s']}"
        assert snap["zumbis_no_conteiner"] >= 1 and snap["processos_no_conteiner"] >= 4, f"contêiner {snap}"
        assert isinstance(snap["rss_descendentes_mb"], float) and snap["rss_descendentes_mb"] > 0, \
            f"rss_descendentes_mb {snap['rss_descendentes_mb']}"
        zombie.wait()
        stop.set()
        worker.join(GRACE_S)
        after = s.snapshot()
        assert after["zumbis"] == 0 and after["filhos_gerenciados"] == 0 and after["filhos_vivos"] == 1, \
            f"depois de coletar: zumbis {after['zumbis']} gerenciados {after['filhos_gerenciados']} vivos {after['filhos_vivos']}"
    finally:
        stop.set()
        child.kill()
        child.wait()
        with contextlib.suppress(Exception):
            zombie.wait(timeout=1)

    # threads
    hold = threading.Event()
    threads = [threading.Thread(target=hold.wait) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        snap = s.snapshot()
        status = Path("/proc/self/status").read_text()
        so = int(re.search(r"^Threads:\s+(\d+)", status, re.M).group(1))
        assert snap["threads_so"] == so, (snap["threads_so"], so)
        assert snap["threads_python"] == threading.active_count(), snap["threads_python"]
    finally:
        hold.set()
        for t in threads:
            t.join()

    # memória: VmRSS sobe com 64 MB tocados; pico >= atual
    before_rss = s.snapshot()["rss_mb"]
    blob = bytearray(64 * 1024 * 1024)
    for i in range(0, len(blob), 4096):
        blob[i] = 1
    snap = s.snapshot()
    del blob
    assert snap["rss_mb"] - before_rss >= 50, (before_rss, snap["rss_mb"])
    assert snap["pico_rss_mb"] >= snap["rss_mb"], snap

    # tarefas asyncio: medidas dentro do laço
    async def tasks():
        hold_async = asyncio.Event()
        pending = [asyncio.create_task(hold_async.wait()) for _ in range(5)]
        await asyncio.sleep(0)
        value = s.snapshot()["tarefas_asyncio_pendentes"]
        hold_async.set()
        await asyncio.gather(*pending)
        return value, len(asyncio.all_tasks())
    counted, _ = asyncio.run(tasks())
    assert counted == 5, f"tarefas pendentes {counted} (esperado 5: a da própria medição não conta)"
    assert mod is epl()
    assert s.snapshot()["medicao_ms"] < 200, "medição cara demais"
    return "filhos 2, zumbi 1, gerenciado 1, idade, nomes, descritores, threads, memória e tarefas batem"


def _app():
    from fastapi import FastAPI
    s = stats()
    app = FastAPI()
    s.install(app)
    return app


GATE_TOKEN = "M1gate-" + "k" * 40  # só neste processo de teste


@contextlib.contextmanager
def env_var(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


@regra("sem_dado_sensivel", linux=True)
def r_sensivel():
    from fastapi.testclient import TestClient
    s = stats()
    with tempfile.TemporaryDirectory(prefix="SEGREDO_M1_DIR_") as td:
        env = dict(os.environ, RX_M1_SEGREDO="SEGREDO_M1_ENV")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", "--token=SEGREDO_M1_ARG"], cwd=td, env=env)
        try:
            assert wait_until(lambda: child.pid in os_children(), 10.0)
            startup = io.StringIO()
            with env_var(s.TOKEN_ENV, GATE_TOKEN), contextlib.redirect_stdout(startup), TestClient(_app()) as client:
                res = client.get(s.ROUTE, headers={"X-RX-Diag-Token": GATE_TOKEN})
            assert "SEGREDO" not in startup.getvalue() and GATE_TOKEN not in startup.getvalue(), "log do arranque: dado vazou"
            body = res.text
            line = s.log_line(s.snapshot(), "teste")
        finally:
            child.kill()
            child.wait()
    data = json.loads(body)
    assert res.status_code == 200 and data["filhos_vivos"] >= 1, body
    for label, text in (("rota", body), ("log", line)):
        assert "SEGREDO" not in text and GATE_TOKEN not in text, f"{label}: dado do processo vazou: {text[:400]}"
        assert "/" not in text and "\\" not in text, f"{label}: caminho no texto: {text[:400]}"
    assert set(data) == ALL_FIELDS | {"ok", "legenda"}, f"campos a mais ou a menos: {sorted(set(data) ^ (ALL_FIELDS | {'ok', 'legenda'}))}"
    assert data["rss_descendentes_mb"] is None or isinstance(data["rss_descendentes_mb"], (int, float)) or \
        data["rss_descendentes_mb"] == s.INDISPONIVEL, data["rss_descendentes_mb"]
    for key in ALL_FIELDS - {"servico", "medido_em", "leitura", "filhos_por_nome", "e_pid_1", "filho_mais_velho_s",
                             "limite_conteiner_mb", "rss_descendentes_mb"}:
        assert isinstance(data[key], (int, float)) or data[key] == s.INDISPONIVEL, f"valor fora do tipo em {key}: {data[key]!r}"
    assert data["servico"] in ("portal", "relatorio") and data["leitura"] in ("proc_linux", s.INDISPONIVEL)
    assert all(re.fullmatch(r"[A-Za-z0-9._+-]{1,15}", k) for k in data["filhos_por_nome"]), f"nome fora do padrão vazou: {data['filhos_por_nome']}"
    assert res.headers.get("cache-control") == "no-store", res.headers
    return "sem argumento, ambiente, caminho ou PID"


@regra("rota_protegida")
def r_rota_protegida():
    from fastapi.testclient import TestClient
    s = stats()
    header = "X-RX-Diag-Token"
    with contextlib.redirect_stdout(io.StringIO()) as boot, TestClient(_app()) as client:  # a linha do arranque não interessa aqui
        missing = client.get("/v1/diag/nao-existe-m1")
        cases: list[tuple[str, object]] = []
        with env_var(s.TOKEN_ENV, None):
            cases.append(("sem RX_DIAG_TOKEN no serviço", client.get(s.ROUTE, headers={header: GATE_TOKEN})))
        with env_var(s.TOKEN_ENV, "curto-" + "x" * 10):
            cases.append(("token curto", client.get(s.ROUTE, headers={header: "curto-" + "x" * 10})))
        with env_var(s.TOKEN_ENV, GATE_TOKEN):
            cases.append(("sem segredo no pedido", client.get(s.ROUTE)))
            cases.append(("segredo errado", client.get(s.ROUTE, headers={header: GATE_TOKEN[:-1] + "z"})))
            cases.append(("segredo vazio", client.get(s.ROUTE, headers={header: ""})))
            cases.append(("segredo na URL", client.get(s.ROUTE + "?token=" + GATE_TOKEN)))
            first = client.get(s.ROUTE, headers={header: GATE_TOKEN})
            second = client.get(s.ROUTE, headers={header: GATE_TOKEN})
    for label, res in cases:
        if res.status_code != 404 or res.text != missing.text:
            raise AssertionError(f"{label}: sem segredo respondeu {res.status_code} {res.text[:160]!r} "
                                 f"(esperado o 404 de endereço inexistente {missing.text!r})")
    assert missing.status_code == 404
    assert first.status_code == 200 and first.json()["ok"] is True, (first.status_code, first.text[:200])
    assert first.headers.get("cache-control") == "no-store", first.headers
    assert first.json()["medido_em"] == second.json()["medido_em"] and first.json()["medicao_ms"] == second.json()["medicao_ms"], \
        "a segunda consulta em seguida mediu de novo (sem reaproveitar)"
    assert GATE_TOKEN not in boot.getvalue() and GATE_TOKEN not in first.text
    assert "hmac.compare_digest(" in Path(s.__file__).read_text(encoding="utf-8"), "comparação do segredo sem tempo constante"
    return f"{len(cases)} jeitos sem segredo dão o 404 de endereço inexistente; com o segredo 200 e medição reaproveitada"


@regra("config_intervalo")
def r_config():
    s = stats()
    expected = {None: (600.0, False), "": (600.0, False), "10": (600.0, False), "2.5": (150.0, False), "0": (0.0, False),
                "0.001": (60.0, True), "0.5": (60.0, True), "nan": (600.0, True), "inf": (600.0, True),
                "-3": (600.0, True), "abc": (600.0, True)}
    for raw, (seconds, warns) in expected.items():
        with env_var("RX_PROC_STATS_MIN", raw):
            got, warning = s.interval_config()
            assert got == seconds, f"RX_PROC_STATS_MIN={raw!r}: {got} s (esperado {seconds} s; piso de 1 min e padrão 10)"
            assert bool(warning) == warns, f"RX_PROC_STATS_MIN={raw!r}: aviso {warning!r}"
            assert s.interval_seconds() == seconds
            if warning:  # avisa sem ecoar o valor que veio da variável
                assert warning.startswith("RX_PROC_STATS_CONFIG ") and f"={raw}" not in warning, warning
    return "piso de 1 min, 0 desliga, inválido volta a 10 com aviso"


@regra("log_arranque_periodico")
def r_log():
    from fastapi.testclient import TestClient
    s = stats()
    buf = io.StringIO()
    app = _app()
    old_floor = s.MIN_INTERVAL_MIN
    s.MIN_INTERVAL_MIN = 0.001  # só aqui: o piso real é conferido em config_intervalo
    try:
        with env_var("RX_PROC_STATS_MIN", "0.005"), contextlib.redirect_stdout(buf):  # 0,3 s
            with TestClient(app):
                end = time.monotonic() + 5.0
                while time.monotonic() < end and "momento=periodico" not in buf.getvalue():
                    time.sleep(0.05)
            task = getattr(app.state, "rx_proc_stats_task", None)
    finally:
        s.MIN_INTERVAL_MIN = old_floor
    out = buf.getvalue()
    lines = [x for x in out.splitlines() if x.startswith("RX_PROC_STATS ")]
    assert any("momento=arranque" in x for x in lines), f"sem linha no arranque: {out[:300]}"
    assert any("momento=periodico" in x for x in lines), f"sem linha periodico: {out[:300]}"
    assert task is not None and task.done(), "a repetição não parou no desligamento"
    for key in ("filhos_vivos=", "zumbis=", "threads_so=", "descritores_abertos=", "rss_mb=", "pico_rss_mb=",
                "tarefas_asyncio_pendentes=", "filho_mais_velho_s="):
        assert key in lines[0], (key, lines[0])
    if LINUX:
        assert "tarefas_asyncio_pendentes=indisponivel" not in lines[0] and "filhos_vivos=indisponivel" not in lines[0], lines[0]
    assert "RX_PROC_STATS_ROTA=desligada" in out, f"sem a linha do estado da rota (desligada sem RX_DIAG_TOKEN): {out[:300]}"
    return f"{len(lines)} linhas (arranque e periódicas) e a rota declarada desligada"


@regra("servicos_ligados")
def r_servicos():
    import report_api
    import rx_proc_stats as s
    app = report_api.app
    assert getattr(app.state, "rx_proc_stats_installed", False), "medição não instalada no app servido"
    paths = [getattr(r, "path", None) for r in app.router.routes]
    assert paths.count(s.ROUTE) == 1, f"rota de diagnóstico {paths.count(s.ROUTE)} vez(es)"
    assert getattr(app.state, "rx_external_process_cleanup_installed", False), "limpeza de processos não instalada no app servido"
    names = {getattr(h, "__name__", "") for h in app.router.on_startup}
    assert "_on_startup" in names, names
    old = os.environ.get("RX_RELEASE")
    try:
        os.environ["RX_RELEASE"] = "V8_OPERATIONAL_ZERO_COST"
        assert s.service_name() == "portal"
        os.environ["RX_RELEASE"] = "OFF"
        assert s.service_name() == "relatorio"
    finally:
        os.environ["RX_RELEASE"] = old or "OFF"
    portal_src = src("portal_api.py").read_text(encoding="utf-8")
    assert "from report_api import app" in portal_src, "o portal deixou de servir o app do report_api"
    return "rota, log e limpeza no app dos dois serviços"


@regra("ci_roda_o_gate")
def r_ci():
    wf = Path(os.environ.get("RX_M1_WORKFLOW", ROOT / ".github" / "workflows" / "quality-gate.yml")).read_text(encoding="utf-8")
    m = re.search(r"\n  (m1-processos-linux):\n(.*?)(?=\n  [A-Za-z0-9_-]+:\n|\Z)", wf, re.S)
    assert m, "quality-gate sem o job m1-processos-linux"
    job = m.group(2)
    assert "runs-on: ubuntu-latest" in job, "job M1 fora do Linux"
    assert "python scripts/m1_processos_gate.py --exigir-linux" in job, "job M1 não exige Linux"
    assert "continue-on-error" not in job, "job M1 com falha tolerada"
    return "job m1-processos-linux em ubuntu com --exigir-linux"


# ------------------------------------------------------------------ controles positivos
MUTATIONS = [
    ("prazo_estourado", "external_process_lifecycle.py",
     "                cancel_event.set()\n                await _drain_thread(task)\n                raise ManagedOperationTimeout",
     "                await _drain_thread(task)\n                raise ManagedOperationTimeout", "ainda vivo"),
    ("abandono_desconecta", "external_process_lifecycle.py",
     "        if proc.poll() is None:\n            _stop_process(proc)\n        if os.name",
     "        if os.name", "ainda"),
    ("descritores", "external_process_lifecycle.py", "                        pipe.close()", "                        pass",
     "descritores abertos a mais"),
    ("escopo_da_rota", "external_process_lifecycle.py",
     "    return any(scope.is_set() for scope in _SCOPES.get())", "    return False", "ainda vivo"),
    ("escopo_prazo_to_thread", "external_process_lifecycle.py",
     "    except BaseException:\n        event.set()\n        raise", "    except BaseException:\n        raise", "ainda vivo"),
    ("escopo_prazo_to_thread", "external_process_lifecycle.py", "    if asyncio.isfuture(aw):\n        raise TypeError(",
     "    if False:\n        raise TypeError(", "tarefa/Future criada antes aceita"),
    ("cancelado_antes_de_nascer", "external_process_lifecycle.py",
     "    if _is_cancelled(cancel_event):\n        raise ManagedProcessCancelled(\"operation_cancelled_before_spawn\")\n", "",
     "chegou a nascer"),
    ("desligamento", "external_process_lifecycle.py", "    _SERVER_STOPPING.set()\n    with _ACTIVE_LOCK:\n        items",
     "    with _ACTIVE_LOCK:\n        items", "não impediu novo processo"),
    ("desligamento", "external_process_lifecycle.py", "            killed.append(pid)\n            _stop_process(proc)",
     "            killed.append(pid)", "voltou com o filho"),
    ("cadeia_car_desconecta", "deploy_app.py", "runner=run_managed_process)", "runner=br_bridge.subprocess_runner)", "ainda vivo"),
    ("cadeia_car_prazo", "report_quick_v22.py",
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6)",
     "car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=6)", "curl do prazo: filho"),
    ("cadeia_car_prazo", "portal_property_tabs.py",
     "try:result=await wait_for_cancelling_processes(report_base.analyze_car(code),18)",
     "try:result=await asyncio.wait_for(report_base.analyze_car(code),timeout=18)", "curl do prazo: filho"),
    ("cadeia_car_prazo", "report_quick_v22.py",
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6)",
     "job=asyncio.ensure_future(asyncio.to_thread(fetch_car_live_resilient,code));car=await wait_for_cancelling_processes(job,6)",
     "passe a corrotina"),
    ("cadeia_slots_relatorio", "report_extras_perf_v30.py",
     "value=await wait_for_cancelling_processes(coro,timeout_s)", "value=await asyncio.wait_for(coro,timeout=timeout_s)",
     "curl: ainda vivo"),
    ("cadeia_slots_relatorio", "core_retry_fast_v29.py", "value=await wait_for_cancelling_processes(coro,timeout_s)",
     "value=await asyncio.wait_for(coro,timeout=timeout_s)", "_bounded: curl: ainda vivo"),
    ("medicao_indisponivel", "rx_proc_stats.py", "if read is not None and pid in table:", "if read is not None:",
     "zero inventado"),
    ("medicao_indisponivel", "rx_proc_stats.py", "    if age < -AGE_TOLERANCE_S:\n        return None\n", "",
     "idade negativa virou número"),
    ("medicao_indisponivel", "rx_proc_stats.py", "                    rss_unknown += 1  # vivo e sem leitura: somar 0 seria inventar",
     "                    pass", "somou zero"),
    ("medicao_indisponivel", "rx_proc_stats.py", "        if not lost:  # com entrada ilegível o total do contêiner não é o total",
     "        if True:", "entrada ilegível"),
    ("config_intervalo", "rx_proc_stats.py", "    if minutes < MIN_INTERVAL_MIN:", "    if False:", "piso de 1 min"),
    ("medicao_real", "rx_proc_stats.py",
     "        out[\"zumbis\"] = sum(1 for k in direct if table[k][\"state\"] == \"Z\")", "        out[\"zumbis\"] = 0", "zumbis"),
    ("medicao_real", "rx_proc_stats.py", "len(os.listdir(f\"{proc_root}/{pid}/fd\")) - 1", "len(os.listdir(f\"{proc_root}/{pid}/fd\"))",
     "descritores"),
    ("sem_dado_sensivel", "rx_proc_stats.py", "names[comm if _COMM_OK.match(comm) else \"outro\"] += 1",
     "names[(_read(f\"{proc_root}/{k}/cmdline\") or comm).replace(chr(0), \" \")[:400]] += 1", "vazou"),
    ("rota_protegida", "rx_proc_stats.py", "        if not authorized(token):", "        if False:", "sem segredo respondeu 200"),
    ("log_arranque_periodico", "rx_proc_stats.py",
     "            app.state.rx_proc_stats_task = asyncio.create_task(_periodic(interval))", "            pass", "periodico"),
    ("servicos_ligados", "report_api.py", "_proc_stats.install(app)\n", "\n", "medição não instalada"),
    ("inventario_processos", "report_quick_v22.py",
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6)",
     "car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=6)", "wait_for puro"),
    ("inventario_processos", "anm_fast_v29.py", "],capture_output=True,timeout=9)", "],capture_output=True)", "sem timeout"),
    ("ci_roda_o_gate", ".github/workflows/quality-gate.yml", "python scripts/m1_processos_gate.py --exigir-linux",
     "python scripts/m1_processos_gate.py", "não exige Linux"),
]


def run_mutant(rule: str, rel: str, old: str, new: str, expect: str) -> tuple[bool, str]:
    src = (ROOT / rel).read_text(encoding="utf-8")
    if src.count(old) != 1:
        return False, f"âncora da mutação não casa 1x em {rel}: {old[:80]!r} ({src.count(old)}x)"
    with tempfile.TemporaryDirectory(prefix="rx_m1_mut_") as td:
        target = Path(td) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src.replace(old, new), encoding="utf-8", newline="\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if rel.endswith(".yml"):
            env["RX_M1_WORKFLOW"] = str(target)
        else:
            env["RX_M1_MUTANT_DIR"] = td
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--regra", rule], cwd=str(ROOT), env=env,
                              capture_output=True, timeout=600)
    out = (proc.stdout + proc.stderr).decode("utf-8", "ignore")
    line = next((x for x in out.splitlines() if x.startswith(f"FALHA {rule}")), "")
    if proc.returncode == 0 or not line:
        return False, f"a regra passou com a mutação (saída: {out[-400:]!r})"
    if expect not in line:
        return False, f"reprovou pelo motivo errado: {line[:400]}"
    return True, line[:220]


def run_rule(name: str) -> tuple[bool, str]:
    fn, linux_only = RULES[name]
    if linux_only and not LINUX:
        return True, "nao_avaliada_sem_linux"
    try:
        detail = fn()
        if LINUX:
            assert_no_children(f"depois da regra {name}")
        return True, str(detail)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=4).replace("\n", " | ")
        return False, f"{type(exc).__name__}: {exc} [{tb[-700:]}]"
    finally:
        with contextlib.suppress(Exception):
            epl()._SERVER_STOPPING.clear()


def main() -> int:
    args = sys.argv[1:]
    if "--regra" in args:
        name = args[args.index("--regra") + 1]
        ok, why = run_rule(name)
        print(f"PASSA {name}: {why}" if ok else f"FALHA {name}: {why}", flush=True)
        return 0 if ok else 1
    if "--exigir-linux" in args and not LINUX:
        print("RX_M1_PROCESSOS_GATE=FALHA precisa de Linux com /proc (nada simulado)", flush=True)
        return 2
    failures: list[str] = []
    skipped: list[str] = []
    for name in RULES:
        t0 = time.monotonic()
        ok, why = run_rule(name)
        if why == "nao_avaliada_sem_linux":
            skipped.append(name)
            print(f"NAO_AVALIADA {name} (precisa de Linux)", flush=True)
            continue
        print(f"{'PASSA' if ok else 'FALHA'} {name} ({time.monotonic() - t0:.1f}s): {why}", flush=True)
        if not ok:
            failures.append(name)
    controls = 0
    if LINUX and "--sem-controles" not in args:
        for rule, rel, old, new, expect in MUTATIONS:
            ok, why = run_mutant(rule, rel, old, new, expect)
            controls += 1
            print(f"{'CONTROLE_OK' if ok else 'CONTROLE_FALHOU'} {rule} <- {rel}: {why}", flush=True)
            if not ok:
                failures.append(f"controle:{rule}:{rel}")
    if failures:
        print(f"RX_M1_PROCESSOS_GATE=FALHA {failures}", flush=True)
        return 1
    if skipped or not LINUX:
        print(f"RX_M1_PROCESSOS_GATE=PARCIAL_SEM_LINUX regras_ok={len(RULES) - len(skipped)} nao_avaliadas={skipped}", flush=True)
        return 2 if "--exigir-linux" in args else 0
    print(f"RX_M1_PROCESSOS_GATE=PASS regras={len(RULES)} controles={controls} filhos_restantes=0", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

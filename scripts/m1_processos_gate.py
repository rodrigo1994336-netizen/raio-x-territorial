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
  desistencia_apos_prazo     depois do prazo estourado, o caminho de reserva abre UMA consulta nova (é o
                             produto: o CAR sozinho ainda serve). Se o cliente desistir aí, o curl do caminho
                             de reserva some e nenhuma consulta nova nasce — e o handler responde 499.
                             Medido em 15/09: uvicorn NÃO cancela a tarefa de uma rota `async def` sem o
                             parâmetro `request` quando o cliente fecha a conexão; por isso a desistência só
                             chega à cadeia pela vigia do wait_for_cancelling_processes(request=...).
  desistencia_sem_reserva    os cinco handlers SEM caminho de reserva (quick_analysis_v24,
                             critical_minerals_v34, climate_detail, groundwater_detail, crop_context): o
                             cliente desiste com o curl da primeira consulta vivo -> nada sobra, nada nasce
                             depois, resposta 499.
  cenario_importa_sozinho    todo módulo que os cenários importam tem que importar sozinho, em processo
                             limpo: remendo de HTML que depende de outro módulo ter vindo antes derruba o
                             arranque do portal e faz o cenário reprovar pelo motivo errado.
  escopo_no_pool             ThreadPoolExecutor não copia o contexto: o sondador de camadas IDE embrulha cada
                             trabalho em in_current_scope, e o curl do trabalhador cai no prazo do escopo.
  copia_de_escopo_no_pool    REGRA DE FORMA: todo submit/map/run_in_executor dos módulos do servidor embrulha
                             o trabalho em in_current_scope. Exemplar consertado não é prova — quem chega ao
                             processo por parâmetro (get=, http_get=) é invisível para o fecho por nome, e foi
                             assim que três módulos ficaram convertidos e inertes.
  vigia_do_cliente_em_todo_handler
                             REGRA DE FORMA: função que abre escopo recebe `request` e o repassa em TODAS as
                             chamadas; rota que chega a um escopo declara e usa `request`; e todo handler de
                             PRAZO_HANDLERS aparece numa cobertura de desistência (ou declarado COM MOTIVO).
  busca_car_no_escopo        nenhum asyncio.to_thread(fetch_car_live*) solto: a cadeia do CAR é a longa
                             (car_resilient.WORST_CASE_SECONDS). O que ainda corre fora do escopo está
                             declarado com motivo — a busca do CAR e também CADA outra fonte, uma a uma, com
                             o número total travado. Motivo que sobra (fonte que já entrou no escopo) também
                             reprova: inventário que envelhece vira dívida imaginária.
  nome_do_car_resolve_em_execucao
                             sonda em execução: imprime para que função os nomes fetch_car_live resolvem
                             DEPOIS do arranque (o car_resilient troca o nome) e confere que todo prazo posto
                             em cima deles é >= o pior caso declarado pelo módulo que os implementa.
  inventario_processos       todo subprocess.run/call/check_* dos módulos do servidor tem timeout; Popen só
                             no módulo de ciclo de vida; sem os.system/os.popen/fork/exec/spawn/posix_spawn/
                             getoutput/subprocess_exec/multiprocessing; lista declarada dos módulos com
                             chamada direta (inclusive quem passa br_bridge.subprocess_runner), CADA UM COM O
                             MOTIVO de não ter sido convertido; nenhum asyncio.wait_for puro sobre função que
                             chega a run_managed_process (fecho transitivo por nome, não lista à mão); os
                             pontos com escopo contados.
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


@contextlib.contextmanager
def gated_first_wait_for(pred, extra_modules=()):
    """Como o gated_wait_for, mas só o PRIMEIRO asyncio.wait_for é estourado (prazo 0, depois de pred()).
    Os seguintes correm com o prazo de verdade: é assim que o caminho de reserva chega a nascer e pode ser
    observado. Sem isso o segundo prazo estouraria na hora (pred já é verdade) e o gate mediria outra coisa."""
    modules = (epl(), *extra_modules)
    real = asyncio.wait_for
    fired: list[bool] = []

    async def gated(aw, timeout):
        task = asyncio.ensure_future(aw)
        if fired:
            return await real(task, timeout)
        fired.append(True)
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
            # Com o caminho de reserva TAMBÉM dentro do escopo, o instrumento estoura o prazo dele junto:
            # o handler pode terminar em "consulta pendente" antes de o gate olhar. Isso é resposta honesta;
            # o que não pode é sobrar curl (conferido logo abaixo).
            if task.done() and not task.cancelled() and task.exception() is not None \
                    and getattr(task.exception(), "status_code", None) not in (502, 503, 504):
                fail(f"{name}: falhou antes de o curl do prazo cair: {task.exception()!r}"[:300], children_now() - before)
            if "pid" not in first:
                fail(f"{name}: terminou sem consultar o SICAR ({task.result() if task.done() else 'em curso'!r})"[:300])
            pid = first["pid"]
            took = await asyncio.to_thread(assert_gone, pid, f"{name}: curl do prazo")
        if fallback:
            # o caminho de reserva consulta de novo (o CAR sozinho ainda serve) e corre dentro do escopo: com o
            # prazo dele também estourado pelo instrumento, o handler termina em "consulta pendente" e NÃO
            # deixa curl vivo — sem o desligamento de emergência que antes escondia a sobra.
            try:
                outcome = await asyncio.wait_for(asyncio.shield(task), 20)
            except BaseException as exc:  # noqa: BLE001
                outcome = exc
            if not (isinstance(outcome, dict) or getattr(outcome, "status_code", None) in (502, 503, 504)):
                fail(f"{name}: resposta inesperada do caminho de reserva: {outcome!r}"[:300], children_now() - before)
            await assert_none_left(before, f"{name}: curl do caminho de reserva")
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


DESISTENCIA_HANDLERS = (
    ("report_v9_patch", "quick_analysis"),
    ("portal_property_tabs", "embargos_detail"),
)


@regra("desistencia_apos_prazo", linux=True)
def r_desistencia():
    """Prazo estourado + cliente desistiu: nenhum filho continua vivo e nenhuma consulta nova é aberta.

    O curl falso trava. O primeiro prazo é estourado de propósito (relógio controlado) com o curl vivo; o
    caminho de reserva nasce e abre o curl dele; aí o cliente desiste. Daí em diante nada pode sobrar."""
    import importlib
    mod = epl()
    import car_resilient  # noqa: F401 - instala a busca resiliente do SICAR em deploy_app/report_api
    notes: list[str] = []

    async def scenario(module, name: str) -> str:
        try:
            return await _scenario(module, name)
        except BaseException:
            # o mutante deixa thread viva com curl travado; parar tudo aqui DENTRO do laco evita que o
            # asyncio.run fique 300 s juntando a thread na saida (e o gate travar em vez de reprovar)
            await asyncio.to_thread(mod.terminate_active_processes)
            raise

    async def _scenario(module, name: str) -> str:
        handler = getattr(module, name)
        before = children_now()
        first: dict[str, int] = {}
        # Nascimentos de curl contados no Popen: "abriu consulta nova" é um evento, não um pid adivinhado
        # (o pid de um curl que morreu pode ser reaproveitado, e a cadeia tem mais de um curl).
        births: list[float] = []

        def counting(real):
            def popen(*a, **kw):
                args = a[0] if a else kw.get("args") or []
                if args and "curl" in str(args[0]):
                    births.append(time.monotonic())
                return real(*a, **kw)
            return popen

        def first_curl() -> bool:
            if "pid" not in first:
                pid = born_registered(before, "sleep")
                if pid is None:
                    return False
                first["pid"] = pid
            return True

        request = Request()
        with patched_popen(counting), gated_first_wait_for(first_curl, extra_modules=(module,)):
            task = asyncio.create_task(handler(CAR, request=request))
            if not await await_until(lambda: "pid" in first or task.done(), 20.0):
                fail(f"{name}: o curl falso não nasceu em 20 s", children_now() - before)
            if "pid" not in first:
                fail(f"{name}: terminou sem consultar o SICAR")
            await asyncio.to_thread(assert_gone, first["pid"], f"{name}: curl do prazo")
            marca = len(births)
            # o caminho de reserva TEM de abrir consulta nova e ter filho vivo: sem isso o cliente receberia
            # menos do que hoje, e o cenário da desistência não estaria sendo medido
            if not await await_until(lambda: len(births) > marca and bool(children_now() - before), 25.0):
                fail(f"{name}: o caminho de reserva não consultou o SICAR depois do prazo "
                     f"(nascimentos={len(births)})", children_now() - before)
            request.disconnected = True          # o cliente desiste AGORA
            na_desistencia = len(births)
            t0 = time.monotonic()
            await assert_none_left(before, f"{name}: curl do caminho de reserva")
            took = round(time.monotonic() - t0, 3)
            try:
                outcome = await asyncio.wait_for(asyncio.shield(task), 15)
            except BaseException as exc:  # noqa: BLE001 - CancelledError tambem e resposta errada, e nao e Exception
                outcome = exc
            await asyncio.sleep(1.0)
            novas = len(births) - na_desistencia
            if novas:
                fail(f"{name}: {novas} consulta(s) nova(s) abertas depois da desistência", children_now() - before)
        if getattr(outcome, "status_code", None) != 499:
            fail(f"{name}: resposta inesperada depois da desistência: {outcome!r}"[:300], children_now() - before)
        await assert_none_left(before, f"{name}: filho vivo depois da desistência")
        return f"{name} {took}s ({len(births)} curls no total)"

    with fake_curl():
        for module_name, name in DESISTENCIA_HANDLERS:
            module = importlib.import_module(module_name)
            try:
                notes.append(asyncio.run(scenario(module, name)))
            finally:
                mod._SERVER_STOPPING.clear()
            assert_no_children(f"depois de {name}")
    return "prazo + desistência não deixam filho nem abrem consulta nova: " + ", ".join(notes)


# Handlers sem caminho de reserva: a desistência é medida na PRIMEIRA consulta (o cenário acima exige uma
# segunda consulta depois do prazo, que estes não têm). Sem esta regra, apagar ",request=request" deles
# passava em silêncio: a regra de prazo só exercita o prazo.
DESISTENCIA_SEM_RESERVA = (
    ("report_quick_v22", "quick_analysis_v24"),
    ("portal_mining_resilience_v34", "critical_minerals_v34"),
    ("portal_property_tabs", "climate_detail"),
    ("portal_property_tabs", "groundwater_detail"),
    ("portal_property_tabs", "crop_context"),
)


@regra("desistencia_sem_reserva", linux=True)
def r_desistencia_simples():
    """O cliente desiste com o curl da primeira consulta vivo: nada pode sobrar e nada pode nascer depois."""
    import importlib
    mod = epl()
    import car_resilient  # noqa: F401 - instala a busca resiliente do SICAR em deploy_app/report_api
    notes: list[str] = []

    async def scenario(module, name: str) -> str:
        handler = getattr(module, name)
        before = children_now()
        births: list[float] = []

        def counting(real):
            def popen(*a, **kw):
                args = a[0] if a else kw.get("args") or []
                if args and "curl" in str(args[0]):
                    births.append(time.monotonic())
                return real(*a, **kw)
            return popen

        request = Request()
        with patched_popen(counting):
            task = asyncio.create_task(handler(CAR, request=request))
            if not await await_until(lambda: born_registered(before, "sleep") is not None or task.done(), 20.0):
                fail(f"{name}: o curl falso não nasceu em 20 s", children_now() - before)
            if born_registered(before, "sleep") is None:
                fail(f"{name}: terminou sem consultar o SICAR ({task.result() if task.done() else 'em curso'!r})"[:300])
            request.disconnected = True          # o cliente desiste AGORA, com o curl vivo
            na_desistencia = len(births)
            t0 = time.monotonic()
            await assert_none_left(before, f"{name}: curl depois da desistência")
            took = round(time.monotonic() - t0, 3)
            try:
                outcome = await asyncio.wait_for(asyncio.shield(task), 15)
            except BaseException as exc:  # noqa: BLE001 - CancelledError também é resposta errada
                outcome = exc
            await asyncio.sleep(1.0)
            novas = len(births) - na_desistencia
            if novas:
                fail(f"{name}: {novas} consulta(s) nova(s) abertas depois da desistência", children_now() - before)
        if getattr(outcome, "status_code", None) != 499:
            fail(f"{name}: resposta inesperada depois da desistência: {outcome!r}"[:300], children_now() - before)
        await assert_none_left(before, f"{name}: filho vivo depois da desistência")
        return f"{name} {took}s"

    with fake_curl():
        for module_name, name in DESISTENCIA_SEM_RESERVA:
            module = importlib.import_module(module_name)
            try:
                notes.append(asyncio.run(scenario(module, name)))
            finally:
                mod._SERVER_STOPPING.clear()
            assert_no_children(f"depois de {name}")
    return "desistência na primeira consulta não deixa filho nem abre consulta nova: " + ", ".join(notes)


# Este portão importa os módulos do handler fora da ordem do arranque real (uma regra por vez, em processo
# próprio). Módulo que remenda o HTML do portal precisa declarar de quem herdou a âncora: senão o cenário
# reprova por motivo alheio ao que prova E o portal fica preso a uma ordem que ninguém escreveu. Medido em
# 16/09: portal_mining_resilience_v34 só casava as duas âncoras depois de portal_property_tabs, e o controle
# de desistencia_sem_reserva reprovou com mining_resilience_anchor_missing.
#
# 16/09, segunda parte: aquele módulo deixou de DERRUBAR o arranque quando a âncora some (as duas vivem em
# código que o portal_experience_v43 desliga; nenhum cliente as vê, e um texto que ninguém vê não pode apagar
# o portal inteiro). Com isso, "o módulo importou" deixou de provar "a âncora casou": esta regra passa a ler
# também o marcador _ANCHOR_MISSING= na saída. Sem essa leitura o controle por mutação deste par passaria com
# o defeito posto de volta — e passou, até esta linha existir.
@regra("cenario_importa_sozinho")
def r_cenario_importa_sozinho():
    """Cada módulo que os cenários importam tem que importar sozinho, em processo limpo."""
    modulos = sorted({m for m, *_ in PRAZO_HANDLERS} | {m for m, _ in DESISTENCIA_HANDLERS}
                     | {m for m, _ in DESISTENCIA_SEM_RESERVA})
    if not modulos:
        fail("instrumento quebrado: nenhum módulo de cenário encontrado nas listas do portão")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", RX_RELEASE="OFF", RX_RASTERIO_RUNTIME_INSTALL="off")
    env.pop("RX_M1_MUTANT_DIR", None)
    # sys.path[0] do -c é o cwd (a árvore original): a cópia mutada precisa entrar antes dele, à mão.
    prefixo = f"import sys;sys.path.insert(0,{MUTANT_DIR!r});" if MUTANT_DIR else ""
    ruins: list[str] = []
    for nome in modulos:
        proc = subprocess.run([sys.executable, "-c", prefixo + f"import {nome}"], cwd=str(ROOT), env=env,
                              capture_output=True, timeout=600)
        saida = (proc.stdout + proc.stderr).decode("utf-8", "ignore")
        if proc.returncode:
            linhas = [x for x in saida.splitlines() if x.strip()]
            ruins.append(f"{nome} -> {linhas[-1][:200] if linhas else 'sem saída'}")
        else:
            perdidas = [x.strip() for x in saida.splitlines() if "_ANCHOR_MISSING=" in x]
            if perdidas:
                ruins.append(f"{nome} -> {perdidas[-1][:200]}")
    if ruins:
        fail("módulo do cenário não importa sozinho (o remendo depende da ordem do arranque): " + "; ".join(ruins))
    return f"{len(modulos)} módulos do cenário importam sozinhos: {', '.join(modulos)}"


@regra("escopo_no_pool", linux=True)
def r_escopo_pool():
    """ThreadPoolExecutor.submit NÃO copia o contexto: sem cópia por trabalho o curl do trabalhador nasce fora
    do escopo e sobrevive ao prazo. Exercita o sondador real das camadas IDE."""
    import ide_layer_probe
    mod = epl()
    bbox = [-44.5, -18.8, -44.4, -18.7]        # dentro de Minas: o sondador só consulta quando cruza MG
    geom = {"type": "Polygon", "coordinates": [[[bbox[0], bbox[1]], [bbox[2], bbox[1]],
                                                [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]}

    async def scenario():
        before = children_now()
        with gated_wait_for(lambda: new_child(before, "sleep") is not None):
            with contextlib.suppress(Exception):
                await mod.wait_for_cancelling_processes(
                    asyncio.to_thread(ide_layer_probe.probe_benchmark, geom, bbox), 30)
        await assert_none_left(before, "sondador IDE: curl do trabalhador do pool")
        return "nenhum curl do pool sobra"

    with fake_curl():
        detail = asyncio.run(scenario())
    return detail


# ------------------------------------------------------------------ inventário estático
# Módulo que ainda cria processo fora do run_managed_process -> MOTIVO de não ter sido convertido.
# Os quinze que chegavam a uma requisição de cliente foram convertidos em 15/09 e saíram desta lista.
# Um motivo vazio reprova: "sobrou" não é motivo.
DIRECT_DECLARED = {
    "br_bridge.py": "subprocess_runner é o executor legado que a ponte ainda oferece a quem chama de fora do "
                    "servidor (conferência do guia no Cloud Shell); nenhum módulo do servidor o passa mais",
    "jwt_runtime_bootstrap.py": "pip no arranque do processo, antes de existir requisição; nada a cancelar",
    "postgres_runtime_bootstrap.py": "pip no arranque do processo, antes de existir requisição; nada a cancelar",
    "rasterio_runtime_bootstrap.py": "pip no arranque do processo, antes de existir requisição; nada a cancelar",
    "redis_runtime_bootstrap.py": "pip no arranque do processo, antes de existir requisição; nada a cancelar",
}
# Quem chega a um processo gerenciado é calculado (fecho transitivo por nome a partir destas sementes), não
# mantido à mão: por nome é conservador (duas funções com o mesmo nome contam juntas).
PROCESS_SEEDS = {"run_managed_process"}
# Chamadas de wait_for_cancelling_processes por arquivo, contadas pela ÁRVORE, não pelo texto. Era
# text.count("await wait_for_cancelling_processes("): um escopo escrito dentro de um asyncio.gather não leva
# `await` na frente e não era contado — a regra dizia "2" num arquivo com 4 escopos e ninguém via a
# diferença. Contagem literal responde sobre a grafia; a pergunta é sobre a chamada.
SCOPED_SITES = {
    "portal_mining_resilience_v34.py": 2, "report_quick_v22.py": 1, "report_v9_patch.py": 2,
    "portal_property_tabs.py": 4, "core_retry_fast_v29.py": 1, "report_extras_perf_v30.py": 1,
    "portal_advanced_name_v40.py": 1, "property_search.py": 2, "heavy_live_api_v20.py": 1,
    "portal_live_fix_v18.py": 1, "portal_api.py": 1,
    # E1 (16/09): fontes do cartão, da leitura completa, do PDF, do mapa e da busca que entraram no escopo.
    "portal_car_integrity_v47.py": 1, "portal_conformity_sinaflor_v48.py": 1,
    "portal_incra_certified_v42.py": 2, "property_names_viewport_v30.py": 1,
    "map_mineral_routes.py": 1, "portal_cafir_inverse_v44.py": 1,
}
# Quais escopos têm efeito NÃO é mais afirmação do autor: sai do mesmo fecho transitivo. Um sítio cujo
# trabalho chega a run_managed_process tem efeito; um sítio que recebe a corrotina pronta por parâmetro é
# indecidível aqui (o fecho por nome não atravessa parâmetro) e precisa ser declarado, com a regra em
# execução que o prova; qualquer outro é decorativo e reprova.
SCOPED_OPACO_DECLARADO = {
    "core_retry_fast_v29.py:_bounded":
        "recebe a corrotina pronta de quem chama; que o escopo tem efeito é provado em execução por "
        "cadeia_slots_relatorio (o curl do slot estourado morre)",
    "report_extras_perf_v30.py:_timed":
        "mesmo caso, no slot do relatório; provado em execução por cadeia_slots_relatorio",
}
DIRECT_PROCESS_CALLS = {"os.system", "os.popen", "os.fork", "os.forkpty", "os.posix_spawn", "os.posix_spawnp",
                        "pty.fork", "pty.spawn", "subprocess.getoutput", "subprocess.getstatusoutput"}
DIRECT_PROCESS_PREFIXES = ("os.exec", "os.spawn", "asyncio.create_subprocess")
DIRECT_PROCESS_ATTRS = {"subprocess_exec", "subprocess_shell"}  # loop.subprocess_exec / subprocess_shell


def _e_chamada_de_escopo(node: ast.AST) -> bool:
    """Chamada de wait_for_cancelling_processes, com ou sem `await` colado na frente."""
    return isinstance(node, ast.Call) and (getattr(node.func, "id", None) == "wait_for_cancelling_processes"
                                           or getattr(node.func, "attr", None) == "wait_for_cancelling_processes")


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


def sem_motivo_declarado(declared: dict[str, str]) -> list[str]:
    """Módulo declarado sem motivo de verdade. "sobrou" não é motivo: exige uma frase."""
    faltando = sorted(name for name, why in declared.items() if len(str(why).strip()) < 20)
    return [f"módulo com processo direto sem motivo declarado: {faltando}"] if faltando else []


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
    escopos_por_arquivo = {
        name: sum(1 for node in ast.walk(tree) if _e_chamada_de_escopo(node)) for name, tree in trees.items()
    }
    for name, count in SCOPED_SITES.items():
        got = escopos_por_arquivo.get(name, 0)
        if got != count:
            problems.append(f"{name}: {got} chamada(s) de wait_for_cancelling_processes, esperado {count}")
    for name in sorted(set(trees) - set(SCOPED_SITES)):
        if escopos_por_arquivo.get(name, 0):
            problems.append(f"{name} abriu escopo e não está no inventário SCOPED_SITES")
    com_efeito = opacos = 0
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if not _e_chamada_de_escopo(node):
                continue
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Name):   # corrotina recebida por parâmetro: indecidível por nome
                chave = f"{name}:{_dono(tree, node)}"
                if chave in SCOPED_OPACO_DECLARADO:
                    opacos += 1
                    continue
                problems.append(f"{name}:{node.lineno} escopo sobre corrotina recebida por parâmetro, sem "
                                f"declaração de como se prova o efeito ({chave})")
                continue
            nomes = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)} |                     {n.attr for n in ast.walk(arg) if isinstance(n, ast.Attribute)} if arg is not None else set()
            if nomes & reaching:
                com_efeito += 1
            else:
                problems.append(f"{name}:{node.lineno} escopo sem efeito: o trabalho dentro dele não chega a "
                                f"processo gerenciado (sítio decorativo)")
    problems.extend(sem_motivo_declarado(SCOPED_OPACO_DECLARADO))
    if direct != set(DIRECT_DECLARED):
        problems.append(f"lista de chamadas diretas mudou: novas {sorted(direct - set(DIRECT_DECLARED))} "
                        f"sumidas {sorted(set(DIRECT_DECLARED) - direct)}")
    problems.extend(sem_motivo_declarado(DIRECT_DECLARED))
    # controle positivo da própria checagem: mutar o ARQUIVO do gate não tem efeito (o gate roda do original,
    # só os módulos vem do MUTANT_DIR), então a prova de que esta regra não é inerte fica aqui.
    if not sem_motivo_declarado({**DIRECT_DECLARED, "br_bridge.py": " "}):
        problems.append("a checagem de motivo declarado não pega motivo vazio (regra inerte)")
    assert not problems, "inventário: " + " | ".join(problems)
    sites = sum(SCOPED_SITES.values())
    return (f"{len(direct)} módulos com processo direto limitado por timeout, todos com motivo declarado; "
            f"{len(reaching)} funções chegam a processo gerenciado; {sites} prazos com escopo, "
            f"{com_efeito} com efeito provado pelo fecho e {opacos} declarados (provados em execução)")


# ------------------------------------------------------------------ forma (regras estáticas)
# Regra de forma, não de exemplar: consertar um sítio e escrever o portão para esse mesmo sítio certifica
# como pronta uma família que pode estar 1/4 consertada. Estas três regras enumeram a família inteira.

# --- 1) o escopo tem de atravessar todo pool -----------------------------------------------------------
# ThreadPoolExecutor.submit/.map e loop.run_in_executor NÃO copiam o contexto (asyncio.to_thread copia): sem
# in_current_scope o trabalhador não enxerga o escopo e o curl dele sobrevive ao prazo e à desistência. Quem
# chega ao processo gerenciado por PARÂMETRO (get=, http_get=, post=) é invisível para o fecho por nome, por
# isso a exigência é da forma em TODO sítio de pool, e não só nos que o fecho consegue provar.
POOL_WRAPPERS = ("in_current_scope",)
POOL_DECLARADO: dict[str, str] = {}  # "arquivo.py:linha": motivo de o trabalho não precisar do escopo


def _is_executor(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return str(name).endswith("Executor")


def _pool_names(tree: ast.AST) -> set[str]:
    """Nomes ligados a um executor neste módulo (with ... as X, X = ThreadPoolExecutor(...))."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name) and _is_executor(node.context_expr):
            names.add(node.optional_vars.id)
        elif isinstance(node, ast.Assign) and _is_executor(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _carrega_escopo(arg: ast.AST | None) -> bool:
    if not isinstance(arg, ast.Call):
        return False
    f = arg.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return name in POOL_WRAPPERS


@regra("copia_de_escopo_no_pool")
def r_pool_forma():
    problems: list[str] = []
    sitios = 0
    for path in runtime_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        pools = _pool_names(tree)
        usa_executor = "Executor" in path.read_text(encoding="utf-8")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if attr in ("submit", "map"):
                dono = node.func.value.id if isinstance(node.func.value, ast.Name) else None
                if dono not in pools:
                    if usa_executor and attr == "submit":
                        problems.append(f"{path.name}:{node.lineno} .submit() em objeto que não reconheci como "
                                        f"pool ({dono}): o instrumento não sabe dizer se o escopo viaja")
                    continue
                alvo = node.args[0] if node.args else None
            elif attr == "run_in_executor":
                alvo = node.args[1] if len(node.args) > 1 else None
            else:
                continue
            sitios += 1
            chave = f"{path.name}:{node.lineno}"
            if _carrega_escopo(alvo) or chave in POOL_DECLARADO:
                continue
            problems.append(f"{chave} trabalho submetido ao pool sem in_current_scope(...): o escopo de "
                            f"cancelamento não chega ao trabalhador")
    problems.extend(sem_motivo_declarado(POOL_DECLARADO))
    assert not problems, "pool: " + " | ".join(problems)
    return f"{sitios} sítios de pool, todos carregando o escopo ({len(POOL_DECLARADO)} declarados sem)"


# --- 2) a vigia do cliente em toda a família ----------------------------------------------------------
# Handler que chega a wait_for_cancelling_processes tem de receber `request` e repassá-lo em TODAS as
# chamadas. Sem isto, apagar ",request=request" de um handler passa em silêncio: a regra de prazo não
# exercita a desistência, e o inventário conta CHAMADAS por arquivo, não os argumentos delas.
VIGIA_DECLARADA = {
    "core_retry_fast_v29.py:_bounded":
        "slot interno da nova tentativa: recebe a corrotina já criada por quem está num escopo com request, "
        "então a desistência chega pelo escopo de cima; provado em execução por cadeia_slots_relatorio",
    "report_extras_perf_v30.py:_timed":
        "mesmo caso: slot do relatório, a corrotina é criada pelo chamador dentro do escopo dele; provado em "
        "execução por cadeia_slots_relatorio",
    "portal_advanced_name_v40.py:one":
        "enriquecimento de referência dentro do gather da busca nominal; o handler que chama ainda não "
        "repassa request (fila do próximo passo) e o prazo de 10 s já limita cada ponto",
}
HANDLER_SEM_VIGIA_DECLARADO: dict[str, str] = {}
# Todo handler com prazo vigiado tem de aparecer também numa cobertura de desistência — ou aqui, COM MOTIVO.
DESISTENCIA_DECLARADA: dict[str, str] = {}


def _decorador_de_rota(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        alvo = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(alvo, ast.Attribute) and alvo.attr in ("get", "post", "put", "delete", "patch"):
            return True
    return False


def _funcoes(tree: ast.AST) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _nos_proprios(fn: ast.AST) -> list[ast.AST]:
    """Nós da função SEM descer nas funções aninhadas: cada uma responde pelas chamadas dela.

    Sem isto, a função de fora é acusada pela chamada da de dentro (e a declaração que isenta a de dentro
    não a isenta), que é o mesmo erro de atribuir a um representante o que é da família."""
    out: list[ast.AST] = []
    pilha = [c for c in ast.iter_child_nodes(fn) if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))]
    while pilha:
        node = pilha.pop()
        out.append(node)
        pilha.extend(c for c in ast.iter_child_nodes(node)
                     if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return out


def _nomes_usados(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _tem_parametro(fn: ast.AST, nome: str) -> bool:
    a = fn.args
    return any(arg.arg == nome for arg in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs))


@regra("vigia_do_cliente_em_todo_handler")
def r_vigia_estatica():
    problems: list[str] = []
    handlers = 0
    for path in runtime_modules():
        texto = path.read_text(encoding="utf-8")
        if "wait_for_cancelling_processes" not in texto:
            continue
        tree = ast.parse(texto, filename=path.name)
        funcs = _funcoes(tree)
        com_escopo: set[str] = set()
        for fn in funcs:
            chamadas = [n for n in _nos_proprios(fn) if isinstance(n, ast.Call)
                        and (getattr(n.func, "id", None) == "wait_for_cancelling_processes"
                             or getattr(n.func, "attr", None) == "wait_for_cancelling_processes")]
            if not chamadas:
                continue
            com_escopo.add(fn.name)
            chave = f"{path.name}:{fn.name}"
            if chave in VIGIA_DECLARADA:
                continue
            if not _tem_parametro(fn, "request"):
                problems.append(f"{chave} abre escopo sem receber request: a desistência do cliente nunca chega")
            sem_request = [n.lineno for n in chamadas if not any(k.arg == "request" for k in n.keywords)]
            if sem_request:
                problems.append(f"{chave} chama wait_for_cancelling_processes sem request= nas linhas {sem_request}")
        # quem chama (por nome, dentro do módulo) uma função com escopo também precisa carregar o request
        alcanca = set(com_escopo)
        mudou = True
        while mudou:
            mudou = False
            for fn in funcs:
                if fn.name not in alcanca and _nomes_usados(fn) & alcanca:
                    alcanca.add(fn.name)
                    mudou = True
        for fn in funcs:
            if not _decorador_de_rota(fn) or fn.name not in alcanca:
                continue
            handlers += 1
            chave = f"{path.name}:{fn.name}"
            if chave in HANDLER_SEM_VIGIA_DECLARADO:
                continue
            if not _tem_parametro(fn, "request"):
                problems.append(f"{chave} é rota que chega a um escopo e não declara request")
            elif "request" not in _nomes_usados(fn):
                problems.append(f"{chave} declara request e não o usa: vigia decorativa")
    falta_desistencia = [f"{m}.{h}" for m, h, _ in PRAZO_HANDLERS
                         if (m, h) not in DESISTENCIA_HANDLERS and (m, h) not in DESISTENCIA_SEM_RESERVA
                         and f"{m}.{h}" not in DESISTENCIA_DECLARADA]
    if falta_desistencia:
        problems.append(f"handler com prazo vigiado e sem cobertura de desistência: {falta_desistencia}")
    problems.extend(sem_motivo_declarado(VIGIA_DECLARADA))
    problems.extend(sem_motivo_declarado(HANDLER_SEM_VIGIA_DECLARADO))
    problems.extend(sem_motivo_declarado(DESISTENCIA_DECLARADA))
    assert not problems, "vigia: " + " | ".join(problems)
    return (f"{handlers} rotas que chegam a um escopo recebem e repassam request; "
            f"{len(VIGIA_DECLARADA)} funções internas declaradas sem vigia, com motivo")


# --- 3) inventário de quem ainda corre fora do escopo --------------------------------------------------
# A busca do CAR é a cadeia longa (car_resilient.WORST_CASE_SECONDS): um asyncio.to_thread(fetch_car_live*)
# solto é rota de cliente vazando por minutos. O inventário antigo só via subprocess direto e contava
# chamadas por arquivo, então não enxergava este caso.
CAR_FORA_DO_ESCOPO_DECLARADO = {
    "report_api.py:_background_ide_probe":
        "sonda de arranque, sem cliente e sem requisição: não há desistência a ouvir, e o desligamento já "
        "derruba os filhos pelo _SERVER_STOPPING",
    "report_api.py:ide_probe":
        "rota interna de diagnóstico (/v1/internal/ide/probe), não é caminho de cliente; fila do próximo passo",
    "deploy_app.py:analyze_car":
        "chamada sempre DENTRO de um escopo pelos handlers (asyncio.to_thread copia o contexto): o escopo de "
        "quem chamou já alcança este curl, e é isso que cadeia_car_prazo mede",
}
# Fontes que NÃO são o CAR e ainda correm em to_thread fora de qualquer escopo. Antes isto era só um número
# (43 em 15/09): o número dizia QUANTAS faltavam, nunca POR QUE cada uma ficou. Agora cada ponto é declarado
# com motivo, e o número continua travado — as duas coisas, porque só o motivo deixaria a dívida crescer em
# silêncio e só o número deixaria "sobrou" passar por explicação.
#
# E1 (16/09): as dez que o cliente dispara entraram no escopo (cartão V46, leitura completa F1B, PDF móvel,
# mapa e busca). 43 -> 33. Cada teto tem DUAS parcelas, e elas não têm o mesmo estatuto:
#   (a) o prazo da FONTE, derivado do módulo que a implementa (nunca escrito à mão) — e, no caso da
#       integridade, também a CONTAGEM de consultas, conferida pela regra integridade_conta_consultas;
#   (b) a FOLGA para o trabalho local que corre na mesma thread depois que a rede volta (leitura de XML,
#       geometria, montagem de tabela). Estas nove folgas são ARBITRADAS, não medidas, e cada uma diz isso no
#       comentário do próprio módulo, com o tempo real da rota medido em 16/09 ao lado. O erro não é
#       simétrico: folga grande só adia o 504; folga pequena corta resposta que hoje chega.
#   portal_car_integrity_v47      2 x 5 consultas x sicar_integrity_v47.QUERY_TIMEOUT_S + montagem da tabela
#   portal_conformity_sinaflor_v48 2 x deploy_app.CURL_WORST_CASE_S + car_resilient.WORST_CASE_SECONDS
#   portal_property_tabs          climate_nasa.DAILY_WORST_CASE_S / CLIMATOLOGY_WORST_CASE_S
#   property_names_viewport_v30   deploy_app.CURL_WORST_CASE_S + property_identity_runtime.OSM_WORST_CASE_S
#   portal_incra_certified_v42    incra_snci_public_v42.CAPABILITIES/VIEWPORT_WORST_CASE_S
#   map_mineral_routes            deploy_app.CURL_WORST_CASE_S + simplificação das geometrias
#   portal_cafir_inverse_v44      cafir_name_search_v44.LOCATE_WORST_CASE_S
#   portal_pdf_v21                sem teto externo: mesma forma da rota /v1/live/property-identity, que
#                                 resolve o MESMO nome (cancel_event chega a cada curl da cadeia)
OUTRAS_FORA_DO_ESCOPO_DECLARADO = {
    # --- chamadas que JÁ correm dentro de um escopo em execução; o filtro estático é que não enxerga ---
    "report_extras_perf_v30.py:_extras_v41":
        "em escopo em execução: o _timed embrulha cada slot em wait_for_cancelling_processes, e o to_thread "
        "está escrito no chamador, não dentro da chamada — o filtro por sintaxe não atravessa parâmetro. "
        "Quem prova em execução é a regra cadeia_slots_relatorio",
    "deploy_app.py:analyze_car":
        "chamada sempre DENTRO de um escopo pelos handlers (asyncio.to_thread copia o contexto): o escopo de "
        "quem chamou já alcança este curl, e é isso que cadeia_car_prazo mede",
    "anm_fast_v29.py:query_anm_fast":
        "substitui deploy_app.query_anm/report_api.query_anm em execução e só roda dentro do analyze_car, "
        "que corre no escopo do handler; o to_thread copia o contexto e o curl herda o cancelamento",

    # --- código substituído em execução: a versão efetiva é outra, e essa já está no escopo ou declarada ---
    "live_report_adapter_v13.py:_extras":
        "substituído em execução por report_extras_perf_v30._extras_v41 (v13._extras=_extras_v41): base da "
        "cadeia de remendos, nunca executada no serviço do relatório",
    "live_report_adapter_v17.py:_extras_v17":
        "substituído em execução por report_extras_perf_v30._extras_v41 (v17._extras_v17=_extras_v41): elo "
        "da cadeia de remendos, nunca executado no serviço do relatório",
    "live_report_adapter_v19.py:_extras_v47":
        "elo da cadeia de remendos do relatório; o efetivo é o _extras_v41, carregado depois pelo "
        "sitecustomize. Entra no escopo junto com o v41 quando a fila do relatório chegar",
    "report_visual_identity_v28.py:_extras_v28":
        "substituído em execução por report_extras_perf_v30._extras_v41, importado depois no sitecustomize",
    "portal_advanced_name_v40.py:advanced_search_v40":
        "a rota /v1/live/search/advanced foi tomada pelo portal_cafir_inverse_v44, que remove a rota do v40 "
        "e registra a própria: esta função não recebe mais pedido de cliente",
    "portal_live_fix_v18.py:critical_minerals_v18":
        "a rota /v1/live/critical-minerals foi tomada pelo portal_mining_resilience_v34, carregado depois: "
        "esta função não recebe mais pedido de cliente",
    "report_api.py:_build":
        "as duas rotas de relatório do report_api são removidas pelo report_v13_patch, que registra as suas; "
        "o _build fica como base da cadeia e não recebe pedido de cliente",

    # --- sem cliente: arranque, segundo plano e diagnóstico interno ---
    "report_api.py:_background_full_smoke":
        "prova de fumaça do arranque, sem cliente e sem requisição: não há desistência a ouvir, e o "
        "desligamento já derruba os filhos pelo _SERVER_STOPPING",
    "report_api.py:_background_ide_probe":
        "sonda de arranque, sem cliente e sem requisição: não há desistência a ouvir, e o desligamento já "
        "derruba os filhos pelo _SERVER_STOPPING",
    "report_api.py:_background_catalog_probe":
        "sonda de catálogo do arranque, sem cliente e sem requisição: o desligamento já derruba os filhos",
    "report_api.py:_reapply_prodes_reading":
        "releitura em segundo plano sobre análise já entregue: não há cliente pendurado nesta chamada",
    "report_api.py:_retry_failed_core":
        "nova tentativa em segundo plano de fonte que falhou; o cliente já recebeu a resposta anterior",
    "report_api.py:ide_catalog":
        "rota interna de diagnóstico (/v1/internal/ide/catalog), não é caminho de cliente; fila do próximo passo",
    "report_api.py:ide_probe":
        "rota interna de diagnóstico (/v1/internal/ide/probe), não é caminho de cliente; fila do próximo passo",
    "deploy_app.py:probe_sources":
        "rota de diagnóstico das fontes, sem cliente do produto pendurado nela; fila do próximo passo",

    # --- fila declarada do próximo passo: caminho de cliente, ainda fora ---
    "report_v13_patch.py:_build_v13":
        "renderização do PDF no serviço do relatório (semáforo de 1): é caminho de cliente e ENTRA na fila "
        "do próximo passo. Fica fora aqui porque o prazo tem que ser medido no serviço do relatório, com o "
        "render real de 55 s, e não nesta máquina",
    "report_v20_patch.py:_build_v20":
        "mesma renderização do PDF, um elo acima do _build_v13: entra na fila do próximo passo junto com ele, "
        "medida no serviço do relatório",
    "heavy_live_api_v20.py:agro_raster":
        "rota /v1/heavy/agro-raster do serviço do relatório: a busca do CAR já corre no escopo; MapBiomas e "
        "SRTM ficam para a fila, porque o teto deles precisa de medição no serviço do relatório",
    "mapbiomas_alerta.py:query_mapbiomas_alerta_async":
        "embrulho assíncrono da consulta de alertas; quem chama repassa cancel_event direto ao curl, então o "
        "cancelamento chega por parâmetro. O teto fica para a fila, medido junto com o relatório",
}
# Total de chamadas ESCRITAS COMO asyncio.to_thread (não de funções: uma função pode ter várias). Medido,
# não estimado: se mudar, o portão obriga a decidir de novo.
#
# ⚠️ O QUE ESTE NÚMERO É, E O QUE ELE NÃO É (16/09). É a população de asyncio.to_thread, não a população de
# "fontes fora do escopo". Um mesmo curl chega a um processo gerenciado por quatro caminhos, e só um deles
# tem esta grafia. Enumerar pela propriedade começa por medir a população inteira, senão o total nunca muda,
# ninguém é obrigado a declarar motivo, e o inventário segue dizendo "todas com motivo declarado" sobre uma
# fatia. Os quatro caminhos e quem responde por cada um:
#   asyncio.to_thread                      -> esta lista (copia o contexto; o escopo viaja)
#   ThreadPoolExecutor.submit / .map /
#   loop.run_in_executor                   -> regra copia_de_escopo_no_pool (NÃO copia o contexto: exige
#                                             in_current_scope em cada trabalho, com controle por mutação)
#   threading.Thread                       -> THREAD_PROPRIA_DECLARADA, logo abaixo (16/09: também enumerada,
#                                             também com motivo por ponto e total travado)
#   await direto de função assíncrona      -> corre no laço do handler, dentro do escopo de quem a chamou
OUTRAS_FONTES_EM_TO_THREAD_FORA_DO_ESCOPO = 33
# Thread própria: não copia o contexto (o escopo NÃO viaja) e quem abre uma precisa repassar cancel_event até
# o run_managed_process. Mesma regra do inventário acima: motivo por ponto e total travado.
THREAD_PROPRIA_DECLARADA = {
    "car_resolver_smoke.py:<módulo>":
        "prova de fumaça do arranque, sem cliente e sem requisição: não há desistência a ouvir, e o "
        "desligamento já derruba os filhos pelo _SERVER_STOPPING",
    "incra_snci_public_v42.py:background_probe":
        "sonda de arranque do INCRA (uma chamada, 3 s depois de subir), sem cliente e sem requisição; o "
        "desligamento já derruba o filho pelo _SERVER_STOPPING",
    "portal_feature_smoke.py:<módulo>":
        "prova de fumaça do arranque, sem cliente e sem requisição: mesmo caso do car_resolver_smoke",
    "sitecustomize.py:<módulo>":
        "carregador de módulo do arranque (importa o serviço do relatório depois do report_api), sem cliente "
        "e sem requisição: não há desistência a ouvir",
}
THREADS_PROPRIAS_FORA_DO_ESCOPO = 4


def _dentro_do_escopo(tree: ast.AST) -> set[int]:
    dentro: set[int] = set()
    for node in ast.walk(tree):
        if _e_chamada_de_escopo(node):
            dentro.update(id(sub) for sub in ast.walk(node))
    return dentro


def _dono(tree: ast.AST, node: ast.AST) -> str:
    """A função mais interna que contém o nó (ast.walk devolve de fora para dentro: vale a última)."""
    dono = "<módulo>"
    for fn in _funcoes(tree):
        if any(sub is node for sub in ast.walk(fn)):
            dono = fn.name
    return dono


def _alvo_de_thread(node: ast.Call) -> str:
    """O nome do trabalho passado a threading.Thread(target=...) ou ao pool, sem o embrulho do escopo."""
    alvo = None
    if node.args:
        alvo = node.args[0]
    for kw in node.keywords or []:
        if kw.arg == "target":
            alvo = kw.value
    if alvo is None:
        return ""
    if isinstance(alvo, ast.Call) and getattr(alvo.func, "id", None) == "in_current_scope" and alvo.args:
        alvo = alvo.args[0]
    return alvo.id if isinstance(alvo, ast.Name) else (alvo.attr if isinstance(alvo, ast.Attribute) else "")


@regra("busca_car_no_escopo")
def r_car_no_escopo():
    problems: list[str] = []
    trees = {path.name: ast.parse(path.read_text(encoding="utf-8"), filename=path.name) for path in runtime_modules()}
    reaching = process_reaching(trees)
    no_escopo = 0
    outras = 0
    threads = 0
    vistas_fora: set[str] = set()
    vistas_thread: set[str] = set()
    # threading.Thread NÃO copia o contexto: o escopo nunca viaja para dentro dela. Enumerada aqui pela mesma
    # propriedade das outras (o trabalho chega a um processo gerenciado), para o inventário deixar de medir
    # só a grafia asyncio.to_thread e dizer que mediu "as fontes fora do escopo".
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and (getattr(node.func, "attr", None) == "Thread" or getattr(node.func, "id", None) == "Thread")):
                continue
            if _alvo_de_thread(node) not in reaching:
                continue
            threads += 1
            chave = f"{name}:{_dono(tree, node)}"
            vistas_thread.add(chave)
            if chave not in THREAD_PROPRIA_DECLARADA:
                problems.append(f"{name}:{node.lineno} thread própria com fonte fora de escopo ({chave}) sem motivo "
                                f"declarado: o escopo não atravessa threading.Thread, então ou repassa cancel_event "
                                f"até o run_managed_process, ou entra no inventário")
    if threads != THREADS_PROPRIAS_FORA_DO_ESCOPO:
        problems.append(f"threads próprias com fonte mudaram: {threads} agora, {THREADS_PROPRIAS_FORA_DO_ESCOPO} "
                        f"declaradas (some do inventário ou repassa cancel_event, mas não passa calado)")
    sobrando_thread = sorted(set(THREAD_PROPRIA_DECLARADA) - vistas_thread)
    if sobrando_thread:
        problems.append(f"motivo declarado para thread que não existe mais: {sobrando_thread}")
    problems.extend(sem_motivo_declarado(THREAD_PROPRIA_DECLARADA))
    for name, tree in trees.items():
        dentro = _dentro_do_escopo(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread" and node.args):
                continue
            alvo = node.args[0]
            nome = alvo.id if isinstance(alvo, ast.Name) else (alvo.attr if isinstance(alvo, ast.Attribute) else "")
            if nome.startswith("fetch_car_live"):
                if id(node) in dentro:
                    no_escopo += 1
                    continue
                chave = f"{name}:{_dono(tree, node)}"
                if chave not in CAR_FORA_DO_ESCOPO_DECLARADO:
                    problems.append(f"{name}:{node.lineno} busca do CAR em to_thread fora de escopo "
                                    f"({chave}): prazo e desistência não a alcançam")
            elif nome in reaching and id(node) not in dentro:
                outras += 1
                chave = f"{name}:{_dono(tree, node)}"
                vistas_fora.add(chave)
                if chave not in OUTRAS_FORA_DO_ESCOPO_DECLARADO:
                    problems.append(f"{name}:{node.lineno} fonte em to_thread fora de escopo ({chave}) sem motivo "
                                    f"declarado: entra no escopo ou entra no inventário, mas não passa calado")
    if outras != OUTRAS_FONTES_EM_TO_THREAD_FORA_DO_ESCOPO:
        problems.append(f"fontes fora de escopo mudaram: {outras} agora, {OUTRAS_FONTES_EM_TO_THREAD_FORA_DO_ESCOPO} "
                        f"declaradas (some do inventário ou entra no escopo, mas não passa calado)")
    # Motivo que sobrou é motivo que envelheceu: quem entrou no escopo sai do inventário no mesmo passo,
    # senão a próxima leitura acredita numa dívida que já foi paga.
    sobrando = sorted(set(OUTRAS_FORA_DO_ESCOPO_DECLARADO) - vistas_fora)
    if sobrando:
        problems.append(f"motivo declarado para fonte que não está mais fora do escopo: {sobrando} "
                        f"(tirar do inventário no mesmo passo em que ela entrou)")
    problems.extend(sem_motivo_declarado(CAR_FORA_DO_ESCOPO_DECLARADO))
    problems.extend(sem_motivo_declarado(OUTRAS_FORA_DO_ESCOPO_DECLARADO))
    assert not problems, "busca do CAR: " + " | ".join(problems)
    # A frase diz a POPULAÇÃO que cada número mede. "Outras fontes fora do escopo" sem dizer a grafia dava a
    # entender que o inventário cobria os quatro caminhos até um processo gerenciado, e cobria um.
    return (f"{no_escopo} buscas do CAR dentro do escopo, {len(CAR_FORA_DO_ESCOPO_DECLARADO)} declaradas fora "
            f"com motivo; {outras} chamadas em asyncio.to_thread fora do escopo, todas com motivo declarado "
            f"({len(OUTRAS_FORA_DO_ESCOPO_DECLARADO)} pontos); {threads} threads próprias com fonte, todas com "
            f"motivo declarado; os sítios de pool são cobrados por copia_de_escopo_no_pool")


# --- 4) o nome resolvido EM EXECUÇÃO, não o escrito no import -----------------------------------------
# Este repositório troca implementações no arranque (car_resilient.install_global_patch). Constante de prazo
# justificada pelo comportamento de um dependente exige sonda em execução dizendo para que função o nome
# resolve: foi assim que um prazo de 46 s, escrito para "UM curl de 45 s", acabou cortando uma cadeia de 12
# tentativas. Quem põe prazo sobre a busca do CAR usa o teto declarado pelo módulo que a implementa.
PRAZOS_DO_CAR = (
    ("report_v9_patch", "_CAR_FALLBACK_S"),
    ("portal_property_tabs", "_CAR_S"),
)


@regra("nome_do_car_resolve_em_execucao", linux=True)
def r_nome_resolve():
    import importlib
    import car_resilient
    resolvido = []
    problems: list[str] = []
    for mod_name in ("deploy_app", "report_api", "portal_api"):
        try:
            module = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{mod_name}: não importou ({type(exc).__name__})")
            continue
        fn = getattr(module, "fetch_car_live", None)
        if fn is None:
            continue
        alvo = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}"
        resolvido.append(f"{mod_name}.fetch_car_live -> {alvo}")
        if fn is not car_resilient.fetch_car_live_resilient:
            problems.append(f"{mod_name}.fetch_car_live resolve para {alvo}, não para a busca resiliente: o "
                            f"teto usado pelos prazos deixou de valer")
    for mod_name, const in PRAZOS_DO_CAR:
        module = importlib.import_module(mod_name)
        valor = getattr(module, const)
        resolvido.append(f"{mod_name}.{const}={valor}")
        if valor < car_resilient.WORST_CASE_SECONDS:
            problems.append(f"{mod_name}.{const}={valor} é menor que o pior caso da função que o nome resolve "
                            f"({car_resilient.WORST_CASE_SECONDS} s): corta resposta que hoje chega")
    # o pior caso não é chute: soma o prazo de rede, a carência de parada do processo e o trabalho em Python
    minimo = car_resilient.MAX_ATTEMPTS * (car_resilient.ATTEMPT_HARD_TIMEOUT_S + epl().STOP_OVERHEAD_SECONDS)
    if car_resilient.WORST_CASE_SECONDS < minimo:
        problems.append(f"WORST_CASE_SECONDS={car_resilient.WORST_CASE_SECONDS} não cobre nem o tempo de rede "
                        f"mais a carência de parada ({round(minimo, 1)} s)")
    print("RX_M1_SONDA_NOME=" + " | ".join(resolvido), flush=True)
    assert not problems, "nome em execução: " + " | ".join(problems)
    return " | ".join(resolvido)


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


# --- desistência é 499 em TODA rota convertida, inclusive nas quatro do relatório móvel --------------
# O declarado era "desistência -> 499", e em quatro rotas não era: a RequestDisconnected que o _report_name
# levanta subia até o ASGI em traceback (caminho de 500) no gesto mais banal do usuário, fechar a tela do
# relatório. Não existe exception_handler global neste projeto, então o log de produção enchia de traceback
# no lugar exato onde falha de verdade precisa aparecer.
@regra("desistencia_vira_499_no_pdf")
def r_pdf_499():
    from fastapi import HTTPException
    import portal_pdf_v21 as pdf
    mod = epl()
    car = "MG-3120904-AAAA1111BBBB2222CCCC3333DDDD4444"

    class ClienteFora:
        async def is_disconnected(self):
            return True

    def identidade_lenta(code, cancel_event=None):
        time.sleep(0.2)          # sem rede: só dá tempo de a vigia ver a desistência
        return {"ok": False}

    real = pdf.resolve_property_identity_sync
    pdf.resolve_property_identity_sync = identidade_lenta
    try:
        # controle negativo do instrumento: a cadeia do nome DEVE levantar RequestDisconnected, senão o 499
        # das rotas abaixo não prova nada (poderiam estar devolvendo 499 por outro motivo, ou nenhum)
        async def crua():
            try:
                await pdf._report_name(car, None, ClienteFora())
                return None
            except mod.RequestDisconnected as exc:
                return exc

        if asyncio.run(crua()) is None:
            fail("instrumento quebrado: a cadeia do nome não levantou RequestDisconnected, então o cenário "
                 "não exercita a desistência")
        ruins: list[str] = []
        for nome in ("mobile_report_prepare", "mobile_report_status", "mobile_report_open", "mobile_report_view"):
            try:
                asyncio.run(getattr(pdf, nome)(car, ClienteFora()))
                ruins.append(f"{nome}: seguiu adiante depois de o cliente sair")
            except HTTPException as exc:
                if exc.status_code != 499:
                    ruins.append(f"{nome}: {exc.status_code}, esperado 499")
            except BaseException as exc:                                    # noqa: BLE001
                ruins.append(f"{nome}: {type(exc).__name__} subiu até o ASGI (caminho de 500, traceback no log)")
    finally:
        pdf.resolve_property_identity_sync = real
    if ruins:
        fail("desistência no relatório móvel: " + " | ".join(ruins))
    return "as quatro rotas móveis do relatório devolvem 499 na desistência, nenhuma deixa a exceção subir"


# --- tentativa cancelada não é resposta, e não vai para cache compartilhado ---------------------------
# A desistência de UM cliente virava resposta errada para TODOS os outros. Provado em 16/09 na branch: o
# cliente A pediu /v1/live/incra-certified/status/BA e desistiu em 0,5 s; o escopo matou o curl; a função
# gravou a tentativa cancelada no _CAP (TTL de meia hora) e o cliente B, com a requisição inteira, recebeu
# ok:false, public_access:false, ManagedProcessCancelled — sobre uma fonte que ninguém perguntou. No mapa era
# pior: /v1/live/property-names/viewport gravava um "não há nome nenhum nesta área" montado SEM o SIGEF, com
# ok:true e count:0, por 15 minutos, no gesto mais comum do produto (arrastar o mapa). Regra 1, regra 2 e a
# regra 3 do dono ("zero não é ausência") invertida.
# Antes do escopo isto era impossível: sem escopo a desistência nunca chegava ao curl. Quem põe um trecho
# DENTRO do escopo passa a responder por cada cache e cada trava que ele escreve.
@regra("cancelamento_nao_vira_cache")
def r_cancelamento_nao_vira_cache():
    mod = epl()
    import incra_snci_public_v42 as snci
    import property_names_viewport_v30 as nomes
    notas: list[str] = []

    @contextlib.contextmanager
    def escopo_cancelado():
        """Escopo aberto e JÁ cancelado: o run_managed_process recusa antes de nascer processo nenhum.

        Nenhuma rede e nenhum filho — é o mesmo caminho do cliente que desistiu, um passo antes do spawn."""
        evento = threading.Event()
        evento.set()
        token = mod._open_scope(evento)
        try:
            yield
        finally:
            mod._SCOPES.reset(token)

    # (a) INCRA · capabilities dentro de um escopo cancelado não grava no _CAP
    snci._CAP.clear()
    with escopo_cancelado():
        out = snci.capabilities("BA")
    if out.get("cancelled") is not True:
        fail(f"capabilities cancelada não se declara cancelada: {out}")
    if "public_access" in out:
        fail("capabilities cancelada ainda afirma public_access sobre uma fonte que não foi perguntada")
    if snci._CAP:
        fail(f"tentativa cancelada gravada no _CAP: {sorted(snci._CAP)} (meia hora de resposta errada para "
             f"todo cliente da mesma UF)")
    # controle negativo do instrumento: falha de verdade CONTINUA sendo cacheada, então o _CAP vazio acima
    # é a ausência da gravação, e não um instrumento que não enxerga gravação nenhuma
    real = snci._curl
    snci._curl = lambda url, timeout=12: {"ok": False, "detail": "falha_de_verdade"}
    try:
        snci.capabilities("BA")
    finally:
        snci._curl = real
    if not snci._CAP:
        fail("instrumento quebrado: nem a falha comum aparece no _CAP, então o _CAP vazio não prova nada")
    snci._CAP.clear()
    notas.append("INCRA: cancelada fora do _CAP, falha comum dentro")

    # (b) mapa · três travas independentes, uma por vez. Nenhuma rede: o SIGEF e o OSM são substituídos, e o
    # bbox é o de Curvelo, do CAR de prova.
    bbox = (-44.46, -18.79, -44.40, -18.73)
    semente = [{"name": "Fazenda de prova", "car_code": "MG-3120904-AAAA", "lat": -18.75, "lon": -44.43, "osm_id": 1}]
    osm_chamado: list[int] = []

    @contextlib.contextmanager
    def mapa_sem_rede(resposta_do_sigef):
        real_curl, real_osm, real_seed = nomes._curl, nomes._osm_named_farms_bbox, nomes.seed.in_bbox
        nomes._curl = lambda url, expect_json=True, **kw: dict(resposta_do_sigef)
        def osm(*a, **k):
            osm_chamado.append(1)
            return {"ok": False, "items": [], "count": 0, "detail": "sem_osm"}
        nomes._osm_named_farms_bbox = osm
        nomes.seed.in_bbox = lambda *a, **k: list(semente)
        nomes._CACHE.clear()
        try:
            yield
        finally:
            nomes._curl, nomes._osm_named_farms_bbox, nomes.seed.in_bbox = real_curl, real_osm, real_seed
            nomes._CACHE.clear()

    # b1 · curl recusado pelo escopo: sai cancelado, e não grava
    with mapa_sem_rede({"ok": False, "cancelled": True, "detail": "request_cancelled", "bytes": 0}):
        with escopo_cancelado():
            out = nomes._query_names_sync(*bbox, 60)
        if out.get("ok") is not False or out.get("cancelled") is not True:
            fail(f"bbox cancelado não se declara cancelado: "
                 f"{({k: out.get(k) for k in ('ok', 'cancelled', 'detail')})}")
        if nomes._CACHE:
            fail("thread abandonada gravou o bbox no _CACHE: o cliente seguinte recebe 'não há nome nenhum "
                 "aqui' por 15 minutos sobre uma área em que o SIGEF não foi consultado")

    # b2 · SIGEF falhou de verdade: entrega o que veio, declara a pendência, e não grava
    with mapa_sem_rede({"ok": False, "detail": "falha_de_verdade"}):
        out = nomes._query_names_sync(*bbox, 60)
        if out.get("ok") is not False:
            fail("SIGEF sem responder e a resposta sai com ok:true: o que veio de outra fonte passa por "
                 "'consultei a área inteira' (zero não é ausência)")
        if "SIGEF/INCRA — espelho público" not in (out.get("pending_sources") or []):
            fail(f"fonte que não respondeu não aparece em pending_sources: {out.get('pending_sources')}")
        if not out.get("items"):
            fail("o que a outra fonte trouxe sumiu da resposta: pendência declarada não é resposta apagada")
        if nomes._CACHE:
            fail("resposta com fonte pendente gravada no _CACHE: pendência congelada por 15 minutos vira ausência")

    # b3 · a desistência chegou DEPOIS do curl: a resposta está completa, mas a thread já foi abandonada
    with mapa_sem_rede({"ok": True, "json": {"features": []}, "bytes": 2}):
        out = nomes._query_names_sync(*bbox, 60)
        if out.get("ok") is not True or nomes._CACHE == {}:
            fail(f"instrumento quebrado: sem cancelamento esta resposta tinha de sair ok e ir para o _CACHE "
                 f"({({k: out.get(k) for k in ('ok', 'detail')})}, cache={len(nomes._CACHE)})")
        nomes._CACHE.clear()
        with escopo_cancelado():
            out = nomes._query_names_sync(*bbox, 60)
        if nomes._CACHE:
            fail("cancelamento chegado depois do curl ainda grava no _CACHE: a thread abandonada segue viva "
                 "durante a leitura das geometrias, e o que ela escrever é o que o próximo cliente lê")
        if out.get("cancelled") is not True:
            fail(f"resposta montada por thread abandonada não se declara cancelada: {out.get('cancelled')}")
    if osm_chamado:
        fail("o OSM ao vivo foi chamado num caminho em que o SIGEF já tinha resolvido: uma rede a mais por arrasto")
    notas.append("mapa: cancelado antes e depois do curl fora do _CACHE, pendência declarada, nunca ok:true sem SIGEF")
    return "; ".join(notas)


# --- teto derivado de UMA chamada numa função que faz N ------------------------------------------------
# O teto da rota de integridade do CAR era 2 x QUERY_TIMEOUT_S: multiplicou pela disputa da trava e esqueceu
# a CONTAGEM. A leitura dispara CINCO consultas BigQuery em sequência, cada uma com o seu próprio
# result(timeout=QUERY_TIMEOUT_S): o teto saia em 105 s sobre um pior caso de 450 s, e um CAR grande com
# BigQuery frio que hoje responde passaria a devolver 504 — resposta que existia, sumiu. Quem escreve o teto
# não conta as chamadas à mão: esta regra conta pela árvore, com peso por sítio de chamada (uma função
# chamada duas vezes custa duas consultas) e reprova se o número do módulo divergir.
@regra("integridade_conta_consultas")
def r_integridade_conta_consultas():
    tree = ast.parse(src("sicar_integrity_v47.py").read_text(encoding="utf-8"), filename="sicar_integrity_v47.py")
    funcs = {fn.name: fn for fn in _funcoes(tree)}
    raiz = "query_car_integrity_v47"
    if raiz not in funcs or "_query" not in funcs:
        fail(f"instrumento quebrado: sicar_integrity_v47 sem {raiz} ou sem _query")

    def custo(nome: str, pilha: tuple[str, ...]) -> int:
        total = 0
        for node in _nos_proprios(funcs[nome]):
            if not isinstance(node, ast.Call):
                continue
            alvo = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if alvo == "_query":
                total += 1
            elif alvo in funcs and alvo not in pilha:   # recursão não entra em laço; um sítio custa o que custa
                total += custo(alvo, pilha + (nome,))
        return total

    contadas = custo(raiz, (raiz,))
    if contadas == 0:
        fail("instrumento quebrado: nenhuma chamada de _query alcançada a partir de " + raiz)
    import portal_car_integrity_v47 as portal_integ
    declaradas = int(portal_integ._QUERIES_PER_INTEGRITY)
    if contadas != declaradas:
        fail(f"consultas por leitura de integridade: {contadas} no código, {declaradas} declaradas em "
             f"portal_car_integrity_v47._QUERIES_PER_INTEGRITY (o teto da rota é derivado deste número: "
             f"com ele errado a rota devolve 504 numa consulta que hoje responde)")
    teto = float(portal_integ._INTEGRITY_S)
    piso = declaradas * float(sicar_int().QUERY_TIMEOUT_S)
    if teto < piso:
        fail(f"teto da rota de integridade ({teto} s) menor que o pior caso de UMA passagem pelas {declaradas} "
             f"consultas ({piso} s): corta resposta que hoje chega")
    return (f"{contadas} consultas BigQuery por leitura de integridade, contadas pela árvore com peso por sítio; "
            f"teto da rota {teto} s >= {piso} s")


def sicar_int():
    import sicar_integrity_v47
    return sicar_integrity_v47


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
     "        event.set()\n        raise\n    finally:", "        raise\n    finally:",
     "ainda vivo"),
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
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6,request=request)",
     "car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=6)", "curl do prazo: filho"),
    ("cadeia_car_prazo", "portal_property_tabs.py",
     "try:result=await wait_for_cancelling_processes(report_base.analyze_car(code),18,request=request)",
     "try:result=await asyncio.wait_for(report_base.analyze_car(code),timeout=18)", "curl do prazo: filho"),
    ("cadeia_car_prazo", "report_quick_v22.py",
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6,request=request)",
     "job=asyncio.ensure_future(asyncio.to_thread(fetch_car_live_resilient,code));car=await wait_for_cancelling_processes(job,6,request=request)",
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
     "car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),6,request=request)",
     "car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=6)", "wait_for puro"),
    ("inventario_processos", "redis_runtime_bootstrap.py", "check=True,timeout=120,stdout=subprocess.DEVNULL",
     "check=True,stdout=subprocess.DEVNULL", "sem timeout"),
    ("inventario_processos", "sicar_detail_sources_v2.py", "runner=run_managed_process)", "runner=br_bridge.subprocess_runner)",
     "lista de chamadas diretas mudou"),
    # ---- prazo estourado + cliente desistiu
    ("desistencia_apos_prazo", "report_v9_patch.py",
     "car=await wait_for_cancelling_processes(asyncio.to_thread(base.fetch_car_live,code),_CAR_FALLBACK_S,request=request)",
     "car=await asyncio.to_thread(base.fetch_car_live,code)", "caminho de reserva"),
    ("desistencia_apos_prazo", "portal_property_tabs.py",
     "        car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code.upper()),\n"
     "                                                _CAR_S if budget is None else budget,request=request)\n",
     "        car=await asyncio.to_thread(fetch_car_live_resilient,code.upper())\n", "caminho de reserva"),
    ("desistencia_apos_prazo", "external_process_lifecycle.py",
     "    watcher = None if request is None else asyncio.ensure_future(_watch_disconnect(request, task, event, seen))",
     "    watcher = None", "caminho de reserva"),
    ("desistencia_apos_prazo", "external_process_lifecycle.py",
     "                seen.append(True)\n                event.set()", "                event.set()",
     "resposta inesperada depois da desist"),
    ("escopo_no_pool", "ide_layer_probe.py",
     "ex.submit(in_current_scope(query_layer),layer,bbox,car_geometry)",
     "ex.submit(query_layer,layer,bbox,car_geometry)", "curl do trabalhador do pool"),
    # ---- as regras de forma: a família inteira, não o exemplar consertado
    ("copia_de_escopo_no_pool", "sicar_detail_sources_v2.py",
     "ex.submit(in_current_scope(_query_layer),t,c,car,bbox,car_code)",
     "ex.submit(_query_layer,t,c,car,bbox,car_code)", "sem in_current_scope"),
    ("copia_de_escopo_no_pool", "water_mg.py",
     "ex.submit(in_current_scope(_query_layer),layer,car,car_m,tr,qb,radius_km)",
     "ex.submit(_query_layer,layer,car,car_m,tr,qb,radius_km)", "sem in_current_scope"),
    ("copia_de_escopo_no_pool", "soilgrids_wcs.py",
     "ex.submit(in_current_scope(_one),p,lon,lat)", "ex.submit(_one,p,lon,lat)", "sem in_current_scope"),
    ("vigia_do_cliente_em_todo_handler", "report_quick_v22.py",
     "asyncio.to_thread(fetch_car_live_resilient,code),6,request=request)",
     "asyncio.to_thread(fetch_car_live_resilient,code),6)", "sem request="),
    ("vigia_do_cliente_em_todo_handler", "portal_property_tabs.py",
     "async def climate_detail(car_code:str,days:int=30,request:Request=None):",
     "async def climate_detail(car_code:str,days:int=30):", "não declara request"),
    ("busca_car_no_escopo", "property_search.py",
     "        try:\n            car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,upper),\n"
     "                                                    car_resilient.WORST_CASE_SECONDS,request=request)\n"
     "        except RequestDisconnected:\n            raise _desistiu()\n",
     "        car=await asyncio.to_thread(fetch_car_live_resilient,upper)\n", "fora de escopo"),
    ("nome_do_car_resolve_em_execucao", "report_v9_patch.py",
     "_CAR_FALLBACK_S=car_resilient.WORST_CASE_SECONDS", "_CAR_FALLBACK_S=46", "corta resposta que hoje chega"),
    ("desistencia_sem_reserva", "portal_mining_resilience_v34.py",
     "asyncio.to_thread(fetch_car_live_resilient,code),9,request=request)",
     "asyncio.to_thread(fetch_car_live_resilient,code),9)", "curl depois da desistência"),
    ("cenario_importa_sozinho", "portal_mining_resilience_v34.py",
     "import portal_property_tabs  # noqa: F401 - dono das âncoras kpi-servico-geologico e kpi-terras-raras\n",
     "", "não importa sozinho"),
    ("ci_roda_o_gate", ".github/workflows/quality-gate.yml", "python scripts/m1_processos_gate.py --exigir-linux",
     "python scripts/m1_processos_gate.py", "não exige Linux"),
    # ---- desistência no relatório móvel: sem o embrulho, a exceção volta a subir até o ASGI
    ("desistencia_vira_499_no_pdf", "portal_pdf_v21.py",
     "        raise HTTPException(status_code=499,detail='client_disconnected')\n",
     "        raise\n", "subiu até o ASGI"),
    # ---- tentativa cancelada nao e resposta: cada trava, uma mutacao
    ("cancelamento_nao_vira_cache", "incra_snci_public_v42.py",
     "    if raw.get('cancelled'):\n"
     "        out.pop('public_access',None);out['cancelled']=True;out['detail']='request_cancelled';return out\n",
     "", "capabilities cancelada não se declara cancelada"),
    ("cancelamento_nao_vira_cache", "incra_snci_public_v42.py",
     "        out.pop('public_access',None);out['cancelled']=True;out['detail']='request_cancelled';return out\n",
     "        out.pop('public_access',None);out['cancelled']=True;out['detail']='request_cancelled';_CAP[key]=(now,out);return out\n", "gravada no _CAP"),
    ("cancelamento_nao_vira_cache", "property_names_viewport_v30.py",
     "    if raw.get('cancelled'):\n"
     "        return {'ok':False,'items':[],'count':0,'cancelled':True,'detail':'request_cancelled',\n"
     "                'source':'SIGEF + OpenStreetMap','coverage':{'elapsed_ms':round((time.monotonic()-started)*1000,1)}}\n",
     "", "bbox cancelado não se declara cancelado"),
    ("cancelamento_nao_vira_cache", "property_names_viewport_v30.py",
     "    if pending:\n"
     "        return {'ok':False,'items':items,'count':len(items),'pending_sources':pending,\n"
     "                'detail':'consulta_pendente','source':'SIGEF/INCRA + OpenStreetMap — referências públicas',\n"
     "                'cached':False,'coverage':coverage}\n",
     "", "sai com ok:true"),
    ("cancelamento_nao_vira_cache", "property_names_viewport_v30.py",
     "    if epl.scope_cancelled():\n"
     "        out['cancelled']=True;return out\n",
     "", "ainda grava no _CACHE"),
    # ---- o teto derivado tem de contar as chamadas, não só o prazo de uma
    ("integridade_conta_consultas", "sicar_integrity_v47.py",
     "        uf_boundary = _fetch_boundary(own_client, uf_table, \"sigla_uf\", uf, uf)\n",
     "        uf_boundary = _fetch_boundary(own_client, uf_table, \"sigla_uf\", uf, uf)\n"
     "        _fetch_boundary(own_client, uf_table, \"sigla_uf\", uf, uf)\n",
     "consultas por leitura de integridade"),
    ("integridade_conta_consultas", "portal_car_integrity_v47.py",
     "_QUERIES_PER_INTEGRITY = 5", "_QUERIES_PER_INTEGRITY = 2", "consultas por leitura de integridade"),
    # ---- thread própria: o escopo não a atravessa, então ela também entra no inventário
    ("busca_car_no_escopo", "incra_snci_public_v42.py",
     "    threading.Thread(target=run,daemon=True).start()",
     "    threading.Thread(target=run,daemon=True).start()\n    threading.Thread(target=run,daemon=True).start()",
     "threads próprias com fonte mudaram"),
    # ---- E1: o inventário das OUTRAS fontes fora do escopo (motivo por ponto, não só o total)
    # fonte do cliente que sai do escopo tem de reprovar POR NÃO TER MOTIVO, não só por mudar o total
    ("busca_car_no_escopo", "portal_conformity_sinaflor_v48.py",
     "        return await wait_for_cancelling_processes(\n"
     "            asyncio.to_thread(query_sinaflor_authorization, code), _SINAFLOR_S, request=request)",
     "        return await asyncio.to_thread(query_sinaflor_authorization, code)",
     "sem motivo declarado"),
    # motivo que sobrou também reprova: fonte declarada fora que entrou no escopo sai do inventário
    ("busca_car_no_escopo", "report_api.py",
     "    terms=[x.strip() for x in q.split(',') if x.strip()]; return await asyncio.to_thread(search_catalog,terms,100)",
     "    terms=[x.strip() for x in q.split(',') if x.strip()]\n"
     "    from external_process_lifecycle import wait_for_cancelling_processes\n"
     "    return await wait_for_cancelling_processes(asyncio.to_thread(search_catalog,terms,100),10)",
     "não está mais fora do escopo"),
    # a contagem de escopos por arquivo é da ÁRVORE: escopo dentro de gather (sem `await` colado) conta
    ("inventario_processos", "portal_property_tabs.py",
     "        wait_for_cancelling_processes(asyncio.to_thread(query_climatology_nasa,geom),_CLIMA_NORMAL_S,request=request),\n",
     "        asyncio.to_thread(query_climatology_nasa,geom),\n",
     "portal_property_tabs.py: 3 chamada(s) de wait_for_cancelling_processes, esperado 4"),
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

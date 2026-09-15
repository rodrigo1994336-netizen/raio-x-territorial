"""M1 · quantos processos, threads, descritores e quanta memória o serviço segura agora.

Vale nos dois serviços (portal e relatório: os dois servem o mesmo app de ``report_api``):

* uma linha ``RX_PROC_STATS`` no log no arranque e a cada ``RX_PROC_STATS_MIN`` minutos (padrão 10, piso
  de 1; 0 desliga a repetição e mantém a do arranque; valor inválido volta ao padrão e avisa com
  ``RX_PROC_STATS_CONFIG``). É o caminho do dono: não precisa de senha nem de rota;
* a rota somente leitura ``GET /v1/diag/processos``, **desligada por padrão**. Só responde com a variável
  ``RX_DIAG_TOKEN`` (>= 32 caracteres) no serviço e o mesmo valor no cabeçalho ``X-RX-Diag-Token``; sem
  isso devolve o mesmo 404 de um endereço que não existe. Memória, idade do processo e quantos curl estão
  vivos dizem o volume de consultas em tempo real: não é dado para qualquer um. A medição é reaproveitada
  por ``CACHE_S`` segundos.

Lê ``/proc`` (Linux). Onde não dá para medir, o valor é ``"indisponivel"`` — nunca zero inventado: a
tabela de processos só vale se o próprio processo aparecer nela; idade negativa, descendente vivo sem
leitura de memória e entrada do /proc ilegível viram "indisponivel" no número que afetariam. ``None``
(``sem_filhos`` no log) é "não se aplica". Não sai linha de comando, variável de ambiente, caminho nem PID:
só contagens, idades, memória e o nome curto do executável dos descendentes.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import hmac
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

INDISPONIVEL = "indisponivel"
ROUTE = "/v1/diag/processos"
TOKEN_ENV = "RX_DIAG_TOKEN"
TOKEN_HEADER = "x-rx-diag-token"
TOKEN_MIN_LEN = 32
PROC_ROOT = "/proc"
CGROUP_ROOT = "/sys/fs/cgroup"
DEFAULT_INTERVAL_MIN = 10.0
MIN_INTERVAL_MIN = 1.0
CACHE_S = 5.0
# /proc/uptime e o starttime do stat são truncados em centésimos: um processo recém-nascido pode dar até
# -0,02 s. Abaixo disso a conta não fecha (relógio virtualizado, namespace de tempo) e a idade não é medida.
AGE_TOLERANCE_S = 0.05
_COMM_OK = re.compile(r"^[A-Za-z0-9._+-]{1,15}$")
_BRT = timezone(timedelta(hours=-3))


def service_name() -> str:
    return "portal" if os.getenv("RX_RELEASE") == "V8_OPERATIONAL_ZERO_COST" else "relatorio"


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _status_kb(text: str | None, key: str) -> int | None:
    if not text:
        return None
    m = re.search(rf"^{key}:\s+(\d+)\s*kB", text, re.M)
    return int(m.group(1)) if m else None


def _status_int(text: str | None, key: str) -> int | None:
    if not text:
        return None
    m = re.search(rf"^{key}:\s+(\d+)\s*$", text, re.M)
    return int(m.group(1)) if m else None


def _status_state(text: str) -> str | None:
    m = re.search(r"^State:\s+([A-Za-z])", text, re.M)
    return m.group(1) if m else None


def _parse_stat(text: str) -> dict[str, Any] | None:
    # "pid (comm) S ppid ..." — o nome pode ter espaço e parêntese: corta no último ")".
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < left:
        return None
    rest = text[right + 2:].split()
    if len(rest) < 20:
        return None
    try:
        return {"comm": text[left + 1:right], "state": rest[0], "ppid": int(rest[1]), "start_ticks": int(rest[19])}
    except ValueError:
        return None


def _process_table(proc_root: str) -> tuple[dict[int, dict[str, Any]], int] | None:
    """(tabela, entradas perdidas). Perdida = entrada numérica que ainda existe mas não deu para ler ou
    interpretar (hidepid, permissão, formato). Processo que terminou entre a listagem e a leitura não conta."""
    try:
        names = os.listdir(proc_root)
    except OSError:
        return None
    table: dict[int, dict[str, Any]] = {}
    lost = 0
    for name in names:
        if not name.isdigit():
            continue
        text = _read(f"{proc_root}/{name}/stat")
        parsed = _parse_stat(text) if text is not None else None
        if parsed is not None:
            table[int(name)] = parsed
        elif os.path.isdir(f"{proc_root}/{name}"):
            lost += 1
    return table, lost


def _clk_tck() -> int | None:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return None


def _age(uptime: float, start_ticks: int, ticks: int) -> float | None:
    age = uptime - start_ticks / ticks
    if age < -AGE_TOLERANCE_S:
        return None
    return round(max(0.0, age), 1)


def _mb(kb: int | None) -> Any:
    return INDISPONIVEL if kb is None else round(kb / 1024, 1)


def _cgroup_memory(cgroup_root: str) -> tuple[Any, Any]:
    """(uso, limite) da memória do contêiner em MB — a conta que o limite do plano cobra (inclui cache de arquivos)."""
    for current, limit in (("memory.current", "memory.max"), ("memory/memory.usage_in_bytes", "memory/memory.limit_in_bytes")):
        used = _read(f"{cgroup_root}/{current}")
        if used is None or not used.strip().isdigit():
            continue
        raw = (_read(f"{cgroup_root}/{limit}") or "").strip()
        if raw == "max" or (raw.isdigit() and int(raw) >= 1 << 60):
            cap: Any = "sem_limite"
        elif raw.isdigit():
            cap = round(int(raw) / 1048576, 1)
        else:
            cap = INDISPONIVEL
        return round(int(used.strip()) / 1048576, 1), cap
    return INDISPONIVEL, INDISPONIVEL


def _asyncio_pending(ignore: tuple[Any, ...] = ()) -> Any:
    """Tarefas do laço fora a da própria medição e as de ``ignore`` (a repetição do log). As do servidor
    (uvicorn: servidor, ciclo de vida, conexões abertas) contam: em repouso o número não é zero."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return INDISPONIVEL  # fora do laço não dá para contar sem risco de corrida
    skip = {asyncio.current_task(loop), *ignore}
    return sum(1 for task in asyncio.all_tasks(loop) if task not in skip)


def _managed_children() -> Any:
    try:
        import external_process_lifecycle
        return len(external_process_lifecycle.active_child_pids())
    except Exception:  # noqa: BLE001 - medição nunca derruba o serviço
        return INDISPONIVEL


def snapshot(proc_root: str = PROC_ROOT, cgroup_root: str = CGROUP_ROOT, ignore_tasks: tuple[Any, ...] = ()) -> dict[str, Any]:
    t0 = time.perf_counter()
    pid = os.getpid()
    out: dict[str, Any] = {
        "servico": service_name(),
        "medido_em": datetime.now(_BRT).isoformat(timespec="seconds"),
        "leitura": INDISPONIVEL,
        "processo_idade_s": INDISPONIVEL,
        "e_pid_1": INDISPONIVEL,
        "filhos_vivos": INDISPONIVEL,
        "zumbis": INDISPONIVEL,
        "descendentes_vivos": INDISPONIVEL,
        "filhos_gerenciados": INDISPONIVEL,
        "filho_mais_velho_s": INDISPONIVEL,
        "filhos_por_nome": INDISPONIVEL,
        "processos_no_conteiner": INDISPONIVEL,
        "zumbis_no_conteiner": INDISPONIVEL,
        "threads_so": INDISPONIVEL,
        "threads_python": threading.active_count(),
        "descritores_abertos": INDISPONIVEL,
        "rss_mb": INDISPONIVEL,
        "pico_rss_mb": INDISPONIVEL,
        "rss_descendentes_mb": INDISPONIVEL,
        "memoria_conteiner_mb": INDISPONIVEL,
        "limite_conteiner_mb": INDISPONIVEL,
        "tarefas_asyncio_pendentes": _asyncio_pending(ignore_tasks),
    }
    read = _process_table(proc_root)
    # O registro é lido DEPOIS da tabela do sistema: filho que nasce no meio da medição aparece como
    # gerenciado a mais, nunca como "vivo fora do registro". Sobra a janela curta entre o nascimento e o
    # registro (microssegundos): filhos_vivos > filhos_gerenciados é só "possível curl direto".
    out["filhos_gerenciados"] = _managed_children()
    table, lost = read if read is not None else ({}, 0)
    if read is not None and pid in table:  # sem o próprio processo, a tabela não é deste serviço
        out["leitura"] = "proc_linux"
        out["e_pid_1"] = pid == 1
        if not lost:  # com entrada ilegível o total do contêiner não é o total
            out["processos_no_conteiner"] = len(table)
            out["zumbis_no_conteiner"] = sum(1 for p in table.values() if p["state"] == "Z")
        # Os filhos deste serviço têm o mesmo dono que ele: hidepid e permissão não os escondem. A entrada
        # perdida é de outro usuário e não muda as contagens abaixo.
        kids: dict[int, list[int]] = collections.defaultdict(list)
        for other, info in table.items():
            kids[info["ppid"]].append(other)
        direct = kids.get(pid, [])
        out["filhos_vivos"] = sum(1 for k in direct if table[k]["state"] != "Z")
        out["zumbis"] = sum(1 for k in direct if table[k]["state"] == "Z")
        below: list[int] = []
        stack = list(direct)
        while stack:
            k = stack.pop()
            if k in below or k == pid:
                continue
            below.append(k)
            stack.extend(kids.get(k, []))
        out["descendentes_vivos"] = sum(1 for k in below if table[k]["state"] != "Z")
        names: collections.Counter[str] = collections.Counter()
        for k in below:
            comm = table[k]["comm"]
            names[comm if _COMM_OK.match(comm) else "outro"] += 1
        out["filhos_por_nome"] = dict(sorted(names.items()))
        rss_kids = 0
        rss_unknown = 0
        living = [k for k in below if table[k]["state"] != "Z"]  # zumbi não tem memória própria
        for k in living:
            text = _read(f"{proc_root}/{k}/status")
            if text is None:
                if os.path.isdir(f"{proc_root}/{k}"):
                    rss_unknown += 1  # vivo e sem leitura: somar 0 seria inventar
                continue  # terminou no meio da medição
            kb = _status_kb(text, "VmRSS")
            if kb is None:
                if _status_state(text) not in ("Z", "X"):
                    rss_unknown += 1
                continue  # saindo: já sem memória de usuário
            rss_kids += kb
        if rss_unknown:
            out["rss_descendentes_mb"] = INDISPONIVEL
        elif living:
            out["rss_descendentes_mb"] = _mb(rss_kids)
        else:
            out["rss_descendentes_mb"] = None
        uptime_text = _read(f"{proc_root}/uptime")
        ticks = _clk_tck()
        try:
            uptime: float | None = float((uptime_text or "").split()[0])
        except (ValueError, IndexError):
            uptime = None
        if uptime is not None and not math.isfinite(uptime):
            uptime = None
        if ticks and uptime is not None:
            own = _age(uptime, table[pid]["start_ticks"], ticks)
            out["processo_idade_s"] = INDISPONIVEL if own is None else own
            ages = [_age(uptime, table[k]["start_ticks"], ticks) for k in below]
            if not ages:
                out["filho_mais_velho_s"] = None
            elif any(a is None for a in ages):
                out["filho_mais_velho_s"] = INDISPONIVEL
            else:
                out["filho_mais_velho_s"] = max(ages)
        elif not below:
            out["filho_mais_velho_s"] = None
    status = _read(f"{proc_root}/{pid}/status") if out["leitura"] == "proc_linux" else None
    if status:
        threads = _status_int(status, "Threads")
        out["threads_so"] = INDISPONIVEL if threads is None else threads
        out["rss_mb"] = _mb(_status_kb(status, "VmRSS"))
        out["pico_rss_mb"] = _mb(_status_kb(status, "VmHWM"))
        try:
            # o próprio listdir abre um descritor para ler a pasta, que aparece na lista
            out["descritores_abertos"] = len(os.listdir(f"{proc_root}/{pid}/fd")) - 1
        except OSError:
            pass
        out["memoria_conteiner_mb"], out["limite_conteiner_mb"] = _cgroup_memory(cgroup_root)
    out["medicao_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def log_line(snap: dict[str, Any], moment: str) -> str:
    def fmt(value: Any) -> str:
        if value is None:
            return "sem_filhos"
        if isinstance(value, dict):
            return ",".join(f"{k}:{v}" for k, v in value.items()) or "nenhum"
        if isinstance(value, bool):
            return "sim" if value else "nao"
        return str(value)

    keys = ("filhos_vivos", "zumbis", "descendentes_vivos", "filhos_gerenciados", "filho_mais_velho_s", "filhos_por_nome",
            "processos_no_conteiner", "zumbis_no_conteiner", "threads_so", "threads_python", "descritores_abertos", "rss_mb",
            "pico_rss_mb", "rss_descendentes_mb", "memoria_conteiner_mb", "limite_conteiner_mb", "tarefas_asyncio_pendentes",
            "processo_idade_s", "e_pid_1", "leitura")
    return f"RX_PROC_STATS servico={snap['servico']} momento={moment} " + " ".join(f"{k}={fmt(snap.get(k))}" for k in keys)


def interval_config() -> tuple[float, str | None]:
    """(segundos, aviso). 0 desliga a repetição; abaixo do piso sobe para o piso; inválido volta ao padrão."""
    raw = os.getenv("RX_PROC_STATS_MIN")
    if raw is None or not raw.strip():
        return DEFAULT_INTERVAL_MIN * 60.0, None
    try:
        minutes = float(raw)
    except ValueError:
        minutes = float("nan")
    if not math.isfinite(minutes) or minutes < 0:
        return DEFAULT_INTERVAL_MIN * 60.0, f"RX_PROC_STATS_CONFIG RX_PROC_STATS_MIN=invalido usando_min={DEFAULT_INTERVAL_MIN:g}"
    if minutes == 0:
        return 0.0, None
    if minutes < MIN_INTERVAL_MIN:
        return MIN_INTERVAL_MIN * 60.0, f"RX_PROC_STATS_CONFIG RX_PROC_STATS_MIN=abaixo_do_piso usando_min={MIN_INTERVAL_MIN:g}"
    return minutes * 60.0, None


def interval_seconds() -> float:
    return interval_config()[0]


def route_token() -> str | None:
    """O segredo da rota, ou None (rota desligada) quando falta ou é curto demais para não ser adivinhado."""
    token = os.getenv(TOKEN_ENV) or ""
    return token if len(token) >= TOKEN_MIN_LEN else None


def authorized(sent: str | None) -> bool:
    expected = route_token()
    if expected is None or not sent:
        return False
    return hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))


def install(app: Any) -> None:
    if getattr(app.state, "rx_proc_stats_installed", False):
        return
    app.state.rx_proc_stats_installed = True
    from fastapi import Header, HTTPException
    from fastapi.responses import JSONResponse

    cache: dict[str, Any] = {"at": -1e9, "data": None}

    @app.get(ROUTE, include_in_schema=False)
    async def diag_processos(token: str | None = Header(default=None, alias=TOKEN_HEADER)):
        if not authorized(token):
            # o mesmo 404 de um endereço que não existe: sem o segredo a rota nem aparece
            raise HTTPException(status_code=404, detail="Not Found")
        now = time.monotonic()
        if cache["data"] is None or now - cache["at"] >= CACHE_S:
            periodic = getattr(app.state, "rx_proc_stats_task", None)
            cache["data"], cache["at"] = snapshot(ignore_tasks=(periodic,) if periodic is not None else ()), now
        return JSONResponse({"ok": True, **cache["data"],
                             "legenda": {"indisponivel": "não deu para medir neste servidor",
                                         "null": "não se aplica (sem filhos agora)"}},
                            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    async def _periodic(interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                print(log_line(snapshot(), "periodico"), flush=True)
            except Exception as exc:  # noqa: BLE001 - a medição nunca derruba o serviço
                print(f"RX_PROC_STATS_FALHA={type(exc).__name__}", flush=True)

    async def _on_startup() -> None:
        interval, warning = interval_config()
        if warning:
            print(warning, flush=True)
        try:
            print(log_line(snapshot(), "arranque"), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"RX_PROC_STATS_FALHA={type(exc).__name__}", flush=True)
        if os.getenv(TOKEN_ENV) and route_token() is None:
            print(f"RX_PROC_STATS_ROTA=desligada motivo=token_curto minimo={TOKEN_MIN_LEN}", flush=True)
        else:
            print(f"RX_PROC_STATS_ROTA={'ligada' if route_token() else 'desligada'}", flush=True)
        if interval > 0:
            app.state.rx_proc_stats_task = asyncio.create_task(_periodic(interval))

    async def _on_shutdown() -> None:
        task = getattr(app.state, "rx_proc_stats_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    app.router.on_startup.append(_on_startup)
    app.router.on_shutdown.append(_on_shutdown)


def main() -> None:  # python rx_proc_stats.py — a mesma medição, fora do servidor
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

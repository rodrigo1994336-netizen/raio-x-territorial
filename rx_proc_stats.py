"""M1 · quantos processos, threads, descritores e quanta memória o serviço segura agora.

Vale nos dois serviços (portal e relatório: os dois servem o mesmo app de ``report_api``):

* uma linha ``RX_PROC_STATS`` no log no arranque e a cada ``RX_PROC_STATS_MIN`` minutos (padrão 10;
  0 desliga a repetição e mantém a do arranque);
* a rota somente leitura ``GET /v1/diag/processos`` com os mesmos números, limitada a
  ``RATE_PER_MINUTE`` consultas por minuto no total e com a medição reaproveitada por ``CACHE_S`` segundos.

Lê ``/proc`` (Linux). Onde não dá para medir, o valor é ``"indisponivel"`` — nunca zero inventado: a
tabela de processos só vale se o próprio processo aparecer nela. ``None`` (``sem_filhos`` no log) é
"não se aplica". Não sai linha de comando, variável de ambiente, caminho nem PID: só contagens, idades,
memória e o nome curto do executável dos descendentes.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

INDISPONIVEL = "indisponivel"
ROUTE = "/v1/diag/processos"
PROC_ROOT = "/proc"
CGROUP_ROOT = "/sys/fs/cgroup"
DEFAULT_INTERVAL_MIN = 10.0
RATE_PER_MINUTE = 30
CACHE_S = 5.0
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


def _process_table(proc_root: str) -> dict[int, dict[str, Any]] | None:
    try:
        names = os.listdir(proc_root)
    except OSError:
        return None
    table: dict[int, dict[str, Any]] = {}
    for name in names:
        if not name.isdigit():
            continue
        text = _read(f"{proc_root}/{name}/stat")
        if text is None:  # terminou entre a listagem e a leitura
            continue
        parsed = _parse_stat(text)
        if parsed is not None:
            table[int(name)] = parsed
    return table


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


def _asyncio_pending() -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return INDISPONIVEL  # fora do laço não dá para contar sem risco de corrida
    return len(asyncio.all_tasks(loop))


def _managed_children() -> Any:
    try:
        import external_process_lifecycle
        return len(external_process_lifecycle.active_child_pids())
    except Exception:  # noqa: BLE001 - medição nunca derruba o serviço
        return INDISPONIVEL


def snapshot(proc_root: str = PROC_ROOT, cgroup_root: str = CGROUP_ROOT) -> dict[str, Any]:
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
        "filhos_gerenciados": _managed_children(),
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
        "tarefas_asyncio_pendentes": _asyncio_pending(),
    }
    table = _process_table(proc_root)
    if table is not None and pid in table:  # sem o próprio processo, a tabela não é deste serviço
        out["leitura"] = "proc_linux"
        out["e_pid_1"] = pid == 1
        out["processos_no_conteiner"] = len(table)
        out["zumbis_no_conteiner"] = sum(1 for p in table.values() if p["state"] == "Z")
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
        for k in below:
            if table[k]["state"] == "Z":  # zumbi não tem memória própria
                continue
            text = _read(f"{proc_root}/{k}/status")
            if text is None:  # terminou no meio da medição
                continue
            rss_kids += _status_kb(text, "VmRSS") or 0  # sem VmRSS = processo saindo, sem memória de usuário
        out["rss_descendentes_mb"] = _mb(rss_kids)
        uptime_text = _read(f"{proc_root}/uptime")
        try:
            ticks = os.sysconf("SC_CLK_TCK")
            uptime = float((uptime_text or "").split()[0])
        except (AttributeError, ValueError, OSError, IndexError):
            ticks, uptime = None, None
        if ticks and uptime is not None:
            out["processo_idade_s"] = round(max(0.0, uptime - table[pid]["start_ticks"] / ticks), 1)
            ages = [max(0.0, uptime - table[k]["start_ticks"] / ticks) for k in below]
            out["filho_mais_velho_s"] = round(max(ages), 1) if ages else None
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


def interval_seconds() -> float:
    try:
        minutes = float(os.getenv("RX_PROC_STATS_MIN", str(DEFAULT_INTERVAL_MIN)))
    except ValueError:
        minutes = DEFAULT_INTERVAL_MIN
    return max(0.0, minutes) * 60.0


class RateLimiter:
    """Janela deslizante global: no máximo ``per_minute`` respostas medidas por minuto, para qualquer origem."""

    def __init__(self, per_minute: int = RATE_PER_MINUTE):
        self.per_minute = per_minute
        self.hits: collections.deque[float] = collections.deque()
        self.lock = threading.Lock()

    def allow(self, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        with self.lock:
            while self.hits and now - self.hits[0] >= 60.0:
                self.hits.popleft()
            if len(self.hits) >= self.per_minute:
                return False, max(1, int(60.0 - (now - self.hits[0])) + 1)
            self.hits.append(now)
            return True, 0


def install(app: Any) -> None:
    if getattr(app.state, "rx_proc_stats_installed", False):
        return
    app.state.rx_proc_stats_installed = True
    from fastapi.responses import JSONResponse

    limiter = RateLimiter()
    cache: dict[str, Any] = {"at": -1e9, "data": None}

    @app.get(ROUTE, include_in_schema=False)
    async def diag_processos():
        allowed, retry = limiter.allow()
        headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
        if not allowed:
            return JSONResponse({"ok": False, "detalhe": "Muitas consultas seguidas. Tente de novo em alguns segundos."},
                                status_code=429, headers={**headers, "Retry-After": str(retry)})
        now = time.monotonic()
        if cache["data"] is None or now - cache["at"] >= CACHE_S:
            cache["data"], cache["at"] = snapshot(), now
        return JSONResponse({"ok": True, **cache["data"],
                             "legenda": {"indisponivel": "não deu para medir neste servidor",
                                         "null": "não se aplica (sem filhos agora)"}}, headers=headers)

    async def _periodic(interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                print(log_line(snapshot(), "periodico"), flush=True)
            except Exception as exc:  # noqa: BLE001 - a medição nunca derruba o serviço
                print(f"RX_PROC_STATS_FALHA={type(exc).__name__}", flush=True)

    async def _on_startup() -> None:
        try:
            print(log_line(snapshot(), "arranque"), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"RX_PROC_STATS_FALHA={type(exc).__name__}", flush=True)
        interval = interval_seconds()
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

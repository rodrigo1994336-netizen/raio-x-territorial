"""Ponte no Brasil: o cliente único do Raio-X.

O acervo fundiário do INCRA (e às vezes o SICAR) não responde a chamadas vindas do Render (EUA).
Quando RX_PONTE_BRASIL_URL (https) e RX_PONTE_BRASIL_TOKEN estão configuradas, as chamadas curl
para esses hosts passam pela ponte (ponte_brasil/app.py, Cloud Run em São Paulo). Sem as duas
variáveis válidas, run_curl chama o executor com exatamente os mesmos argumentos de antes.

Roteamento (decidido pelo código, 14/09/2026):
  * INCRA (acervofundiario.incra.gov.br): SEMPRE pela ponte. Daqui dos EUA ele não responde; tentar
    direto antes gastaria o tempo de conexão inteiro (5 s de 10 s no acervo) e a ponte ficaria sem
    prazo. As respostas são GML pequenos, custo de saída desprezível.
  * SICAR (geoserver.car.gov.br): DIRETO primeiro, com os mesmos argumentos e prazos de hoje; se a
    tentativa falhar no transporte (sem resposta: DNS, conexão, TLS, tempo, conexão caída) ou, nas
    chamadas que já usam --fail, com HTTP 403/429/5xx, e ainda sobrar prazo (>= 3 s dentro do mesmo
    limite duro), a MESMA chamada vai pela ponte com o tempo que resta. Motivo: o SICAR costuma
    responder ao Render, e os mapas e camadas dele pesam megabytes (mais latência e saída de rede
    paga em São Paulo, e a ponte tem uma instância só). Depois de uma queda direta resgatada pela
    ponte, as chamadas ao SICAR vão direto para a ponte por DIRECT_SKIP_WINDOW_S; a janela só fecha
    quando a falha é da PRÓPRIA ponte (não chegou nela, ou ela recusou o token). Ponte ocupada
    (429/503) ou erro da fonte repassado (500/502/504...) mantêm a janela: voltar a tentar direto
    só gastaria de novo o tempo de conexão.
  * O limite duro (timeout_seconds) de cada chamada nunca aumenta: a ponte recebe só o que sobra,
    e o --max-time do curl é reduzido para caber nele. Cancelamento continua o mesmo (o executor
    gerenciado mata o curl).

Segredo: o token vai para o curl pela entrada padrão (-H @-), nunca na linha de comando (visível
para outros processos) nem em log. Pela ponte o curl sempre verifica o certificado (-k removido) e
usa --fail: erro da ponte ou da fonte vira falha (consulta pendente), nunca "não encontrado".

Endereço da ponte: nunca sai no resultado. O curl escreve o host no erro (códigos 6, 7, 35) e o
TimeoutExpired carrega a linha de comando; os transportes põem esse texto em "detail", que chega ao
navegador. Por isso o resultado devolvido traz os argumentos originais (endereço oficial) e o erro
com o endereço da ponte trocado pelo oficial.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

ENV_URL = "RX_PONTE_BRASIL_URL"
ENV_TOKEN = "RX_PONTE_BRASIL_TOKEN"
INCRA_HOSTS = frozenset({"acervofundiario.incra.gov.br"})
SICAR_HOSTS = frozenset({"geoserver.car.gov.br"})
ROUTED_HOSTS = INCRA_HOSTS | SICAR_HOSTS   # o gate exige igualdade com ponte_brasil/app.py ALLOWED_HOSTS
FETCH_PATH = "/v1/fetch"
BRIDGE_TIMEOUT_CAP_S = 25.0               # o mesmo UPSTREAM_TIMEOUT_S da ponte
MIN_BRIDGE_BUDGET_S = 3.0
DIRECT_SKIP_WINDOW_S = 300.0
MIN_TOKEN_LEN = 32
# curl: 6 DNS · 7 conexão · 28 tempo · 35 TLS · 52 resposta vazia · 55 envio · 56 recepção
FALLBACK_CURL_EXITS = frozenset({6, 7, 28, 35, 52, 55, 56})
FALLBACK_HTTP_STATUS = frozenset({403, 429, 500, 502, 503, 504})
LOG_EVERY_S = 600.0

ROUTE_DIRECT = "direct"
ROUTE_BRIDGE = "bridge"
ROUTE_DIRECT_THEN_BRIDGE = "direct_then_bridge"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
_HTTP_CODE_RE = re.compile(r"error: (\d{3})")
_MAX_TIME_OPTS = ("--max-time", "-m")
_FAIL_OPTS = ("--fail", "-f", "--fail-with-body")
_INSECURE_OPTS = ("-k", "--insecure")

_now: Callable[[], float] = time.monotonic
_lock = threading.Lock()
_skip_direct_until: dict[str, float] = {}
_last_log: dict[str, float] = {}


@dataclass(frozen=True)
class Config:
    fetch_url: str
    token: str
    bridge_host: str


def config(env: Mapping[str, str] | None = None) -> Config | None:
    """As duas variáveis válidas, ou None (ponte desligada)."""
    source = os.environ if env is None else env
    raw_url = str(source.get(ENV_URL) or "").strip()
    token = str(source.get(ENV_TOKEN) or "").strip()
    if not raw_url or not token:
        return None
    if len(token) < MIN_TOKEN_LEN or len(token) > 512 or not _TOKEN_RE.match(token):
        return None
    if not raw_url.startswith("https://") or any(ord(ch) <= 0x20 for ch in raw_url):
        return None
    parts = urlsplit(raw_url)
    if not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment:
        return None
    base = raw_url.rstrip("/")
    if base.endswith(FETCH_PATH):
        base = base[: -len(FETCH_PATH)]
    return Config(fetch_url=base + FETCH_PATH, token=token, bridge_host=parts.hostname)


def status_line(env: Mapping[str, str] | None = None) -> str:
    cfg = config(env)
    source = os.environ if env is None else env
    if cfg is not None:
        return f"RX_PONTE_BRASIL=on host={cfg.bridge_host} incra=ponte sicar=direto_primeiro"
    if source.get(ENV_URL) or source.get(ENV_TOKEN):
        return "RX_PONTE_BRASIL=off motivo=configuracao_invalida"
    return "RX_PONTE_BRASIL=off"


def url_has_forbidden_chars(url: str) -> bool:
    """Controle, espaço, barra invertida ou não-ASCII: o endereço vai numa linha de cabeçalho da entrada
    padrão do curl (X-Ponte-Url), e CR/LF ali seria cabeçalho injetado."""
    return (not url.isascii()) or "\\" in url or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url)


def routed_host(url: str) -> str | None:
    """Host da lista quando o endereço é https canônico para ele; senão None (vai direto)."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    if url_has_forbidden_chars(url):
        return None
    netloc = urlsplit(url).netloc
    if "@" in netloc:
        return None
    host, _, port = netloc.partition(":")
    if port not in ("", "443"):
        return None
    return host if host in ROUTED_HOSTS else None


def route_for(url: str, env: Mapping[str, str] | None = None) -> str:
    if config(env) is None:
        return ROUTE_DIRECT
    host = routed_host(url)
    if host in INCRA_HOSTS:
        return ROUTE_BRIDGE
    if host in SICAR_HOSTS:
        return ROUTE_DIRECT_THEN_BRIDGE
    return ROUTE_DIRECT


def _url_index(args: Sequence[str]) -> int | None:
    found = [i for i, a in enumerate(args) if i > 0 and isinstance(a, str) and a.startswith(("https://", "http://"))]
    return found[0] if len(found) == 1 else None


def _fmt_seconds(value: float) -> str:
    return f"{max(0.5, value):.2f}".rstrip("0").rstrip(".")


def _max_time(args: Sequence[str]) -> float | None:
    for i, a in enumerate(args):
        if a in _MAX_TIME_OPTS and i + 1 < len(args):
            try:
                return float(args[i + 1])
            except ValueError:
                return None
    return None


def bridge_request(args: Sequence[str], cfg: Config, *, budget_s: float | None = None) -> tuple[list[str], bytes]:
    """Argumentos do curl pela ponte e a entrada padrão com os cabeçalhos (token incluído)."""
    idx = _url_index(args)
    if idx is None:
        raise ValueError("curl_sem_url_unica")
    target = args[idx]
    original_max = _max_time(args)
    max_time = original_max
    if budget_s is not None:
        max_time = min(original_max, budget_s - 0.25) if original_max is not None else budget_s - 0.25
    out: list[str] = [args[0], "-H", "@-"]
    i = 1
    saw_max = saw_fail = False
    while i < len(args):
        a = args[i]
        if i == idx:
            out.append(cfg.fetch_url)
        elif a in _INSECURE_OPTS:
            pass
        elif a in _MAX_TIME_OPTS and i + 1 < len(args):
            saw_max = True
            out.extend([a, _fmt_seconds(max_time) if max_time is not None else args[i + 1]])
            i += 1
        else:
            if a in _FAIL_OPTS:
                saw_fail = True
            out.append(a)
        i += 1
    if not saw_fail:
        out.insert(3, "--fail")
    if not saw_max and max_time is not None:
        out[3:3] = ["--max-time", _fmt_seconds(max_time)]
    timeout_header = min(BRIDGE_TIMEOUT_CAP_S, max_time) if max_time is not None else BRIDGE_TIMEOUT_CAP_S
    stdin = (
        f"X-Ponte-Token: {cfg.token}\r\n"
        f"X-Ponte-Url: {target}\r\n"
        f"X-Ponte-Timeout: {_fmt_seconds(timeout_header)}\r\n"
    ).encode("ascii")
    return out, stdin


def _log_once(key: str, line: str) -> None:
    now = _now()
    with _lock:
        last = _last_log.get(key)
        if last is not None and now - last < LOG_EVERY_S:
            return
        _last_log[key] = now
    print(line, flush=True)


def _http_status(proc: subprocess.CompletedProcess) -> int | None:
    err = proc.stderr or b""
    text = err.decode("utf-8", "ignore") if isinstance(err, bytes) else str(err)
    match = _HTTP_CODE_RE.search(text)
    return int(match.group(1)) if match else None


def should_fallback(proc: subprocess.CompletedProcess) -> bool:
    if proc.returncode in FALLBACK_CURL_EXITS:
        return True
    return proc.returncode == 22 and _http_status(proc) in FALLBACK_HTTP_STATUS


def bridge_itself_failed(proc: subprocess.CompletedProcess) -> bool:
    """A falha foi da PRÓPRIA ponte: o curl não chegou nela (DNS, conexão, TLS, tempo, conexão caída) ou
    ela recusou o token (401). Ponte ocupada (429/503) e erro da fonte repassado (500/502/504) não contam."""
    if proc.returncode in FALLBACK_CURL_EXITS:
        return True
    return proc.returncode == 22 and _http_status(proc) == 401


def _note_bridge_failure(host: str, proc: subprocess.CompletedProcess) -> None:
    status = _http_status(proc) if proc.returncode == 22 else None
    if status in (401, 403, 503):
        # O curl do Render não vê de onde veio o código: 401/403/503 podem ser da ponte (token errado, host
        # fora da lista, ponte sem token ou ocupada) ou da fonte repassada. O guia manda conferir pelo
        # Cloud Shell antes de trocar o token. Nunca o token no log.
        _log_once(f"bridge:{status}", f"RX_PONTE_BRASIL=erro_ponte http={status} origem=ponte_ou_fonte host={host}")


def _scrubber(cfg: Config, target: str) -> Callable[[Any], Any]:
    """Troca o endereço e o host da ponte pelo endereço e host oficiais (texto ou bytes)."""
    official_host = urlsplit(target).hostname or "fonte"
    host_re = re.compile(re.escape(cfg.bridge_host), re.I)
    base = cfg.fetch_url[: -len(FETCH_PATH)]

    def scrub(value: Any) -> Any:
        if isinstance(value, bytes):
            return scrub(value.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
        if not isinstance(value, str):
            return value
        return host_re.sub(official_host, value.replace(cfg.fetch_url, target).replace(base, target))

    return scrub


def _run_bridge(runner: Callable[..., subprocess.CompletedProcess], args: Sequence[str], cfg: Config, *,
                timeout_seconds: float | None, cancel_event, budget_s: float | None = None
                ) -> subprocess.CompletedProcess:
    bridge_args, stdin = bridge_request(args, cfg, budget_s=budget_s)
    scrub = _scrubber(cfg, args[_url_index(args)])
    try:
        proc = runner(bridge_args, timeout_seconds=timeout_seconds, cancel_event=cancel_event, input_bytes=stdin)
    except subprocess.TimeoutExpired as exc:
        # str(exc) traz a linha de comando (endereço da ponte): relança com a chamada original.
        raise subprocess.TimeoutExpired(list(args), exc.timeout, output=exc.output, stderr=scrub(exc.stderr)) from None
    return subprocess.CompletedProcess(list(args), proc.returncode, proc.stdout, scrub(proc.stderr))


def direct_skipped(host: str) -> bool:
    with _lock:
        until = _skip_direct_until.get(host)
        if until is None:
            return False
        if _now() >= until:
            _skip_direct_until.pop(host, None)
            return False
        return True


def _open_skip_window(host: str, reason: str) -> None:
    with _lock:
        _skip_direct_until[host] = _now() + DIRECT_SKIP_WINDOW_S
    _log_once(f"window:{host}", f"RX_PONTE_BRASIL=sicar_direto_falhou motivo={reason} ponte=ok janela_s={int(DIRECT_SKIP_WINDOW_S)}")


def _close_skip_window(host: str) -> None:
    with _lock:
        _skip_direct_until.pop(host, None)


def reset_state() -> None:
    with _lock:
        _skip_direct_until.clear()
        _last_log.clear()


def remaining_budget(timeout_seconds: float | None, started: float) -> float | None:
    """O que sobra do limite duro da chamada depois da tentativa direta (nunca mais que ele)."""
    if timeout_seconds is None:
        return None
    return timeout_seconds - (_now() - started)


def subprocess_runner(args: Sequence[str], *, timeout_seconds: float | None, cancel_event=None,
                      input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Executor dos módulos que usavam subprocess.run(args, capture_output=True, timeout=...)."""
    if input_bytes is None:
        return subprocess.run(list(args), capture_output=True, timeout=timeout_seconds)
    return subprocess.run(list(args), input=input_bytes, capture_output=True, timeout=timeout_seconds)


def run_curl(args: Sequence[str], *, timeout_seconds: float | None, cancel_event=None,
             runner: Callable[..., subprocess.CompletedProcess], env: Mapping[str, str] | None = None
             ) -> subprocess.CompletedProcess:
    """Executa um curl, pela ponte quando o host é do INCRA/SICAR e a ponte está configurada."""
    cfg = config(env)
    idx = _url_index(args) if cfg is not None else None
    host = routed_host(args[idx]) if idx is not None else None
    if cfg is None or host is None:
        return runner(args, timeout_seconds=timeout_seconds, cancel_event=cancel_event)

    if host in INCRA_HOSTS:
        proc = _run_bridge(runner, args, cfg, timeout_seconds=timeout_seconds, cancel_event=cancel_event)
        if proc.returncode:
            _note_bridge_failure(host, proc)
        return proc

    started = _now()
    reason: str | None = None
    budget = timeout_seconds
    if not direct_skipped(host):
        proc = runner(args, timeout_seconds=timeout_seconds, cancel_event=cancel_event)
        if not should_fallback(proc):
            return proc
        if cancel_event is not None and cancel_event.is_set():
            return proc
        budget = remaining_budget(timeout_seconds, started)
        if budget is not None and budget < MIN_BRIDGE_BUDGET_S:
            return proc
        status = _http_status(proc) if proc.returncode == 22 else None
        reason = f"http_{status}" if status else f"curl_{proc.returncode}"
    bridged = _run_bridge(runner, args, cfg, timeout_seconds=budget, cancel_event=cancel_event, budget_s=budget)
    if bridged.returncode == 0:
        if reason is not None:
            _open_skip_window(host, reason)
    else:
        _note_bridge_failure(host, bridged)
        if reason is None and bridge_itself_failed(bridged):
            _close_skip_window(host)
    return bridged


print(status_line(), flush=True)

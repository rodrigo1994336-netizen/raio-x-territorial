"""Ponte no Brasil: repassador mínimo para as fontes oficiais que não respondem fora do Brasil.

Roda no Google Cloud Run em southamerica-east1 (São Paulo). O Raio-X (Render, EUA) chama
POST/GET /v1/fetch com o token no cabeçalho X-Ponte-Token e o endereço oficial em X-Ponte-Url;
a ponte busca SOMENTE hosts oficiais da lista fechada e devolve o status e o corpo da fonte.

Travas (cada uma tem teste e controle positivo em scripts/ponte_brasil_gate.py):
  * token obrigatório (hmac.compare_digest); sem PONTE_TOKEN (ou curto demais) recusa tudo;
  * só https, porta 443, host exato da lista, sem IP literal, sem userinfo, sem ponto final
    nem maiúsculas;
  * DNS resolvido pela ponte e conexão presa ao IP conferido (sem DNS rebinding): IP privado,
    loopback, link-local (169.254.169.254, metadados) ou reservado é recusado;
  * até 3 redirecionamentos, cada um conferido pelas mesmas travas;
  * só GET e POST; corpo de requisição até 1 MiB; resposta até 25 MiB; 25 s no total (o cliente
    pode pedir menos, nunca mais);
  * repassa à fonte só Accept, Content-Type (conferido) e User-Agent; devolve só Content-Type e
    Content-Encoding (nada de cookie); POST que recebe 301/302/303 segue como GET sem corpo;
  * TLS verificado; a concessão de cifra com troca de chave RSA vale só para o SICAR, que só
    negocia assim; o INCRA usa o contexto padrão do Python;
  * prazo total de 10 s para linha de pedido, cabeçalhos e corpo, e teto de conexões abertas
    (cliente que pinga um byte por vez não segura a ponte);
  * limite de taxa por instância; log sem token, sem corpo e sem endereço completo.

Porta de fora (15/09/2026): o guia publica a ponte com --no-allow-unauthenticated. O Cloud Run recusa,
antes do contêiner, quem não traz token de identidade do Google (X-Serverless-Authorization) da conta de
serviço com roles/run.invoker; pela página de preços do Cloud Run, pedido recusado pelo IAM não é
cobrado. O X-Ponte-Token continua obrigatório aqui dentro (segunda trava). Nenhum dos dois cabeçalhos
vai para a fonte (só Accept, Content-Type e User-Agent). --max-instances 1 limita máquinas, não pedidos:
o teto de gasto é o orçamento com teto do Cloud Run (ver docs/PONTE_BRASIL_ATIVACAO.md, custo).

Só biblioteca padrão. Sobe com: PORT=8080 PONTE_TOKEN=... python app.py
"""
from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urljoin, urlsplit

# Lista fechada: TODOS os hosts do INCRA e do SICAR que o sistema chama pelo servidor (conferido no código
# em 14/09/2026). consulta.car.gov.br é só link aberto no navegador do cliente; não passa pelo servidor.
ALLOWED_HOSTS = frozenset({
    "acervofundiario.incra.gov.br",  # INCRA Acervo Fundiário: i3geo/ogc.php (SIGEF e SNCI)
    "geoserver.car.gov.br",          # SICAR: geoserver/sicar/ows e geoserver/ows
})
ALLOWED_METHODS = frozenset({"GET", "POST"})
UPSTREAM_PORT = 443
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
UPSTREAM_TIMEOUT_S = 25.0
MIN_TOKEN_LEN = 32
MAX_URL_LEN = 8192
MAX_HEADER_VALUE_LEN = 512
RATE_PER_S = 20.0            # pedidos autenticados por segundo, por instância
RATE_BURST = 40
UNAUTH_RATE_PER_S = 5.0      # tentativas sem token válido
UNAUTH_BURST = 10
MAX_INFLIGHT = 16            # buscas simultâneas na fonte
BUFFER_BUDGET_BYTES = 96 * 1024 * 1024   # soma dos corpos em memória (instância de 256 MiB)
READ_CHUNK = 64 * 1024
DEFAULT_USER_AGENT = "RaioX-PonteBrasil/1"
HEADER_DEADLINE_S = 10.0     # linha de pedido + cabeçalhos + corpo, no total (o timeout de leitura é por leitura)
MAX_CONNECTIONS = 96         # conexões abertas ao mesmo tempo; acima fecha na hora (Cloud Run: concorrência 80)

# SICAR: só negocia troca de chave RSA (AES256-GCM-SHA384, medido em 14/09/2026). A lista
# do Python a recusa; esta aceita, com certificado verificado e sem cifras nulas, MD5 ou 3DES.
RSA_KEX_TLS_HOSTS = frozenset({"geoserver.car.gov.br"})
RSA_KEX_CIPHERS = "DEFAULT:!aNULL:!eNULL:!MD5:!3DES:@SECLEVEL=2"

FETCH_PATH = "/v1/fetch"
# Nunca um caminho terminado em "z" (/healthz): o Cloud Run reserva e responde antes do contêiner.
HEALTH_PATH = "/v1/health"
HDR_TOKEN = "X-Ponte-Token"
HDR_URL = "X-Ponte-Url"
HDR_TIMEOUT = "X-Ponte-Timeout"
HDR_ERROR = "X-Ponte-Error"
HDR_ORIGIN = "X-Ponte-Origin"
FORWARDED_REQUEST_HEADERS = ("Accept", "Content-Type", "User-Agent")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(\s*;\s*[A-Za-z0-9_.-]+=\"?[A-Za-z0-9_.:/-]+\"?)*$")
_CONTENT_ENCODINGS = frozenset({"gzip", "deflate", "br", "zstd"})
_IPISH_RE = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$")


class Refused(Exception):
    """Recusa da própria ponte: status HTTP e código curto (vai no cabeçalho X-Ponte-Error)."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------------------------
# travas puras
# ---------------------------------------------------------------------------------------------

def token_configured(expected: str | None) -> bool:
    return bool(expected) and len(expected) >= MIN_TOKEN_LEN


def token_ok(provided: str | None, expected: str | None) -> bool:
    if not token_configured(expected) or provided is None:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), str(expected).encode("utf-8"))


def method_allowed(method: str) -> bool:
    return method in ALLOWED_METHODS


def has_userinfo(netloc: str) -> bool:
    return "@" in netloc


def port_ok(port: str) -> bool:
    return port in ("", str(UPSTREAM_PORT))


def is_ip_literal(host: str) -> bool:
    if host.startswith("[") or ":" in host:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # 2130706433, 0x7f000001, 127.1: formas que resolvedores aceitam como IP
    return bool(_IPISH_RE.match(host.lower()))


def host_canonical(host: str) -> bool:
    return bool(host) and host == host.lower() and not host.endswith(".")


def host_allowed(host: str) -> bool:
    return host in ALLOWED_HOSTS


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _address_is_public(ip) -> bool:
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified):
        return False
    return bool(ip.is_global)


def ip_is_public(ip_text: str) -> bool:
    if "%" in ip_text:  # IPv6 com zona é sempre link-local
        return False
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if ip.version == 6:
        # IPv4 embutido (mapeado, 6to4, Teredo, NAT64) é conferido como IPv4, sem depender da versão do
        # Python (a 3.12 do CI e a 3.13+ classificam essas faixas de jeitos diferentes).
        if ip.ipv4_mapped is not None:
            return _address_is_public(ip.ipv4_mapped)
        embedded = []
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if ip in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(not _address_is_public(v4) for v4 in embedded):
            return False
    return _address_is_public(ip)


def validate_target(url: str | None) -> tuple[str, str]:
    """Endereço oficial -> (host, alvo da requisição). Levanta Refused com o código da trava."""
    if not isinstance(url, str) or not url:
        raise Refused(400, "target_missing")
    if len(url) > MAX_URL_LEN or "\\" in url or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        raise Refused(400, "target_malformed")
    if not url.startswith("https://"):
        raise Refused(403, "scheme_not_https")
    try:
        parts = urlsplit(url)  # "https://[::1/" levanta ValueError: é entrada malformada, não erro interno
    except ValueError:
        raise Refused(400, "target_malformed") from None
    netloc = parts.netloc
    if has_userinfo(netloc):
        raise Refused(403, "userinfo_refused")
    if netloc.startswith("["):
        raise Refused(403, "ip_literal_refused")
    host, _, port = netloc.partition(":")
    if ":" in port:
        raise Refused(400, "target_malformed")
    if not port_ok(port):
        raise Refused(403, "port_refused")
    if not host:
        raise Refused(400, "target_malformed")
    if is_ip_literal(host):
        raise Refused(403, "ip_literal_refused")
    if not host_canonical(host):
        raise Refused(403, "host_not_canonical")
    if not host_allowed(host):
        raise Refused(403, "host_not_allowed")
    path = parts.path or "/"
    if not path.startswith("/"):
        raise Refused(400, "target_malformed")
    return host, path + ("?" + parts.query if parts.query else "")


def redirect_allowed(url: str) -> bool:
    try:
        validate_target(url)
        return True
    except Refused:
        return False


def body_within_limit(size: int, limit: int) -> bool:
    return size <= limit


def request_within_limit(size: int, limit: int) -> bool:
    return size <= limit


def clean_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > MAX_HEADER_VALUE_LEN or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    return value


def clean_content_type(value: str | None) -> str | None:
    value = clean_header_value(value)
    return value if value and _CONTENT_TYPE_RE.match(value) else None


def upstream_headers(client_headers, has_body: bool) -> dict[str, str]:
    """O que vai para a fonte: só Accept, Content-Type (com corpo) e User-Agent do cliente."""
    get = client_headers.get if client_headers is not None else (lambda _k: None)
    headers = {
        "User-Agent": clean_header_value(get("User-Agent")) or DEFAULT_USER_AGENT,
        "Accept": clean_header_value(get("Accept")) or "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if has_body:
        headers["Content-Type"] = clean_content_type(get("Content-Type")) or "application/octet-stream"
    return headers


def response_headers(resp) -> list[tuple[str, str]]:
    """O que volta ao cliente: só Content-Type e Content-Encoding (nada de cookie)."""
    out = [("Content-Type", clean_content_type(resp.getheader("Content-Type")) or "application/octet-stream")]
    encoding = clean_header_value(resp.getheader("Content-Encoding"))
    if encoding in _CONTENT_ENCODINGS:
        out.append(("Content-Encoding", encoding))
    return out


# ---------------------------------------------------------------------------------------------
# limites de taxa e de memória
# ---------------------------------------------------------------------------------------------

class TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self.rate = float(rate)
        self.burst = float(burst)
        self.clock = clock
        self.tokens = float(burst)
        self.last = clock()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            self.tokens = min(self.burst, self.tokens + max(0.0, now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class ByteBudget:
    def __init__(self, limit: int):
        self.limit = int(limit)
        self.used = 0
        self.lock = threading.Lock()

    def reserve(self, n: int) -> bool:
        with self.lock:
            if self.used + n > self.limit:
                return False
            self.used += n
            return True

    def release(self, n: int) -> None:
        with self.lock:
            self.used = max(0, self.used - n)


# ---------------------------------------------------------------------------------------------
# DNS e conexão presa ao IP conferido
# ---------------------------------------------------------------------------------------------

_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ponte-dns")


def system_resolver(host: str, timeout: float) -> list[str]:
    future = _DNS_POOL.submit(socket.getaddrinfo, host, UPSTREAM_PORT, 0, socket.SOCK_STREAM)
    try:
        infos = future.result(timeout=max(0.1, timeout))
    except FutureTimeout:
        raise Refused(504, "dns_timeout") from None
    except OSError:
        raise Refused(502, "dns_failed") from None
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        if ip not in ips:
            ips.append(ip)
    ips.sort(key=lambda ip: ":" in ip)  # IPv4 primeiro
    return ips


def tls_context(host: str | None = None) -> ssl.SSLContext:
    """Certificado e nome sempre conferidos. Só os hosts de RSA_KEX_TLS_HOSTS (o SICAR) recebem a lista de
    cifras que aceita troca de chave RSA; os demais (o INCRA) ficam com a lista padrão do Python."""
    ctx = ssl.create_default_context()
    if host in RSA_KEX_TLS_HOSTS:
        ctx.set_ciphers(RSA_KEX_CIPHERS)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


_TLS_DEFAULT = tls_context()
_TLS_RSA_KEX = tls_context(next(iter(RSA_KEX_TLS_HOSTS)))


def tls_context_for(host: str) -> ssl.SSLContext:
    return _TLS_RSA_KEX if host in RSA_KEX_TLS_HOSTS else _TLS_DEFAULT


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS para o nome oficial (SNI e certificado), conectando no IP já conferido."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, UPSTREAM_PORT, timeout=timeout, context=tls_context_for(host))
        self.pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self.pinned_ip, UPSTREAM_PORT), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def tls_connector(host: str, ip: str, timeout: float) -> http.client.HTTPConnection:
    return PinnedHTTPSConnection(host, ip, timeout)


def resolve_public(host: str, resolver: Callable[[str, float], list[str]], timeout: float) -> list[str]:
    ips = resolver(host, timeout)
    if not ips:
        raise Refused(502, "dns_failed")
    for ip in ips:
        if not ip_is_public(ip):
            raise Refused(403, "resolved_private_ip")
    return ips


# ---------------------------------------------------------------------------------------------
# busca na fonte
# ---------------------------------------------------------------------------------------------

@dataclass
class Upstream:
    status: int
    body: bytes
    headers: list[tuple[str, str]]
    host: str
    reserved: int = 0


@dataclass
class Service:
    token: str = ""
    resolver: Callable[[str, float], list[str]] = system_resolver
    connector: Callable[[str, str, float], http.client.HTTPConnection] = tls_connector
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_request_bytes: int = MAX_REQUEST_BYTES
    upstream_timeout_s: float = UPSTREAM_TIMEOUT_S
    max_redirects: int = MAX_REDIRECTS
    header_deadline_s: float = HEADER_DEADLINE_S
    max_connections: int = MAX_CONNECTIONS
    clock: Callable[[], float] = time.monotonic
    log_stream: object = None
    bucket: TokenBucket = field(default_factory=lambda: TokenBucket(RATE_PER_S, RATE_BURST))
    unauth_bucket: TokenBucket = field(default_factory=lambda: TokenBucket(UNAUTH_RATE_PER_S, UNAUTH_BURST))
    inflight: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(MAX_INFLIGHT))
    budget: ByteBudget = field(default_factory=lambda: ByteBudget(BUFFER_BUDGET_BYTES))

    @property
    def token_configured(self) -> bool:
        return token_configured(self.token)

    def log(self, **fields: object) -> None:
        stream = self.log_stream or sys.stderr
        try:
            stream.write(json.dumps(fields, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
        except Exception:
            pass


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    left = deadline - clock()
    if left <= 0:
        raise Refused(504, "upstream_timeout")
    return left


def _open(svc: Service, host: str, ips: list[str], deadline: float) -> http.client.HTTPConnection:
    last: Exception | None = None
    for ip in ips:
        conn = svc.connector(host, ip, _remaining(deadline, svc.clock))
        try:
            conn.connect()
            return conn
        except (TimeoutError, socket.timeout) as exc:
            conn.close()
            last = exc
        except (OSError, ssl.SSLError) as exc:
            conn.close()
            last = exc
    if isinstance(last, (TimeoutError, socket.timeout)):
        raise Refused(504, "upstream_timeout")
    raise Refused(502, "upstream_connect_failed")


def body_complete(resp) -> bool:
    """Conexão caída no meio de um corpo com Content-Length não é resposta: read1 só fecha, sem erro."""
    return resp.length in (None, 0)


def _shutdown_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _expire(sock: socket.socket, expired: threading.Event) -> None:
    expired.set()
    _shutdown_socket(sock)


def fetch(svc: Service, method: str, url: str, body: bytes | None, *, client_headers, timeout_s: float) -> Upstream:
    deadline = svc.clock() + timeout_s
    current = url
    for hop in range(svc.max_redirects + 1):
        host, target = validate_target(current)
        ips = resolve_public(host, svc.resolver, _remaining(deadline, svc.clock))
        conn = _open(svc, host, ips, deadline)
        # http.client fecha o socket da conexão ao ver "Connection: close" (antes de lermos o corpo); a
        # ponte precisa dele aberto para prazo e vigia, e fecha ela mesma no finally.
        real_close = conn.close
        conn.close = lambda: None  # type: ignore[method-assign]
        sock = conn.sock
        resp = None
        reserved = 0
        # Vigia do prazo total: fonte que pinga um byte por vez não estica a leitura além dos 25 s.
        expired = threading.Event()
        watchdog = threading.Timer(max(0.0, deadline - svc.clock()), _expire, args=(sock, expired))
        watchdog.daemon = True
        watchdog.start()
        try:
            send_body = body if method == "POST" else None
            headers = upstream_headers(client_headers, send_body is not None)
            sock.settimeout(_remaining(deadline, svc.clock))
            conn.request(method, target, body=send_body, headers=headers)
            sock.settimeout(_remaining(deadline, svc.clock))
            resp = conn.getresponse()
            if resp.status in REDIRECT_STATUSES:
                location = resp.getheader("Location")
                if not location:
                    raise Refused(502, "redirect_without_location")
                nxt = urljoin(current, location.strip())
                if not redirect_allowed(nxt):
                    raise Refused(502, "redirect_blocked")
                if hop >= svc.max_redirects:
                    raise Refused(502, "too_many_redirects")
                if resp.status in (301, 302, 303) and method != "GET":
                    method, body = "GET", None
                current = nxt
                continue
            length = resp.getheader("Content-Length")
            if length is not None and length.strip().isdigit() and not body_within_limit(int(length), svc.max_response_bytes):
                raise Refused(502, "response_too_large")
            chunks: list[bytes] = []
            total = 0
            while True:
                sock.settimeout(_remaining(deadline, svc.clock))
                chunk = resp.read1(READ_CHUNK)
                if expired.is_set():
                    raise Refused(504, "upstream_timeout")
                if not chunk:
                    break
                total += len(chunk)
                if not body_within_limit(total, svc.max_response_bytes):
                    raise Refused(502, "response_too_large")
                if not svc.budget.reserve(len(chunk)):
                    raise Refused(503, "busy")
                reserved += len(chunk)
                chunks.append(chunk)
            if expired.is_set():
                raise Refused(504, "upstream_timeout")
            if not body_complete(resp):
                raise Refused(502, "upstream_incomplete")
            result = Upstream(status=resp.status, body=b"".join(chunks), headers=response_headers(resp),
                              host=host, reserved=reserved)
            reserved = 0  # dono passa a ser o chamador
            return result
        except Refused:
            raise
        except (TimeoutError, socket.timeout):
            raise Refused(504, "upstream_timeout") from None
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
            if expired.is_set():
                raise Refused(504, "upstream_timeout") from None
            raise Refused(502, "upstream_error") from None
        finally:
            watchdog.cancel()
            if reserved:
                svc.budget.release(reserved)
            if resp is not None:
                resp.close()
            real_close()
    raise Refused(502, "too_many_redirects")


# ---------------------------------------------------------------------------------------------
# servidor HTTP
# ---------------------------------------------------------------------------------------------

class PonteHandler(BaseHTTPRequestHandler):
    server_version = "ponte"
    sys_version = ""

    def version_string(self) -> str:
        return self.server_version
    protocol_version = "HTTP/1.1"
    timeout = 30

    @property
    def svc(self) -> Service:
        return self.server.service  # type: ignore[attr-defined]

    # nada do log padrão (linha de requisição, cabeçalhos): o log é o de _finish
    def log_request(self, code="-", size="-") -> None:  # noqa: D401
        return None

    def log_message(self, format, *args) -> None:  # noqa: A002
        return None

    def handle_one_request(self) -> None:
        # Prazo total para linha de pedido, cabeçalhos e corpo. O timeout acima (30 s) vale por leitura: um
        # cliente que manda um byte a cada 20 s o renovaria para sempre. No Cloud Run o front-end do Google
        # já junta os cabeçalhos; este prazo vale para a ponte em qualquer lugar.
        expired = threading.Event()
        self._intake_timer = threading.Timer(self.svc.header_deadline_s, _expire, args=(self.connection, expired))
        self._intake_timer.daemon = True
        self._intake_timer.start()
        try:
            super().handle_one_request()
        except OSError:
            if not expired.is_set():
                raise
            self.close_connection = True  # prazo vencido: a leitura cai (no Windows com erro, no Linux vazia)
        finally:
            self._intake_timer.cancel()

    def _intake_done(self) -> None:
        timer = getattr(self, "_intake_timer", None)
        if timer is not None:
            timer.cancel()

    def do_GET(self) -> None:
        self._handle()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_TRACE = do_CONNECT = do_GET

    def _send(self, status: int, body: bytes, *, headers: list[tuple[str, str]] | None = None,
              error: str | None = None, origin: str | None = None) -> None:
        if error:
            self.close_connection = True
        self.send_response(status)
        for name, value in headers or [("Content-Type", "text/plain; charset=utf-8")]:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if error:
            self.send_header(HDR_ERROR, error)
        if origin:
            self.send_header(HDR_ORIGIN, origin)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _refuse(self, status: int, code: str) -> None:
        self._send(status, f"ponte_brasil: {code}\n".encode("ascii"), error=code, origin="ponte")

    def _read_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding"):
            raise Refused(411, "length_required")
        raw = self.headers.get("Content-Length")
        if raw is None or raw.strip() == "":
            if self.command == "POST":
                raise Refused(411, "length_required")
            return None
        raw = raw.strip()
        if not (raw.isascii() and raw.isdigit()):  # "²".isdigit() é True e int("²") falha
            raise Refused(400, "bad_content_length")
        n = int(raw)
        if not request_within_limit(n, self.svc.max_request_bytes):
            raise Refused(413, "request_too_large")
        if self.command == "GET":
            if n:
                raise Refused(400, "get_with_body")
            return None
        data = self.rfile.read(n) if n else b""
        if len(data) != n:
            raise Refused(400, "body_incomplete")
        return data

    def _timeout(self) -> float:
        limit = float(self.svc.upstream_timeout_s)
        raw = self.headers.get(HDR_TIMEOUT)
        try:
            asked = float(raw) if raw is not None else limit
        except ValueError:
            asked = limit
        if asked != asked:  # NaN
            asked = limit
        return max(1.0, min(limit, asked))

    def _handle(self) -> None:
        svc = self.svc
        started = svc.clock()
        path = self.path.split("?", 1)[0]
        status, code, host, size = 500, "internal", None, 0
        upstream: Upstream | None = None
        try:
            if path == HEALTH_PATH:
                if self.command not in ("GET", "HEAD"):
                    raise Refused(405, "method_not_allowed")
                if not svc.token_configured:
                    raise Refused(503, "not_configured")
                status, code = 200, ""
                self._send(200, b"ok\n")
                return
            if path != FETCH_PATH:
                raise Refused(404, "not_found")
            if not svc.token_configured:
                raise Refused(503, "not_configured")
            if not token_ok(self.headers.get(HDR_TOKEN), svc.token):
                if not svc.unauth_bucket.allow():
                    raise Refused(429, "rate_limited")
                raise Refused(401, "unauthorized")
            if not svc.bucket.allow():
                raise Refused(429, "rate_limited")
            if not method_allowed(self.command):
                raise Refused(405, "method_not_allowed")
            body = self._read_body()
            self._intake_done()  # pedido inteiro lido: daqui em diante vale o prazo da busca
            url = self.headers.get(HDR_URL)
            host, _ = validate_target(url)
            if not svc.inflight.acquire(blocking=False):
                raise Refused(503, "busy")
            try:
                upstream = fetch(svc, self.command, str(url), body, client_headers=self.headers,
                                 timeout_s=self._timeout())
            finally:
                svc.inflight.release()
            status, code, size = upstream.status, "", len(upstream.body)
            self._send(upstream.status, upstream.body, headers=upstream.headers, origin="upstream")
        except Refused as exc:
            status, code = exc.status, exc.code
            try:
                self._refuse(exc.status, exc.code)
            except Exception:
                self.close_connection = True
        except Exception as exc:  # nunca o texto da exceção (pode carregar endereço ou corpo)
            status, code = 500, f"internal:{type(exc).__name__}"
            try:
                self._refuse(500, "internal")
            except Exception:
                self.close_connection = True
        finally:
            if upstream is not None and upstream.reserved:
                svc.budget.release(upstream.reserved)
            svc.log(event="req", method=str(self.command)[:10], path=path[:64], status=status, error=code or None,
                    host=host, bytes=size, ms=int((svc.clock() - started) * 1000))


class PonteServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: Service):
        self.service = service
        self._slots = threading.BoundedSemaphore(service.max_connections)
        super().__init__(address, PonteHandler)

    def process_request(self, request, client_address) -> None:
        # Teto de conexões abertas (uma thread cada): acima dele a conexão fecha na hora, sem thread nova.
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(address: tuple[str, int], service: Service) -> PonteServer:
    return PonteServer(address, service)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    service = Service(token=os.environ.get("PONTE_TOKEN", "").strip())
    server = make_server(("0.0.0.0", port), service)
    service.log(event="start", port=port, token_configured=service.token_configured,
                hosts=sorted(ALLOWED_HOSTS))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

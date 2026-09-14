"""Ponte no Brasil gate: travas da ponte, roteamento do cliente e controles positivos.

Sem rede externa. Sobe a ponte (ponte_brasil/app.py) em 127.0.0.1 com resolvedor e conector
injetados que levam os hosts oficiais a uma fonte falsa local, e confere:

  S  servidor: token ausente/errado, sem token configurado, host fora da lista, IP literal,
     userinfo, porta, ponto final/maiúsculas, IP privado pelo DNS (e conexão presa ao IP
     conferido), redirecionamento para fora/demais, corpo grande (com o limite de produção),
     método proibido, corpo de requisição, tempo limite, cabeçalhos repassados, limite de taxa,
     log sem token/corpo/consulta, status da fonte repassado, TLS verificado;
  C  cliente (br_bridge): sem as variáveis (ou com elas inválidas) cada transporte chama o
     executor com exatamente os argumentos de antes; ligado, INCRA vai pela ponte, SICAR tenta
     direto e usa a ponte só com prazo, janela, cancelamento, tempo total que não cresce; curl
     real lendo os cabeçalhos pela entrada padrão; lista de hosts igual nos dois lados e igual
     aos hosts que o código chama;
  M  controles positivos: cada trava desligada por mutação faz a verificação dela falhar.

Uso: PYTHONPATH=. python scripts/ponte_brasil_gate.py
"""
from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("ponte_brasil_app", ROOT / "ponte_brasil" / "app.py")
ponte = importlib.util.module_from_spec(_spec)
sys.modules["ponte_brasil_app"] = ponte
_spec.loader.exec_module(ponte)

import br_bridge  # noqa: E402
import external_process_lifecycle as epl  # noqa: E402

TOKEN = "gate-token-" + "a1b2c3d4" * 6
WRONG = "gate-token-" + "zzzzzzzz" * 6
BRIDGE_BASE = "https://ponte-gate.a.run.app"
BRIDGE_FETCH = BRIDGE_BASE + "/v1/fetch"
ENV_ON = {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: TOKEN}
SICAR_URL = "https://geoserver.car.gov.br/geoserver/sicar/ows?service=WFS&request=GetFeature&typeName=sicar:sicar_imoveis_mg"
SICAR2_URL = "https://geoserver.car.gov.br/geoserver/ows?service=WFS&version=1.0.0&request=GetCapabilities"
INCRA_URL = "https://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_mg&service=WFS"
OTHER_URL = "https://pamgia.ibama.gov.br/server/rest/services/x/FeatureServer/0/query?f=json"
PUBLIC_IP = "200.152.46.60"

FAILURES: list[str] = []
PASSED: list[str] = []


# ---------------------------------------------------------------------------------------------
# fonte falsa local
# ---------------------------------------------------------------------------------------------

UPSTREAM_SEEN: list[dict] = []


class FakeUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return None

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        UPSTREAM_SEEN.append({"method": self.command, "path": self.path,
                              "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        path = self.path.split("?", 1)[0]
        try:
            if path == "/ok":
                self._reply(200, b'{"features": [], "marca": "RESPOSTA-OK"}',
                            [("Content-Type", "application/json"), ("Set-Cookie", "sessao=1"), ("X-Interno", "segredo")])
            elif path == "/echo":
                self._reply(200, json.dumps({"method": self.command, "headers": UPSTREAM_SEEN[-1]["headers"],
                                             "body_len": len(body)}).encode(), [("Content-Type", "application/json")])
            elif path == "/redir-out":
                self._reply(302, b"", [("Location", "https://evil.example.com/ok")])
            elif path == "/redir-http":
                self._reply(302, b"", [("Location", "http://geoserver.car.gov.br/ok")])
            elif path == "/redir-in":
                self._reply(302, b"", [("Location", "/ok")])
            elif path == "/redir-loop":
                self._reply(302, b"", [("Location", "https://geoserver.car.gov.br/redir-loop")])
            elif path == "/redir-incra":
                self._reply(302, b"", [("Location", "https://acervofundiario.incra.gov.br/ok")])
            elif path.startswith("/size/"):
                self._reply(200, b"x" * int(path.rsplit("/", 1)[1]), [("Content-Type", "text/plain")])
            elif path.startswith("/nolen/"):
                size = int(path.rsplit("/", 1)[1])
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Connection", "close")
                self.end_headers()
                block = b"y" * 65536
                sent = 0
                while sent < size:
                    piece = block[: min(len(block), size - sent)]
                    self.wfile.write(piece)
                    sent += len(piece)
                self.close_connection = True
            elif path == "/slow":
                time.sleep(3.0)
                self._reply(200, b"lento", [("Content-Type", "text/plain")])
            elif path == "/drip":
                self.send_response(200)
                self.send_header("Content-Length", "60")
                self.send_header("Connection", "close")
                self.end_headers()
                for _ in range(60):
                    self.wfile.write(b"z")
                    self.wfile.flush()
                    time.sleep(0.1)
                self.close_connection = True
            elif path == "/truncated":
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"0123456789")
                self.wfile.flush()
                self.close_connection = True
            elif path == "/drip-headers":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-A: ")
                for _ in range(40):
                    self.wfile.write(b"a")
                    self.wfile.flush()
                    time.sleep(0.1)
                self.wfile.write(b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                self.close_connection = True
            elif path == "/status500":
                self._reply(500, b"erro interno da fonte", [("Content-Type", "text/plain")])
            else:
                self._reply(404, b"nao", [("Content-Type", "text/plain")])
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        return None


UPSTREAM = _QuietServer(("127.0.0.1", 0), FakeUpstream)
UP_PORT = UPSTREAM.server_address[1]
threading.Thread(target=UPSTREAM.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()


class PlainPinned(http.client.HTTPConnection):
    """Conector de teste: fala HTTP com a fonte falsa, registrando o IP que a ponte conferiu."""

    def __init__(self, host, ip, timeout):
        super().__init__(host, UP_PORT, timeout=timeout)
        self.pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", UP_PORT), self.timeout)


class PonteEnv:
    def __init__(self, resolver=None, **svc_kwargs):
        self.log = io.StringIO()
        self.connects: list[tuple[str, str]] = []
        self.resolves: list[str] = []
        token = svc_kwargs.pop("token", TOKEN)
        self.svc = ponte.Service(token=token, resolver=resolver or self._resolver, connector=self._connector,
                                 log_stream=self.log, **svc_kwargs)
        self.server = ponte.make_server(("127.0.0.1", 0), self.svc)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def _resolver(self, host, timeout):
        self.resolves.append(host)
        return [PUBLIC_IP]

    def _connector(self, host, ip, timeout):
        self.connects.append((host, ip))
        return PlainPinned(host, ip, timeout)

    def call(self, method="GET", url="https://geoserver.car.gov.br/ok", token=TOKEN, headers=None, body=None,
             path="/v1/fetch", chunked=False, timeout=15):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_accept_encoding=True)
            if token is not None:
                conn.putheader("X-Ponte-Token", token)
            if url is not None:
                conn.putheader("X-Ponte-Url", url)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            if chunked:
                conn.putheader("Transfer-Encoding", "chunked")
                conn.endheaders(b"5\r\nhello\r\n0\r\n\r\n")
            elif body is not None:
                conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body)
            else:
                conn.endheaders()
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        finally:
            conn.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def refused(result, status, code):
    st, hdrs, _ = result
    assert st == status and hdrs.get("x-ponte-error") == code, ("esperado", status, code, "veio", st, hdrs.get("x-ponte-error"))


# ---------------------------------------------------------------------------------------------
# S · servidor
# ---------------------------------------------------------------------------------------------

def s_token():
    with PonteEnv() as e:
        refused(e.call(token=None), 401, "unauthorized")
        refused(e.call(token=WRONG), 401, "unauthorized")
        refused(e.call(token=TOKEN[:-1]), 401, "unauthorized")
        refused(e.call(token=TOKEN + "x"), 401, "unauthorized")
        assert e.connects == [] and e.resolves == [], "pedido sem token chegou à fonte"
        st, hdrs, body = e.call()
        assert st == 200 and b"RESPOSTA-OK" in body and hdrs.get("x-ponte-origin") == "upstream", (st, hdrs, body)


def s_not_configured():
    for token in ("", "curto-demais"):
        with PonteEnv(token=token) as e:
            refused(e.call(token=token or TOKEN), 503, "not_configured")
            refused(e.call(token=TOKEN), 503, "not_configured")
            refused(e.call(path="/healthz", token=None, url=None), 503, "not_configured")
            assert e.connects == []


def s_health_and_paths():
    with PonteEnv() as e:
        st, _, body = e.call(path="/healthz", token=None, url=None)
        assert st == 200 and body == b"ok\n", (st, body)
        refused(e.call(method="POST", path="/healthz", token=None, url=None, body=b""), 405, "method_not_allowed")
        refused(e.call(path="/qualquer", token=TOKEN), 404, "not_found")


def s_scheme():
    with PonteEnv() as e:
        refused(e.call(url="http://geoserver.car.gov.br/ok"), 403, "scheme_not_https")
        refused(e.call(url="ftp://geoserver.car.gov.br/ok"), 403, "scheme_not_https")
        refused(e.call(url=None), 400, "target_missing")
        assert e.connects == []


def s_host_list():
    with PonteEnv() as e:
        for url in ("https://evil.example.com/ok", "https://car.gov.br/ok", "https://geoserver.car.gov.br.evil.com/ok",
                    "https://sigef.incra.gov.br/ok", "https://consulta.car.gov.br/ok", "https://metadata.google.internal/"):
            refused(e.call(url=url), 403, "host_not_allowed")
        assert e.connects == [] and e.resolves == []


def s_ip_literal():
    with PonteEnv() as e:
        for url in ("https://200.152.46.60/ok", "https://127.0.0.1/ok", "https://2130706433/ok",
                    "https://0x7f000001/ok", "https://169.254.169.254/computeMetadata/v1/"):
            refused(e.call(url=url), 403, "ip_literal_refused")
        refused(e.call(url="https://[::1]/ok"), 403, "ip_literal_refused")
        assert e.connects == []


def s_userinfo():
    with PonteEnv() as e:
        refused(e.call(url="https://user@geoserver.car.gov.br/ok"), 403, "userinfo_refused")
        refused(e.call(url="https://user:senha@acervofundiario.incra.gov.br/ok"), 403, "userinfo_refused")
        refused(e.call(url="https://geoserver.car.gov.br@evil.example.com/ok"), 403, "userinfo_refused")
        assert e.connects == []


def s_port():
    with PonteEnv() as e:
        refused(e.call(url="https://geoserver.car.gov.br:8443/ok"), 403, "port_refused")
        refused(e.call(url="https://geoserver.car.gov.br:80/ok"), 403, "port_refused")
        assert e.connects == []
        st, _, _ = e.call(url="https://geoserver.car.gov.br:443/ok")
        assert st == 200, st


def s_canonical():
    with PonteEnv() as e:
        refused(e.call(url="https://GEOSERVER.car.gov.br/ok"), 403, "host_not_canonical")
        refused(e.call(url="https://geoserver.car.gov.br./ok"), 403, "host_not_canonical")
        assert e.connects == []


def s_private_dns():
    for ips in (["10.0.0.5"], ["127.0.0.1"], ["169.254.169.254"], ["::1"], ["::ffff:10.0.0.1"], ["fd00::1"],
                ["100.64.0.1"], ["0.0.0.0"], ["fe80::1%eth0"], [PUBLIC_IP, "192.168.0.10"]):
        with PonteEnv(resolver=lambda host, timeout, ips=ips: list(ips)) as e:
            refused(e.call(), 403, "resolved_private_ip")
            assert e.connects == [], ("conectou apesar do IP privado", ips)
    with PonteEnv() as e:
        st, _, _ = e.call()
        assert st == 200 and e.connects == [("geoserver.car.gov.br", PUBLIC_IP)], ("conexão não presa ao IP conferido", e.connects)


def s_redirects():
    with PonteEnv() as e:
        refused(e.call(url="https://geoserver.car.gov.br/redir-out"), 502, "redirect_blocked")
        refused(e.call(url="https://geoserver.car.gov.br/redir-http"), 502, "redirect_blocked")
        st, _, body = e.call(url="https://geoserver.car.gov.br/redir-in")
        assert st == 200 and b"RESPOSTA-OK" in body, (st, body)
        before = len(UPSTREAM_SEEN)
        refused(e.call(url="https://geoserver.car.gov.br/redir-loop"), 502, "too_many_redirects")
        loops = [x for x in UPSTREAM_SEEN[before:] if x["path"] == "/redir-loop"]
        assert len(loops) == ponte.MAX_REDIRECTS + 1 == 4, len(loops)

    def resolver(host, timeout):
        return ["10.1.2.3"] if host == "acervofundiario.incra.gov.br" else [PUBLIC_IP]

    with PonteEnv(resolver=resolver) as e:
        refused(e.call(url="https://geoserver.car.gov.br/redir-incra"), 403, "resolved_private_ip")


def s_response_size():
    with PonteEnv(max_response_bytes=1000) as e:
        refused(e.call(url="https://geoserver.car.gov.br/size/1001"), 502, "response_too_large")
        refused(e.call(url="https://geoserver.car.gov.br/nolen/1001"), 502, "response_too_large")
        st, _, body = e.call(url="https://geoserver.car.gov.br/size/1000")
        assert st == 200 and len(body) == 1000, (st, len(body))
        assert e.svc.budget.used == 0, ("memória não liberada", e.svc.budget.used)
    limit = ponte.MAX_RESPONSE_BYTES
    with PonteEnv() as e:  # limite de produção (25 MiB), não só o reduzido
        assert e.svc.max_response_bytes == limit == 25 * 1024 * 1024
        refused(e.call(url=f"https://geoserver.car.gov.br/size/{limit + 1}", timeout=60), 502, "response_too_large")
        refused(e.call(url=f"https://geoserver.car.gov.br/nolen/{limit + 1}", timeout=60), 502, "response_too_large")
        assert e.svc.budget.used == 0, e.svc.budget.used


def s_incomplete():
    with PonteEnv() as e:
        refused(e.call(url="https://geoserver.car.gov.br/truncated"), 502, "upstream_incomplete")
        assert e.svc.budget.used == 0


def s_methods():
    with PonteEnv() as e:
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"):
            refused(e.call(method=method), 405, "method_not_allowed")
        st, hdrs, _ = e.call(method="HEAD")
        assert st == 405 and hdrs.get("x-ponte-error") == "method_not_allowed", (st, hdrs)
        assert e.connects == []
        st, _, body = e.call(method="POST", url="https://geoserver.car.gov.br/echo", body=b"<Filter/>",
                             headers={"Content-Type": "text/xml"})
        seen = json.loads(body)
        assert st == 200 and seen["method"] == "POST" and seen["body_len"] == 9, (st, seen)


def s_request_body():
    with PonteEnv(max_request_bytes=100) as e:
        refused(e.call(method="POST", body=b"x" * 101), 413, "request_too_large")
        refused(e.call(method="POST", chunked=True), 411, "length_required")
        refused(e.call(method="POST"), 411, "length_required")
        refused(e.call(method="GET", body=b"12345"), 400, "get_with_body")
        assert e.connects == []
        st, _, _ = e.call(method="POST", url="https://geoserver.car.gov.br/echo", body=b"x" * 100)
        assert st == 200, st
    assert ponte.Service().max_request_bytes == ponte.MAX_REQUEST_BYTES == 1024 * 1024


def s_timeout():
    with PonteEnv(upstream_timeout_s=1.0) as e:
        for path in ("/slow", "/drip", "/drip-headers"):
            t0 = time.monotonic()
            refused(e.call(url="https://geoserver.car.gov.br" + path), 504, "upstream_timeout")
            took = time.monotonic() - t0
            assert took < 2.5, (path, took)
    with PonteEnv() as e:  # o cliente pede menos que os 25 s
        t0 = time.monotonic()
        refused(e.call(url="https://geoserver.car.gov.br/slow", headers={"X-Ponte-Timeout": "1"}), 504, "upstream_timeout")
        assert time.monotonic() - t0 < 2.5
    assert ponte.Service().upstream_timeout_s == ponte.UPSTREAM_TIMEOUT_S == 25.0


def s_headers():
    with PonteEnv() as e:
        st, hdrs, body = e.call(url="https://geoserver.car.gov.br/echo", headers={
            "Cookie": "sessao=cliente", "Authorization": "Bearer cliente", "X-Forwarded-For": "1.2.3.4",
            "Referer": "https://raioxterritorial.com.br/", "Origin": "https://raioxterritorial.com.br",
            "Accept": "application/json", "User-Agent": "Raio-X-Territorial/gate"})
        seen = json.loads(body)["headers"]
        assert st == 200
        allowed = {"host", "user-agent", "accept", "accept-encoding", "connection"}
        assert set(seen) <= allowed, ("cabeçalho do cliente chegou à fonte", sorted(set(seen) - allowed))
        assert seen["user-agent"] == "Raio-X-Territorial/gate" and seen["accept"] == "application/json"
        assert seen["host"].split(":")[0] == "geoserver.car.gov.br" and seen["accept-encoding"] == "identity"
        assert all(TOKEN not in v for v in seen.values()), "token repassado à fonte"
        st, hdrs, _ = e.call(url="https://geoserver.car.gov.br/ok")
        assert st == 200 and "set-cookie" not in hdrs and "x-interno" not in hdrs, hdrs
        assert hdrs.get("content-type") == "application/json", hdrs


def s_rate_limit():
    with PonteEnv(bucket=ponte.TokenBucket(0.0, 3), unauth_bucket=ponte.TokenBucket(0.0, 2)) as e:
        for _ in range(3):
            assert e.call()[0] == 200
        refused(e.call(), 429, "rate_limited")
        refused(e.call(token=WRONG), 401, "unauthorized")
        refused(e.call(token=WRONG), 401, "unauthorized")
        refused(e.call(token=WRONG), 429, "rate_limited")
    assert (ponte.RATE_PER_S, ponte.RATE_BURST, ponte.UNAUTH_RATE_PER_S, ponte.UNAUTH_BURST) == (20.0, 40, 5.0, 10)


def s_log_hygiene():
    with PonteEnv() as e:
        e.call(url="https://geoserver.car.gov.br/echo?q=MARCA-CONSULTA", method="POST", body=b"SEGREDO-CORPO")
        e.call(url="https://geoserver.car.gov.br/ok?q=MARCA-CONSULTA")
        e.call(token=WRONG)
        e.call(url="https://evil.example.com/MARCA-CONSULTA")
        text = e.log.getvalue()
        assert text.count('"event": "req"') == 4, text
        for secret in (TOKEN, WRONG, "SEGREDO-CORPO", "MARCA-CONSULTA", "RESPOSTA-OK"):
            assert secret not in text, ("log vazou", secret)


def s_upstream_status():
    with PonteEnv() as e:
        st, hdrs, body = e.call(url="https://geoserver.car.gov.br/status500")
        assert st == 500 and "x-ponte-error" not in hdrs and hdrs.get("x-ponte-origin") == "upstream", (st, hdrs)


def s_tls_and_source():
    ctx = ponte.tls_context()
    import ssl
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ponte.Service().connector is ponte.tls_connector and ponte.Service().resolver is ponte.system_resolver
    src = (ROOT / "ponte_brasil" / "app.py").read_text(encoding="utf-8")
    assert "server_hostname=self.host" in src and "create_connection((self.pinned_ip" in src
    assert re.search(r"^(import|from) ", src, re.M) and not re.search(r"^\s*(import|from)\s+(requests|httpx|urllib3|aiohttp)", src, re.M)
    docker = (ROOT / "ponte_brasil" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in docker and "USER 10001" in docker and not re.search(r"^RUN .*pip", docker, re.M)
    assert ponte.UPSTREAM_PORT == 443 and ponte.MAX_REDIRECTS == 3 and ponte.ALLOWED_METHODS == {"GET", "POST"}


# ---------------------------------------------------------------------------------------------
# C · cliente (br_bridge e os transportes)
# ---------------------------------------------------------------------------------------------

def cp(args, rc=0, out=b"{}", err=b""):
    return subprocess.CompletedProcess(list(args), rc, out, err)


class Recorder:
    """Executor falso: registra (args, kwargs) e devolve roteiro; step(args, kwargs) pode avançar o relógio."""

    def __init__(self, *results, clock=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.results = list(results)
        self.clock = clock

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        item = self.results.pop(0) if self.results else (0, 0.0, b"{}", b"")
        if isinstance(item, BaseException):
            raise item
        rc, advance, out, err = item
        if self.clock is not None:
            self.clock.t += advance
        return subprocess.CompletedProcess(list(args), rc, out, err)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@contextlib.contextmanager
def ponte_env(values):
    keys = (br_bridge.ENV_URL, br_bridge.ENV_TOKEN)
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(values)
        br_bridge.reset_state()
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        br_bridge.reset_state()


def _import_transports():
    import deploy_app
    import incra_acervo_f2
    import incra_snci_public_v42
    import sicar_detail_sources
    import sicar_detail_sources_v2
    return deploy_app, incra_acervo_f2, incra_snci_public_v42, sicar_detail_sources, sicar_detail_sources_v2


def legacy_cases():
    """(nome, chamada, alvo do patch, args de antes, kwargs de antes) — copiados da main b5b2069."""
    deploy_app, acervo, snci, sds, sds2 = _import_transports()
    return [
        ("deploy_app._curl SICAR", lambda: deploy_app._curl(SICAR_URL, True), (deploy_app, "run_managed_process"),
         ["curl", "-k", "-sS", "--connect-timeout", "12", "--max-time", "40", "-A", "Raio-X-Territorial/0.14.6", SICAR_URL],
         {"timeout_seconds": 45, "cancel_event": None}),
        ("deploy_app._curl SICAR curto", lambda: deploy_app._curl(SICAR_URL, True, connect_timeout=5, max_time=10, hard_timeout=11),
         (deploy_app, "run_managed_process"),
         ["curl", "-k", "-sS", "--connect-timeout", "5", "--max-time", "10", "-A", "Raio-X-Territorial/0.14.6", SICAR_URL],
         {"timeout_seconds": 11, "cancel_event": None}),
        ("incra_acervo_f2.curl_fetch", lambda: acervo.curl_fetch(INCRA_URL), (epl, "run_managed_process"),
         ["curl", "-sS", "-L", "--fail", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "5",
          "--max-time", "8", "--max-filesize", "30000000", "-A", "Raio-X-Territorial/f2-incra-acervo", INCRA_URL],
         {"timeout_seconds": 10, "cancel_event": None}),
        ("incra_snci_public_v42._curl", lambda: snci._curl(INCRA_URL, 10), (subprocess, "run"),
         ["curl", "-k", "-sS", "-L", "--fail", "--retry", "0", "--connect-timeout", "5", "--max-time", "10", "-A",
          "Raio-X-Territorial/INCRA-SNCI-v42", INCRA_URL],
         {"capture_output": True, "timeout": 14}),
        ("sicar_detail_sources._curl", lambda: sds._curl(SICAR_URL), (subprocess, "run"),
         ["curl", "-k", "-sS", "--connect-timeout", "12", "--max-time", "35", "-A", "Raio-X-Territorial/0.14.10", SICAR_URL],
         {"capture_output": True, "timeout": 40}),
        ("sicar_detail_sources_v2._curl", lambda: sds2._curl(SICAR2_URL), (subprocess, "run"),
         ["curl", "-k", "-sS", "--fail", "--retry", "0", "--connect-timeout", "6", "--max-time", "13", "-A",
          "Raio-X-Territorial/SICAR-v41", SICAR2_URL],
         {"capture_output": True, "timeout": 17}),
    ]


OFF_ENVS = (
    {},
    {br_bridge.ENV_URL: BRIDGE_BASE},
    {br_bridge.ENV_TOKEN: TOKEN},
    {br_bridge.ENV_URL: "http://ponte-gate.a.run.app", br_bridge.ENV_TOKEN: TOKEN},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: "curto"},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: TOKEN[:20] + " " + TOKEN[20:]},
    {br_bridge.ENV_URL: "https://user@ponte-gate.a.run.app", br_bridge.ENV_TOKEN: TOKEN},
)


def c_off_identical():
    for env in OFF_ENVS:
        for name, call, (target, attr), args, kwargs in legacy_cases():
            with ponte_env(env):
                rec = Recorder((7, 0.0, b"", b"curl: (7) Failed to connect"))
                with mock.patch.object(target, attr, rec):
                    call()
                assert len(rec.calls) == 1, (name, env, "chamadas", len(rec.calls))
                got_args, got_kwargs = rec.calls[0]
                assert got_args == args and got_kwargs == kwargs, (name, env, got_args, got_kwargs)
    # ligada, mas host que não é do INCRA/SICAR: igual a antes, sem ponte nem após falha
    deploy_app = _import_transports()[0]
    with ponte_env(ENV_ON):
        rec = Recorder((7, 0.0, b"", b"curl: (7) Failed to connect"))
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            deploy_app._curl(OTHER_URL, True)
        assert len(rec.calls) == 1 and rec.calls[0] == (
            ["curl", "-k", "-sS", "--connect-timeout", "12", "--max-time", "40", "-A", "Raio-X-Territorial/0.14.6", OTHER_URL],
            {"timeout_seconds": 45, "cancel_event": None}), rec.calls


def _bridge_call_ok(call, name, *, url, timeout, max_time):
    args, kwargs = call
    assert args[:3] == ["curl", "-H", "@-"], (name, args)
    assert BRIDGE_FETCH in args and url not in args, (name, args)
    assert all(TOKEN not in a for a in args), (name, "token na linha de comando")
    assert "-k" not in args and "--insecure" not in args, (name, args)
    assert "--fail" in args, (name, args)
    got_timeout = kwargs.get("timeout_seconds", kwargs.get("timeout"))
    assert got_timeout is not None and abs(float(got_timeout) - timeout) < 1e-6, (name, kwargs)
    mt = args[args.index("--max-time") + 1]
    assert abs(float(mt) - max_time) < 0.011, (name, mt, max_time)
    stdin = kwargs.get("input_bytes") or kwargs.get("input")
    assert stdin and f"X-Ponte-Token: {TOKEN}".encode() in stdin and f"X-Ponte-Url: {url}".encode() in stdin, (name, stdin)


def c_incra_bridge():
    _, acervo, snci, _, _ = _import_transports()
    with ponte_env(ENV_ON):
        rec = Recorder((0, 0.0, b"<gml/>", b""))
        with mock.patch.object(epl, "run_managed_process", rec):
            out = acervo.curl_fetch(INCRA_URL)
        assert out["ok"] is True and len(rec.calls) == 1, (out, rec.calls)
        _bridge_call_ok(rec.calls[0], "acervo", url=INCRA_URL, timeout=10, max_time=8)
        args = rec.calls[0][0]
        assert "--proto" in args and "Raio-X-Territorial/f2-incra-acervo" in args
        rec = Recorder((0, 0.0, b"<xml/>", b""))
        with mock.patch.object(subprocess, "run", rec):
            snci._curl(INCRA_URL, 10)
        assert len(rec.calls) == 1, rec.calls
        _bridge_call_ok(rec.calls[0], "snci", url=INCRA_URL, timeout=14, max_time=10)
        assert rec.calls[0][1]["capture_output"] is True
        # ponte recusou (token errado): falha, nunca dado
        rec = Recorder((22, 0.0, b"", b"curl: (22) The requested URL returned error: 401"))
        with mock.patch.object(epl, "run_managed_process", rec):
            out = acervo.curl_fetch(INCRA_URL)
        assert out["ok"] is False and len(rec.calls) == 1, out


def c_sicar_direct_then_bridge():
    deploy_app = _import_transports()[0]
    legacy = ["curl", "-k", "-sS", "--connect-timeout", "12", "--max-time", "40", "-A", "Raio-X-Territorial/0.14.6", SICAR_URL]
    clock = FakeClock()
    with ponte_env(ENV_ON), mock.patch.object(br_bridge, "_now", clock):
        # direto responde: nada de ponte
        rec = Recorder((0, 0.4, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        assert out["ok"] and len(rec.calls) == 1 and rec.calls[0] == (legacy, {"timeout_seconds": 45, "cancel_event": None}), rec.calls
        # direto cai na conexão (12 s): a mesma chamada vai pela ponte com o que sobra (33 s)
        rec = Recorder((7, 12.0, b"", b"curl: (7) Failed to connect"), (0, 1.0, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        assert out["ok"] and len(rec.calls) == 2, rec.calls
        assert rec.calls[0] == (legacy, {"timeout_seconds": 45, "cancel_event": None}), rec.calls[0]
        _bridge_call_ok(rec.calls[1], "sicar fallback", url=SICAR_URL, timeout=33.0, max_time=32.75)
        assert 12.0 + rec.calls[1][1]["timeout_seconds"] <= 45 + 1e-9, "tempo total cresceu"
        # janela aberta: vai direto para a ponte, prazo inteiro da chamada (comportamento antes do estado)
        clock.t += 5
        rec = Recorder((0, 1.0, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            deploy_app._curl(SICAR_URL, True)
        assert len(rec.calls) == 1, rec.calls
        _bridge_call_ok(rec.calls[0], "sicar janela", url=SICAR_URL, timeout=45, max_time=40)
        # janela vencida: tenta direto de novo
        clock.t += br_bridge.DIRECT_SKIP_WINDOW_S + 1
        rec = Recorder((0, 0.3, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            deploy_app._curl(SICAR_URL, True)
        assert len(rec.calls) == 1 and rec.calls[0][0] == legacy and "input_bytes" not in rec.calls[0][1], rec.calls


def c_sicar_budget_and_codes():
    _, _, _, _, sds2 = _import_transports()
    clock = FakeClock()
    with ponte_env(ENV_ON), mock.patch.object(br_bridge, "_now", clock):
        # sobra 2 s de 17: não chama a ponte
        rec = Recorder((28, 15.0, b"", b"curl: (28) Operation timed out"), clock=clock)
        with mock.patch.object(subprocess, "run", rec):
            out = sds2._curl(SICAR2_URL, False)
        assert out["ok"] is False and len(rec.calls) == 1, rec.calls
        # --fail com 403: ponte com o que sobra (16 s; --max-time 13 cabe)
        br_bridge.reset_state()
        rec = Recorder((22, 1.0, b"", b"curl: (22) The requested URL returned error: 403"), (0, 1.0, b"<xml/>", b""), clock=clock)
        with mock.patch.object(subprocess, "run", rec):
            out = sds2._curl(SICAR2_URL, False)
        assert out["ok"] is True and len(rec.calls) == 2, rec.calls
        _bridge_call_ok(rec.calls[1], "sicar v2 403", url=SICAR2_URL, timeout=16.0, max_time=13)
        # 404 é resposta da fonte: sem ponte
        br_bridge.reset_state()
        rec = Recorder((22, 1.0, b"", b"curl: (22) The requested URL returned error: 404"), clock=clock)
        with mock.patch.object(subprocess, "run", rec):
            sds2._curl(SICAR2_URL, False)
        assert len(rec.calls) == 1, rec.calls
        # ponte falha dentro da janela: a janela fecha
        br_bridge.reset_state()
        rec = Recorder((7, 1.0, b"", b""), (0, 1.0, b"<xml/>", b""), (22, 1.0, b"", b"curl: (22) The requested URL returned error: 502"),
                       (0, 0.5, b"<xml/>", b""), clock=clock)
        with mock.patch.object(subprocess, "run", rec):
            sds2._curl(SICAR2_URL, False)
            assert br_bridge.direct_skipped("geoserver.car.gov.br")
            sds2._curl(SICAR2_URL, False)
            assert not br_bridge.direct_skipped("geoserver.car.gov.br"), "janela não fechou com a ponte em falha"
            sds2._curl(SICAR2_URL, False)
        assert len(rec.calls) == 4 and "input" not in rec.calls[3][1], rec.calls[3]


def c_cancel_and_timeout():
    deploy_app = _import_transports()[0]
    clock = FakeClock()
    with ponte_env(ENV_ON), mock.patch.object(br_bridge, "_now", clock):
        rec = Recorder(epl.ManagedProcessCancelled("operation_cancelled"), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        assert out.get("cancelled") is True and len(rec.calls) == 1, (out, rec.calls)
        rec = Recorder(subprocess.TimeoutExpired(["curl"], 45), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        assert out.get("timed_out") is True and len(rec.calls) == 1, (out, rec.calls)
        event = threading.Event()

        def cancel_during(args, **kwargs):
            event.set()
            clock.t += 1
            return subprocess.CompletedProcess(args, 7, b"", b"")

        calls = []
        with mock.patch.object(deploy_app, "run_managed_process", lambda a, **k: calls.append(a) or cancel_during(a, **k)):
            deploy_app._curl(SICAR_URL, True, cancel_event=event)
        assert len(calls) == 1, "chamou a ponte depois do cancelamento"


def c_config():
    cases = {
        "https://ponte-abc.a.run.app": "https://ponte-abc.a.run.app/v1/fetch",
        "https://ponte-abc.a.run.app/": "https://ponte-abc.a.run.app/v1/fetch",
        "https://ponte-abc.a.run.app/v1/fetch": "https://ponte-abc.a.run.app/v1/fetch",
    }
    for raw, want in cases.items():
        cfg = br_bridge.config({br_bridge.ENV_URL: raw, br_bridge.ENV_TOKEN: TOKEN})
        assert cfg is not None and cfg.fetch_url == want, (raw, cfg)
    for env in OFF_ENVS:
        assert br_bridge.config(env) is None, env
    line = br_bridge.status_line(ENV_ON)
    assert line.startswith("RX_PONTE_BRASIL=on") and TOKEN not in line, line
    assert br_bridge.status_line({}) == "RX_PONTE_BRASIL=off"
    assert br_bridge.route_for(INCRA_URL, ENV_ON) == "bridge"
    assert br_bridge.route_for(SICAR_URL, ENV_ON) == "direct_then_bridge"
    assert br_bridge.route_for(OTHER_URL, ENV_ON) == "direct"
    assert br_bridge.route_for(INCRA_URL, {}) == "direct"
    for url in ("http://acervofundiario.incra.gov.br/x", "https://acervofundiario.incra.gov.br:8443/x",
                "https://u@acervofundiario.incra.gov.br/x", "https://ACERVOFUNDIARIO.incra.gov.br/x"):
        assert br_bridge.route_for(url, ENV_ON) == "direct", url


def c_real_curl():
    curl = shutil.which("curl")
    assert curl, "curl ausente: o CI precisa dele (os transportes do portal usam curl)"
    target = "https://acervofundiario.incra.gov.br/ok?tema=x"
    base_args = ["curl", "-sS", "--fail", "--connect-timeout", "5", "--max-time", "8", "-A", "Raio-X-Territorial/gate-curl", target]
    with PonteEnv() as e:
        for token, runner_name in ((TOKEN, "managed"), (TOKEN, "subprocess"), (WRONG, "managed")):
            cfg = br_bridge.config({br_bridge.ENV_URL: f"https://127.0.0.1:{e.port}", br_bridge.ENV_TOKEN: token})
            args, stdin = br_bridge.bridge_request(base_args, cfg)
            assert all(token not in a for a in args)
            # só o transporte do teste troca https por http (a ponte local não tem certificado)
            args = [a.replace(f"https://127.0.0.1:{e.port}", f"http://127.0.0.1:{e.port}") for a in args]
            before = len(UPSTREAM_SEEN)
            if runner_name == "managed":
                proc = epl.run_managed_process(args, timeout_seconds=20, input_bytes=stdin)
            else:
                proc = br_bridge.subprocess_runner(args, timeout_seconds=20, input_bytes=stdin)
            if token == TOKEN:
                assert proc.returncode == 0 and b"RESPOSTA-OK" in proc.stdout, (proc.returncode, proc.stderr[:200])
                seen = UPSTREAM_SEEN[before:]
                assert len(seen) == 1 and seen[0]["path"] == "/ok?tema=x", seen
                assert seen[0]["headers"]["user-agent"] == "Raio-X-Territorial/gate-curl", seen[0]["headers"]
                assert "x-ponte-token" not in seen[0]["headers"]
            else:
                assert proc.returncode == 22 and proc.stdout == b"" and len(UPSTREAM_SEEN) == before, (proc.returncode, proc.stdout)
        assert epl.active_child_pids() == [], "processo curl pendurado"


HOST_RE = re.compile(r"https?://([a-z0-9.-]+\.(?:incra|car)\.gov\.br)", re.I)
BROWSER_ONLY = {"consulta.car.gov.br": "portal_map_v46.py"}
TRANSPORTS = ("deploy_app.py", "incra_acervo_f2.py", "incra_snci_public_v42.py", "sicar_detail_sources.py",
              "sicar_detail_sources_v2.py")
# httpx direto ao SICAR, mas a rota foi substituída no arranque por módulo que usa deploy_app._curl
SUPERSEDED = {
    "portal_api.py": ("portal_sicar_resilient.py", "'/v1/live/sicar/viewport','/v1/live/resolve'"),
    "portal_v8.py": ("portal_sicar_resilient.py", "'/v1/live/sicar/viewport','/v1/live/resolve'"),
    "portal_advanced_search_v39.py": ("portal_cafir_inverse_v44.py", "!='/v1/live/search/advanced'"),
}
SKIP_DIRS = {"tests", "scripts", ".github", "benchmark", "ponte_brasil", "static", "data", ".git"}


def c_hosts_and_transports():
    assert ponte.ALLOWED_HOSTS == br_bridge.ROUTED_HOSTS, (ponte.ALLOWED_HOSTS, br_bridge.ROUTED_HOSTS)
    found: dict[str, set[str]] = {}
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for host in HOST_RE.findall(text):
            found.setdefault(host.lower(), set()).add(path.name)
    for host, files in found.items():
        if host in BROWSER_ONLY:
            assert files == {BROWSER_ONLY[host]}, (host, files)
            src = (ROOT / BROWSER_ONLY[host]).read_text(encoding="utf-8")
            assert f"window.open('https://{host}/'" in src, "consulta.car.gov.br deixou de ser só link do navegador"
            continue
        assert host in ponte.ALLOWED_HOSTS, ("host oficial fora da lista da ponte", host, files)
    assert set(ponte.ALLOWED_HOSTS) <= set(found), ("host na lista que o código não chama", set(ponte.ALLOWED_HOSTS) - set(found))
    for name in TRANSPORTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "br_bridge.run_curl(" in text, (name, "transporte sem br_bridge")
        assert "subprocess.run(" not in text and "run_managed_process(" not in text, (name, "curl fora do br_bridge")
    consumers = set()
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        uses_host = any(h in text for h in ponte.ALLOWED_HOSTS) or re.search(r"\bbase\.SICAR\b|from deploy_app import[^\n]*\bSICAR\b", text)
        direct_http = re.search(r"subprocess\.run\(|run_managed_process\(|httpx\.|urlopen\(|requests\.(get|post)\(", text)
        if uses_host and direct_http:
            consumers.add(path.name)
    unknown = consumers - set(TRANSPORTS) - set(SUPERSEDED) - {"br_bridge.py"}
    assert not unknown, ("transporte novo para INCRA/SICAR sem br_bridge", sorted(unknown))
    for name, (replacer, marker) in SUPERSEDED.items():
        assert marker in (ROOT / replacer).read_text(encoding="utf-8"), (name, "rota antiga voltou a valer", replacer)
    wf = (ROOT / ".github" / "workflows" / "quality-gate.yml").read_text(encoding="utf-8")
    assert "scripts/ponte_brasil_gate.py" in wf, "gate fora do CI"


# ---------------------------------------------------------------------------------------------
# M · controles positivos
# ---------------------------------------------------------------------------------------------

def _open_repinned(svc, host, ips, deadline):
    conn = svc.connector(host, host, ponte._remaining(deadline, svc.clock))
    conn.connect()
    return conn


def _bridge_request_token_in_argv(args, cfg, *, budget_s=None):
    out, stdin = _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=budget_s)
    return out + ["-H", f"X-Ponte-Token: {cfg.token}"], stdin


def _bridge_request_no_budget(args, cfg, *, budget_s=None):
    return _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=None)


def _bridge_request_no_fail(args, cfg, *, budget_s=None):
    out, stdin = _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=budget_s)
    return [a for a in out if a != "--fail"], stdin


_ORIG_BRIDGE_REQUEST = br_bridge.bridge_request

MUTATIONS = [
    ("token_ok sempre verdadeiro", lambda: mock.patch.object(ponte, "token_ok", lambda *a: True), s_token),
    ("token_configured sempre verdadeiro", lambda: mock.patch.object(ponte, "token_configured", lambda *a: True), s_not_configured),
    ("host_allowed sempre verdadeiro", lambda: mock.patch.object(ponte, "host_allowed", lambda *a: True), s_host_list),
    ("is_ip_literal desligado", lambda: mock.patch.object(ponte, "is_ip_literal", lambda *a: False), s_ip_literal),
    ("has_userinfo desligado", lambda: mock.patch.object(ponte, "has_userinfo", lambda *a: False), s_userinfo),
    ("port_ok sempre verdadeiro", lambda: mock.patch.object(ponte, "port_ok", lambda *a: True), s_port),
    ("host_canonical sempre verdadeiro", lambda: mock.patch.object(ponte, "host_canonical", lambda *a: True), s_canonical),
    ("ip_is_public sempre verdadeiro", lambda: mock.patch.object(ponte, "ip_is_public", lambda *a: True), s_private_dns),
    ("conexão sem IP preso", lambda: mock.patch.object(ponte, "_open", _open_repinned), s_private_dns),
    ("redirect_allowed sempre verdadeiro", lambda: mock.patch.object(ponte, "redirect_allowed", lambda *a: True), s_redirects),
    ("MAX_REDIRECTS ignorado", lambda: mock.patch.object(ponte.Service, "__init__", _service_init_redirects), s_redirects),
    ("body_within_limit sempre verdadeiro", lambda: mock.patch.object(ponte, "body_within_limit", lambda *a: True), s_response_size),
    ("body_complete sempre verdadeiro", lambda: mock.patch.object(ponte, "body_complete", lambda resp: True), s_incomplete),
    ("vigia do prazo desligado", lambda: mock.patch.object(ponte, "_expire", lambda conn, expired: None), s_timeout),
    ("method_allowed sempre verdadeiro", lambda: mock.patch.object(ponte, "method_allowed", lambda *a: True), s_methods),
    ("request_within_limit sempre verdadeiro", lambda: mock.patch.object(ponte, "request_within_limit", lambda *a: True), s_request_body),
    ("prazo ignorado", lambda: mock.patch.object(ponte, "_remaining", lambda deadline, clock: 30.0), s_timeout),
    ("cabeçalhos do cliente repassados", lambda: mock.patch.object(ponte, "upstream_headers",
                                                                     lambda ch, hb: {k: v for k, v in ch.items()}), s_headers),
    ("cabeçalhos da fonte devolvidos", lambda: mock.patch.object(ponte, "response_headers", lambda resp: [(k, v) for k, v in resp.getheaders()
                                                       if k.lower() not in ("content-length", "connection")]), s_headers),
    ("limite de taxa desligado", lambda: mock.patch.object(ponte.TokenBucket, "allow", lambda self: True), s_rate_limit),
    ("log com token", lambda: _patch_log(), s_log_hygiene),
    ("cliente sempre ligado", lambda: mock.patch.object(br_bridge, "config",
                                                        lambda env=None: br_bridge.Config(BRIDGE_FETCH, TOKEN, "x")), c_off_identical),
    ("cliente roteia qualquer host", lambda: mock.patch.object(br_bridge, "routed_host",
                                                               lambda url: urlsplit(url).hostname), c_off_identical),
    ("INCRA direto primeiro", lambda: mock.patch.object(br_bridge, "INCRA_HOSTS", frozenset()), c_incra_bridge),
    ("-k mantido na ponte", lambda: mock.patch.object(br_bridge, "_INSECURE_OPTS", ()), c_incra_bridge),
    ("token na linha de comando", lambda: mock.patch.object(br_bridge, "bridge_request", _bridge_request_token_in_argv), c_incra_bridge),
    ("sem --fail pela ponte", lambda: mock.patch.object(br_bridge, "bridge_request", _bridge_request_no_fail), c_incra_bridge),
    ("SICAR sem ponte após queda", lambda: mock.patch.object(br_bridge, "should_fallback", lambda proc: False), c_sicar_direct_then_bridge),
    ("SICAR sem janela", lambda: mock.patch.object(br_bridge, "direct_skipped", lambda host: False), c_sicar_direct_then_bridge),
    ("ponte com o prazo inteiro", lambda: mock.patch.object(br_bridge, "remaining_budget", lambda t, s: t), c_sicar_direct_then_bridge),
    ("--max-time sem o prazo que sobra", lambda: mock.patch.object(br_bridge, "bridge_request", _bridge_request_no_budget),
     c_sicar_direct_then_bridge),
    ("ponte sem prazo mínimo", lambda: mock.patch.object(br_bridge, "MIN_BRIDGE_BUDGET_S", 0.0), c_sicar_budget_and_codes),
    ("404 vira queda", lambda: mock.patch.object(br_bridge, "FALLBACK_HTTP_STATUS", frozenset({403, 404})), c_sicar_budget_and_codes),
]

_ORIG_SERVICE_INIT = ponte.Service.__init__


def _service_init_redirects(self, *args, **kwargs):
    _ORIG_SERVICE_INIT(self, *args, **kwargs)
    self.max_redirects = 50


@contextlib.contextmanager
def _patch_log():
    orig = ponte.Service.log

    def leaky(self, **fields):
        fields["token"] = self.token
        return orig(self, **fields)

    with mock.patch.object(ponte.Service, "log", leaky):
        yield


def run(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"PONTE_OK {name}", flush=True)
    except Exception as exc:  # erro inesperado também é falha da verificação
        FAILURES.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"PONTE_FALHOU {name}: {type(exc).__name__}: {str(exc)[:600]}", flush=True)


def run_mutations():
    survived = []
    for label, patcher, check in MUTATIONS:
        br_bridge.reset_state()
        try:
            with patcher():
                check()
        except AssertionError as exc:  # o motivo aparece: controle positivo tem que cair pela trava certa
            print(f"PONTE_MUTANTE_MORTO {label} ({check.__name__}): {str(exc)[:160]}", flush=True)
            continue
        except Exception as exc:  # erro inesperado não conta como controle positivo
            survived.append(f"{label}: erro {type(exc).__name__}: {exc}")
            print(f"PONTE_MUTANTE_ERRO {label}: {type(exc).__name__}: {exc}", flush=True)
            continue
        survived.append(label)
        print(f"PONTE_MUTANTE_SOBREVIVEU {label} ({check.__name__})", flush=True)
    return survived


def main() -> int:
    checks = [
        s_token, s_not_configured, s_health_and_paths, s_scheme, s_host_list, s_ip_literal, s_userinfo, s_port,
        s_canonical, s_private_dns, s_redirects, s_response_size, s_incomplete, s_methods, s_request_body, s_timeout, s_headers,
        s_rate_limit, s_log_hygiene, s_upstream_status, s_tls_and_source,
        c_off_identical, c_incra_bridge, c_sicar_direct_then_bridge, c_sicar_budget_and_codes, c_cancel_and_timeout,
        c_config, c_real_curl, c_hosts_and_transports,
    ]
    for fn in checks:
        br_bridge.reset_state()
        run(fn.__name__, fn)
    ran = not FAILURES
    survived = run_mutations() if ran else ["(mutações não rodaram: há verificação falhando)"]
    UPSTREAM.shutdown()
    UPSTREAM.server_close()
    killed = f"{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)}" if ran else "nao_rodaram"
    print(f"RX_PONTE_BRASIL_GATE checks={len(PASSED)}/{len(checks)} mutants_killed={killed}", flush=True)
    if FAILURES or survived:
        for line in FAILURES:
            print("FALHA:", line[:800], flush=True)
        for line in survived:
            print("MUTANTE VIVO:", line, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

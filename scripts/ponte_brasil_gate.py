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
  I  ponte fechada pelo Google (15/09/2026): chave da conta de serviço (JSON ou base64) lida e conferida,
     chave inválida desliga a ponte (nunca meio ligada), assinatura RS256 em Python puro idêntica byte a
     byte à da biblioteca cryptography e verificada pela chave pública, JWT com target_audience, token de
     identidade no cabeçalho X-Serverless-Authorization só pela entrada padrão, cache (uma troca por hora),
     espera limitada ao prazo da consulta, falha da credencial vira consulta pendente (nunca chamada sem
     credencial), log e resultado sem chave/token; conferência do Cloud Shell (scripts/ponte_brasil_conferir.py);
  G  guia (docs/PONTE_BRASIL_ATIVACAO.md): blocos bash com sintaxe válida, ponte fechada, PAROU em falha,
     segredo nunca impresso fora da caixa do Render; CODIGO_REVISADO igual aos hashes do git do código; cada
     bloco rodado como o dono cola, com gcloud/git/python3/openssl/cloudshell falsos: trava de projeto (AFP,
     outro, Firebase, nenhum), --project em toda chamada, papel/recurso/membro de cada permissão, publicação
     fechada com conta sem papel, umask 077 na chave, conferência antes de apagar chaves, aviso de token novo;
  M  controles positivos: cada trava desligada por mutação faz a verificação dela falhar.

Uso: PYTHONPATH=. python scripts/ponte_brasil_gate.py
"""
from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
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
import tempfile
import threading
import time
import types
import urllib.error
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
            elif path == "/redir-post":
                self._reply(302, b"", [("Location", "/echo")])
            elif path == "/redir-307":
                self._reply(307, b"", [("Location", "/echo")])
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
    def __init__(self, resolver=None, connector=None, **svc_kwargs):
        self.log = io.StringIO()
        self.connects: list[tuple[str, str]] = []
        self.resolves: list[str] = []
        token = svc_kwargs.pop("token", TOKEN)
        self.svc = ponte.Service(token=token, resolver=resolver or self._resolver, connector=connector or self._connector,
                                 log_stream=self.log, **svc_kwargs)
        self.server = ponte.make_server(("127.0.0.1", 0), self.svc)
        self.port = self.server.server_address[1]
        self.answered = 0
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
            self.answered += 1
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        finally:
            conn.close()

    def settle(self, timeout_s=5.0):
        """Espera o servidor terminar cada pedido já respondido. A ponte envia a resposta e só depois, no
        finally do handler, devolve a reserva de memória e grava a linha "req" do log; o cliente recebe a
        resposta antes disso. Ler budget.used ou o log sem esperar é corrida (CI de 15/09: "memória não
        liberada na recusa", 40 = um corpo ainda reservado). A linha do log sai depois da devolução."""
        deadline = time.monotonic() + timeout_s
        while self.log.getvalue().count('"event": "req"') < self.answered:
            if time.monotonic() >= deadline:
                raise AssertionError(("pedido respondido sem terminar no servidor", self.answered,
                                      self.log.getvalue().count('"event": "req"')))
            time.sleep(0.005)

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
            refused(e.call(path=ponte.HEALTH_PATH, token=None, url=None), 503, "not_configured")
            assert e.connects == []


def s_health_and_paths():
    health = ponte.HEALTH_PATH
    # Cloud Run reserva caminhos terminados em "z" (/healthz) e os que começam com /_ah/: nunca chegam ao contêiner.
    assert not health.rstrip("/").endswith("z") and not health.startswith("/_ah/"), health
    with PonteEnv() as e:
        st, _, body = e.call(path=health, token=None, url=None)
        assert st == 200 and body == b"ok\n", (st, body)
        refused(e.call(method="POST", path=health, token=None, url=None, body=b""), 405, "method_not_allowed")
        refused(e.call(path="/qualquer", token=TOKEN), 404, "not_found")
        refused(e.call(path="/healthz", token=TOKEN), 404, "not_found")
    doc = (ROOT / "docs" / "PONTE_BRASIL_ATIVACAO.md").read_text(encoding="utf-8")
    assert f"{{URL}}{health}" in doc and "healthz" not in doc, "guia fora do caminho de saúde da ponte"
    # a conferência do Cloud Shell (que o guia manda rodar) bate no mesmo caminho
    conferir = (ROOT / "scripts" / "ponte_brasil_conferir.py").read_text(encoding="utf-8")
    assert f'"{health}"' in conferir and "healthz" not in conferir, "conferência fora do caminho de saúde da ponte"


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
                ["100.64.0.1"], ["0.0.0.0"], ["fe80::1%eth0"], [PUBLIC_IP, "192.168.0.10"],
                # IPv4 interno embutido em IPv6: mapeado, NAT64, 6to4, Teredo (cliente 192.0.2.45)
                ["::ffff:127.0.0.1"], ["64:ff9b::a9fe:a9fe"], ["2002:a9fe:a9fe::1"],
                ["2001:0:4136:e378:8000:63bf:3fff:fdd2"]):
        with PonteEnv(resolver=lambda host, timeout, ips=ips: list(ips)) as e:
            refused(e.call(), 403, "resolved_private_ip")
            assert e.connects == [], ("conectou apesar do IP privado", ips)
    with PonteEnv() as e:
        st, _, _ = e.call()
        assert st == 200 and e.connects == [("geoserver.car.gov.br", PUBLIC_IP)], ("conexão não presa ao IP conferido", e.connects)
    mapped_public = "::ffff:" + PUBLIC_IP  # IPv4 público mapeado é o próprio IPv4: aceito e preso a ele
    with PonteEnv(resolver=lambda host, timeout: [mapped_public]) as e:
        st, _, _ = e.call()
        assert st == 200 and e.connects == [("geoserver.car.gov.br", mapped_public)], (st, e.connects)


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
        e.settle()
        assert e.svc.budget.used == 0, ("memória não liberada", e.svc.budget.used)
    limit = ponte.MAX_RESPONSE_BYTES
    with PonteEnv() as e:  # limite de produção (25 MiB), não só o reduzido
        assert e.svc.max_response_bytes == limit == 25 * 1024 * 1024
        refused(e.call(url=f"https://geoserver.car.gov.br/size/{limit + 1}", timeout=60), 502, "response_too_large")
        refused(e.call(url=f"https://geoserver.car.gov.br/nolen/{limit + 1}", timeout=60), 502, "response_too_large")
        e.settle()
        assert e.svc.budget.used == 0, e.svc.budget.used


def s_incomplete():
    with PonteEnv() as e:
        refused(e.call(url="https://geoserver.car.gov.br/truncated"), 502, "upstream_incomplete")
        e.settle()
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


class _SlowConnect(PlainPinned):
    """Conexão que só desiste quando o prazo recebido acaba (no máximo 5 s)."""

    def connect(self):
        time.sleep(min(float(self.timeout), 5.0))
        raise socket.timeout("connect")


def _slow_resolver(host, timeout):
    time.sleep(min(float(timeout), 5.0))
    raise ponte.Refused(504, "dns_timeout")


def s_timeout():
    with PonteEnv(upstream_timeout_s=1.0) as e:
        for path in ("/slow", "/drip", "/drip-headers"):
            t0 = time.monotonic()
            refused(e.call(url="https://geoserver.car.gov.br" + path), 504, "upstream_timeout")
            took = time.monotonic() - t0
            assert took < 2.5, (path, took)
    # DNS e conexão acontecem ANTES da vigia do prazo: só o prazo que sobra (passado a cada fase) os limita.
    # No Linux a vigia sozinha já corta a leitura da fonte; sem estes dois casos, ignorar o prazo passava.
    for label, kwargs, code in (("DNS lento", {"resolver": _slow_resolver}, "dns_timeout"),
                                ("conexão lenta", {"connector": lambda host, ip, timeout: _SlowConnect(host, ip, timeout)},
                                 "upstream_timeout")):
        with PonteEnv(upstream_timeout_s=1.0, **kwargs) as e:
            t0 = time.monotonic()
            refused(e.call(), 504, code)
            took = time.monotonic() - t0
            assert took < 2.5, (label, "passou do prazo total", round(took, 2))
    with PonteEnv() as e:  # o cliente pede menos que os 25 s
        t0 = time.monotonic()
        refused(e.call(url="https://geoserver.car.gov.br/slow", headers={"X-Ponte-Timeout": "1"}), 504, "upstream_timeout")
        assert time.monotonic() - t0 < 2.5
    with PonteEnv(upstream_timeout_s=1.0) as e:  # o cliente pede MAIS que o teto: vale o teto
        t0 = time.monotonic()
        refused(e.call(url="https://geoserver.car.gov.br/slow", headers={"X-Ponte-Timeout": "60"}), 504, "upstream_timeout")
        took = time.monotonic() - t0
        assert took < 2.5, ("X-Ponte-Timeout passou do teto", took)
    assert ponte.Service().upstream_timeout_s == ponte.UPSTREAM_TIMEOUT_S == 25.0


def s_inflight_release():
    """20 buscas no MESMO Service (com e sem erro): a vaga de busca simultânea sempre volta."""
    with PonteEnv() as e:
        paths = ["/ok", "/status500", "/redir-out", "/truncated"]
        for i in range(ponte.MAX_INFLIGHT + 4):
            path = paths[i % len(paths)]
            st, hdrs, _ = e.call(url="https://geoserver.car.gov.br" + path)
            assert hdrs.get("x-ponte-error") != "busy", ("vaga de busca não liberada", i, path, st)
        got = [e.svc.inflight.acquire(blocking=False) for _ in range(ponte.MAX_INFLIGHT + 1)]
        for ok in got:
            if ok:
                e.svc.inflight.release()
        assert got.count(True) == ponte.MAX_INFLIGHT, ("vagas livres depois das buscas", got.count(True))
        e.settle()
        assert e.svc.budget.used == 0, e.svc.budget.used


def s_malformed():
    """Entrada malformada com token válido é 400 da ponte, nunca 500 internal."""
    with PonteEnv() as e:
        refused(e.call(url="https://[::1/ok"), 400, "target_malformed")
        refused(e.call(method="POST", url="https://geoserver.car.gov.br/echo", headers={"Content-Length": "²"}),
                400, "bad_content_length")
        assert e.connects == []
        e.settle()
        assert '"status": 500' not in e.log.getvalue(), e.log.getvalue()


def s_content_type():
    """Content-Type do cliente só chega à fonte se for válido; senão vai application/octet-stream."""
    with PonteEnv() as e:
        for bad in ("text/xml;evil=<x>", "text/xml\x01", "a b/c"):
            st, _, body = e.call(method="POST", url="https://geoserver.car.gov.br/echo", body=b"<a/>",
                                 headers={"Content-Type": bad})
            seen = json.loads(body)["headers"]
            assert st == 200 and seen.get("content-type") == "application/octet-stream", (bad, st, seen.get("content-type"))
        st, _, body = e.call(method="POST", url="https://geoserver.car.gov.br/echo", body=b"<a/>",
                             headers={"Content-Type": "text/xml; charset=UTF-8"})
        assert json.loads(body)["headers"].get("content-type") == "text/xml; charset=UTF-8", body


def s_redirect_post():
    """POST que recebe 302 segue como GET sem corpo (como o curl -L); 307 mantém POST e corpo."""
    with PonteEnv() as e:
        st, _, body = e.call(method="POST", url="https://geoserver.car.gov.br/redir-post", body=b"<Filter/>",
                             headers={"Content-Type": "text/xml"})
        seen = json.loads(body)
        assert st == 200 and seen["method"] == "GET" and seen["body_len"] == 0, (st, seen)
        assert "content-type" not in seen["headers"] and "content-length" not in seen["headers"], seen["headers"]
        st, _, body = e.call(method="POST", url="https://geoserver.car.gov.br/redir-307", body=b"<Filter/>",
                             headers={"Content-Type": "text/xml"})
        seen = json.loads(body)
        assert st == 200 and seen["method"] == "POST" and seen["body_len"] == 9, (st, seen)


def _socket_closed_within(sock, seconds, drip=None):
    """True se o servidor fechar a conexão antes de `seconds` (mandando `drip` a cada 0,3 s)."""
    t0 = time.monotonic()
    sock.settimeout(0.3)
    while time.monotonic() - t0 < seconds:
        try:
            if drip:
                sock.sendall(drip)
            if sock.recv(1) == b"":
                return True
        except socket.timeout:
            continue
        except OSError:
            return True
    return False


def s_slow_client():
    """Cliente que pinga um cabeçalho por vez perde a conexão no prazo total, não fica pendurado."""
    with PonteEnv(header_deadline_s=1.0) as e:
        sock = socket.create_connection(("127.0.0.1", e.port), 5)
        try:
            sock.sendall(b"GET /v1/fetch HTTP/1.1\r\nHost: ponte\r\n")
            t0 = time.monotonic()
            closed = _socket_closed_within(sock, 4.0, drip=b"X-A: a\r\n")
            took = time.monotonic() - t0
        finally:
            sock.close()
        assert closed and took < 2.5, ("conexão lenta continuou viva", closed, round(took, 2))
        st, _, _ = e.call()  # pedido normal continua funcionando
        assert st == 200, st
    assert ponte.Service().header_deadline_s == ponte.HEADER_DEADLINE_S == 10.0


def _wait(predicate, seconds=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def s_connection_cap():
    """Acima do teto de conexões abertas a nova conexão fecha na hora; ao liberar, volta a atender."""
    request = b"GET " + ponte.HEALTH_PATH.encode() + b" HTTP/1.1\r\nHost: ponte\r\n\r\n"

    def keepalive():  # conexão atendida (resposta lida) e mantida aberta: ocupa uma vaga
        sock = socket.create_connection(("127.0.0.1", e.port), 5)
        sock.sendall(request)
        data = b""
        while not data.endswith(b"ok\n"):
            chunk = sock.recv(4096)
            assert chunk, ("conexão dentro do teto não foi atendida", data)
            data += chunk
        return sock

    with PonteEnv(max_connections=2) as e:
        held = [keepalive() for _ in range(2)]
        try:
            extra = socket.create_connection(("127.0.0.1", e.port), 5)
            try:
                try:
                    extra.sendall(request)
                except OSError:
                    pass
                assert _socket_closed_within(extra, 2.0), "conexão acima do teto foi atendida"
            finally:
                extra.close()
        finally:
            for sock in held:
                sock.close()
        assert _wait(lambda: e.server._slots._value == 2), "vagas de conexão não voltaram"
        st, _, _ = e.call(path=ponte.HEALTH_PATH, token=None, url=None)
        assert st == 200, st
    assert ponte.Service().max_connections == ponte.MAX_CONNECTIONS == 96


def s_headers():
    with PonteEnv() as e:
        st, hdrs, body = e.call(url="https://geoserver.car.gov.br/echo", headers={
            "Cookie": "sessao=cliente", "Authorization": "Bearer cliente", "X-Forwarded-For": "1.2.3.4",
            "X-Serverless-Authorization": "Bearer identidade.do.google",
            "Referer": "https://raioxterritorial.com.br/", "Origin": "https://raioxterritorial.com.br",
            "Accept": "application/json", "User-Agent": "Raio-X-Territorial/gate"})
        seen = json.loads(body)["headers"]
        assert st == 200
        allowed = {"host", "user-agent", "accept", "accept-encoding", "connection"}
        assert set(seen) <= allowed, ("cabeçalho do cliente chegou à fonte", sorted(set(seen) - allowed))
        assert seen["user-agent"] == "Raio-X-Territorial/gate" and seen["accept"] == "application/json"
        assert seen["host"].split(":")[0] == "geoserver.car.gov.br" and seen["accept-encoding"] == "identity"
        assert all(TOKEN not in v and "identidade" not in v for v in seen.values()), "token repassado à fonte"
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


def s_egress_budget():
    """Teto de saída: no máximo EGRESS_BYTES_PER_HOUR de corpo devolvido por hora (balde); acima, 429 egress_budget."""
    clock = FakeClock()
    body_len = len(b'{"features": [], "marca": "RESPOSTA-OK"}')
    with PonteEnv(egress=ponte.EgressBudget(2 * body_len, clock=clock)) as e:
        assert e.call()[0] == 200 and e.call()[0] == 200
        refused(e.call(), 429, "egress_budget")
        e.settle()
        assert '"error": "egress_budget"' in e.log.getvalue(), ("recusa egress_budget sem linha no log", e.log.getvalue()[-300:])
        clock.t += 1900.0          # pouco mais de meia hora devolve um corpo
        assert e.call()[0] == 200
        refused(e.call(), 429, "egress_budget")
        clock.t += 36000.0         # dez horas não passam do tamanho do balde (dois corpos)
        assert e.call()[0] == 200 and e.call()[0] == 200
        refused(e.call(), 429, "egress_budget")
        e.settle()
        assert e.svc.budget.used == 0, ("memória não liberada na recusa", e.svc.budget.used)
    assert ponte.EGRESS_BYTES_PER_HOUR == 1024 ** 3, ("teto de saída de produção mudou", ponte.EGRESS_BYTES_PER_HOUR)
    assert ponte.Service().egress.capacity == float(ponte.EGRESS_BYTES_PER_HOUR), "Service sem o teto de produção"


def s_settle_after_send():
    """Corrida do CI de 15/09 reproduzida: a devolução da memória atrasada 0,2 s depois do envio da resposta
    não pode derrubar as verificações que leem budget.used e o log (sem settle, caem sempre)."""
    orig = ponte.ByteBudget.release

    def slow_release(self, n):
        time.sleep(0.2)
        return orig(self, n)

    with mock.patch.object(ponte.ByteBudget, "release", slow_release):
        s_egress_budget()
        s_log_hygiene()


def s_log_hygiene():
    with PonteEnv() as e:
        e.call(url="https://geoserver.car.gov.br/echo?q=MARCA-CONSULTA", method="POST", body=b"SEGREDO-CORPO")
        e.call(url="https://geoserver.car.gov.br/ok?q=MARCA-CONSULTA")
        e.call(token=WRONG)
        e.call(url="https://evil.example.com/MARCA-CONSULTA")
        e.settle()
        text = e.log.getvalue()
        assert text.count('"event": "req"') == 4, text
        for secret in (TOKEN, WRONG, "SEGREDO-CORPO", "MARCA-CONSULTA", "RESPOSTA-OK"):
            assert secret not in text, ("log vazou", secret)


def s_upstream_status():
    with PonteEnv() as e:
        st, hdrs, body = e.call(url="https://geoserver.car.gov.br/status500")
        assert st == 500 and "x-ponte-error" not in hdrs and hdrs.get("x-ponte-origin") == "upstream", (st, hdrs)


def s_tls_and_source():
    import ssl
    for host in sorted(ponte.ALLOWED_HOSTS):
        ctx = ponte.tls_context_for(host)
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True, host
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2, host
    # Concessão medida em 14/09/2026: o SICAR só negocia AES256-GCM-SHA384 (troca de chave RSA). Se a cifra
    # sumir do contexto, a ponte deixa de alcançar o SICAR em silêncio.
    sicar = {c["name"] for c in ponte.tls_context_for("geoserver.car.gov.br").get_ciphers()}
    assert "AES256-GCM-SHA384" in sicar, "contexto do SICAR sem AES256-GCM-SHA384: a ponte não alcança o SICAR"
    incra = ponte.tls_context_for("acervofundiario.incra.gov.br").get_ciphers()
    rsa_kex = sorted(c["name"] for c in incra if c.get("kea") == "kx-rsa")
    assert not rsa_kex, ("INCRA com troca de chave RSA: a concessão é só do SICAR", rsa_kex[:5])
    assert {c["name"] for c in incra} == {c["name"] for c in ssl.create_default_context().get_ciphers()}
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
    keys = (br_bridge.ENV_URL, br_bridge.ENV_TOKEN, br_bridge.ENV_KEY)
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
        # 15/09: os tres deixaram o br_bridge.subprocess_runner e passaram ao run_managed_process (cancelavel).
        # A chamada e' a mesma; o que muda e' o executor e o nome do prazo.
        ("incra_snci_public_v42._curl", lambda: snci._curl(INCRA_URL, 10), (snci, "run_managed_process"),
         ["curl", "-k", "-sS", "-L", "--fail", "--retry", "0", "--connect-timeout", "5", "--max-time", "10", "-A",
          "Raio-X-Territorial/INCRA-SNCI-v42", INCRA_URL],
         {"timeout_seconds": 14, "cancel_event": None}),
        ("sicar_detail_sources._curl", lambda: sds._curl(SICAR_URL), (sds, "run_managed_process"),
         ["curl", "-k", "-sS", "--connect-timeout", "12", "--max-time", "35", "-A", "Raio-X-Territorial/0.14.10", SICAR_URL],
         {"timeout_seconds": 40, "cancel_event": None}),
        ("sicar_detail_sources_v2._curl", lambda: sds2._curl(SICAR2_URL), (sds2, "run_managed_process"),
         ["curl", "-k", "-sS", "--fail", "--retry", "0", "--connect-timeout", "6", "--max-time", "13", "-A",
          "Raio-X-Territorial/SICAR-v41", SICAR2_URL],
         {"timeout_seconds": 17, "cancel_event": None}),
    ]


OFF_ENVS = (
    {},
    {br_bridge.ENV_URL: BRIDGE_BASE},
    {br_bridge.ENV_TOKEN: TOKEN},
    {br_bridge.ENV_URL: "http://ponte-gate.a.run.app", br_bridge.ENV_TOKEN: TOKEN},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: "curto"},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: TOKEN[:20] + " " + TOKEN[20:]},
    {br_bridge.ENV_URL: "https://user@ponte-gate.a.run.app", br_bridge.ENV_TOKEN: TOKEN},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: TOKEN, br_bridge.ENV_KEY: "nao-e-uma-chave"},
    {br_bridge.ENV_URL: BRIDGE_BASE, br_bridge.ENV_TOKEN: TOKEN, br_bridge.ENV_KEY: '{"type": "service_account"}'},
    {br_bridge.ENV_KEY: "nao-e-uma-chave"},
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
        with mock.patch.object(snci, "run_managed_process", rec):
            snci._curl(INCRA_URL, 10)
        assert len(rec.calls) == 1, rec.calls
        _bridge_call_ok(rec.calls[0], "snci", url=INCRA_URL, timeout=14, max_time=10)
        assert rec.calls[0][1]["cancel_event"] is None
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
        with mock.patch.object(sds2, "run_managed_process", rec):
            out = sds2._curl(SICAR2_URL, False)
        assert out["ok"] is False and len(rec.calls) == 1, rec.calls
        # --fail com 403: ponte com o que sobra (16 s; --max-time 13 cabe)
        br_bridge.reset_state()
        rec = Recorder((22, 1.0, b"", b"curl: (22) The requested URL returned error: 403"), (0, 1.0, b"<xml/>", b""), clock=clock)
        with mock.patch.object(sds2, "run_managed_process", rec):
            out = sds2._curl(SICAR2_URL, False)
        assert out["ok"] is True and len(rec.calls) == 2, rec.calls
        _bridge_call_ok(rec.calls[1], "sicar v2 403", url=SICAR2_URL, timeout=16.0, max_time=13)
        # 404 é resposta da fonte: sem ponte
        br_bridge.reset_state()
        rec = Recorder((22, 1.0, b"", b"curl: (22) The requested URL returned error: 404"), clock=clock)
        with mock.patch.object(sds2, "run_managed_process", rec):
            sds2._curl(SICAR2_URL, False)
        assert len(rec.calls) == 1, rec.calls


def c_sicar_window_rule():
    """Dentro da janela, só falha da PRÓPRIA ponte fecha a janela; ponte ocupada ou erro da fonte, não."""
    _, _, _, _, sds2 = _import_transports()
    host = "geoserver.car.gov.br"
    cases = (
        ("fonte 502 repassada", 22, b"curl: (22) The requested URL returned error: 502", True),
        ("fonte 504 pela ponte", 22, b"curl: (22) The requested URL returned error: 504", True),
        ("ponte ocupada 503", 22, b"curl: (22) The requested URL returned error: 503", True),
        ("limite de taxa 429", 22, b"curl: (22) The requested URL returned error: 429", True),
        ("ponte fora: conexão", 7, b"curl: (7) Failed to connect", False),
        ("ponte fora: DNS", 6, b"curl: (6) Could not resolve host", False),
        ("ponte fora: tempo", 28, b"curl: (28) Operation timed out", False),
        ("token recusado 401", 22, b"curl: (22) The requested URL returned error: 401", False),
    )
    clock = FakeClock()
    with ponte_env(ENV_ON), mock.patch.object(br_bridge, "_now", clock):
        for name, rc, err, stays in cases:
            br_bridge.reset_state()
            rec = Recorder((7, 1.0, b"", b""), (0, 1.0, b"<xml/>", b""), (rc, 1.0, b"", err), (0, 0.5, b"<xml/>", b""),
                           clock=clock)
            with mock.patch.object(sds2, "run_managed_process", rec):
                sds2._curl(SICAR2_URL, False)  # direto cai, ponte resgata: janela abre
                assert br_bridge.direct_skipped(host), (name, "janela não abriu")
                sds2._curl(SICAR2_URL, False)  # na janela, pela ponte, falha
                assert br_bridge.direct_skipped(host) is stays, (name, "janela devia ficar " + ("aberta" if stays else "fechada"))
                sds2._curl(SICAR2_URL, False)
            assert len(rec.calls) == 4 and "input_bytes" in rec.calls[2][1], (name, rec.calls)
            assert ("input_bytes" in rec.calls[3][1]) is stays, (name, "próxima chamada", rec.calls[3])


def c_no_bridge_url_leak():
    """Erro de transporte e TimeoutExpired pela ponte nunca levam o endereço da ponte ao resultado."""
    leak_err = (b"curl: (7) Failed to connect to ponte-gate.a.run.app port 443 after 3 ms: Couldn't connect to server; "
                b"curl: (6) Could not resolve host: PONTE-GATE.a.run.app (" + BRIDGE_FETCH.encode() + b")")
    for mode in ("stderr", "timeout"):
        for name, call, (target, attr), _args, _kwargs in legacy_cases():
            bridge_seen: list[list[str]] = []

            def runner(args, **kwargs):
                args = list(args)
                if "input" in kwargs or "input_bytes" in kwargs:
                    bridge_seen.append(args)
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", kwargs.get("timeout_seconds")),
                                                        stderr=leak_err)
                    return subprocess.CompletedProcess(args, 7, b"", leak_err)
                return subprocess.CompletedProcess(args, 7, b"", b"curl: (7) Failed to connect to geoserver.car.gov.br port 443")

            with ponte_env(ENV_ON), mock.patch.object(target, attr, runner):
                try:
                    out = call()
                    text = json.dumps(out, default=str, ensure_ascii=False)
                except Exception as exc:  # sicar_detail_sources._curl não captura TimeoutExpired: o texto dela conta
                    text = f"{type(exc).__name__}:{exc}:{getattr(exc, 'cmd', '')}:{getattr(exc, 'stderr', '')}"
            assert bridge_seen and BRIDGE_FETCH in bridge_seen[0], (name, mode, "a chamada não passou pela ponte")
            assert "ponte-gate" not in text.lower(), (name, mode, "endereço da ponte no resultado", text[:300])


def c_probe_sources():
    """Com a ponte ligada o probe mede o INCRA pela ponte (nunca httpx direto); desligada, igual a antes."""
    import asyncio

    deploy_app = _import_transports()[0]
    incra_root = deploy_app.TARGETS["incra_root"]

    class FakeResponse:
        status_code = 200
        content = b"ok"

    class FakeClient:
        seen: list[str] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            FakeClient.seen.append(str(url))
            return FakeResponse()

    for env, bridged in ((ENV_ON, True), ({}, False)):
        FakeClient.seen = []
        rec = Recorder()
        with ponte_env(env), mock.patch.object(deploy_app.httpx, "AsyncClient", FakeClient), \
                mock.patch.object(deploy_app, "run_managed_process", rec):
            out = asyncio.run(deploy_app.probe_sources())
        hosts = {urlsplit(u).hostname for u in FakeClient.seen}
        assert set(deploy_app.TARGETS) <= set(out), sorted(out)
        bridge_calls = [c for c in rec.calls if "input_bytes" in c[1]]
        if bridged:
            assert "acervofundiario.incra.gov.br" not in hosts, ("INCRA por httpx direto com a ponte ligada", FakeClient.seen)
            assert out["incra_root"].get("via") == "ponte" and out["incra_root"]["ok"] is True, out["incra_root"]
            assert len(bridge_calls) == 1 and f"X-Ponte-Url: {incra_root}".encode() in bridge_calls[0][1]["input_bytes"], rec.calls
        else:
            assert "acervofundiario.incra.gov.br" in hosts and "via" not in out["incra_root"], (FakeClient.seen, out["incra_root"])
            assert not bridge_calls, rec.calls


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
    assert line == "RX_PONTE_BRASIL=on host=ponte-gate.a.run.app incra=ponte sicar=direto_primeiro", line
    assert br_bridge.status_line({}) == "RX_PONTE_BRASIL=off"
    assert br_bridge.status_line(OFF_ENVS[-2]) == "RX_PONTE_BRASIL=off motivo=chave_invalida", br_bridge.status_line(OFF_ENVS[-2])
    assert br_bridge.status_line(OFF_ENVS[-1]) == "RX_PONTE_BRASIL=off motivo=configuracao_invalida"
    assert br_bridge.route_for(INCRA_URL, ENV_ON) == "bridge"
    assert br_bridge.route_for(SICAR_URL, ENV_ON) == "direct_then_bridge"
    assert br_bridge.route_for(OTHER_URL, ENV_ON) == "direct"
    assert br_bridge.route_for(INCRA_URL, {}) == "direct"
    for url in ("http://acervofundiario.incra.gov.br/x", "https://acervofundiario.incra.gov.br:8443/x",
                "https://u@acervofundiario.incra.gov.br/x", "https://ACERVOFUNDIARIO.incra.gov.br/x"):
        assert br_bridge.route_for(url, ENV_ON) == "direct", url
    # o endereço vai numa linha de cabeçalho (X-Ponte-Url) da entrada padrão do curl: CR/LF, controle,
    # barra invertida ou não-ASCII nunca vão pela ponte
    for url in (INCRA_URL + "\r\nX-Ponte-Url: https://geoserver.car.gov.br/", INCRA_URL + "\x00", INCRA_URL + "\x7f",
                INCRA_URL + "&nome=São", "https://acervofundiario.incra.gov.br\\@evil.example.com/"):
        assert br_bridge.route_for(url, ENV_ON) == "direct", repr(url)


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
# função com httpx direto ao SICAR, mas a rota foi substituída no arranque por módulo que usa deploy_app._curl
SUPERSEDED = {
    "portal_api.py:_sicar_at_point": ("portal_sicar_resilient.py", "'/v1/live/sicar/viewport','/v1/live/resolve'"),
    "portal_v8.py:live_sicar_viewport": ("portal_sicar_resilient.py", "'/v1/live/sicar/viewport','/v1/live/resolve'"),
    "portal_advanced_search_v39.py:advanced_property_search": ("portal_cafir_inverse_v44.py", "!='/v1/live/search/advanced'"),
}
# função que chama direto E conhece a ponte: cada uma com a verificação que prova o desvio
BRIDGE_AWARE = {"deploy_app.py:probe_sources": "c_probe_sources"}
DIRECT_HTTP_MODULES = {"httpx", "requests", "urllib3", "aiohttp"}
OFFICIAL_HOSTS = ("acervofundiario.incra.gov.br", "geoserver.car.gov.br")


def _host_literal(node) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and any(h in n.value for h in OFFICIAL_HOSTS)
               for n in ast.walk(node))


def direct_http_functions(sources: dict[str, str]) -> set[str]:
    """'arquivo.py:função' de toda função que faz HTTP/curl direto (httpx, requests, urllib, subprocess, run_managed_process)
    E referencia um host do INCRA/SICAR (literal ou constante com o host, inclusive importada ou reexportada)."""
    trees = {name[:-3]: ast.parse(text) for name, text in sources.items()}
    imports: dict[str, dict[str, object]] = {}
    consts: dict[str, set[str]] = {}
    for mod, tree in trees.items():
        imp: dict[str, object] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imp[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom) and n.module:
                for a in n.names:
                    imp[a.asname or a.name] = (n.module, a.name)
        imports[mod] = imp
        for n in tree.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and _host_literal(n.value):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    if isinstance(t, ast.Name):
                        consts.setdefault(mod, set()).add(t.id)
    changed = True
    while changed:  # reexportação: from deploy_app import *, from deploy_app import SICAR
        changed = False
        for mod, imp in imports.items():
            for alias, origin in imp.items():
                if isinstance(origin, tuple) and origin[0] in consts:
                    add = set(consts[origin[0]]) if origin[1] == "*" else ({alias} if origin[1] in consts[origin[0]] else set())
                    if add - consts.get(mod, set()):
                        consts.setdefault(mod, set()).update(add)
                        changed = True
        for mod, tree in trees.items():
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.module in consts and any(a.name == "*" for a in n.names):
                    if consts[n.module] - consts.get(mod, set()):
                        consts.setdefault(mod, set()).update(consts[n.module])
                        changed = True

    flagged: set[str] = set()
    for mod, tree in trees.items():
        imp, local = imports[mod], consts.get(mod, set())

        def refs_host(fn) -> bool:
            if _host_literal(fn):
                return True
            for n in ast.walk(fn):
                if isinstance(n, ast.Name) and n.id in local:
                    return True
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                    origin = imp.get(n.value.id)
                    if isinstance(origin, str) and n.attr in consts.get(origin, set()):
                        return True
            return False

        def calls_direct(fn) -> bool:
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                if isinstance(f, ast.Name):
                    origin = imp.get(f.id)
                    if f.id == "run_managed_process" or (isinstance(origin, tuple) and (
                            origin[0].split(".")[0] in DIRECT_HTTP_MODULES or origin[0] == "urllib.request")):
                        return True
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    origin = imp.get(f.value.id)
                    if origin in DIRECT_HTTP_MODULES:
                        return True
                    if origin == "subprocess" and f.attr in ("run", "Popen", "call", "check_call", "check_output"):
                        return True
                    if origin == "asyncio" and f.attr.startswith("create_subprocess"):
                        return True
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute) and isinstance(f.value.value, ast.Name):
                    if imp.get(f.value.value.id) == "urllib" and f.value.attr == "request":
                        return True
            return False

        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and calls_direct(n) and refs_host(n):
                flagged.add(f"{mod}.py:{n.name}")
    return flagged


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
    assert set(OFFICIAL_HOSTS) == set(ponte.ALLOWED_HOSTS)
    # Varredura por FUNÇÃO, em todos os arquivos (sem isentar os transportes): chamada direta + host oficial.
    sources = {p.name: p.read_text(encoding="utf-8", errors="ignore") for p in ROOT.glob("*.py")}
    flagged = direct_http_functions(sources)
    expected = set(SUPERSEDED) | set(BRIDGE_AWARE)
    assert flagged <= expected, ("chamada direta ao INCRA/SICAR fora do br_bridge", sorted(flagged - expected))
    assert expected <= flagged, ("lista da varredura desatualizada", sorted(expected - flagged))
    # controle positivo da própria varredura: httpx novo dentro de um transporte e requests num arquivo novo
    probe = dict(sources)
    probe["deploy_app.py"] += "\n\nasync def _gate_novo_httpx():\n    async with httpx.AsyncClient() as c:\n        return await c.get(SICAR)\n"
    probe["gate_novo_modulo.py"] = ("import requests\nimport portal_api as base\n\n"
                                    "def buscar():\n    return requests.get(base.SICAR, timeout=5)\n")
    caught = direct_http_functions(probe) - flagged
    assert caught == {"deploy_app.py:_gate_novo_httpx", "gate_novo_modulo.py:buscar"}, ("varredura cega", sorted(caught))
    for name, (replacer, marker) in SUPERSEDED.items():
        assert marker in (ROOT / replacer).read_text(encoding="utf-8"), (name, "rota antiga voltou a valer", replacer)
    wf = (ROOT / ".github" / "workflows" / "quality-gate.yml").read_text(encoding="utf-8")
    assert "scripts/ponte_brasil_gate.py" in wf, "gate fora do CI"


# ---------------------------------------------------------------------------------------------
# M · controles positivos
# ---------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------
# I · ponte fechada pelo Google (conta de serviço)
# ---------------------------------------------------------------------------------------------

GATE_SA_EMAIL = "ponte-render@raio-x-gate.iam.gserviceaccount.com"
GATE_KEY_ID = "0123456789abcdef0123456789abcdef01234567"
_GATE_KEY: dict = {}


def gate_key():
    """Chave RSA de teste gerada na hora (nunca gravada no repositório) e o JSON no formato do Google."""
    if not _GATE_KEY:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:  # o CI instala PyJWT[crypto]; sem ela a comparação byte a byte não roda
            raise AssertionError("biblioteca cryptography ausente: o gate precisa dela para conferir a assinatura") from None
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode("ascii")
        info = {"type": "service_account", "project_id": "raio-x-gate", "private_key_id": GATE_KEY_ID,
                "private_key": pem, "client_email": GATE_SA_EMAIL, "client_id": "1",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": br_bridge.GOOGLE_TOKEN_URI}
        raw = json.dumps(info)
        _GATE_KEY.update(private=private, pem=pem, info=info, json=raw, b64=base64.b64encode(raw.encode()).decode("ascii"))
    return _GATE_KEY


def env_identity():
    return {**ENV_ON, br_bridge.ENV_KEY: gate_key()["b64"]}


def _b64url_json(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _b64url_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def fake_id_token(audience=BRIDGE_BASE, lifetime=3600.0, marker="GATEIDTOKEN"):
    return _b64url_json({"alg": "RS256"}) + "." + _b64url_json({"aud": audience, "exp": time.time() + lifetime, "m": marker}) + ".c2ln"


class FakeTokenEndpoint:
    """Troca falsa do JWT: registra a asserção e devolve o token de identidade (ou erro, ou demora)."""

    def __init__(self, *, error=None, delay=0.0, audience=BRIDGE_BASE, lifetime=3600.0):
        self.assertions: list[str] = []
        self.error, self.delay = error, delay
        self.token = fake_id_token(audience, lifetime)

    def __call__(self, assertion, timeout):
        self.assertions.append(assertion)
        assert timeout == br_bridge.ID_TOKEN_HTTP_TIMEOUT_S, timeout
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return json.dumps({"id_token": self.token}).encode()


def _wait_refresh_idle(seconds=5.0):
    assert _wait(lambda: not br_bridge._id_refreshing, seconds), "troca de credencial pendurada"


def _verify_rs256(public_key, signing_input: bytes, signature: bytes):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())


def i_key_parse_and_signature():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = gate_key()
    for raw in (k["json"], k["b64"], base64.urlsafe_b64encode(k["json"].encode()).decode().rstrip("="),
                "\n".join(k["b64"][i:i + 76] for i in range(0, len(k["b64"]), 76))):
        br_bridge.parse_service_account.cache_clear()
        cfg = br_bridge.config({**ENV_ON, br_bridge.ENV_KEY: raw})
        assert cfg is not None and cfg.identity is not None and cfg.identity.email == GATE_SA_EMAIL, raw[:20]
        assert cfg.identity.signer == "cryptography" and cfg.audience == BRIDGE_BASE, cfg.identity
    line = br_bridge.status_line(env_identity())
    assert line == ("RX_PONTE_BRASIL=on host=ponte-gate.a.run.app incra=ponte sicar=direto_primeiro "
                    "acesso=google assinatura=cryptography"), line
    identity = br_bridge.config(env_identity()).identity
    pem_line = k["pem"].splitlines()[5]
    for text in (repr(identity), repr(identity.numbers), repr(br_bridge.config(env_identity())), line):
        assert pem_line not in text and str(identity.numbers.d)[:40] not in text, ("chave no repr", text[:120])
    # Python puro: mesma assinatura, byte a byte, que a biblioteca (PKCS#1 v1.5 é determinística) e verificável
    for data in (b"", b"cabecalho.claims", os.urandom(333)):
        pure = br_bridge.sign_rs256_python(identity.numbers, data)
        assert pure == identity.sign(data), "assinatura em Python puro diferente da cryptography"
        _verify_rs256(k["private"].public_key(), data, pure)
    with mock.patch.object(br_bridge, "_cryptography_signer", lambda pem: None):
        br_bridge.parse_service_account.cache_clear()
        cfg = br_bridge.config(env_identity())
        assert cfg.identity.signer == "python" and cfg.identity.sign(b"x") == identity.sign(b"x"), cfg.identity
        assert br_bridge.status_line(env_identity()).endswith("acesso=google assinatura=python")
    br_bridge.parse_service_account.cache_clear()
    pkcs1 = k["private"].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()).decode("ascii")
    assert br_bridge.rsa_numbers_from_pem(pkcs1) == identity.numbers
    # chave estragada: ponte desligada e dito no log (nunca meio ligada, só com token)
    info = k["info"]
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_numbers()
    mine = k["private"].private_numbers()
    der = bytearray(k["private"].private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.TraditionalOpenSSL,
                                               serialization.NoEncryption()))
    n_bytes = mine.public_numbers.n.to_bytes(257, "big")
    at = bytes(der).find(n_bytes)
    assert at > 0, "n não achado no DER de teste"
    mixed_der = bytes(der[:at]) + other.public_numbers.n.to_bytes(257, "big") + bytes(der[at + 257:])
    incoherent = ("-----BEGIN RSA PRIVATE KEY-----\n" + base64.encodebytes(mixed_der).decode("ascii")
                  + "-----END RSA PRIVATE KEY-----\n")
    # n = p·q certo, mas d mod (p-1) errado: só a segunda conferência dos números pega
    dp_len = mine.dmp1.bit_length() // 8 + 1
    dp_bytes = mine.dmp1.to_bytes(dp_len, "big")
    at = bytes(der).find(dp_bytes)
    assert at > 0 and bytes(der).count(dp_bytes) == 1, "dp não achado no DER de teste"
    dp_der = bytes(der[:at]) + (mine.dmp1 ^ 1).to_bytes(dp_len, "big") + bytes(der[at + dp_len:])
    wrong_dp = ("-----BEGIN RSA PRIVATE KEY-----\n" + base64.encodebytes(dp_der).decode("ascii")
                + "-----END RSA PRIVATE KEY-----\n")
    bad = [
        k["b64"][: len(k["b64"]) // 2],                                   # colada pela metade
        k["json"][:-40],
        json.dumps({**info, "type": "authorized_user"}),
        json.dumps({**info, "token_uri": "https://evil.example.com/token"}),
        json.dumps({**info, "client_email": "alguem@gmail.com"}),
        json.dumps({**info, "private_key_id": "x"}),
        json.dumps({**info, "private_key": small}),                       # 1024 bits
        json.dumps({**info, "private_key": incoherent}),                  # n de outra chave
        json.dumps({**info, "private_key": wrong_dp}),                    # CRT incoerente
        json.dumps({**info, "private_key": k["pem"].replace("PRIVATE KEY-----", "PUBLIC KEY-----")}),
        json.dumps([info]),
        "A" * (br_bridge.MAX_KEY_ENV_LEN + 1),
    ]
    for raw in bad:
        br_bridge.parse_service_account.cache_clear()
        env = {**ENV_ON, br_bridge.ENV_KEY: raw}
        assert br_bridge.config(env) is None, ("chave estragada aceita", raw[:60])
        assert br_bridge.status_line(env) == "RX_PONTE_BRASIL=off motivo=chave_invalida", (raw[:60], br_bridge.status_line(env))
        assert br_bridge.route_for(INCRA_URL, env) == "direct"
    br_bridge.parse_service_account.cache_clear()


def i_assertion():
    k = gate_key()
    identity = br_bridge.config(env_identity()).identity
    jwt = br_bridge.signed_assertion(identity, BRIDGE_BASE, 1_800_000_000)
    head, body, sig = jwt.split(".")
    assert json.loads(_b64url_dec(head)) == {"alg": "RS256", "typ": "JWT", "kid": GATE_KEY_ID}, _b64url_dec(head)
    assert json.loads(_b64url_dec(body)) == {
        "iss": GATE_SA_EMAIL, "sub": GATE_SA_EMAIL, "aud": "https://oauth2.googleapis.com/token",
        "iat": 1_800_000_000, "exp": 1_800_003_600, "target_audience": BRIDGE_BASE}, _b64url_dec(body)
    _verify_rs256(k["private"].public_key(), f"{head}.{body}".encode(), _b64url_dec(sig))
    token, exp = br_bridge.parse_id_token_response(json.dumps({"id_token": fake_id_token()}).encode(), BRIDGE_BASE, time.time())
    assert token.count(".") == 2 and exp > time.time() + 3000
    for raw in (b"{}", json.dumps({"id_token": fake_id_token(audience="https://outra.a.run.app")}).encode(),
                json.dumps({"id_token": fake_id_token(lifetime=5)}).encode(),
                json.dumps({"id_token": "a.b.c\r\nX-Injetado: 1"}).encode(), b"nao-json"):
        try:
            br_bridge.parse_id_token_response(raw, BRIDGE_BASE, time.time())
        except (ValueError, TypeError):
            continue
        raise AssertionError(("resposta estranha aceita como token", raw[:60]))
    try:
        br_bridge.bridge_request(["curl", INCRA_URL], br_bridge.config(env_identity()), id_token="a.b.c\r\nX-Injetado: 1")
    except ValueError:
        pass
    else:
        raise AssertionError("token de identidade com CR/LF foi para os cabeçalhos")


def _id_header(call):
    stdin = call[1].get("input_bytes") or call[1].get("input") or b""
    found = [ln for ln in stdin.split(b"\r\n") if ln.lower().startswith(b"x-serverless-authorization:")]
    return found[0].decode() if found else None


def i_incra_identity():
    _, acervo, snci, _, _ = _import_transports()
    clock = FakeClock()
    endpoint = FakeTokenEndpoint()
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_now", clock), \
            mock.patch.object(br_bridge, "_post_token", endpoint):
        rec = Recorder((0, 0.0, b"<gml/>", b""))
        with mock.patch.object(epl, "run_managed_process", rec):
            out = acervo.curl_fetch(INCRA_URL)
        assert out["ok"] is True and len(rec.calls) == 1, (out, rec.calls)
        _bridge_call_ok(rec.calls[0], "acervo google", url=INCRA_URL, timeout=10, max_time=8)
        assert _id_header(rec.calls[0]) == f"X-Serverless-Authorization: Bearer {endpoint.token}", _id_header(rec.calls[0])
        assert all(endpoint.token not in a and "GATE" not in a for a in rec.calls[0][0]), "token de identidade na linha de comando"
        rec = Recorder((0, 0.0, b"<xml/>", b""))
        with mock.patch.object(snci, "run_managed_process", rec):
            snci._curl(INCRA_URL, 10)
        _bridge_call_ok(rec.calls[0], "snci google", url=INCRA_URL, timeout=14, max_time=10)
        assert _id_header(rec.calls[0]) is not None
        _wait_refresh_idle()
        assert len(endpoint.assertions) == 1, ("uma troca por hora, não uma por consulta", len(endpoint.assertions))
        claims = json.loads(_b64url_dec(endpoint.assertions[0].split(".")[1]))
        assert claims["target_audience"] == BRIDGE_BASE and claims["iss"] == GATE_SA_EMAIL, claims
    # sem a chave (variáveis antigas): nenhuma troca, nenhum cabeçalho do Google
    endpoint = FakeTokenEndpoint()
    with ponte_env(ENV_ON), mock.patch.object(br_bridge, "_post_token", endpoint):
        rec = Recorder((0, 0.0, b"<gml/>", b""))
        with mock.patch.object(epl, "run_managed_process", rec):
            acervo.curl_fetch(INCRA_URL)
        assert _id_header(rec.calls[0]) is None and not endpoint.assertions, (rec.calls, endpoint.assertions)
    # token perto de vencer (faltam 200 s): usa o que ainda vale, renova em segundo plano, a seguinte usa o novo
    endpoint = FakeTokenEndpoint()
    wall = [time.time()]
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_post_token", endpoint),             mock.patch.object(br_bridge, "_wall", lambda: wall[0]):
        rec = Recorder(*([(0, 0.0, b"<gml/>", b"")] * 3))
        with mock.patch.object(epl, "run_managed_process", rec):
            acervo.curl_fetch(INCRA_URL)
            _wait_refresh_idle()
            first = endpoint.token
            endpoint.token = fake_id_token(lifetime=7000.0, marker="RENOVADO")
            wall[0] += 3400.0
            acervo.curl_fetch(INCRA_URL)
            _wait_refresh_idle()
            acervo.curl_fetch(INCRA_URL)
            _wait_refresh_idle()
        got = [_id_header(c) for c in rec.calls]
        assert got == [f"X-Serverless-Authorization: Bearer {t}" for t in (first, first, endpoint.token)], got
        assert len(endpoint.assertions) == 2, ("renovação do token perto de vencer", len(endpoint.assertions))


def i_sicar_identity():
    deploy_app = _import_transports()[0]
    clock = FakeClock()
    endpoint = FakeTokenEndpoint()
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_now", clock), \
            mock.patch.object(br_bridge, "_post_token", endpoint):
        # direto responde: nem ponte, nem troca de credencial
        rec = Recorder((0, 0.4, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            deploy_app._curl(SICAR_URL, True)
        assert len(rec.calls) == 1 and not endpoint.assertions, (rec.calls, endpoint.assertions)
        # direto cai (12 s): ponte com o que sobra e com o token de identidade
        rec = Recorder((7, 12.0, b"", b"curl: (7) Failed to connect"), (0, 1.0, b'{"features": []}', b""), clock=clock)
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        assert out["ok"] and len(rec.calls) == 2, rec.calls
        _bridge_call_ok(rec.calls[1], "sicar google", url=SICAR_URL, timeout=33.0, max_time=32.75)
        assert _id_header(rec.calls[1]) is not None and 12.0 + rec.calls[1][1]["timeout_seconds"] <= 45 + 1e-9
    # direto cai e a credencial falha: devolve a falha direta (honesta), sem chamar a ponte sem credencial
    endpoint = FakeTokenEndpoint(error=urllib.error.HTTPError(br_bridge.GOOGLE_TOKEN_URI, 400, "invalid_grant", {}, None))
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_post_token", endpoint), \
            contextlib.redirect_stdout(io.StringIO()):
        rec = Recorder((7, 0.0, b"", b"curl: (7) Failed to connect to geoserver.car.gov.br"))
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
        _wait_refresh_idle()
        assert out["ok"] is False and len(rec.calls) == 1 and "input_bytes" not in rec.calls[0][1], rec.calls
        br_bridge.reset_state()
        rec = Recorder((7, 0.0, b"", b"curl: (7) Failed to connect to geoserver.car.gov.br"))
        proc = br_bridge.run_curl(["curl", "-sS", "--max-time", "10", SICAR_URL], timeout_seconds=12.0, runner=rec)
        _wait_refresh_idle()
        assert (proc.returncode, proc.stderr) == (7, b"curl: (7) Failed to connect to geoserver.car.gov.br"), proc
        assert len(rec.calls) == 1, ("chamou a ponte sem credencial", rec.calls)
        # janela aberta e credencial indisponível: fecha a janela e a próxima tenta direto
        br_bridge._open_skip_window("geoserver.car.gov.br", "gate")
        rec = Recorder((0, 0.0, b'{"features": []}', b""))
        with mock.patch.object(deploy_app, "run_managed_process", rec):
            out = deploy_app._curl(SICAR_URL, True)
            assert out["ok"] is False and not rec.calls, ("chamou a ponte sem credencial", rec.calls)
            assert not br_bridge.direct_skipped("geoserver.car.gov.br"), "janela devia fechar sem credencial"
            out = deploy_app._curl(SICAR_URL, True)
        assert out["ok"] and len(rec.calls) == 1 and "input_bytes" not in rec.calls[0][1], rec.calls


def i_credential_failures():
    _, acervo, _, _, _ = _import_transports()
    k = gate_key()
    errors = (
        (urllib.error.HTTPError(br_bridge.GOOGLE_TOKEN_URI, 400, "invalid_grant", {}, None), "http_400"),
        (urllib.error.URLError("sem rede"), "rede"),
        (TimeoutError("tempo"), "rede"),
    )
    for error, reason in errors:
        endpoint = FakeTokenEndpoint(error=error)
        log = io.StringIO()
        br_bridge.reset_state()
        with ponte_env(env_identity()), mock.patch.object(br_bridge, "_post_token", endpoint), contextlib.redirect_stdout(log):
            rec = Recorder((0, 0.0, b"<gml/>", b""))
            with mock.patch.object(epl, "run_managed_process", rec):
                out = acervo.curl_fetch(INCRA_URL)
                _wait_refresh_idle()
                out2 = acervo.curl_fetch(INCRA_URL)   # dentro de 30 s: não insiste na troca
            _wait_refresh_idle()
        assert out["ok"] is False and out2["ok"] is False and not rec.calls, ("chamada sem credencial", reason, rec.calls)
        assert len(endpoint.assertions) == 1, (reason, "insistiu na troca", len(endpoint.assertions))
        text = log.getvalue() + json.dumps([out, out2], default=str, ensure_ascii=False)
        assert f"RX_PONTE_BRASIL=erro_credencial motivo={reason}" in log.getvalue(), log.getvalue()
        for secret in (TOKEN, k["pem"].splitlines()[3], endpoint.assertions[0][-60:], "ponte-gate"):
            assert secret not in text, ("vazou", reason, secret[:20])
    # resposta estranha do Google
    endpoint = FakeTokenEndpoint()
    endpoint.token = "nao-e-token"
    log = io.StringIO()
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_post_token", endpoint), contextlib.redirect_stdout(log):
        rec = Recorder()
        with mock.patch.object(epl, "run_managed_process", rec):
            assert acervo.curl_fetch(INCRA_URL)["ok"] is False and not rec.calls
        _wait_refresh_idle()
    assert "motivo=resposta_invalida" in log.getvalue(), log.getvalue()
    # a espera pela credencial nunca passa do prazo da consulta menos o mínimo da ponte (4 s - 3 s = 1 s)
    # (a troca falsa demora 3 s: se a espera passasse do limite, o token chegaria e a chamada iria à ponte)
    endpoint = FakeTokenEndpoint(delay=3.0)
    with ponte_env(env_identity()), mock.patch.object(br_bridge, "_post_token", endpoint):
        rec = Recorder((0, 0.0, b"<gml/>", b""))
        started = time.monotonic()
        proc = br_bridge.run_curl(["curl", "-sS", "--max-time", "3", INCRA_URL], timeout_seconds=4.0, runner=rec)
        elapsed = time.monotonic() - started
        _wait_refresh_idle()
        assert proc.returncode == br_bridge.CREDENTIAL_FAILURE_EXIT and not rec.calls, (proc, rec.calls)
        assert 0.8 <= elapsed <= 2.5, ("espera pela credencial fora do prazo", round(elapsed, 2))
        # cancelamento durante a espera: sai já
        br_bridge.reset_state()
        event = threading.Event()
        event.set()
        started = time.monotonic()
        proc = br_bridge.run_curl(["curl", "-sS", INCRA_URL], timeout_seconds=30.0, cancel_event=event, runner=rec)
        assert proc.returncode != 0 and time.monotonic() - started < 0.5 and not rec.calls, proc
        _wait_refresh_idle()


def i_sicar_budget_after_credential():
    """SICAR: se a espera pela credencial consumir o prazo, a falha direta volta; nunca ponte com menos de 3 s."""
    for advance, bridged in ((3.0, False), (0.0, True)):
        clock = FakeClock()
        endpoint = FakeTokenEndpoint()

        def slow_endpoint(assertion, timeout, advance=advance):
            clock.t += advance      # a troca "levou" advance segundos no relógio da consulta
            return endpoint(assertion, timeout)

        with ponte_env(env_identity()), mock.patch.object(br_bridge, "_now", clock), \
                mock.patch.object(br_bridge, "_post_token", slow_endpoint):
            rec = Recorder((7, 40.0, b"", b"curl: (7) Failed to connect"), (0, 1.0, b'{"features": []}', b""), clock=clock)
            proc = br_bridge.run_curl(["curl", "-sS", "--max-time", "43", SICAR_URL], timeout_seconds=45.0, runner=rec)
            _wait_refresh_idle()
        if bridged:   # controle: sem demora na credencial, a mesma queda vai à ponte (o teste não é vazio)
            assert proc.returncode == 0 and len(rec.calls) == 2 and _id_header(rec.calls[1]), rec.calls
        else:
            assert proc.returncode == 7 and len(rec.calls) == 1, ("ponte com menos que o prazo mínimo", rec.calls)


def i_prewarm():
    """Arranque real (módulo carregado do zero): com a chave, o br_bridge já pede o primeiro token sozinho."""
    path = ROOT / "br_bridge.py"
    source = _BRIDGE_FILE_OVERRIDE.get("src") or path.read_text(encoding="utf-8")
    endpoint = FakeTokenEndpoint()
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append((request.full_url, timeout))
        return io.BytesIO(json.dumps({"id_token": endpoint.token}).encode())

    for env, wants_token in ((env_identity(), True), (ENV_ON, False)):
        seen.clear()
        name = "br_bridge_arranque"
        module = types.ModuleType(name)
        module.__file__ = str(path)
        sys.modules[name] = module
        out = io.StringIO()
        try:
            with ponte_env(env), mock.patch("urllib.request.urlopen", fake_urlopen), contextlib.redirect_stdout(out):
                exec(compile(source, str(path), "exec"), module.__dict__)
                if wants_token:
                    assert _wait(lambda: bool(module._id_tokens), 5.0), ("arranque sem pedir o token de identidade", seen)
                    assert seen == [(module.GOOGLE_TOKEN_URI, module.ID_TOKEN_HTTP_TIMEOUT_S)], seen
                else:
                    time.sleep(0.3)
                    assert not seen and not module._id_tokens, seen
        finally:
            sys.modules.pop(name, None)
        assert ("acesso=google" in out.getvalue()) == wants_token, out.getvalue()


def i_real_curl_identity():
    """curl de verdade com o token de identidade pela entrada padrão: a ponte aceita e a fonte nunca o vê."""
    assert shutil.which("curl"), "curl ausente"
    target = "https://acervofundiario.incra.gov.br/ok?tema=x"
    base_args = ["curl", "-sS", "--fail", "--connect-timeout", "5", "--max-time", "8", "-A", "Raio-X-Territorial/gate-curl", target]
    idt = fake_id_token()
    with PonteEnv() as e:
        cfg = br_bridge.config({br_bridge.ENV_URL: f"https://127.0.0.1:{e.port}", br_bridge.ENV_TOKEN: TOKEN,
                                br_bridge.ENV_KEY: gate_key()["b64"]})
        args, stdin = br_bridge.bridge_request(base_args, cfg, id_token=idt)
        assert all(idt not in a and TOKEN not in a for a in args)
        assert f"X-Serverless-Authorization: Bearer {idt}".encode() in stdin
        args = [a.replace(f"https://127.0.0.1:{e.port}", f"http://127.0.0.1:{e.port}") for a in args]
        before = len(UPSTREAM_SEEN)
        proc = epl.run_managed_process(args, timeout_seconds=20, input_bytes=stdin)
        assert proc.returncode == 0 and b"RESPOSTA-OK" in proc.stdout, (proc.returncode, proc.stderr[:200])
        seen = UPSTREAM_SEEN[before:]
        assert len(seen) == 1 and not any("serverless" in h or "authorization" in h for h in seen[0]["headers"]), seen
        assert all(idt not in v for v in seen[0]["headers"].values())
        assert "GATEIDTOKEN" not in e.log.getvalue() and idt not in e.log.getvalue()
        assert epl.active_child_pids() == [], "processo curl pendurado"


def _load_conferir():
    spec = importlib.util.spec_from_file_location("ponte_brasil_conferir", ROOT / "scripts" / "ponte_brasil_conferir.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ponte_brasil_conferir"] = module
    spec.loader.exec_module(module)
    return module


CONFERIR = _load_conferir()


def _conferir_run(env, statuses, *, token_ok=True, incra_rc=0, sicar_rc=0, esperar="0", clock=None):
    """Roda a conferência com o Google e as fontes falsos. statuses(com_identidade) -> código HTTP."""
    mod = CONFERIR
    calls = {"health": [], "fetch": []}

    def http_status(url, headers):
        calls["health"].append((url, dict(headers)))
        return statuses(br_bridge.ID_TOKEN_HEADER in headers)

    def fetch_via_bridge(args, *, timeout_seconds, runner, env=None):
        calls["fetch"].append(list(args))
        rc = incra_rc if "incra" in args[-1] else sicar_rc
        return subprocess.CompletedProcess(args, rc, b"", b"curl: (22) The requested URL returned error: 502" if rc == 22 else b"")

    out = io.StringIO()
    endpoint = FakeTokenEndpoint(error=None if token_ok else OSError("sem rede"))
    patches = [mock.patch.object(mod, "http_status", http_status), mock.patch.object(br_bridge, "fetch_via_bridge", fetch_via_bridge),
               mock.patch.object(br_bridge, "_post_token", endpoint), mock.patch.object(mod, "_sleep", lambda s: None)]
    if clock is not None:
        patches.append(mock.patch.object(mod, "_monotonic", clock))
    with contextlib.ExitStack() as stack, contextlib.redirect_stdout(out):
        for p in patches:
            stack.enter_context(p)
        code = mod.main(["--esperar", esperar], env=env)
    _wait_refresh_idle()
    br_bridge.reset_state()
    return code, out.getvalue(), calls, endpoint


def i_conferir():
    env = env_identity()
    k = gate_key()
    code, text, calls, endpoint = _conferir_run(env, lambda with_id: 200 if with_id else 403)
    assert code == 0 and text.rstrip().endswith("RESULTADO=ok"), text
    assert "INCRA: respondeu pela ponte" in text and "fechada: pedido sem credencial do Google recusado (403)" in text, text
    assert [c[0] for c in calls["health"]] == [BRIDGE_BASE + "/v1/health"] * 2, calls["health"]
    assert br_bridge.ID_TOKEN_HEADER not in calls["health"][0][1] and calls["health"][0][1].get("X-Ponte-Token") == TOKEN
    assert [a[-1] for a in calls["fetch"]] == [CONFERIR.INCRA_URL, CONFERIR.SICAR_URL], calls["fetch"]
    for secret in (TOKEN, "ponte-gate", endpoint.token, k["b64"][:80], k["pem"].splitlines()[3]):
        assert secret not in text, ("conferência imprimiu segredo", secret[:20])
    scenarios = (
        (lambda w: 200, {}, "PAROU: a ponte continua aberta ao publico"),
        (lambda w: 200 if w else 403, {"token_ok": False}, "PAROU: o Google nao entregou a credencial"),
        (lambda w: 403, {}, "PAROU: a chave ainda nao tem permissao"),
        (lambda w: 200 if w else 403, {"incra_rc": 22}, "PAROU: o INCRA nao respondeu pela ponte"),
        (lambda w: None, {}, "PAROU: a ponte nao respondeu como esperado"),
        (lambda w: 200 if w else 401, {}, "PAROU: a ponte nao respondeu como esperado"),
    )
    for statuses, kwargs, want in scenarios:
        code, text, calls, _ = _conferir_run(env, statuses, **kwargs)
        assert code == 1 and text.rstrip().splitlines()[-1].startswith(want), (want, text)
        assert "RESULTADO=ok" not in text and TOKEN not in text and "ponte-gate" not in text, text
        if "INCRA" not in want:
            assert not calls["fetch"], ("consultou as fontes com a ponte errada", want)
    code, text, _, _ = _conferir_run(ENV_ON, lambda w: 200 if w else 403)
    assert code == 1 and "PAROU: falta a chave" in text, text
    code, text, _, _ = _conferir_run({**ENV_ON, br_bridge.ENV_KEY: "estragada"}, lambda w: 200 if w else 403)
    assert code == 1 and "PAROU: a URL, o token ou a chave" in text and "motivo=chave_invalida" in text, text
    # espera: tenta de novo até a permissão valer (relógio falso, sem dormir de verdade)
    answers = iter([403, 403, 200])
    ticks = iter(range(0, 1000, 5))
    code, text, calls, _ = _conferir_run(env, lambda w: next(answers) if w else 403, esperar="100",
                                         clock=lambda: float(next(ticks)))
    assert code == 0 and "aguardando o Google" in text and len(calls["health"]) == 6, (text, len(calls["health"]))
    # sem esperar: não repete
    code, text, calls, _ = _conferir_run(env, lambda w: 403, esperar="0")
    assert code == 1 and len(calls["health"]) == 2, len(calls["health"])


# ---------------------------------------------------------------------------------------------
# G · guia do dono
# ---------------------------------------------------------------------------------------------

GUIDE = ROOT / "docs" / "PONTE_BRASIL_ATIVACAO.md"


def guide_blocks(text: str) -> dict[str, str]:
    blocks = {}
    for body in re.findall(r"```bash\n(.*?)```", text, re.S):
        marker = re.match(r"bash <<'([A-Z]+)'", body.strip())
        blocks[marker.group(1) if marker else f"linha{len(blocks)}"] = body
    return blocks


def guide_problems(text: str) -> list[str]:
    problems = []
    blocks = guide_blocks(text)
    main = blocks.get("PONTE", "")
    if not main:
        return ["bloco principal PONTE ausente"]
    if "--no-allow-unauthenticated" not in main or "--allow-unauthenticated" in main:
        problems.append("ponte publicada sem --no-allow-unauthenticated")
    if "remove-iam-policy-binding" not in main or "allUsers" not in main:
        problems.append("acesso público antigo não é retirado")
    if "roles/run.invoker" not in main or "ponte_brasil_conferir.py" not in main:
        problems.append("sem permissão da conta de serviço ou sem conferência")
    for name, body in blocks.items():
        if name.startswith("linha"):
            continue
        if "set -Eeuo pipefail" not in body or "trap " not in body or "PAROU" not in body:
            problems.append(f"{name}: falha sem PAROU")
        for bad in (r'cat\s+"?\$(CHAVE|HOME/\.raio-x)', r"echo[^\n]*\$\(base64", r"set -x", r"echo[^\n]*\$CHAVE"):
            if re.search(bad, body):
                problems.append(f"{name}: segredo impresso ({bad})")
        for echo in re.findall(r'echo "([^"\n]*\$TOKEN[^"\n]*)"', body):
            if not echo.startswith("RX_PONTE_BRASIL_TOKEN"):
                problems.append(f"{name}: token impresso fora da caixa do Render")
    for needed in ("NAO MANDE NO CHAT", "raio-x-territorial-app", "raio-x-territorial-report", "RX_PONTE_BRASIL_CHAVE",
                   "Add Environment Variable", "dashboard.render.com"):
        if needed not in text:
            problems.append(f"guia sem: {needed}")
    return problems


def g_guide():
    text = GUIDE.read_text(encoding="utf-8")
    assert guide_problems(text) == [], guide_problems(text)
    bash = shutil.which("bash")
    assert bash, "bash ausente: o CI precisa dele para conferir os blocos do guia"
    blocks = guide_blocks(text)
    assert {"PONTE", "CONFERE", "TROCA"} <= set(blocks), sorted(blocks)
    for name, body in blocks.items():
        proc = subprocess.run([bash, "-n"], input=body.encode("utf-8"), capture_output=True, timeout=20)
        assert proc.returncode == 0, (name, proc.stderr.decode("utf-8", "replace")[:300])
        inner = re.search(r"bash <<'([A-Z]+)'\n(.*)\n\1\s*$", body.strip() + "\n", re.S)
        if inner:   # o heredoc não é analisado pelo bash -n de fora: confere o de dentro também
            proc = subprocess.run([bash, "-n"], input=inner.group(2).encode("utf-8"), capture_output=True, timeout=20)
            assert proc.returncode == 0, (name, "interno", proc.stderr.decode("utf-8", "replace")[:300])
    proc = subprocess.run([bash, "-n"], input=b"if then fi\n", capture_output=True, timeout=20)
    assert proc.returncode != 0, "bash -n não pega erro de sintaxe"
    # controles positivos da própria conferência do guia
    main = blocks["PONTE"]
    for label, mutated in (
        ("ponte aberta", text.replace("--no-allow-unauthenticated", "--allow-unauthenticated")),
        ("sem retirar allUsers", text.replace("remove-iam-policy-binding", "get-iam-policy")),
        ("sem PAROU", text.replace(main, main.replace("set -Eeuo pipefail", "set -uo pipefail"))),
        ("chave impressa", text.replace(main, main.replace("\nPONTE", '\ncat "$CHAVE_JSON"\nPONTE'))),
        ("token fora da caixa", text.replace(main, main.replace("\nPONTE", '\necho "token: $TOKEN"\nPONTE'))),
        ("sem o serviço do relatório", text.replace("raio-x-territorial-report", "relatorio")),
    ):
        assert guide_problems(mutated), ("conferência do guia cega", label)


# ---------------------------------------------------------------------------------------------
# G2 · código revisado (CODIGO_REVISADO) e blocos do guia rodados com gcloud/git/python3 falsos
# ---------------------------------------------------------------------------------------------

PIN_PATHS = ("ponte_brasil", "br_bridge.py", "scripts/ponte_brasil_conferir.py")
_GUIDE_OVERRIDE: dict[str, str] = {}


def _guide_text() -> str:
    return _GUIDE_OVERRIDE.get("text") or GUIDE.read_text(encoding="utf-8")


def _pin_read(rel: str) -> bytes:
    return (ROOT / rel).read_bytes()


_ORIG_PIN_READ = _pin_read


def _git_object(kind: str, data: bytes) -> str:
    return hashlib.sha1(kind.encode("ascii") + b" %d\0" % len(data) + data).hexdigest()


def reviewed_code_hashes() -> str:
    """Hashes do git (árvore de ponte_brasil/, blobs do cliente e da conferência) calculados do conteúdo atual."""
    listing = subprocess.run(["git", "ls-files", "-s", "--", *PIN_PATHS], cwd=ROOT, capture_output=True, timeout=30)
    assert listing.returncode == 0, listing.stderr[:300]
    entries: dict[str, tuple[str, str]] = {}
    for line in listing.stdout.decode("utf-8").splitlines():
        meta, path = line.split("\t", 1)
        entries[path] = (meta.split()[0], _git_object("blob", _pin_read(path)))
    assert {"ponte_brasil/app.py", "br_bridge.py", "scripts/ponte_brasil_conferir.py"} <= set(entries), sorted(entries)

    def tree(prefix: str) -> str:
        children: dict[str, tuple[str, str]] = {}
        for path, (mode, blob) in entries.items():
            if path.startswith(prefix + "/"):
                rest = path[len(prefix) + 1:]
                name = rest.split("/", 1)[0]
                children[name] = ("40000", tree(prefix + "/" + name)) if "/" in rest else (mode, blob)
        order = sorted(children, key=lambda n: n + "/" if children[n][0] == "40000" else n)
        data = b"".join(children[n][0].encode("ascii") + b" " + n.encode("utf-8") + b"\0" + bytes.fromhex(children[n][1])
                        for n in order)
        return _git_object("tree", data)

    return " ".join((tree("ponte_brasil"), entries["br_bridge.py"][1], entries["scripts/ponte_brasil_conferir.py"][1]))


def g_code_pin():
    want = reviewed_code_hashes()
    blocks = guide_blocks(_guide_text())
    for name in ("PONTE", "CONFERE"):
        got = re.findall(r'^CODIGO_REVISADO="([^"\n]*)"$', blocks.get(name, ""), re.M)
        assert got == [want], (f"{name}: CODIGO_REVISADO do guia difere do código atual (mudou a ponte, o br_bridge ou a "
                               "conferência? atualize os blocos PONTE e CONFERE)", got, want)
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *PIN_PATHS], cwd=ROOT, timeout=30).returncode == 0
    if os.environ.get("CI"):
        assert clean, "no CI o código da ponte tem de ser o do HEAD"
    if clean:   # conferência independente do cálculo: o próprio git
        proc = subprocess.run(["git", "rev-parse", *[f"HEAD:{p}" for p in PIN_PATHS]], cwd=ROOT, capture_output=True, timeout=30)
        assert proc.returncode == 0 and proc.stdout.decode("ascii").split() == want.split(), ("hash calculado difere do git",
                                                                                            proc.stdout[:200], want)
    else:
        print("PONTE_AVISO g_code_pin: código da ponte difere do HEAD; a conferência com git rev-parse roda no CI", flush=True)


SIM_PROJECT = "raio-x-4711"
SIM_OLD_TOKEN = "0a" * 32
SIM_NEW_TOKEN = "5e" * 32
SIM_NEW_KEY = "c0ffee" * 6 + "abcd"
SIM_SECRET = "SIMSEGREDO-CHAVE-PRIVADA"
SIM_URL = "https://ponte-brasil-sim-rj.a.run.app"
SIM_BUILD_MEMBER = "serviceAccount:123456789012-compute@developer.gserviceaccount.com"
SIM_RENDER_MEMBER = f"serviceAccount:ponte-render@{SIM_PROJECT}.iam.gserviceaccount.com"
SIM_RUNTIME = f"ponte-runtime@{SIM_PROJECT}.iam.gserviceaccount.com"
SIM_OPEN_POLICY = '{"bindings": [{"members": ["allUsers"], "role": "roles/run.invoker"}]}'

_SIM_LOG_LINE = """{ printf '%s' "$(umask)"; printf '\\037%s' "$@"; printf '\\n'; } >> "$SIM_LOG\""""

FAKE_BIN = {
    "gcloud": r"""#!/usr/bin/env bash
# gcloud falso do gate: registra a chamada (umask, argumentos) e responde pelo estado em $SIM_STATE
set -- gcloud "$@"
""" + _SIM_LOG_LINE + r"""
shift
st="$SIM_STATE"
case "$*" in
  "config get-value project"*) printf '%s\n' "$SIM_CONFIG_PROJECT" ;;
  "projects describe "*"value(name)"*) printf '%s\n' "$SIM_PROJECT_NAME" ;;
  "projects describe "*"projectNumber"*) printf '123456789012\n' ;;
  "projects list"*) if [ -n "$SIM_CANDIDATE" ]; then printf '%s\n' "$SIM_CANDIDATE"; fi ;;
  "services list"*) if [ "$SIM_FIREBASE" = 1 ]; then printf 'firebase.googleapis.com\n'; fi ;;
  "run services list"*) if [ -e "$st/service" ]; then printf 'ponte-brasil\n'; fi ;;
  "run services describe"*"--format=json"*)
    [ -e "$st/service" ] || { echo "ERROR: (gcloud.run.services.describe) Cannot find service [ponte-brasil]" >&2; exit 1; }
    printf '{"spec": {"template": {"spec": {"containers": [{"env": [{"name": "PONTE_TOKEN", "value": "%s"}]}]}}}}\n' "$(cat "$st/token")" ;;
  "run services describe"*)
    [ -e "$st/service" ] || { echo "ERROR: (gcloud.run.services.describe) Cannot find service [ponte-brasil]" >&2; exit 1; }
    printf 'https://ponte-brasil-sim-rj.a.run.app\n' ;;
  "run deploy"*|"run services update"*)
    for a in "$@"; do case "$a" in PONTE_TOKEN=*) printf '%s' "${a#PONTE_TOKEN=}" > "$st/token" ;; esac; done
    : > "$st/service" ;;
  "run services delete"*) [ -e "$st/service" ] || exit 1; rm -f "$st/service" ;;
  "run services get-iam-policy"*) printf '%s\n' "$SIM_POLICY" ;;
  "iam service-accounts describe "*) [ -e "$st/sa-${4%%@*}" ] || { echo "ERROR: NOT_FOUND" >&2; exit 1; } ;;
  "iam service-accounts create "*) : > "$st/sa-$4" ;;
  "iam service-accounts keys list"*) cat "$st/keys" ;;
  "iam service-accounts keys create "*)
    id="$(cat "$st/next-key")"
    printf '{"type": "service_account", "private_key_id": "%s", "private_key": "-----BEGIN PRIVATE KEY-----\\nSIMSEGREDO-CHAVE-PRIVADA\\n-----END PRIVATE KEY-----\\n", "client_email": "ponte-render@sim"}\n' "$id" > "$5"
    printf '%s\n' "$id" >> "$st/keys" ;;
  "iam service-accounts keys delete "*) { grep -vx -- "$5" "$st/keys" || true; } > "$st/keys.novo"; mv -f "$st/keys.novo" "$st/keys" ;;
esac
exit 0
""",
    "git": r"""#!/usr/bin/env bash
set -- git "$@"
""" + _SIM_LOG_LINE + r"""
shift
if [ "$1" = clone ]; then
  for alvo in "$@"; do :; done
  mkdir -p "$alvo/scripts" "$alvo/ponte_brasil"
  : > "$alvo/scripts/ponte_brasil_conferir.py"
elif [ "$1" = -C ] && [ "$3" = rev-parse ]; then
  printf '%s\n' $SIM_REV_PARSE
fi
exit 0
""",
    "python3": r"""#!/usr/bin/env bash
case "${1:-}" in
  *ponte_brasil_conferir.py)
    set -- conferir "$@" "url=${RX_PONTE_BRASIL_URL:-}" "token=${RX_PONTE_BRASIL_TOKEN:-}" "chave=${RX_PONTE_BRASIL_CHAVE:+presente}"
""" + _SIM_LOG_LINE + r"""
    exit "$SIM_CONFERIR_RC" ;;
esac
"$SIM_PYTHON" "$@" | tr -d '\r'
exit "${PIPESTATUS[0]}"
""",
    "openssl": r"""#!/usr/bin/env bash
set -- openssl "$@"
""" + _SIM_LOG_LINE + r"""
printf '%s\n' "$SIM_NEW_TOKEN"
""",
    "cloudshell": r"""#!/usr/bin/env bash
set -- cloudshell "$@"
""" + _SIM_LOG_LINE + r"""
exit 0
""",
    "sleep": "#!/usr/bin/env bash\nexit 0\n",
}


class SimResult:
    def __init__(self, rc: int, out: str, calls: list[list[str]], render: str | None, keys: list[str], service: bool):
        self.rc, self.out, self.calls, self.render, self.keys, self.service = rc, out, calls, render, keys, service

    def gcloud(self) -> list[list[str]]:
        return [c[2:] for c in self.calls if c[1] == "gcloud"]

    def index(self, predicate) -> list[int]:
        return [i for i, c in enumerate(self.calls) if predicate(c)]

    def tail(self) -> str:
        return self.out[-700:]


def sim_block(name: str, *, project: str = SIM_PROJECT, project_name: str = "Raio-X", firebase: bool = False,
              candidate: str = "", service: bool = False, token: str = SIM_OLD_TOKEN, accounts=(), keys=(),
              key_json_id: str | None = None, conferir_rc: int = 0, policy: str = '{"bindings": []}',
              rev_parse: str | None = None) -> SimResult:
    """Roda um bloco do guia como o dono cola (bash lendo o bloco), com executáveis falsos na frente do PATH."""
    bash = shutil.which("bash")
    assert bash, "bash ausente"
    body = guide_blocks(_guide_text()).get(name)
    assert body, f"bloco {name} ausente"
    tmp = Path(tempfile.mkdtemp(prefix="ponte-sim-"))
    try:
        home, state, bin_dir = tmp / "home", tmp / "state", tmp / "bin"
        for folder in (home, state, bin_dir):
            folder.mkdir()
        for tool, script in FAKE_BIN.items():
            path = bin_dir / tool
            path.write_bytes(script.encode("utf-8"))
            path.chmod(0o755)
        if service:
            (state / "service").write_bytes(b"")
            (state / "token").write_bytes(token.encode("ascii"))
        for account in accounts:
            (state / f"sa-{account}").write_bytes(b"")
        (state / "keys").write_bytes("".join(k + "\n" for k in keys).encode("ascii"))
        (state / "next-key").write_bytes(SIM_NEW_KEY.encode("ascii"))
        if key_json_id:
            (home / ".raio-x-ponte").mkdir()
            (home / ".raio-x-ponte" / "chave.json").write_bytes(json.dumps(
                {"private_key_id": key_json_id, "private_key": f"-----BEGIN PRIVATE KEY-----\n{SIM_SECRET}\n"}).encode("ascii"))
        log = tmp / "calls.log"
        log.write_bytes(b"")
        env = {k: v for k, v in os.environ.items()
               if k not in ("GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", br_bridge.ENV_URL, br_bridge.ENV_TOKEN, br_bridge.ENV_KEY)}
        env.update(HOME=home.as_posix(), PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), SIM_LOG=log.as_posix(),
                   SIM_STATE=state.as_posix(), SIM_PYTHON=Path(sys.executable).as_posix(), SIM_CONFIG_PROJECT=project,
                   SIM_PROJECT_NAME=project_name, SIM_FIREBASE="1" if firebase else "0", SIM_CANDIDATE=candidate,
                   SIM_POLICY=policy, SIM_CONFERIR_RC=str(conferir_rc), SIM_NEW_TOKEN=SIM_NEW_TOKEN,
                   SIM_REV_PARSE=reviewed_code_hashes() if rev_parse is None else rev_parse)
        # umask 022 antes de colar: a trava "umask 077" do bloco tem de ser dele, não herdada do ambiente do gate
        proc = subprocess.run([bash], input=("umask 022\n" + body).encode("utf-8"), capture_output=True, env=env, timeout=240)
        calls = [line.split("\x1f") for line in log.read_bytes().decode("utf-8").splitlines() if line]
        render_path = home / "raio-x-chave-render.txt"
        render = render_path.read_bytes().decode("ascii") if render_path.exists() else None
        left = [k for k in (state / "keys").read_bytes().decode("ascii").split() if k]
        return SimResult(proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace"), calls, render, left,
                         (state / "service").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_LOCK_ONLY = {("config", "get-value"), ("projects", "describe"), ("projects", "list"), ("services", "list")}
_READ_ONLY = (("config", "get-value"), ("projects", "describe"), ("projects", "list"), ("services", "list"),
              ("run", "services", "describe"), ("run", "services", "list"), ("run", "services", "get-iam-policy"),
              ("iam", "service-accounts", "describe"), ("iam", "service-accounts", "keys", "list"))


def _flag(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _read_only(args) -> bool:
    return any(tuple(args[:len(prefix)]) == prefix for prefix in _READ_ONLY)


def _gcloud_project(args):
    if tuple(args[:2]) in (("config", "get-value"), ("projects", "list")):
        return None
    if tuple(args[:2]) in (("projects", "describe"), ("projects", "add-iam-policy-binding")):
        return args[2]
    return _flag(args, "--project") or ""


def _assert_on_project(r: SimResult, label: str):
    for args in r.gcloud():
        got = _gcloud_project(args)
        assert got is None or got == SIM_PROJECT, (label, "chamada do gcloud fora do projeto conferido", args)


def _bindings(r: SimResult):
    found = set()
    for args in r.gcloud():
        if "add-iam-policy-binding" in args:
            i = args.index("add-iam-policy-binding")
            found.add((" ".join(args[:i]), args[i + 1], _flag(args, "--member"), _flag(args, "--role")))
    return found


def _assert_secrets_hidden(r: SimResult, label: str, tokens=(SIM_OLD_TOKEN, SIM_NEW_TOKEN)):
    assert SIM_SECRET not in r.out, (label, "chave impressa na tela")
    if r.render is not None:   # a chave está mesmo no caminho (controle do próprio detector)
        assert SIM_SECRET in base64.b64decode(r.render.strip()).decode("utf-8"), (label, "arquivo do Render sem a chave")
        assert r.render.strip()[:48] not in r.out, (label, "chave em base64 impressa na tela")
    for token in tokens:
        shown = [ln for ln in r.out.splitlines() if token in ln]
        assert all(ln.startswith("RX_PONTE_BRASIL_TOKEN = ") for ln in shown), (label, "token fora da caixa do Render", shown)


def _assert_stopped(r: SimResult, label: str, *, lock_only=True):
    assert r.rc != 0 and "PAROU:" in r.out, (label, r.rc, r.tail())
    assert r.gcloud() and r.gcloud()[0][:2] == ["config", "get-value"], (label, "gcloud falso não foi chamado", r.calls)
    for args in r.gcloud():
        if lock_only:
            assert tuple(args[:2]) in _LOCK_ONLY, (label, "agiu antes de conferir o projeto", args)
        else:
            assert _read_only(args), (label, "mudou algo no Google antes de parar", args)
    assert not any(c[1] in ("conferir", "cloudshell") for c in r.calls), (label, r.calls)
    assert "COPIE PARA O RENDER" not in r.out, label


def g_sim_ponte():
    """Bloco PONTE: projeto, permissões (papel, recurso, membro), publicação, umask, conferência, avisos, segredo."""
    conferir_path = "/raio-x-ponte/scripts/ponte_brasil_conferir.py"

    def deploy_of(r):
        deploys = [a for a in r.gcloud() if a[:2] == ["run", "deploy"]]
        assert len(deploys) == 1, deploys
        return deploys[0]

    def conferir_calls(r):
        return [c for c in r.calls if c[1] == "conferir"]

    # 1. primeira vez: tudo novo
    r = sim_block("PONTE")
    assert r.rc == 0 and "COPIE PARA O RENDER" in r.out, ("primeira vez", r.rc, r.tail())
    assert len(r.gcloud()) >= 15, ("simulação não rodou o bloco", len(r.gcloud()))
    _assert_on_project(r, "primeira vez")
    assert _bindings(r) == {("projects", SIM_PROJECT, SIM_BUILD_MEMBER, "roles/run.builder"),
                            ("run services", "ponte-brasil", SIM_RENDER_MEMBER, "roles/run.invoker")}, _bindings(r)
    d = deploy_of(r)
    assert d[2] == "ponte-brasil" and "--no-allow-unauthenticated" in d, d
    assert (_flag(d, "--service-account"), _flag(d, "--max-instances"), _flag(d, "--region")) == (SIM_RUNTIME, "1", "southamerica-east1"), d
    assert not [a for a in d if ("allow-unauthenticated" in a and a != "--no-allow-unauthenticated") or "invoker-iam" in a], d
    assert _flag(d, "--set-env-vars") == f"PONTE_TOKEN={SIM_NEW_TOKEN}", d
    created = [a[3] for a in r.gcloud() if a[:3] == ["iam", "service-accounts", "create"]]
    assert created == ["ponte-runtime", "ponte-render"], created
    umasks = [c[0] for c in r.calls if c[1] == "gcloud" and c[2:6] == ["iam", "service-accounts", "keys", "create"]]
    assert umasks == ["0077"], ("chave criada sem umask 077", umasks)
    conf = conferir_calls(r)
    assert len(conf) == 1 and conf[0][2].endswith(conferir_path) and conf[0][3:] == [
        "--esperar", "480", f"url={SIM_URL}", f"token={SIM_NEW_TOKEN}", "chave=presente"], conf
    assert "O TOKEN E NOVO" in r.out and "A CHAVE E NOVA" in r.out and "IGUAIS AOS DE ANTES" not in r.out, r.tail()
    assert any(c[1] == "cloudshell" and c[-1].endswith("raio-x-chave-render.txt") for c in r.calls), r.calls
    _assert_secrets_hidden(r, "primeira vez")
    # 2. colado de novo: reaproveita token e chave e diz que não precisa mexer no Render
    r = sim_block("PONTE", service=True, accounts=("ponte-runtime", "ponte-render"), keys=("k1",), key_json_id="k1")
    assert r.rc == 0 and "TOKEN E CHAVE IGUAIS AOS DE ANTES" in r.out, ("reaproveita", r.tail())
    assert "O TOKEN E NOVO" not in r.out and "A CHAVE E NOVA" not in r.out, r.tail()
    assert _flag(deploy_of(r), "--set-env-vars") == f"PONTE_TOKEN={SIM_OLD_TOKEN}"
    assert not [a for a in r.gcloud() if a[:3] == ["iam", "service-accounts", "create"] or a[:4] == ["iam", "service-accounts", "keys", "create"]]
    _assert_on_project(r, "reaproveita")
    _assert_secrets_hidden(r, "reaproveita")
    # 3. ponte apagada, chave mantida: token novo tem de ser avisado
    r = sim_block("PONTE", accounts=("ponte-runtime", "ponte-render"), keys=("k1",), key_json_id="k1")
    assert r.rc == 0 and "O TOKEN E NOVO" in r.out and "A CHAVE E NOVA" not in r.out, ("token novo sem aviso", r.tail())
    assert "IGUAIS AOS DE ANTES" not in r.out and _flag(deploy_of(r), "--set-env-vars") == f"PONTE_TOKEN={SIM_NEW_TOKEN}"
    # 4. chaves de tentativa anterior: apagadas só depois da conferência, nunca a nova
    r = sim_block("PONTE", accounts=("ponte-runtime", "ponte-render"), keys=("k0", "k9"))
    assert r.rc == 0 and r.keys == [SIM_NEW_KEY], ("chaves antigas", r.keys, r.tail())
    deletes = r.index(lambda c: c[1] == "gcloud" and c[2:6] == ["iam", "service-accounts", "keys", "delete"])
    conf = r.index(lambda c: c[1] == "conferir")
    assert len(deletes) == 2 and len(conf) == 1 and conf[0] < min(deletes), ("apagou chave antes de conferir", deletes, conf)
    # 5. conferência falhou: para, não apaga chave, não mostra a caixa, não baixa
    r = sim_block("PONTE", accounts=("ponte-runtime", "ponte-render"), keys=("k0",), conferir_rc=1)
    assert r.rc != 0 and "COPIE PARA O RENDER" not in r.out, ("seguiu depois da conferência falhar", r.rc, r.tail())
    assert "k0" in r.keys and not any(c[1] == "cloudshell" for c in r.calls), (r.keys, r.calls)
    # 6. ponte continua aberta ao público: para antes de conta, chave e conferência
    r = sim_block("PONTE", policy=SIM_OPEN_POLICY)
    assert r.rc != 0 and "PAROU: a ponte continua aberta" in r.out, ("ponte aberta", r.tail())
    assert not [a for a in r.gcloud() if a[:4] == ["iam", "service-accounts", "keys", "create"] or "ponte-render" in a]
    assert not conferir_calls(r) and "COPIE PARA O RENDER" not in r.out


def g_sim_travas():
    """Os quatro blocos param no projeto errado (AFP, outro, Raio-X com Firebase, nenhum) sem agir; PONTE e CONFERE
    param com código diferente do revisado."""
    cases = (   # "outro projeto sem Firebase" primeiro: sem a trava do nome, é ele que prova que o bloco agiria
        ("outro projeto sem Firebase", dict(project="meu-outro-projeto", project_name="Outro")),
        ("projeto do AFP", dict(project="metodo-afp-prod", project_name="Metodo AFP", firebase=True, candidate=SIM_PROJECT)),
        ("nome Raio-X com Firebase", dict(firebase=True)),
        ("nenhum projeto", dict(project="")),
    )
    for block in ("PONTE", "CONFERE", "TROCA", "PARAR"):
        for label, kwargs in cases:
            r = sim_block(block, service=True, accounts=("ponte-runtime", "ponte-render"), keys=("k1",), key_json_id="k1", **kwargs)
            _assert_stopped(r, f"{block}: {label}")
            if kwargs.get("project"):
                assert ["projects", "describe", kwargs["project"], "--format=value(name)"] in r.gcloud(), (block, label, r.gcloud())
            if kwargs.get("candidate"):
                assert f"gcloud config set project {SIM_PROJECT}" in r.out, (block, label, r.tail())
    wrong = " ".join(["0" * 40] * 3)
    r = sim_block("PONTE", rev_parse=wrong)
    _assert_stopped(r, "PONTE: código diferente do revisado")
    assert any(c[1] == "git" and c[2] == "clone" for c in r.calls), r.calls
    r = sim_block("CONFERE", service=True, key_json_id="k1", rev_parse=wrong)
    _assert_stopped(r, "CONFERE: código diferente do revisado", lock_only=False)


def g_sim_outros():
    """CONFERE só lê; TROCA troca token e chave e apaga as antigas; PARAR apaga só a ponte do projeto conferido."""
    r = sim_block("CONFERE", service=True, key_json_id="k1")
    assert r.rc == 0, ("CONFERE", r.tail())
    assert all(_read_only(a) for a in r.gcloud()), [a for a in r.gcloud() if not _read_only(a)]
    conf = [c for c in r.calls if c[1] == "conferir"]
    assert len(conf) == 1 and conf[0][3:] == ["--esperar", "0", f"url={SIM_URL}", f"token={SIM_OLD_TOKEN}", "chave=presente"], conf
    _assert_on_project(r, "CONFERE")
    _assert_secrets_hidden(r, "CONFERE")
    r = sim_block("TROCA", service=True, accounts=("ponte-render",), keys=("k0", "k1"))
    assert r.rc == 0 and r.keys == [SIM_NEW_KEY], ("TROCA", r.keys, r.tail())
    updates = [a for a in r.gcloud() if a[:3] == ["run", "services", "update"]]
    assert len(updates) == 1 and _flag(updates[0], "--update-env-vars") == f"PONTE_TOKEN={SIM_NEW_TOKEN}", updates
    created = r.index(lambda c: c[1] == "gcloud" and c[2:6] == ["iam", "service-accounts", "keys", "create"])
    deletes = r.index(lambda c: c[1] == "gcloud" and c[2:6] == ["iam", "service-accounts", "keys", "delete"])
    assert len(created) == 1 and len(deletes) == 2 and created[0] < min(deletes), (created, deletes)
    assert r.calls[created[0]][0] == "0077", ("TROCA sem umask 077", r.calls[created[0]][0])
    assert "O TOKEN E NOVO" in r.out and "A CHAVE E NOVA" in r.out and f"RX_PONTE_BRASIL_TOKEN = {SIM_NEW_TOKEN}" in r.out, r.tail()
    _assert_on_project(r, "TROCA")
    _assert_secrets_hidden(r, "TROCA")
    r = sim_block("TROCA", accounts=("ponte-render",), keys=("k0",))
    assert r.rc == 0 and r.keys == [SIM_NEW_KEY] and "RX_PONTE_BRASIL_TOKEN =" not in r.out and "O TOKEN E NOVO" not in r.out, r.tail()
    assert not [a for a in r.gcloud() if a[:3] == ["run", "services", "update"]]
    r = sim_block("PARAR", service=True)
    deletes = [a for a in r.gcloud() if a[:3] == ["run", "services", "delete"]]
    assert r.rc == 0 and "PONTE APAGADA" in r.out and not r.service, ("PARAR", r.tail())
    assert deletes == [["run", "services", "delete", "ponte-brasil", "--region", "southamerica-east1", "--project", SIM_PROJECT,
                        "--quiet"]], deletes
    _assert_on_project(r, "PARAR")
    r = sim_block("PARAR")
    assert r.rc == 0 and "ja nao existe" in r.out and not [a for a in r.gcloud() if a[:3] == ["run", "services", "delete"]], r.tail()


@contextlib.contextmanager
def guide_block_mutant(block: str, transform):
    """Muda um trecho de um bloco do guia (como numa revisão) e roda a simulação contra o guia mudado."""
    text = GUIDE.read_text(encoding="utf-8")
    body = guide_blocks(text).get(block, "")
    new_body = transform(body)
    if not body or new_body == body:
        raise MutationTargetMissing(f"{block}: mutação não mudou nada")
    _GUIDE_OVERRIDE["text"] = text.replace(body, new_body)
    try:
        yield
    finally:
        _GUIDE_OVERRIDE.pop("text", None)


def _guide_mut(block: str, old: str, new: str):
    def transform(body: str) -> str:
        if body.count(old) != 1:
            raise MutationTargetMissing(f"{block}: trecho aparece {body.count(old)}x: {old.strip()[:70]!r}")
        return body.replace(old, new)
    return lambda: guide_block_mutant(block, transform)


def _delete_keys_before_check(body: str) -> str:
    match = re.search(r'if \[ "\$NOVA" = 1 \]; then\n  NOVO_ID=.*?\nfi\n', body, re.S)
    line = 'python3 "$CODIGO/scripts/ponte_brasil_conferir.py" --esperar 480 || exit 1\n'
    if not match or body.count(line) != 1:
        raise MutationTargetMissing("PONTE: trecho de apagar chaves ou conferência não encontrado")
    moved = body.replace(match.group(0), "")
    return moved.replace(line, match.group(0) + line)


_LOCK_PATTERN = "  raio-x*|*'|raio-x'*) ;;\n"
_BRIDGE_FILE_OVERRIDE: dict[str, str] = {}


@contextlib.contextmanager
def bridge_file_mutant(old: str, new: str):
    """Muda o br_bridge.py como lido por uma verificação que o carrega do zero (arranque)."""
    _BRIDGE_FILE_OVERRIDE["src"] = _mutated_source(ROOT / "br_bridge.py", old, new)
    try:
        yield
    finally:
        _BRIDGE_FILE_OVERRIDE.pop("src", None)


def _open_repinned(svc, host, ips, deadline):
    conn = svc.connector(host, host, ponte._remaining(deadline, svc.clock))
    conn.connect()
    return conn


def _bridge_request_token_in_argv(args, cfg, *, budget_s=None, id_token=None):
    out, stdin = _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=budget_s, id_token=id_token)
    return out + ["-H", f"X-Ponte-Token: {cfg.token}"], stdin


def _bridge_request_no_budget(args, cfg, *, budget_s=None, id_token=None):
    return _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=None, id_token=id_token)


def _bridge_request_no_fail(args, cfg, *, budget_s=None, id_token=None):
    out, stdin = _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=budget_s, id_token=id_token)
    return [a for a in out if a != "--fail"], stdin


def _bridge_request_id_in_argv(args, cfg, *, budget_s=None, id_token=None):
    out, stdin = _ORIG_BRIDGE_REQUEST(args, cfg, budget_s=budget_s, id_token=id_token)
    return (out + ["-H", f"{br_bridge.ID_TOKEN_HEADER}: Bearer {id_token}"] if id_token else out), stdin


_ORIG_BRIDGE_REQUEST = br_bridge.bridge_request


class MutationTargetMissing(RuntimeError):
    """O trecho a mutar não existe (ou não é único): erro da mutação, nunca "mutante morto"."""


def _mutated_source(path: Path, old: str, new: str) -> str:
    src = path.read_text(encoding="utf-8")
    if src.count(old) != 1:
        raise MutationTargetMissing(f"{path.name}: trecho aparece {src.count(old)}x: {old.strip()[:70]!r}")
    return src.replace(old, new)


@contextlib.contextmanager
def ponte_source_mutant(old: str, new: str):
    """Troca uma linha do app.py da ponte (mutação de código, como numa revisão) e roda a verificação contra ela."""
    global ponte
    path = ROOT / "ponte_brasil" / "app.py"
    name = "ponte_brasil_app_mutante"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    original = ponte
    try:
        exec(compile(_mutated_source(path, old, new), str(path), "exec"), module.__dict__)
        ponte = module
        yield
    finally:
        ponte = original
        sys.modules.pop(name, None)


@contextlib.contextmanager
def bridge_source_mutant(old: str, new: str):
    """Mesma ideia no br_bridge, no próprio módulo (os transportes o chamam por br_bridge.run_curl)."""
    saved = dict(br_bridge.__dict__)
    try:
        code = compile(_mutated_source(ROOT / "br_bridge.py", old, new), str(ROOT / "br_bridge.py"), "exec")
        with contextlib.redirect_stdout(io.StringIO()):
            exec(code, br_bridge.__dict__)
        yield
    finally:
        br_bridge.__dict__.clear()
        br_bridge.__dict__.update(saved)


@contextlib.contextmanager
def conferir_source_mutant(old: str, new: str):
    global CONFERIR
    path = ROOT / "scripts" / "ponte_brasil_conferir.py"
    module = types.ModuleType("ponte_brasil_conferir_mutante")
    module.__file__ = str(path)
    original = CONFERIR
    try:
        code = compile(_mutated_source(path, old, new), str(path), "exec")
        exec(code, module.__dict__)
        CONFERIR = module
        yield
    finally:
        CONFERIR = original


def _conferir_mut(old, new):
    return lambda: conferir_source_mutant(old, new)


def _ponte_mut(old, new):
    return lambda: ponte_source_mutant(old, new)


def _bridge_mut(old, new):
    return lambda: bridge_source_mutant(old, new)


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
    # mutações de código (revisão de 14/09/2026: estas sobreviviam ao gate anterior)
    ("busca não libera a vaga", _ponte_mut("                svc.inflight.release()\n", "                pass\n"), s_inflight_release),
    ("SICAR sem a lista de cifras RSA", _ponte_mut("        ctx.set_ciphers(RSA_KEX_CIPHERS)\n", "        pass\n"), s_tls_and_source),
    ("INCRA com a concessão do SICAR", _ponte_mut("return _TLS_RSA_KEX if host in RSA_KEX_TLS_HOSTS else _TLS_DEFAULT",
                                                   "return _TLS_RSA_KEX"), s_tls_and_source),
    ("X-Ponte-Timeout sem teto", _ponte_mut("return max(1.0, min(limit, asked))", "return max(1.0, asked)"), s_timeout),
    ("Content-Type do cliente sem limpeza", _ponte_mut('clean_content_type(get("Content-Type"))', 'get("Content-Type")'),
     s_content_type),
    ("POST mantido após 302", _ponte_mut('if resp.status in (301, 302, 303) and method != "GET":',
                                         'if resp.status == 303 and method != "GET":'), s_redirect_post),
    ("urlsplit malformado vira 500", _ponte_mut('    except ValueError:\n        raise Refused(400, "target_malformed") from None',
                                                '    except KeyError:\n        raise Refused(400, "target_malformed") from None'),
     s_malformed),
    ("Content-Length não ASCII vira 500", _ponte_mut("if not (raw.isascii() and raw.isdigit()):", "if not raw.isdigit():"),
     s_malformed),
    ("sem prazo total para ler o pedido", _ponte_mut("        self._intake_timer.start()\n", "        pass\n"), s_slow_client),
    ("sem teto de conexões", _ponte_mut("threading.BoundedSemaphore(service.max_connections)", "threading.BoundedSemaphore(10 ** 6)"),
     s_connection_cap),
    ("erro da ponte devolvido com o endereço dela",
     _bridge_mut("    return subprocess.CompletedProcess(list(args), proc.returncode, proc.stdout, scrub(proc.stderr))\n",
                 "    return proc\n"), c_no_bridge_url_leak),
    ("TimeoutExpired com a linha de comando da ponte",
     _bridge_mut("        raise subprocess.TimeoutExpired(list(args), exc.timeout, output=exc.output, stderr=scrub(exc.stderr)) from None\n",
                 "        raise\n"), c_no_bridge_url_leak),
    ("janela fecha com qualquer falha pela ponte", lambda: mock.patch.object(br_bridge, "bridge_itself_failed", lambda proc: True),
     c_sicar_window_rule),
    ("janela nunca fecha", lambda: mock.patch.object(br_bridge, "bridge_itself_failed", lambda proc: False), c_sicar_window_rule),
    ("URL com CR/LF pela ponte", lambda: mock.patch.object(br_bridge, "url_has_forbidden_chars", lambda url: False), c_config),
    ("probe mede o INCRA por httpx com a ponte ligada",
     lambda: mock.patch.object(br_bridge, "route_for", lambda url, env=None: br_bridge.ROUTE_DIRECT), c_probe_sources),
    # ponte fechada pelo Google (15/09/2026)
    ("sem o cabeçalho X-Serverless-Authorization",
     _bridge_mut('        headers += f"{ID_TOKEN_HEADER}: Bearer {id_token}\\r\\n"\n', "        pass\n"), i_incra_identity),
    ("token de identidade na linha de comando", lambda: mock.patch.object(br_bridge, "bridge_request", _bridge_request_id_in_argv),
     i_incra_identity),
    ("uma troca de credencial por consulta", lambda: mock.patch.object(br_bridge, "ID_TOKEN_REFRESH_MARGIN_S", 10.0 ** 9),
     i_incra_identity),
    ("token perto de vencer nunca renova", lambda: mock.patch.object(br_bridge, "ID_TOKEN_REFRESH_MARGIN_S", 0.0), i_incra_identity),
    ("chave estragada vira ponte só com token",
     _bridge_mut("            return None   # chave colada pela metade: desligada e dito no log, nunca meio ligada\n",
                 "            identity = None\n"), i_key_parse_and_signature),
    ("chave com números incoerentes aceita",
     _bridge_mut("    if dp != d % (p - 1) or dq != d % (q - 1) or (qinv * q) % p != 1 or pow(pow(2, e, n), d, n) != 2:\n"
                 "        raise ValueError(\"rsa_incoerente\")\n", ""), i_key_parse_and_signature),
    ("token_uri de outro endereço aceito",
     _bridge_mut("        if data.get(\"token_uri\") not in (None, GOOGLE_TOKEN_URI):\n", "        if False:\n"),
     i_key_parse_and_signature),
    ("assinatura em Python puro com o algoritmo errado",
     lambda: mock.patch.object(br_bridge, "_SHA256_DIGEST_INFO", bytes.fromhex("3041300d060960864801650304020205000430")),
     i_key_parse_and_signature),
    ("JWT sem target_audience", _bridge_mut('"exp": now + ASSERTION_LIFETIME_S, "target_audience": audience}',
                                             '"exp": now + ASSERTION_LIFETIME_S}'), i_assertion),
    ("token de identidade de outro serviço aceito",
     _bridge_mut('    if claims.get("aud") != audience or exp - now < ID_TOKEN_MIN_LEFT_S:\n',
                 "    if exp - now < ID_TOKEN_MIN_LEFT_S:\n"), i_assertion),
    ("INCRA pela ponte sem credencial",
     _bridge_mut("                return credential_failure(args)\n            budget = remaining_budget(timeout_seconds, started)\n",
                 "                pass\n            budget = remaining_budget(timeout_seconds, started)\n"), i_credential_failures),
    ("insiste na troca recusada", lambda: mock.patch.object(br_bridge, "ID_TOKEN_RETRY_AFTER_S", -1.0), i_credential_failures),
    ("espera pela credencial sem limite", lambda: mock.patch.object(br_bridge, "_credential_wait", lambda t, s: 30.0),
     i_credential_failures),
    ("SICAR pela ponte sem credencial após queda direta",
     _bridge_mut("        if token is None:\n            if proc is not None:\n",
                 "        if False:\n            if proc is not None:\n"), i_sicar_identity),
    ("SICAR devolve falha inventada em vez da falha direta",
     _bridge_mut("                return proc                 # a falha direta é a resposta honesta\n", "                pass\n"),
     i_sicar_identity),
    ("conferência aceita ponte aberta", _conferir_mut("    if closed == 200:\n        print(\"PAROU: a ponte continua aberta",
                                                      "    if False:\n        print(\"PAROU: a ponte continua aberta"), i_conferir),
    ("conferência imprime segredo", _conferir_mut('print(f"  chave: lida (assinatura {cfg.identity.signer})", flush=True)',
                                                  'print(f"  chave: lida {cfg.token} {cfg.audience}", flush=True)'), i_conferir),
    # revisão de 15/09/2026: teto de saída, prazo depois da credencial, arranque, 401, guia rodado com gcloud falso
    ("saída sem teto por hora", lambda: mock.patch.object(ponte.EgressBudget, "take", lambda self, n: True), s_egress_budget),
    ("memória da resposta não devolvida", _ponte_mut("                svc.budget.release(upstream.reserved)\n", "                pass\n"),
     s_egress_budget),
    ("teto de saída não conferido na resposta", _ponte_mut("            if not svc.egress.take(len(upstream.body)):\n",
                                                           "            if False:\n"), s_egress_budget),
    ("balde de saída sem tamanho máximo", _ponte_mut("self.available = min(self.capacity, self.available + max(0.0, now - self.last) * self.rate)",
                                                     "self.available = self.available + max(0.0, now - self.last) * self.rate"),
     s_egress_budget),
    ("verificação sem esperar o servidor terminar o pedido", lambda: mock.patch.object(PonteEnv, "settle", lambda self, timeout_s=5.0: None),
     s_settle_after_send),
    ("teto de saída de 1 TiB", _ponte_mut("EGRESS_BYTES_PER_HOUR = 1024 ** 3", "EGRESS_BYTES_PER_HOUR = 1024 ** 4"), s_egress_budget),
    ("SICAR pela ponte com menos que o prazo mínimo depois da credencial",
     _bridge_mut("        if budget is not None and budget < MIN_BRIDGE_BUDGET_S:\n"
                 "            return proc if proc is not None else credential_failure(args)\n", ""),
     i_sicar_budget_after_credential),
    ("arranque sem prewarm", lambda: bridge_file_mutant("\nprewarm()\n", "\n"), i_prewarm),
    ("conferência aceita 401 sem credencial como fechada",
     _conferir_mut("    if closed != 403 or healthy != 200:\n", "    if closed not in (401, 403) or healthy != 200:\n"), i_conferir),
    ("guia: CODIGO_REVISADO desatualizado", lambda: mock.patch.object(
        sys.modules[__name__], "_pin_read", lambda rel: _ORIG_PIN_READ(rel) + (b"#" if rel == "ponte_brasil/app.py" else b"")),
     g_code_pin),
    ("guia: run.invoker dado no PROJETO",
     _guide_mut("PONTE", 'gcloud run services add-iam-policy-binding "$SERVICO" --region "$REGIAO" --project "$PROJETO" \\\n'
                         '       --member="serviceAccount:$EMAIL"',
                'gcloud projects add-iam-policy-binding "$PROJETO" \\\n       --member="serviceAccount:$EMAIL"'), g_sim_ponte),
    ("guia: roles/editor extra para ponte-render",
     _guide_mut("PONTE", '[ "$OK" = 1 ] || parou "o Google nao deu a permissao de chamar a ponte.',
                'gcloud projects add-iam-policy-binding "$PROJETO" --member="serviceAccount:$EMAIL" --role=roles/editor '
                '--condition=None --quiet >/dev/null\n[ "$OK" = 1 ] || parou "o Google nao deu a permissao de chamar a ponte.'),
     g_sim_ponte),
    ("guia: chave criada sem umask 077", _guide_mut("PONTE", "umask 077\n", ""), g_sim_ponte),
    ("guia: TROCA sem umask 077", _guide_mut("TROCA", "umask 077\n", ""), g_sim_outros),
    ("guia: sem conferir allUsers depois de publicar",
     _guide_mut("PONTE", 'case "$POLITICA" in\n  *\'"allUsers"\'*|*\'"allAuthenticatedUsers"\'*) parou "a ponte continua aberta ao publico. '
                         'NAO coloque nada no Render." ;;\nesac\n', ""), g_sim_ponte),
    ("guia: publicação com --no-invoker-iam-check",
     _guide_mut("PONTE", "--memory 256Mi --no-allow-unauthenticated", "--memory 256Mi --no-allow-unauthenticated --no-invoker-iam-check"),
     g_sim_ponte),
    ("guia: ponte roda com a conta padrão", _guide_mut("PONTE", '  --service-account "$RUNTIME" ', "  "), g_sim_ponte),
    ("guia: publicação sem --project",
     _guide_mut("PONTE", '--source "$CODIGO/ponte_brasil" --region "$REGIAO" --project "$PROJETO"',
                '--source "$CODIGO/ponte_brasil" --region "$REGIAO"'), g_sim_ponte),
    ("guia: chave impressa com printf",
     _guide_mut("PONTE", 'export RX_PONTE_BRASIL_CHAVE\npython3 "$CODIGO',
                'export RX_PONTE_BRASIL_CHAVE\nprintf \'%s\\n\' "$RX_PONTE_BRASIL_CHAVE"\npython3 "$CODIGO'), g_sim_ponte),
    ("guia: conferência com || true", _guide_mut("PONTE", "--esperar 480 || exit 1", "--esperar 480 || true"), g_sim_ponte),
    ("guia: chaves apagadas antes da conferência", lambda: guide_block_mutant("PONTE", _delete_keys_before_check), g_sim_ponte),
    ("guia: token novo sem aviso", _guide_mut("PONTE", 'if [ "$TOKEN_NOVO" = 1 ]; then echo "O TOKEN E NOVO', 'if false; then echo "O TOKEN E NOVO'),
     g_sim_ponte),
    ("guia: TROCA sem apagar as chaves antigas", _guide_mut("TROCA", "for K in $ANTIGAS; do", "for K in; do"), g_sim_outros),
    ("guia: PARAR sem --project no apagar",
     _guide_mut("PARAR", 'gcloud run services delete "$SERVICO" --region "$REGIAO" --project "$PROJETO" --quiet',
                'gcloud run services delete "$SERVICO" --region "$REGIAO" --quiet'), g_sim_outros),
    ("guia: PONTE sem trava do nome do projeto", _guide_mut("PONTE", _LOCK_PATTERN, "  *) ;;\n"), g_sim_travas),
    ("guia: CONFERE sem trava do nome do projeto", _guide_mut("CONFERE", _LOCK_PATTERN, "  *) ;;\n"), g_sim_travas),
    ("guia: TROCA sem trava do nome do projeto", _guide_mut("TROCA", _LOCK_PATTERN, "  *) ;;\n"), g_sim_travas),
    ("guia: PARAR sem trava do nome do projeto", _guide_mut("PARAR", _LOCK_PATTERN, "  *) ;;\n"), g_sim_travas),
    ("guia: PONTE sem trava do Firebase", _guide_mut("PONTE", '[ -z "$FIREBASE" ] || parou', 'true || parou'), g_sim_travas),
    ("guia: PONTE sem trava do código revisado", _guide_mut("PONTE", '[ "$ACHADO" = "$CODIGO_REVISADO " ]', "true"), g_sim_travas),
    ("guia: CONFERE sem trava do código revisado", _guide_mut("CONFERE", '[ "$ACHADO" = "$CODIGO_REVISADO " ]', "true"), g_sim_travas),
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
        s_rate_limit, s_egress_budget, s_settle_after_send, s_log_hygiene, s_upstream_status, s_tls_and_source,
        s_inflight_release, s_malformed, s_content_type, s_redirect_post, s_slow_client, s_connection_cap,
        c_off_identical, c_incra_bridge, c_sicar_direct_then_bridge, c_sicar_budget_and_codes, c_sicar_window_rule,
        c_cancel_and_timeout, c_no_bridge_url_leak, c_probe_sources,
        c_config, c_real_curl, c_hosts_and_transports,
        i_key_parse_and_signature, i_assertion, i_incra_identity, i_sicar_identity, i_credential_failures,
        i_sicar_budget_after_credential, i_prewarm, i_real_curl_identity, i_conferir, g_guide, g_code_pin,
        g_sim_travas, g_sim_ponte, g_sim_outros,
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

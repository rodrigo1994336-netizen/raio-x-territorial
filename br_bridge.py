"""Ponte no Brasil: o cliente único do Raio-X.

O acervo fundiário do INCRA (e às vezes o SICAR) não responde a chamadas vindas do Render (EUA).
Quando RX_PONTE_BRASIL_URL (https) e RX_PONTE_BRASIL_TOKEN estão configuradas, as chamadas curl
para esses hosts passam pela ponte (ponte_brasil/app.py, Cloud Run em São Paulo). Sem as duas
variáveis válidas, run_curl chama o executor com exatamente os mesmos argumentos de antes.

Ponte fechada pelo Google (15/09/2026): com RX_PONTE_BRASIL_CHAVE (chave JSON de uma conta de serviço,
o JSON ou o JSON em base64), cada chamada à ponte leva também um token de identidade do Google no
cabeçalho X-Serverless-Authorization. Aí a ponte roda com --no-allow-unauthenticated e o próprio
Cloud Run recusa, antes do contêiner, quem não tem credencial do Google; a documentação de preços do
Cloud Run diz que pedido recusado pelo IAM não é cobrado. Sem a chave, tudo continua como antes
(só X-Ponte-Token). O Render não tem identidade do Google, por isso a chave: o JWT é assinado aqui
(RS256, com a biblioteca cryptography quando existe; senão em Python puro, com a mesma assinatura
byte a byte) e trocado em https://oauth2.googleapis.com/token pelo token de identidade (1 hora).
A troca roda numa linha de execução à parte e o token fica em cache; a consulta espera por ele no
máximo o que sobra do próprio prazo menos MIN_BRIDGE_BUDGET_S. Sem token, a consulta falha (consulta
pendente), nunca vira "não encontrado".

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

Segredo: o token (e o token de identidade) vai para o curl pela entrada padrão (-H @-), nunca na linha
de comando (visível para outros processos) nem em log; a chave nunca sai deste processo, só a
assinatura. Pela ponte o curl sempre verifica o certificado (-k removido) e usa --fail: erro da ponte
ou da fonte vira falha (consulta pendente), nunca "não encontrado".

Endereço da ponte: nunca sai no resultado. O curl escreve o host no erro (códigos 6, 7, 35) e o
TimeoutExpired carrega a linha de comando; os transportes põem esse texto em "detail", que chega ao
navegador. Por isso o resultado devolvido traz os argumentos originais (endereço oficial) e o erro
com o endereço da ponte trocado pelo oficial.
"""
from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

ENV_URL = "RX_PONTE_BRASIL_URL"
ENV_TOKEN = "RX_PONTE_BRASIL_TOKEN"
ENV_KEY = "RX_PONTE_BRASIL_CHAVE"
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

# Ponte fechada pelo Google (conta de serviço)
ID_TOKEN_HEADER = "X-Serverless-Authorization"   # o Cloud Run confere este cabeçalho antes do contêiner
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ASSERTION_LIFETIME_S = 3600
ID_TOKEN_REFRESH_MARGIN_S = 300.0   # renova 5 min antes de vencer (o do Google vale 1 hora)
ID_TOKEN_MIN_LEFT_S = 30.0          # token com menos que isto não é usado
ID_TOKEN_HTTP_TIMEOUT_S = 10.0      # troca em segundo plano; a consulta não espera por isto inteiro
ID_TOKEN_RETRY_AFTER_S = 30.0       # depois de uma troca recusada, não insiste a cada consulta
MAX_KEY_ENV_LEN = 16384
CREDENTIAL_FAILURE_EXIT = 7         # sem token de identidade: mesma classe de "não chegou à ponte"

ROUTE_DIRECT = "direct"
ROUTE_BRIDGE = "bridge"
ROUTE_DIRECT_THEN_BRIDGE = "direct_then_bridge"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
_HTTP_CODE_RE = re.compile(r"error: (\d{3})")
_MAX_TIME_OPTS = ("--max-time", "-m")
_FAIL_OPTS = ("--fail", "-f", "--fail-with-body")
_INSECURE_OPTS = ("-k", "--insecure")
_SA_EMAIL_RE = re.compile(r"^[a-z][a-z0-9-]{4,29}@[a-z0-9.:-]+\.iam\.gserviceaccount\.com$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_PEM_RE = re.compile(r"-----BEGIN (RSA )?PRIVATE KEY-----([A-Za-z0-9+/=\s]+)-----END (RSA )?PRIVATE KEY-----")
_RSA_OID = bytes.fromhex("2a864886f70d010101")
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

_now: Callable[[], float] = time.monotonic
_wall: Callable[[], float] = time.time
_lock = threading.Lock()
_skip_direct_until: dict[str, float] = {}
_last_log: dict[str, float] = {}


# ---------------------------------------------------------------------------------------------
# conta de serviço: leitura da chave e assinatura RS256
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RsaNumbers:
    n: int
    e: int
    d: int
    p: int
    q: int
    dp: int
    dq: int
    qinv: int

    def __repr__(self) -> str:       # os números privados nunca em log ou em mensagem de erro
        return f"RsaNumbers(bits={self.n.bit_length()})"


@dataclass(frozen=True)
class ServiceAccount:
    email: str
    key_id: str
    numbers: RsaNumbers
    signer: str                      # "cryptography" ou "python"
    _sign: Callable[[bytes], bytes]

    def sign(self, data: bytes) -> bytes:
        return self._sign(data)

    def __repr__(self) -> str:       # nunca a chave em log ou em mensagem de erro
        return f"ServiceAccount(email={self.email!r}, signer={self.signer!r})"


def _der_read(buf: bytes, pos: int) -> tuple[int, bytes, int]:
    if pos + 2 > len(buf):
        raise ValueError("der_curto")
    tag, length = buf[pos], buf[pos + 1]
    pos += 2
    if length & 0x80:
        size = length & 0x7F
        if size == 0 or size > 4 or pos + size > len(buf):
            raise ValueError("der_tamanho")
        length = int.from_bytes(buf[pos:pos + size], "big")
        pos += size
    end = pos + length
    if end > len(buf):
        raise ValueError("der_estourado")
    return tag, buf[pos:end], end


def _der_items(content: bytes) -> list[tuple[int, bytes]]:
    items, pos = [], 0
    while pos < len(content):
        tag, value, pos = _der_read(content, pos)
        items.append((tag, value))
    return items


def _der_sequence(buf: bytes) -> list[tuple[int, bytes]]:
    tag, content, end = _der_read(buf, 0)
    if tag != 0x30 or end != len(buf):
        raise ValueError("der_nao_sequencia")
    return _der_items(content)


def rsa_numbers_from_pem(pem: str) -> RsaNumbers:
    """PKCS#8 (a chave do Google) ou PKCS#1; confere a coerência dos números antes de aceitar."""
    match = _PEM_RE.search(pem or "")
    if not match or (match.group(1) or "") != (match.group(3) or ""):
        raise ValueError("pem_invalido")
    try:
        der = base64.b64decode("".join(match.group(2).split()), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("pem_base64") from None
    items = _der_sequence(der)
    if not match.group(1):
        if len(items) < 3 or items[0] != (0x02, b"\x00") or items[1][0] != 0x30 or items[2][0] != 0x04:
            raise ValueError("pkcs8_invalido")
        algorithm = _der_items(items[1][1])
        if not algorithm or algorithm[0] != (0x06, _RSA_OID):
            raise ValueError("chave_nao_rsa")
        items = _der_sequence(items[2][1])
    if len(items) < 9 or any(tag != 0x02 or not value or value[0] & 0x80 for tag, value in items[:9]):
        raise ValueError("pkcs1_invalido")
    version, n, e, d, p, q, dp, dq, qinv = (int.from_bytes(value, "big") for _, value in items[:9])
    if version != 0 or not 2048 <= n.bit_length() <= 4096 or p * q != n or e < 3 or e % 2 == 0 or e >= n:
        raise ValueError("rsa_incoerente")
    if dp != d % (p - 1) or dq != d % (q - 1) or (qinv * q) % p != 1 or pow(pow(2, e, n), d, n) != 2:
        raise ValueError("rsa_incoerente")
    return RsaNumbers(n, e, d, p, q, dp, dq, qinv)


def sign_rs256_python(numbers: RsaNumbers, data: bytes) -> bytes:
    """RSASSA-PKCS1-v1_5 com SHA-256 (RFC 8017, 8.2.1), com CRT e conferência da assinatura antes de usar."""
    k = (numbers.n.bit_length() + 7) // 8
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    padding_len = k - len(digest_info) - 3
    if padding_len < 8:
        raise ValueError("chave_curta")
    encoded = b"\x00\x01" + b"\xff" * padding_len + b"\x00" + digest_info
    m = int.from_bytes(encoded, "big")
    s1 = pow(m, numbers.dp, numbers.p)
    s2 = pow(m, numbers.dq, numbers.q)
    s = s2 + ((numbers.qinv * (s1 - s2)) % numbers.p) * numbers.q
    if pow(s, numbers.e, numbers.n) != m:
        raise ValueError("assinatura_incoerente")
    return s.to_bytes(k, "big")


def _cryptography_signer(pem: str) -> Callable[[bytes], bytes] | None:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except Exception:
        return None
    try:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except Exception:
        return None
    if not isinstance(key, rsa.RSAPrivateKey):
        return None
    return lambda data: key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def _decode_key_env(raw: str) -> dict:
    text = raw.strip()
    if not text.startswith("{"):
        compact = "".join(text.split())
        try:
            text = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_" if ("-" in compact or "_" in compact) else None,
                                    validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise ValueError("chave_base64") from None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("chave_json")
    return data


@functools.lru_cache(maxsize=4)
def parse_service_account(raw: str) -> ServiceAccount | None:
    """A chave JSON da conta de serviço (texto ou base64). Qualquer coisa estranha: None (chave inválida)."""
    if not raw or len(raw) > MAX_KEY_ENV_LEN:
        return None
    try:
        data = _decode_key_env(raw)
        email = str(data.get("client_email") or "")
        key_id = str(data.get("private_key_id") or "")
        pem = str(data.get("private_key") or "")
        if data.get("type") != "service_account" or not _SA_EMAIL_RE.match(email) or not _KEY_ID_RE.match(key_id):
            return None
        if data.get("token_uri") not in (None, GOOGLE_TOKEN_URI):
            return None   # a assinatura só vai para o endereço fixo do Google
        numbers = rsa_numbers_from_pem(pem)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    signer = _cryptography_signer(pem)
    if signer is None:
        return ServiceAccount(email, key_id, numbers, "python", functools.partial(sign_rs256_python, numbers))
    return ServiceAccount(email, key_id, numbers, "cryptography", signer)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signed_assertion(identity: ServiceAccount, audience: str, now: int) -> str:
    """JWT autoassinado com target_audience = endereço da ponte (o Google troca por um token de identidade)."""
    header = {"alg": "RS256", "typ": "JWT", "kid": identity.key_id}
    claims = {"iss": identity.email, "sub": identity.email, "aud": GOOGLE_TOKEN_URI, "iat": now,
              "exp": now + ASSERTION_LIFETIME_S, "target_audience": audience}
    signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                     + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    return signing_input + "." + _b64url(identity.sign(signing_input.encode("ascii")))


def _post_token(assertion: str, timeout: float) -> bytes:
    body = urllib.parse.urlencode({"grant_type": JWT_BEARER_GRANT, "assertion": assertion}).encode("ascii")
    request = urllib.request.Request(GOOGLE_TOKEN_URI, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 (endereço fixo https do Google)
        return resp.read(65536)


def parse_id_token_response(raw: bytes, audience: str, now: float) -> tuple[str, float]:
    data = json.loads(raw.decode("utf-8"))
    token = str(data.get("id_token") or "") if isinstance(data, dict) else ""
    if not _JWT_RE.match(token) or len(token) > 4096:
        raise ValueError("id_token_ausente")
    claims = json.loads(_b64url_decode(token.split(".")[1]))
    exp = float(claims.get("exp"))
    if claims.get("aud") != audience or exp - now < ID_TOKEN_MIN_LEFT_S:
        raise ValueError("id_token_incoerente")
    return token, exp


# ---------------------------------------------------------------------------------------------
# configuração
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    fetch_url: str
    token: str
    bridge_host: str
    identity: ServiceAccount | None = None

    @property
    def audience(self) -> str:
        return self.fetch_url[: -len(FETCH_PATH)]


def config(env: Mapping[str, str] | None = None) -> Config | None:
    """As duas variáveis válidas (e a chave, se ela existir válida), ou None (ponte desligada)."""
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
    raw_key = str(source.get(ENV_KEY) or "").strip()
    identity = None
    if raw_key:
        identity = parse_service_account(raw_key)
        if identity is None:
            return None   # chave colada pela metade: desligada e dito no log, nunca meio ligada
    return Config(fetch_url=base + FETCH_PATH, token=token, bridge_host=parts.hostname, identity=identity)


def status_line(env: Mapping[str, str] | None = None) -> str:
    cfg = config(env)
    source = os.environ if env is None else env
    if cfg is not None:
        line = f"RX_PONTE_BRASIL=on host={cfg.bridge_host} incra=ponte sicar=direto_primeiro"
        if cfg.identity is not None:
            line += f" acesso=google assinatura={cfg.identity.signer}"
        return line
    if source.get(ENV_KEY) and config({**dict(source), ENV_KEY: ""}) is not None:
        return "RX_PONTE_BRASIL=off motivo=chave_invalida"
    if source.get(ENV_URL) or source.get(ENV_TOKEN) or source.get(ENV_KEY):
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


def bridge_request(args: Sequence[str], cfg: Config, *, budget_s: float | None = None,
                   id_token: str | None = None) -> tuple[list[str], bytes]:
    """Argumentos do curl pela ponte e a entrada padrão com os cabeçalhos (token incluído)."""
    idx = _url_index(args)
    if idx is None:
        raise ValueError("curl_sem_url_unica")
    if id_token is not None and not _JWT_RE.match(id_token):
        raise ValueError("id_token_invalido")
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
    headers = (
        f"X-Ponte-Token: {cfg.token}\r\n"
        f"X-Ponte-Url: {target}\r\n"
        f"X-Ponte-Timeout: {_fmt_seconds(timeout_header)}\r\n"
    )
    if id_token is not None:
        headers += f"{ID_TOKEN_HEADER}: Bearer {id_token}\r\n"
    return out, headers.encode("ascii")


def _log_once(key: str, line: str) -> None:
    now = _now()
    with _lock:
        last = _last_log.get(key)
        if last is not None and now - last < LOG_EVERY_S:
            return
        _last_log[key] = now
    print(line, flush=True)


# ---------------------------------------------------------------------------------------------
# token de identidade do Google (cache, troca em segundo plano)
# ---------------------------------------------------------------------------------------------

_id_cond = threading.Condition()
_id_tokens: dict[tuple[str, str, str], tuple[str, float]] = {}
_id_refreshing: set[tuple[str, str, str]] = set()
_id_failed_until: dict[tuple[str, str, str], float] = {}


def _id_key(cfg: Config) -> tuple[str, str, str]:
    assert cfg.identity is not None
    return (cfg.identity.email, cfg.identity.key_id, cfg.audience)


def _refresh_id_token(cfg: Config, key: tuple[str, str, str]) -> None:
    token: str | None = None
    exp = 0.0
    reason = ""
    try:
        now = _wall()
        raw = _post_token(signed_assertion(cfg.identity, cfg.audience, int(now)), ID_TOKEN_HTTP_TIMEOUT_S)
        token, exp = parse_id_token_response(raw, cfg.audience, _wall())
    except urllib.error.HTTPError as exc:   # 400 invalid_grant: chave apagada, errada ou relógio torto
        reason = f"http_{exc.code}"
    except (urllib.error.URLError, OSError):
        reason = "rede"
    except Exception:                        # resposta estranha; nunca o texto (pode carregar token)
        reason = "resposta_invalida"
    with _id_cond:
        _id_refreshing.discard(key)
        if token is not None:
            _id_tokens[key] = (token, exp)
            _id_failed_until.pop(key, None)
        else:
            _id_failed_until[key] = _now() + ID_TOKEN_RETRY_AFTER_S
        _id_cond.notify_all()
    if token is None:
        _log_once(f"idtoken:{reason}", f"RX_PONTE_BRASIL=erro_credencial motivo={reason}")


def _start_refresh_locked(cfg: Config, key: tuple[str, str, str]) -> None:
    _id_refreshing.add(key)
    threading.Thread(target=_refresh_id_token, args=(cfg, key), name="ponte-id-token", daemon=True).start()


def id_token(cfg: Config, *, wait_s: float, cancel_event=None) -> str | None:
    """Token de identidade válido do cache; se não houver, espera a troca por até wait_s. None = sem credencial."""
    key = _id_key(cfg)
    deadline = time.monotonic() + max(0.0, wait_s)
    with _id_cond:
        while True:
            cached = _id_tokens.get(key)
            left = cached[1] - _wall() if cached else -1.0
            if left > ID_TOKEN_REFRESH_MARGIN_S:
                return cached[0]
            if key not in _id_refreshing and _now() >= _id_failed_until.get(key, float("-inf")):
                _start_refresh_locked(cfg, key)
            if left > ID_TOKEN_MIN_LEFT_S:
                return cached[0]            # ainda vale: usa enquanto renova
            if key not in _id_refreshing:
                return None                 # troca recusada há pouco: não insiste nem espera
            if cancel_event is not None and cancel_event.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            _id_cond.wait(min(0.2, remaining))


def prewarm(env: Mapping[str, str] | None = None) -> None:
    """Pede o primeiro token de identidade ao subir, para a primeira consulta não esperar por ele."""
    cfg = config(env)
    if cfg is None or cfg.identity is None:
        return
    key = _id_key(cfg)
    with _id_cond:
        if key not in _id_refreshing and key not in _id_tokens:
            _start_refresh_locked(cfg, key)


def _credential_wait(timeout_seconds: float | None, started: float) -> float:
    if timeout_seconds is None:
        return ID_TOKEN_HTTP_TIMEOUT_S
    return max(0.0, remaining_budget(timeout_seconds, started) - MIN_BRIDGE_BUDGET_S)


def credential_failure(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(list(args), CREDENTIAL_FAILURE_EXIT, b"", b"ponte: credencial do Google indisponivel")


# ---------------------------------------------------------------------------------------------
# chamada
# ---------------------------------------------------------------------------------------------

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
        # fora da lista, ponte sem token ou ocupada), do Google (403: ponte fechada e credencial ausente ou
        # sem permissão) ou da fonte repassada. O guia manda conferir pelo Cloud Shell antes de trocar
        # qualquer coisa. Nunca o token no log.
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
                timeout_seconds: float | None, cancel_event, budget_s: float | None = None,
                id_token: str | None = None) -> subprocess.CompletedProcess:
    extra = {"id_token": id_token} if id_token is not None else {}
    bridge_args, stdin = bridge_request(args, cfg, budget_s=budget_s, **extra)
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
    with _id_cond:
        _id_tokens.clear()
        _id_refreshing.clear()
        _id_failed_until.clear()


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


def fetch_via_bridge(args: Sequence[str], *, timeout_seconds: float, runner: Callable[..., subprocess.CompletedProcess],
                     env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    """Sempre pela ponte, mesmo para o SICAR (usado pela conferência do guia no Cloud Shell)."""
    cfg = config(env)
    if cfg is None:
        raise ValueError("ponte_desligada")
    started = _now()
    token = None
    if cfg.identity is not None:
        token = id_token(cfg, wait_s=_credential_wait(timeout_seconds, started))
        if token is None:
            return credential_failure(args)
    budget = remaining_budget(timeout_seconds, started)
    return _run_bridge(runner, args, cfg, timeout_seconds=budget, cancel_event=None, budget_s=budget, id_token=token)


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
        if cfg.identity is None:
            proc = _run_bridge(runner, args, cfg, timeout_seconds=timeout_seconds, cancel_event=cancel_event)
        else:
            started = _now()
            token = id_token(cfg, wait_s=_credential_wait(timeout_seconds, started), cancel_event=cancel_event)
            if token is None:
                return credential_failure(args)
            budget = remaining_budget(timeout_seconds, started)
            proc = _run_bridge(runner, args, cfg, timeout_seconds=budget, cancel_event=cancel_event, budget_s=budget,
                               id_token=token)
        if proc.returncode:
            _note_bridge_failure(host, proc)
        return proc

    started = _now()
    reason: str | None = None
    budget = timeout_seconds
    proc = None
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
    token = None
    if cfg.identity is not None:
        token = id_token(cfg, wait_s=_credential_wait(timeout_seconds, started), cancel_event=cancel_event)
        if token is None:
            if proc is not None:
                return proc                 # a falha direta é a resposta honesta
            _close_skip_window(host)        # sem credencial a ponte não serve: próxima tenta direto
            return credential_failure(args)
        budget = remaining_budget(timeout_seconds, started)
        if budget is not None and budget < MIN_BRIDGE_BUDGET_S:
            return proc if proc is not None else credential_failure(args)
    bridged = _run_bridge(runner, args, cfg, timeout_seconds=budget, cancel_event=cancel_event, budget_s=budget,
                          id_token=token)
    if bridged.returncode == 0:
        if reason is not None:
            _open_skip_window(host, reason)
    else:
        _note_bridge_failure(host, bridged)
        if reason is None and bridge_itself_failed(bridged):
            _close_skip_window(host)
    return bridged


print(status_line(), flush=True)
prewarm()

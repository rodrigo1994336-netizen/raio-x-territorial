"""Conferência da ponte fechada, rodada pelo dono no Cloud Shell (docs/PONTE_BRASIL_ATIVACAO.md).

Usa o MESMO cliente do Render (br_bridge): lê RX_PONTE_BRASIL_URL, RX_PONTE_BRASIL_TOKEN e
RX_PONTE_BRASIL_CHAVE do ambiente e confere, em ordem:

  1. fechada: pedido só com o token da ponte (sem credencial do Google) recebe 403 do Google;
  2. credencial: a chave assina o JWT e o Google devolve o token de identidade;
  3. saúde: com o token de identidade, /v1/health responde 200;
  4. INCRA e SICAR respondem PELA ponte (o SICAR forçado pela ponte, sem tentar direto).

As etapas 1 a 3 esperam até --esperar segundos (permissão nova e chave nova levam alguns minutos
para valer no Google). Nunca imprime token, chave, token de identidade nem o endereço da ponte.
Última linha: RESULTADO=ok, ou uma linha começando com PAROU:. Código de saída 0 só com RESULTADO=ok.

Uso: python3 scripts/ponte_brasil_conferir.py [--esperar 480]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with contextlib.redirect_stdout(io.StringIO()):   # a linha de estado do br_bridge traz o host da ponte
    import br_bridge  # noqa: E402

INCRA_URL = ("https://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_mg"
             "&service=WFS&request=GetCapabilities")
SICAR_URL = "https://geoserver.car.gov.br/geoserver/sicar/ows?service=WFS&version=1.0.0&request=GetCapabilities"
HTTP_TIMEOUT_S = 40.0      # a primeira chamada liga a máquina da ponte (partida a frio)
PAUSE_S = 15.0

_sleep = time.sleep
_monotonic = time.monotonic


def http_status(url: str, headers: dict[str, str]) -> int | None:
    """Código HTTP de um GET (None = sem resposta). Nunca devolve corpo nem texto de erro."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310 (https da ponte)
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def _gate_checks(cfg: br_bridge.Config) -> tuple[int | None, str | None, int | None]:
    health = cfg.audience + "/v1/health"
    closed = http_status(health, {"X-Ponte-Token": cfg.token})
    br_bridge.reset_state()
    token = br_bridge.id_token(cfg, wait_s=br_bridge.ID_TOKEN_HTTP_TIMEOUT_S + 5)
    healthy = None
    if token is not None:
        healthy = http_status(health, {br_bridge.ID_TOKEN_HEADER: f"Bearer {token}"})
    return closed, token, healthy


def _source(name: str, url: str, cfg_env=None) -> bool:
    args = ["curl", "-sS", "--fail", "--connect-timeout", "10", "--max-time", "30", "-A", "Raio-X-Territorial/conferencia", url]
    try:
        proc = br_bridge.fetch_via_bridge(args, timeout_seconds=35, runner=br_bridge.subprocess_runner, env=cfg_env)
    except Exception as exc:
        print(f"  {name}: nao respondeu ({type(exc).__name__})", flush=True)
        return False
    if proc.returncode == 0:
        print(f"  {name}: respondeu pela ponte", flush=True)
        return True
    status = br_bridge._http_status(proc) if proc.returncode == 22 else None
    detail = f"codigo {status}" if status else f"curl {proc.returncode}"
    print(f"  {name}: nao respondeu ({detail})", flush=True)
    return False


def main(argv: list[str] | None = None, env=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--esperar", type=float, default=0.0)
    opts = parser.parse_args(argv)
    cfg = br_bridge.config(env)
    if cfg is None:
        print(br_bridge.status_line(env).split(" host=")[0], flush=True)
        print("PAROU: a URL, o token ou a chave estao incompletos (colados pela metade?).", flush=True)
        return 1
    if cfg.identity is None:
        print("PAROU: falta a chave (RX_PONTE_BRASIL_CHAVE): sem ela a ponte fechada recusa o Raio-X.", flush=True)
        return 1
    print(f"  chave: lida (assinatura {cfg.identity.signer})", flush=True)
    deadline = _monotonic() + max(0.0, opts.esperar)
    waited = False
    while True:
        closed, token, healthy = _gate_checks(cfg)
        if closed == 403 and token is not None and healthy == 200:
            break
        if _monotonic() + PAUSE_S > deadline:
            break
        if not waited:
            print("  aguardando o Google aplicar a permissao e a chave (pode levar alguns minutos)...", flush=True)
            waited = True
        _sleep(PAUSE_S)
    if closed == 403:
        print("  fechada: pedido sem credencial do Google recusado (403)", flush=True)
    elif closed == 200:
        print("  fechada: NAO, a ponte ainda aceita pedido sem credencial do Google", flush=True)
    else:
        print(f"  fechada: codigo {closed if closed is not None else 'sem_resposta'}", flush=True)
    print(f"  credencial do Google: {'ok' if token is not None else 'recusada ou sem resposta'}", flush=True)
    if token is not None:
        print(f"  saude: {'ok' if healthy == 200 else 'codigo ' + str(healthy if healthy is not None else 'sem_resposta')}", flush=True)
    if closed == 200:
        print("PAROU: a ponte continua aberta ao publico. NAO coloque nada no Render. Me mande as linhas acima.", flush=True)
        return 1
    if token is None:
        print("PAROU: o Google nao entregou a credencial com esta chave. Me mande as linhas acima.", flush=True)
        return 1
    if healthy == 403:
        print("PAROU: a chave ainda nao tem permissao de chamar a ponte. Cole o bloco de novo daqui a 5 minutos.", flush=True)
        return 1
    if closed != 403 or healthy != 200:
        print("PAROU: a ponte nao respondeu como esperado. Me mande as linhas acima.", flush=True)
        return 1
    incra_ok = _source("INCRA", INCRA_URL, env)
    _source("SICAR", SICAR_URL, env)
    if not incra_ok:
        print("PAROU: o INCRA nao respondeu pela ponte. NAO coloque nada no Render. Me mande as linhas acima.", flush=True)
        return 1
    print("RESULTADO=ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import asyncio
import time
import threading
from typing import Any

from fastapi import HTTPException, Request

import portal_v8
import sicar_overlap_hardening_v47  # applies narrow CAR-overlap dedup patch before imports below
import sicar_integrity_v47
from external_process_lifecycle import RequestDisconnected, wait_for_cancelling_processes
from sicar_integrity_v47 import CAR_RE, query_car_integrity_v47
from sicar_integrity_display_v47 import panel_table_rows

app = portal_v8.app
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 900
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(code: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(code)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[code] = lock
        return lock


def _query_sync(car_code: str) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        return {"ok": False, "state": "invalid", "car_code": code, "detail": "invalid_car_format"}
    now = time.monotonic()
    cached = _CACHE.get(code)
    if cached and now - cached[0] < _TTL_SECONDS:
        out = dict(cached[1])
        out["cached"] = True
        return out
    with _lock_for(code):
        now = time.monotonic()
        cached = _CACHE.get(code)
        if cached and now - cached[0] < _TTL_SECONDS:
            out = dict(cached[1])
            out["cached"] = True
            return out
        out = query_car_integrity_v47(code)
        if out.get("ok"):
            out = dict(out)
            out["table_rows"] = panel_table_rows(out)
            out["cached"] = False
            _CACHE[code] = (now, out)
            if len(_CACHE) > 300:
                for key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:60]:
                    _CACHE.pop(key, None)
        return out


# Teto desta consulta. A derivacao anterior (2 x QUERY_TIMEOUT_S) multiplicava pela disputa da trava e
# ESQUECIA a contagem: uma leitura de integridade dispara CINCO consultas BigQuery em sequencia, cada uma
# com o seu proprio result(timeout=QUERY_TIMEOUT_S). O teto saia em 105 s, menos da metade do pior caso da
# propria rota, e um CAR grande com BigQuery frio — que hoje responde — passaria a devolver 504 e "consulta
# pendente" no cartao: resposta que existia, sumiu.
#
# Quantas consultas: _fetch_property, _fetch_themes, _fetch_overlap e _fetch_boundary duas vezes (municipio
# e UF), em sicar_integrity_v47.query_car_integrity_v47. O numero NAO e afirmacao deste arquivo: a regra
# estatica integridade_conta_consultas (scripts/m1_processos_gate.py) conta as chamadas de _query
# alcancaveis a partir de query_car_integrity_v47 e reprova se divergir daqui.
_QUERIES_PER_INTEGRITY = 5
# A trava por CAR pode fazer este pedido esperar uma leitura igual ja em curso antes de comecar a sua.
_LOCK_WAIT_FACTOR = 2
# Folga ARBITRADA (nao medida): montagem da tabela do painel a partir de linhas ja em memoria, trabalho de
# CPU sem rede. E a menor parcela do teto (15 s em 465 s) e so pode adiar o 504, nunca cortar resposta.
_TABLE_BUILD_S = 15.0
_INTEGRITY_S = round(
    _LOCK_WAIT_FACTOR * _QUERIES_PER_INTEGRITY * sicar_integrity_v47.QUERY_TIMEOUT_S + _TABLE_BUILD_S, 1)


@app.get("/v1/live/car-integrity/{car_code}")
async def car_integrity_v47(car_code: str, request: Request):
    # O QUE O ESCOPO FAZ AQUI, E O QUE NAO FAZ. O BigQuery nao e processo gerenciado: nenhum cancel_event
    # chega ao job em curso, entao o escopo NAO interrompe a consulta que ja esta rodando. O que ele da e
    # (a) a resposta imediata ao cliente — 504 no prazo, 499 na desistencia — e (b) a parada da cadeia: o
    # sicar_integrity_v47._query pergunta pelo cancelamento ANTES de abrir cada uma das cinco consultas,
    # entao a thread abandonada para na proxima fronteira em vez de rodar as cinco e segurar a trava por CAR
    # ate o fim. Cancelar o job de verdade (job.cancel()) fica para quando o BigQuery entrar na fila.
    # Dentro do escopo: prazo e desistencia do cliente alcancam esta consulta e os processos que ela abrir.
    try:
        out = await wait_for_cancelling_processes(
            asyncio.to_thread(_query_sync, car_code), _INTEGRITY_S, request=request)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="car_integrity_timeout")
    except RequestDisconnected:
        raise HTTPException(status_code=499, detail="client_disconnected")
    if out.get("state") == "invalid":
        raise HTTPException(status_code=422, detail=out)
    return out


# V48 Source 2 is deliberately loaded here because sitecustomize imports this
# module immediately after the MTE conformity extension. This preserves the
# additive wrapper order without changing the frozen V47 integrity engine.
import portal_conformity_sinaflor_v48  # noqa: E402,F401

print("RX_PORTAL_CAR_INTEGRITY_V47=lazy_basedosdados_fail_closed", flush=True)

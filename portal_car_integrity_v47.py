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


# Teto desta consulta: o prazo do proprio BigQuery contado DUAS vezes, porque a trava por CAR pode fazer
# este pedido esperar uma consulta igual ja em curso antes de comecar a sua, mais folga para a montagem da
# tabela. Derivado do modulo que a implementa; menor cortaria resposta que hoje chega.
_TABLE_BUILD_S = 15.0
_INTEGRITY_S = round(2 * sicar_integrity_v47.QUERY_TIMEOUT_S + _TABLE_BUILD_S, 1)


@app.get("/v1/live/car-integrity/{car_code}")
async def car_integrity_v47(car_code: str, request: Request):
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

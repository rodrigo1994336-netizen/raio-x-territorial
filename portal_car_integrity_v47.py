from __future__ import annotations

import asyncio
import time
import threading
from typing import Any

from fastapi import HTTPException

import portal_v8
import sicar_overlap_hardening_v47  # applies narrow CAR-overlap dedup patch before imports below
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


@app.get("/v1/live/car-integrity/{car_code}")
async def car_integrity_v47(car_code: str):
    out = await asyncio.to_thread(_query_sync, car_code)
    if out.get("state") == "invalid":
        raise HTTPException(status_code=422, detail=out)
    return out


print("RX_PORTAL_CAR_INTEGRITY_V47=lazy_basedosdados_fail_closed", flush=True)

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException

import report_api as base
import report_v9_patch as prog
from car_resilient import fetch_car_live_resilient

app=base.app

# Replace V9 quick endpoint. "Quick" must never wait for analyze_car()/SIGEF/IBAMA/ANM.
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/quick/{car_code}']


@app.get('/v1/live/quick/{car_code}')
async def quick_analysis_v22(car_code:str):
    code=car_code.upper();t0=time.monotonic()

    # If the complete analysis is already cached, return it immediately.
    try:cached_full=base._cache_get(code,time.monotonic())
    except Exception:cached_full=None
    if cached_full is not None:
        prog._ensure_deep(code)
        elapsed=round((time.monotonic()-t0)*1000)
        summary=base._report_summary(cached_full)
        summary['progressive']={'state':'quick-cache','deep_analysis':'ready','elapsed_ms':elapsed}
        print(f'RX_QUICK_V22_CACHE={code}:{elapsed}ms',flush=True)
        return {'ok':True,'mode':'quick-cache','elapsed_ms':elapsed,'analysis':summary,'deep_state':prog._PROGRESS.get(code,{'state':'ready','stage':'complete'})}

    # Reuse the tiny quick cache if present; otherwise fetch only the CAR geometry/properties.
    cached=prog._quick_get(code)
    if cached is None:
        try:
            car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=6)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504,detail='SICAR demorou além de 6 segundos para confirmar o imóvel.')
        if not car.get('ok'):
            raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR temporariamente indisponível.')
        cached={'car':car}
        prog._quick_put(code,cached)

    # Deep analysis starts after the immediate CAR result is available and never blocks this response.
    prog._ensure_deep(code)
    elapsed=round((time.monotonic()-t0)*1000)
    summary=base._report_summary(cached)
    summary['progressive']={'state':'quick-car','deep_analysis':'running','elapsed_ms':elapsed}
    print(f'RX_QUICK_V22_READY={code}:{elapsed}ms',flush=True)
    return {'ok':True,'mode':'quick-car','elapsed_ms':elapsed,'analysis':summary,'deep_state':prog._PROGRESS.get(code,{'state':'running','stage':'queued'})}


print('RX_REPORT_QUICK_V22=car_first_no_full_wait',flush=True)

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException

import report_api as base
import report_v9_patch as prog
from car_resilient import fetch_car_live_resilient

app=base.app

app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/quick/{car_code}']


@app.get('/v1/live/quick/{car_code}')
async def quick_analysis_v24(car_code:str,deep:bool=False):
    code=car_code.upper();t0=time.monotonic()

    try:cached_full=base._cache_get(code,time.monotonic())
    except Exception:cached_full=None
    if cached_full is not None:
        if deep:prog._ensure_deep(code)
        elapsed=round((time.monotonic()-t0)*1000)
        summary=base._report_summary(cached_full)
        summary['progressive']={'state':'quick-cache','deep_analysis':'ready','elapsed_ms':elapsed}
        print(f'RX_QUICK_V24_CACHE={code}:{elapsed}ms:deep={deep}',flush=True)
        return {'ok':True,'mode':'quick-cache','elapsed_ms':elapsed,'analysis':summary,'deep_state':prog._PROGRESS.get(code,{'state':'ready','stage':'complete'})}

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

    if deep:
        prog._ensure_deep(code)
        deep_state=prog._PROGRESS.get(code,{'state':'running','stage':'queued'})
        deep_label='running'
    else:
        deep_state={'state':'idle','stage':'on_demand'}
        deep_label='on_demand'

    elapsed=round((time.monotonic()-t0)*1000)
    summary=base._report_summary(cached)
    summary['progressive']={'state':'quick-car','deep_analysis':deep_label,'elapsed_ms':elapsed}
    print(f'RX_QUICK_V24_READY={code}:{elapsed}ms:deep={deep}',flush=True)
    return {'ok':True,'mode':'quick-car','elapsed_ms':elapsed,'analysis':summary,'deep_state':deep_state}


print('RX_REPORT_QUICK_V24=car_first_deep_on_demand',flush=True)

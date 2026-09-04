from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import report_api as base
from live_report_adapter_v9 import generate_live_report as generate_live_report_v9

app=base.app
base.generate_live_report=generate_live_report_v9
base.APP_VERSION='0.19.1-progressive-v9-report'

_PROGRESS:dict[str,dict]={}
_PROGRESS_TASKS:dict[str,asyncio.Task]={}
_QUICK:OrderedDict[str,tuple[float,dict]]=OrderedDict()
_QUICK_TTL=120
_QUICK_MAX=16


def _prune_quick():
    now=time.monotonic()
    for k,(ts,_) in list(_QUICK.items()):
        if now-ts>_QUICK_TTL:_QUICK.pop(k,None)
    while len(_QUICK)>_QUICK_MAX:_QUICK.popitem(last=False)


def _quick_get(code):
    _prune_quick(); item=_QUICK.get(code)
    if not item:return None
    _QUICK.move_to_end(code); return item[1]


def _quick_put(code,result):
    _QUICK[code]=(time.monotonic(),result); _QUICK.move_to_end(code); _prune_quick()


async def _run_deep(code:str):
    started=time.monotonic()
    _PROGRESS[code]={'state':'running','stage':'deep_sources','started_at':started}
    try:
        result=await base._analyze_with_live_addons(code)
        _PROGRESS[code]={
            'state':'ready','stage':'complete','elapsed_ms':round((time.monotonic()-started)*1000),
            'analysis':base._report_summary(result)
        }
        print(f'RX_PROGRESSIVE_READY={code}:{_PROGRESS[code]["elapsed_ms"]}ms',flush=True)
    except Exception as e:
        _PROGRESS[code]={'state':'failed','stage':'deep_sources','elapsed_ms':round((time.monotonic()-started)*1000),'detail':f'{type(e).__name__}:{str(e)[:300]}'}
        print(f'RX_PROGRESSIVE_FAIL={code}:{type(e).__name__}:{str(e)[:200]}',flush=True)
    finally:
        _PROGRESS_TASKS.pop(code,None)


def _ensure_deep(code:str):
    t=_PROGRESS_TASKS.get(code)
    if t and not t.done():return
    # If full analysis is already cached, expose it immediately.
    try:
        cached=base._cache_get(code,time.monotonic())
    except Exception:
        cached=None
    if cached is not None:
        _PROGRESS[code]={'state':'ready','stage':'complete','elapsed_ms':0,'analysis':base._report_summary(cached)}
        return
    _PROGRESS_TASKS[code]=asyncio.create_task(_run_deep(code))


@app.get('/v1/live/quick/{car_code}')
async def quick_analysis(car_code:str):
    code=car_code.upper(); t0=time.monotonic()
    cached=_quick_get(code)
    if cached is None:
        try:
            # Core sources are CAR/SIGEF/IBAMA/ANM/PRODES. The deep state (fire,
            # territorial constraints, water, pivots, climate, SGB and IDE layers)
            # is deliberately not awaited here.
            result=await asyncio.wait_for(base.analyze_car(code),timeout=15)
        except asyncio.TimeoutError:
            # CAR alone is still useful for an immediate acknowledgement.
            car=await asyncio.to_thread(base.fetch_car_live,code)
            if not car.get('ok'):
                raise base.HTTPException(status_code=502,detail='As fontes principais estão lentas e o CAR não pôde ser confirmado agora.')
            result={'car':car}
        car=result.get('car') or {}
        if not car.get('ok'):
            raise base.HTTPException(status_code=404 if car.get('not_found') else 502,detail=base._safe_summary(result))
        _quick_put(code,result); cached=result
    _ensure_deep(code)
    elapsed=round((time.monotonic()-t0)*1000)
    summary=base._report_summary(cached)
    summary['progressive']={'state':'quick','deep_analysis':'running','elapsed_ms':elapsed}
    print(f'RX_QUICK_READY={code}:{elapsed}ms',flush=True)
    return {'ok':True,'mode':'quick-first','elapsed_ms':elapsed,'analysis':summary,'deep_state':_PROGRESS.get(code,{'state':'running','stage':'queued'})}


@app.get('/v1/live/progressive/status/{car_code}')
async def progressive_status(car_code:str):
    code=car_code.upper()
    state=_PROGRESS.get(code)
    if not state:
        _ensure_deep(code); state=_PROGRESS.get(code,{'state':'running','stage':'queued'})
    return state


@app.post('/v1/live/progressive/refresh/{car_code}')
async def progressive_refresh(car_code:str):
    code=car_code.upper()
    _PROGRESS.pop(code,None); _QUICK.pop(code,None)
    # Do not duplicate a task already executing.
    if code not in _PROGRESS_TASKS:
        _PROGRESS_TASKS[code]=asyncio.create_task(_run_deep(code))
    return {'ok':True,'state':'running','car_code':code}


print('RX_REPORT_V9_PATCH=loaded',flush=True)

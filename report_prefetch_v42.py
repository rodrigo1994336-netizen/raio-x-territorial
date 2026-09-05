from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import live_report_adapter_v13 as live_v13
import report_api as base
import report_v13_patch as report_patch
from car_resilient import fetch_car_live_resilient
from live_report_adapter import REPORT_DIR


# V42 target: the expensive visual/data extras and the deep territorial analysis
# are independent after the CAR geometry is known. Run them concurrently instead
# of serially; preserve the exact same payload/report path and truth guards.
_orig_v13_generate = live_v13.generate_live_report


def _prefetch_aware_v13(result:dict,car_code:str):
    pref=result.get('_rx_prefetched_report_extras')
    if not (isinstance(pref,list) and len(pref)==8):
        return _orig_v13_generate(result,car_code)
    original_extras=live_v13._extras
    async def _ready(*_args,**_kwargs):
        return pref
    live_v13._extras=_ready
    try:
        return _orig_v13_generate(result,car_code)
    finally:
        live_v13._extras=original_extras


live_v13.generate_live_report=_prefetch_aware_v13


def _prune_prefetch_dirs():
    root=Path(REPORT_DIR)/'_prefetch_v42'
    try:
        root.mkdir(parents=True,exist_ok=True);now=time.time();dirs=[p for p in root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p:p.stat().st_mtime,reverse=True)
        for p in dirs[8:]:shutil.rmtree(p,ignore_errors=True)
        for p in dirs[:8]:
            try:
                if now-p.stat().st_mtime>7200:shutil.rmtree(p,ignore_errors=True)
            except Exception:pass
    except Exception:pass


async def _build_v42(car_code:str,property_name:str|None=None):
    code=car_code.upper();started=time.monotonic();_prune_prefetch_dirs()
    # Launch deep analysis immediately; resolve the lightweight CAR geometry in
    # parallel only to give the extras their spatial seed.
    deep_task=asyncio.create_task(base._analyze_with_live_addons(code))
    car_task=asyncio.create_task(asyncio.to_thread(fetch_car_live_resilient,code))
    prefetch_task=None;pref_dir=None
    try:
        car=await car_task
        if isinstance(car,dict) and car.get('ok') and car.get('geometry'):
            safe=''.join(ch for ch in code if ch.isalnum() or ch in '-_')
            pref_dir=Path(REPORT_DIR)/'_prefetch_v42'/f'{safe[-12:]}_{int(time.time()*1000)}';pref_dir.mkdir(parents=True,exist_ok=True)
            seed={'car':car}
            if property_name and str(property_name).strip():seed['_requested_property_name']=str(property_name).strip()[:120]
            prefetch_task=asyncio.create_task(live_v13._extras(seed,code,pref_dir))
            print(f'RX_REPORT_PREFETCH={code}:started',flush=True)
    except Exception as exc:
        print(f'RX_REPORT_PREFETCH={code}:seed_failed:{type(exc).__name__}',flush=True)
    result=await deep_task
    pref=None
    if prefetch_task:
        try:
            pref=await prefetch_task
            if isinstance(pref,list) and len(pref)==8:
                print(f'RX_REPORT_PREFETCH={code}:ready:{round((time.monotonic()-started)*1000)}ms',flush=True)
            else:pref=None
        except Exception as exc:
            print(f'RX_REPORT_PREFETCH={code}:extras_failed:{type(exc).__name__}',flush=True);pref=None
    working=dict(result)
    if property_name and str(property_name).strip():working['_requested_property_name']=str(property_name).strip()[:120]
    if pref is not None:working['_rx_prefetched_report_extras']=pref
    async with base._REPORT_SEMAPHORE:
        try:meta=await asyncio.to_thread(report_patch.generate_live_report,working,code)
        finally:base._release_memory()
    print(f'RX_REPORT_PREFETCH_TOTAL={code}:{round((time.monotonic()-started)*1000)}ms:prefetched={pref is not None}',flush=True)
    return result,meta


report_patch._build_v13=_build_v42
print('RX_REPORT_PREFETCH_V42=deep_analysis_parallel_report_extras',flush=True)

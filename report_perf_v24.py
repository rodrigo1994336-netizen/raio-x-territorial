from __future__ import annotations

import asyncio
import functools
import inspect
import time

import report_api as base

_PATCHED=False


def _async_timed(name,fn):
    @functools.wraps(fn)
    async def wrapped(*args,**kwargs):
        t0=time.monotonic()
        try:
            return await fn(*args,**kwargs)
        finally:
            print(f'RX_DEEP_STAGE={name}:{round((time.monotonic()-t0)*1000)}ms',flush=True)
    return wrapped


def _sync_timed(name,fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        t0=time.monotonic()
        try:
            return fn(*args,**kwargs)
        finally:
            print(f'RX_DEEP_STAGE={name}:{round((time.monotonic()-t0)*1000)}ms',flush=True)
    return wrapped


def _patch_attr(name,label):
    fn=getattr(base,name,None)
    if not callable(fn) or getattr(fn,'_rx_perf_wrapped',False):return
    w=_async_timed(label,fn) if inspect.iscoroutinefunction(fn) else _sync_timed(label,fn)
    w._rx_perf_wrapped=True
    setattr(base,name,w)


for attr,label in (
    ('analyze_car','core_car_sigef_ibama_anm_prodes'),
    ('query_ibama_autos','ibama_autos'),
    ('analyze_fire_near_property','fire'),
    ('query_territorial_constraints','territorial_constraints'),
    ('query_outorgas_mg','water'),
    ('query_pivots_ana','pivots'),
    ('query_climate_nasa','climate_recent'),
    ('query_critical_minerals','critical_minerals'),
    ('probe_benchmark','ide_useful_layers'),
):
    _patch_attr(attr,label)

_orig_uncached=base._analyze_uncached
if not getattr(_orig_uncached,'_rx_perf_wrapped',False):
    @functools.wraps(_orig_uncached)
    async def _uncached_timed(car_code:str):
        t0=time.monotonic()
        try:
            return await _orig_uncached(car_code)
        finally:
            print(f'RX_DEEP_TOTAL={car_code.upper()}:{round((time.monotonic()-t0)*1000)}ms',flush=True)
    _uncached_timed._rx_perf_wrapped=True
    base._analyze_uncached=_uncached_timed

print('RX_REPORT_PERF_V24=source_timing_enabled',flush=True)

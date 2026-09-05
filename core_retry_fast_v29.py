from __future__ import annotations

import asyncio
import time

import report_api as base
from anm_fast_v29 import query_anm_fast


async def _bounded(label,coro,timeout_s:float):
    t0=time.monotonic()
    try:
        value=await asyncio.wait_for(coro,timeout=timeout_s)
        ms=round((time.monotonic()-t0)*1000)
        print(f'RX_CORE_RETRY={label}:{ms}ms:ok={bool((value or {}).get("ok"))}',flush=True)
        return value
    except Exception as e:
        ms=round((time.monotonic()-t0)*1000)
        print(f'RX_CORE_RETRY_FAIL={label}:{ms}ms:{type(e).__name__}',flush=True)
        return {'ok':False,'source':label,'detail':f'{type(e).__name__}:{str(e)[:180]}','retry_timeout_s':timeout_s}


async def _retry_failed_core_v29(result:dict):
    car=result.get('car') or {};bbox=car.get('bbox')
    if not bbox:return result
    started=time.monotonic()
    jobs=[];keys=[]

    # One bounded retry only. The report must not wait a second full provider timeout
    # after the first core attempt already failed.
    if not (result.get('sigef') or {}).get('ok'):
        keys.append('sigef');jobs.append(_bounded('SIGEF',base.query_sigef(bbox),10))
    if not (result.get('embargos_ibama') or {}).get('ok'):
        keys.append('embargos_ibama');jobs.append(_bounded('IBAMA_embargos',base.query_embargos(bbox),10))
    if not (result.get('prodes') or {}).get('ok'):
        keys.append('prodes');jobs.append(_bounded('PRODES',base.query_prodes(bbox),14))
    if not (result.get('anm') or {}).get('ok'):
        keys.append('anm');jobs.append(_bounded('ANM',query_anm_fast(bbox),14))

    if jobs:
        vals=await asyncio.gather(*jobs,return_exceptions=False)
        for k,v in zip(keys,vals):
            result[k]=v

    print(f'RX_CORE_RETRY_TOTAL={round((time.monotonic()-started)*1000)}ms:retried={len(keys)}',flush=True)
    return result


base._retry_failed_core=_retry_failed_core_v29
print('RX_CORE_RETRY_V29=concurrent_bounded_single_retry',flush=True)

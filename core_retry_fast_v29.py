from __future__ import annotations

import asyncio
import time

import report_api as base


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


async def _retry_failed_core_v30(result:dict):
    car=result.get('car') or {};bbox=car.get('bbox')
    if not bbox:return result
    started=time.monotonic()
    jobs=[];keys=[]

    # Retry only sources where a second short attempt has historically recovered.
    # ANM is deliberately excluded: it already receives one bounded attempt in the
    # core analysis and a second attempt was adding ~14 s with no useful result.
    if not (result.get('sigef') or {}).get('ok'):
        keys.append('sigef');jobs.append(_bounded('SIGEF',base.query_sigef(bbox),8))
    if not (result.get('embargos_ibama') or {}).get('ok'):
        keys.append('embargos_ibama');jobs.append(_bounded('IBAMA_embargos',base.query_embargos(bbox),8))
    if not (result.get('prodes') or {}).get('ok'):
        keys.append('prodes');jobs.append(_bounded('PRODES',base.query_prodes(bbox),10))

    if jobs:
        vals=await asyncio.gather(*jobs,return_exceptions=False)
        for k,v in zip(keys,vals):result[k]=v

    if not (result.get('anm') or {}).get('ok'):
        anm=result.setdefault('anm',{})
        anm.setdefault('source','ANM/SIGMINE')
        anm.setdefault('state','temporarily_unavailable')
        anm['retry_skipped']=True
        anm['note']='A fonte ANM não respondeu no limite rápido desta emissão. O relatório segue sem inferir ausência; a aba Mineração pode ser atualizada separadamente.'

    print(f'RX_CORE_RETRY_TOTAL={round((time.monotonic()-started)*1000)}ms:retried={len(keys)}:anm_retry=skipped',flush=True)
    return result


base._retry_failed_core=_retry_failed_core_v30
print('RX_CORE_RETRY_V30=bounded_recovery_anm_never_retried',flush=True)

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

import report_api as base
import report_v13_patch as v13

app=base.app
TTL=max(300,int(os.getenv('RX_PDF_CACHE_TTL_SECONDS','3600')))
MAX=max(2,int(os.getenv('RX_PDF_CACHE_MAX_ITEMS','8')))
_CACHE:OrderedDict[str,dict]=OrderedDict()
_TASKS:dict[str,asyncio.Task]={}


def _name(v):
    return ' '.join(str(v or '').strip().split())[:120]


def _key(code,name=''):
    # A CAR identifies one property report. The previous CAR+name key allowed the
    # mobile prewarm (often name='') and a named request to render the SAME PDF at
    # the same time. That doubled raster/network/ReportLab work on small instances.
    return code.upper()


def _prune():
    now=time.monotonic()
    for k,v in list(_CACHE.items()):
        if now-float(v.get('ts') or 0)>TTL or (v.get('state')=='ready' and not Path(str((v.get('meta') or {}).get('pdf_path') or '')).exists()):
            _CACHE.pop(k,None)
    while len(_CACHE)>MAX:
        _CACHE.popitem(last=False)


def _public(state:dict):
    meta=state.get('meta') or {}
    return {
        'state':state.get('state'),'car_code':state.get('car_code'),'property_name':state.get('property_name'),
        'started_at_ms':state.get('started_at_ms'),'elapsed_ms':state.get('elapsed_ms'),
        'report_id':meta.get('report_id'),'bytes':meta.get('bytes'),'sha256':meta.get('sha256'),
        'detail':state.get('detail'),'cached':state.get('state')=='ready'
    }


def _prefer_name(current:str|None,new:str|None)->str:
    current=_name(current);new=_name(new)
    if not current and new:return new
    # Generic UI/test labels should never replace a real property designation.
    generic=('imóvel rural','imovel rural','property','benchmark','fazenda selecionada')
    cur_generic=any(x in current.casefold() for x in generic) if current else True
    new_generic=any(x in new.casefold() for x in generic) if new else True
    if cur_generic and new and not new_generic:return new
    return current or new


async def _worker(code:str,property_name:str,key:str):
    started=time.monotonic(); st=_CACHE.setdefault(key,{})
    property_name=_prefer_name(st.get('property_name'),property_name)
    st.update(state='running',car_code=code,property_name=property_name,started_at_ms=int(time.time()*1000),ts=time.monotonic())
    print(f'RX_PDF_STAGE={code}:worker_start:name={property_name or "(auto)"}',flush=True)
    try:
        t0=time.monotonic()
        result,meta=await v13._build_v13(code,property_name or None)
        build_ms=round((time.monotonic()-t0)*1000)
        st.update(state='ready',meta=meta,property_name=meta.get('property_name') or property_name,
                  elapsed_ms=round((time.monotonic()-started)*1000),build_ms=build_ms,ts=time.monotonic())
        _CACHE.move_to_end(key);_prune()
        print(f'RX_PDF_STAGE={code}:build_complete:{build_ms}ms',flush=True)
        print(f'RX_PDF_CACHE_READY={code}:{st["elapsed_ms"]}ms:{meta.get("bytes")}',flush=True)
        return result,meta
    except Exception as e:
        st.update(state='failed',detail=f'{type(e).__name__}:{str(e)[:260]}',elapsed_ms=round((time.monotonic()-started)*1000),ts=time.monotonic())
        print(f'RX_PDF_CACHE_FAIL={code}:{st["detail"]}',flush=True)
        raise
    finally:
        _TASKS.pop(key,None)


def _ensure(code:str,property_name:str=''):
    code=code.upper();property_name=_name(property_name);key=_key(code);_prune()
    st=_CACHE.get(key)
    if st:
        st['property_name']=_prefer_name(st.get('property_name'),property_name)
    if st and st.get('state')=='ready' and Path(str((st.get('meta') or {}).get('pdf_path') or '')).exists():
        _CACHE.move_to_end(key);return key,None
    task=_TASKS.get(key)
    if task and not task.done():
        print(f'RX_PDF_DEDUP_HIT={code}:existing_task',flush=True)
        return key,task
    _TASKS[key]=asyncio.create_task(_worker(code,property_name,key));task=_TASKS[key]
    return key,task


# Replace old synchronous PDF/meta routes. Direct PDF requests still wait when there
# is no prewarm, preserving API compatibility. Mobile prewarms on property selection.
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in {
    '/v1/reports/property/{car_code}','/v1/reports/property/{car_code}/meta'
}]


@app.post('/v1/reports/property/{car_code}/prepare')
@app.get('/v1/reports/property/{car_code}/prepare')
async def prepare_pdf_v21(car_code:str,property_name:str|None=None):
    key,task=_ensure(car_code,property_name or '')
    st=_CACHE.get(key) or {'state':'queued','car_code':car_code.upper(),'property_name':_name(property_name)}
    return {'ok':True,**_public(st)}


@app.get('/v1/reports/property/{car_code}/status')
async def pdf_status_v21(car_code:str,property_name:str|None=None):
    key=_key(car_code);_prune();st=_CACHE.get(key)
    if not st:return {'ok':True,'state':'idle','car_code':car_code.upper(),'property_name':_name(property_name),'cached':False}
    if property_name:st['property_name']=_prefer_name(st.get('property_name'),property_name)
    data=_public(st)
    if st.get('build_ms') is not None:data['build_ms']=st.get('build_ms')
    return {'ok':True,**data}


@app.get('/v1/reports/property/{car_code}/meta')
async def report_meta_v21(car_code:str,property_name:str|None=None):
    key,task=_ensure(car_code,property_name or '')
    if task:
        try:result,meta=await task
        except Exception as e:raise HTTPException(status_code=502,detail=f'Falha ao gerar relatório: {type(e).__name__}')
    else:
        st=_CACHE[key];meta=st['meta'];result=await base._analyze_with_live_addons(car_code.upper())
    public=base._public_meta(meta);public['property_name']=meta.get('property_name') or (_CACHE.get(key) or {}).get('property_name') or _name(property_name)
    return {'report':public,'analysis':base._report_summary(result),'cache':'ready'}


@app.get('/v1/reports/property/{car_code}')
async def report_pdf_v21(car_code:str,property_name:str|None=None):
    key,task=_ensure(car_code,property_name or '')
    if task:
        try:_,meta=await task
        except Exception as e:raise HTTPException(status_code=502,detail=f'Falha ao gerar relatório: {type(e).__name__}')
    else:meta=_CACHE[key]['meta']
    pdf=Path(str(meta.get('pdf_path') or ''))
    if not pdf.exists():raise HTTPException(status_code=500,detail='PDF pronto sem arquivo disponível')
    return FileResponse(str(pdf),media_type='application/pdf',filename=f'raio_x_territorial_{car_code.upper()}.pdf',headers={
        'Cache-Control':'private, max-age=3600, immutable','X-RaioX-Report-ID':str(meta.get('report_id') or ''),
        'X-RaioX-SHA256':str(meta.get('sha256') or ''),'X-RaioX-PDF-Cache':'HIT',
        'Content-Disposition':f'inline; filename="raio_x_territorial_{car_code.upper()}.pdf"'
    })


print('RX_PDF_CACHE_V24=car_dedup_prewarm_cache_status',flush=True)

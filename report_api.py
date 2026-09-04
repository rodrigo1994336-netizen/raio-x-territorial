from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from deploy_app import analyze_car, fetch_car_live, _safe_summary, TEST_CAR, query_embargos, query_prodes, query_sigef
from anm_resilient import query_anm_curl_exact
from fire_live import analyze_fire_near_property
from live_extra_sources import query_ibama_autos
from territorial_constraints import query_territorial_constraints
from water_mg import query_outorgas_mg
from pivots_ana import query_pivots_ana
from climate_nasa import query_climate_nasa
from ide_catalog import benchmark_targets, search_catalog
from ide_layer_probe import probe_benchmark
from critical_minerals import query_critical_minerals
from live_report_adapter_v8 import generate_live_report

APP_VERSION='0.18.1-operational-critical-minerals'
app = FastAPI(title='Raio-X Territorial Report API', version=APP_VERSION)
CACHE_TTL_SECONDS=300
_CACHE:dict[str,tuple[float,dict]]={}
_LOCKS:dict[str,asyncio.Lock]={}


def _public_meta(meta: dict):
    return {k: meta.get(k) for k in ('report_id','sha256','bytes','payload_sha256')}


def _report_summary(result: dict):
    base=_safe_summary(result)
    autos=result.get('autos_ibama') or {}; fire=result.get('fire_live') or {}; cons=result.get('territorial_constraints') or {}; water=result.get('water_mg') or {}; piv=result.get('pivots_ana') or {}; cl=result.get('climate_nasa') or {}; ide=result.get('ide_layers') or {}; minerals=result.get('critical_minerals') or {}
    base['autos_ibama']={'ok':autos.get('ok'),'feature_count_bbox':autos.get('feature_count_bbox'),'occurrence_count':autos.get('occurrence_count'),'fine_total':autos.get('fine_total'),'source':autos.get('source'),'deduplicated':autos.get('deduplicated'),'detail':autos.get('detail')}
    base['fire_live']={'ok':fire.get('ok'),'latest_file':fire.get('latest_file'),'feed_focus_count':fire.get('feed_focus_count'),'radius_km':fire.get('radius_km'),'inside_count':fire.get('inside_count'),'near_count':fire.get('near_count'),'nearest':fire.get('nearest'),'window_note':fire.get('window_note'),'source':fire.get('source'),'detail':fire.get('detail')}
    base['territorial_constraints']={'ok':cons.get('ok'),'area_unique_all_constraints_ha':cons.get('area_unique_all_constraints_ha'),'services':{k:{'ok':v.get('ok'),'label':v.get('label'),'occurrence_count':v.get('occurrence_count'),'area_unique_ha':v.get('area_unique_ha'),'feature_count_bbox':v.get('feature_count_bbox'),'source':v.get('source'),'detail':v.get('detail')} for k,v in (cons.get('services') or {}).items()},'detail':cons.get('detail')}
    base['water_mg']={'ok':water.get('ok'),'layer':water.get('layer'),'feature_count_bbox':water.get('feature_count_bbox'),'inside_count':water.get('inside_count'),'near_count':water.get('near_count'),'radius_km':water.get('radius_km'),'nearest':water.get('nearest'),'layers':water.get('layers'),'source':water.get('source'),'discovery':water.get('discovery'),'detail':water.get('detail')}
    base['pivots_ana']={'ok':piv.get('ok'),'detail':piv.get('detail'),'reference_year':piv.get('reference_year'),'feature_count_bbox':piv.get('feature_count_bbox'),'parsed_feature_count':piv.get('parsed_feature_count'),'intersection_count':piv.get('intersection_count'),'intersection_area_unique_ha':piv.get('intersection_area_unique_ha'),'near_count':piv.get('near_count'),'radius_km':piv.get('radius_km'),'nearest':piv.get('nearest'),'source':piv.get('source')}
    base['climate_nasa']={'ok':cl.get('ok'),'detail':cl.get('detail'),'available_days':cl.get('available_days'),'period_start':cl.get('period_start'),'period_end':cl.get('period_end'),'rain_sum_mm':cl.get('rain_sum_mm'),'temp_avg_c':cl.get('temp_avg_c'),'temp_max_avg_c':cl.get('temp_max_avg_c'),'temp_min_avg_c':cl.get('temp_min_avg_c'),'rh_avg_pct':cl.get('rh_avg_pct'),'solar_avg_kwh_m2_day':cl.get('solar_avg_kwh_m2_day'),'latest_data_date':cl.get('latest_data_date'),'source':cl.get('source')}
    base['ide_layers']={k:{'ok':v.get('ok'),'layer':v.get('layer'),'feature_count_bbox':v.get('feature_count_bbox'),'exact_count':v.get('exact_count'),'samples':v.get('samples'),'detail':v.get('detail')} for k,v in ide.items()}
    manm=minerals.get('anm') or {}; msgb=minerals.get('sgb') or {}
    base['critical_minerals']={
        'ok':minerals.get('ok'),'source':minerals.get('source'),'mineral_codes':minerals.get('mineral_codes') or [],
        'rare_earth_signal':bool(minerals.get('rare_earth_signal')),'interpretation':minerals.get('interpretation'),
        'anm_process_count':manm.get('process_count'),'anm_critical_process_count':manm.get('critical_process_count'),'anm_counts':manm.get('counts') or {},
        'sgb_capabilities_ok':msgb.get('capabilities_ok'),'sgb_candidate_layer_count':msgb.get('candidate_layer_count'),'sgb_queried_layer_count':msgb.get('queried_layer_count'),
        'sgb_hit_layers':[{'layer':x.get('layer'),'title':x.get('title'),'minerals':x.get('minerals'),'hit_count':x.get('hit_count')} for x in (msgb.get('hit_layers') or [])[:20]],
        'detail':msgb.get('detail')
    }
    base['cache']={'ttl_seconds':CACHE_TTL_SECONDS}
    return base


async def _retry_failed_core(result:dict):
    car=result.get('car') or {}; bbox=car.get('bbox')
    if not bbox: return result
    jobs=[]; keys=[]
    if not (result.get('sigef') or {}).get('ok'): keys.append('sigef'); jobs.append(query_sigef(bbox))
    if not (result.get('embargos_ibama') or {}).get('ok'): keys.append('embargos_ibama'); jobs.append(query_embargos(bbox))
    if not (result.get('prodes') or {}).get('ok'): keys.append('prodes'); jobs.append(query_prodes(bbox))
    if jobs:
        values=await asyncio.gather(*jobs,return_exceptions=True)
        for k,v in zip(keys,values):
            if isinstance(v,Exception): result[k]={'ok':False,'detail':f'{type(v).__name__}:{v}'}
            else: result[k]=v
    if not (result.get('anm') or {}).get('ok'):
        try: result['anm']=await asyncio.to_thread(query_anm_curl_exact,car.get('geometry'),bbox)
        except Exception as e: result['anm']={'ok':False,'source':'ANM/SIGMINE','detail':f'{type(e).__name__}:{e}'}
    return result


async def _query_autos_resilient(geometry,bbox,attempts=3):
    last={}
    for i in range(attempts):
        try:
            last=await query_ibama_autos(geometry,bbox)
            if last.get('ok'): return last
        except Exception as e: last={'ok':False,'source':'IBAMA/PAMGIA - autos de infração ambiental','detail':f'{type(e).__name__}:{e}'}
        if i<attempts-1: await asyncio.sleep(.7*(i+1))
    return last


async def _safe_async(label:str, awaitable, source:str|None=None):
    try: return await awaitable
    except Exception as e: return {'ok':False,'source':source or label,'detail':f'{type(e).__name__}:{e}'}


async def _safe_thread(label:str, fn, *args, source:str|None=None):
    try: return await asyncio.to_thread(fn,*args)
    except Exception as e: return {'ok':False,'source':source or label,'detail':f'{type(e).__name__}:{e}'}


async def _analyze_uncached(car_code:str):
    result=await analyze_car(car_code); car=result.get('car') or {}
    if not car.get('ok'): raise HTTPException(status_code=404 if car.get('not_found') else 502,detail=_safe_summary(result))
    result=await _retry_failed_core(result); car=result.get('car') or {}
    geometry=car.get('geometry'); bbox=car.get('bbox')
    jobs=[
        _query_autos_resilient(geometry,bbox),
        _safe_async('fire_live',analyze_fire_near_property(geometry,5.0,6),'INPE Programa Queimadas'),
        _safe_async('territorial_constraints',query_territorial_constraints(geometry,bbox),'restrições territoriais'),
        _safe_thread('water_mg',query_outorgas_mg,geometry,bbox,5.0,source='IDE-Sisema / IGAM + ANA'),
        _safe_thread('pivots_ana',query_pivots_ana,geometry,bbox,5.0,source='ANA / SNIRH - Pivôs Centrais'),
        _safe_thread('climate_nasa',query_climate_nasa,geometry,30,source='NASA POWER - Daily API'),
        _safe_async('critical_minerals',query_critical_minerals(geometry,result.get('anm')),'ANM/SIGMINE + SGB/GeoSGB'),
    ]
    vals=await asyncio.gather(*jobs)
    result['autos_ibama'],result['fire_live'],result['territorial_constraints'],result['water_mg'],result['pivots_ana'],result['climate_nasa'],result['critical_minerals']=vals
    result['ide_layers']=await _safe_thread('ide_layers',probe_benchmark,geometry,bbox,source='IDE-Sisema - Solo/Aptidão/Relevo/Uso')
    return result


async def _analyze_with_live_addons(car_code:str,force_refresh:bool=False):
    code=car_code.upper(); now=time.monotonic(); cached=_CACHE.get(code)
    if not force_refresh and cached and now-cached[0] < CACHE_TTL_SECONDS: return copy.deepcopy(cached[1])
    lock=_LOCKS.setdefault(code,asyncio.Lock())
    async with lock:
        now=time.monotonic(); cached=_CACHE.get(code)
        if not force_refresh and cached and now-cached[0] < CACHE_TTL_SECONDS: return copy.deepcopy(cached[1])
        result=await _analyze_uncached(code); _CACHE[code]=(time.monotonic(),copy.deepcopy(result)); return result


async def _build(car_code:str):
    result=await _analyze_with_live_addons(car_code); meta=await asyncio.to_thread(generate_live_report,result,car_code.upper()); return result,meta


async def _background_full_smoke():
    print('RX_REAL_PDF_START',flush=True)
    try:
        result=await _analyze_with_live_addons(TEST_CAR,force_refresh=True)
        meta=await asyncio.to_thread(generate_live_report,result,TEST_CAR)
        log=_public_meta(meta); log['summary']=_report_summary(result)
        print('RX_REAL_PDF_RESULT='+json.dumps(log,ensure_ascii=False,default=str),flush=True); print('RX_REAL_PDF_OK',flush=True)
    except Exception as e: print(f'RX_REAL_PDF_FAIL={type(e).__name__}:{str(e)[:500]}',flush=True)


async def _background_ide_probe():
    print('RX_IDE_LAYER_PROBE_START',flush=True)
    try:
        car=await asyncio.to_thread(fetch_car_live,TEST_CAR)
        if not car.get('ok'):
            print('RX_IDE_LAYER_PROBE_FAIL=car_unavailable:'+json.dumps({k:car.get(k) for k in ('source','detail','not_found','bytes')},ensure_ascii=False,default=str),flush=True); return
        probe=await asyncio.to_thread(probe_benchmark,car.get('geometry'),car.get('bbox'))
        print('RX_IDE_LAYER_PROBE='+json.dumps(probe,ensure_ascii=False,default=str),flush=True)
    except Exception as e: print(f'RX_IDE_LAYER_PROBE_FAIL={type(e).__name__}:{str(e)[:500]}',flush=True)


async def _background_catalog_probe():
    try:
        data=await asyncio.to_thread(benchmark_targets)
        compact={k:{'ok':v.get('ok'),'hit_count':v.get('hit_count'),'hits':[{'name':x.get('name'),'title':x.get('title'),'score':x.get('score')} for x in (v.get('hits') or [])[:12]]} for k,v in data.items()}
        print('RX_IDE_CATALOG='+json.dumps(compact,ensure_ascii=False,default=str),flush=True)
    except Exception as e: print(f'RX_IDE_CATALOG_FAIL={type(e).__name__}:{str(e)[:500]}',flush=True)


@app.on_event('startup')
async def startup_tasks():
    mode=os.getenv('RX_STARTUP_DIAGNOSTIC','off').strip().lower()
    print(f'RX_STARTUP_DIAGNOSTIC={mode}',flush=True)
    if mode=='full': asyncio.create_task(_background_full_smoke())
    elif mode=='ide': asyncio.create_task(_background_ide_probe())
    elif mode=='catalog': asyncio.create_task(_background_catalog_probe())

@app.get('/')
def root(): return {'app':'Raio-X Territorial','service':'report-api','status':'online','version':APP_VERSION,'benchmark_car':TEST_CAR,'cache_ttl_seconds':CACHE_TTL_SECONDS,'startup_diagnostic':os.getenv('RX_STARTUP_DIAGNOSTIC','off')}
@app.head('/')
def root_head(): return Response(status_code=200)
@app.get('/health')
def health(): return {'ok':True,'service':'report-api','version':APP_VERSION}
@app.head('/health')
def health_head(): return Response(status_code=200)
@app.get('/v1/live/fire/{car_code}')
async def live_fire(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'fire':result.get('fire_live')}
@app.get('/v1/live/constraints/{car_code}')
async def live_constraints(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'constraints':result.get('territorial_constraints')}
@app.get('/v1/live/water/{car_code}')
async def live_water(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'water':result.get('water_mg'),'pivots':result.get('pivots_ana'),'climate':result.get('climate_nasa')}
@app.get('/v1/live/ide/{car_code}')
async def live_ide(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'ide_layers':result.get('ide_layers')}
@app.get('/v1/live/minerals/{car_code}')
async def live_minerals(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'critical_minerals':result.get('critical_minerals')}
@app.get('/v1/internal/ide/catalog')
async def ide_catalog(q:str='solo,aptidão,Mapbiomas,declividade,rodovias,APPs'):
    terms=[x.strip() for x in q.split(',') if x.strip()]; return await asyncio.to_thread(search_catalog,terms,100)
@app.get('/v1/internal/ide/probe/{car_code}')
async def ide_probe(car_code:str):
    car=await asyncio.to_thread(fetch_car_live,car_code.upper())
    if not car.get('ok'): raise HTTPException(status_code=404 if car.get('not_found') else 502,detail=car)
    return await asyncio.to_thread(probe_benchmark,car.get('geometry'),car.get('bbox'))
@app.get('/v1/reports/property/{car_code}/meta')
async def report_meta(car_code:str):
    result,meta=await _build(car_code); return {'report':_public_meta(meta),'analysis':_report_summary(result)}
@app.get('/v1/reports/property/{car_code}')
async def report_pdf(car_code:str):
    _,meta=await _build(car_code); pdf=Path(meta['pdf_path'])
    if not pdf.exists(): raise HTTPException(status_code=500,detail='PDF generation completed without output file')
    return FileResponse(path=str(pdf),media_type='application/pdf',filename=f"raio_x_territorial_{car_code.upper()}.pdf",headers={'X-RaioX-Report-ID':meta['report_id'],'X-RaioX-SHA256':meta['sha256']})
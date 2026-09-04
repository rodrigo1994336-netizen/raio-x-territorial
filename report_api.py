from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from deploy_app import analyze_car, _safe_summary, TEST_CAR, query_embargos, query_prodes, query_sigef
from anm_resilient import query_anm_curl_exact
from fire_live import analyze_fire_near_property
from live_extra_sources import query_ibama_autos
from territorial_constraints import query_territorial_constraints
from water_mg import query_outorgas_mg
from pivots_ana import query_pivots_ana
from live_report_adapter_v7 import generate_live_report

app = FastAPI(title='Raio-X Territorial Report API', version='0.16.0-water-irrigation-live-pdf')


def _public_meta(meta: dict):
    return {k: meta.get(k) for k in ('report_id','sha256','bytes','payload_sha256')}


def _report_summary(result: dict):
    base=_safe_summary(result)
    autos=result.get('autos_ibama') or {}; fire=result.get('fire_live') or {}; cons=result.get('territorial_constraints') or {}; water=result.get('water_mg') or {}; piv=result.get('pivots_ana') or {}
    base['autos_ibama']={'ok':autos.get('ok'),'feature_count_bbox':autos.get('feature_count_bbox'),'occurrence_count':autos.get('occurrence_count'),'fine_total':autos.get('fine_total'),'source':autos.get('source'),'deduplicated':autos.get('deduplicated')}
    base['fire_live']={'ok':fire.get('ok'),'latest_file':fire.get('latest_file'),'feed_focus_count':fire.get('feed_focus_count'),'radius_km':fire.get('radius_km'),'inside_count':fire.get('inside_count'),'near_count':fire.get('near_count'),'nearest':fire.get('nearest'),'window_note':fire.get('window_note'),'source':fire.get('source')}
    base['territorial_constraints']={'ok':cons.get('ok'),'area_unique_all_constraints_ha':cons.get('area_unique_all_constraints_ha'),'services':{k:{'ok':v.get('ok'),'label':v.get('label'),'occurrence_count':v.get('occurrence_count'),'area_unique_ha':v.get('area_unique_ha'),'feature_count_bbox':v.get('feature_count_bbox'),'source':v.get('source')} for k,v in (cons.get('services') or {}).items()}}
    base['water_mg']={'ok':water.get('ok'),'layer':water.get('layer'),'feature_count_bbox':water.get('feature_count_bbox'),'inside_count':water.get('inside_count'),'near_count':water.get('near_count'),'radius_km':water.get('radius_km'),'nearest':water.get('nearest'),'layers':water.get('layers'),'source':water.get('source'),'discovery':water.get('discovery')}
    base['pivots_ana']={'ok':piv.get('ok'),'reference_year':piv.get('reference_year'),'feature_count_bbox':piv.get('feature_count_bbox'),'intersection_count':piv.get('intersection_count'),'intersection_area_unique_ha':piv.get('intersection_area_unique_ha'),'near_count':piv.get('near_count'),'radius_km':piv.get('radius_km'),'nearest':piv.get('nearest'),'source':piv.get('source')}
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
            if not isinstance(v,Exception): result[k]=v
    if not (result.get('anm') or {}).get('ok'):
        result['anm']=await asyncio.to_thread(query_anm_curl_exact,car.get('geometry'),bbox)
    return result


async def _analyze_with_live_addons(car_code:str):
    result=await analyze_car(car_code.upper()); car=result.get('car') or {}
    if not car.get('ok'): raise HTTPException(status_code=404 if car.get('not_found') else 502,detail=_safe_summary(result))
    result=await _retry_failed_core(result); car=result.get('car') or {}
    autos_task=query_ibama_autos(car.get('geometry'),car.get('bbox'))
    fire_task=analyze_fire_near_property(car.get('geometry'),5.0,6)
    constraints_task=query_territorial_constraints(car.get('geometry'),car.get('bbox'))
    water_task=asyncio.to_thread(query_outorgas_mg,car.get('geometry'),car.get('bbox'),5.0)
    pivots_task=asyncio.to_thread(query_pivots_ana,car.get('geometry'),car.get('bbox'),5.0)
    result['autos_ibama'],result['fire_live'],result['territorial_constraints'],result['water_mg'],result['pivots_ana']=await asyncio.gather(autos_task,fire_task,constraints_task,water_task,pivots_task)
    return result


async def _build(car_code:str):
    result=await _analyze_with_live_addons(car_code); meta=await asyncio.to_thread(generate_live_report,result,car_code.upper()); return result,meta


@app.on_event('startup')
async def startup_pdf_smoke():
    print('RX_REAL_PDF_START',flush=True)
    try:
        result,meta=await _build(TEST_CAR); log=_public_meta(meta); log['summary']=_report_summary(result)
        print('RX_REAL_PDF_RESULT='+json.dumps(log,ensure_ascii=False,default=str),flush=True); print('RX_REAL_PDF_OK',flush=True)
    except Exception as e: print(f'RX_REAL_PDF_FAIL={type(e).__name__}:{str(e)[:500]}',flush=True)

@app.get('/')
def root(): return {'app':'Raio-X Territorial','service':'report-api','status':'online','version':'0.16.0-water-irrigation-live-pdf','benchmark_car':TEST_CAR}
@app.get('/health')
def health(): return {'ok':True,'service':'report-api'}
@app.get('/v1/live/fire/{car_code}')
async def live_fire(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'fire':result.get('fire_live')}
@app.get('/v1/live/constraints/{car_code}')
async def live_constraints(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'constraints':result.get('territorial_constraints')}
@app.get('/v1/live/water/{car_code}')
async def live_water(car_code:str):
    result=await _analyze_with_live_addons(car_code); return {'car':_safe_summary(result).get('car'),'water':result.get('water_mg'),'pivots':result.get('pivots_ana')}
@app.get('/v1/reports/property/{car_code}/meta')
async def report_meta(car_code:str):
    result,meta=await _build(car_code); return {'report':_public_meta(meta),'analysis':_report_summary(result)}
@app.get('/v1/reports/property/{car_code}')
async def report_pdf(car_code:str):
    _,meta=await _build(car_code); pdf=Path(meta['pdf_path'])
    if not pdf.exists(): raise HTTPException(status_code=500,detail='PDF generation completed without output file')
    return FileResponse(path=str(pdf),media_type='application/pdf',filename=f"raio_x_territorial_{car_code.upper()}.pdf",headers={'X-RaioX-Report-ID':meta['report_id'],'X-RaioX-SHA256':meta['sha256']})

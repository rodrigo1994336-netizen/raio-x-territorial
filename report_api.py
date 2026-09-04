from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from deploy_app import analyze_car, _safe_summary, TEST_CAR
from live_extra_sources import query_ibama_autos
from sicar_detail_sources import query_sicar_details
from live_report_adapter_v3 import generate_live_report

app = FastAPI(title='Raio-X Territorial Report API', version='0.14.11-live-pdf')


def _public_meta(meta: dict):
    return {k: meta.get(k) for k in ('report_id','sha256','bytes','payload_sha256')}


def _safe_car_fields(result: dict):
    props=((result.get('car') or {}).get('properties') or {})
    allow=('area','reserva','app','veget','consolid','fiscal','condicao','status','tipo','municipio','uf','cod_','data','modulo','remanesc','restrit','preserv')
    deny=('cpf','cnpj','nome','propriet','possuidor','email','fone','telefone','endereco','endereço')
    out={}
    for k,v in props.items():
        lk=str(k).lower()
        if any(d in lk for d in deny): continue
        if any(a in lk for a in allow): out[str(k)]=v
    return out


def _report_summary(result: dict):
    base = _safe_summary(result)
    autos = result.get('autos_ibama') or {}
    details = result.get('sicar_details') or {}
    base['car_non_pii_fields'] = _safe_car_fields(result)
    base['autos_ibama'] = {'ok':autos.get('ok'),'feature_count_bbox':autos.get('feature_count_bbox'),'occurrence_count':autos.get('occurrence_count'),'fine_total':autos.get('fine_total'),'source':autos.get('source'),'deduplicated':autos.get('deduplicated')}
    base['sicar_details'] = {'ok':details.get('ok'),'discovery_total':details.get('discovery_total'),'feature_type_count':details.get('feature_type_count'),'raw_names':details.get('raw_names'),'selected_layers':details.get('selected_layers'),'summary':details.get('summary'),'source':details.get('source')}
    return base


async def _build(car_code: str):
    result = await analyze_car(car_code.upper())
    car = result.get('car') or {}
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail=_safe_summary(result))
    autos_task = query_ibama_autos(car.get('geometry'), car.get('bbox'))
    sicar_task = asyncio.to_thread(query_sicar_details, car.get('geometry'), car.get('bbox'), 10)
    result['autos_ibama'], result['sicar_details'] = await asyncio.gather(autos_task, sicar_task)
    meta = await asyncio.to_thread(generate_live_report, result, car_code.upper())
    return result, meta


@app.on_event('startup')
async def startup_pdf_smoke():
    print('RX_REAL_PDF_START', flush=True)
    try:
        result, meta = await _build(TEST_CAR)
        log = _public_meta(meta); log['summary'] = _report_summary(result)
        print('RX_REAL_PDF_RESULT=' + json.dumps(log, ensure_ascii=False, default=str), flush=True)
        print('RX_REAL_PDF_OK', flush=True)
    except Exception as e:
        print(f'RX_REAL_PDF_FAIL={type(e).__name__}:{str(e)[:500]}', flush=True)

@app.get('/')
def root(): return {'app':'Raio-X Territorial','service':'report-api','status':'online','version':'0.14.11-live-pdf','benchmark_car':TEST_CAR}
@app.get('/health')
def health(): return {'ok':True,'service':'report-api'}
@app.get('/v1/reports/property/{car_code}/meta')
async def report_meta(car_code: str):
    result, meta = await _build(car_code); return {'report':_public_meta(meta),'analysis':_report_summary(result)}
@app.get('/v1/reports/property/{car_code}')
async def report_pdf(car_code: str):
    _, meta = await _build(car_code); pdf=Path(meta['pdf_path'])
    if not pdf.exists(): raise HTTPException(status_code=500,detail='PDF generation completed without output file')
    return FileResponse(path=str(pdf),media_type='application/pdf',filename=f"raio_x_territorial_{car_code.upper()}.pdf",headers={'X-RaioX-Report-ID':meta['report_id'],'X-RaioX-SHA256':meta['sha256']})

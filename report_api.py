from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from deploy_app import analyze_car, _safe_summary, TEST_CAR
from live_report_adapter import generate_live_report

app = FastAPI(title='Raio-X Territorial Report API', version='0.14.7-live-pdf')


def _public_meta(meta: dict):
    return {k: meta.get(k) for k in ('report_id','sha256','bytes','payload_sha256')}


async def _build(car_code: str):
    result = await analyze_car(car_code.upper())
    car = result.get('car') or {}
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail=_safe_summary(result))
    meta = await asyncio.to_thread(generate_live_report, result, car_code.upper())
    return result, meta


@app.on_event('startup')
async def startup_pdf_smoke():
    print('RX_REAL_PDF_START', flush=True)
    try:
        result, meta = await _build(TEST_CAR)
        log = _public_meta(meta)
        log['summary'] = _safe_summary(result)
        print('RX_REAL_PDF_RESULT=' + json.dumps(log, ensure_ascii=False, default=str), flush=True)
        print('RX_REAL_PDF_OK', flush=True)
    except Exception as e:
        print(f'RX_REAL_PDF_FAIL={type(e).__name__}:{str(e)[:500]}', flush=True)


@app.get('/')
def root():
    return {
        'app':'Raio-X Territorial',
        'service':'report-api',
        'status':'online',
        'version':'0.14.7-live-pdf',
        'benchmark_car':TEST_CAR,
    }


@app.get('/health')
def health():
    return {'ok':True,'service':'report-api'}


@app.get('/v1/reports/property/{car_code}/meta')
async def report_meta(car_code: str):
    result, meta = await _build(car_code)
    return {'report': _public_meta(meta), 'analysis': _safe_summary(result)}


@app.get('/v1/reports/property/{car_code}')
async def report_pdf(car_code: str):
    _, meta = await _build(car_code)
    pdf = Path(meta['pdf_path'])
    if not pdf.exists():
        raise HTTPException(status_code=500, detail='PDF generation completed without output file')
    headers = {
        'X-RaioX-Report-ID': meta['report_id'],
        'X-RaioX-SHA256': meta['sha256'],
    }
    return FileResponse(
        path=str(pdf),
        media_type='application/pdf',
        filename=f"raio_x_territorial_{car_code.upper()}.pdf",
        headers=headers,
    )

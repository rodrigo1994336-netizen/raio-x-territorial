from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

import report_api as base
import report_v9_patch  # keep progressive quick/deep endpoints
import parity_public_layers  # keep SFB/IPHAN in territorial engine
from live_report_adapter_v13 import generate_live_report


base.generate_live_report=generate_live_report
base.APP_VERSION='0.25.0-progressive-v13-complete-public-expansion'

# Replace only the two report endpoints so the UI can propagate a public farm name
# discovered during search without mutating the cached analysis object.
paths={'/v1/reports/property/{car_code}','/v1/reports/property/{car_code}/meta'}
base.app.router.routes=[r for r in base.app.router.routes if getattr(r,'path',None) not in paths]


async def _build_v13(car_code:str,property_name:str|None=None):
    code=car_code.upper()
    result=await base._analyze_with_live_addons(code)
    working=dict(result)
    if property_name and str(property_name).strip():working['_requested_property_name']=str(property_name).strip()[:120]
    async with base._REPORT_SEMAPHORE:
        try:meta=await asyncio.to_thread(generate_live_report,working,code)
        finally:base._release_memory()
    return result,meta


@base.app.get('/v1/reports/property/{car_code}/meta')
async def report_meta_v13(car_code:str,property_name:str|None=None):
    result,meta=await _build_v13(car_code,property_name)
    public=base._public_meta(meta);public['property_name']=meta.get('property_name')
    return {'report':public,'analysis':base._report_summary(result)}


@base.app.get('/v1/reports/property/{car_code}')
async def report_pdf_v13(car_code:str,property_name:str|None=None):
    _,meta=await _build_v13(car_code,property_name);pdf=Path(meta['pdf_path'])
    if not pdf.exists():raise HTTPException(status_code=500,detail='PDF generation completed without output file')
    return FileResponse(path=str(pdf),media_type='application/pdf',filename=f"raio_x_territorial_{car_code.upper()}.pdf",headers={'X-RaioX-Report-ID':meta['report_id'],'X-RaioX-SHA256':meta['sha256'],'X-RaioX-Report-Version':'V13'})


print('RX_REPORT_V13_RUNTIME=complete_public_expansion_identity',flush=True)

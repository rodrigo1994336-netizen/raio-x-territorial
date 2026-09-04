from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException

import portal_v8
from agropecuaria import query_ppm
from groundwater_siagas import query_groundwater
from safras_ibge import query_safras

app=portal_v8.app


def _fake_car(mun:str):
    if not re.fullmatch(r'\d{7}',str(mun or '')):raise HTTPException(status_code=422,detail='Código municipal inválido')
    return f'XX-{mun}-'+'0'*32


@app.get('/v1/map/agro-context')
async def agro_context(municipality_code:str):
    code=_fake_car(municipality_code)
    ppm,safras=await asyncio.gather(query_ppm(code),query_safras(code))
    bov=None
    for x in ppm.get('series') or []:
        if 'bovin' in str(x.get('herd') or '').lower():bov=x;break
    top=[]
    for p in safras.get('products') or []:
        vals=[]
        for m in p.get('metrics') or []:
            if m.get('value') is not None:vals.append(m)
        if vals:top.append({'product':p.get('product'),'metrics':vals[:3],'year':p.get('year')})
    return {'ok':bool(ppm.get('ok') or safras.get('ok')),'municipality_code':municipality_code,'bovines':bov,'crops':top[:5],'livestock_source':ppm.get('source'),'crop_source':safras.get('source'),'note':'Contexto municipal. Não classifica automaticamente uma fazenda como apta para pecuária ou lavoura.'}


@app.get('/v1/map/groundwater-context')
async def groundwater_context(lat:float,lon:float,radius_km:float=20):
    if not(-90<=lat<=90 and -180<=lon<=180):raise HTTPException(status_code=422,detail='Coordenadas inválidas')
    return await query_groundwater({'type':'Point','coordinates':[lon,lat]},max(5,min(float(radius_km),40)))

print('RX_MAP_CONTEXT=agro_groundwater_lightweight',flush=True)

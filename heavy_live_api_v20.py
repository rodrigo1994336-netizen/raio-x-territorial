from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

import report_api
from car_resilient import fetch_car_live_resilient
from mapbiomas_coverage import query_mapbiomas_coverage
from terrain_srtm import query_terrain_srtm
from landuse_profile_v39 import classify_landuse_profile

app=report_api.app


async def _car(code:str):
    car=await asyncio.to_thread(fetch_car_live_resilient,code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR indisponível')
    return car


@app.get('/v1/heavy/agro-raster/{car_code}')
async def agro_raster(car_code:str):
    car=await _car(car_code);geom=car.get('geometry')
    mb,t=await asyncio.gather(
        asyncio.to_thread(query_mapbiomas_coverage,geom,2025),
        asyncio.to_thread(query_terrain_srtm,geom),
        return_exceptions=True
    )
    if isinstance(mb,Exception):mb={'ok':False,'source':'MapBiomas Brasil — Coleção 11','detail':f'{type(mb).__name__}:{str(mb)[:220]}'}
    if isinstance(t,Exception):t={'ok':False,'source':'SRTM ~30 m','detail':f'{type(t).__name__}:{str(t)[:220]}'}
    return {'ok':bool(mb.get('ok') or t.get('ok')),'car_code':car_code.upper(),'mapbiomas':mb,'landuse_profile':classify_landuse_profile(mb),'terrain_srtm':t,'execution':'report-service-heavy-worker'}


@app.get('/v1/heavy/landuse-profile/{car_code}')
async def landuse_profile(car_code:str):
    car=await _car(car_code);geom=car.get('geometry')
    try:mb=await asyncio.to_thread(query_mapbiomas_coverage,geom,2025)
    except Exception as e:mb={'ok':False,'source':'MapBiomas Brasil — Coleção 11','detail':f'{type(e).__name__}:{str(e)[:220]}'}
    profile=classify_landuse_profile(mb)
    return {'ok':profile.get('ok',False),'car_code':car_code.upper(),'profile':profile,'execution':'report-service-heavy-worker'}


class LanduseCandidate(BaseModel):
    car_code:str
    geometry:dict[str,Any]


class LanduseBatch(BaseModel):
    candidates:list[LanduseCandidate]=Field(default_factory=list,max_length=16)


@app.post('/v1/heavy/landuse-profiles')
async def landuse_profiles(payload:LanduseBatch):
    candidates=payload.candidates[:16]
    sem=asyncio.Semaphore(3)
    async def one(item:LanduseCandidate):
        async with sem:
            try:
                mb=await asyncio.wait_for(asyncio.to_thread(query_mapbiomas_coverage,item.geometry,2025),timeout=24)
                profile=classify_landuse_profile(mb)
                return {'car_code':item.car_code.upper(),'ok':profile.get('ok',False),'profile':profile}
            except Exception as e:
                return {'car_code':item.car_code.upper(),'ok':False,'profile':{'ok':False,'profile_codes':[],'profiles':[],'detail':f'{type(e).__name__}:{str(e)[:180]}','interpretation':'Perfil não concluído; falha da fonte não é ausência de atividade.'}}
    rows=await asyncio.gather(*(one(x) for x in candidates))
    return {'ok':any(x.get('ok') for x in rows),'items':rows,'count':len(rows),'source':'MapBiomas Brasil — Coleção 11','execution':'report-service-heavy-worker','note':'Perfis de uso/cobertura do solo, não declaração de atividade econômica.'}


print('RX_HEAVY_LIVE_API=V39_MAPBIOMAS_SRTM_LANDUSE_BATCH',flush=True)

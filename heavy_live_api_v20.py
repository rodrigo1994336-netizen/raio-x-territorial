from __future__ import annotations

import asyncio
from fastapi import HTTPException

import report_api
from car_resilient import fetch_car_live_resilient
from mapbiomas_coverage import query_mapbiomas_coverage
from terrain_srtm import query_terrain_srtm

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
    return {'ok':bool(mb.get('ok') or t.get('ok')),'car_code':car_code.upper(),'mapbiomas':mb,'terrain_srtm':t,'execution':'report-service-heavy-worker'}

print('RX_HEAVY_LIVE_API=V20_MAPBIOMAS_SRTM',flush=True)

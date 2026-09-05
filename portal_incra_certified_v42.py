from __future__ import annotations

import asyncio
from fastapi import HTTPException

import portal_v8
from incra_snci_public_v42 import viewport, capabilities, background_probe

app=portal_v8.app


@app.get('/v1/live/incra-certified/status/{uf}')
async def incra_certified_status(uf:str):
    code=str(uf or '').upper().strip()
    if len(code)!=2:raise HTTPException(status_code=422,detail='UF inválida.')
    return await asyncio.to_thread(capabilities,code)


@app.get('/v1/live/incra-certified/viewport')
async def incra_certified_viewport(west:float,south:float,east:float,north:float,uf:str,limit:int=50):
    if not (-180<=west<east<=180 and -90<=south<north<=90):raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    if max(east-west,north-south)>1.5:raise HTTPException(status_code=422,detail='Aproxime o mapa para consultar certificações fundiárias.')
    return await asyncio.to_thread(viewport,west,south,east,north,uf,limit)


background_probe('MG')
print('RX_PORTAL_INCRA_CERTIFIED_V42=official_wfs_diagnostic_routes',flush=True)

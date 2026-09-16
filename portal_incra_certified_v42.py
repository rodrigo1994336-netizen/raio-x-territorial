from __future__ import annotations

import asyncio
from fastapi import HTTPException, Request

import portal_v8
import incra_snci_public_v42 as snci
from external_process_lifecycle import RequestDisconnected, wait_for_cancelling_processes
from incra_snci_public_v42 import viewport, capabilities, background_probe

app=portal_v8.app
# Tetos declarados pelo modulo que implementa cada consulta (prazo de rede + folga do processo + carencia
# de parada), mais a folga abaixo.
# Folga ARBITRADA (não medida) para a leitura do XML/GeoJSON, que corre na mesma thread depois que o
# curl volta — trabalho de CPU, sem rede, sobre no máximo 80 feições (maxFeatures). Medido em 16/09 nesta
# máquina: a rota inteira responde em 3-4 ms com o cache quente e a fonte devolvendo erro; o caminho com
# XML de verdade não foi exercitado porque o WFS do INCRA vem com XML inválido hoje.
_PARSE_S=6.0
_STATUS_S=round(snci.CAPABILITIES_WORST_CASE_S+_PARSE_S,1)
_VIEWPORT_S=round(snci.VIEWPORT_WORST_CASE_S+_PARSE_S,1)


@app.get('/v1/live/incra-certified/status/{uf}')
async def incra_certified_status(uf:str,request:Request):
    code=str(uf or '').upper().strip()
    if len(code)!=2:raise HTTPException(status_code=422,detail='UF inválida.')
    # Dentro do escopo: prazo e desistencia do cliente derrubam o curl do INCRA desta consulta.
    try:
        return await wait_for_cancelling_processes(asyncio.to_thread(capabilities,code),_STATUS_S,request=request)
    except asyncio.TimeoutError:raise HTTPException(status_code=504,detail='incra_certified_status_timeout')
    except RequestDisconnected:raise HTTPException(status_code=499,detail='client_disconnected')


@app.get('/v1/live/incra-certified/viewport')
async def incra_certified_viewport(west:float,south:float,east:float,north:float,uf:str,request:Request,limit:int=50):
    if not (-180<=west<east<=180 and -90<=south<north<=90):raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    if max(east-west,north-south)>1.5:raise HTTPException(status_code=422,detail='Aproxime o mapa para consultar certificações fundiárias.')
    # Dentro do escopo: o mapa do cliente troca de area o tempo todo; o curl da area abandonada cai junto.
    try:
        return await wait_for_cancelling_processes(
            asyncio.to_thread(viewport,west,south,east,north,uf,limit),_VIEWPORT_S,request=request)
    except asyncio.TimeoutError:raise HTTPException(status_code=504,detail='incra_certified_viewport_timeout')
    except RequestDisconnected:raise HTTPException(status_code=499,detail='client_disconnected')


background_probe('MG')
print('RX_PORTAL_INCRA_CERTIFIED_V42=official_wfs_diagnostic_routes',flush=True)

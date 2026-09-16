from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode
from typing import Any

from fastapi import HTTPException, Request
from shapely.geometry import shape, mapping

import deploy_app
from anm_resilient import ANM_QUERY
from critical_minerals import MINERAL_TERMS, _capabilities, _classify_text
from deploy_app import _curl
from external_process_lifecycle import RequestDisconnected, wait_for_cancelling_processes

# Teto desta camada: uma chamada do curl padrao do deploy_app (prazo declarado por ele), mais a folga
# abaixo.
# Folga ARBITRADA (não medida) para a simplificação das geometrias e a classificação dos processos, que
# correm na mesma thread depois que o curl volta: até 300 feições da ANM, trabalho de CPU sem rede.
# Medido em 16/09 nesta máquina, num bbox de 1 grau com limit=300: 0,64 s a rota inteira (rede inclusa)
# contra os 65,8 s do teto. É a folga que domina o teto, e é assim de propósito: grande só adia o 504.
_GEOMETRY_S=20.0
_ANM_VIEWPORT_S=round(deploy_app.CURL_WORST_CASE_S+_GEOMETRY_S,1)


def _norm_code(v:str|None) -> str:
    x=(v or 'terras_raras').strip().lower()
    return x if x in MINERAL_TERMS else 'terras_raras'


def _safe_props(props:dict[str,Any]) -> dict[str,Any]:
    safe={}
    for k,v in props.items():
        if v in (None,''):continue
        lk=str(k).lower()
        if any(x in lk for x in ('process','numero','número','subst','fase','titular','evento','area','área','ano','uf','municip')):
            safe[str(k)]=v
        if len(safe)>=14:break
    return safe


async def mineral_wms_layers(mineral:str='terras_raras'):
    code=_norm_code(mineral)
    layers,err=await _capabilities()
    if err:
        return {'ok':False,'mineral':code,'layers':[],'detail':err,'source':'SGB / GeoSGB WMS'}
    selected=[x for x in layers if code in (x.get('minerals') or [])]
    return {
        'ok':True,'mineral':code,'source':'Serviço Geológico do Brasil',
        'wms_url':'https://geoservicos.sgb.gov.br/geoserver/ows',
        'layers':selected[:40],
        'interpretation':'Camadas de interesse/potencial geológico. Não significam jazida, recurso ou reserva economicamente explotável.'
    }


async def anm_mineral_viewport(west:float,south:float,east:float,north:float,request:Request,mineral:str='terras_raras',limit:int=300):
    code=_norm_code(mineral)
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise HTTPException(status_code=422,detail='Área inválida.')
    span=max(east-west,north-south)
    if span>8:
        raise HTTPException(status_code=422,detail='Aproxime o mapa para consultar processos minerários.')
    cap=max(10,min(int(limit or 300),600))
    params={
        'f':'geojson','where':'1=1','geometry':f'{west},{south},{east},{north}','geometryType':'esriGeometryEnvelope',
        'inSR':'4326','spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','outSR':'4326',
        'resultRecordCount':str(cap)
    }
    # Dentro do escopo: o cliente arrasta o mapa e a camada anterior deixa de interessar; o curl dela cai.
    try:
        raw=await wait_for_cancelling_processes(
            asyncio.to_thread(_curl,ANM_QUERY+'?'+urlencode(params),True),_ANM_VIEWPORT_S,request=request)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504,detail='ANM não respondeu dentro do prazo desta consulta.')
    except RequestDisconnected:
        raise HTTPException(status_code=499,detail='client_disconnected')
    if not raw.get('ok'):
        raise HTTPException(status_code=502,detail='ANM indisponível: '+str(raw.get('detail') or raw.get('preview') or 'falha')[:180])
    data=raw.get('json') or {};features=data.get('features') or [];out=[]
    tol=max(0.00001,min(0.001,span/3000 if span else 0.00001))
    for f in features:
        props=f.get('properties') or {}
        classes=_classify_text(json.dumps(props,ensure_ascii=False,default=str))
        if code not in classes:continue
        try:g=shape(f.get('geometry')).simplify(tol,preserve_topology=True)
        except Exception:continue
        out.append({'type':'Feature','geometry':mapping(g),'properties':{**_safe_props(props),'rx_minerals':classes}})
        if len(out)>=cap:break
    return {
        'type':'FeatureCollection','features':out,'mineral':code,
        'source':'ANM / SIGMINE — processos minerários filtrados na área visível',
        'candidate_count':len(features),'match_count':len(out),'truncated':len(features)>=cap,
        'interpretation':'Processo minerário relacionado ao termo mineral não comprova jazida, teor, recurso ou reserva.'
    }


def register_map_mineral_routes(app):
    app.get('/v1/map/minerals/layers')(mineral_wms_layers)
    app.get('/v1/map/minerals/anm')(anm_mineral_viewport)

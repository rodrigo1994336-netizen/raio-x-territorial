from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from fastapi import HTTPException
from shapely.geometry import Point, mapping, shape

import portal_v8
from deploy_app import SICAR, _curl

app = portal_v8.app


def _type_name(uf: str) -> str:
    code=(uf or '').strip().upper()
    if len(code) != 2:
        raise HTTPException(status_code=422, detail='UF inválida para consulta SICAR.')
    return f"sicar:sicar_imoveis_{'DF' if code == 'DF' else code.lower()}"


async def _fetch_sicar_bbox(west: float, south: float, east: float, north: float, uf: str, limit: int) -> dict:
    cap=max(1,min(int(limit or 40),50))
    params={
        'service':'WFS',
        'version':'1.0.0',
        'request':'GetFeature',
        'typeName':_type_name(uf),
        'outputFormat':'application/json',
        'srsName':'EPSG:4674',
        'bbox':f'{west},{south},{east},{north},EPSG:4674',
        'maxFeatures':str(cap),
    }
    url=SICAR+'?'+urlencode(params)
    raw=await asyncio.to_thread(_curl,url,True)
    if not raw.get('ok'):
        raise HTTPException(status_code=502,detail='SICAR indisponível no momento: '+str(raw.get('detail') or raw.get('preview') or 'falha de rede')[:220])
    data=raw.get('json') or {}
    if data.get('exceptions') or data.get('ExceptionReport'):
        raise HTTPException(status_code=502,detail='SICAR retornou erro de serviço.')
    return {'data':data,'bytes':raw.get('bytes',0),'cap':cap}


async def live_sicar_viewport_resilient(
    west: float,
    south: float,
    east: float,
    north: float,
    uf: str | None=None,
    limit: int=50,
):
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    span=max(east-west,north-south)
    if span>1.5:
        raise HTTPException(status_code=422,detail='Aproxime o mapa para carregar os imóveis rurais.')
    if not uf:
        center_lat=(south+north)/2
        center_lon=(west+east)/2
        uf=await portal_v8.base._reverse_uf(center_lat,center_lon)
    uf=uf.upper()
    fetched=await _fetch_sicar_bbox(west,south,east,north,uf,limit)
    features=(fetched['data'].get('features') or [])
    # Map display only: simplify geometry while preserving topology. Full geometry is
    # fetched again by CAR when the user opens the property analysis/export.
    tolerance=max(0.000002,min(0.00004,span/3500 if span else 0.000002))
    out=[]
    for f in features:
        try:
            geom=f.get('geometry')
            if not geom:
                continue
            g=shape(geom).simplify(tolerance,preserve_topology=True)
            props=f.get('properties') or {}
            out.append({
                'type':'Feature',
                'geometry':mapping(g),
                'properties':{k:props.get(k) for k in (
                    'cod_imovel','area','municipio','uf','status_imovel','condicao','tipo_imovel','m_fiscal'
                )},
            })
        except Exception:
            continue
    return {
        'type':'FeatureCollection',
        'features':out,
        'uf':uf,
        'source':'SICAR/WFS público · transporte resiliente',
        'truncated':len(features)>=fetched['cap'],
        'source_bytes':fetched['bytes'],
    }


async def resolve_point_resilient(lat: float, lon: float):
    if not (-90<=lat<=90 and -180<=lon<=180):
        raise HTTPException(status_code=422,detail='Coordenadas inválidas.')
    uf=await portal_v8.base._reverse_uf(lat,lon)
    # Small envelope to obtain candidates, then exact point-in-polygon locally.
    eps=0.0015
    fetched=await _fetch_sicar_bbox(lon-eps,lat-eps,lon+eps,lat+eps,uf,30)
    features=fetched['data'].get('features') or []
    point=Point(float(lon),float(lat))
    exact=[]
    for f in features:
        try:
            g=shape(f.get('geometry'))
            if g.contains(point) or g.touches(point):
                exact.append(f)
        except Exception:
            continue
    chosen=exact[0] if exact else None
    if not chosen:
        raise HTTPException(status_code=404,detail='Nenhum imóvel do SICAR foi localizado exatamente neste ponto.')
    props=chosen.get('properties') or {}
    return {
        'ok':True,
        'source':'SICAR/WFS público · transporte resiliente',
        'uf':uf,
        'property':{
            'car_code':props.get('cod_imovel'),
            'municipality':props.get('municipio'),
            'uf':props.get('uf') or uf,
            'area_ha':props.get('area'),
            'status':props.get('status_imovel'),
            'condition':props.get('condicao'),
            'type':props.get('tipo_imovel'),
            'fiscal_modules':props.get('m_fiscal'),
        },
        'geometry':chosen.get('geometry'),
        'candidate_count':len(features),
        'exact_count':len(exact),
    }


# Replace the two map-facing SICAR routes that used direct httpx transport.
app.router.routes = [
    r for r in app.router.routes
    if getattr(r,'path',None) not in {'/v1/live/sicar/viewport','/v1/live/resolve'}
]
app.get('/v1/live/sicar/viewport')(live_sicar_viewport_resilient)
app.get('/v1/live/resolve')(resolve_point_resilient)

# Keep direct function references aligned so internal smoke tests exercise the same code.
portal_v8.live_sicar_viewport = live_sicar_viewport_resilient
portal_v8.base.resolve_point = resolve_point_resilient

print('RX_SICAR_MAP_TRANSPORT=resilient_curl',flush=True)

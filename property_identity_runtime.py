from __future__ import annotations

import asyncio
import math
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from shapely.geometry import shape

import portal_v8
from car_resilient import fetch_car_live_resilient, CAR_RE
from deploy_app import SIGEF_MIRROR, _curl

app=portal_v8.app
_CACHE:dict[str,tuple[float,dict[str,Any]]]={}
TTL_SECONDS=3600

_NAME_FIELDS=(
    'nome_imovel','denominacao','nome_area','nom_imovel','nome_fazenda','fazenda','nome_propriedade'
)
_GENERIC={'IMOVEL RURAL','IMÓVEL RURAL','AREA CERTIFICADA SIGEF','ÁREA CERTIFICADA SIGEF','SEM DENOMINACAO','SEM DENOMINAÇÃO'}


def _clean_name(value:Any)->str|None:
    s=' '.join(str(value or '').strip().split())
    if len(s)<3:return None
    if s.upper() in _GENERIC:return None
    if s.upper().startswith('IMÓVEL RURAL —') or s.upper().startswith('IMOVEL RURAL -'):return None
    return s[:180]


def _first_name(props:dict[str,Any])->str|None:
    for key in _NAME_FIELDS:
        n=_clean_name(props.get(key))
        if n:return n
    return None


def _sigef_candidates(car_geom:dict[str,Any],bbox:list[float]):
    env=','.join(str(float(x)) for x in bbox)
    params={
        'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope',
        'inSR':'4326','spatialRel':'esriSpatialRelIntersects',
        'outFields':'parcela_co,codigo_imo,nome_area,registro_m,registro_d,municipio_,uf_id,status,situacao_i',
        'returnGeometry':'true','outSR':'4326','resultRecordCount':'80'
    }
    raw=_curl(SIGEF_MIRROR+'?'+urlencode(params),True)
    if not raw.get('ok'):
        return {'ok':False,'detail':raw.get('detail') or raw.get('preview'),'items':[]}
    data=raw.get('json') or {};features=data.get('features') or []
    try:car=shape(car_geom);car_area=max(float(car.area),1e-12);car_centroid=car.centroid
    except Exception as exc:return {'ok':False,'detail':f'geometry:{type(exc).__name__}:{exc}','items':[]}
    items=[]
    for f in features:
        props=f.get('properties') or {};name=_clean_name(props.get('nome_area'))
        if not name:continue
        try:
            g=shape(f.get('geometry'))
            if g.is_empty or not g.intersects(car):continue
            inter=car.intersection(g)
            overlap=float(inter.area/car_area) if not inter.is_empty else 0.0
            area_ratio=float(g.area/car_area) if car_area else math.inf
            centroid_inside=bool(g.contains(car_centroid) or g.touches(car_centroid))
        except Exception:continue
        # Strong overlap is required before a SIGEF area name can be promoted as the farm identity.
        # This avoids assigning a neighboring certified parcel name to the selected CAR.
        score=overlap
        if centroid_inside:score+=0.08
        if 0.50<=area_ratio<=2.0:score+=0.06
        items.append({
            'name':name,'overlap_ratio':round(overlap,4),'area_ratio':round(area_ratio,4),
            'centroid_inside':centroid_inside,'score':round(score,4),
            'parcel_code':props.get('parcela_co'),'property_code':props.get('codigo_imo'),
            'registry':props.get('registro_m') or props.get('registro_d'),
            'municipality':props.get('municipio_'),'uf':props.get('uf_id'),
            'source':'SIGEF/INCRA — espelho público IBAMA/PAMGIA'
        })
    items.sort(key=lambda x:x['score'],reverse=True)
    return {'ok':True,'items':items,'count':len(items)}


def resolve_property_identity_sync(car_code:str)->dict[str,Any]:
    code=str(car_code or '').strip().upper()
    if not CAR_RE.match(code):
        return {'ok':False,'car_code':code,'detail':'invalid_car_format'}
    now=time.monotonic();cached=_CACHE.get(code)
    if cached and now-cached[0]<TTL_SECONDS:return dict(cached[1])
    car=fetch_car_live_resilient(code)
    if not car.get('ok'):
        out={'ok':False,'car_code':code,'detail':car.get('detail') or 'CAR não localizado','source':'SICAR'}
        _CACHE[code]=(now,out);return out
    props=car.get('properties') or {}
    direct=_first_name(props)
    if direct:
        out={'ok':True,'car_code':code,'name':direct,'source':'SICAR','confidence':'high','method':'explicit_sicar_field','candidates':[]}
        _CACHE[code]=(now,out);return out
    sig=_sigef_candidates(car.get('geometry'),car.get('bbox') or [])
    items=sig.get('items') or []
    chosen=None
    if items:
        top=items[0];second=items[1] if len(items)>1 else None
        # Promote only when the certified area covers most of the CAR and is not ambiguous.
        strong=top.get('overlap_ratio',0)>=0.72 or (top.get('centroid_inside') and top.get('overlap_ratio',0)>=0.58)
        unambiguous=(second is None) or (top.get('score',0)-second.get('score',0)>=0.12) or (top.get('name')==second.get('name'))
        if strong and unambiguous:chosen=top
    if chosen:
        out={
            'ok':True,'car_code':code,'name':chosen['name'],'source':chosen['source'],
            'confidence':'medium_high','method':'strong_sigef_overlap','overlap_ratio':chosen['overlap_ratio'],
            'registry':chosen.get('registry'),'parcel_code':chosen.get('parcel_code'),'candidates':items[:5]
        }
    else:
        out={
            'ok':True,'car_code':code,'name':None,'source':'SICAR + SIGEF',
            'confidence':'unresolved','method':'no_safe_public_name','candidates':items[:5],
            'note':'Nenhum nome público pôde ser atribuído com segurança. O sistema não inventa a denominação da fazenda.'
        }
    _CACHE[code]=(now,out)
    if len(_CACHE)>500:
        oldest=sorted(_CACHE.items(),key=lambda kv:kv[1][0])[:100]
        for k,_ in oldest:_CACHE.pop(k,None)
    return out


@app.get('/v1/live/property-identity/{car_code}')
async def property_identity(car_code:str):
    out=await asyncio.to_thread(resolve_property_identity_sync,car_code)
    if not out.get('ok'):
        raise HTTPException(status_code=404 if out.get('detail')!='invalid_car_format' else 422,detail=out)
    return out


print('RX_PROPERTY_IDENTITY=public_name_resolver_sicar_sigef_overlap',flush=True)

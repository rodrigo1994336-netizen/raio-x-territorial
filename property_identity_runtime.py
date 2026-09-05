from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException
from shapely.geometry import Point, shape

import portal_v8
import public_property_name_seed_v43 as seed
from car_resilient import fetch_car_live_resilient, CAR_RE
from deploy_app import SIGEF_MIRROR, _curl

app=portal_v8.app
_CACHE:dict[str,tuple[float,dict[str,Any]]]={}
TTL_SECONDS=3600

_NAME_FIELDS=(
    'nome_imovel','denominacao','nome_area','nom_imovel','nome_fazenda','fazenda','nome_propriedade'
)
_GENERIC={
    'IMOVEL RURAL','IMÓVEL RURAL','AREA CERTIFICADA SIGEF','ÁREA CERTIFICADA SIGEF',
    'SEM DENOMINACAO','SEM DENOMINAÇÃO','FAZENDA','SITIO','SÍTIO'
}
_OSM_ENDPOINTS=(
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
)
_OSM_SOURCE='OpenStreetMap contributors — denominação geográfica pública (ODbL)'


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


def _osm_named_farms_bbox(west:float,south:float,east:float,north:float,limit:int=60)->dict[str,Any]:
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        return {'ok':False,'items':[],'detail':'invalid_bbox','source':_OSM_SOURCE}
    if max(east-west,north-south)>1.5:
        return {'ok':False,'items':[],'detail':'bbox_too_wide','source':_OSM_SOURCE}
    cap=max(1,min(int(limit),80))
    query=f'[out:json][timeout:6];node["name"]["place"="farm"]({south},{west},{north},{east});out body {cap};'
    body=urlencode({'data':query}).encode('utf-8')
    errors=[]
    for endpoint in _OSM_ENDPOINTS:
        try:
            req=Request(endpoint,data=body,headers={'User-Agent':'Raio-X-Territorial/V43.8.1 (+public-name-resolution)','Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
            with urlopen(req,timeout=7) as response:data=json.load(response)
            items=[];seen=set()
            for element in data.get('elements') or []:
                if element.get('type')!='node':continue
                tags=element.get('tags') or {};name=_clean_name(tags.get('name'))
                lat=element.get('lat');lon=element.get('lon');node_id=element.get('id')
                if not name or lat is None or lon is None:continue
                key=(node_id or 0,name.casefold(),round(float(lat),7),round(float(lon),7))
                if key in seen:continue
                seen.add(key)
                items.append({'name':name,'lat':float(lat),'lon':float(lon),'osm_type':'node','osm_id':node_id,'place':tags.get('place'),'source':_OSM_SOURCE})
            return {'ok':True,'items':items,'count':len(items),'source':_OSM_SOURCE,'endpoint':endpoint}
        except Exception as exc:
            errors.append(f'{type(exc).__name__}:{str(exc)[:120]}')
    return {'ok':False,'items':[],'detail':' | '.join(errors[-2:]) or 'osm_unavailable','source':_OSM_SOURCE}


def _osm_identity_candidate(car_geom:dict[str,Any],bbox:list[float])->dict[str,Any]:
    if not car_geom or not bbox or len(bbox)!=4:return {'ok':False,'chosen':None,'items':[],'detail':'missing_geometry_or_bbox'}
    try:
        west,south,east,north=[float(x) for x in bbox];car=shape(car_geom)
        if car.is_empty:return {'ok':False,'chosen':None,'items':[],'detail':'empty_car_geometry'}
    except Exception as exc:return {'ok':False,'chosen':None,'items':[],'detail':f'geometry:{type(exc).__name__}:{exc}'}
    osm=_osm_named_farms_bbox(west,south,east,north,60)
    if not osm.get('ok'):return {'ok':False,'chosen':None,'items':[],'detail':osm.get('detail'),'source':_OSM_SOURCE}
    inside=[]
    for item in osm.get('items') or []:
        try:
            if not car.covers(Point(float(item['lon']),float(item['lat']))):continue
        except Exception:continue
        inside.append(item)
    by_name:dict[str,list[dict[str,Any]]]={}
    for item in inside:by_name.setdefault(str(item['name']).casefold(),[]).append(item)
    if len(by_name)==1:
        group=next(iter(by_name.values()));first=group[0];chosen={**first,'evidence_count':len(group)}
        return {'ok':True,'chosen':chosen,'items':inside,'conflict':False,'source':_OSM_SOURCE}
    return {'ok':True,'chosen':None,'items':inside,'conflict':len(by_name)>1,'names':sorted({x['name'] for x in inside},key=str.casefold),'source':_OSM_SOURCE}


def _seed_identity(code:str,items:list[dict[str,Any]])->dict[str,Any]|None:
    conflict=seed.conflict_by_car(code)
    if conflict:
        return {
            'ok':True,'car_code':code,'name':None,'source':seed.SOURCE,
            'confidence':'unresolved','method':'audited_osm_conflict','candidates':items[:5],
            'candidate_count':len(items),'osm_candidates_inside_car':len(conflict.get('names') or []),'osm_conflict':True,
            'conflicting_public_names':conflict.get('names') or [],
            'note':'Mais de uma denominação geográfica pública foi validada dentro do CAR; nenhuma é promovida sem evidência adicional.'
        }
    item=seed.by_car(code)
    if not item:return None
    return {
        'ok':True,'car_code':code,'name':item['name'],'source':seed.SOURCE,
        'confidence':'medium','method':'audited_osm_point_inside_car',
        'osm_node_id':item.get('osm_id'),'osm_lat':item.get('lat'),'osm_lon':item.get('lon'),
        'evidence_count':1,'candidates':items[:5],'candidate_count':len(items),'osm_candidates_inside_car':1,
        'note':'Denominação geográfica pública previamente auditada por ponto nomeado contido no polígono exato do CAR; não implica titularidade.'
    }


def resolve_property_identity_sync(car_code:str)->dict[str,Any]:
    code=str(car_code or '').strip().upper()
    if not CAR_RE.match(code):return {'ok':False,'car_code':code,'detail':'invalid_car_format'}
    now=time.monotonic();cached=_CACHE.get(code)
    if cached and now-cached[0]<TTL_SECONDS:return dict(cached[1])
    car=fetch_car_live_resilient(code)
    if not car.get('ok'):
        out={'ok':False,'car_code':code,'detail':car.get('detail') or 'CAR não localizado','source':'SICAR'};_CACHE[code]=(now,out);return out
    props=car.get('properties') or {};direct=_first_name(props)
    if direct:
        out={'ok':True,'car_code':code,'name':direct,'source':'SICAR','confidence':'high','method':'explicit_sicar_field','candidates':[],'candidate_count':0};_CACHE[code]=(now,out);return out
    sig=_sigef_candidates(car.get('geometry'),car.get('bbox') or []);items=sig.get('items') or [];chosen=None
    if items:
        top=items[0];second=items[1] if len(items)>1 else None
        strong=top.get('overlap_ratio',0)>=0.72 or (top.get('centroid_inside') and top.get('overlap_ratio',0)>=0.58)
        unambiguous=(second is None) or (top.get('score',0)-second.get('score',0)>=0.12) or (top.get('name')==second.get('name'))
        if strong and unambiguous:chosen=top
    if chosen:
        out={'ok':True,'car_code':code,'name':chosen['name'],'source':chosen['source'],'confidence':'medium_high','method':'strong_sigef_overlap','overlap_ratio':chosen['overlap_ratio'],'registry':chosen.get('registry'),'parcel_code':chosen.get('parcel_code'),'candidates':items[:5],'candidate_count':len(items)}
    else:
        # Deterministic audited cache is preferred over a live Overpass dependency.
        out=_seed_identity(code,items)
        if out is None:
            osm=_osm_identity_candidate(car.get('geometry'),car.get('bbox') or []);osm_chosen=osm.get('chosen')
            if osm_chosen:
                out={'ok':True,'car_code':code,'name':osm_chosen['name'],'source':_OSM_SOURCE,'confidence':'medium','method':'osm_named_farm_point_inside_car','osm_node_id':osm_chosen.get('osm_id'),'osm_lat':osm_chosen.get('lat'),'osm_lon':osm_chosen.get('lon'),'evidence_count':osm_chosen.get('evidence_count',1),'candidates':items[:5],'candidate_count':len(items),'osm_candidates_inside_car':len(osm.get('items') or []),'note':'Denominação geográfica pública associada por ponto nomeado contido no polígono exato do CAR; não implica titularidade.'}
            else:
                out={'ok':True,'car_code':code,'name':None,'source':'SICAR + SIGEF + OpenStreetMap','confidence':'unresolved','method':'no_safe_public_name','candidates':items[:5],'candidate_count':len(items),'osm_candidates_inside_car':len(osm.get('items') or []),'osm_conflict':bool(osm.get('conflict')),'note':'Nenhum nome público pôde ser atribuído com segurança. O sistema não inventa a denominação da fazenda.'}
    _CACHE[code]=(now,out)
    if len(_CACHE)>500:
        for k,_ in sorted(_CACHE.items(),key=lambda kv:kv[1][0])[:100]:_CACHE.pop(k,None)
    return out


@app.get('/v1/live/property-identity/{car_code}')
async def property_identity(car_code:str):
    out=await asyncio.to_thread(resolve_property_identity_sync,car_code)
    if not out.get('ok'):raise HTTPException(status_code=404 if out.get('detail')!='invalid_car_format' else 422,detail=out)
    return out


print('RX_PROPERTY_IDENTITY=public_name_resolver_sicar_sigef_audited_seed_osm_live',flush=True)

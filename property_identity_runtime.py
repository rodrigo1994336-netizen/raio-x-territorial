from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen

from fastapi import HTTPException, Request
from shapely.geometry import Point, shape
try:
    from shapely import make_valid as _make_valid
except ImportError:  # shapely < 2
    from shapely.validation import make_valid as _make_valid

import portal_v8
import public_property_name_seed_v43 as seed
from car_resilient import fetch_car_live_resilient, CAR_RE
from deploy_app import SIGEF_MIRROR, _curl
from external_process_lifecycle import ManagedOperationTimeout, RequestDisconnected, install_shutdown_cleanup, run_sync_with_request_lifecycle

app=portal_v8.app
install_shutdown_cleanup(app)
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
_SIGEF_RECORD_CAP=80
# C2b: minimum share of the CAR a SIGEF parcel must cover to be shown as its cadastral reference.
SIGEF_REFERENCE_MIN_OVERLAP=0.50
# C2b: an attempt that did not answer (SICAR or SIGEF) is remembered only briefly and never as an
# answer, so a burst of clicks shares one attempt. The explicit client retry forgets it first.
NEGATIVE_TTL_SECONDS=60
_INFLIGHT:dict[str,threading.Event]={}
_INFLIGHT_LOCK=threading.Lock()


def _unanswered_key(code:str)->str:
    return f'{code}|unanswered'


def forget_unanswered(car_code:str)->None:
    _CACHE.pop(_unanswered_key(str(car_code or '').strip().upper()),None)


def _share(value:float)->float:
    """A 0..1 share kept at 6 decimals and floored, so a sub-100% share never reads as 100%.

    Integer arithmetic: a float product such as 0.5005*1e6 (=500499.99999999994) must not lose a millionth.
    """
    v=float(value)
    if not math.isfinite(v):v=0.0
    v=min(max(v,0.0),1.0)
    return (int(round(v*1_000_000_000))//1000)/1_000_000


def sigef_reference_rank(item:dict[str,Any])->tuple[float,float]:
    """How well a SIGEF parcel and the CAR coincide: min(share of the CAR, share of the parcel), then share of the CAR.

    A settlement enclosing a single lot covers 100% of the lot's CAR but a tiny share of itself; the lot's own
    parcel, covering both almost entirely, is the more specific reference.
    """
    try:car=float(item.get('overlap_ratio') or 0)
    except Exception:car=0.0
    try:parcel=float(item.get('parcel_overlap_ratio')) if item.get('parcel_overlap_ratio') is not None else car
    except Exception:parcel=car
    return (min(car,parcel),car)


def _valid(geom):
    return geom if geom.is_valid else _make_valid(geom)


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


def _sigef_candidates(car_geom:dict[str,Any],bbox:list[float],cancel_event=None):
    env=','.join(str(float(x)) for x in bbox)
    params={
        'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope',
        'inSR':'4326','spatialRel':'esriSpatialRelIntersects',
        'outFields':'parcela_co,codigo_imo,nome_area,registro_m,registro_d,municipio_,uf_id,status,situacao_i',
        'returnGeometry':'true','outSR':'4326','resultRecordCount':str(_SIGEF_RECORD_CAP),
        # Largest parcels first: a capped page can then be proven complete for the 50% question.
        'orderByFields':'Shape__Area DESC'
    }
    params['outFields']+=',Shape__Area'
    raw=_curl(SIGEF_MIRROR+'?'+urlencode(params),True,cancel_event=cancel_event,connect_timeout=12,max_time=40,hard_timeout=45)
    if not raw.get('ok'):
        return {'ok':False,'detail':raw.get('detail') or raw.get('preview'),'items':[]}
    data=raw.get('json')
    # ArcGIS answers overload, a secured service or a rejected query as HTTP 200 + {"error":...}.
    # Trying is not answering: only a FeatureCollection with a features list is an answer.
    if not isinstance(data,dict) or 'error' in data or not isinstance(data.get('features'),list):
        err=data.get('error') if isinstance(data,dict) else None
        code=err.get('code') if isinstance(err,dict) else None
        return {'ok':False,'detail':f'arcgis_error:{code}' if err is not None else 'arcgis_malformed_answer','items':[]}
    features=data['features']
    # 200 is not all: ArcGIS flags a capped answer (top level and, in GeoJSON, under properties).
    truncated=bool(data.get('exceededTransferLimit') or (data.get('properties') or {}).get('exceededTransferLimit') or len(features)>=_SIGEF_RECORD_CAP)
    try:car=_valid(shape(car_geom));car_area=max(float(car.area),1e-12);car_centroid=car.centroid
    except Exception as exc:return {'ok':False,'detail':f'geometry:{type(exc).__name__}:{exc}','items':[]}
    items=[];failed=0;areas=[];ordered=True
    for f in features:
        props=(f.get('properties') if isinstance(f,dict) else None) or {}
        try:shape_area=float(props.get('Shape__Area'))
        except Exception:shape_area=None
        if shape_area is None or not math.isfinite(shape_area):ordered=False
        else:
            if areas and shape_area>areas[-1]*(1+1e-9):ordered=False
            areas.append(shape_area)
        name=_clean_name(props.get('nome_area'))
        if not name:continue
        try:
            # An invalid ring (self-intersection) is repaired before any area math: never a wrong share.
            g=_valid(shape(f.get('geometry')))
            if g.is_empty or not g.intersects(car):continue
            inter=car.intersection(g)
            overlap=float(inter.area/car_area) if not inter.is_empty else 0.0
            area_ratio=float(g.area/car_area) if car_area else math.inf
            parcel_overlap=float(inter.area/g.area) if g.area>0 and not inter.is_empty else 0.0
            centroid_inside=bool(g.contains(car_centroid) or g.touches(car_centroid))
        except Exception:
            failed+=1;continue
        score=overlap
        if centroid_inside:score+=0.08
        if 0.50<=area_ratio<=2.0:score+=0.06
        items.append({
            'name':name,'overlap_ratio':_share(overlap),'parcel_overlap_ratio':_share(parcel_overlap),'area_ratio':round(area_ratio,4),
            'centroid_inside':centroid_inside,'score':round(score,4),
            'parcel_code':props.get('parcela_co'),'property_code':props.get('codigo_imo'),
            'registry':props.get('registro_m') or props.get('registro_d'),
            'municipality':props.get('municipio_'),'uf':props.get('uf_id'),
            'source':'SIGEF/INCRA — espelho público IBAMA/PAMGIA',
            'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,
            'reference_kind':'SIGEF_CADASTRAL','map_anchor':'CADASTRAL_REFERENCE',
            'origin_label':'SIGEF/INCRA — referência cadastral ainda não vinculada ao CAR'
        })
    items.sort(key=lambda x:x['score'],reverse=True)
    relevant=truncated
    if truncated and ordered and areas and len(areas)==len(features):
        # Sorted by Shape__Area DESC, every parcel left out is no larger than the smallest one returned, and a
        # parcel cannot cover more of the CAR than its own area. Below 0.9x the threshold (margin for the
        # SIRGAS/WGS84 degree areas) no parcel left out can reach it, so the cut does not change the answer.
        relevant=not (min(areas)<0.9*SIGEF_REFERENCE_MIN_OVERLAP*car_area)
    # A parcel whose geometry could not be measured makes the answer partial, never a complete "none".
    return {'ok':True,'items':items,'count':len(items),'truncated':truncated,'truncated_relevant':relevant,'partial':failed>0,'failed':failed}


def _sigef_evidence(sig:dict[str,Any],items:list[dict[str,Any]])->dict[str,Any]:
    """C2b: whether SIGEF answered, and every parcel covering at least half of the CAR (by overlap)."""
    answered=sig.get('ok') is True
    strong=[x for x in items if float(x.get('overlap_ratio') or 0)>=SIGEF_REFERENCE_MIN_OVERLAP] if answered else []
    strong.sort(key=sigef_reference_rank,reverse=True)
    incomplete=bool(sig.get('truncated_relevant',sig.get('truncated')) or sig.get('partial')) if answered else False
    return {
        'sigef_state':'answered' if answered else 'unavailable',
        'sigef_truncated':bool(sig.get('truncated')) if answered else False,
        'sigef_incomplete':incomplete,
        'sigef_reference_candidates':strong[:10],'sigef_reference_candidate_count':len(strong),
    }


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
            req=URLRequest(endpoint,data=body,headers={'User-Agent':'Raio-X-Territorial/V44 (+public-name-resolution)','Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
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
                items.append({'name':name,'lat':float(lat),'lon':float(lon),'osm_type':'node','osm_id':node_id,'place':tags.get('place'),'source':_OSM_SOURCE,'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,'reference_kind':'OSM_LIVE','map_anchor':'GEOGRAPHIC_POINT','origin_label':'OpenStreetMap ao vivo — referência geográfica não confirmada para o CAR'})
            return {'ok':True,'items':items,'count':len(items),'source':_OSM_SOURCE,'endpoint':endpoint}
        except Exception as exc:
            errors.append(f'{type(exc).__name__}:{str(exc)[:120]}')
    return {'ok':False,'items':[],'detail':' | '.join(errors[-2:]) or 'osm_unavailable','source':_OSM_SOURCE}


def _osm_identity_candidate(car_geom:dict[str,Any],bbox:list[float])->dict[str,Any]:
    """Diagnostic only. A live OSM point inside CAR is not a validated CAR denomination."""
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
    return {'ok':True,'chosen':None,'items':inside,'conflict':len(by_name)>1,'names':sorted({x['name'] for x in inside},key=str.casefold),'source':_OSM_SOURCE,'note':'Referências OSM ao vivo são contexto geográfico e nunca são promovidas automaticamente a denominação do CAR.'}


def _seed_identity(code:str,items:list[dict[str,Any]])->dict[str,Any]|None:
    conflict=seed.conflict_by_car(code)
    if conflict:
        return {
            'ok':False,'car_code':code,'name':None,'source':seed.SOURCE,
            'detail':'property_name_ambiguous','confidence':'unresolved','method':'audited_osm_conflict','candidates':items[:5],
            'candidate_count':len(items),'osm_candidates_inside_car':len(conflict.get('names') or []),'osm_conflict':True,
            'conflicting_public_names':conflict.get('names') or [],
            'display_kind':'UNRESOLVED','validation_status':'AMBIGUOUS','panel_name_eligible':False,
            'geographic_reference_names':conflict.get('names') or [],
            'note':'Mais de uma referência geográfica pública foi encontrada no CAR; nenhuma valida sua denominação.'
        }
    item=seed.by_car(code)
    if not item:return None
    return {
        'ok':False,'car_code':code,'name':None,'source':seed.SOURCE,
        'detail':'property_name_unvalidated_reference','confidence':'medium','method':'audited_osm_point_inside_exact_car',
        'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,
        'reference_kind':'OSM_AUDITED','map_anchor':'GEOGRAPHIC_POINT',
        'geographic_reference_names':[item['name']],
        'origin_label':'OpenStreetMap auditado — referência geográfica não confirmada para o CAR',
        'osm_node_id':item.get('osm_id'),'osm_lat':item.get('lat'),'osm_lon':item.get('lon'),
        'evidence_count':1,'candidates':items[:5],'candidate_count':len(items),'osm_candidates_inside_car':1,
        'note':'Ponto OSM dentro do CAR, mesmo auditado, não valida a denominação pelo protocolo congelado da Etapa 2.'
    }


def _cancelled(code:str,source:str|None=None)->dict[str,Any]:
    out={'ok':False,'car_code':code,'cancelled':True,'detail':'request_cancelled','sigef_state':'cancelled'}
    if source:out['source']=source
    return out


def _cached_identity(code:str,has_car:bool=False)->dict[str,Any]|None:
    now=time.monotonic();hit=_CACHE.get(code)
    if hit and now-hit[0]<TTL_SECONDS:return dict(hit[1])
    miss=_CACHE.get(_unanswered_key(code))
    # A remembered SICAR failure never outlives a caller that holds a fresh SICAR answer.
    if miss and now-miss[0]<NEGATIVE_TTL_SECONDS and not (has_car and miss[1].get('sicar_failed')):return dict(miss[1])
    return None


def _store(key:str,stamp:float,out:dict[str,Any])->None:
    _CACHE[key]=(stamp,out)
    if len(_CACHE)>500:
        for k,_ in sorted(_CACHE.items(),key=lambda kv:kv[1][0])[:100]:_CACHE.pop(k,None)


def resolve_property_identity_sync(car_code:str, *, cancel_event=None, car:dict[str,Any]|None=None)->dict[str,Any]:
    """car: the SICAR answer the caller already holds (map panel), so SICAR is not asked twice."""
    code=str(car_code or '').strip().upper()
    if cancel_event and cancel_event.is_set():return _cancelled(code)
    if not CAR_RE.match(code):return {'ok':False,'car_code':code,'detail':'invalid_car_format'}
    has_car=isinstance(car,dict) and bool(car.get('ok'))
    while True:
        hit=_cached_identity(code,has_car)
        if hit is not None:return hit
        with _INFLIGHT_LOCK:
            hit=_cached_identity(code,has_car)
            if hit is not None:return hit
            gate=_INFLIGHT.get(code)
            if gate is None:
                gate=_INFLIGHT[code]=threading.Event()
                break
        # Single flight: a concurrent caller for the same CAR waits for that attempt instead of repeating it.
        while not gate.wait(0.2):
            if cancel_event and cancel_event.is_set():return _cancelled(code)
    try:
        return _resolve_identity_uncached(code,cancel_event,car)
    finally:
        with _INFLIGHT_LOCK:
            if _INFLIGHT.get(code) is gate:_INFLIGHT.pop(code,None)
        gate.set()


def _resolve_identity_uncached(code:str,cancel_event,car:dict[str,Any]|None)->dict[str,Any]:
    now=time.monotonic()
    if not (isinstance(car,dict) and car.get('ok')):
        car=fetch_car_live_resilient(code,cancel_event=cancel_event)
    if not car.get('ok'):
        if car.get('cancelled'):return _cancelled(code,'SICAR')
        # Trying is not answering: a SICAR failure is remembered briefly, never for the hour of an answer.
        out={'ok':False,'car_code':code,'detail':car.get('detail') or 'CAR não localizado','source':'SICAR','sigef_state':'unavailable','sigef_truncated':False,'sicar_failed':True}
        _store(_unanswered_key(code),time.monotonic(),out);return out
    props=car.get('properties') or {};direct=_first_name(props)
    if direct:
        out={'ok':True,'car_code':code,'name':direct,'source':'SICAR','confidence':'high','method':'explicit_sicar_field','display_kind':'VALIDATED_PROPERTY_NAME','validation_status':'VALIDATED','panel_name_eligible':True,'map_anchor':'CAR_POLYGON','validation_scope':'DIRECT_CAR_FIELD','origin_label':'SICAR — denominação explícita do próprio cadastro CAR','candidates':[],'candidate_count':0,'sigef_state':'not_queried','sigef_truncated':False}
        _CACHE.pop(_unanswered_key(code),None);_store(code,now,out);return out

    sig=_sigef_candidates(car.get('geometry'),car.get('bbox') or [],cancel_event);items=sig.get('items') or []
    if cancel_event and cancel_event.is_set():return _cancelled(code)

    # A named SIGEF parcel intersecting a CAR is useful cadastral context, but
    # overlap alone does not prove that the SIGEF denomination belongs to that CAR.
    out=_seed_identity(code,items)
    if out is None:
        osm=_osm_identity_candidate(car.get('geometry'),car.get('bbox') or [])
        out={
            'ok':False,'car_code':code,'name':None,'source':'SICAR + SIGEF + OpenStreetMap',
            'detail':'property_name_unresolved','confidence':'unresolved','method':'no_validated_property_name',
            'display_kind':'UNRESOLVED','validation_status':'UNRESOLVED','panel_name_eligible':False,
            'candidates':items[:5],'candidate_count':len(items),
            'osm_candidates_inside_car':len(osm.get('items') or []),'osm_conflict':bool(osm.get('conflict')),
            'geographic_reference_names':osm.get('names') or [],
            'note':'Nenhuma denominação foi validada para este CAR. SIGEF não vinculado e OSM ao vivo permanecem referências cartográficas e não podem preencher o painel do imóvel.'
        }
    out.update(_sigef_evidence(sig,items))
    # A SIGEF query that did not answer is not an answer: never cached as one (only briefly remembered),
    # so the retry can succeed.
    if out['sigef_state']!='answered':
        _store(_unanswered_key(code),time.monotonic(),out);return out
    _CACHE.pop(_unanswered_key(code),None)
    _store(code,now,out)
    return out


@app.get('/v1/live/property-identity/{car_code}')
async def property_identity(car_code:str, request:Request):
    try:
        out=await run_sync_with_request_lifecycle(request,resolve_property_identity_sync,car_code,timeout_seconds=None)
    except RequestDisconnected:
        raise HTTPException(status_code=499,detail='client_disconnected')
    except ManagedOperationTimeout:
        raise HTTPException(status_code=504,detail='property_identity_timeout')
    if not out.get('ok') and not out.get('validation_status'):
        raise HTTPException(status_code=404 if out.get('detail')!='invalid_car_format' else 422, detail=out)
    return out


print('RX_PROPERTY_IDENTITY_V44=validated_car_names_only_references_never_promoted',flush=True)

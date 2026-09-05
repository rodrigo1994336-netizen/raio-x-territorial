from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from shapely.geometry import shape

import portal_v8
from deploy_app import SIGEF_MIRROR, _curl
from property_identity_runtime import _clean_name, _osm_named_farms_bbox

app = portal_v8.app
TTL_SECONDS = 900
_CACHE: dict[tuple[float, float, float, float, int], tuple[float, dict[str, Any]]] = {}


def _cache_key(west: float, south: float, east: float, north: float, limit: int):
    return (round(west, 3), round(south, 3), round(east, 3), round(north, 3), int(limit))


def _query_names_sync(west: float, south: float, east: float, north: float, limit: int = 60) -> dict[str, Any]:
    started = time.monotonic()
    cap = max(1, min(int(limit), 100))
    key = _cache_key(west, south, east, north, cap)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < TTL_SECONDS:
        out = dict(cached[1])
        out['coverage'] = dict(out.get('coverage') or {})
        out['cached'] = True
        return out
    env = ','.join(str(float(x)) for x in (west, south, east, north))
    params = {
        'f': 'geojson','where': '1=1','geometry': env,'geometryType': 'esriGeometryEnvelope','inSR': '4326',
        'spatialRel': 'esriSpatialRelIntersects','outFields': 'parcela_co,codigo_imo,nome_area,registro_m,registro_d,municipio_,uf_id,status,situacao_i',
        'returnGeometry': 'true','outSR': '4326','resultRecordCount': str(min(220, max(cap * 3, cap))),
    }
    raw = _curl(SIGEF_MIRROR + '?' + urlencode(params), True)
    sigef_available=bool(raw.get('ok'))
    data = (raw.get('json') or {}) if sigef_available else {}
    features = data.get('features') or []
    items=[]
    seen=set()
    valid_name_count=0
    named_geometry_count=0
    duplicate_count=0
    for feature in features:
        props=feature.get('properties') or {}
        name=_clean_name(props.get('nome_area'))
        geom=feature.get('geometry')
        if name:
            valid_name_count += 1
        if not name or not geom:
            continue
        try:
            g=shape(geom)
            if g.is_empty:
                continue
            c=g.representative_point()
            center={'lat':float(c.y),'lon':float(c.x)}
        except Exception:
            continue
        named_geometry_count += 1
        parcel=str(props.get('parcela_co') or '').strip()
        registry=props.get('registro_m') or props.get('registro_d')
        dedupe=parcel or f"{name.upper()}|{round(center['lat'],5)}|{round(center['lon'],5)}"
        if dedupe in seen:
            duplicate_count += 1
            continue
        seen.add(dedupe)
        items.append({
            'name':name,'municipality':props.get('municipio_'),'uf':props.get('uf_id'),
            'parcel_code':props.get('parcela_co'),'property_code':props.get('codigo_imo'),
            'registry':registry,'status':props.get('status') or props.get('situacao_i'),
            'center':center,'source':'SIGEF/INCRA — espelho público',
        })
        if len(items)>=cap:
            break

    # V43.8: SIGEF has no spatial coverage in parts of Brazil (including the
    # Curvelo sample). Fill only the remaining label capacity with explicit,
    # named OSM place=farm points. These are geographic labels, never owner data.
    osm=_osm_named_farms_bbox(west,south,east,north,max(1,cap-len(items))) if len(items)<cap else {'ok':True,'items':[],'count':0}
    osm_added=0
    if osm.get('ok'):
        for farm in osm.get('items') or []:
            name=_clean_name(farm.get('name'))
            if not name:continue
            center={'lat':float(farm['lat']),'lon':float(farm['lon'])}
            dedupe=f"OSM|{farm.get('osm_id')}|{name.casefold()}"
            if dedupe in seen:continue
            seen.add(dedupe)
            items.append({
                'name':name,'municipality':None,'uf':None,'parcel_code':None,'property_code':None,
                'registry':None,'status':None,'center':center,
                'osm_node_id':farm.get('osm_id'),
                'source':'OpenStreetMap contributors — denominação geográfica pública (ODbL)',
            })
            osm_added += 1
            if len(items)>=cap:break

    items.sort(key=lambda x:(str(x.get('name') or '').upper(),str(x.get('municipality') or '').upper()))
    coverage={
        'sigef_candidates': len(features) if sigef_available else None,
        'sigef_with_valid_name': valid_name_count if sigef_available else None,
        'sigef_named_with_valid_geometry': named_geometry_count if sigef_available else None,
        'osm_candidates': int(osm.get('count') or 0) if osm.get('ok') else None,
        'osm_names_returned': osm_added,
        'deduplicated': duplicate_count,
        'names_returned': len(items),
        'limit': cap,
        'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
        'source_available': bool(sigef_available or osm.get('ok')),
    }
    if not sigef_available and not osm.get('ok'):
        return {
            'ok':False,'items':[],'count':0,'source':'SIGEF + OpenStreetMap',
            'detail':raw.get('detail') or raw.get('preview') or osm.get('detail') or 'fontes_indisponiveis',
            'coverage':coverage,
        }
    out={
        'ok':True,'items':items,'count':len(items),'candidate_count':len(features),
        'truncated':(len(features)>len(items) and len(items)>=cap) or (len(items)>=cap and int(osm.get('count') or 0)>osm_added),
        'source':'SIGEF/INCRA + OpenStreetMap — fontes públicas','cached':False,
        'note':'Denominações públicas disponíveis no mapa. Nomes OSM são rótulos geográficos; a associação a um CAR só é promovida após validação ponto-dentro-do-polígono e ausência de conflito.',
        'coverage':coverage,
    }
    _CACHE[key]=(now,out)
    if len(_CACHE)>300:
        for k,_ in sorted(_CACHE.items(),key=lambda kv:kv[1][0])[:60]:
            _CACHE.pop(k,None)
    return out


@app.get('/v1/live/property-names/viewport')
async def property_names_viewport(
    west:float,
    south:float,
    east:float,
    north:float,
    limit:int=60,
    car_visible:int|None=None,
    diagnostic:bool=False,
):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail='Área do mapa inválida.')
    if max(east-west,north-south)>1.50:
        raise HTTPException(status_code=422, detail='Aproxime o mapa para visualizar os nomes das fazendas.')
    out = await asyncio.to_thread(_query_names_sync,west,south,east,north,limit)
    out = dict(out)
    coverage = dict(out.get('coverage') or {})
    if car_visible is not None:
        coverage['car_visible'] = max(0, min(int(car_visible), 5000))
    else:
        coverage['car_visible'] = None
    out['coverage'] = coverage
    if diagnostic:
        print('RX_PROPERTY_NAMES_COVERAGE=' + json.dumps(coverage, ensure_ascii=False, separators=(',', ':')), flush=True)
    return out


print('RX_PROPERTY_NAMES_V43=osm_fallback_coverage_funnel_no_owner_inference', flush=True)

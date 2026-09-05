from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from shapely.geometry import shape

import portal_v8
from deploy_app import SIGEF_MIRROR, _curl
from property_identity_runtime import _clean_name

app = portal_v8.app
TTL_SECONDS = 600
_CACHE: dict[tuple[float, float, float, float, int], tuple[float, dict[str, Any]]] = {}


def _cache_key(west: float, south: float, east: float, north: float, limit: int):
    return (round(west, 3), round(south, 3), round(east, 3), round(north, 3), int(limit))


def _query_names_sync(west: float, south: float, east: float, north: float, limit: int = 60) -> dict[str, Any]:
    cap = max(1, min(int(limit), 100))
    key = _cache_key(west, south, east, north, cap)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < TTL_SECONDS:
        out = dict(cached[1])
        out['cached'] = True
        return out

    env = ','.join(str(float(x)) for x in (west, south, east, north))
    params = {
        'f': 'geojson',
        'where': '1=1',
        'geometry': env,
        'geometryType': 'esriGeometryEnvelope',
        'inSR': '4326',
        'spatialRel': 'esriSpatialRelIntersects',
        'outFields': 'parcela_co,codigo_imo,nome_area,registro_m,registro_d,municipio_,uf_id,status,situacao_i',
        'returnGeometry': 'true',
        'outSR': '4326',
        'resultRecordCount': str(min(180, max(cap * 2, cap))),
    }
    raw = _curl(SIGEF_MIRROR + '?' + urlencode(params), True)
    if not raw.get('ok'):
        return {
            'ok': False,
            'items': [],
            'count': 0,
            'source': 'SIGEF/INCRA — espelho público IBAMA/PAMGIA',
            'detail': raw.get('detail') or raw.get('preview') or 'fonte_indisponivel',
        }

    data = raw.get('json') or {}
    features = data.get('features') or []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feature in features:
        props = feature.get('properties') or {}
        name = _clean_name(props.get('nome_area'))
        geom = feature.get('geometry')
        if not name or not geom:
            continue
        try:
            g = shape(geom)
            if g.is_empty:
                continue
            c = g.representative_point()
            center = {'lat': float(c.y), 'lon': float(c.x)}
        except Exception:
            continue
        parcel = str(props.get('parcela_co') or '').strip()
        registry = props.get('registro_m') or props.get('registro_d')
        dedupe = parcel or f"{name.upper()}|{round(center['lat'], 5)}|{round(center['lon'], 5)}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        items.append({
            'name': name,
            'municipality': props.get('municipio_'),
            'uf': props.get('uf_id'),
            'parcel_code': props.get('parcela_co'),
            'property_code': props.get('codigo_imo'),
            'registry': registry,
            'status': props.get('status') or props.get('situacao_i'),
            'center': center,
            'source': 'SIGEF/INCRA — espelho público',
        })
        if len(items) >= cap:
            break

    items.sort(key=lambda x: (str(x.get('name') or '').upper(), str(x.get('municipality') or '').upper()))
    out = {
        'ok': True,
        'items': items,
        'count': len(items),
        'candidate_count': len(features),
        'truncated': len(features) > len(items) and len(items) >= cap,
        'source': 'SIGEF/INCRA — espelho público IBAMA/PAMGIA',
        'cached': False,
        'note': 'Os nomes exibidos são denominações públicas de áreas certificadas. O sistema não inventa nomes nem presume titularidade.',
    }
    _CACHE[key] = (now, out)
    if len(_CACHE) > 250:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:50]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    return out


@app.get('/v1/live/property-names/viewport')
async def property_names_viewport(
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int = 60,
):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail='Área do mapa inválida.')
    if max(east - west, north - south) > 0.85:
        raise HTTPException(status_code=422, detail='Aproxime o mapa para visualizar os nomes das fazendas.')
    return await asyncio.to_thread(_query_names_sync, west, south, east, north, limit)


print('RX_PROPERTY_NAMES_V30=viewport_sigef_public_names_cached', flush=True)

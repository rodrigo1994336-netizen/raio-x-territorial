from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import httpx
from shapely.geometry import shape

IBAMA_AUTOS = 'https://pamgia.ibama.gov.br/server/rest/services/app_dadosabertos/adm_auto_infracao_p/FeatureServer/0/query'


def _pick(props: dict[str, Any], *needles: str):
    lowered = [(str(k).lower(), k) for k in props]
    for needle in needles:
        n = needle.lower()
        for lk, original in lowered:
            if lk == n:
                return props.get(original)
    for needle in needles:
        n = needle.lower()
        for lk, original in lowered:
            if n in lk:
                return props.get(original)
    return None


def _clean_text(v: Any, max_len=240):
    if v is None:
        return None
    s = str(v).strip()
    return s[:max_len] if s else None


def _date_text(v: Any):
    if v is None:
        return None
    try:
        n = float(v)
        if n > 10_000_000_000:
            return datetime.fromtimestamp(n/1000.0, tz=timezone.utc).date().isoformat()
        if n > 1_000_000_000:
            return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat()
    except Exception:
        pass
    return _clean_text(v, 40)


def _money(v: Any):
    if v is None:
        return None
    try:
        return round(float(str(v).replace('.', '').replace(',', '.')) if isinstance(v, str) and ',' in v else float(v), 2)
    except Exception:
        return None


def _public_auto(props: dict[str, Any]):
    number = _pick(props, 'num_auto_infracao','numero_auto_infracao','num_auto','numero_auto','auto_infracao','nu_auto_infracao')
    process = _pick(props, 'num_processo','numero_processo','processo','nu_processo')
    date = _pick(props, 'dat_auto_infracao','data_auto_infracao','dt_auto_infracao','data_auto','dat_lavratura','data_lavratura')
    value = _pick(props, 'valor_multa','vlr_multa','valor_auto','valor_infracao','multa')
    description = _pick(props, 'des_infracao','descricao_infracao','descricao','infracao')
    status = _pick(props, 'situacao','status','situacao_auto','status_auto')
    return {
        'auto_number': _clean_text(number, 80),
        'process': _clean_text(process, 80),
        'date': _date_text(date),
        'fine_value': _money(value),
        'description': _clean_text(description, 260),
        'status': _clean_text(status, 120),
    }


def _dedupe_key(item: dict[str, Any], feature: dict[str, Any]):
    if item.get('auto_number'):
        return ('auto', item['auto_number'])
    p = feature.get('properties') or {}
    oid = _pick(p, 'objectid','object_id','fid','id')
    if oid is not None:
        return ('oid', str(oid))
    coords = (feature.get('geometry') or {}).get('coordinates') or []
    return ('fallback', item.get('date'), item.get('fine_value'), tuple(coords[:2]) if isinstance(coords, list) else str(coords))


async def query_ibama_autos(car_geometry: dict[str, Any], bbox: list[float]):
    env = ','.join(str(x) for x in bbox)
    params = {
        'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4674',
        'spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','outSR':'4674',
        'resultRecordCount':'2000'
    }
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={'User-Agent':'Raio-X-Territorial/0.14.8'}) as client:
            r = await client.get(IBAMA_AUTOS, params=params)
        data = r.json()
        features = data.get('features') or []
        car = shape(car_geometry)
        kept = []
        seen = set()
        for f in features:
            try:
                g = shape(f.get('geometry'))
                if not car.intersects(g):
                    continue
            except Exception:
                continue
            item = _public_auto(f.get('properties') or {})
            key = _dedupe_key(item, f)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        total = round(sum(x.get('fine_value') or 0 for x in kept), 2)
        return {
            'ok': r.status_code == 200 and 'error' not in data,
            'status': r.status_code,
            'feature_count_bbox': len(features),
            'occurrence_count': len(kept),
            'fine_total': total,
            'autos': kept,
            'source': 'IBAMA/PAMGIA - autos de infração ambiental',
            'deduplicated': True,
        }
    except Exception as e:
        return {'ok':False,'error':type(e).__name__,'detail':str(e)[:300],'source':'IBAMA/PAMGIA - autos de infração ambiental'}

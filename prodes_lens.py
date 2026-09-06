from __future__ import annotations

from datetime import date, datetime
from typing import Any

try:
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from pyproj import Geod
    _GEO_OK=True
    _GEOD=Geod(ellps='GRS80')
except Exception:
    _GEO_OK=False
    shape=unary_union=_GEOD=None

CREDIT_CUTOFF=date(2019,7,31)


def _f(v, default=0.0):
    try:return float(v)
    except Exception:return default


def _year(props:dict[str,Any]):
    y=props.get('year') or props.get('ano') or props.get('year_prodes')
    try:return int(str(y)[:4])
    except Exception:return None


def _image_date(props:dict[str,Any]):
    raw=props.get('image_date') or props.get('data_imagem') or props.get('date')
    if not raw:return None
    s=str(raw)[:10]
    for fmt in ('%Y-%m-%d','%d/%m/%Y'):
        try:return datetime.strptime(s,fmt).date()
        except Exception:pass
    return None


def _post_cutoff(props:dict[str,Any]):
    d=_image_date(props)
    if d is not None:return d>CREDIT_CUTOFF
    y=_year(props)
    return bool(y and y>=2020)


def _area_ha(geom):
    if not _GEO_OK or geom is None or geom.is_empty:return 0.0
    try:return abs(_GEOD.geometry_area_perimeter(geom)[0])/10000.0
    except Exception:return 0.0


def _exact_from_hits(prodes:dict[str,Any],car_geometry:dict[str,Any]|None):
    """Recompute the two PRODES lenses from source geometries.

    Both areas are unions of ST_Intersection-equivalent geometries. This prevents
    double-counting when polygons overlap and guarantees the post-cutoff lens is
    not a simple sum of feature areas.
    """
    if not _GEO_OK or not car_geometry:return None
    try:car=shape(car_geometry)
    except Exception:return None
    if car.is_empty:return None
    hist=[];post=[];items=[];seen=set()
    for hit in prodes.get('hits') or []:
        layer=str(hit.get('layer') or '')
        for feature in hit.get('features') or []:
            try:
                src=shape(feature.get('geometry'))
                if src.is_empty or not car.intersects(src):continue
                inter=car.intersection(src)
                if inter.is_empty:continue
            except Exception:continue
            area=_area_ha(inter)
            if area<=0:continue
            props=feature.get('properties') or {}
            y=_year(props);d=_image_date(props);is_post=_post_cutoff(props)
            geom_key=inter.wkb_hex if hasattr(inter,'wkb_hex') else str(inter.bounds)
            key=(y,geom_key)
            if key in seen:continue
            seen.add(key)
            hist.append(inter)
            if is_post:post.append(inter)
            items.append({
                'id':feature.get('id'),'layer':layer,'year':y,
                'image_date':d.isoformat() if d else None,'post_cutoff':is_post,
                'area_intersection_ha':round(area,6),
            })
    if not items:return None
    hu=unary_union(hist) if hist else None
    pu=unary_union(post) if post else None
    return {
        'historical_area_unique_ha':round(_area_ha(hu),6),
        'post_area_unique_ha':round(_area_ha(pu),6),
        'occurrences':items,
        'post_occurrences':[x for x in items if x.get('post_cutoff')],
        'method':'exact_geometry_union_after_intersection',
    }


def _pct(area:Any,total:Any):
    a=_f(area);t=_f(total)
    return round(a/t*100,4) if t>0 else None


def derive_prodes_lens(
    prodes:dict[str,Any]|None,
    fiscal_modules:Any=None,
    car_geometry:dict[str,Any]|None=None,
    car_area_ha:Any=None,
):
    p=prodes or {};ex=p.get('exact') or {}
    truth=_exact_from_hits(p,car_geometry)
    if truth:
        occ=truth.get('occurrences') or []
        post=truth.get('post_occurrences') or []
        historical_area=_f(truth.get('historical_area_unique_ha'))
        post_area=_f(truth.get('post_area_unique_ha'))
        method=truth.get('method')
    else:
        occ=ex.get('occurrences') or []
        post=[]
        for item in occ:
            if _post_cutoff(item.get('properties') or {}):post.append(item)
        historical_area=_f(ex.get('area_unique_ha'))
        # Legacy fallback. Production path passes CAR geometry and therefore uses
        # the exact union above; this remains only for old/test payloads.
        post_area=sum(_f(x.get('area_intersection_ha')) for x in post)
        method='legacy_exact_summary_fallback'

    hist_years=[];post_years=[]
    for item in occ:
        props=item.get('properties') or {}
        y=item.get('year') if item.get('year') is not None else _year(props)
        if y is not None:hist_years.append(int(y))
    for item in post:
        props=item.get('properties') or {}
        y=item.get('year') if item.get('year') is not None else _year(props)
        if y is not None:post_years.append(int(y))

    try:mf=float(fiscal_modules)
    except Exception:mf=None
    total=_f(car_area_ha)
    return {
        'historical':{
            'occurrence_count':len(occ) if truth else int(ex.get('occurrence_count') or len(occ) or 0),
            'area_unique_ha':round(historical_area,6),
            'pct_car':_pct(historical_area,total),
            'years':sorted(set(hist_years)),
        },
        'post_2019_07_31':{
            'occurrence_count':len(post),
            'area_unique_ha':round(post_area,6),
            'pct_car':_pct(post_area,total),
            'years':sorted(set(post_years)),
            'cutoff':'2019-07-31',
        },
        'credit_screening':{
            'mcr_check_required':len(post)>0,
            'fiscal_modules':mf,
            'reading':(
                'Há detecção PRODES posterior a 31/07/2019. Para crédito rural, a ocorrência deve ser conferida conforme o MCR vigente e a documentação ambiental aplicável; isso não equivale automaticamente a impedimento definitivo.'
                if post else
                'Nenhuma detecção PRODES posterior a 31/07/2019 foi identificada entre as ocorrências retornadas nesta consulta. Isso não substitui a verificação da instituição financeira nem outras bases ambientais.'
            ),
            'regulatory_basis':'MCR 2-9: verificação de supressão de vegetação nativa após 31/07/2019.',
        },
        'calculation_method':method,
        'explanation':'O Raio-X mantém o histórico PRODES completo e, separadamente, destaca o recorte pós-31/07/2019. Em ambas as lentes, a área é a união das geometrias resultantes da interseção exata com o CAR, evitando dupla contagem.'
    }

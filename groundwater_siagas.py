from __future__ import annotations

import math
import statistics
from typing import Any

import httpx
from shapely.geometry import shape

SIAGAS_LAYER='https://geoportal.sgb.gov.br/server/rest/services/Hosted/siagas_web/FeatureServer/1/query'
HYDRO_ATLAS='https://geoportal.sgb.gov.br/server/rest/services/hidrologia/Mapa_Hidrogeologico_Atlas_Brasil/MapServer'
FIELDS='objectid,idt_ponto,str_municipio,str_uf,str_local_ponto,str_nome_ponto,str_natureza_ponto,str_tipo_situacao,str_uso_agua,str_aquifero,num_profundidade,num_ph,num_condutividade_eletrica,num_temperatura,nd,ne,num_vazao_especifica,num_latitude_decimal,num_longitude_decimal,data_perfuracao,data_cadastro,status_rimas'


def _f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:return None


def _positive(v):
    x=_f(v); return x if x is not None and x>0 else None


def _median(vals):
    xs=[float(x) for x in vals if x is not None]
    return round(statistics.median(xs),2) if xs else None


def _mean(vals):
    xs=[float(x) for x in vals if x is not None]
    return round(sum(xs)/len(xs),2) if xs else None


def _haversine_km(lat1,lon1,lat2,lon2):
    r=6371.0088
    p1,p2=math.radians(lat1),math.radians(lat2)
    dphi=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return r*2*math.atan2(math.sqrt(a),math.sqrt(max(0,1-a)))


def _centroid(car_geometry:dict[str,Any]):
    c=shape(car_geometry).centroid
    return float(c.x),float(c.y)


def _dominant(values,limit=5):
    counts={}
    for v in values:
        s=str(v or '').strip()
        if not s: continue
        counts[s]=counts.get(s,0)+1
    return [{'name':k,'count':v} for k,v in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))[:limit]]


async def _query_radius(lon:float,lat:float,radius_km:float,limit:int=500):
    params={
      'f':'geojson','where':'1=1','geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':'4326',
      'spatialRel':'esriSpatialRelIntersects','distance':str(radius_km*1000),'units':'esriSRUnit_Meter',
      'outFields':FIELDS,'returnGeometry':'true','outSR':'4326','resultRecordCount':str(max(20,min(limit,1000)))
    }
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.23-groundwater'}) as c:
            r=await c.get(SIAGAS_LAYER,params=params);r.raise_for_status();data=r.json()
        if data.get('error'): return {'ok':False,'detail':str(data['error'])[:400]}
        return {'ok':True,'features':data.get('features') or []}
    except Exception as e:
        return {'ok':False,'detail':f'{type(e).__name__}:{str(e)[:260]}'}


async def query_groundwater(car_geometry:dict[str,Any],initial_radius_km:float=20.0):
    try:lon,lat=_centroid(car_geometry)
    except Exception as e:return {'ok':False,'source':'SGB/SIAGAS','detail':f'centroid:{e}'}
    radius=float(initial_radius_km);res=await _query_radius(lon,lat,radius)
    if res.get('ok') and len(res.get('features') or [])<4 and radius<50:
        radius=50.0;res=await _query_radius(lon,lat,radius)
    if not res.get('ok'):
        return {'ok':False,'source':'SGB/SIAGAS — Poços','detail':res.get('detail'),'latitude':lat,'longitude':lon,'hydrogeology_map_url':HYDRO_ATLAS}
    rows=[]
    for f in res.get('features') or []:
        p=f.get('properties') or {};g=f.get('geometry') or {};coords=g.get('coordinates') if isinstance(g,dict) else None
        wlon=_f(p.get('num_longitude_decimal'));wlat=_f(p.get('num_latitude_decimal'))
        if (wlon is None or wlat is None) and isinstance(coords,(list,tuple)) and len(coords)>=2:
            wlon,wlat=_f(coords[0]),_f(coords[1])
        dist=_haversine_km(lat,lon,wlat,wlon) if wlat is not None and wlon is not None else None
        rows.append({
          'id':p.get('idt_ponto') or p.get('objectid'),'distance_km':round(dist,2) if dist is not None else None,
          'municipality':p.get('str_municipio'),'uf':p.get('str_uf'),'local':p.get('str_local_ponto'),'name':p.get('str_nome_ponto'),
          'nature':p.get('str_natureza_ponto'),'situation':p.get('str_tipo_situacao'),'water_use':p.get('str_uso_agua'),'aquifer':p.get('str_aquifero'),
          'well_depth_m':_positive(p.get('num_profundidade')),'static_level_m':_positive(p.get('ne')),'dynamic_level_m':_positive(p.get('nd')),
          'specific_yield':_positive(p.get('num_vazao_especifica')),'ph':_positive(p.get('num_ph')),'conductivity':_positive(p.get('num_condutividade_eletrica')),
          'latitude':wlat,'longitude':wlon
        })
    rows=sorted(rows,key=lambda x:(999999 if x.get('distance_km') is None else x['distance_km']))
    depths=[x['well_depth_m'] for x in rows if x.get('well_depth_m') is not None]
    static=[x['static_level_m'] for x in rows if x.get('static_level_m') is not None]
    dynamic=[x['dynamic_level_m'] for x in rows if x.get('dynamic_level_m') is not None]
    yields=[x['specific_yield'] for x in rows if x.get('specific_yield') is not None]
    phs=[x['ph'] for x in rows if x.get('ph') is not None]
    n=len(rows);quality='alta' if n>=15 and len(depths)>=8 and len(static)>=5 else ('moderada' if n>=5 and len(depths)>=3 else 'baixa')
    evidence='forte' if n>=15 else ('moderada' if n>=5 else ('limitada' if n else 'sem poços próximos cadastrados'))
    return {
      'ok':True,'source':'Serviço Geológico do Brasil — SIAGAS / FeatureServer','latitude':round(lat,6),'longitude':round(lon,6),'search_radius_km':radius,
      'well_count':n,'groundwater_evidence':evidence,'confidence':quality,
      'well_depth_median_m':_median(depths),'well_depth_mean_m':_mean(depths),'well_depth_sample_n':len(depths),
      'static_water_level_median_m':_median(static),'static_water_level_sample_n':len(static),
      'dynamic_water_level_median_m':_median(dynamic),'dynamic_water_level_sample_n':len(dynamic),
      'specific_yield_median':_median(yields),'specific_yield_sample_n':len(yields),'ph_median':_median(phs),'ph_sample_n':len(phs),
      'dominant_aquifers':_dominant([x.get('aquifer') for x in rows]),'dominant_uses':_dominant([x.get('water_use') for x in rows]),
      'nearest_wells':rows[:20],'hydrogeology_map_url':HYDRO_ATLAS,
      'interpretation':'Evidência hidrogeológica regional baseada em poços cadastrados no SIAGAS e no mapa hidrogeológico do SGB. Profundidade de poço e nível estático dos vizinhos não garantem água na mesma profundidade dentro do imóvel.',
      'drilling_note':'Para definir ponto de perfuração e profundidade provável com segurança são recomendados estudo hidrogeológico local e, quando cabível, métodos geofísicos. A autorização para uso/perfuração de água subterrânea é de competência estadual.'
    }

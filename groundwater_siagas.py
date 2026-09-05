from __future__ import annotations

import math
import statistics
from typing import Any

import httpx
from shapely.geometry import shape

SIAGAS_ENDPOINTS=[
    ('SGB/SIAGAS Hosted — Poços','https://geoportal.sgb.gov.br/server/rest/services/Hosted/siagas_web/FeatureServer/1/query'),
    ('SGB/SIAGAS MODDAD','https://geoportal.sgb.gov.br/server/rest/services/hidrologia/SIAGAS_MODDAD/FeatureServer/0/query'),
    ('SGB/SIAGAS Hosted — Legacy','https://geoportal.sgb.gov.br/server/rest/services/Hosted/siagas_web/FeatureServer/0/query'),
]
HYDRO_ATLAS='https://geoportal.sgb.gov.br/server/rest/services/hidrologia/Mapa_Hidrogeologico_Atlas_Brasil/MapServer'


def _f(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except Exception:return None


def _positive(v):
    x=_f(v);return x if x is not None and x>0 else None


def _median(vals):
    xs=[float(x) for x in vals if x is not None];return round(statistics.median(xs),2) if xs else None


def _mean(vals):
    xs=[float(x) for x in vals if x is not None];return round(sum(xs)/len(xs),2) if xs else None


def _haversine_km(lat1,lon1,lat2,lon2):
    r=6371.0088;p1,p2=math.radians(lat1),math.radians(lat2);dphi=math.radians(lat2-lat1);dl=math.radians(lon2-lon1);a=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2;return r*2*math.atan2(math.sqrt(a),math.sqrt(max(0,1-a)))


def _centroid(car_geometry:dict[str,Any]):
    c=shape(car_geometry).centroid;return float(c.x),float(c.y)


def _dominant(values,limit=5):
    counts={}
    for v in values:
        s=str(v or '').strip()
        if s:counts[s]=counts.get(s,0)+1
    return [{'name':k,'count':v} for k,v in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))[:limit]]


def _bbox(lon:float,lat:float,radius_km:float):
    dlat=radius_km/110.574;dlon=radius_km/max(20.0,111.320*math.cos(math.radians(lat)));return lon-dlon,lat-dlat,lon+dlon,lat+dlat


async def _call(url:str,params:dict):
    async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.29-groundwater-resilient'}) as c:
        r=await c.get(url,params=params);r.raise_for_status();data=r.json()
    if data.get('error'):raise RuntimeError(str(data.get('error'))[:400])
    return data.get('features') or []


async def _query_radius(lon:float,lat:float,radius_km:float,limit:int=800):
    attempts=[];limit=max(20,min(int(limit),1000));minlon,minlat,maxlon,maxlat=_bbox(lon,lat,radius_km)
    for label,url in SIAGAS_ENDPOINTS:
        # Strategy A: native ArcGIS distance query.
        params={'f':'geojson','where':'1=1','geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':'4326','spatialRel':'esriSpatialRelIntersects','distance':str(radius_km*1000),'units':'esriSRUnit_Meter','outFields':'*','returnGeometry':'true','outSR':'4326','resultRecordCount':str(limit)}
        try:
            fs=await _call(url,params);attempts.append({'source':label,'strategy':'distance','ok':True,'count':len(fs)})
            if fs:return {'ok':True,'features':fs,'source':label,'attempts':attempts}
        except Exception as e:attempts.append({'source':label,'strategy':'distance','ok':False,'detail':f'{type(e).__name__}:{str(e)[:180]}'})
        # Strategy B: envelope query. More portable across ArcGIS service versions.
        params={'f':'geojson','where':'1=1','geometry':f'{minlon},{minlat},{maxlon},{maxlat}','geometryType':'esriGeometryEnvelope','inSR':'4326','spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','outSR':'4326','resultRecordCount':str(limit)}
        try:
            fs=await _call(url,params);attempts.append({'source':label,'strategy':'envelope','ok':True,'count':len(fs)})
            if fs:return {'ok':True,'features':fs,'source':label,'attempts':attempts}
        except Exception as e:attempts.append({'source':label,'strategy':'envelope','ok':False,'detail':f'{type(e).__name__}:{str(e)[:180]}'})
    # Zero wells is still a valid consultation if at least one official request succeeded.
    successful=[x for x in attempts if x.get('ok')]
    if successful:return {'ok':True,'features':[],'source':successful[0]['source'],'attempts':attempts}
    return {'ok':False,'features':[],'detail':'all_siagas_routes_failed','attempts':attempts}


def _prop(p:dict,*names):
    lower={str(k).lower():v for k,v in p.items()}
    for n in names:
        if n in p and p.get(n) not in (None,''):return p.get(n)
        if n.lower() in lower and lower.get(n.lower()) not in (None,''):return lower.get(n.lower())
    return None


async def query_groundwater(car_geometry:dict[str,Any],initial_radius_km:float=20.0):
    try:lon,lat=_centroid(car_geometry)
    except Exception as e:return {'ok':False,'source':'SGB/SIAGAS','detail':f'centroid:{e}'}
    radius=float(initial_radius_km);res=await _query_radius(lon,lat,radius)
    if res.get('ok') and len(res.get('features') or [])<4 and radius<50:
        radius=50.0;res2=await _query_radius(lon,lat,radius)
        if res2.get('ok'):res=res2
    if not res.get('ok'):
        return {'ok':False,'source':'SGB/SIAGAS — Poços','detail':res.get('detail'),'attempts':res.get('attempts'),'latitude':lat,'longitude':lon,'hydrogeology_map_url':HYDRO_ATLAS}
    rows=[]
    for f in res.get('features') or []:
        p=f.get('properties') or {};g=f.get('geometry') or {};coords=g.get('coordinates') if isinstance(g,dict) else None
        wlon=_f(_prop(p,'num_longitude_decimal','longitude','lon'));wlat=_f(_prop(p,'num_latitude_decimal','latitude','lat'))
        if (wlon is None or wlat is None) and isinstance(coords,(list,tuple)) and len(coords)>=2:wlon,wlat=_f(coords[0]),_f(coords[1])
        dist=_haversine_km(lat,lon,wlat,wlon) if wlat is not None and wlon is not None else None
        if dist is not None and dist>radius*1.15:continue
        rows.append({'id':_prop(p,'idt_ponto','objectid','OBJECTID'),'distance_km':round(dist,2) if dist is not None else None,'municipality':_prop(p,'str_municipio','municipio'),'uf':_prop(p,'str_uf','uf'),'local':_prop(p,'str_local_ponto','local_ponto'),'name':_prop(p,'str_nome_ponto','nome_ponto'),'nature':_prop(p,'str_natureza_ponto','natureza_ponto'),'situation':_prop(p,'str_tipo_situacao','tipo_situacao'),'water_use':_prop(p,'str_uso_agua','uso_agua'),'aquifer':_prop(p,'str_aquifero','aquifero'),'well_depth_m':_positive(_prop(p,'num_profundidade','profundidade')),'static_level_m':_positive(_prop(p,'ne','nivel_estatico')),'dynamic_level_m':_positive(_prop(p,'nd','nivel_dinamico')),'specific_yield':_positive(_prop(p,'num_vazao_especifica','vazao_especifica')),'ph':_positive(_prop(p,'num_ph','ph')),'conductivity':_positive(_prop(p,'num_condutividade_eletrica','condutividade_eletrica')),'latitude':wlat,'longitude':wlon})
    rows=sorted(rows,key=lambda x:999999 if x.get('distance_km') is None else x['distance_km'])
    depths=[x['well_depth_m'] for x in rows if x.get('well_depth_m') is not None];static=[x['static_level_m'] for x in rows if x.get('static_level_m') is not None];dynamic=[x['dynamic_water_level_m'] for x in rows if x.get('dynamic_water_level_m') is not None] if rows and 'dynamic_water_level_m' in rows[0] else [x['dynamic_level_m'] for x in rows if x.get('dynamic_level_m') is not None];yields=[x['specific_yield'] for x in rows if x.get('specific_yield') is not None];phs=[x['ph'] for x in rows if x.get('ph') is not None]
    n=len(rows);quality='alta' if n>=15 and len(depths)>=8 and len(static)>=5 else ('moderada' if n>=5 and len(depths)>=3 else 'baixa');evidence='forte' if n>=15 else ('moderada' if n>=5 else ('limitada' if n else 'sem poços próximos cadastrados'))
    return {'ok':True,'source':res.get('source') or 'Serviço Geológico do Brasil — SIAGAS','consultation_state':'consulted','attempts':res.get('attempts'),'latitude':round(lat,6),'longitude':round(lon,6),'search_radius_km':radius,'well_count':n,'groundwater_evidence':evidence,'confidence':quality,'well_depth_median_m':_median(depths),'well_depth_mean_m':_mean(depths),'well_depth_sample_n':len(depths),'static_water_level_median_m':_median(static),'static_water_level_sample_n':len(static),'dynamic_water_level_median_m':_median(dynamic),'dynamic_water_level_sample_n':len(dynamic),'specific_yield_median':_median(yields),'specific_yield_sample_n':len(yields),'ph_median':_median(phs),'ph_sample_n':len(phs),'dominant_aquifers':_dominant([x.get('aquifer') for x in rows]),'dominant_uses':_dominant([x.get('water_use') for x in rows]),'nearest_wells':rows[:20],'hydrogeology_map_url':HYDRO_ATLAS,'interpretation':'Evidência hidrogeológica regional baseada em poços cadastrados no SIAGAS e no mapa hidrogeológico do SGB. Profundidade e nível d’água dos poços vizinhos não garantem água na mesma profundidade dentro do imóvel.','drilling_note':'Para definir ponto de perfuração e profundidade provável com segurança são recomendados estudo hidrogeológico local e, quando cabível, métodos geofísicos. A autorização para uso/perfuração de água subterrânea é de competência estadual.'}


print('RX_GROUNDWATER=SIAGAS_MULTI_ROUTE_RESILIENT',flush=True)

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pyproj import Geod
from shapely.geometry import shape
from shapely.ops import unary_union

GEOD=Geod(ellps='GRS80')

SERVICES={
    'terra_indigena':('Terra Indígena','FUNAI / IBAMA-PAMGIA','https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_terra_indigena_a/FeatureServer'),
    'unidade_conservacao':('Unidade de Conservação','CNUC/MMA / IBAMA-PAMGIA','https://pamgia.ibama.gov.br/server/rest/services/BasesSincronizadas/lim_unidades_conserva%C3%A7%C3%A3o_mma_a/FeatureServer'),
    'quilombola':('Território Quilombola','INCRA / IBAMA-PAMGIA','https://pamgia.ibama.gov.br/server/rest/services/BasesSincronizadas/lim_quilombos_incra_a/FeatureServer'),
    'assentamento':('Assentamento','INCRA / IBAMA-PAMGIA','https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/assentamentos_incra/FeatureServer'),
    'embargo_icmbio':('Embargo ICMBio','ICMBio / IBAMA-PAMGIA','https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/adm_embargo_icmbio_a/FeatureServer'),
}


def _area_ha(g):
    try:
        return abs(GEOD.geometry_area_perimeter(g)[0])/10000.0 if g is not None and not g.is_empty else 0.0
    except Exception:
        return 0.0


def _safe_attrs(props:dict[str,Any]):
    out={}
    allow=('nome','name','denomin','terra','etnia','fase','modalidade','categoria','grupo','esfera','municip','uf','codigo','código','situacao','situação','ato','data','area','área','identif')
    deny=('cpf','cnpj','email','telefone','fone','endereco','endereço','propriet','possuidor')
    for k,v in (props or {}).items():
        lk=str(k).lower()
        if any(x in lk for x in deny): continue
        if any(x in lk for x in allow) and v not in (None,''):
            out[str(k)]=v
        if len(out)>=12: break
    return out


async def _query_one(client:httpx.AsyncClient,key:str,meta,car,bbox):
    label,source,root=meta
    try:
        mr=await client.get(root,params={'f':'pjson'})
        md=mr.json(); layers=md.get('layers') or []
        if not layers:
            return key,{'ok':False,'label':label,'source':source,'detail':'service_without_layers'}
        layer_id=layers[0].get('id')
        url=f'{root}/{layer_id}/query'
        env=','.join(str(x) for x in bbox)
        params={'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4674','spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','outSR':'4674','resultRecordCount':'2000'}
        rr=await client.get(url,params=params); data=rr.json(); fs=data.get('features') or []
        intersections=[]; occurrences=[]
        for f in fs:
            try:
                g=shape(f.get('geometry'))
                if not car.intersects(g): continue
                inter=car.intersection(g)
                if inter.is_empty: continue
                intersections.append(inter)
                occurrences.append({'area_intersection_ha':round(_area_ha(inter),6),'attributes':_safe_attrs(f.get('properties') or {})})
            except Exception: continue
        union=unary_union(intersections) if intersections else None
        return key,{'ok':rr.status_code==200 and 'error' not in data,'status':rr.status_code,'label':label,'source':source,'service':root,'layer_id':layer_id,'feature_count_bbox':len(fs),'occurrence_count':len(intersections),'area_unique_ha':round(_area_ha(union),6) if union is not None else 0.0,'occurrences':occurrences[:20],'_geoms':intersections}
    except Exception as e:
        return key,{'ok':False,'label':label,'source':source,'error':type(e).__name__,'detail':str(e)[:280]}


async def query_territorial_constraints(car_geometry:dict[str,Any],bbox:list[float]):
    car=shape(car_geometry)
    async with httpx.AsyncClient(timeout=40,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.15.1'}) as client:
        pairs=await asyncio.gather(*[_query_one(client,k,m,car,bbox) for k,m in SERVICES.items()])
    results=dict(pairs)
    all_geoms=[]
    for r in results.values(): all_geoms.extend(r.pop('_geoms',[]) or [])
    union=unary_union(all_geoms) if all_geoms else None
    return {'ok':any(r.get('ok') for r in results.values()),'services':results,'area_unique_all_constraints_ha':round(_area_ha(union),6) if union is not None else 0.0,'source':'Fontes públicas territoriais via IBAMA/PAMGIA'}

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode

from fastapi import HTTPException
from shapely.geometry import shape
from pyproj import Geod

import portal_v8
from deploy_app import SIGEF_MIRROR, _curl
from car_resilient import fetch_car_live_resilient, CAR_RE

app=portal_v8.app
GEOD=Geod(ellps='GRS80')


def _safe_term(v:str)->str:
    s=re.sub(r'\s+',' ',str(v or '').strip())[:90]
    return s.replace("'","''")


def _sigef_search_sync(term:str,limit:int=20):
    t=_safe_term(term);cap=max(1,min(int(limit),30))
    where=(f"UPPER(nome_area) LIKE UPPER('%{t}%') OR UPPER(municipio_) LIKE UPPER('%{t}%') "
           f"OR UPPER(registro_m) LIKE UPPER('%{t}%') OR UPPER(registro_d) LIKE UPPER('%{t}%') "
           f"OR UPPER(parcela_co) LIKE UPPER('%{t}%') OR UPPER(codigo_imo) LIKE UPPER('%{t}%')")
    p={'f':'geojson','where':where,'outFields':'parcela_co,situacao_i,codigo_imo,data_aprov,status,nome_area,registro_m,registro_d,municipio_,uf_id','returnGeometry':'true','outSR':'4326','resultRecordCount':str(cap)}
    raw=_curl(SIGEF_MIRROR+'?'+urlencode(p),True)
    if not raw.get('ok'):return {'ok':False,'source':'SIGEF/INCRA — espelho público IBAMA/PAMGIA','detail':raw.get('detail') or raw.get('preview')}
    data=raw.get('json') or {};features=data.get('features') or [];items=[]
    for f in features:
        props=f.get('properties') or {};geom=f.get('geometry');center=None;area_ha=None
        try:
            g=shape(geom);c=g.centroid;center={'lat':float(c.y),'lon':float(c.x)}
            area_ha=abs(GEOD.geometry_area_perimeter(g)[0])/10000.0
        except Exception:pass
        items.append({'type':'sigef','name':props.get('nome_area') or 'Área certificada SIGEF','municipality':props.get('municipio_'),'uf':props.get('uf_id'),'parcel_code':props.get('parcela_co'),'property_code':props.get('codigo_imo'),'registry':props.get('registro_m') or props.get('registro_d'),'status':props.get('status') or props.get('situacao_i'),'area_ha':round(area_ha,4) if area_ha is not None else None,'center':center,'geometry':geom,'source':'SIGEF/INCRA — espelho público'})
    return {'ok':True,'source':'SIGEF/INCRA — espelho público IBAMA/PAMGIA','items':items,'count':len(items)}


@app.get('/v1/live/search/properties')
async def smart_property_search(q:str,limit:int=20):
    term=str(q or '').strip()
    if len(term)<2:raise HTTPException(status_code=422,detail='Digite pelo menos 2 caracteres.')
    upper=term.upper()
    if CAR_RE.match(upper):
        car=await asyncio.to_thread(fetch_car_live_resilient,upper)
        if not car.get('ok'):return {'ok':False,'mode':'car','items':[],'detail':car.get('detail') or 'CAR não localizado'}
        p=car.get('properties') or {};geom=car.get('geometry');center=None
        try:
            c=shape(geom).centroid;center={'lat':float(c.y),'lon':float(c.x)}
        except Exception:pass
        return {'ok':True,'mode':'car','items':[{'type':'car','name':p.get('nome_imovel') or p.get('denominacao') or f"Imóvel rural — {p.get('municipio') or ''}",'car_code':p.get('cod_imovel') or upper,'municipality':p.get('municipio'),'uf':p.get('uf'),'area_ha':p.get('area'),'center':center,'geometry':geom,'source':'SICAR'}]}
    sig=await asyncio.to_thread(_sigef_search_sync,term,limit)
    return {'ok':sig.get('ok',False),'mode':'name_or_identifier','query':term,'items':sig.get('items') or [],'source':sig.get('source'),'detail':sig.get('detail'),'note':'Busca nominal usa campos públicos efetivamente expostos pelo SIGEF/espelho público. Titularidade por CPF/CNPJ só será habilitada por integração legalmente autorizada.'}

print('RX_PROPERTY_SEARCH=farm_name_car_registry_sigef_area',flush=True)

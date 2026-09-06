from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_advanced_search_v39 as adv
from property_search import _sigef_search_sync
from car_resilient import CAR_RE
from portal_mobile_v19 import resolve_v19

app=portal_v8.app


def _norm(v:Any)->str:
    return adv._norm(v)


async def _enrich_sigef(rows:list[dict[str,Any]],limit:int=12)->list[dict[str,Any]]:
    sem=asyncio.Semaphore(3)
    async def one(x:dict[str,Any]):
        y=dict(x)
        c=y.get('center') or {}
        lat,lon=c.get('lat'),c.get('lon')
        if lat is None or lon is None:return y
        async with sem:
            try:
                d=await asyncio.wait_for(resolve_v19(float(lat),float(lon)),timeout=10)
                p=d.get('property') or {}
                if p.get('car_code'):
                    # Centroid coincidence is context only. It is not a denomination link.
                    y['nearby_car_code']=p.get('car_code')
                    y['nearby_car_geometry']=d.get('geometry')
                    y['car_match']='REFERENCE_ONLY_CENTROID'
                    y['panel_name_eligible']=False
            except Exception:
                y['car_match']='not_resolved'
        return y
    return await asyncio.gather(*(one(x) for x in rows[:limit])) if rows else []


async def advanced_search_v40(uf:str,municipality:str,q:str|None=None,min_area_ha:float|None=None,max_area_ha:float|None=None,limit:int=40):
    term=(q or '').strip()
    if not term or CAR_RE.match(term.upper()):
        return await adv.advanced_property_search(uf,municipality,q,min_area_ha,max_area_ha,limit)

    # Legacy reference branch. V44 replaces this route with direct CAFIR name search,
    # but this module remains truth-safe if import order changes in the future.
    base_task=asyncio.create_task(adv.advanced_property_search(uf,municipality,None,min_area_ha,max_area_ha,min(limit,40)))
    sig_task=asyncio.to_thread(_sigef_search_sync,term,min(max(int(limit),12),30))
    base_result,sig=await asyncio.gather(base_task,sig_task,return_exceptions=True)
    if isinstance(base_result,Exception):base_result={'ok':False,'items':[],'uf':uf,'municipality':municipality,'detail':str(base_result)}
    if isinstance(sig,Exception):sig={'ok':False,'items':[],'detail':str(sig)}

    wanted_city=_norm(municipality);filtered=[]
    for x in sig.get('items') or []:
        xuf=str(x.get('uf') or '').upper()
        city=_norm(x.get('municipality'))
        if xuf and xuf!=str(uf).upper():continue
        if city and wanted_city not in city and city not in wanted_city:continue
        area=x.get('area_ha')
        try:area=float(area) if area is not None else None
        except Exception:area=None
        if min_area_ha is not None and (area is None or area<float(min_area_ha)):continue
        if max_area_ha is not None and (area is None or area>float(max_area_ha)):continue
        y=dict(x);y['type']='sigef';y['display_kind']='REFERENCE';y['panel_name_eligible']=False;filtered.append(y)
    filtered=await _enrich_sigef(filtered,min(12,int(limit)))

    if filtered:
        return {
            'ok':True,'items':filtered[:max(1,min(int(limit),40))],'count':len(filtered[:max(1,min(int(limit),40))]),
            'uf':str(uf).upper(),'municipality':municipality,'source':'SIGEF/INCRA — referência cadastral',
            'truncated':len(filtered)>int(limit),
            'mode':'legacy_sigef_reference',
            'note':'Nome/matrícula vêm da parcela certificada SIGEF. Eventual CAR sob o centroide é apenas referência espacial e nunca herda a denominação.'
        }
    out=dict(base_result if isinstance(base_result,dict) else {'ok':False,'items':[]})
    out['mode']='car_fallback_after_name_lookup'
    out['note']='Nenhuma referência SIGEF compatível foi localizada; a busca CAR municipal permanece disponível. O sistema não inventa nome de fazenda.'
    return out


app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/search/advanced']
app.get('/v1/live/search/advanced')(advanced_search_v40)

old="function mapOpen(x){close();try{if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(typeof showProperty==='function')showProperty({car_code:x.car_code,name:x.name,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry)}catch(e){if(typeof toast==='function')toast('Não foi possível abrir este imóvel agora.')}}"
new="async function mapOpen(x){close();try{if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(x.car_code&&x.name_validation_status==='VALIDATED'&&x.panel_name_eligible===true&&typeof showProperty==='function'){showProperty({car_code:x.car_code,name:x.name,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry);return}if(typeof toast==='function')toast('Referência SIGEF localizada. Nenhuma denominação foi aplicada a um CAR sem vínculo validado.')}catch(e){if(typeof toast==='function')toast('Não foi possível abrir esta referência agora.')}}"
if old in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(old,new,1)

print('RX_ADVANCED_NAME_V40=legacy_reference_truth_safe_no_name_inheritance',flush=True)

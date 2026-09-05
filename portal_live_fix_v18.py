from __future__ import annotations

import asyncio
import os
import httpx
from fastapi import HTTPException

import portal_v8
from critical_minerals import query_critical_minerals
from agropecuaria import build_agro_profile
from car_resilient import fetch_car_live_resilient
from anm_resilient import query_anm_curl_exact

app=portal_v8.app
HEAVY_BASE=os.getenv('RX_HEAVY_BASE_URL','https://raio-x-territorial-report.onrender.com').rstrip('/')

REPLACE_PATHS={'/v1/live/critical-minerals/{car_code}','/v1/live/agropecuaria/{car_code}'}
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in REPLACE_PATHS]

async def _car(code:str):
    car=await asyncio.to_thread(fetch_car_live_resilient,code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR indisponível')
    return car

@app.get('/v1/live/critical-minerals/{car_code}')
async def critical_minerals_v18(car_code:str):
    code=car_code.upper();car=await _car(code);geom=car.get('geometry');bbox=car.get('bbox') or []
    anm_task=asyncio.to_thread(query_anm_curl_exact,geom,bbox)
    # SGB can classify the ANM payload, but doing both serially is slow. Query ANM
    # first with a strict direct endpoint, then give it to SGB classification.
    anm=await anm_task
    try:current=await asyncio.wait_for(query_critical_minerals(geom,anm),timeout=45)
    except Exception as e:current={'ok':False,'detail':f'{type(e).__name__}:{str(e)[:220]}','anm':{}}
    classified=current.get('anm') or {};sgb=current.get('sgb') or {};exact=anm.get('exact') or {}
    anm_available=bool(anm.get('ok') and exact.get('available'))
    sgb_available=bool(sgb.get('capabilities_ok'))
    return {
        **current,'ok':bool(anm_available or sgb_available),
        'state':'consulted' if anm_available and sgb_available else ('partial' if anm_available or sgb_available else 'unavailable'),
        'anm_available':anm_available,'sgb_available':sgb_available,
        'anm':{**classified,'process_count':classified.get('process_count') if classified.get('process_count') is not None else exact.get('occurrence_count',0),'exact':exact},
        'source':'ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB)',
        'note':'Consulta direta da aba. Não depende da geração do dossiê completo; falha de uma fonte não é tratada como ausência.'
    }

async def _heavy_agro(code:str):
    try:
        async with httpx.AsyncClient(timeout=120,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/mobile-heavy-proxy'}) as c:
            r=await c.get(f'{HEAVY_BASE}/v1/heavy/agro-raster/{code}')
            if r.status_code>=400:return {'ok':False,'detail':f'heavy_http_{r.status_code}'}
            return r.json()
    except Exception as e:return {'ok':False,'detail':f'heavy_worker:{type(e).__name__}:{str(e)[:180]}'}

@app.get('/v1/live/agropecuaria/{car_code}')
async def agropecuaria_v18(car_code:str):
    code=car_code.upper();car=await _car(code)
    minimal={'car':car}
    profile_task=build_agro_profile(minimal,code,True)
    heavy_task=_heavy_agro(code)
    profile,heavy=await asyncio.gather(profile_task,heavy_task,return_exceptions=True)
    if isinstance(profile,Exception):profile={'ok':False,'detail':f'{type(profile).__name__}:{str(profile)[:200]}'}
    if isinstance(heavy,Exception):heavy={'ok':False,'detail':f'{type(heavy).__name__}:{str(heavy)[:200]}'}
    mb=(heavy or {}).get('mapbiomas') or {};terrain=(heavy or {}).get('terrain_srtm') or {}
    screening=(profile.setdefault('property_screening',{}) if isinstance(profile,dict) else {}).setdefault('checks',[])
    # Remove stale placeholder terrain/soil rows from the lightweight municipal profile.
    screening=[x for x in screening if str(x.get('factor') or '') not in {'Solo','Aptidão agrícola','Declividade'}]
    if terrain.get('ok'):
        screening.append({'factor':'Declividade','scope':'SRTM ~30 m dentro do CAR','status':'consultada','value':{'median_deg':terrain.get('slope_median_deg'),'p90_deg':terrain.get('slope_p90_deg'),'elevation_median_m':terrain.get('elevation_median_m')}})
    profile['property_screening']['checks']=screening
    profile['mapbiomas']=mb;profile['terrain_srtm']=terrain
    profile['pasture']={
        'state':'ready' if mb.get('ok') else 'unavailable','source':mb.get('source') or 'MapBiomas Brasil — Coleção 11',
        'year':mb.get('year'),'pasture_area_ha':mb.get('pasture_area_ha'),'pasture_share_pct':mb.get('pasture_share_pct'),
        'native_vegetation_share_pct':mb.get('native_vegetation_share_pct'),'agriculture_and_pasture_share_pct':mb.get('agriculture_and_pasture_share_pct'),
        'note':mb.get('note') or mb.get('detail') or heavy.get('detail')
    }
    profile['heavy_worker']='report-service' if heavy.get('ok') else 'degraded'
    profile['ok']=bool(profile.get('ok') or mb.get('ok') or terrain.get('ok'))
    profile['state']='consulted' if profile['ok'] else 'unavailable'
    return profile

# Final HTML corrections after tabs are loaded.
def install_ui_fixes():
    html=portal_v8.PORTAL_HTML
    html=html.replace("const active=d.persistence==='durable'","const active=['durable','operational_nonpersistent'].includes(d.persistence)")
    html=html.replace("${active?'PERSISTENTE — PRONTO':'AGUARDANDO VÍNCULO DO BANCO'}","${d.persistence==='durable'?'PERSISTENTE — PRONTO':(active?'OPERACIONAL — FREE':'BACKEND DE ALERTAS INDISPONÍVEL')}")
    html=html.replace("Pastagem/vigor MapBiomas só exibirá percentuais quando o worker raster devolver métricas reais do polígono.","${d.pasture?.state==='ready'?`Pastagem MapBiomas ${d.pasture.year||''}: <b>${fmt(d.pasture.pasture_area_ha,2)} ha</b> (${fmt(d.pasture.pasture_share_pct,1)}% do CAR). Vegetação nativa: ${fmt(d.pasture.native_vegetation_share_pct,1)}%.`:'MapBiomas não respondeu nesta consulta; isso não significa ausência de pastagem.'}")
    html=html.replace("Fontes restritas permanecem preparadas — OFF até habilitação.","Fontes públicas são consultadas automaticamente. Serviços cadastrais/registrários pagos aparecem abaixo apenas como integrações opcionais que exigem credencial ou contratação.")
    html=html.replace("<span class=\"rx-pill\">${x.ready?'ATIVO':'PREPARADO — OFF'}</span>","<span class=\"rx-pill\">${x.ready?'ATIVO':'OPCIONAL / REQUER HABILITAÇÃO'}</span>")
    portal_v8.PORTAL_HTML=html

install_ui_fixes()
print('RX_PORTAL_LIVE_FIX=V20_DIRECT_TABS_HEAVY_WORKER',flush=True)

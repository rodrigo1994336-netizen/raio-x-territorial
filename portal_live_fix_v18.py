from __future__ import annotations

import asyncio
from fastapi import HTTPException

import portal_v8
import report_api
from critical_minerals import query_critical_minerals
from agropecuaria import build_agro_profile
from mapbiomas_coverage import query_mapbiomas_coverage
from terrain_srtm import query_terrain_srtm

app=portal_v8.app

# Replace brittle V8 endpoints with resilient versions loaded after portal_v8.
REPLACE_PATHS={'/v1/live/critical-minerals/{car_code}','/v1/live/agropecuaria/{car_code}'}
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in REPLACE_PATHS]


async def _result(code:str):
    try:
        return await report_api._analyze_with_live_addons(code.upper())
    except Exception as exc:
        raise HTTPException(status_code=502,detail=f'análise territorial indisponível: {type(exc).__name__}:{str(exc)[:180]}')


@app.get('/v1/live/critical-minerals/{car_code}')
async def critical_minerals_v18(car_code:str):
    result=await _result(car_code);car=result.get('car') or {}
    if not car.get('ok'):raise HTTPException(status_code=404,detail='CAR não localizado')
    current=result.get('critical_minerals') or {}
    # Re-run only when the cached addon did not return a usable object.
    if not current or (not current.get('anm') and not current.get('sgb')):
        try:current=await query_critical_minerals(car.get('geometry'),result.get('anm'))
        except Exception as e:current={'ok':False,'detail':f'{type(e).__name__}:{str(e)[:220]}'}
    anm=current.get('anm') or {};sgb=current.get('sgb') or {}
    anm_exact=((result.get('anm') or {}).get('exact') or {})
    anm_available=bool((result.get('anm') or {}).get('ok') or anm_exact.get('available'))
    sgb_available=bool(sgb.get('capabilities_ok'))
    # A partial SGB outage must not make the whole mining tab unavailable when ANM responded.
    return {
        **current,
        'ok':bool(anm_available or sgb_available),
        'state':'consulted' if anm_available and sgb_available else ('partial' if anm_available or sgb_available else 'unavailable'),
        'anm_available':anm_available,'sgb_available':sgb_available,
        'anm':{**anm,'process_count':anm.get('process_count') if anm.get('process_count') is not None else anm_exact.get('occurrence_count')},
        'source':'ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB)',
        'note':'ANM e GeoSGB são avaliados separadamente. Falha de uma fonte não é interpretada como ausência de processo ou potencial mineral.'
    }


@app.get('/v1/live/agropecuaria/{car_code}')
async def agropecuaria_v18(car_code:str):
    code=car_code.upper();result=await _result(code);car=result.get('car') or {};geom=car.get('geometry')
    if not car.get('ok'):raise HTTPException(status_code=404,detail='CAR não localizado')
    profile_task=build_agro_profile(result,code,True)
    mb_task=asyncio.to_thread(query_mapbiomas_coverage,geom,2025)
    terrain_task=asyncio.to_thread(query_terrain_srtm,geom)
    profile,mb,terrain=await asyncio.gather(profile_task,mb_task,terrain_task,return_exceptions=True)
    if isinstance(profile,Exception):profile={'ok':False,'detail':f'{type(profile).__name__}:{str(profile)[:200]}'}
    if isinstance(mb,Exception):mb={'ok':False,'detail':f'{type(mb).__name__}:{str(mb)[:200]}'}
    if isinstance(terrain,Exception):terrain={'ok':False,'detail':f'{type(terrain).__name__}:{str(terrain)[:200]}'}
    ide=result.get('ide_layers') or {}
    soil=ide.get('soil') or ide.get('solo') or {};apt=ide.get('aptitude') or ide.get('aptidao') or {};slope=ide.get('slope') or ide.get('declividade') or {}
    screening=(profile.setdefault('property_screening',{}) if isinstance(profile,dict) else {}).setdefault('checks',[])
    screening=[x for x in screening if str(x.get('factor') or '') not in {'Solo','Aptidão agrícola','Declividade'}]
    screening += [
        {'factor':'Solo','scope':'interseção cartográfica','status':'consultada' if soil.get('ok') else 'parcial','value':{'count':soil.get('exact_count'),'layer':soil.get('layer')}},
        {'factor':'Aptidão agrícola','scope':'interseção cartográfica','status':'consultada' if apt.get('ok') else 'parcial','value':{'count':apt.get('exact_count'),'layer':apt.get('layer')}},
        {'factor':'Declividade','scope':'SRTM ~30 m dentro do CAR','status':'consultada' if terrain.get('ok') else ('consultada' if slope.get('ok') else 'parcial'),'value':{'median_deg':terrain.get('slope_median_deg'),'p90_deg':terrain.get('slope_p90_deg')}},
    ]
    profile['property_screening']['checks']=screening
    profile['mapbiomas']=mb
    profile['terrain_srtm']=terrain
    profile['pasture']={
        'state':'ready' if mb.get('ok') else 'unavailable','source':mb.get('source') or 'MapBiomas Brasil — Coleção 11',
        'year':mb.get('year'),'pasture_area_ha':mb.get('pasture_area_ha'),'pasture_share_pct':mb.get('pasture_share_pct'),
        'native_vegetation_share_pct':mb.get('native_vegetation_share_pct'),'agriculture_and_pasture_share_pct':mb.get('agriculture_and_pasture_share_pct'),
        'note':mb.get('note') or mb.get('detail')
    }
    profile['ok']=bool(profile.get('ok') or mb.get('ok') or terrain.get('ok'))
    profile['state']='consulted' if profile['ok'] else 'unavailable'
    return profile


# Final HTML corrections after tabs are loaded.
def install_ui_fixes():
    html=portal_v8.PORTAL_HTML
    html=html.replace("const active=d.persistence==='durable'","const active=['durable','operational_nonpersistent'].includes(d.persistence)")
    html=html.replace("${active?'PERSISTENTE — PRONTO':'AGUARDANDO VÍNCULO DO BANCO'}","${d.persistence==='durable'?'PERSISTENTE — PRONTO':(active?'OPERACIONAL — FREE':'BACKEND DE ALERTAS INDISPONÍVEL')}")
    html=html.replace("Pastagem/vigor MapBiomas só exibirá percentuais quando o worker raster devolver métricas reais do polígono.","${d.pasture?.state==='ready'?`Pastagem MapBiomas ${d.pasture.year||''}: <b>${fmt(d.pasture.pasture_area_ha,2)} ha</b> (${fmt(d.pasture.pasture_share_pct,1)}% do CAR). Vegetação nativa: ${fmt(d.pasture.native_vegetation_share_pct,1)}%.`:'MapBiomas não respondeu nesta consulta; isso não significa ausência de pastagem.'}")
    # Premium/restricted integrations should not look like broken public sources.
    html=html.replace("Fontes restritas permanecem preparadas — OFF até habilitação.","Fontes públicas são consultadas automaticamente. Serviços cadastrais/registrários pagos aparecem abaixo apenas como integrações opcionais que exigem credencial ou contratação.")
    html=html.replace("<span class=\"rx-pill\">${x.ready?'ATIVO':'PREPARADO — OFF'}</span>","<span class=\"rx-pill\">${x.ready?'ATIVO':'OPCIONAL / REQUER HABILITAÇÃO'}</span>")
    portal_v8.PORTAL_HTML=html

install_ui_fixes()
print('RX_PORTAL_LIVE_FIX=V18_MINING_AGRO_MONITORING',flush=True)

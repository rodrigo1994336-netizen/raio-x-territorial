from __future__ import annotations

import asyncio

import portal_v8
from anm_resilient import query_anm_curl_exact
from car_resilient import fetch_car_live_resilient
from critical_minerals import query_critical_minerals

app=portal_v8.app
PATH='/v1/live/critical-minerals/{car_code}'
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!=PATH]


def _unavailable(code:str,detail:str):
    return {
        'ok':False,'state':'unavailable','car_code':code,'rare_earth_signal':None,
        'interpretation':'Consulta mineral temporariamente indisponível. Não interpretar como ausência de processo minerário, mineral crítico ou terras raras.',
        'anm':{'process_count':'NÃO CONCLUÍDO','critical_process_count':'NÃO CONCLUÍDO','exact':{'available':False}},
        'sgb':{'hit_layers':[],'capabilities_ok':False},
        'detail':detail,'source':'ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB)'
    }


@app.get(PATH)
async def critical_minerals_v34(car_code:str):
    code=car_code.upper()
    try:
        car=await asyncio.wait_for(asyncio.to_thread(fetch_car_live_resilient,code),timeout=9)
    except Exception as e:
        return _unavailable(code,f'CAR/SICAR lento: {type(e).__name__}')
    if not car.get('ok'):
        return _unavailable(code,'CAR/SICAR não respondeu a tempo para a consulta mineral.')
    geom=car.get('geometry');bbox=car.get('bbox') or []

    try:
        anm=await asyncio.wait_for(asyncio.to_thread(query_anm_curl_exact,geom,bbox),timeout=10)
    except Exception as e:
        anm={'ok':False,'detail':f'{type(e).__name__}:{str(e)[:160]}','exact':{'available':False,'occurrence_count':None}}

    try:
        sgb_result=await asyncio.wait_for(query_critical_minerals(geom,anm),timeout=16)
    except Exception as e:
        sgb_result={'ok':False,'detail':f'{type(e).__name__}:{str(e)[:160]}','anm':{},'sgb':{}}

    classified=sgb_result.get('anm') or {};sgb=sgb_result.get('sgb') or {};exact=anm.get('exact') or {}
    anm_available=bool(anm.get('ok') and exact.get('available'))
    sgb_available=bool(sgb.get('capabilities_ok') or sgb.get('ok'))
    state='consulted' if anm_available and sgb_available else ('partial' if anm_available or sgb_available else 'unavailable')
    if state=='unavailable':
        return _unavailable(code,sgb_result.get('detail') or anm.get('detail') or 'fontes minerais indisponíveis')
    rare=sgb_result.get('rare_earth_signal') if state!='unavailable' else None
    return {
        **sgb_result,'ok':True,'state':state,'car_code':code,'rare_earth_signal':rare,
        'anm_available':anm_available,'sgb_available':sgb_available,
        'anm':{**classified,'process_count':classified.get('process_count') if classified.get('process_count') is not None else exact.get('occurrence_count'),'exact':exact},
        'source':'ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB)',
        'note':'A aba devolve resultado parcial quando uma fonte externa está lenta; indisponibilidade nunca é tratada como ausência.'
    }


html=portal_v8.PORTAL_HTML
html=html.replace(
    "['SGB',fmt((s.hit_layers||[]).length,0),'camadas com sinal']",
    "['SGB',d.state==='unavailable'?'NÃO CONCLUÍDO':fmt((s.hit_layers||[]).length,0),d.state==='unavailable'?'fonte indisponível':'camadas com sinal']"
)
html=html.replace(
    "['Terras raras',rare?'SINAL':'SEM SINAL ESPECÍFICO','nas fontes que responderam']",
    "['Terras raras',d.state==='unavailable'?'NÃO CONCLUÍDO':(rare?'SINAL':'SEM SINAL ESPECÍFICO'),d.state==='unavailable'?'consulta inconclusiva':'nas fontes que responderam']"
)
portal_v8.PORTAL_HTML=html
print('RX_MINING_RESILIENCE_V34=fail_soft_no_false_negative',flush=True)

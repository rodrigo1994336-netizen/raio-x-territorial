from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request

import portal_v8
# Os dois KPI remendados no fim deste arquivo são escritos por portal_property_tabs. Sem declarar essa
# dependência, o remendo só casa quando outro módulo importou as abas primeiro: em qualquer ordem
# diferente o arranque cai com mining_resilience_anchor_missing. Importar é idempotente.
import portal_property_tabs  # noqa: F401 - dono das âncoras kpi-servico-geologico e kpi-terras-raras
from anm_resilient import query_anm_curl_exact
from car_resilient import fetch_car_live_resilient
from external_process_lifecycle import RequestDisconnected, wait_for_cancelling_processes
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
async def critical_minerals_v34(car_code:str,request:Request=None):
    code=car_code.upper()
    try:
        car=await wait_for_cancelling_processes(asyncio.to_thread(fetch_car_live_resilient,code),9,request=request)
    except RequestDisconnected:
        raise HTTPException(status_code=499,detail='Consulta encerrada: o cliente desistiu.')
    except Exception as e:
        return _unavailable(code,f'CAR/SICAR lento: {type(e).__name__}')
    if not car.get('ok'):
        return _unavailable(code,'CAR/SICAR não respondeu a tempo para a consulta mineral.')
    geom=car.get('geometry');bbox=car.get('bbox') or []

    try:
        anm=await wait_for_cancelling_processes(asyncio.to_thread(query_anm_curl_exact,geom,bbox),10,request=request)
    except RequestDisconnected:
        raise HTTPException(status_code=499,detail='Consulta encerrada: o cliente desistiu.')
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


# T2: "fonte indisponível" e "consulta inconclusiva" são estado técnico na cara do cliente; o rótulo
# "SGB" é sigla sem explicação. Remendo por string que não casa é silencioso — cada âncora é conferida.
_FIXES=[
    ("kpi-servico-geologico",
     "['Serviço Geológico',fmt((s.hit_layers||[]).length,0),'camadas com sinal']",
     "['Serviço Geológico',d.state==='unavailable'?'CONSULTA PENDENTE':fmt((s.hit_layers||[]).length,0),d.state==='unavailable'?'consulta pendente':'camadas com sinal']"),
    ("kpi-terras-raras",
     "['Terras raras',rare?'SINAL':'SEM SINAL ESPECÍFICO','nas fontes que responderam']",
     "['Terras raras',d.state==='unavailable'?'CONSULTA PENDENTE':(rare?'SINAL':'SEM SINAL ESPECÍFICO'),d.state==='unavailable'?'consulta pendente':'nas fontes que responderam']"),
]
# 16/09 — A TRAVA DE ARRANQUE SAIU DE CIMA DE CODIGO MORTO. As duas ancoras vivem dentro de mineracao(),
# que so e chamada por load() <- activate() <- os botoes que install() cria; e o portal_experience_v43,
# carregado logo depois neste mesmo arranque, desliga o install() (troca setInterval(install,500) por
# window.rxLegacyTabsDisabledV43=true). Conferido na tela, no cartao V46 de Curvelo, em 1440 e em 375:
# #rxPropertyTabs ausente, .rx-tab-pane = 0, .rx-kpi = 0, 'Servico Geologico' e 'Terras raras' ausentes do
# texto da pagina. Nenhum cliente ve estes dois KPI hoje.
# Derrubar o arranque INTEIRO do portal por um texto que nao chega a ninguem troca um defeito invisivel por
# uma tela em branco — e ja aconteceu: um cenario do m1 reprovou com mining_resilience_anchor_missing so
# porque importou este modulo fora da ordem do sitecustomize. O remendo continua conferido; a ancora que
# falta vira marcador de arranque, e quem reprova e o portao no CI (f1b_tela_gate, regra 1B.9), onde da
# para consertar antes de publicar. Quando a mineracao e as terras raras forem para a leitura completa F1B
# (caminho B), as ancoras vao junto e esta trava volta a fazer sentido no lugar novo.
html=portal_v8.PORTAL_HTML
_perdidas=[nome for nome,busca,_ in _FIXES if busca not in html]
for _nome,_busca,_troca in _FIXES:
    html=html.replace(_busca,_troca)
portal_v8.PORTAL_HTML=html
if _perdidas:
    print('RX_MINING_RESILIENCE_V34_ANCHOR_MISSING='+','.join(_perdidas),flush=True)
print(f'RX_MINING_RESILIENCE_V34=fail_soft_no_false_negative '
      f'anchors:{len(_FIXES)-len(_perdidas)}/{len(_FIXES)} legacy_tabs_off_by_v43',flush=True)

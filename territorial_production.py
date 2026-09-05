from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

import httpx

from agropecuaria import municipality_code_from_car, query_ppm, query_animal_products

SIDRA='https://apisidra.ibge.gov.br/values'


def _norm(v: Any) -> str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',s).strip().lower()


def _num(v):
    raw=str(v or '').strip()
    if raw in {'','-','..','...','X'}:return None
    try:return float(raw.replace('.','').replace(',','.'))
    except Exception:return None


def _period(row:dict[str,Any]):
    for v in row.values():
        s=str(v or '').strip()
        if re.fullmatch(r'19\d{2}|20\d{2}',s):return s
    return None


def _unit(row:dict[str,Any]):
    for k,v in row.items():
        lk=_norm(k);nv=_norm(v)
        if 'unidade' in lk or nv in {'hectare','hectares','ha','tonelada','toneladas','quilo','quilogramas','mil reais','metros cubicos','m³','m3'}:
            return str(v)
    return None


def _measure(row:dict[str,Any]):
    text=' | '.join(str(v or '') for v in row.values());nt=_norm(text)
    for label in ('Área total existente em 31/12','Quantidade produzida','Valor da produção','Área colhida'):
        if _norm(label) in nt:return label
    return None


async def _sidra_table(table:int,car_code:str,periods:int=2,timeout:float=18.0):
    mun=municipality_code_from_car(car_code)
    if not mun:return None,{'ok':False,'source':f'IBGE/SIDRA tabela {table}','detail':'municipality_code_not_available'}
    url=f'{SIDRA}/t/{table}/n6/{mun}/v/allxp/p/last%20{max(1,min(periods,3))}'
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.33-territorial-production'}) as c:
            r=await c.get(url);r.raise_for_status();rows=r.json()
        if not isinstance(rows,list) or len(rows)<2:
            return None,{'ok':False,'source':f'IBGE/SIDRA tabela {table}','detail':'empty_response','municipality_code':mun}
        return rows[1:],None
    except Exception as e:
        return None,{'ok':False,'source':f'IBGE/SIDRA tabela {table}','detail':f'{type(e).__name__}:{str(e)[:220]}','municipality_code':mun}


async def query_silviculture_area(car_code:str):
    rows,err=await _sidra_table(5930,car_code,2)
    if err:return {**err,'dataset':'PEVS — área de silvicultura'}
    out=[]
    for row in rows:
        text=' | '.join(str(v or '') for v in row.values());nt=_norm(text)
        species=None
        if 'eucalipto' in nt:species='Eucalipto'
        elif 'pinus' in nt:species='Pinus'
        elif any(x in nt for x in ('outras especies','outra especie')):species='Outras espécies florestais'
        if not species:continue
        value=_num(row.get('V'))
        if value is None:continue
        out.append({'species':species,'period':_period(row),'value':value,'unit':_unit(row) or 'ha','measure':_measure(row) or 'área existente'})
    out.sort(key=lambda x:(x.get('period') or '',x.get('species') or ''))
    return {
        'ok':True,'source':'IBGE/SIDRA — PEVS tabela 5930','series':out,
        'eucalyptus_latest':next((x for x in reversed(out) if x['species']=='Eucalipto'),None),
        'note':'Dado municipal de silvicultura. Eucalipto no município não prova plantio dentro da fazenda; no imóvel, a classe de silvicultura é avaliada separadamente por uso/cobertura do solo.'
    }


async def query_forestry_products(car_code:str):
    rows,err=await _sidra_table(291,car_code,2)
    if err:return {**err,'dataset':'PEVS — produção da silvicultura'}
    terms=('carvao vegetal','lenha','madeira em tora','folhas de eucalipto','resina','casca de acacia')
    out=[]
    for row in rows:
        text=' | '.join(str(v or '') for v in row.values());nt=_norm(text)
        product=next((t for t in terms if t in nt),None)
        if not product:continue
        value=_num(row.get('V'))
        if value is None:continue
        out.append({'product':product.title(),'period':_period(row),'value':value,'unit':_unit(row),'measure':_measure(row)})
    return {'ok':True,'source':'IBGE/SIDRA — PEVS tabela 291','products':out[:40],'note':'Produção florestal municipal; não é atribuída automaticamente ao imóvel.'}


async def query_plant_extraction(car_code:str):
    rows,err=await _sidra_table(289,car_code,1)
    if err:return {**err,'dataset':'PEVS — extração vegetal'}
    out=[]
    for row in rows:
        value=_num(row.get('V'))
        if value is None or value==0:continue
        text=[str(v or '').strip() for v in row.values() if str(v or '').strip()]
        # Keep the descriptive category without exposing the entire raw SIDRA row.
        category=next((x for x in text if any(t in _norm(x) for t in ('acai','erva-mate','castanha','babacu','carnauba','pequi','pinhão','piaçava','latex','palmito','madeira','lenha','carvao'))),None)
        if not category:continue
        out.append({'product':category,'period':_period(row),'value':value,'unit':_unit(row),'measure':_measure(row)})
    return {'ok':True,'source':'IBGE/SIDRA — PEVS tabela 289','products':out[:30],'note':'Extração vegetal municipal; não comprova exploração dentro do imóvel.'}


async def query_aquaculture(car_code:str):
    rows,err=await _sidra_table(3940,car_code,2)
    if err:return {**err,'dataset':'PPM — aquicultura'}
    known=('tilapia','tambaqui','tambacu','tambatinga','pacu','patinga','carpa','truta','pirarucu','camarao','ostras','vieiras','mexilhoes','alevinos','curimata','dourado','lambari','matrinxa','pintado','surubim','tucunaré','tucunare','rã','jacare')
    out=[]
    for row in rows:
        text=' | '.join(str(v or '') for v in row.values());nt=_norm(text)
        product=next((x for x in known if _norm(x) in nt),None)
        if not product:continue
        value=_num(row.get('V'))
        if value is None:continue
        out.append({'product':product.title(),'period':_period(row),'value':value,'unit':_unit(row),'measure':_measure(row)})
    return {'ok':True,'source':'IBGE/SIDRA — PPM tabela 3940','products':out[:50],'note':'Aquicultura municipal/regional; não indica criação dentro da fazenda sem evidência específica do imóvel.'}


async def build_territorial_production(car_code:str):
    ppm_task=query_ppm(car_code)
    animal_task=query_animal_products(car_code)
    silv_task=query_silviculture_area(car_code)
    forest_task=query_forestry_products(car_code)
    extract_task=query_plant_extraction(car_code)
    aqua_task=query_aquaculture(car_code)
    vals=await asyncio.gather(ppm_task,animal_task,silv_task,forest_task,extract_task,aqua_task,return_exceptions=True)
    keys=('livestock','animal_products','silviculture','forestry_products','plant_extraction','aquaculture')
    out={}
    for key,val in zip(keys,vals):
        out[key]={'ok':False,'detail':f'{type(val).__name__}:{str(val)[:180]}'} if isinstance(val,Exception) else val
    out['ok']=any(isinstance(v,dict) and v.get('ok') for v in out.values())
    out['source']='IBGE/SIDRA — PPM + PEVS'
    out['interpretation']='Panorama produtivo territorial. Dados municipais mostram o contexto econômico da região e nunca são apresentados como atividade existente dentro da propriedade sem evidência espacial específica.'
    return out


print('RX_TERRITORIAL_PRODUCTION_V33=forestry_eucalyptus_aquaculture_other_animals',flush=True)

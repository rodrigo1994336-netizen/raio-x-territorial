from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

SIDRA='https://apisidra.ibge.gov.br/values'


def _norm(v:Any)->str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',s).strip().lower()


def municipality_code_from_car(code:str)->str|None:
    m=re.match(r'^[A-Z]{2}-(\d{7})-',str(code or '').strip().upper())
    return m.group(1) if m else None


def _num(v):
    try:return float(str(v).replace('.','').replace(',','.'))
    except Exception:return None


def _year(row):
    for v in row.values():
        if re.fullmatch(r'19\d{2}|20\d{2}',str(v or '').strip()):return str(v)
    return None


def _product(row):
    candidates=[]
    for k,v in row.items():
        nv=_norm(v);lk=_norm(k)
        if not nv or len(nv)>90:continue
        if any(w in lk for w in ('produto','lavoura','cultura')):return str(v)
        if any(x in nv for x in ('soja','milho','feijao','arroz','sorgo','algodao','trigo','cana','mandioca','batata','amendoim','girassol','tomate','cafe','banana','laranja')):candidates.append(str(v))
    return candidates[0] if candidates else None


def _measure(row):
    # SIDRA descriptive rows normally expose variable in D2N/V names. Keep generic so API changes fail visibly instead of inventing data.
    name=None;unit=None
    for k,v in row.items():
        nv=_norm(v);lk=_norm(k)
        if any(x in nv for x in ('area plantada','area colhida','quantidade produzida','rendimento medio','valor da producao')):name=str(v)
        if 'unidade' in lk or nv in ('hectares','hectare','toneladas','quilogramas por hectare','mil reais'):unit=str(v)
    return name,unit


async def _table(table:int,mun:str):
    # all variables + all products, most recent published period. The result stays municipal and is not attributed to the farm.
    url=f'{SIDRA}/t/{table}/n6/{mun}/v/allxp/p/last%201/c81/all'
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.24-safras'}) as c:
            r=await c.get(url);r.raise_for_status();rows=r.json()
        if not isinstance(rows,list) or len(rows)<2:return {'ok':False,'detail':'empty_response','table':table}
        out=[]
        for row in rows[1:]:
            value=_num(row.get('V'))
            prod=_product(row);measure,unit=_measure(row);year=_year(row)
            if value is None or not prod:continue
            out.append({'product':prod,'measure':measure or 'Indicador agrícola','value':value,'unit':unit,'year':year})
        return {'ok':True,'table':table,'rows':out}
    except Exception as e:return {'ok':False,'table':table,'detail':f'{type(e).__name__}:{str(e)[:240]}'}


def _summarize(rows:list[dict]):
    by={}
    for r in rows:
        p=r.get('product');
        if not p:continue
        item=by.setdefault(p,{'product':p,'year':r.get('year'),'metrics':[]})
        item['metrics'].append({'measure':r.get('measure'),'value':r.get('value'),'unit':r.get('unit')})
    return list(by.values())[:40]


async def query_safras(car_code:str):
    mun=municipality_code_from_car(car_code)
    if not mun:return {'ok':False,'source':'IBGE/SIDRA — PAM','detail':'municipality_code_not_available'}
    import asyncio
    temp,perm=await asyncio.gather(_table(1612,mun),_table(1613,mun))
    rows=[]
    if temp.get('ok'):rows.extend(temp.get('rows') or [])
    if perm.get('ok'):rows.extend(perm.get('rows') or [])
    return {
      'ok':bool(rows),'source':'IBGE/SIDRA — Produção Agrícola Municipal (PAM)','municipality_code':mun,
      'products':_summarize(rows),'temporary_table':temp,'permanent_table':perm,
      'conab':{'source':'CONAB — Acompanhamento da Safra Brasileira','state':'official_context','note':'A CONAB publica levantamentos mensais de grãos/fibras e periódicos de café e cana. A leitura municipal abaixo usa IBGE/PAM; não é produção medida dentro do imóvel.'},
      'note':'Dados municipais de área, produção e rendimento. Servem para contexto produtivo regional e não comprovam qual cultura existe no imóvel.'
    }

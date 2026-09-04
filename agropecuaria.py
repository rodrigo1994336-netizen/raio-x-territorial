from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import unicodedata
from typing import Any

import httpx

SIDRA_PPM_TABLE=3939
SIDRA_API='https://apisidra.ibge.gov.br/values'
SIF_CSV='https://dados.agricultura.gov.br/dataset/062166e3-b515-4274-8e7d-68aadd64b820/resource/97277e92-264a-4dc0-9aea-f87b8ea93798/download/sigsifestabelecimentosregistradosnosif.csv'


def _norm(v: Any) -> str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',s).strip().lower()


def municipality_code_from_car(car_code:str) -> str|None:
    m=re.match(r'^[A-Z]{2}-(\d{7})-',(car_code or '').strip().upper())
    return m.group(1) if m else None


def _num(v):
    try:return float(str(v).replace('.','').replace(',','.'))
    except Exception:return None


def _safe_text(v,max_len=180):
    s=str(v or '').strip()
    return s[:max_len] if s else None


async def query_ppm(car_code:str) -> dict[str,Any]:
    mun=municipality_code_from_car(car_code)
    if not mun:
        return {'ok':False,'source':'IBGE / SIDRA / PPM','detail':'municipality_code_not_available'}
    # Table 3939 = Efetivo dos rebanhos por tipo. Request all livestock classes for
    # the last two periods so the UI can show current level and direction without
    # hardcoding category IDs.
    url=f'{SIDRA_API}/t/{SIDRA_PPM_TABLE}/n6/{mun}/v/allxp/p/last%202/c79/all'
    try:
        async with httpx.AsyncClient(timeout=30,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.22'}) as c:
            r=await c.get(url)
            r.raise_for_status()
            rows=r.json()
        if not isinstance(rows,list) or len(rows)<2:
            return {'ok':False,'source':'IBGE / SIDRA / PPM','detail':'empty_response','municipality_code':mun}
        # SIDRA responses contain a metadata/header row followed by observations.
        data=[]
        for row in rows[1:]:
            herd_name=None;period=None;value=None;unit=None;municipality=None
            for k,v in row.items():
                lk=str(k).lower(); nv=_norm(v)
                if herd_name is None and any(x in nv for x in ('bovino','bubalino','equino','suino','caprino','ovino','galinaceo','galinha','codorna')):
                    herd_name=str(v)
                if period is None and re.fullmatch(r'20\d{2}|19\d{2}',str(v or '').strip()): period=str(v)
                if municipality is None and ('municip' in lk or lk in ('d1n','d2n','d3n')) and isinstance(v,str) and len(v)>2: municipality=v
                if unit is None and ('unidade' in lk or nv in ('cabecas','cabeças')): unit=str(v)
            # SIDRA convention normally puts measure in V.
            value=_num(row.get('V'))
            if herd_name and value is not None:
                data.append({'herd':herd_name,'period':period,'value':value,'unit':unit or 'cabeças'})
        # keep most recent two records per herd
        groups={}
        for x in data:
            groups.setdefault(_norm(x['herd']),[]).append(x)
        series=[]
        for _,items in groups.items():
            items=sorted(items,key=lambda x:x.get('period') or '')[-2:]
            latest=items[-1]; previous=items[-2] if len(items)>1 else None
            delta_pct=None
            if previous and previous.get('value') not in (None,0):
                delta_pct=round((latest['value']-previous['value'])/previous['value']*100,2)
            series.append({'herd':latest['herd'],'period':latest.get('period'),'value':latest['value'],'previous_period':previous.get('period') if previous else None,'previous_value':previous.get('value') if previous else None,'delta_pct':delta_pct})
        return {'ok':True,'source':'IBGE / SIDRA — Pesquisa da Pecuária Municipal (Tabela 3939)','municipality_code':mun,'series':series,'note':'Dados municipais/regionais; não representam o rebanho existente dentro do imóvel.'}
    except Exception as e:
        return {'ok':False,'source':'IBGE / SIDRA / PPM','municipality_code':mun,'detail':f'{type(e).__name__}:{str(e)[:260]}'}


async def query_sif_establishments(municipality:str|None,uf:str|None,limit:int=30) -> dict[str,Any]:
    target_m=_norm(municipality); target_uf=_norm(uf)
    try:
        async with httpx.AsyncClient(timeout=40,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.22'}) as c:
            r=await c.get(SIF_CSV)
            r.raise_for_status()
        raw=r.content
        text=None
        for enc in ('utf-8-sig','latin-1','cp1252'):
            try:text=raw.decode(enc);break
            except Exception:pass
        if text is None: raise ValueError('csv_decode_failed')
        sample=text[:12000]
        try: dialect=csv.Sniffer().sniff(sample,delimiters=';,\t|')
        except Exception: dialect=csv.excel
        reader=csv.DictReader(io.StringIO(text),dialect=dialect)
        hits=[]
        for row in reader:
            flat=' | '.join(str(v or '') for v in row.values())
            nf=_norm(flat)
            if target_m and target_m not in nf: continue
            if target_uf and target_uf not in nf: continue
            kind=[]
            for label,terms in {
                'frigorífico/abate':('abate','matadouro','frigorifico','frigorífico'),
                'laticínios':('leite','laticinio','laticínio','queijo'),
                'carne/produtos cárneos':('carne','carnes','produtos carneos','produtos cárneos'),
            }.items():
                if any(_norm(t) in nf for t in terms):kind.append(label)
            safe={}
            for k,v in row.items():
                if v in (None,''):continue
                lk=_norm(k)
                if any(x in lk for x in ('sif','razao','razão','nome','municip','uf','categoria','classe','atividade','produto','endereco','endereço')):
                    safe[str(k)]=_safe_text(v)
                if len(safe)>=12:break
            hits.append({'categories':kind or ['estabelecimento SIF'],'fields':safe})
            if len(hits)>=max(1,min(limit,100)):break
        return {'ok':True,'source':'MAPA — PGA/SIGSIF — Estabelecimentos Registrados no SIF','municipality':municipality,'uf':uf,'count':len(hits),'establishments':hits,'note':'Filtro municipal/UF. Distância exata só é exibida quando a fonte fornece coordenadas verificáveis.'}
    except Exception as e:
        return {'ok':False,'source':'MAPA / SIGSIF','municipality':municipality,'uf':uf,'detail':f'{type(e).__name__}:{str(e)[:260]}'}


def _ide_classes(result:dict,key:str):
    ide=(result.get('ide_layers') or {}).get(key) or {}
    samples=ide.get('samples') or []
    vals=[]
    for s in samples[:20]:
        props=s.get('properties') if isinstance(s,dict) else None
        if props:
            txt='; '.join(f'{k}: {v}' for k,v in list(props.items())[:8] if v not in (None,''))
            if txt:vals.append(txt[:300])
    return vals


def property_livestock_screening(result:dict) -> dict[str,Any]:
    water=result.get('water_mg') or {}; climate=result.get('climate_nasa') or {}; piv=result.get('pivots_ana') or {}
    terrain=(result.get('ide_layers') or {}).get('declividade') or {}
    soil=(result.get('ide_layers') or {}).get('solo') or {}
    aptitude=(result.get('ide_layers') or {}).get('aptidao') or {}
    checks=[]
    checks.append({'factor':'Água outorgada/próxima','status':'consultada' if water.get('ok') else 'indisponível','value':{'inside':water.get('inside_count'),'near':water.get('near_count')}})
    checks.append({'factor':'Clima recente','status':'consultada' if climate.get('ok') else 'indisponível','value':{'rain_30d_mm':climate.get('rain_sum_mm'),'temp_avg_c':climate.get('temp_avg_c')}})
    checks.append({'factor':'Pivôs','status':'consultada' if piv.get('ok') else 'indisponível','value':{'inside':piv.get('intersection_count'),'near':piv.get('near_count')}})
    checks.append({'factor':'Solo','status':'consultada' if soil.get('ok') else ('parcial' if soil else 'indisponível'),'value':_ide_classes(result,'solo')[:3]})
    checks.append({'factor':'Aptidão agrícola','status':'consultada' if aptitude.get('ok') else ('parcial' if aptitude else 'indisponível'),'value':_ide_classes(result,'aptidao')[:3]})
    checks.append({'factor':'Declividade','status':'consultada' if terrain.get('ok') else ('parcial' if terrain else 'indisponível'),'value':_ide_classes(result,'declividade')[:3]})
    return {
        'checks':checks,
        'carrying_capacity':None,
        'carrying_capacity_note':'Lotação animal (UA/ha) não é estimada sem dados de forragem, manejo, estação, suplementação e validação agronômica. O Raio-X não inventa capacidade de suporte.',
    }


async def build_agro_profile(result:dict,car_code:str) -> dict[str,Any]:
    props=(result.get('car') or {}).get('properties') or {}
    municipality=props.get('municipio');uf=props.get('uf')
    ppm,sif=await asyncio.gather(query_ppm(car_code),query_sif_establishments(municipality,uf))
    return {
        'ok':bool(ppm.get('ok') or sif.get('ok')),
        'source':'IBGE/PPM + MAPA/SIGSIF + fontes territoriais já consultadas pelo Raio-X',
        'municipality':municipality,'uf':uf,
        'livestock_municipal':ppm,
        'sif_chain':sif,
        'property_screening':property_livestock_screening(result),
        'pasture':{
            'source':'MapBiomas Pastagem — Coleção 11',
            'state':'worker/import prepared',
            'planned_metrics':['pasture_area_ha','pasture_share_pct','vigor_low_pct','vigor_medium_pct','vigor_high_pct','vigor_trend_2000_2025'],
            'note':'Os dados são públicos/abertos; o processamento raster por polígono precisa rodar no worker para retornar métricas reais da propriedade.'
        },
        'interpretation':'Triagem agropecuária. Dados municipais não são atribuídos automaticamente ao imóvel e não substituem vistoria, análise de pastagem, zootecnia ou projeto produtivo.'
    }

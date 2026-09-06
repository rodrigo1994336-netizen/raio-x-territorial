from __future__ import annotations

import gzip
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from deploy_app import _curl

ROOT=Path(__file__).resolve().parent
INDEX_ROOT=ROOT/'data/cafir/name_shards/MG'
SNAPSHOT='D60901'
GENERIC=('FAZENDA','SITIO','SÍTIO','CHACARA','CHÁCARA','ESTANCIA','ESTÂNCIA','RANCHO','GLEBA','PROPRIEDADE','IMOVEL RURAL','IMÓVEL RURAL','AREA RURAL','ÁREA RURAL')
SIGEF=(
 ('PUBLICO','https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10/query'),
 ('PRIVADO','https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_privado_a/FeatureServer/9/query'),
)


def _norm(v:Any)->str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    s=re.sub(r'[^A-Z0-9 ]+',' ',s.upper())
    return ' '.join(s.split())


def _alias(v:Any)->str:
    s=_norm(v);changed=True
    while changed:
        changed=False
        for g in GENERIC:
            n=_norm(g)
            if s==n:return s
            if s.startswith(n+' '):s=s[len(n):].strip();changed=True;break
    return s


def _shard(term:str)->str:
    c=(term[:1] or '_').upper()
    return c if c.isalnum() else '_'


@lru_cache(maxsize=3)
def _load_shard(key:str)->tuple[tuple[Any,...],...]:
    p=INDEX_ROOT/f'{key}.jsonl.gz'
    if not p.exists():return ()
    rows=[]
    with gzip.open(p,'rt',encoding='utf-8') as f:
        for line in f:
            try:rows.append(tuple(json.loads(line)))
            except Exception:continue
    return tuple(rows)


def _score(alias:str,term:str)->tuple[int,int,str]:
    if alias==term:return (0,len(alias),alias)
    if alias.startswith(term):return (1,len(alias),alias)
    if term in alias:return (2,len(alias),alias)
    toks=term.split()
    if toks and all(t in alias for t in toks):return (3,len(alias),alias)
    return (99,len(alias),alias)


def search_sync(q:str,uf:str='MG',municipality:str|None=None,limit:int=30)->dict[str,Any]:
    code=(uf or '').strip().upper();term=_alias(q);cap=max(1,min(int(limit),60))
    if code!='MG':
        return {'ok':True,'indexed':False,'items':[],'count':0,'uf':code,'source':'CAFIR/Receita Federal','snapshot':SNAPSHOT,'detail':'Índice direto CAFIR desta etapa está materializado para MG; arquitetura de shards é expansível por UF.'}
    if not term:return {'ok':True,'indexed':True,'items':[],'count':0,'uf':'MG','source':'CAFIR/Receita Federal','snapshot':SNAPSHOT,'detail':'Informe uma denominação pesquisável.'}
    wanted=_norm(municipality) if municipality else ''
    hits=[]
    for r in _load_shard(_shard(term)):
        if len(r)<6:continue
        a,name,mun,area_tenth,incra,nirf=r[:6]
        if wanted and mun!=wanted:continue
        sc=_score(str(a),term)
        if sc[0]>=99:continue
        area_ha=(float(area_tenth)/10.0) if area_tenth is not None else None
        hits.append((sc,{
          'type':'cafir','display_kind':'CAFIR_RECORD','name':name,'name_normalized_alias':a,
          'municipality':mun.title() if mun else None,'municipality_normalized':mun,'uf':'MG','area_ha':area_ha,
          'incra_code':incra or None,'nirf':nirf or None,'source':'Receita Federal / CAFIR — compartilhamento público oficial',
          'snapshot':SNAPSHOT,'car_code':None,'car_link_status':'NOT_VALIDATED','panel_name_eligible':False,
          'note':'Registro cadastral CAFIR localizado pelo nome. Isso não atribui automaticamente esta denominação a nenhum polígono CAR.'
        }))
    hits.sort(key=lambda x:(x[0],str(x[1].get('name') or ''),str(x[1].get('municipality') or '')))
    items=[x[1] for x in hits[:cap]]
    return {'ok':True,'indexed':True,'items':items,'count':len(items),'candidate_count':len(hits),'uf':'MG','source':'Receita Federal / CAFIR — compartilhamento público oficial','snapshot':SNAPSHOT,'query_alias':term,'truncated':len(hits)>cap}


def locate_incra_sync(incra_code:str)->dict[str,Any]:
    incra=''.join(ch for ch in str(incra_code or '') if ch.isdigit())
    if not incra:return {'ok':False,'detail':'invalid_incra_code','items':[]}
    items=[];errors=[];seen=set()
    escaped=incra.replace("'","''")
    for source,base in SIGEF:
        params={'f':'geojson','where':f"codigo_imo='{escaped}'",'outFields':'objectid,parcela_co,codigo_imo,nome_area,registro_m,municipio_,uf_id,status,situacao_i','returnGeometry':'true','outSR':'4674','resultRecordCount':'20'}
        raw=_curl(base+'?'+urlencode(params),True)
        if not raw.get('ok'):
            errors.append(f"{source}:{raw.get('detail') or raw.get('preview')}");continue
        for f in (raw.get('json') or {}).get('features') or []:
            p=f.get('properties') or {};key=(source,p.get('objectid'),p.get('parcela_co'))
            if key in seen:continue
            seen.add(key)
            try:
                from shapely.geometry import shape
                g=shape(f.get('geometry'));c=g.representative_point();center={'lat':float(c.y),'lon':float(c.x)}
            except Exception:center=None
            items.append({'source':f'SIGEF/INCRA {source.lower()} — espelho público IBAMA/PAMGIA','incra_code':p.get('codigo_imo'),'parcel_code':p.get('parcela_co'),'sigef_name':p.get('nome_area'),'registry':p.get('registro_m'),'status':p.get('status') or p.get('situacao_i'),'center':center,'geometry':f.get('geometry'),'display_kind':'INCRA_CERTIFIED_PARCEL','car_code':None,'panel_name_eligible':False})
    if not items and errors:return {'ok':False,'detail':'SIGEF unavailable: '+' | '.join(errors)[:500],'items':[]}
    return {'ok':True,'items':items,'count':len(items),'incra_code':incra,'source':'SIGEF/INCRA','car_link_status':'NOT_VALIDATED','note':'A parcela certificada pode ser exibida no mapa, mas não é convertida em CAR nem herda automaticamente o nome CAFIR.'}


print('RX_CAFIR_NAME_SEARCH_V44=direct_inverse_name_search_no_car_name_inheritance',flush=True)

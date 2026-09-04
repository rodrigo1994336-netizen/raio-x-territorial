from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from functools import lru_cache

WFS='https://geoserver.meioambiente.mg.gov.br/ows'


def _local(tag:str)->str:
    return tag.rsplit('}',1)[-1]


def _curl_text(url:str,max_time:int=70):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.17-ide-catalog',url],capture_output=True,timeout=max_time+10)
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:400]}
    return {'ok':True,'text':p.stdout.decode('utf-8','ignore'),'bytes':len(p.stdout)}


@lru_cache(maxsize=1)
def fetch_catalog():
    r=_curl_text(WFS+'?service=WFS&version=2.0.0&request=GetCapabilities')
    if not r.get('ok'): return r
    try: root=ET.fromstring(r['text'])
    except Exception as e: return {'ok':False,'detail':f'XML:{e}'}
    rows=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType': continue
        row={'name':None,'title':None,'abstract':None,'default_crs':None}
        for ch in ft:
            key=_local(ch.tag)
            if key=='Name' and ch.text: row['name']=ch.text.strip()
            elif key=='Title' and ch.text: row['title']=ch.text.strip()
            elif key=='Abstract' and ch.text: row['abstract']=ch.text.strip()
            elif key in ('DefaultCRS','DefaultSRS') and ch.text: row['default_crs']=ch.text.strip()
        if row['name']: rows.append(row)
    return {'ok':True,'feature_type_count':len(rows),'layers':rows}


def search_catalog(terms:list[str],limit:int=50):
    cat=fetch_catalog()
    if not cat.get('ok'): return cat
    terms=[t.lower().strip() for t in terms if t and t.strip()]
    hits=[]
    for r in cat['layers']:
        hay=' '.join(str(r.get(k) or '') for k in ('name','title','abstract')).lower()
        matched=[t for t in terms if t in hay]
        if matched:
            score=sum(8 if t in str(r.get('title') or '').lower() else 4 if t in str(r.get('name') or '').lower() else 1 for t in matched)
            hits.append({**r,'matched':matched,'score':score})
    hits.sort(key=lambda x:(-x['score'],str(x.get('title') or x.get('name'))))
    return {'ok':True,'feature_type_count':cat['feature_type_count'],'terms':terms,'hits':hits[:limit],'hit_count':len(hits)}


def benchmark_targets():
    groups={
        'solo':['mapa de solos','solos de minas','textura do solo','matéria orgânica','materia organica','capacidade de água disponível','capacidade de agua disponivel'],
        'aptidao':['aptidão agrícola','aptidao agricola','potencial de uso conservacionista','aptidão edafoclimática','aptidao edafoclimatica'],
        'car_app':['análise dinamizada - apps','analise dinamizada - apps','recomposição de apps','recomposicao de apps','reservas legais declarados','reserva legal'],
        'uso_solo':['mapbiomas','áreas naturais e usos antrópicos','areas naturais e usos antropicos','uso e cobertura da terra'],
        'relevo':['declividade','altimetria','altitude','hipsometria','relevo'],
        'erosao':['risco à erosão','risco a erosao','vulnerabilidade do solo à erosão','vulnerabilidade do solo a erosao'],
        'transporte':['rodovias estaduais e federais','principais trechos rodoviários','principais trechos rodoviarios','ferrovias de minas','aeródromos','aerodromos'],
    }
    return {k:search_catalog(v,30) for k,v in groups.items()}

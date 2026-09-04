from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from shapely.geometry import shape

SGB_WMS = os.getenv('SGB_WMS_URL', 'https://geoservicos.sgb.gov.br/geoserver/ows')

MINERAL_TERMS = {
    'terras_raras': ('terra rara','terras raras','etr','ree','rare earth'),
    'litio': ('litio','lítio','lithium'),
    'grafita': ('grafita','graphite'),
    'niquel': ('niquel','níquel','nickel'),
    'cobalto': ('cobalto','cobalt'),
    'cobre': ('cobre','copper'),
    'tungstenio': ('tungstenio','tungstênio','tungsten','wolfram'),
    'uranio': ('uranio','urânio','uranium'),
    'fosfato': ('fosfato','phosphate'),
    'potassio': ('potassio','potássio','potash','potassium'),
    'ouro': ('ouro','gold'),
}


def _norm(value: Any) -> str:
    text = str(value or '')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).lower().strip()


def _classify_text(text: str) -> list[str]:
    s = _norm(text)
    hits=[]
    for code, terms in MINERAL_TERMS.items():
        if any(_norm(t) in s for t in terms):
            hits.append(code)
    return hits


def classify_anm(anm_result: dict[str,Any] | None) -> dict[str,Any]:
    exact = ((anm_result or {}).get('exact') or {})
    rows=[]; counts={k:0 for k in MINERAL_TERMS}
    for occ in exact.get('occurrences') or []:
        props=occ.get('properties') or {}
        classes=_classify_text(json.dumps(props,ensure_ascii=False,default=str))
        if not classes: continue
        for c in classes: counts[c]+=1
        rows.append({
            'minerals':classes,
            'area_intersection_ha':occ.get('area_intersection_ha'),
            'properties':props,
        })
    return {
        'process_count':int(exact.get('occurrence_count') or 0),
        'critical_process_count':len(rows),
        'counts':{k:v for k,v in counts.items() if v},
        'occurrences':rows[:50],
    }


def _local(tag: str) -> str:
    return tag.rsplit('}',1)[-1]


def _parse_layers(xml_text: str) -> list[dict[str,Any]]:
    root=ET.fromstring(xml_text)
    out=[]
    def walk(node, inherited_queryable=False):
        if _local(node.tag)!='Layer': return
        queryable = node.attrib.get('queryable') in ('1','true','True') or inherited_queryable
        name=title=None
        for ch in node:
            if _local(ch.tag)=='Name' and ch.text: name=ch.text.strip()
            elif _local(ch.tag)=='Title' and ch.text: title=ch.text.strip()
        if name:
            text=f'{name} {title or ""}'
            classes=_classify_text(text)
            if classes:
                out.append({'name':name,'title':title or name,'minerals':classes,'queryable':queryable})
        for ch in node:
            if _local(ch.tag)=='Layer': walk(ch,queryable)
    for n in root.iter():
        if _local(n.tag)=='Capability':
            for ch in n:
                if _local(ch.tag)=='Layer': walk(ch,False)
            break
    # deduplicate by layer name
    seen=set(); uniq=[]
    for x in out:
        if x['name'] in seen: continue
        seen.add(x['name']); uniq.append(x)
    return uniq


async def _capabilities() -> tuple[list[dict[str,Any]], str|None]:
    params={'service':'WMS','version':'1.1.1','request':'GetCapabilities'}
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.18'}) as c:
            r=await c.get(SGB_WMS,params=params)
            r.raise_for_status()
        return _parse_layers(r.text),None
    except Exception as e:
        return [],f'{type(e).__name__}:{str(e)[:240]}'


async def _get_feature_info(layer: dict[str,Any], geom) -> dict[str,Any]:
    minx,miny,maxx,maxy=geom.bounds
    # avoid zero-width image envelopes
    if maxx-minx < 1e-5: minx-=5e-5; maxx+=5e-5
    if maxy-miny < 1e-5: miny-=5e-5; maxy+=5e-5
    pts=[geom.representative_point(),geom.centroid]
    hits=[]
    async with httpx.AsyncClient(timeout=25,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.18'}) as c:
        for p in pts:
            x=max(0,min(511,round((p.x-minx)/(maxx-minx)*511)))
            y=max(0,min(511,round((maxy-p.y)/(maxy-miny)*511)))
            params={
                'SERVICE':'WMS','VERSION':'1.1.1','REQUEST':'GetFeatureInfo',
                'LAYERS':layer['name'],'QUERY_LAYERS':layer['name'],'STYLES':'',
                'SRS':'EPSG:4326','BBOX':f'{minx},{miny},{maxx},{maxy}',
                'WIDTH':'512','HEIGHT':'512','X':str(x),'Y':str(y),
                'INFO_FORMAT':'application/json','FEATURE_COUNT':'20','FORMAT':'image/png'
            }
            try:
                rr=await c.get(SGB_WMS,params=params)
                if rr.status_code>=400: continue
                data=rr.json()
                fs=data.get('features') or []
                for f in fs:
                    props=f.get('properties') or {}
                    key=json.dumps(props,ensure_ascii=False,sort_keys=True,default=str)
                    if key not in {x['_key'] for x in hits}:
                        hits.append({'_key':key,'properties':props})
            except Exception:
                continue
    return {'layer':layer['name'],'title':layer['title'],'minerals':layer['minerals'],'hit_count':len(hits),'samples':[{'properties':x['properties']} for x in hits[:10]]}


async def query_critical_minerals(car_geometry: dict[str,Any], anm_result: dict[str,Any] | None=None) -> dict[str,Any]:
    anm=classify_anm(anm_result)
    try: geom=shape(car_geometry)
    except Exception as e:
        return {'ok':False,'source':'ANM + SGB/GeoSGB','detail':f'invalid_geometry:{e}','anm':anm}
    layers,err=await _capabilities()
    # Bound work: prefer rare-earth layers, then other critical minerals.
    layers=sorted(layers,key=lambda x:(0 if 'terras_raras' in x['minerals'] else 1,x['title']))[:24]
    queryable=[x for x in layers if x.get('queryable')][:12]
    results=[]
    if queryable:
        vals=await asyncio.gather(*[_get_feature_info(x,geom) for x in queryable],return_exceptions=True)
        for v in vals:
            if isinstance(v,dict): results.append(v)
    sgb_hits=[x for x in results if x.get('hit_count')]
    mineral_codes=sorted(set(sum((x.get('minerals') or [] for x in sgb_hits),[])))
    if anm.get('counts'):
        mineral_codes=sorted(set(mineral_codes)|set(anm['counts']))
    return {
        'ok': err is None,
        'source':'ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB/WMS)',
        'anm':anm,
        'sgb':{
            'capabilities_ok':err is None,
            'candidate_layer_count':len(layers),
            'candidate_layers':layers,
            'queried_layer_count':len(queryable),
            'hit_layers':sgb_hits,
            'detail':err,
        },
        'mineral_codes':mineral_codes,
        'rare_earth_signal': bool(anm.get('counts',{}).get('terras_raras')) or any('terras_raras' in (x.get('minerals') or []) for x in sgb_hits),
        'interpretation':'Triagem de interesse/potencial mineral. Não comprova ocorrência economicamente explotável, recurso, reserva ou direito minerário.',
        'licensing_note':'Dados consultados em serviços públicos oficiais; antes de redistribuir comercialmente camadas brutas do SGB, confirmar os termos específicos do produto/dataset e manter atribuição da fonte.',
    }

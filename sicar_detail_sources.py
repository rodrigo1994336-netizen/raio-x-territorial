from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from typing import Any

from shapely.geometry import shape
from shapely.ops import unary_union
from pyproj import Geod

SICAR='https://geoserver.car.gov.br/geoserver/sicar/ows'
GEOD=Geod(ellps='GRS80')


def _curl(url: str, expect_json=True):
    p=subprocess.run(['curl','-k','-sS','--connect-timeout','12','--max-time','35','-A','Raio-X-Territorial/0.14.9',url],capture_output=True,timeout=40)
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:240]}
    raw=p.stdout
    if not expect_json:
        return {'ok':bool(raw),'text':raw.decode('utf-8','ignore'),'bytes':len(raw)}
    try:
        return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:
        return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:180].decode('utf-8','ignore')}


def _local(tag):
    return tag.rsplit('}',1)[-1]


def _score(name: str):
    s=name.lower(); score=0
    positive={'reserva':100,'legal':40,'app':90,'preserv':70,'veget':80,'nativa':60,'consolid':90,'uso_restrito':70,'restrito':55,'nascente':40,'hidrog':35,'servidao':30,'remanesc':60,'area_rural_consolidada':120}
    negative={'imoveis':-150,'municip':-120,'estado':-120,'tema':-60,'ponto':-20}
    for k,v in positive.items():
        if k in s: score+=v
    for k,v in negative.items():
        if k in s: score+=v
    return score


def _classify(name: str):
    s=name.lower()
    if 'reserva' in s and 'legal' in s: return 'Reserva Legal'
    if 'app' in s or 'preserv' in s: return 'APP'
    if 'consolid' in s: return 'Área consolidada'
    if 'veget' in s or 'remanesc' in s or 'nativa' in s: return 'Vegetação nativa/remanescente'
    if 'uso_restrito' in s or 'restrito' in s: return 'Uso restrito'
    if 'nascente' in s: return 'Nascentes'
    if 'hidrog' in s: return 'Hidrografia'
    return 'Outra camada ambiental CAR'


def _area_ha(geom):
    try:
        if geom is None or geom.is_empty: return 0.0
        return abs(GEOD.geometry_area_perimeter(geom)[0])/10000.0
    except Exception:
        return 0.0


def discover_layers(limit=14):
    cap=_curl(SICAR+'?service=WFS&version=1.0.0&request=GetCapabilities',False)
    if not cap.get('ok'): return {'ok':False,'detail':cap.get('detail')}
    try: root=ET.fromstring(cap['text'])
    except Exception as e: return {'ok':False,'detail':f'XML:{e}'}
    names=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType': continue
        name=None
        for ch in ft:
            if _local(ch.tag)=='Name' and ch.text: name=ch.text.strip(); break
        if name:
            sc=_score(name)
            if sc>0: names.append((sc,name,_classify(name)))
    names=sorted(names,reverse=True)
    return {'ok':True,'layers':[{'score':sc,'name':name,'category':cat} for sc,name,cat in names[:limit]],'total_candidates':len(names)}


def query_sicar_details(car_geometry: dict[str,Any], bbox: list[float], max_layers=10):
    disc=discover_layers(max_layers*2)
    if not disc.get('ok'): return {'ok':False,'discovery':disc,'source':'SICAR detalhado'}
    car=shape(car_geometry); xmin,ymin,xmax,ymax=bbox
    selected=[]; categories_seen={}
    for layer in disc.get('layers') or []:
        cat=layer['category']
        if categories_seen.get(cat,0)>=2: continue
        selected.append(layer); categories_seen[cat]=categories_seen.get(cat,0)+1
        if len(selected)>=max_layers: break
    results=[]; category_geoms={}
    for layer in selected:
        params={'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':layer['name'],'outputFormat':'application/json','srsName':'EPSG:4674','bbox':f'{xmin},{ymin},{xmax},{ymax}','maxFeatures':'2000'}
        res=_curl(SICAR+'?'+urlencode(params),True)
        item={'name':layer['name'],'category':layer['category'],'score':layer['score'],'ok':res.get('ok')}
        if not res.get('ok'):
            item['detail']=res.get('detail') or res.get('preview'); results.append(item); continue
        fs=(res.get('json') or {}).get('features') or []
        intersections=[]
        for f in fs:
            try:
                src=shape(f.get('geometry'))
                if not car.intersects(src): continue
                inter=car.intersection(src)
                if inter.is_empty: continue
                intersections.append(inter)
                category_geoms.setdefault(layer['category'],[]).append(inter)
            except Exception: continue
        union=unary_union(intersections) if intersections else None
        item.update({'feature_count_bbox':len(fs),'exact_count':len(intersections),'area_unique_ha':round(_area_ha(union) if union is not None else 0.0,6)})
        results.append(item)
    summary={}
    for cat, geoms in category_geoms.items():
        union=unary_union(geoms) if geoms else None
        summary[cat]={'occurrence_count':len(geoms),'area_unique_ha':round(_area_ha(union) if union is not None else 0.0,6),'layer_count':len([r for r in results if r.get('category')==cat and r.get('ok')])}
    for r in results:
        summary.setdefault(r['category'],{'occurrence_count':0,'area_unique_ha':0.0,'layer_count':0})
        if r.get('ok') and summary[r['category']]['layer_count']==0:
            summary[r['category']]['layer_count']=1
    return {'ok':True,'source':'SICAR WFS - camadas ambientais detalhadas','discovery_total':disc.get('total_candidates'),'selected_layers':[x['name'] for x in selected],'layers':results,'summary':summary}

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
    s=name.lower()
    score=0
    positive={
        'reserva':100,'legal':40,'app':90,'preserv':70,'veget':80,'nativa':60,
        'consolid':90,'uso_restrito':70,'restrito':55,'nascente':40,'hidrog':35,
        'servidao':30,'remanesc':60,'area_rural_consolidada':120
    }
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
    try:
        root=ET.fromstring(cap['text'])
    except Exception as e:
        return {'ok':False,'detail':f'XML:{e}'}
    names=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType': continue
        name=None
        for ch in ft:
            if _local(ch.tag)=='Name' and ch.text:
                name=ch.text.strip(); break
        if name:
            sc=_score(name)
            if sc>0: names.append((sc,name,_classify(name)))
    names=sorted(names,reverse=True)
    return {'ok':True,'layers':[{'score':sc,'name':name,'category':cat} for sc,name,cat in names[:limit]],'total_candidates':len(names)}


def query_sicar_details(car_geometry: dict[str,Any], bbox: list[float], max_layers=10):
    disc=discover_layers(max_layers*2)
    if not disc.get('ok'): return {'ok':False,'discovery':disc,'source':'SICAR detalhado'}
    car=shape(car_geometry)
    xmin,ymin,xmax,ymax=bbox
    selected=[]
    categories_seen={}
    # Keep a few layers per category, prioritizing score.
    for layer in disc.get('layers') or []:
        cat=layer['category']
        if categories_seen.get(cat,0)>=2: continue
        selected.append(layer); categories_seen[cat]=categories_seen.get(cat,0)+1
        if len(selected)>=max_layers: break
    results=[]
    for layer in selected:
        params={
            'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':layer['name'],
            'outputFormat':'application/json','srsName':'EPSG:4674','bbox':f'{xmin},{ymin},{xmax},{ymax}',
            'maxFeatures':'2000'
        }
        res=_curl(SICAR+'?'+urlencode(params),True)
        item={'name':layer['name'],'category':layer['category'],'score':layer['score'],'ok':res.get('ok')}
        if not res.get('ok'):
            item['detail']=res.get('detail') or res.get('preview'); results.append(item); continue
        fs=(res.get('json') or {}).get('features') or []
        intersections=[]; exact_count=0
        for f in fs:
            try:
                src=shape(f.get('geometry'))
                if not car.intersects(src): continue
                inter=car.intersection(src)
                if inter.is_empty: continue
                intersections.append(inter); exact_count+=1
            except Exception:
                continue
        union=unary_union(intersections) if intersections else None
        area=_area_ha(union) if union is not None else 0.0
        item.update({'feature_count_bbox':len(fs),'exact_count':exact_count,'area_unique_ha':round(area,6)})
        results.append(item)
    # Aggregate same categories without double-counting across layers in this lightweight stage.
    summary={}
    for r in results:
        cat=r['category']; cur=summary.setdefault(cat,{'layers':0,'exact_count':0,'area_ha_max':0.0,'ok_layers':0})
        cur['layers']+=1
        if r.get('ok'): cur['ok_layers']+=1
        cur['exact_count']+=int(r.get('exact_count') or 0)
        cur['area_ha_max']=max(cur['area_ha_max'],float(r.get('area_unique_ha') or 0))
    return {'ok':True,'source':'SICAR WFS - camadas ambientais detalhadas','discovery_total':disc.get('total_candidates'),'layers':results,'summary':summary}

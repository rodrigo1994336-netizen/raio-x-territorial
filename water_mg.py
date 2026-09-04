from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

from pyproj import CRS, Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

WFS='https://geoserver.meioambiente.mg.gov.br/ows'
TARGET_ID='Ide_2103_mg_outorgas_uso_recursos_hidricos_pto'


def _curl(url:str,expect_json=False,max_time=55):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.15-water',url],capture_output=True,timeout=max_time+10)
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    if not expect_json:
        return {'ok':bool(raw),'text':raw.decode('utf-8','ignore'),'bytes':len(raw)}
    try:
        return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:
        return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore'),'bytes':len(raw)}


def _local(tag): return tag.rsplit('}',1)[-1]


def discover_outorga_layer():
    cap=_curl(WFS+'?service=WFS&version=2.0.0&request=GetCapabilities')
    if not cap.get('ok'): return {'ok':False,'detail':cap.get('detail')}
    try: root=ET.fromstring(cap['text'])
    except Exception as e: return {'ok':False,'detail':f'XML:{e}'}
    matches=[]; names=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType': continue
        name=title=None
        for ch in ft:
            if _local(ch.tag)=='Name' and ch.text: name=ch.text.strip()
            elif _local(ch.tag)=='Title' and ch.text: title=ch.text.strip()
        if name:
            names.append(name)
            hay=(name+' '+(title or '')).lower()
            score=0
            if TARGET_ID.lower() in hay: score+=1000
            if 'outorga' in hay: score+=200
            if 'recurso' in hay and 'hidric' in hay: score+=80
            if '2103' in hay: score+=60
            if score: matches.append((score,name,title))
    matches.sort(reverse=True)
    if not matches:
        return {'ok':False,'detail':'target_layer_not_found','feature_type_count':len(names),'sample_names':names[:80]}
    score,name,title=matches[0]
    return {'ok':True,'name':name,'title':title,'score':score,'matches':[{'name':n,'title':t,'score':s} for s,n,t in matches[:8]],'feature_type_count':len(names)}


def _metric(car):
    c=car.centroid
    local=CRS.from_proj4(f'+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs')
    return Transformer.from_crs('EPSG:4674',local,always_xy=True)


def _safe_props(p:dict[str,Any]):
    wanted=('objectid','geocod_4','numpa_4','numport_4','dtpub_4','diavenc_4','mesvenc_4','anovenc_4','statuspa_4','usoinsig_4','tipouso_4','arresthidr','outcol_4','numdac_4','cocursodag','cobacia','muncap','ch_4','bcfed_4')
    return {k:p.get(k) for k in wanted if p.get(k) not in (None,'')}


def query_outorgas_mg(car_geometry:dict[str,Any], bbox:list[float], radius_km:float=5.0):
    disc=discover_outorga_layer()
    if not disc.get('ok'):
        return {'ok':False,'source':'IDE-Sisema/IGAM WFS','discovery':disc}
    car=shape(car_geometry); tr=_metric(car); car_m=transform(tr.transform,car)
    # Expand roughly by 5 km for nearby-water-right context; exact distance is measured in a local metric CRS.
    c=car.centroid; dlat=radius_km/111.0; dlon=radius_km/(111.0*max(0.2,abs(__import__('math').cos(__import__('math').radians(c.y)))))
    xmin,ymin,xmax,ymax=bbox; qb=[xmin-dlon,ymin-dlat,xmax+dlon,ymax+dlat]
    params={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':disc['name'],'srsName':'EPSG:4674','bbox':f'{qb[0]},{qb[1]},{qb[2]},{qb[3]},EPSG:4674','count':'2000','outputFormat':'application/json'}
    res=_curl(WFS+'?'+urlencode(params),True,65)
    if not res.get('ok'):
        return {'ok':False,'source':'IDE-Sisema/IGAM WFS','layer':disc.get('name'),'detail':res.get('detail'),'preview':res.get('preview')}
    data=res['json']; fs=data.get('features') or []
    inside=[]; near=[]
    for f in fs:
        try:
            g=shape(f.get('geometry'))
            # Outorga layer is point, but geometry handling is generic.
            gm=transform(tr.transform,g)
            dist=float(car_m.distance(gm))
            item={'distance_m':round(dist,1),'inside':bool(car.intersects(g)),'properties':_safe_props(f.get('properties') or {})}
            if item['inside']: inside.append(item)
            if dist<=radius_km*1000: near.append(item)
        except Exception: continue
    near.sort(key=lambda x:x['distance_m'])
    return {'ok':True,'source':'IDE-Sisema / IGAM - Outorgas de direito de uso de recursos hídricos','layer':disc.get('name'),'layer_title':disc.get('title'),'feature_count_bbox':len(fs),'inside_count':len(inside),'near_count':len(near),'radius_km':radius_km,'inside':inside[:100],'near':near[:200],'nearest':near[0] if near else None,'discovery':{'feature_type_count':disc.get('feature_type_count'),'matches':disc.get('matches')}}

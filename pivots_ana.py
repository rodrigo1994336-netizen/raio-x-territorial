from __future__ import annotations

import json
import math
import subprocess
from typing import Any
from urllib.parse import urlencode

from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

LAYER='https://portal1.snirh.gov.br/server/rest/services/SFI/PIVOS_2022_SNIRH/MapServer/0'
QUERY=LAYER+'/query'


def _curl_json(url:str,max_time=55):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.16-pivots',url],capture_output=True,timeout=max_time+10)
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    try:
        return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:
        return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore')}


def _metric(car):
    c=car.centroid
    local=CRS.from_proj4(f'+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs')
    return Transformer.from_crs('EPSG:4674',local,always_xy=True)


def _query_bbox(bbox):
    xmin,ymin,xmax,ymax=bbox
    params={
        'f':'geojson','where':'1=1','outFields':'FID,Hectares,PIVO_2019,Polo_Nome,Polo_Tipo,UGRH_Nome,UGRH_ID,MUNIC_CD,MUNIC_NOME,UF,class_pre3',
        'returnGeometry':'true','outSR':'4674','geometryType':'esriGeometryEnvelope','spatialRel':'esriSpatialRelIntersects',
        'geometry':f'{xmin},{ymin},{xmax},{ymax}','inSR':'4674','resultRecordCount':'2000'
    }
    return _curl_json(QUERY+'?'+urlencode(params),65)


def query_pivots_ana(car_geometry:dict[str,Any], bbox:list[float], radius_km:float=5.0):
    car=shape(car_geometry); tr=_metric(car); car_m=transform(tr.transform,car)
    c=car.centroid; dlat=radius_km/111.0; dlon=radius_km/(111.0*max(.2,abs(math.cos(math.radians(c.y)))))
    xmin,ymin,xmax,ymax=bbox; qb=[xmin-dlon,ymin-dlat,xmax+dlon,ymax+dlat]
    res=_query_bbox(qb)
    if not res.get('ok'):
        return {'ok':False,'source':'ANA / SNIRH - Pivôs Centrais 2022','detail':res.get('detail'),'preview':res.get('preview')}
    data=res.get('json') or {}
    if data.get('error'):
        return {'ok':False,'source':'ANA / SNIRH - Pivôs Centrais 2022','detail':str(data.get('error'))[:500]}
    fs=data.get('features') or []
    exact=[]; near=[]; intersections=[]
    for f in fs:
        try:
            g=shape(f.get('geometry'))
            gm=transform(tr.transform,g)
            dist=float(car_m.distance(gm))
            props=f.get('properties') or {}
            item={
                'fid':props.get('FID'),'mapped_area_ha':props.get('Hectares'),'distance_m':round(dist,1),
                'municipality':props.get('MUNIC_NOME'),'uf':props.get('UF'),'irrigation_pole':props.get('Polo_Nome'),
                'pole_type':props.get('Polo_Tipo'),'ugrh':props.get('UGRH_Nome'),'crop_dynamics_2021_22':props.get('class_pre3')
            }
            if car.intersects(g):
                inter=car.intersection(g)
                if not inter.is_empty:
                    im=transform(tr.transform,inter)
                    item['intersection_area_ha']=round(float(im.area)/10000.0,6)
                    exact.append(item); intersections.append(inter)
            if dist<=radius_km*1000: near.append(item)
        except Exception:
            continue
    near.sort(key=lambda x:x.get('distance_m',10**12))
    union=unary_union(intersections) if intersections else None
    union_m=transform(tr.transform,union) if union is not None and not union.is_empty else None
    unique_ha=round(float(union_m.area)/10000.0,6) if union_m is not None else 0.0
    return {
        'ok':True,'source':'ANA / SNIRH - Mapeamento Atualizado da Agricultura Irrigada por Pivôs Centrais no Brasil (2022)',
        'layer':LAYER,'reference_year':2022,'feature_count_bbox':len(fs),'intersection_count':len(exact),
        'intersection_area_unique_ha':unique_ha,'near_count':len(near),'radius_km':radius_km,
        'intersections':exact[:100],'near':near[:200],'nearest':near[0] if near else None,
    }

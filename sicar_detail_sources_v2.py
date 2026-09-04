from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from typing import Any

from shapely.geometry import shape
from shapely.ops import unary_union
from pyproj import Geod

SICAR='https://geoserver.car.gov.br/geoserver/ows'
GEOD=Geod(ellps='GRS80')

# Public SICAR validated layers documented/used by public CAR GIS clients.
# Area consolidada is intentionally NOT hard-coded until the live capability
# advertises a matching layer name.
EXPLICIT=[
    ('sicar:CAR_VALIDADO_APP','APP'),
    ('sicar:CAR_VALIDADO_RESERVA_LEGAL','Reserva Legal'),
    ('sicar:CAR_VALIDADO_USO_RESTRITO','Uso restrito'),
    ('sicar:CAR_VALIDADO_VEG_NATIVA','Vegetação nativa/remanescente'),
]


def _curl(url:str,expect_json=True):
    try:
        p=subprocess.run([
            'curl','-k','-sS','--fail','--retry','2','--retry-delay','1',
            '--connect-timeout','12','--max-time','45','-A','Raio-X-Territorial/SICAR-v2',url
        ],capture_output=True,timeout=52)
    except Exception as e:
        return {'ok':False,'detail':f'{type(e).__name__}:{str(e)[:220]}'}
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:260]}
    raw=p.stdout
    if not expect_json:
        return {'ok':bool(raw),'text':raw.decode('utf-8','ignore'),'bytes':len(raw)}
    try:return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:220].decode('utf-8','ignore')}


def _local(tag):return tag.rsplit('}',1)[-1]


def _area_ha(geom):
    try:
        if geom is None or geom.is_empty:return 0.0
        return abs(GEOD.geometry_area_perimeter(geom)[0])/10000.0
    except Exception:return 0.0


def _capability_names():
    cap=_curl(SICAR+'?service=WFS&version=1.0.0&request=GetCapabilities',False)
    if not cap.get('ok'):return {'ok':False,'detail':cap.get('detail'),'names':[]}
    try:root=ET.fromstring(cap['text'])
    except Exception as e:return {'ok':False,'detail':f'XML:{e}','names':[]}
    names=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType':continue
        for ch in ft:
            if _local(ch.tag)=='Name' and ch.text:
                names.append(ch.text.strip());break
    return {'ok':True,'names':names,'count':len(names)}


def _dynamic_layers(names:list[str]):
    out=[]
    for name in names:
        s=name.lower()
        cat=None
        if 'consolid' in s:cat='Área consolidada'
        elif 'nascente' in s:cat='Nascentes'
        elif 'hidrog' in s or 'curso_dagua' in s or 'curso_d_agua' in s:cat='Hidrografia'
        if cat and not any(x[0]==name for x in out):out.append((name,cat))
    return out[:8]


def _feature_car_code(props:dict[str,Any]):
    for key,value in props.items():
        lk=str(key).lower().replace('-','_')
        if lk in {'cod_imovel','codigo_imovel','cod_imov','codigo_car','cod_car'} or ('cod' in lk and 'imovel' in lk):
            if value not in (None,''):return str(value).strip().upper()
    return None


def _query_layer(type_name:str,category:str,car,bbox,car_code:str|None=None):
    xmin,ymin,xmax,ymax=bbox
    params={
        'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':type_name,
        'outputFormat':'application/json','srsName':'EPSG:4674',
        'bbox':f'{xmin},{ymin},{xmax},{ymax}','maxFeatures':'2500'
    }
    res=_curl(SICAR+'?'+urlencode(params),True)
    item={'name':type_name,'category':category,'ok':res.get('ok'),'endpoint':SICAR}
    if not res.get('ok'):
        item['detail']=res.get('detail') or res.get('preview');return item,[]
    fs=(res.get('json') or {}).get('features') or []
    intersections=[];matched_code=0;features_with_code=0
    expected=(car_code or '').strip().upper()
    for f in fs:
        try:
            props=f.get('properties') or {};fc=_feature_car_code(props)
            if fc:features_with_code+=1
            if expected and fc and fc!=expected:continue
            if expected and fc==expected:matched_code+=1
            src=shape(f.get('geometry'))
            if src.is_empty or not car.intersects(src):continue
            inter=car.intersection(src)
            if not inter.is_empty:intersections.append(inter)
        except Exception:continue
    union=unary_union(intersections) if intersections else None
    item.update({
        'feature_count_bbox':len(fs),'features_with_car_code':features_with_code,
        'matched_car_code':matched_code,'exact_count':len(intersections),
        'area_unique_ha':round(_area_ha(union) if union is not None else 0.0,6),
        'bytes':res.get('bytes')
    })
    return item,intersections


def query_sicar_details_v2(car_geometry:dict[str,Any],bbox:list[float],max_layers:int=10,car_code:str|None=None):
    try:car=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'SICAR WFS validado v2','detail':f'geometry:{e}'}
    cap=_capability_names();names=cap.get('names') or []
    layers=list(EXPLICIT)
    for pair in _dynamic_layers(names):
        if pair not in layers:layers.append(pair)
    layers=layers[:max_layers]
    results=[];category_geoms={};successful=0
    for type_name,category in layers:
        item,geoms=_query_layer(type_name,category,car,bbox,car_code)
        results.append(item)
        if item.get('ok'):
            successful+=1
            category_geoms.setdefault(category,[]).extend(geoms)
    summary={}
    for _,category in layers:
        geoms=category_geoms.get(category) or []
        union=unary_union(geoms) if geoms else None
        ok_layers=[r for r in results if r.get('category')==category and r.get('ok')]
        summary[category]={
            'consulted':bool(ok_layers),'occurrence_count':len(geoms),
            'area_unique_ha':round(_area_ha(union) if union is not None else 0.0,6),
            'layer_count':len(ok_layers),
            'layers':[r.get('name') for r in ok_layers]
        }
    return {
        'ok':successful>0,'partial':successful<len(layers),'source':'SICAR WFS — camadas validadas explícitas',
        'endpoint':SICAR,'car_code_filter':car_code,'capabilities_ok':cap.get('ok'),
        'feature_type_count':cap.get('count'),'selected_layers':[x[0] for x in layers],
        'successful_layers':successful,'requested_layers':len(layers),'layers':results,'summary':summary,
        'note':'APP, Reserva Legal, Uso Restrito e Vegetação Nativa são consultados em camadas SICAR validadas explícitas. Área consolidada só é consultada se o GetCapabilities ao vivo expuser camada identificável; não é inferida.'
    }


def query_sicar_details(car_geometry:dict[str,Any],bbox:list[float],max_layers:int=10):
    return query_sicar_details_v2(car_geometry,bbox,max_layers,None)


print('RX_SICAR_DETAILS=V2_ROOT_WFS_EXPLICIT_VALIDATED_LAYERS',flush=True)

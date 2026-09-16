from __future__ import annotations

import json
import math
import subprocess

from external_process_lifecycle import ManagedProcessCancelled, run_managed_process
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

import source_layer_guard as layer_guard
from outorga_vazao import safe_grant_props

WFS='https://geoserver.meioambiente.mg.gov.br/ows'

# H1: registry keys used to confirm a live layer before stating "no outorga".
GUARD_KEYS={'igam':'outorgas_igam_mg','ana':'outorgas_ana_mg'}

STATIC_LAYERS={
    'igam':{
        'ok':True,
        'label':'IGAM - Outorgas estaduais',
        'name':'IDE:ide_2103_mg_outorgas_uso_recursos_hidricos_pto',
        'title':'Outorgas de direito de uso de recursos hídricos',
    },
    'ana':{
        'ok':True,
        'label':'ANA - Outorgas federais',
        'name':'IDE:ide_2103_mg_federais_ana_outorgas_pto',
        'title':'Outorgas federais de direito de uso de recursos hídricos (ANA)',
    },
}


def _curl(url:str,expect_json=False,max_time=40):
    try:
        p=run_managed_process(['curl','-sS','--retry','1','--retry-delay','1','--connect-timeout','10','--max-time',str(max_time),'-A','Raio-X-Territorial/0.17-water-direct',url],timeout_seconds=max_time+8)
    except subprocess.TimeoutExpired as e:
        return {'ok':False,'detail':f'TimeoutExpired:{e}'}
    except ManagedProcessCancelled:
        return {'ok':False,'cancelled':True,'detail':'request_cancelled'}
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    if not expect_json:
        return {'ok':bool(raw),'text':raw.decode('utf-8','ignore'),'bytes':len(raw)}
    try:
        return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:
        return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore'),'bytes':len(raw)}


def _metric(car):
    c=car.centroid
    local=CRS.from_proj4(f'+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs')
    return Transformer.from_crs('EPSG:4674',local,always_xy=True)


def _safe_props(p:dict[str,Any]):
    deny=('cpf','cnpj','nome','titular','requerente','usuario','usuário','email','telefone','fone','endereco','endereço','empto','empreend','respons')
    allow=('objectid','numpa','process','proc','port','status','uso','tipo','final','vaz','volume','data','dtpub','venc','bacia','curso','capt','ch_','bcfed','cocurso','cod_','mun','geocod','moduso','unvaz')
    # F2: campos da outorga por lista explícita (vazão, horas/dia, dias, validade, base),
    # fora do corte de 32; o corte antigo derrubava as horas por dia e a data da base.
    out=safe_grant_props(p)
    extra=0
    for k,v in p.items():
        lk=str(k).lower()
        if str(k) in out: continue
        if any(d in lk for d in deny): continue
        if not any(a in lk for a in allow): continue
        if isinstance(v,(dict,list)): continue
        if v in (None,''): continue
        out[str(k)]=v
        extra+=1
        if extra>=32: break
    return out


def _query_layer(layer:dict, car, car_m, tr, qb, radius_km):
    params={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':layer['name'],'srsName':'EPSG:4674','bbox':f'{qb[0]},{qb[1]},{qb[2]},{qb[3]},EPSG:4674','count':'3000','outputFormat':'application/json'}
    res=_curl(WFS+'?'+urlencode(params),True,45)
    if not res.get('ok'):
        return {'ok':False,'label':layer.get('label'),'layer':layer.get('name'),'title':layer.get('title'),'detail':res.get('detail'),'preview':res.get('preview')}
    data=res.get('json') or {}
    if data.get('exceptions') or data.get('ExceptionReport'):
        return {'ok':False,'label':layer.get('label'),'layer':layer.get('name'),'title':layer.get('title'),'detail':str(data)[:500]}
    if not isinstance(data.get('features'),list):
        return {'ok':False,'label':layer.get('label'),'layer':layer.get('name'),'title':layer.get('title'),'detail':'consulta_pendente:features_missing'}
    fs=data.get('features') or []
    matched=data.get('numberMatched')
    if len(fs)>=3000 or (isinstance(matched,int) and matched>len(fs)):
        return {'ok':False,'label':layer.get('label'),'layer':layer.get('name'),'title':layer.get('title'),'detail':'consulta_pendente:truncated'}
    inside=[]; near=[]
    for f in fs:
        try:
            g=shape(f.get('geometry'))
            gm=transform(tr.transform,g)
            dist=float(car_m.distance(gm))
            item={'distance_m':round(dist,1),'inside':bool(car.intersects(g)),'authority':layer.get('label'),'layer':layer.get('name'),'properties':_safe_props(f.get('properties') or {})}
            if item['inside']: inside.append(item)
            if dist<=radius_km*1000: near.append(item)
        except Exception: continue
    near.sort(key=lambda x:x['distance_m'])
    return {'ok':True,'label':layer.get('label'),'layer':layer.get('name'),'title':layer.get('title'),'feature_count_bbox':len(fs),'inside_count':len(inside),'near_count':len(near),'inside':inside,'near':near}


def query_outorgas_mg(car_geometry:dict[str,Any], bbox:list[float], radius_km:float=5.0, uf:str|None=None):
    # H1: IDE-Sisema only publishes Minas Gerais; elsewhere its silence is not "no outorga".
    if uf and str(uf).strip().upper()!='MG':
        return {'ok':False,'source':'IDE-Sisema / IGAM + ANA - Outorgas de direito de uso de recursos hídricos','source_state':'pending',
                'layer_guard':{'reason':'outside_source_coverage_mg'},'detail':'consulta_pendente:outside_source_coverage_mg','radius_km':radius_km}
    car=shape(car_geometry); tr=_metric(car); car_m=transform(tr.transform,car)
    c=car.centroid; dlat=radius_km/111.0; dlon=radius_km/(111.0*max(0.2,abs(math.cos(math.radians(c.y)))))
    xmin,ymin,xmax,ymax=bbox; qb=[xmin-dlon,ymin-dlat,xmax+dlon,ymax+dlat]
    layer_results={}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs={ex.submit(_query_layer,layer,car,car_m,tr,qb,radius_km):key for key,layer in STATIC_LAYERS.items()}
        for fut in as_completed(futs):
            key=futs[fut]
            try: layer_results[key]=fut.result()
            except Exception as e: layer_results[key]={'ok':False,'label':STATIC_LAYERS[key]['label'],'layer':STATIC_LAYERS[key]['name'],'detail':f'{type(e).__name__}:{e}'}
    combined_inside=[]; combined_near=[]; total_bbox=0
    for key in STATIC_LAYERS:
        r=layer_results.get(key) or {'ok':False}
        if r.get('ok'):
            total_bbox+=int(r.get('feature_count_bbox') or 0)
            combined_inside.extend(r.get('inside') or [])
            combined_near.extend(r.get('near') or [])
    combined_near.sort(key=lambda x:x.get('distance_m',10**12))
    # H1: the combined reading is an answer only when every authority answered; an
    # empty envelope also needs each national/state layer to be alive.
    ok_all=all((layer_results.get(k) or {}).get('ok') for k in STATIC_LAYERS)
    guard_reason=None if ok_all else 'layer_query_failed'
    if ok_all and total_bbox==0:
        for key in STATIC_LAYERS:
            verdict=layer_guard.zero_verdict(GUARD_KEYS[key],zero=True)
            if not verdict.get('answer'):
                ok_all=False;guard_reason=f"{key}:{verdict.get('reason')}";break
    return {
        'ok':ok_all,
        'source_state':('answered_hit' if total_bbox else 'answered_clear') if ok_all else 'pending',
        'layer_guard':{'reason':guard_reason or 'layer_alive'},
        'source':'IDE-Sisema / IGAM + ANA - Outorgas de direito de uso de recursos hídricos',
        'layer':'; '.join((layer_results.get(k) or {}).get('layer') for k in STATIC_LAYERS if (layer_results.get(k) or {}).get('ok') and (layer_results.get(k) or {}).get('layer')),
        'feature_count_bbox':total_bbox,
        'inside_count':len(combined_inside),
        'near_count':len(combined_near),
        'radius_km':radius_km,
        'inside':combined_inside[:150],
        'near':combined_near[:300],
        'nearest':combined_near[0] if combined_near else None,
        'layers':{k:{kk:v for kk,v in (layer_results.get(k) or {}).items() if kk not in ('inside','near')} for k in STATIC_LAYERS},
        'discovery':{'mode':'static_verified_layers','layers':STATIC_LAYERS},
    }

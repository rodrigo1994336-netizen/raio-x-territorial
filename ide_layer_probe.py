from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode
from shapely.geometry import shape

WFS='https://geoserver.meioambiente.mg.gov.br/ows'
LAYERS={
 'soil':'IDE:ide_1502_mg_mapa_solos_pol',
 'aptitude':'IDE:ide_1504_mg_solos_aptidao_agricola_pol',
 'slope':'IDE:ide_2401_mg_declividade_pol',
 'erosion':'IDE:ide_2401_mg_risco_potencial_erosao_pol',
 'landcover_centro_norte':'IDE:ide_210603_mg_uso_cobertura_mapcar_area7_pol',
 'rl_recomposition':'IDE:ide_210602_mg_imoveis_recomp_res_legal_declarado_car_pol',
}


def _curl_json(url:str,max_time=60):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.17-layer-probe',url],capture_output=True,timeout=max_time+10)
    if p.returncode: return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    try: return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e: return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore')}


def _safe_props(p:dict):
    out={}
    deny=('cpf','cnpj','nome','propriet','possuidor','email','telefone','fone','endereco','endereço')
    for k,v in (p or {}).items():
        lk=str(k).lower()
        if any(d in lk for d in deny): continue
        if isinstance(v,(dict,list)): continue
        if v in (None,''): continue
        out[str(k)]=v
        if len(out)>=30: break
    return out


def query_layer(layer:str,bbox:list[float],car_geometry:dict,max_features=1000):
    xmin,ymin,xmax,ymax=bbox
    params={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':layer,'srsName':'EPSG:4674','bbox':f'{xmin},{ymin},{xmax},{ymax},EPSG:4674','count':str(max_features),'outputFormat':'application/json'}
    r=_curl_json(WFS+'?'+urlencode(params),65)
    if not r.get('ok'): return {'ok':False,'layer':layer,'detail':r.get('detail'),'preview':r.get('preview')}
    data=r.get('json') or {}
    if data.get('exceptions') or data.get('ExceptionReport'): return {'ok':False,'layer':layer,'detail':str(data)[:500]}
    fs=data.get('features') or []
    car=shape(car_geometry)
    hits=[]
    for f in fs:
        try:
            g=shape(f.get('geometry'))
            if not car.intersects(g): continue
            inter=car.intersection(g)
            if inter.is_empty: continue
            hits.append({'properties':_safe_props(f.get('properties') or {}),'geometry_type':g.geom_type})
        except Exception: continue
    return {'ok':True,'layer':layer,'feature_count_bbox':len(fs),'exact_count':len(hits),'samples':hits[:4]}


def probe_benchmark(car_geometry:dict,bbox:list[float]):
    out={}
    with ThreadPoolExecutor(max_workers=len(LAYERS)) as ex:
        futs={ex.submit(query_layer,layer,bbox,car_geometry):key for key,layer in LAYERS.items()}
        for fut in as_completed(futs):
            key=futs[fut]
            try: out[key]=fut.result()
            except Exception as e: out[key]={'ok':False,'layer':LAYERS[key],'detail':f'{type(e).__name__}:{e}'}
    return {k:out.get(k,{'ok':False,'detail':'no_result'}) for k in LAYERS}

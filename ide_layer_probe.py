from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
from urllib.parse import urlencode
from shapely.geometry import shape
from pyproj import Geod

WFS='https://geoserver.meioambiente.mg.gov.br/ows'
GEOD=Geod(ellps='GRS80')
LAYERS={
 'soil':'IDE:ide_1502_mg_mapa_solos_pol',
 'aptitude':'IDE:ide_1504_mg_solos_aptidao_agricola_pol',
 'erosion':'IDE:ide_2401_mg_risco_potencial_erosao_pol',
 'rl_recomposition':'IDE:ide_210602_mg_imoveis_recomp_res_legal_declarado_car_pol',
}
SUPERSEDED={
 'slope':{'layer':'IDE:ide_2401_mg_declividade_pol','superseded_by':'SRTM ~30 m','reason':'vetor legado pesado; SRTM é calculado diretamente no CAR'},
 'landcover_centro_norte':{'layer':'IDE:ide_210603_mg_uso_cobertura_mapcar_area7_pol','superseded_by':'MapBiomas Coleção 11','reason':'camada regional pesada; MapBiomas nacional substitui esta função'},
}
MG_BBOX=(-51.2,-23.0,-39.7,-14.1)
DEFAULT_FEATURE_LIMIT=80
HEAVY_FEATURE_LIMIT=30


def _area_ha(g):
    try:
        return abs(GEOD.geometry_area_perimeter(g)[0])/10000.0 if g is not None and not g.is_empty else 0.0
    except Exception:
        return 0.0


def _curl_json(url:str,max_time=18):
    try:
        p=subprocess.run(['curl','-sS','--retry','1','--retry-delay','1','--connect-timeout','6','--max-time',str(max_time),'-A','Raio-X-Territorial/0.23-layer-probe',url],capture_output=True,timeout=max_time+4)
    except subprocess.TimeoutExpired as e:
        return {'ok':False,'detail':f'TimeoutExpired:{e}'}
    if p.returncode:return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    if len(raw)>12_000_000:
        return {'ok':False,'detail':f'payload_guard:{len(raw)} bytes','bytes':len(raw),'capped':True}
    try:return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore')}


def _safe_props(p:dict):
    out={}
    deny=('cpf','cnpj','nome','propriet','possuidor','email','telefone','fone','endereco','endereço')
    for k,v in (p or {}).items():
        lk=str(k).lower()
        if any(d in lk for d in deny):continue
        if isinstance(v,(dict,list)):continue
        if v in (None,''):continue
        out[str(k)]=v
        if len(out)>=30:break
    return out


def query_layer(layer:str,bbox:list[float],car_geometry:dict,max_features=None):
    if max_features is None:
        max_features=HEAVY_FEATURE_LIMIT if 'recomp_res_legal' in layer else DEFAULT_FEATURE_LIMIT
    xmin,ymin,xmax,ymax=bbox
    params={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':layer,'srsName':'EPSG:4674','bbox':f'{xmin},{ymin},{xmax},{ymax},EPSG:4674','count':str(max_features),'outputFormat':'application/json'}
    r=_curl_json(WFS+'?'+urlencode(params),18)
    if not r.get('ok'):return {'ok':False,'layer':layer,'detail':r.get('detail'),'preview':r.get('preview'),'bytes':r.get('bytes'),'capped':r.get('capped',False)}
    data=r.get('json') or {}
    if data.get('exceptions') or data.get('ExceptionReport'):return {'ok':False,'layer':layer,'detail':str(data)[:500]}
    fs=data.get('features') or []
    car=shape(car_geometry);car_area=_area_ha(car);hits=[]
    for f in fs:
        try:
            g=shape(f.get('geometry'))
            if not car.intersects(g):continue
            inter=car.intersection(g)
            if inter.is_empty:continue
            ha=_area_ha(inter)
            if ha<=0:continue
            hits.append({'properties':_safe_props(f.get('properties') or {}),'geometry_type':g.geom_type,'intersection_area_ha':round(ha,6),'intersection_pct_car':round((ha/car_area)*100,4) if car_area>0 else None})
        except Exception:continue
    return {'ok':True,'layer':layer,'feature_count_bbox':len(fs),'exact_count':len(hits),'samples':hits[:8],'request_limit':max_features,'truncated_possible':len(fs)>=max_features,'bytes':r.get('bytes'),'car_area_ha_geodesic':round(car_area,6),'intersection_area_sum_ha':round(sum(float(x.get('intersection_area_ha') or 0) for x in hits),6)}


def _intersects_mg(bbox:list[float])->bool:
    try:
        w,s,e,n=map(float,bbox);mw,ms,me,mn=MG_BBOX
        return not (e<mw or w>me or n<ms or s>mn)
    except Exception:return False


def probe_benchmark(car_geometry:dict,bbox:list[float]):
    out={}
    # IDE-Sisema is a Minas Gerais source. Outside MG it is not an error and must
    # not waste latency on irrelevant state layers.
    if not _intersects_mg(bbox):
        for key,layer in LAYERS.items():out[key]={'ok':False,'state':'not_applicable','layer':layer,'detail':'IDE-Sisema é fonte estadual de Minas Gerais; não aplicável a este imóvel.'}
        for key,meta in SUPERSEDED.items():out[key]={'ok':False,'state':'superseded',**meta}
        print('RX_IDE_PROBE=not_applicable_outside_mg',flush=True)
        return out

    # The useful state layers are independent: query them concurrently. This turns
    # the old sum-of-latencies path into roughly the latency of the slowest source.
    with ThreadPoolExecutor(max_workers=len(LAYERS)) as ex:
        jobs={ex.submit(query_layer,layer,bbox,car_geometry):key for key,layer in LAYERS.items()}
        for fut in as_completed(jobs):
            key=jobs[fut];layer=LAYERS[key]
            try:result=fut.result()
            except Exception as e:result={'ok':False,'layer':layer,'detail':f'{type(e).__name__}:{e}'}
            out[key]=result
            print('RX_IDE_LAYER_SINGLE='+json.dumps({'key':key,**result},ensure_ascii=False,default=str),flush=True)

    for key,meta in SUPERSEDED.items():
        out[key]={'ok':False,'state':'superseded',**meta}
        print('RX_IDE_LAYER_SUPERSEDED='+json.dumps({'key':key,**out[key]},ensure_ascii=False),flush=True)
    return out

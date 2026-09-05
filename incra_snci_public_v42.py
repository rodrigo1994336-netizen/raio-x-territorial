from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

from shapely.geometry import shape

BASE='https://acervofundiario.incra.gov.br/i3geo/ogc.php'
TTL=1800
_CAP:dict[str,tuple[float,dict[str,Any]]]={}


def _local(tag:str)->str:return tag.rsplit('}',1)[-1]


def _theme(uf:str,kind:str='privado')->str:
    u=str(uf or '').strip().lower()
    return f'imoveiscertificados_{kind}_{u}'


def _curl(url:str,timeout:int=12)->dict[str,Any]:
    try:
        p=subprocess.run(['curl','-k','-sS','-L','--fail','--retry','0','--connect-timeout','5','--max-time',str(timeout),'-A','Raio-X-Territorial/INCRA-SNCI-v42',url],capture_output=True,timeout=timeout+4)
    except Exception as e:return {'ok':False,'detail':f'{type(e).__name__}:{str(e)[:180]}'}
    if p.returncode:return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:220],'bytes':len(p.stdout)}
    raw=p.stdout
    return {'ok':bool(raw),'bytes':len(raw),'text':raw.decode('utf-8','ignore')}


def capabilities(uf:str,kind:str='privado')->dict[str,Any]:
    key=f'{kind}:{str(uf).upper()}';now=time.monotonic();cached=_CAP.get(key)
    if cached and now-cached[0]<TTL:return dict(cached[1])
    tema=_theme(uf,kind);url=BASE+'?'+urlencode({'tema':tema,'service':'WFS','request':'GetCapabilities'})
    raw=_curl(url,10)
    out={'ok':False,'uf':str(uf).upper(),'theme':tema,'source':'INCRA Acervo Fundiário — WFS oficial','feature_types':[],'public_access':False}
    if not raw.get('ok'):
        out['detail']=raw.get('detail');_CAP[key]=(now,out);return out
    txt=raw.get('text') or ''
    if '<html' in txt.lower() and ('gov.br' in txt.lower() or 'login' in txt.lower()):
        out['detail']='authentication_required';_CAP[key]=(now,out);return out
    try:root=ET.fromstring(txt)
    except Exception as e:
        out['detail']=f'capabilities_xml:{type(e).__name__}:{str(e)[:140]}';out['preview']=txt[:160];_CAP[key]=(now,out);return out
    names=[]
    for ft in root.iter():
        if _local(ft.tag)!='FeatureType':continue
        name=None;title=None
        for ch in ft:
            if _local(ch.tag)=='Name' and ch.text:name=ch.text.strip()
            elif _local(ch.tag)=='Title' and ch.text:title=ch.text.strip()
        if name:names.append({'name':name,'title':title})
    out.update({'ok':bool(names),'public_access':bool(names),'feature_types':names,'count':len(names),'bytes':raw.get('bytes')})
    if not names:out['detail']='no_feature_types'
    _CAP[key]=(now,out);return out


def _pick(props:dict[str,Any],keys:tuple[str,...]):
    low={str(k).lower():v for k,v in props.items()}
    for k in keys:
        v=low.get(k.lower())
        if v not in (None,''):return v
    for k,v in low.items():
        if any(token in k for token in keys) and v not in (None,''):return v
    return None


def _clean(v):
    s=re.sub(r'\s+',' ',str(v or '')).strip()
    return s[:180] if len(s)>=2 else None


def viewport(west:float,south:float,east:float,north:float,uf:str,limit:int=50)->dict[str,Any]:
    cap=capabilities(uf,'privado')
    if not cap.get('ok'):
        return {'ok':False,'items':[],'count':0,'uf':str(uf).upper(),'source':cap.get('source'),'detail':cap.get('detail'),'public_access':cap.get('public_access',False)}
    ft=(cap.get('feature_types') or [{}])[0].get('name')
    if not ft:return {'ok':False,'items':[],'count':0,'detail':'no_feature_type','source':cap.get('source')}
    params={'tema':cap['theme'],'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':ft,'outputFormat':'application/json','srsName':'EPSG:4326','bbox':f'{west},{south},{east},{north},EPSG:4326','maxFeatures':str(max(1,min(int(limit),80)))}
    raw=_curl(BASE+'?'+urlencode(params),13)
    if not raw.get('ok'):return {'ok':False,'items':[],'count':0,'uf':str(uf).upper(),'source':cap.get('source'),'detail':raw.get('detail'),'public_access':True}
    try:data=json.loads(raw.get('text') or '')
    except Exception as e:return {'ok':False,'items':[],'count':0,'source':cap.get('source'),'detail':f'geojson:{type(e).__name__}'}
    items=[];seen=set()
    for f in data.get('features') or []:
        p=f.get('properties') or {};geom=f.get('geometry')
        name=_clean(_pick(p,('nome_area','nome_imovel','denominacao','nome','imovel')))
        registry=_clean(_pick(p,('registro_m','registro','matricula','matrícula','cartorio')))
        system=_clean(_pick(p,('sistema','banco','origem','certificacao','certificação')))
        parcel=_clean(_pick(p,('parcela','parcela_co','codigo','codigo_imo','cod_imovel')))
        if not (name or registry) or not geom:continue
        try:
            c=shape(geom).representative_point();center={'lat':float(c.y),'lon':float(c.x)}
        except Exception:continue
        k=parcel or registry or f"{name}|{center['lat']:.5f}|{center['lon']:.5f}"
        if k in seen:continue
        seen.add(k)
        items.append({'name':name or 'Imóvel certificado INCRA','registry':registry,'system':system,'parcel_code':parcel,'uf':str(uf).upper(),'center':center,'geometry':geom,'source':'INCRA Acervo Fundiário — WFS oficial','properties':{str(k):v for k,v in p.items() if v not in (None,'')}})
    return {'ok':True,'items':items,'count':len(items),'uf':str(uf).upper(),'source':'INCRA Acervo Fundiário — WFS oficial','feature_type':ft,'public_access':True,'note':'Camada oficial de imóveis certificados. O campo de sistema/origem é preservado quando exposto; nenhuma feição é chamada de SNCI sem atributo que sustente essa identificação.'}


def background_probe(uf:str='MG'):
    def run():
        time.sleep(3)
        try:
            out=capabilities(uf)
            print('RX_INCRA_CERTIFIED_PROBE='+json.dumps({k:out.get(k) for k in ('ok','uf','theme','public_access','count','detail')},ensure_ascii=False),flush=True)
        except Exception as e:print(f'RX_INCRA_CERTIFIED_PROBE=error:{type(e).__name__}:{str(e)[:180]}',flush=True)
    threading.Thread(target=run,daemon=True).start()


print('RX_INCRA_CERTIFIED_V42=official_wfs_fail_soft_probe',flush=True)

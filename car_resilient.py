from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import deploy_app

CAR_RE=re.compile(r'^[A-Z]{2}-\d{7}-[A-F0-9]{32}$',re.I)


def _norm(v):
    return str(v or '').strip().upper()


def _build_result(raw, code, strategy):
    if not raw.get('ok'):
        return None
    data=raw.get('json') or {}
    fs=data.get('features') or []
    exact=[]
    for f in fs:
        p=f.get('properties') or {}
        if _norm(p.get('cod_imovel'))==code:
            exact.append(f)
    if not exact:
        return None
    f=exact[0]
    return {
        'ok':True,
        'source':'SICAR',
        'strategy':strategy,
        'feature_count':len(exact),
        'properties':f.get('properties') or {},
        'geometry':f.get('geometry'),
        'bbox':deploy_app._bbox(f.get('geometry')),
        'bytes':raw.get('bytes',0),
    }


def _req(params):
    return deploy_app._curl(deploy_app.SICAR+'?'+urlencode(params),True)


def fetch_car_live_resilient(car_code:str):
    code=_norm(car_code)
    if not CAR_RE.match(code):
        return {'ok':False,'source':'SICAR','not_found':True,'detail':'invalid_car_format'}
    uf=code[:2]
    mun=code[3:10]
    tn=f"sicar:sicar_imoveis_{'DF' if uf=='DF' else uf.lower()}"
    attempts=[]

    strategies=[
        ('wfs1_equal',{
            'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,
            'outputFormat':'application/json','srsName':'EPSG:4674',
            'CQL_FILTER':f"cod_imovel='{code}'",'maxFeatures':'5'}),
        ('wfs1_in',{
            'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,
            'outputFormat':'application/json','srsName':'EPSG:4674',
            'CQL_FILTER':f"cod_imovel IN ('{code}')",'maxFeatures':'5'}),
        ('wfs2_equal',{
            'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':tn,
            'outputFormat':'application/json','srsName':'EPSG:4674',
            'CQL_FILTER':f"cod_imovel='{code}'",'count':'5'}),
        ('wfs1_like_exact',{
            'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,
            'outputFormat':'application/json','srsName':'EPSG:4674',
            'CQL_FILTER':f"cod_imovel LIKE '{code}'",'maxFeatures':'5'}),
    ]

    # OGC XML filter avoids relying on the CQL parser.
    ogc=(
        '<Filter xmlns="http://www.opengis.net/ogc">'
        '<PropertyIsEqualTo><PropertyName>cod_imovel</PropertyName>'
        f'<Literal>{code}</Literal></PropertyIsEqualTo></Filter>'
    )
    strategies.append(('wfs1_ogc_filter',{
        'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,
        'outputFormat':'application/json','srsName':'EPSG:4674','FILTER':ogc,'maxFeatures':'5'}))

    for name,params in strategies:
        try:
            raw=_req(params)
            attempts.append({'strategy':name,'ok':raw.get('ok'),'bytes':raw.get('bytes',0),'detail':raw.get('detail')})
            result=_build_result(raw,code,name)
            if result:
                result['attempts']=attempts
                return result
        except Exception as exc:
            attempts.append({'strategy':name,'ok':False,'detail':f'{type(exc).__name__}:{str(exc)[:160]}'})

    # Last-resort targeted municipality scan. First request only CAR codes to keep the
    # response small, then fetch the exact feature by its feature id if found.
    # The municipality code is embedded in the CAR identifier itself.
    try:
        prefix=f'{uf}-{mun}-%'
        for start in (0,500,1000,1500,2000):
            params={
                'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':tn,
                'outputFormat':'application/json','CQL_FILTER':f"cod_imovel LIKE '{prefix}'",
                'propertyName':'cod_imovel','count':'500','startIndex':str(start),
            }
            raw=_req(params)
            attempts.append({'strategy':f'municipality_codes_{start}','ok':raw.get('ok'),'bytes':raw.get('bytes',0),'detail':raw.get('detail')})
            if not raw.get('ok'):
                continue
            fs=(raw.get('json') or {}).get('features') or []
            match=None
            for f in fs:
                if _norm((f.get('properties') or {}).get('cod_imovel'))==code:
                    match=f
                    break
            if match:
                fid=match.get('id')
                if fid:
                    raw2=_req({'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,'outputFormat':'application/json','featureID':fid,'srsName':'EPSG:4674'})
                    attempts.append({'strategy':'feature_id','ok':raw2.get('ok'),'bytes':raw2.get('bytes',0),'detail':raw2.get('detail')})
                    result=_build_result(raw2,code,'municipality_scan_feature_id')
                    if result:
                        result['attempts']=attempts
                        return result
                # Some GeoServer responses omit feature ids on propertyName requests.
                raw3=_req({'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,'outputFormat':'application/json','srsName':'EPSG:4674','CQL_FILTER':f"cod_imovel='{code}'",'maxFeatures':'5'})
                result=_build_result(raw3,code,'municipality_scan_refetch')
                if result:
                    result['attempts']=attempts
                    return result
            if len(fs)<500:
                break
    except Exception as exc:
        attempts.append({'strategy':'municipality_scan','ok':False,'detail':f'{type(exc).__name__}:{str(exc)[:180]}'})

    return {
        'ok':False,'source':'SICAR','not_found':True,'feature_count':0,
        'detail':'CAR não localizado após múltiplas estratégias de consulta SICAR.',
        'attempts':attempts,
    }


def install_global_patch():
    deploy_app.fetch_car_live=fetch_car_live_resilient
    try:
        import report_api
        report_api.fetch_car_live=fetch_car_live_resilient
    except Exception:
        pass
    try:
        import portal_api
        portal_api.fetch_car_live=fetch_car_live_resilient
    except Exception:
        pass
    print('RX_CAR_RESOLVER=resilient_multi_strategy',flush=True)


if __name__!='__main__':
    install_global_patch()

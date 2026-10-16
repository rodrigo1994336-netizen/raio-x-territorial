from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import deploy_app
import external_process_lifecycle as epl
from sicar_lookup_http import PENDING_DETAIL

CAR_RE=re.compile(r'^[A-Z]{2}-\d{7}-[A-F0-9]{32}$',re.I)


def _norm(v):
    return str(v or '').strip().upper()


def _feature_count(raw):
    # Number of features when SICAR answered with a FeatureCollection; None when it did not answer
    # (transport failure, HTML error page, GeoServer exception document: JSON without features).
    data=raw.get('json') if isinstance(raw,dict) and raw.get('ok') else None
    fs=data.get('features') if isinstance(data,dict) else None
    return len(fs) if isinstance(fs,list) else None


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


ATTEMPT_HARD_TIMEOUT_S=11
# Tentativas no pior caso: 5 estratégias + 5 páginas da varredura do município + 2 rebuscas pelo id do feature.
MAX_ATTEMPTS=5+5+2
# Cada tentativa custa MAIS que o prazo de rede:
#  (a) a carência de parada do processo gerenciado (terminate+espera, kill+espera) e o passo do laço; e
#  (b) o trabalho em Python DEPOIS que o curl volta — json.loads da página e a varredura de coordenadas do
#      _bbox — que corre na mesma thread e fora de qualquer prazo.
# (b) medido em 15/09 nesta máquina: json.loads de uma página de 13 MB (7.500 feições, o tamanho das páginas
# da varredura do município) leva 0,38 s; o _bbox de uma geometria, 0,01 s. A folga de 2,0 s por tentativa
# cobre ~5x isso, para a CPU do plano grátis do Render.
PAGE_PROCESSING_S=2.0
ATTEMPT_WORST_CASE_S=ATTEMPT_HARD_TIMEOUT_S+epl.STOP_OVERHEAD_SECONDS+PAGE_PROCESSING_S
# Teto do pior caso desta busca (~166 s). Quem põe prazo em cima dela usa este número: um prazo menor
# cortaria resposta que hoje chega. Derivado, não escrito à mão, para não envelhecer quando as tentativas,
# a carência de parada ou o prazo de cada tentativa mudarem.
WORST_CASE_SECONDS=round(MAX_ATTEMPTS*ATTEMPT_WORST_CASE_S,1)


def _req(params, cancel_event=None):
    return deploy_app._curl(deploy_app.SICAR+'?'+urlencode(params),True,cancel_event=cancel_event,connect_timeout=5,max_time=10,hard_timeout=ATTEMPT_HARD_TIMEOUT_S)


def _cancelled(cancel_event):
    return bool(cancel_event and cancel_event.is_set())


def fetch_car_live_resilient(car_code:str, *, cancel_event=None):
    code=_norm(car_code)
    if _cancelled(cancel_event):
        return {'ok':False,'source':'SICAR','cancelled':True,'detail':'request_cancelled','attempts':[]}
    if not CAR_RE.match(code):
        return {'ok':False,'source':'SICAR','not_found':True,'detail':'invalid_car_format'}
    uf=code[:2]
    mun=code[3:10]
    tn=f"sicar:sicar_imoveis_{'DF' if uf=='DF' else uf.lower()}"
    attempts=[]
    # Absence needs SICAR to answer an exact query with NO feature. An exact query answered with
    # features that are not this property means the filter was ignored (or a proxy answered): that
    # proves nothing, so it keeps the lookup pending. The municipality scan never proves absence.
    answered_empty=False
    answered_other=False

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
        if _cancelled(cancel_event):
            return {'ok':False,'source':'SICAR','cancelled':True,'detail':'request_cancelled','attempts':attempts}
        try:
            raw=_req(params,cancel_event)
            if raw.get('cancelled'):
                return {'ok':False,'source':'SICAR','cancelled':True,'detail':'request_cancelled','attempts':attempts}
            count=_feature_count(raw)
            # features: 0 is the only record that can prove absence (the link reader checks it too).
            attempts.append({'strategy':name,'ok':raw.get('ok'),'bytes':raw.get('bytes',0),'detail':raw.get('detail'),'features':count})
            # Classified before the features are read: a malformed item that breaks _build_result is never absence.
            answered_empty=answered_empty or count==0
            answered_other=answered_other or bool(count)
            result=_build_result(raw,code,name)
            if result:
                result['attempts']=attempts
                return result
        except Exception as exc:
            attempts.append({'strategy':name,'ok':False,'detail':f'{type(exc).__name__}:{str(exc)[:160]}'})

    # Last-resort targeted municipality scan. First request only CAR codes to keep the
    # response small, then fetch the exact feature by its feature id if found.
    # The municipality code is embedded in the CAR identifier itself.
    layer_empty=False
    try:
        prefix=f'{uf}-{mun}-%'
        for start in (0,500,1000,1500,2000):
            if _cancelled(cancel_event):
                return {'ok':False,'source':'SICAR','cancelled':True,'detail':'request_cancelled','attempts':attempts}
            params={
                'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':tn,
                'outputFormat':'application/json','CQL_FILTER':f"cod_imovel LIKE '{prefix}'",
                'propertyName':'cod_imovel','count':'500','startIndex':str(start),
            }
            raw=_req(params,cancel_event)
            scan_count=_feature_count(raw)
            attempts.append({'strategy':f'municipality_codes_{start}','ok':raw.get('ok'),'bytes':raw.get('bytes',0),'detail':raw.get('detail'),'features':scan_count})
            if start==0 and scan_count==0:
                # No CAR at all in the municipality: the layer is answering empty to everything.
                layer_empty=True
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
                    raw2=_req({'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,'outputFormat':'application/json','featureID':fid,'srsName':'EPSG:4674'},cancel_event)
                    attempts.append({'strategy':'feature_id','ok':raw2.get('ok'),'bytes':raw2.get('bytes',0),'detail':raw2.get('detail')})
                    result=_build_result(raw2,code,'municipality_scan_feature_id')
                    if result:
                        result['attempts']=attempts
                        return result
                # Some GeoServer responses omit feature ids on propertyName requests.
                raw3=_req({'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,'outputFormat':'application/json','srsName':'EPSG:4674','CQL_FILTER':f"cod_imovel='{code}'",'maxFeatures':'5'},cancel_event)
                result=_build_result(raw3,code,'municipality_scan_refetch')
                if result:
                    result['attempts']=attempts
                    return result
            if len(fs)<500:
                break
    except Exception as exc:
        attempts.append({'strategy':'municipality_scan','ok':False,'detail':f'{type(exc).__name__}:{str(exc)[:180]}'})

    not_found=answered_empty and not answered_other and not layer_empty
    return {
        'ok':False,'source':'SICAR','not_found':not_found,'feature_count':0 if not_found else None,
        'detail':'CAR não localizado após múltiplas estratégias de consulta SICAR.' if not_found else PENDING_DETAIL,
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

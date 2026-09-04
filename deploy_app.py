from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import httpx
import asyncio
import json
import subprocess
from urllib.parse import urlencode

app = FastAPI(title='Raio-X Territorial API', version='0.14.4-live-real-car')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

TEST_CAR = 'MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F'

TARGETS = {
    'ibama':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer?f=pjson',
    'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
    'prodes':'https://terrabrasilis.dpi.inpe.br/geoserver/ows?service=WFS&request=GetCapabilities',
    'sigef_mirror':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10?f=pjson',
    'incra_root':'https://acervofundiario.incra.gov.br/'
}

async def _probe_one(client: httpx.AsyncClient, key: str, url: str):
    try:
        r = await client.get(url)
        return key, {'ok': 200 <= r.status_code < 400, 'status': r.status_code, 'bytes': len(r.content), 'content_type': r.headers.get('content-type','')[:120], 'final_url': str(r.url)[:300]}
    except Exception as e:
        return key, {'ok': False, 'error': type(e).__name__, 'detail': str(e)[:240]}

async def probe_sources():
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=12.0), follow_redirects=True, headers={'User-Agent':'Raio-X-Territorial/0.14.4'}) as c:
        pairs = await asyncio.gather(*[_probe_one(c, k, u) for k, u in TARGETS.items()])
    out = dict(pairs)
    # SICAR is tested separately with system curl because its TLS stack rejects modern Python/OpenSSL in some environments.
    try:
        cap = await asyncio.to_thread(_curl_json_or_text, 'https://geoserver.car.gov.br/geoserver/sicar/ows?service=WFS&version=1.0.0&request=GetCapabilities', False)
        out['sicar_curl'] = {'ok': cap['ok'], 'bytes': cap.get('bytes',0), 'detail': cap.get('detail','')}
    except Exception as e:
        out['sicar_curl'] = {'ok': False, 'error': type(e).__name__, 'detail': str(e)[:240]}
    return out

def _curl_json_or_text(url: str, expect_json: bool = True):
    p = subprocess.run(['curl','-k','-sS','--connect-timeout','12','--max-time','35','-A','Raio-X-Territorial/0.14.4',url], capture_output=True, timeout=40)
    if p.returncode != 0:
        return {'ok': False, 'detail': p.stderr.decode('utf-8','ignore')[:300], 'bytes': len(p.stdout)}
    raw = p.stdout
    if expect_json:
        try:
            return {'ok': True, 'json': json.loads(raw.decode('utf-8')), 'bytes': len(raw)}
        except Exception as e:
            return {'ok': False, 'detail': f'JSONDecodeError:{e}', 'preview': raw[:200].decode('utf-8','ignore'), 'bytes': len(raw)}
    return {'ok': len(raw)>0, 'bytes': len(raw), 'preview': raw[:160].decode('utf-8','ignore')}

def _iter_coords(obj):
    if isinstance(obj, (list,tuple)):
        if len(obj) >= 2 and isinstance(obj[0], (int,float)) and isinstance(obj[1], (int,float)):
            yield float(obj[0]), float(obj[1])
        else:
            for item in obj:
                yield from _iter_coords(item)

def _bbox_from_geometry(geom):
    pts = list(_iter_coords((geom or {}).get('coordinates', [])))
    if not pts:
        return None
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]

def fetch_car_live(car_code: str):
    uf = car_code[:2]
    typename = f"sicar:sicar_imoveis_{'DF' if uf == 'DF' else uf.lower()}"
    params = {
        'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':typename,
        'outputFormat':'application/json','CQL_FILTER':f"cod_imovel IN ('{car_code}')"
    }
    url='https://geoserver.car.gov.br/geoserver/sicar/ows?' + urlencode(params)
    res=_curl_json_or_text(url, True)
    if not res.get('ok'):
        return {'ok':False,'source':'SICAR','detail':res.get('detail'),'preview':res.get('preview'),'bytes':res.get('bytes',0)}
    data=res['json']; feats=data.get('features') or []
    if not feats:
        return {'ok':False,'source':'SICAR','not_found':True,'feature_count':0}
    f=feats[0]
    props=f.get('properties') or {}
    bbox=_bbox_from_geometry(f.get('geometry'))
    return {'ok':True,'source':'SICAR','feature_count':len(feats),'properties':props,'geometry':f.get('geometry'),'bbox':bbox,'bytes':res.get('bytes',0)}

async def query_sigef_mirror(bbox):
    if not bbox:
        return {'ok':False,'detail':'missing_bbox'}
    env=','.join(str(x) for x in bbox)
    params={
        'f':'json','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4674',
        'spatialRel':'esriSpatialRelIntersects','outFields':'parcela_co,situacao_i,codigo_imo,data_submi,data_aprov,status,nome_area,registro_m,registro_d,municipio_,uf_id',
        'returnGeometry':'true','outSR':'4674','resultRecordCount':'100'
    }
    url='https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10/query'
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            r=await c.get(url, params=params)
            data=r.json()
        feats=data.get('features') or []
        return {'ok':r.status_code==200 and 'error' not in data,'status':r.status_code,'feature_count':len(feats),'features':feats,'source':'IBAMA mirror oficial do SIGEF/INCRA'}
    except Exception as e:
        return {'ok':False,'error':type(e).__name__,'detail':str(e)[:240]}

@app.on_event('startup')
async def startup_probe():
    print('RX_STARTUP_PROBE_BEGIN', flush=True)
    try:
        result = await probe_sources()
        print('RX_STARTUP_PROBE_RESULT=' + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        car = await asyncio.to_thread(fetch_car_live, TEST_CAR)
        car_log={k:v for k,v in car.items() if k not in ('geometry',)}
        if isinstance(car_log.get('properties'),dict):
            car_log['properties']={k:car_log['properties'].get(k) for k in ('cod_imovel','area','municipio','uf','m_fiscal','status_imovel','tipo_imovel','condicao') if k in car_log['properties']}
        print('RX_REAL_CAR_RESULT=' + json.dumps(car_log, ensure_ascii=False, sort_keys=True, default=str), flush=True)
        if car.get('ok'):
            sigef=await query_sigef_mirror(car.get('bbox'))
            sigef_log={'ok':sigef.get('ok'),'status':sigef.get('status'),'feature_count':sigef.get('feature_count'),'source':sigef.get('source')}
            if sigef.get('features'):
                sigef_log['sample_attributes']=(sigef['features'][0].get('attributes') or {})
            print('RX_SIGEF_MIRROR_RESULT=' + json.dumps(sigef_log, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    except Exception as e:
        print(f'RX_STARTUP_PROBE_FATAL={type(e).__name__}:{str(e)[:240]}', flush=True)

@app.get('/')
def root():
    return {'app':'Raio-X Territorial','status':'online','version':'0.14.4-live-real-car'}

@app.get('/health')
def health():
    return {'ok': True, 'env': os.getenv('APP_ENV','unknown')}

@app.get('/v1/live/probe')
async def live_probe():
    return {'sources': await probe_sources()}

@app.get('/v1/live/car/{car_code}')
async def live_car(car_code: str):
    car=await asyncio.to_thread(fetch_car_live, car_code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail={k:v for k,v in car.items() if k!='geometry'})
    sigef=await query_sigef_mirror(car.get('bbox'))
    return {'car':car,'sigef_candidates':sigef}

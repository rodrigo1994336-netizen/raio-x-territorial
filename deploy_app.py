from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import httpx
import asyncio
import json

app = FastAPI(title='Raio-X Territorial API', version='0.14.3-live-probe')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

TARGETS = {
    'sicar':'https://geoserver.car.gov.br/geoserver/sicar/ows?service=WFS&version=1.0.0&request=GetCapabilities',
    'ibama':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer?f=pjson',
    'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
    'prodes':'https://terrabrasilis.dpi.inpe.br/geoserver/ows?service=WFS&request=GetCapabilities',
    'incra_wfs_https':'https://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_mg&service=WFS&version=1.0.0&request=GetCapabilities',
    'incra_wfs_http':'http://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_mg&service=WFS&version=1.0.0&request=GetCapabilities',
    'incra_download':'https://acervofundiario.incra.gov.br/geodownload/geodados.php',
    'incra_download_i3':'https://acervofundiario.incra.gov.br/i3geo/geodownload/geodados.php',
    'incra_root':'https://acervofundiario.incra.gov.br/'
}

async def _probe_one(client: httpx.AsyncClient, key: str, url: str):
    try:
        r = await client.get(url)
        return key, {
            'ok': 200 <= r.status_code < 400,
            'status': r.status_code,
            'bytes': len(r.content),
            'content_type': r.headers.get('content-type','')[:120],
            'final_url': str(r.url)[:300]
        }
    except Exception as e:
        return key, {'ok': False, 'error': type(e).__name__, 'detail': str(e)[:240]}

async def probe_sources():
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=12.0),
        follow_redirects=True,
        headers={'User-Agent':'Raio-X-Territorial/0.14.3 (+fontes-publicas)'}
    ) as c:
        pairs = await asyncio.gather(*[_probe_one(c, k, u) for k, u in TARGETS.items()])
    return dict(pairs)

@app.on_event('startup')
async def startup_probe():
    print('RX_STARTUP_PROBE_BEGIN', flush=True)
    try:
        result = await probe_sources()
        print('RX_STARTUP_PROBE_RESULT=' + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        principal = ['sicar','ibama','anm','prodes']
        principal_ok = sum(1 for k in principal if result.get(k,{}).get('ok'))
        incra_ok = any(result.get(k,{}).get('ok') for k in ('incra_wfs_https','incra_wfs_http','incra_download','incra_download_i3','incra_root'))
        print(f'RX_STARTUP_PRINCIPAL={principal_ok}/{len(principal)};INCRA_ANY={incra_ok}', flush=True)
    except Exception as e:
        print(f'RX_STARTUP_PROBE_FATAL={type(e).__name__}:{str(e)[:200]}', flush=True)

@app.get('/')
def root():
    return {'app':'Raio-X Territorial','status':'online','version':'0.14.3-live-probe'}

@app.get('/health')
def health():
    return {'ok': True, 'env': os.getenv('APP_ENV','unknown')}

@app.get('/v1/live/probe')
async def live_probe():
    return {'sources': await probe_sources()}

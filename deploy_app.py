from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import httpx
import asyncio
import json

app = FastAPI(title='Raio-X Territorial API', version='0.14.2-live-probe')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

TARGETS = {
    'ibama':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer?f=pjson',
    'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
    'prodes':'https://terrabrasilis.dpi.inpe.br/geoserver/ows?service=WFS&request=GetCapabilities',
    'incra':'https://acervofundiario.incra.gov.br/i3geo/ogc.php?service=wfs&request=GetCapabilities'
}

async def probe_sources():
    out = {}
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers={'User-Agent':'Raio-X-Territorial/0.14'}) as c:
        for k,u in TARGETS.items():
            try:
                r = await c.get(u)
                out[k] = {
                    'ok': 200 <= r.status_code < 400,
                    'status': r.status_code,
                    'bytes': len(r.content),
                    'content_type': r.headers.get('content-type','')[:120]
                }
            except Exception as e:
                out[k] = {'ok': False, 'error': type(e).__name__, 'detail': str(e)[:200]}
    return out

@app.on_event('startup')
async def startup_probe():
    print('RX_STARTUP_PROBE_BEGIN', flush=True)
    try:
        result = await probe_sources()
        print('RX_STARTUP_PROBE_RESULT=' + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        ok_count = sum(1 for v in result.values() if v.get('ok'))
        print(f'RX_STARTUP_PROBE_SUMMARY={ok_count}/{len(result)}', flush=True)
    except Exception as e:
        print(f'RX_STARTUP_PROBE_FATAL={type(e).__name__}:{str(e)[:200]}', flush=True)

@app.get('/')
def root():
    return {'app':'Raio-X Territorial','status':'online','version':'0.14.2-live-probe'}

@app.get('/health')
def health():
    return {'ok': True, 'env': os.getenv('APP_ENV','unknown')}

@app.get('/v1/live/probe')
async def live_probe():
    return {'sources': await probe_sources()}

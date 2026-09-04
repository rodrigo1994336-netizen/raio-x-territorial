from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import httpx

app = FastAPI(title='Raio-X Territorial API', version='0.14.1-live-bootstrap')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

@app.get('/')
def root():
    return {'app':'Raio-X Territorial','status':'online','version':'0.14.1-live-bootstrap'}

@app.get('/health')
def health():
    return {'ok': True, 'env': os.getenv('APP_ENV','unknown')}

@app.get('/v1/live/probe')
async def live_probe():
    targets = {
        'ibama':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer?f=pjson',
        'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
        'prodes':'https://terrabrasilis.dpi.inpe.br/geoserver/ows?service=WFS&request=GetCapabilities',
        'incra':'https://acervofundiario.incra.gov.br/i3geo/ogc.php?service=wfs&request=GetCapabilities'
    }
    out = {}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
        for k,u in targets.items():
            try:
                r = await c.get(u)
                out[k] = {'ok': 200 <= r.status_code < 400, 'status': r.status_code, 'bytes': len(r.content)}
            except Exception as e:
                out[k] = {'ok': False, 'error': type(e).__name__}
    return {'sources': out}

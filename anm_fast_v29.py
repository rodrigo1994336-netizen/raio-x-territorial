from __future__ import annotations

import asyncio
import json
import subprocess
import time
from urllib.parse import urlencode

import deploy_app

ANM_QUERY=deploy_app.ANM


def _curl_anm_bbox(bbox):
    env=','.join(str(x) for x in bbox)
    params={
        'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope',
        'inSR':'4326','spatialRel':'esriSpatialRelIntersects','outFields':'*',
        'returnGeometry':'true','outSR':'4326','resultRecordCount':'500'
    }
    url=ANM_QUERY+'?'+urlencode(params)
    t0=time.monotonic()
    try:
        # ANM is valuable, but a slow provider must not block the whole dossier.
        # One bounded attempt is enough in the synchronous report path. The mining
        # tab can refresh independently later when the provider recovers.
        p=subprocess.run([
            'curl','-sS','--retry','0',
            '--connect-timeout','3','--max-time','7',
            '-A','Raio-X-Territorial/0.30-ANM-bounded',url
        ],capture_output=True,timeout=9)
        ms=round((time.monotonic()-t0)*1000)
        if p.returncode:
            return {'ok':False,'source':'ANM/SIGMINE','error':'timeout_or_transport','detail':p.stderr.decode('utf-8','ignore')[:240],'elapsed_ms':ms,'state':'temporarily_unavailable'}
        data=json.loads(p.stdout.decode('utf-8'))
        if data.get('error'):
            return {'ok':False,'source':'ANM/SIGMINE','error':'arcgis','detail':str(data.get('error'))[:300],'elapsed_ms':ms,'state':'temporarily_unavailable'}
        fs=data.get('features') or []
        print(f'RX_ANM_FAST={ms}ms:features={len(fs)}',flush=True)
        return {'ok':True,'status':200,'feature_count':len(fs),'features':fs,'source':'ANM/SIGMINE','transport':'curl-bounded','elapsed_ms':ms}
    except Exception as e:
        ms=round((time.monotonic()-t0)*1000)
        print(f'RX_ANM_FAST_FAIL={ms}ms:{type(e).__name__}',flush=True)
        return {'ok':False,'source':'ANM/SIGMINE','error':type(e).__name__,'detail':str(e)[:240],'elapsed_ms':ms,'state':'temporarily_unavailable'}


async def query_anm_fast(bbox):
    return await asyncio.to_thread(_curl_anm_bbox,bbox)


# deploy_app.analyze_car resolves this global dynamically at call time.
deploy_app.query_anm=query_anm_fast
try:
    import report_api
    report_api.query_anm=query_anm_fast
except Exception:
    pass

print('RX_ANM_V30=single_bounded_7s_no_retry',flush=True)

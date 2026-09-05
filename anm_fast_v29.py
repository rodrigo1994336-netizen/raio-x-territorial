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
        p=subprocess.run([
            'curl','-sS','--retry','1','--retry-delay','1',
            '--connect-timeout','5','--max-time','12',
            '-A','Raio-X-Territorial/0.29-ANM-fast',url
        ],capture_output=True,timeout=16)
        ms=round((time.monotonic()-t0)*1000)
        if p.returncode:
            return {'ok':False,'source':'ANM/SIGMINE','error':'timeout_or_transport','detail':p.stderr.decode('utf-8','ignore')[:240],'elapsed_ms':ms}
        data=json.loads(p.stdout.decode('utf-8'))
        if data.get('error'):
            return {'ok':False,'source':'ANM/SIGMINE','error':'arcgis','detail':str(data.get('error'))[:300],'elapsed_ms':ms}
        fs=data.get('features') or []
        print(f'RX_ANM_FAST={ms}ms:features={len(fs)}',flush=True)
        return {'ok':True,'status':200,'feature_count':len(fs),'features':fs,'source':'ANM/SIGMINE','transport':'curl-fast','elapsed_ms':ms}
    except Exception as e:
        ms=round((time.monotonic()-t0)*1000)
        print(f'RX_ANM_FAST_FAIL={ms}ms:{type(e).__name__}',flush=True)
        return {'ok':False,'source':'ANM/SIGMINE','error':type(e).__name__,'detail':str(e)[:240],'elapsed_ms':ms}


async def query_anm_fast(bbox):
    return await asyncio.to_thread(_curl_anm_bbox,bbox)


# deploy_app.analyze_car resolves this global dynamically at call time.
deploy_app.query_anm=query_anm_fast
try:
    import report_api
    report_api.query_anm=query_anm_fast
except Exception:
    pass

print('RX_ANM_V29=fast_bounded_latency',flush=True)

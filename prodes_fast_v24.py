from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET

import httpx
import deploy_app

PRODES=deploy_app.PRODES
_CACHE_TTL=6*3600
_layer_cache={'ts':0.0,'layers':[]}
_cache_lock=asyncio.Lock()


def _local(tag):
    return tag.rsplit('}',1)[-1]


async def _discover_layers(client:httpx.AsyncClient):
    now=time.monotonic()
    if _layer_cache['layers'] and now-_layer_cache['ts']<_CACHE_TTL:
        return list(_layer_cache['layers']),True
    async with _cache_lock:
        now=time.monotonic()
        if _layer_cache['layers'] and now-_layer_cache['ts']<_CACHE_TTL:
            return list(_layer_cache['layers']),True
        t0=time.monotonic()
        rr=await client.get(PRODES,params={'service':'WFS','version':'2.0.0','request':'GetCapabilities'})
        rr.raise_for_status()
        root=ET.fromstring(rr.text)
        found=[]
        for ft in root.iter():
            if _local(ft.tag)!='FeatureType':
                continue
            name=title=None
            for ch in ft:
                tag=_local(ch.tag)
                if tag=='Name' and ch.text:name=ch.text.strip()
                elif tag=='Title' and ch.text:title=ch.text.strip()
            if name:
                score=deploy_app._layer_score(name,title or '')
                if score>0:found.append((score,name,title))
        layers=sorted(found,reverse=True)[:8]
        _layer_cache.update(ts=time.monotonic(),layers=list(layers))
        print(f'RX_PRODES_STAGE=catalog:{round((time.monotonic()-t0)*1000)}ms:layers={len(layers)}',flush=True)
        return layers,False


async def query_prodes_fast(bbox):
    started=time.monotonic()
    timeout=httpx.Timeout(16.0,connect=6.0,read=16.0,write=10.0,pool=8.0)
    limits=httpx.Limits(max_connections=10,max_keepalive_connections=8)
    headers={'User-Agent':'Raio-X-Territorial/0.24 PRODES-parallel'}
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,limits=limits,headers=headers) as c:
            layers,cached=await _discover_layers(c)
            xmin,ymin,xmax,ymax=bbox
            sem=asyncio.Semaphore(6)

            async def one(score,name,title):
                t0=time.monotonic()
                async with sem:
                    try:
                        rr=await c.get(PRODES,params={
                            'service':'WFS','version':'2.0.0','request':'GetFeature',
                            'typeNames':name,'srsName':'EPSG:4674',
                            'bbox':f'{xmin},{ymin},{xmax},{ymax},EPSG:4674',
                            'count':'2000','outputFormat':'application/json'
                        })
                        rr.raise_for_status()
                        data=rr.json();fs=data.get('features') or []
                        ms=round((time.monotonic()-t0)*1000)
                        print(f'RX_PRODES_LAYER={name}:{ms}ms:count={len(fs)}',flush=True)
                        return {'layer':name,'title':title,'score':score,'count':len(fs),'features':fs,'elapsed_ms':ms} if fs else None
                    except Exception as e:
                        ms=round((time.monotonic()-t0)*1000)
                        print(f'RX_PRODES_LAYER_FAIL={name}:{ms}ms:{type(e).__name__}',flush=True)
                        return {'layer':name,'title':title,'score':score,'count':0,'features':[],'error':type(e).__name__,'detail':str(e)[:160],'elapsed_ms':ms}

            rows=await asyncio.gather(*(one(*x) for x in layers)) if layers else []
            # Preserve only actual hits in `hits`, matching the legacy contract; failed
            # layers are exposed separately so a timeout is never misread as no PRODES.
            hits=[x for x in rows if x and x.get('count',0)>0]
            failed=[x for x in rows if x and x.get('error')]
            total_ms=round((time.monotonic()-started)*1000)
            print(f'RX_PRODES_FAST_READY={total_ms}ms:hits={len(hits)}:failed={len(failed)}:catalog_cache={cached}',flush=True)
            return {
                'ok':True,
                'candidate_layers':[x[1] for x in layers],
                'hits':hits,
                'failed_layers':[{k:x.get(k) for k in ('layer','error','detail','elapsed_ms')} for x in failed],
                'feature_count':sum(int(x.get('count') or 0) for x in hits),
                'source':'INPE/TerraBrasilis WFS — consultas paralelas',
                'elapsed_ms':total_ms,
                'catalog_cached':cached,
            }
    except Exception as e:
        ms=round((time.monotonic()-started)*1000)
        print(f'RX_PRODES_FAST_FAIL={ms}ms:{type(e).__name__}:{str(e)[:180]}',flush=True)
        return {'ok':False,'error':type(e).__name__,'detail':str(e)[:250],'source':'INPE/TerraBrasilis WFS','elapsed_ms':ms}


# analyze_car is an existing function object whose global name lookup resolves in
# deploy_app at call time, so replacing this global accelerates every caller.
deploy_app.query_prodes=query_prodes_fast
try:
    import report_api
    report_api.query_prodes=query_prodes_fast
except Exception:
    pass

print('RX_PRODES_V24=parallel_cached_top8',flush=True)

from __future__ import annotations

import asyncio
import json
import threading

from deploy_app import TEST_CAR
from map_mineral_routes import mineral_wms_layers
from agropecuaria import query_ppm


async def _run():
    try:
        minerals,ppm=await asyncio.gather(mineral_wms_layers('terras_raras'),query_ppm(TEST_CAR))
        print('RX_RARE_EARTH_FILTER_SMOKE='+json.dumps({'ok':minerals.get('ok'),'layer_count':len(minerals.get('layers') or []),'source':minerals.get('source'),'detail':minerals.get('detail')},ensure_ascii=False,default=str),flush=True)
        print('RX_AGRO_PPM_SMOKE='+json.dumps({'ok':ppm.get('ok'),'municipality_code':ppm.get('municipality_code'),'series_count':len(ppm.get('series') or []),'source':ppm.get('source'),'detail':ppm.get('detail')},ensure_ascii=False,default=str),flush=True)
    except Exception as e:
        print(f'RX_FEATURE_SMOKE_FAIL={type(e).__name__}:{str(e)[:300]}',flush=True)


def _thread_main():
    try: asyncio.run(_run())
    except Exception as e: print(f'RX_FEATURE_SMOKE_THREAD_FAIL={type(e).__name__}:{str(e)[:300]}',flush=True)

threading.Thread(target=_thread_main,daemon=True).start()

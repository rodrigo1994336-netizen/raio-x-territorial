from __future__ import annotations

import json
import threading

from car_resilient import fetch_car_live_resilient
from deploy_app import TEST_CAR


def _run():
    try:
        r=fetch_car_live_resilient(TEST_CAR)
        if r.get('ok'):
            p=r.get('properties') or {}
            log={
                'car':p.get('cod_imovel'),
                'municipio':p.get('municipio'),
                'uf':p.get('uf'),
                'area':p.get('area'),
                'strategy':r.get('strategy'),
                'bytes':r.get('bytes'),
            }
            print('RX_CAR_SMOKE_OK='+json.dumps(log,ensure_ascii=False,default=str),flush=True)
        else:
            print('RX_CAR_SMOKE_FAIL='+json.dumps({'detail':r.get('detail'),'attempts':r.get('attempts')},ensure_ascii=False,default=str)[:8000],flush=True)
    except Exception as exc:
        print(f'RX_CAR_SMOKE_FAIL={type(exc).__name__}:{str(exc)[:500]}',flush=True)

threading.Thread(target=_run,daemon=True).start()

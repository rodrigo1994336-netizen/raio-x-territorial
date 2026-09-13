from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request

import portal_v8
from car_resilient import fetch_car_live_resilient
from external_process_lifecycle import ManagedOperationTimeout, RequestDisconnected, install_shutdown_cleanup, run_sync_with_request_lifecycle

app=portal_v8.app
install_shutdown_cleanup(app)


async def live_car_resilient(car_code:str, request:Request):
    try:
        car=await run_sync_with_request_lifecycle(request,fetch_car_live_resilient,car_code.upper(),timeout_seconds=None)
    except RequestDisconnected:
        raise HTTPException(status_code=499,detail='client_disconnected')
    except ManagedOperationTimeout:
        raise HTTPException(status_code=504,detail='sicar_lookup_timeout')
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail={
            'car':{
                'ok':False,
                'source':car.get('source'),
                'not_found':car.get('not_found'),
                'detail':car.get('detail'),
                'attempts':car.get('attempts') or [],
            }
        })
    # Lookup endpoint is intentionally light and fast. The deep analysis is started
    # separately after the property has been located.
    return {'car':car,'lookup_mode':'resilient_multi_strategy'}


app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/car/{car_code}']
app.get('/v1/live/car/{car_code}')(live_car_resilient)

print('RX_PORTAL_CAR_RESOLVER=resilient_multi_strategy',flush=True)

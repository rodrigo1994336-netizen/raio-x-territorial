from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request

import portal_v8
from car_resilient import fetch_car_live_resilient
from sicar_lookup_http import lookup_http_error
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
        # Same body for every answer (422 invalid code, 404 not located, 503 pending): the link reader (W1a)
        # checks detail.car and the exact attempts itself.
        body={
            'car':{
                'ok':False,
                'source':car.get('source'),
                'not_found':car.get('not_found'),
                'detail':car.get('detail'),
                'attempts':car.get('attempts') or [],
            }
        }
        raise lookup_http_error(car,not_found_detail=body,pending_detail=body,invalid_detail=body)
    # Lookup endpoint is intentionally light and fast. The deep analysis is started
    # separately after the property has been located.
    return {'car':car,'lookup_mode':'resilient_multi_strategy'}


app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/car/{car_code}']
app.get('/v1/live/car/{car_code}')(live_car_resilient)

print('RX_PORTAL_CAR_RESOLVER=resilient_multi_strategy',flush=True)

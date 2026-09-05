from __future__ import annotations

import portal_v8
from territorial_production import build_territorial_production

app = portal_v8.app


@app.get('/v1/live/territorial-production/{car_code}')
async def territorial_production(car_code: str):
    """Return territorial production data on demand.

    V43 deliberately keeps this module route-only. The former V33 presentation
    layer depended on the retired eight-tab UI and ran a 900 ms polling timer
    even when the user never opened production details.
    """
    return await build_territorial_production(car_code.upper())


print('RX_PORTAL_TERRITORIAL_PRODUCTION_V43=route_only_no_legacy_tab_timer', flush=True)

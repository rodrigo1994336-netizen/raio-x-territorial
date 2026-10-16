from __future__ import annotations

import time

import portal_api as base
from fastapi.responses import JSONResponse

app = base.app
STATE = {
    'ready': False,
    'error': None,
    'started_at': time.time(),
    'ready_at': None,
    'version': 'V26',
}


@app.get('/v1/bootstrap/state')
async def bootstrap_state_v26():
    elapsed_ms = int((time.time() - STATE['started_at']) * 1000)
    return JSONResponse({
        'ok': True,
        'ready': bool(STATE['ready']),
        'error': STATE['error'],
        'elapsed_ms': elapsed_ms,
        'version': STATE['version'],
    }, headers={'Cache-Control': 'no-store'})


def mark_ready():
    STATE['ready'] = True
    STATE['error'] = None
    STATE['ready_at'] = time.time()
    print('RX_PORTAL_V26_BOOT=ready', flush=True)


def mark_failed(exc):
    STATE['ready'] = False
    STATE['error'] = f'{type(exc).__name__}:{str(exc)[:240]}'
    print('RX_PORTAL_V26_BOOT=degraded:'+STATE['error'], flush=True)


# W1a: the overlay + "reload once per tab" script that used to be injected into
# PORTAL_HTML is gone. It existed because GET / answered during the deferred load
# returned an incomplete page; the page could not tell, so every new tab reloaded
# (3-7 s). Now portal_boot_assets_w1a answers GET / with a boot page while STATE
# is not ready and with the complete HTML afterwards - no reload on the ready page.

print('RX_PORTAL_BOOT_GUARD_V26=installed state_only:w1a', flush=True)

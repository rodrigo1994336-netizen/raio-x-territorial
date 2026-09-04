import os
import sys
import threading
import time

# Render starts `uvicorn portal_api:app`. Importing portal_v8 synchronously from
# sitecustomize creates a circular import and can leave the old/minimal root active.
# Load the extension only after portal_api has finished defining PORTAL_HTML/app.
def _load_v8_after_portal():
    if os.getenv('RX_RELEASE') != 'V8_OPERATIONAL_ZERO_COST':
        return
    for _ in range(240):
        mod = sys.modules.get('portal_api')
        if mod is not None and hasattr(mod, 'PORTAL_HTML') and hasattr(mod, 'app'):
            try:
                import portal_v8  # noqa: F401
                import portal_sicar_resilient  # noqa: F401
                import portal_map_smoke  # noqa: F401
                print('RX_PORTAL_V8_EXTENSION=loaded_deferred', flush=True)
            except Exception as exc:
                print(f'RX_PORTAL_V8_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:300]}', flush=True)
            return
        time.sleep(0.05)
    print('RX_PORTAL_V8_EXTENSION=timeout_waiting_portal_api', flush=True)

if os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST':
    threading.Thread(target=_load_v8_after_portal, daemon=True).start()

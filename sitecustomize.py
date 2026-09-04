import os
import sys
import threading
import time


def _load_report_v15_after_report_api():
    for _ in range(320):
        mod=sys.modules.get('report_api')
        if mod is not None and hasattr(mod,'app') and hasattr(mod,'_analyze_with_live_addons'):
            try:
                import car_resilient  # noqa: F401
                import rasterio_runtime_bootstrap
                rasterio_runtime_bootstrap.ensure_rasterio()
                import report_v15_patch  # noqa: F401
                print('RX_REPORT_V15_RUNTIME=loaded_deferred',flush=True)
            except Exception as exc:
                print(f'RX_REPORT_V15_RUNTIME=failed:{type(exc).__name__}:{str(exc)[:300]}',flush=True)
            return
        time.sleep(0.05)


def _load_v8_after_portal():
    if os.getenv('RX_RELEASE') != 'V8_OPERATIONAL_ZERO_COST':
        return
    for _ in range(320):
        mod=sys.modules.get('portal_api')
        if mod is not None and hasattr(mod,'PORTAL_HTML') and hasattr(mod,'app'):
            try:
                import postgres_runtime_bootstrap
                postgres_runtime_bootstrap.ensure_postgres_driver()
                import car_resilient  # noqa: F401
                import parity_public_layers  # noqa: F401
                import portal_v8  # noqa: F401
                import portal_sicar_resilient  # noqa: F401
                import portal_car_resilient  # noqa: F401
                import property_search  # noqa: F401
                import map_context  # noqa: F401
                import portal_progressive  # noqa: F401
                import portal_alerts  # noqa: F401
                import portal_intelligence_filters  # noqa: F401
                import portal_rare_earth_locator_upgrade  # noqa: F401
                import portal_rare_earth_symbols  # noqa: F401
                import portal_property_tabs  # noqa: F401
                import portal_smart_search  # noqa: F401
                import portal_map_context_symbols  # noqa: F401
                import portal_property_identity_v13  # noqa: F401
                import portal_feature_smoke  # noqa: F401
                import portal_map_smoke  # noqa: F401
                import car_resolver_smoke  # noqa: F401
                print('RX_PORTAL_V8_EXTENSION=loaded_deferred',flush=True)
            except Exception as exc:
                print(f'RX_PORTAL_V8_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:300]}',flush=True)
            return
        time.sleep(0.05)
    print('RX_PORTAL_V8_EXTENSION=timeout_waiting_portal_api',flush=True)


threading.Thread(target=_load_report_v15_after_report_api,daemon=True).start()
if os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST':
    threading.Thread(target=_load_v8_after_portal,daemon=True).start()

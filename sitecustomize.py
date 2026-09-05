import os
import sys
import threading
import time

IS_PORTAL = os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST'


def _load_report_v22_after_report_api():
    if IS_PORTAL:
        print('RX_REPORT_V22_RUNTIME=skipped_on_portal_service',flush=True)
        return
    for _ in range(320):
        mod=sys.modules.get('report_api')
        if mod is not None and hasattr(mod,'app') and hasattr(mod,'_analyze_with_live_addons'):
            try:
                import car_resilient  # noqa: F401
                import rasterio_runtime_bootstrap
                rasterio_runtime_bootstrap.ensure_rasterio()
                import report_v18_patch  # noqa: F401
                import heavy_live_api_v20  # noqa: F401
                import report_pdf_cache_v21  # noqa: F401
                import report_quick_v22  # noqa: F401
                print('RX_REPORT_V22_RUNTIME=loaded_deferred',flush=True)
            except Exception as exc:
                print(f'RX_REPORT_V22_RUNTIME=failed:{type(exc).__name__}:{str(exc)[:300]}',flush=True)
            return
        time.sleep(0.05)


def _load_portal_v21():
    if not IS_PORTAL:
        return
    for _ in range(320):
        mod=sys.modules.get('portal_api')
        if mod is not None and hasattr(mod,'PORTAL_HTML') and hasattr(mod,'app'):
            try:
                import postgres_runtime_bootstrap
                postgres_runtime_bootstrap.ensure_postgres_driver()
                import redis_runtime_bootstrap
                redis_runtime_bootstrap.ensure_redis_driver()
                import jwt_runtime_bootstrap
                threading.Thread(target=jwt_runtime_bootstrap.ensure_jwt,daemon=True).start()
                import car_resilient  # noqa: F401
                import parity_public_layers  # noqa: F401
                import portal_v8  # noqa: F401
                import portal_sicar_resilient  # noqa: F401
                import portal_car_resilient  # noqa: F401
                import property_search  # noqa: F401
                import property_identity_runtime  # noqa: F401
                import map_context  # noqa: F401
                import portal_progressive  # noqa: F401
                import portal_alerts  # noqa: F401
                import portal_intelligence_filters  # noqa: F401
                import portal_rare_earth_locator_upgrade  # noqa: F401
                import portal_rare_earth_symbols  # noqa: F401
                import portal_property_tabs  # noqa: F401
                import portal_live_fix_v18  # noqa: F401
                import portal_smart_search  # noqa: F401
                import portal_map_context_symbols  # noqa: F401
                import portal_property_identity_v13  # noqa: F401
                import portal_mobile_v19  # noqa: F401
                import portal_nationwide_v21  # noqa: F401
                import portal_legacy_source_cleanup  # noqa: F401
                import portal_mobile_dossier_v20  # noqa: F401
                import portal_pdf_v21  # noqa: F401
                import portal_feature_smoke  # noqa: F401
                import portal_map_smoke  # noqa: F401
                import car_resolver_smoke  # noqa: F401
                print('RX_PORTAL_V21_EXTENSION=loaded_deferred',flush=True)
            except Exception as exc:
                print(f'RX_PORTAL_V21_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:300]}',flush=True)
            return
        time.sleep(0.05)
    print('RX_PORTAL_V21_EXTENSION=timeout_waiting_portal_api',flush=True)


if IS_PORTAL:
    threading.Thread(target=_load_portal_v21,daemon=True).start()
else:
    threading.Thread(target=_load_report_v22_after_report_api,daemon=True).start()

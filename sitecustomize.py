import os
import sys
import threading
import time

IS_PORTAL = os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST'


def _load_report_v24_after_report_api():
    if IS_PORTAL:
        print('RX_REPORT_V24_RUNTIME=skipped_on_portal_service',flush=True)
        return
    for _ in range(320):
        mod=sys.modules.get('report_api')
        if mod is not None and hasattr(mod,'app') and hasattr(mod,'_analyze_with_live_addons'):
            try:
                import car_resilient  # noqa: F401
                import rasterio_runtime_bootstrap
                rasterio_runtime_bootstrap.ensure_rasterio()
                import prodes_fast_v24  # noqa: F401
                import report_perf_v24  # noqa: F401
                import report_v18_patch  # noqa: F401
                import heavy_live_api_v20  # noqa: F401
                import report_pdf_cache_v21  # noqa: F401
                import report_quick_v22  # noqa: F401
                print('RX_REPORT_V24_RUNTIME=loaded_deferred',flush=True)
            except Exception as exc:
                print(f'RX_REPORT_V24_RUNTIME=failed:{type(exc).__name__}:{str(exc)[:300]}',flush=True)
            return
        time.sleep(0.05)


def _load_portal_v25_sync():
    """Import the complete portal before Uvicorn exposes the app.

    V23 loaded these modules in a background thread after the server was already
    accepting requests. That produced a real half-ready window: HTML was 200 while
    routes such as /v1/alerts/summary were still 404. Buttons could therefore look
    dead even though their backend appeared seconds later.
    """
    if not IS_PORTAL:return
    try:
        # Importing portal_api here is intentional. Uvicorn later imports the same
        # cached module, but only after every extension below has finished loading.
        import portal_api  # noqa: F401
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
        import portal_human_reading_v23  # noqa: F401
        import portal_pdf_v21  # noqa: F401
        import portal_action_runtime_v25  # noqa: F401
        import portal_feature_smoke  # noqa: F401
        import portal_map_smoke  # noqa: F401
        import car_resolver_smoke  # noqa: F401
        ready=portal_action_runtime_v25._route_paths()
        missing=[x for x in portal_action_runtime_v25.REQUIRED_ROUTES if x not in ready]
        if missing:raise RuntimeError('missing_required_routes:'+','.join(missing))
        print(f'RX_PORTAL_V25_SYNC_READY=routes:{len(ready)}',flush=True)
    except Exception as exc:
        print(f'RX_PORTAL_V25_SYNC_FATAL={type(exc).__name__}:{str(exc)[:500]}',flush=True)
        raise


if IS_PORTAL:
    _load_portal_v25_sync()
else:
    threading.Thread(target=_load_report_v24_after_report_api,daemon=True).start()

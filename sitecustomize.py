import importlib.util
import os
import sys
import threading
import time

IS_PORTAL = os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST'

# sitecustomize is imported automatically by every Python process, including pip
# during Render builds. Never import application modules until core dependencies are
# already installed. This prevents build-time ModuleNotFoundError and concurrent
# mutation of site-packages.
CORE_RUNTIME_READY = all(importlib.util.find_spec(name) is not None for name in ('fastapi', 'httpx', 'shapely'))


def _load_report_v29_after_report_api():
    if IS_PORTAL:
        return
    for _ in range(320):
        mod = sys.modules.get('report_api')
        if mod is not None and hasattr(mod, 'app') and hasattr(mod, '_analyze_with_live_addons'):
            try:
                import car_resilient  # noqa: F401
                # Raster dependencies belong only to the heavy report worker.
                import rasterio_runtime_bootstrap
                rasterio_runtime_bootstrap.ensure_rasterio()
                # Patch the slow core sources before timing/analyzing any request.
                import prodes_fast_v24  # noqa: F401
                import anm_fast_v29  # noqa: F401
                import report_perf_v24  # noqa: F401
                import core_retry_fast_v29  # noqa: F401
                import report_v18_patch  # noqa: F401
                # Install the V28 property-identity visual hook after the report
                # pipeline is assembled. It patches only future report calls.
                import report_visual_identity_v28  # noqa: F401
                import heavy_live_api_v20  # noqa: F401
                import report_pdf_cache_v21  # noqa: F401
                import report_quick_v22  # noqa: F401
                print('RX_REPORT_V29_RUNTIME=loaded_deferred', flush=True)
            except Exception as exc:
                print(f'RX_REPORT_V29_RUNTIME=failed:{type(exc).__name__}:{str(exc)[:300]}', flush=True)
            return
        time.sleep(0.05)


def _load_portal_v27_deferred():
    """Keep HTTP startup independent from every optional integration.

    No package installer is called here. Missing optional drivers may degrade alerts
    or persistence, but they can never prevent the public portal from binding its
    port. Heavy raster/report modules must remain out of the portal process.
    """
    if not IS_PORTAL:
        return
    for _ in range(600):
        mod = sys.modules.get('portal_api')
        if mod is not None and hasattr(mod, 'PORTAL_HTML') and hasattr(mod, 'app'):
            guard = None
            try:
                import portal_boot_guard_v26 as guard

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
                import portal_resource_guard_v27  # noqa: F401
                import portal_feature_smoke  # noqa: F401
                import portal_map_smoke  # noqa: F401
                import car_resolver_smoke  # noqa: F401

                ready = portal_action_runtime_v25._route_paths()
                missing = [x for x in portal_action_runtime_v25.REQUIRED_ROUTES if x not in ready]
                if missing:
                    raise RuntimeError('missing_required_routes:' + ','.join(missing))
                heavy = portal_resource_guard_v27._heavy_loaded()
                if heavy:
                    raise RuntimeError('heavy_modules_loaded_on_portal:' + ','.join(heavy))
                guard.mark_ready()
                print(f'RX_PORTAL_V27_EXTENSION=loaded_deferred routes:{len(ready)}', flush=True)
            except Exception as exc:
                if guard is not None:
                    try:
                        guard.mark_failed(exc)
                    except Exception:
                        pass
                print(f'RX_PORTAL_V27_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:500]}', flush=True)
            return
        time.sleep(0.05)
    print('RX_PORTAL_V27_EXTENSION=timeout_waiting_portal_api', flush=True)


# During Render's build, core dependencies do not exist yet and this file becomes a
# true no-op. At runtime Uvicorn starts with dependencies already installed.
if CORE_RUNTIME_READY:
    if IS_PORTAL:
        threading.Thread(target=_load_portal_v27_deferred, daemon=True).start()
    else:
        threading.Thread(target=_load_report_v29_after_report_api, daemon=True).start()

import importlib.util
import os
import sys
import threading
import time

IS_PORTAL = os.getenv('RX_RELEASE') == 'V8_OPERATIONAL_ZERO_COST'
CORE_RUNTIME_READY = all(importlib.util.find_spec(name) is not None for name in ('fastapi', 'httpx', 'shapely'))


def _load_report_after_report_api():
    if IS_PORTAL:
        return
    for _ in range(320):
        mod = sys.modules.get('report_api')
        if mod is not None and hasattr(mod, 'app') and hasattr(mod, '_analyze_with_live_addons'):
            try:
                import car_resilient  # noqa: F401
                import rasterio_runtime_bootstrap
                rasterio_runtime_bootstrap.ensure_rasterio()
                import prodes_fast_v24  # noqa: F401
                import anm_fast_v29  # noqa: F401
                import report_perf_v24  # noqa: F401
                import core_retry_fast_v29  # noqa: F401
                import report_v18_patch  # noqa: F401
                import report_visual_identity_v28  # noqa: F401
                import report_extras_perf_v30  # noqa: F401
                import landuse_profile_v39  # noqa: F401
                import heavy_live_api_v20  # noqa: F401
                import report_pdf_cache_v21  # noqa: F401
                import report_quick_v22  # noqa: F401
                print('RX_REPORT_V41_RUNTIME=loaded_deferred anm_retry:off extras_bounded:on sicar_parallel:on landuse_batch:on', flush=True)
            except Exception as exc:
                print(f'RX_REPORT_V41_RUNTIME=failed:{type(exc).__name__}:{str(exc)[:300]}', flush=True)
            return
        time.sleep(0.05)


def _load_portal_deferred():
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
                import property_names_viewport_v30  # noqa: F401
                import map_context  # noqa: F401
                import portal_progressive  # noqa: F401
                import portal_alerts  # noqa: F401
                import portal_intelligence_filters  # noqa: F401
                import portal_rare_earth_locator_upgrade  # noqa: F401
                import portal_rare_earth_symbols  # noqa: F401

                # Route-bearing legacy modules remain registered. Their historical
                # presentation layers are superseded by portal_experience_v43.
                import portal_property_tabs  # noqa: F401
                import portal_live_fix_v18  # noqa: F401
                import portal_smart_search  # noqa: F401
                import portal_map_context_symbols  # noqa: F401
                import portal_mobile_v19  # noqa: F401
                import portal_nationwide_v21  # noqa: F401
                import portal_property_names_v30  # noqa: F401
                import portal_map_visual_v32  # noqa: F401
                import portal_field_mode_v31  # noqa: F401
                import portal_territorial_production_v33  # noqa: F401
                import portal_mining_resilience_v34  # noqa: F401
                import portal_legacy_source_cleanup  # noqa: F401
                import portal_pdf_v21  # noqa: F401
                import portal_action_runtime_v25  # noqa: F401
                import portal_advanced_search_v39  # noqa: F401
                import portal_advanced_name_v40  # noqa: F401
                import portal_incra_certified_v42  # noqa: F401

                # V43 is the single canonical experience layer. Do not re-add the
                # retired visual wrappers (V13/V20/V23/V35/V36/V37/V38/V40).
                import portal_experience_v43  # noqa: F401

                import portal_resource_guard_v27  # noqa: F401
                import portal_feature_smoke  # noqa: F401
                import portal_map_smoke  # noqa: F401
                import car_resolver_smoke  # noqa: F401

                ready = portal_action_runtime_v25._route_paths()
                missing = [x for x in portal_action_runtime_v25.REQUIRED_ROUTES if x not in ready]
                if missing:
                    raise RuntimeError('missing_required_routes:' + ','.join(missing))
                for required in (
                    '/sw.js',
                    '/v1/live/snapshot/{car_code}',
                    '/v1/live/property-identity/{car_code}',
                    '/v1/live/property-names/viewport',
                    '/v1/live/territorial-production/{car_code}',
                    '/v1/live/critical-minerals/{car_code}',
                    '/v1/live/search/advanced',
                    '/v1/live/search/landuse-profiles',
                    '/v1/live/incra-certified/status/{uf}',
                    '/v1/live/incra-certified/viewport',
                ):
                    if required not in ready:
                        raise RuntimeError('missing_portal_route:' + required)
                if 'RX_EXPERIENCE_V43' not in portal_v8.PORTAL_HTML:
                    raise RuntimeError('v43_experience_not_loaded')
                heavy = portal_resource_guard_v27._heavy_loaded()
                if heavy:
                    raise RuntimeError('heavy_modules_loaded_on_portal:' + ','.join(heavy))
                guard.mark_ready()
                print(f'RX_PORTAL_V43_EXTENSION=loaded_deferred routes:{len(ready)} snapshot:on single_surface:on advanced_search:on names:on', flush=True)
            except Exception as exc:
                if guard is not None:
                    try: guard.mark_failed(exc)
                    except Exception: pass
                print(f'RX_PORTAL_V43_EXTENSION=failed:{type(exc).__name__}:{str(exc)[:500]}', flush=True)
            return
        time.sleep(0.05)
    print('RX_PORTAL_V43_EXTENSION=timeout_waiting_portal_api', flush=True)


if CORE_RUNTIME_READY:
    if IS_PORTAL:
        threading.Thread(target=_load_portal_deferred, daemon=True).start()
    else:
        threading.Thread(target=_load_report_after_report_api, daemon=True).start()

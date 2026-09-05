from __future__ import annotations

import portal_v8

# Production identity must describe the experience actually served, not the
# historical portal_v8 base module that V43 composes underneath.
portal_v8.APP_PORTAL_VERSION = '0.43.3-v43-snapshot-first'

html = portal_v8.PORTAL_HTML
_name_count_marker = "setMapState(`${d.features?.length||0} imóvel(is) CAR carregado(s) nesta área"
if _name_count_marker not in html:
    raise RuntimeError('v43_visible_car_counter_injection_point_missing')

# Reuse the SICAR response already loaded for the map. No extra request is made.
# The property-name layer can attach this count to its own coverage telemetry.
html = html.replace(
    _name_count_marker,
    "window.rxVisibleCarCountV43=Number(d.features?.length||0);" + _name_count_marker,
    1,
)

# Do not ask for geolocation permission as soon as the application opens.
# The map must be usable first; location is an explicit user action.
_auto_locate = "setTimeout(locateUser,350)"
if _auto_locate not in html:
    raise RuntimeError('v43_auto_geolocation_injection_point_missing')
html = html.replace(
    _auto_locate,
    "setMapState('Busque um município, mova o mapa ou use “Minha localização”.');setTimeout(()=>loadVisibleParcels(false),420)",
    1,
)

portal_v8.PORTAL_HTML = html

print('RX_RELEASE_V43=0.43.3_snapshot_first_name_coverage_observable_explicit_geolocation', flush=True)
print('RX_GEOLOCATION_V43=explicit_user_action_only', flush=True)

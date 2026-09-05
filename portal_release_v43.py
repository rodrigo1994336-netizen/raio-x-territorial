from __future__ import annotations

import portal_v8

# Production identity must describe the experience actually served, not the
# historical portal_v8 base module that V43 composes underneath.
portal_v8.APP_PORTAL_VERSION = '0.43.4-v43-snapshot-first'

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

# A hidden/zero-sized Leaflet map can temporarily collapse getBounds() to one
# point while the V43 dossier owns the screen. Never send a zero-area viewport
# to SICAR during that transition; wait until the map is visible again.
_viewport_loader = "async function loadVisibleParcels(force){const m=rxMap();if(!m||rxLoading)return;const z=m.getZoom();"
if _viewport_loader not in html:
    raise RuntimeError('v43_viewport_loader_guard_injection_point_missing')
html = html.replace(
    _viewport_loader,
    "async function loadVisibleParcels(force){const m=rxMap();if(!m||rxLoading||document.body.classList.contains('rx43-dossier-open'))return;const z=m.getZoom();",
    1,
)
_viewport_bounds = "const b=m.getBounds();const span=Math.max(b.getEast()-b.getWest(),b.getNorth()-b.getSouth());"
if _viewport_bounds not in html:
    raise RuntimeError('v43_viewport_area_guard_injection_point_missing')
html = html.replace(
    _viewport_bounds,
    "const b=m.getBounds(),west=b.getWest(),south=b.getSouth(),east=b.getEast(),north=b.getNorth();if(!(Number.isFinite(west)&&Number.isFinite(south)&&Number.isFinite(east)&&Number.isFinite(north)&&east>west&&north>south)){setMapState('Mapa ajustando…');return}const span=Math.max(east-west,north-south);",
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

print('RX_RELEASE_V43=0.43.4_snapshot_first_viewport_guard_name_coverage_observable_explicit_geolocation', flush=True)
print('RX_GEOLOCATION_V43=explicit_user_action_only', flush=True)
print('RX_VIEWPORT_GUARD_V43=dossier_and_positive_area', flush=True)

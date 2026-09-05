from __future__ import annotations

import portal_v8

# Production identity must describe the experience actually served, not the
# historical portal_v8 base module that V43 composes underneath.
portal_v8.APP_PORTAL_VERSION = '0.43.1-v43-snapshot-first'

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

portal_v8.PORTAL_HTML = html

print('RX_RELEASE_V43=0.43.1_snapshot_first_name_coverage_ready', flush=True)

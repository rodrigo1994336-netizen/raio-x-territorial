from __future__ import annotations

import portal_v8

# C1 (owner decision, 2026-09-13): no property name is written on the map.
# A name only appears after the user clicks a property, through the card's
# identity path (portal_experience_v43 + portal_identity_title_guard_v49).
# The map therefore no longer requests the property-names viewport route on
# every pan/zoom; that route stays registered for the Stage 2 name-truth gates.

# The base portal must stay explicitly generic until a validated-name path
# passes the CAR cross-check. This is presentation-only and never derives
# a property name from a nearby map label.
portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace(
    "$('#name').textContent=`Imóvel rural • ${p.municipality||'-'}/${p.uf||'-'}`;",
    "$('#name').textContent=`Imóvel rural — ${p.municipality||'-'}/${p.uf||'-'}`;"
)

if 'RX_NAMES_ON_CLICK_ONLY_C1' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace('</body>', '<!-- RX_NAMES_ON_CLICK_ONLY_C1 --></body>')

print('RX_PORTAL_PROPERTY_NAMES_C1=names_on_click_only', flush=True)

from __future__ import annotations

import portal_v8
from alerts_routes import register_alert_routes

app = portal_v8.app
register_alert_routes(app)

# V43 intentionally keeps monitoring routes available without mounting the
# historical alert drawer. Durable 24/7 persistence is not yet bound in
# production, so polling /v1/alerts/summary every 30 seconds would waste
# bandwidth and imply a reliability level the product does not currently have.
# The alert UI returns in the dedicated monitoring phase after persistence,
# snapshots/diffs and read-state are operational and verified.

print('RX_ALERT_ROUTES_V43=registered_ui_dormant_until_durable_persistence', flush=True)

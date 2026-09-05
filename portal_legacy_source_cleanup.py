from __future__ import annotations

import portal_v8

old="Object.entries(ide).forEach(([k,v])=>sourceHtml+=sourceCard('IDE '+k,v));"
new="Object.entries(ide).filter(([k])=>!['slope','declividade','landcover_centro_norte'].includes(String(k).toLowerCase())).forEach(([k,v])=>sourceHtml+=sourceCard('IDE '+k,v));"
if old in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(old,new)

print('RX_LEGACY_IDE_UI=obsolete_slope_landcover_hidden',flush=True)

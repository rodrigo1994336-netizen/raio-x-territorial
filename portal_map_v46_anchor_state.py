from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML


def once(old: str, new: str, error: str) -> None:
    global html
    if html.count(old) != 1:
        raise RuntimeError(error)
    html = html.replace(old, new, 1)


# The card is enriched asynchronously after selection. If the user closes the
# anchor while that request is still in flight, the late response must update the
# selected geometry/data without resurrecting the dismissed popup.
once(
    "let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0;",
    "let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0,anchorOpen=false;",
    "v46_anchor_state_slot_missing",
)
once(
    "function renderAnchor(latlng){const m=mapRef();if(!m||!selected||!latlng)return;",
    "function renderAnchor(latlng){const m=mapRef();if(!anchorOpen||!m||!selected||!latlng)return;",
    "v46_anchor_render_guard_missing",
)
once(
    "function closeAnchor(){const m=mapRef();",
    "function closeAnchor(){anchorOpen=false;const m=mapRef();",
    "v46_anchor_close_guard_missing",
)
once(
    "window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;selected=",
    "window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;anchorOpen=true;selected=",
    "v46_anchor_open_guard_missing",
)

html = html.replace("</body>", "<!-- RX_MAP_V46_ANCHOR_STATE -->\n</body>")
portal_v8.PORTAL_HTML = html

print("RX_MAP_V46_ANCHOR_STATE=late_enrichment_never_reopens_closed_card", flush=True)

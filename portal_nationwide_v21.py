from __future__ import annotations

import portal_v8

html=portal_v8.PORTAL_HTML

# Never pin manual map navigation to the last GPS/search UF. The resilient backend
# resolves the state(s) from the current viewport center and checks real SICAR geometry.
html=html.replace("if(rxLastUf)u.searchParams.set('uf',rxLastUf);","")

# If the user pans while a request is running, don't silently discard the newest
# viewport. Re-run as soon as the in-flight request releases the loading flag.
html=html.replace(
    "async function loadVisibleParcels(force){const m=rxMap();if(!m||rxLoading)return;",
    "async function loadVisibleParcels(force){const m=rxMap();if(!m)return;if(rxLoading){clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(force),280);return;}"
)

# Manual navigation must invalidate any previously remembered UF and react quickly.
html=html.replace(
    "function scheduleParcels(){clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(false),500)}",
    "function scheduleParcels(){rxLastUf=null;clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(false),220)}"
)

# When a city is selected, use its UF only for the immediate first load. Any later
# pan/zoom is again viewport-driven by scheduleParcels().
html=html.replace(
    "setTimeout(()=>loadVisibleParcels(true),350)",
    "setTimeout(()=>loadVisibleParcels(true),180)"
)

# Make the UI state explicit on mobile so users know the system follows the map.
html=html.replace(
    "Carregando imóveis rurais do SICAR na área visível…",
    "Carregando imóveis do SICAR nesta área do Brasil…"
)

portal_v8.PORTAL_HTML=html
print('RX_NATIONWIDE_V21=viewport_driven_all_brazil_no_sticky_uf',flush=True)

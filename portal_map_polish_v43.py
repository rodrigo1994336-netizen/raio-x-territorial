from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML


def once(old: str, new: str, error: str) -> None:
    global html
    if html.count(old) != 1:
        raise RuntimeError(error)
    html = html.replace(old, new, 1)


# Regional agro/water context remains available in the application data model,
# but must not inject automatic markers or network traffic into the default map.
_context_refresh = "async function refresh(){const mm=m();if(!mm||busy||matchMedia('(max-width:720px)').matches)return;busy=true;ensureLegend();"
once(
    _context_refresh,
    "async function refresh(){return;/* RX_CONTEXT_DEFAULT_DISABLED_V43_6 */const mm=m();if(!mm||busy||matchMedia('(max-width:720px)').matches)return;busy=true;ensureLegend();",
    'v43_6_context_default_disable_point_missing',
)

# Replace technical count copy with a shorter action-oriented state.
_old_count = "imóvel(is) CAR carregado(s) nesta área. Clique em um polígono."
if html.count(_old_count) == 1:
    html = html.replace(_old_count, "imóveis rurais nesta área · selecione um limite.", 1)

POLISH = r'''
<style id="rxMapPolishV43">
/* Quiet default map: one visual grammar, no redundant legends. */
#rxMapLegend,#rxFarmNameSource,#rxContextLegend,.hint{display:none!important}
.rx-context-icon{display:none!important;visibility:hidden!important;pointer-events:none!important}
@media(min-width:721px){
  .rx-map-state{max-width:min(430px,calc(100vw - 470px))!important;background:rgba(6,20,14,.91)!important;border-color:#2a493b!important;box-shadow:0 5px 18px #0005!important}
  .rx-locate,.rx-filter-btn{box-shadow:0 6px 18px #0006!important}
  .leaflet-control-zoom{opacity:.88}
}
</style>
<script>
(function(){
  /* All unselected CAR parcels use one restrained cartographic style. */
  window.rxParcelStyle=function(){
    return {color:'#73b99a',weight:1.25,opacity:.82,fillColor:'#73b99a',fillOpacity:.055};
  };
})();
</script>
<!-- RX_MAP_POLISH_V43_6 -->
'''

html = html.replace('</body>', POLISH + '</body>')
portal_v8.PORTAL_HTML = html
portal_v8.APP_PORTAL_VERSION = '0.43.6-v43-snapshot-first'

print('RX_MAP_POLISH_V43=V43_6_single_parcel_style_quiet_default_map', flush=True)
print('RX_CONTEXT_DEFAULT_V43=manual_or_dossier_only', flush=True)

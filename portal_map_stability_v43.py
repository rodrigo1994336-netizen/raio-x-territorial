from __future__ import annotations

import re
import portal_v8

html = portal_v8.PORTAL_HTML


def once(old: str, new: str, error: str) -> None:
    global html
    if html.count(old) != 1:
        raise RuntimeError(error)
    html = html.replace(old, new, 1)


# CAR: keep one stable layer group and reconcile properties instead of blanking
# the complete map on every viewport response.
once(
    "let rxParcelLayer=null, rxLocationMarker=null, rxLastUf=null, rxTimer=null, rxLoading=false;",
    "let rxParcelLayer=null, rxLocationMarker=null, rxLastUf=null, rxTimer=null, rxLoading=false, rxParcelIndex=new Map();",
    'v43_5_parcel_state_injection_point_missing',
)
once(
    "if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}setMapState('Aproxime o mapa para visualizar os limites dos imóveis rurais do CAR.');return}",
    "if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}rxParcelIndex.clear();setMapState('Aproxime o mapa para visualizar os limites dos imóveis rurais do CAR.');return}",
    'v43_5_parcel_clear_injection_point_missing',
)
parcel_start = "if(rxParcelLayer)m.removeLayer(rxParcelLayer);"
parcel_end = "window.rxVisibleCarCountV43=Number(d.features?.length||0);setMapState("
start_i = html.find(parcel_start)
end_i = html.find(parcel_end, start_i + len(parcel_start)) if start_i >= 0 else -1
if start_i < 0 or end_i < 0 or html.find(parcel_start, start_i + 1) >= 0:
    raise RuntimeError('v43_5_parcel_reconcile_injection_point_missing')
parcel_reconcile = r"""if(!rxParcelLayer)rxParcelLayer=L.layerGroup().addTo(m);const rxNextParcels=new Set();const rxParcelStyleFor=f=>(window.rxParcelStyle?window.rxParcelStyle(f):{color:'#48d995',weight:1.4,fillColor:'#48d995',fillOpacity:.075});const rxParcelKey=(f,i)=>{const p=propertyFromFeature(f);return String(p.car_code||f?.id||('anon:'+i+':'+(p.municipality||'')+':'+(p.area_ha??'')))};const rxGeomSig=f=>{try{return JSON.stringify(f?.geometry?.coordinates||[]).length}catch(e){return 0}};const rxBuildParcel=(f,key)=>{const group=L.geoJSON(f,{style:rxParcelStyleFor,onEachFeature:(ff,l)=>{l.bindTooltip('',{sticky:true});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof showProperty==='function')showProperty(p,live.geometry)})}});group.__rxGeomSig=rxGeomSig(f);group.eachLayer(l=>{const p=propertyFromFeature(f);l.feature=f;l.setTooltipContent?.(`<b>${p.municipality||'Imóvel rural'}</b><br>${p.area_ha??'—'} ha`)});group.addTo(rxParcelLayer);rxParcelIndex.set(key,group);return group};(d.features||[]).forEach((f,i)=>{const key=rxParcelKey(f,i);rxNextParcels.add(key);let group=rxParcelIndex.get(key),sig=rxGeomSig(f);if(!group){rxBuildParcel(f,key);return}if(group.__rxGeomSig!==sig){const fresh=rxBuildParcel(f,key);try{rxParcelLayer.removeLayer(group)}catch(e){}rxParcelIndex.set(key,fresh);return}group.eachLayer(l=>{l.feature=f;try{l.setStyle?.(rxParcelStyleFor(f))}catch(e){}const p=propertyFromFeature(f);try{l.setTooltipContent?.(`<b>${p.municipality||'Imóvel rural'}</b><br>${p.area_ha??'—'} ha`)}catch(e){}})});for(const [key,group] of [...rxParcelIndex.entries()]){if(rxNextParcels.has(key))continue;try{rxParcelLayer.removeLayer(group)}catch(e){}rxParcelIndex.delete(key)}"""
html = html[:start_i] + parcel_reconcile + html[end_i:]

# Names: V43 originally replaced the whole render function for incremental marker
# reconciliation. V44 introduced a stricter semantic split between VALIDATED_PROPERTY_NAME
# and REFERENCE. Replacing that renderer would silently reintroduce the old truth bug.
# Therefore V44 keeps its own renderer intact; this module only applies density/clear/zoom
# stability around it. The legacy renderer replacement remains available only when V44 is
# not present.
v44_name_truth = 'RX_PROPERTY_NAMES_V44_TRUTH_UI' in html
once(
    "let nameLayer=null,timer=null,lastKey='',seq=0;",
    "let nameLayer=null,timer=null,lastKey='',seq=0,nameIndex=new Map();",
    'v43_5_name_state_injection_point_missing',
)
once(
    "function limitFor(z){if(field())return z>=15?24:z>=13?16:z>=12?10:6;return z>=15?60:z>=13?35:z>=12?22:12}",
    "function mobileMap(){return matchMedia('(max-width:720px)').matches}function limitFor(z){if(mobileMap())return z>=16?10:z>=14?7:z>=13?5:z>=12?3:0;if(field())return z>=15?24:z>=13?16:z>=12?10:6;return z>=15?48:z>=13?28:z>=12?16:8}",
    'v43_5_name_density_injection_point_missing',
)
once(
    "function clearNames(){const m=getMap();if(m&&nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=null;sourceBadge('')}",
    "function clearNames(){const m=getMap();if(m&&nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=null;nameIndex.clear();sourceBadge('')}",
    'v43_5_name_clear_injection_point_missing',
)
if v44_name_truth:
    # Preserve V44's isValidated/openValidatedProperty/openReference renderer verbatim.
    once(
        "const m=getMap();if(!m||dossierOpen())return;const z=m.getZoom();if(z<11){clearNames();return}",
        "const m=getMap();if(!m||dossierOpen())return;const z=m.getZoom();if(z<(mobileMap()?12:11)){clearNames();return}",
        'v43_5_v44_name_zoom_injection_point_missing',
    )
else:
    name_pattern = re.compile(r"function render\(items,z\)\{.*?return rendered\}\s*async function refresh", re.S)
    if len(name_pattern.findall(html)) != 1:
        raise RuntimeError('v43_5_name_reconcile_injection_point_missing')
    name_render = r"""function render(items,z){const m=getMap();if(!m)return 0;if(!nameLayer)nameLayer=L.layerGroup().addTo(m);const limit=limitFor(z),next=new Set();let rendered=0;const mkIcon=x=>{const label=esc(x.name),low=z<=12?' rx-name-lowzoom':'';return L.divIcon({className:'rx-farm-name-icon',html:`<div class=\"rx-farm-name-label${low}\" title=\"${label}\">${label}</div>`,iconSize:[1,1],iconAnchor:[0,0]})};(items||[]).slice(0,limit).forEach(x=>{const lat=Number(x?.center?.lat),lon=Number(x?.center?.lon);if(!Number.isFinite(lat)||!Number.isFinite(lon)||!x?.name)return;const key=String(x.registry||x.car_code||x.name)+'|'+lat.toFixed(5)+'|'+lon.toFixed(5);next.add(key);let mk=nameIndex.get(key);if(!mk){mk=L.marker([lat,lon],{icon:mkIcon(x),keyboard:true,riseOnHover:true});mk.__rxName=x;mk.on('click',()=>openNamedProperty(mk.__rxName));mk.addTo(nameLayer);nameIndex.set(key,mk)}else{mk.__rxName=x;mk.setLatLng([lat,lon]);mk.setIcon(mkIcon(x))}const tip=`${esc(x.name)}${x.municipality?' · '+esc(x.municipality):''}${x.uf?' / '+esc(x.uf):''}${x.registry?' · Matrícula '+esc(x.registry):''}`;if(mk.getTooltip?.())mk.setTooltipContent(tip);else mk.bindTooltip(tip,{direction:'top',offset:[0,-8]});rendered++});for(const [key,mk] of [...nameIndex.entries()]){if(next.has(key))continue;try{nameLayer.removeLayer(mk)}catch(e){}nameIndex.delete(key)}return rendered}
  async function refresh"""
    html = name_pattern.sub(lambda _: name_render, html, count=1)
    once(
        "async function refresh(){const m=getMap();if(!m||dossierOpen())return;const z=m.getZoom();if(z<11){clearNames();return}",
        "async function refresh(){const m=getMap();if(!m||dossierOpen())return;const z=m.getZoom();if(z<(mobileMap()?12:11)){clearNames();return}",
        'v43_5_name_zoom_injection_point_missing',
    )

# Automatic regional context is secondary on phones. Desktop swaps the new set
# before removing the old set to avoid a visible blank frame.
once(
    "async function refresh(){const mm=m();if(!mm||busy)return;busy=true;ensureLegend();",
    "async function refresh(){const mm=m();if(!mm||busy||matchMedia('(max-width:720px)').matches)return;busy=true;ensureLegend();",
    'v43_5_context_mobile_injection_point_missing',
)
once(
    "try{if(ctxLayer)mm.removeLayer(ctxLayer);ctxLayer=L.layerGroup().addTo(mm);const groups=parcelMunicipalities();",
    "try{const previousCtx=ctxLayer;ctxLayer=L.layerGroup();const groups=parcelMunicipalities();",
    'v43_5_context_swap_injection_point_missing',
)
once(
    "const c=mm.getCenter();try{const r=await fetch(`/v1/map/groundwater-context?lat=${c.lat}&lon=${c.lng}&radius_km=20`,{cache:'no-store'}),d=await r.json();",
    "ctxLayer.addTo(mm);if(previousCtx)mm.removeLayer(previousCtx);const c=mm.getCenter();try{const r=await fetch(`/v1/map/groundwater-context?lat=${c.lat}&lon=${c.lng}&radius_km=20`,{cache:'no-store'}),d=await r.json();",
    'v43_5_context_commit_injection_point_missing',
)

# Rare-earth marker layer duplicates the explicit polygon filter on mobile. Keep
# the filter, suppress the extra marker/panel surface, remove auto-fit, and swap
# desktop marker sets new-before-old.
once(
    "async function refresh(){const mm=m(),tog=q('#rxMineralToggle'),sel=q('#rxMineralSelect'),panel=ensurePanel();if(!mm||!tog||!sel||!panel)return;",
    "async function refresh(){const mm=m(),tog=q('#rxMineralToggle'),sel=q('#rxMineralSelect'),panel=ensurePanel();if(!mm||!tog||!sel||!panel)return;if(matchMedia('(max-width:720px)').matches){clear();return}",
    'v43_5_rare_mobile_injection_point_missing',
)
once(
    "if(markerLayer)mm.removeLayer(markerLayer);markerLayer=L.layerGroup().addTo(mm);const interests=[];",
    "const previousMarkers=markerLayer;markerLayer=L.layerGroup();const interests=[];",
    'v43_5_rare_swap_start_missing',
)
once(
    "lastCount=(d.features||[]).length;q('#rxRareCount').textContent=String(lastCount);",
    "markerLayer.addTo(mm);if(previousMarkers)mm.removeLayer(previousMarkers);lastCount=(d.features||[]).length;q('#rxRareCount').textContent=String(lastCount);",
    'v43_5_rare_commit_injection_point_missing',
)
once(
    "if(lastCount&&markerLayer&&!autoFitDone){const bounds=L.featureGroup(markerLayer.getLayers()).getBounds();if(bounds.isValid()){autoFitDone=true;mm.fitBounds(bounds.pad(.22),{maxZoom:11,animate:true})}}",
    "autoFitDone=true;",
    'v43_5_rare_autofit_injection_point_missing',
)

# Explicit ANM polygons also use new-before-old swap.
once(
    "if(anmLayer)mm.removeLayer(anmLayer);anmLayer=L.geoJSON(a,",
    "const previousAnm=anmLayer;const nextAnm=L.geoJSON(a,",
    'v43_5_anm_swap_start_missing',
)
once(
    "}).addTo(mm);wmsLayers.forEach(x=>{try{mm.removeLayer(x)}catch(e){}});",
    "});nextAnm.addTo(mm);if(previousAnm)mm.removeLayer(previousAnm);anmLayer=nextAnm;wmsLayers.forEach(x=>{try{mm.removeLayer(x)}catch(e){}});",
    'v43_5_anm_swap_commit_missing',
)

MOBILE_CLEANUP = r'''
<style id="rxMapStabilityV43">
@media(max-width:720px){
 #rxMapLegend,#rxFarmNameSource,#rxContextLegend,.rx-rare-mode,.leaflet-control-zoom{display:none!important}
 .rx-context-icon,.rx-rare-marker{display:none!important;visibility:hidden!important;pointer-events:none!important}
 .rx-locate,.rx-filter-btn{bottom:max(14px,env(safe-area-inset-bottom))!important;width:44px!important;height:44px!important;border-radius:14px!important;box-shadow:0 8px 24px #0008!important}
 .rx-locate{left:12px!important}.rx-filter-btn{left:64px!important}
 .rx-filter-panel{left:8px!important;right:8px!important;bottom:calc(max(68px,env(safe-area-inset-bottom) + 58px))!important;max-height:55dvh!important;border-radius:18px!important}
 .rx-map-state{top:8px!important;left:10px!important;right:10px!important;max-width:none!important;min-height:0!important;padding:6px 9px!important;background:rgba(6,20,14,.88)!important;box-shadow:0 4px 16px #0005!important}
 .rx-net-state.show{right:10px!important;bottom:max(15px,env(safe-area-inset-bottom))!important;max-width:calc(100vw - 126px)!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important}
 .leaflet-control-attribution{font-size:6px!important;line-height:1.2!important;max-width:62vw!important;background:rgba(255,255,255,.78)!important}
 .rx-farm-name-label{box-shadow:0 3px 10px #0007!important;backdrop-filter:none!important}
}
</style>
<!-- RX_MAP_STABILITY_V43_5 -->
'''

html = html.replace('</body>', MOBILE_CLEANUP + '</body>')
portal_v8.PORTAL_HTML = html
portal_v8.APP_PORTAL_VERSION = '0.43.5-v43-snapshot-first'
print('RX_MAP_STABILITY_V43=V43_5_incremental_layers_mobile_hierarchy_v44_name_truth_safe', flush=True)

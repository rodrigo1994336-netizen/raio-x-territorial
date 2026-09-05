from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML

_old = "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);"
_new = r'''const rxStreetLayer=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxNativeZoom:19,maxZoom:20,keepBuffer:4,updateWhenIdle:false,attribution:'© OpenStreetMap contributors'});
const rxSatelliteLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:19,maxZoom:20,keepBuffer:4,updateWhenIdle:false,attribution:'Tiles © Esri · Maxar · Earthstar Geographics · GIS User Community'});
const rxRoadLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:19,maxZoom:20,keepBuffer:4,updateWhenIdle:false,attribution:'Esri',pane:'overlayPane'});
const rxPlaceLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:19,maxZoom:20,keepBuffer:4,updateWhenIdle:false,attribution:'Esri',pane:'overlayPane'});
function rxSetBasemapV43(mode,persist=true){
  const satellite=mode!=='street';
  [rxStreetLayer,rxSatelliteLayer,rxRoadLayer,rxPlaceLayer].forEach(x=>{if(map.hasLayer(x))map.removeLayer(x)});
  if(satellite){rxSatelliteLayer.addTo(map);rxRoadLayer.addTo(map);rxPlaceLayer.addTo(map)}else rxStreetLayer.addTo(map);
  window.rxBasemapModeV43=satellite?'satellite':'street';
  if(persist){try{localStorage.setItem('rx-basemap-v43',window.rxBasemapModeV43)}catch(e){}}
  document.querySelectorAll('.rx-basemap-switch button').forEach(b=>b.classList.toggle('active',b.dataset.mode===window.rxBasemapModeV43));
}
window.rxSetBasemapV43=rxSetBasemapV43;
let rxInitialBasemap='satellite';try{const saved=localStorage.getItem('rx-basemap-v43');if(saved==='street'||saved==='satellite')rxInitialBasemap=saved}catch(e){}
rxSetBasemapV43(rxInitialBasemap,false);
const rxBasemapControl=L.control({position:'bottomleft'});
rxBasemapControl.onAdd=function(){
  const box=L.DomUtil.create('div','rx-basemap-switch leaflet-control');
  box.innerHTML='<button type="button" data-mode="satellite" aria-label="Usar mapa por satélite">SATÉLITE</button><button type="button" data-mode="street" aria-label="Usar mapa de ruas">MAPA</button>';
  L.DomEvent.disableClickPropagation(box);L.DomEvent.disableScrollPropagation(box);
  box.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>rxSetBasemapV43(b.dataset.mode,true)));
  setTimeout(()=>rxSetBasemapV43(window.rxBasemapModeV43||'satellite',false),0);
  return box;
};
rxBasemapControl.addTo(map);
window.rxBasemapLayersV43={street:rxStreetLayer,satellite:rxSatelliteLayer,roads:rxRoadLayer,places:rxPlaceLayer};'''

if html.count(_old) != 1:
    raise RuntimeError('v43_9_basemap_injection_point_missing')
html = html.replace(_old, _new, 1)

POLISH = r'''
<style id="rxHybridBasemapV43">
.rx-basemap-switch{display:flex!important;gap:3px!important;padding:4px!important;background:rgba(7,20,14,.92)!important;border:1px solid rgba(255,255,255,.18)!important;border-radius:12px!important;box-shadow:0 8px 26px #0008!important;backdrop-filter:blur(9px)!important;overflow:hidden!important}
.rx-basemap-switch button{height:34px!important;padding:0 11px!important;border:0!important;border-radius:8px!important;background:transparent!important;color:#d7e4dc!important;font-size:9px!important;font-weight:900!important;letter-spacing:.55px!important;cursor:pointer!important}
.rx-basemap-switch button.active{background:#f5fbf7!important;color:#102018!important;box-shadow:0 2px 9px #0004!important}
.leaflet-tile-pane{filter:saturate(1.03) contrast(1.015)}
.leaflet-control-attribution{background:rgba(5,13,9,.68)!important;color:#dce8e1!important;font-size:8px!important;backdrop-filter:blur(4px)!important}
.leaflet-control-attribution a{color:#dce8e1!important}
@media(max-width:720px){
 .rx-basemap-switch{margin-left:120px!important;margin-bottom:8px!important;padding:3px!important;border-radius:10px!important}
 .rx-basemap-switch button{height:31px!important;padding:0 9px!important;font-size:8px!important}
 .leaflet-control-attribution{max-width:68vw!important;white-space:normal!important;line-height:1.15!important}
}
body.rx43-dossier-open .rx-basemap-switch{display:none!important}
</style>
<!-- RX_HYBRID_BASEMAP_V43_9 -->
<!-- RX_HYBRID_BASEMAP_MOBILE_SPACING_V43_9_2 -->
'''

html = html.replace('</body>', POLISH + '</body>')
portal_v8.PORTAL_HTML = html
portal_v8.APP_PORTAL_VERSION = '0.43.8-v43-snapshot-first'

print('RX_HYBRID_BASEMAP_V43=V43_9_2_satellite_default_mobile_controls_clear', flush=True)

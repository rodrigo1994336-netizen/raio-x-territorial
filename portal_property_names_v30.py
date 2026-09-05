from __future__ import annotations

import portal_v8

UI = r'''
<style>
.rx-farm-name-icon{background:transparent;border:0;overflow:visible}
.rx-farm-name-label{display:inline-flex;align-items:center;gap:5px;transform:translate(-50%,-50%);padding:5px 8px;border-radius:8px;background:rgba(5,25,17,.92);border:1px solid rgba(95,226,161,.72);box-shadow:0 5px 16px rgba(0,0,0,.42);color:#f0fff7;font-size:9px;font-weight:850;line-height:1.15;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;backdrop-filter:blur(5px)}
.rx-farm-name-label:before{content:'◆';font-size:7px;color:#63e6a4;flex:0 0 auto}.rx-farm-name-label:hover{background:rgba(7,40,26,.98);border-color:#83f0ba;z-index:1000}
.rx-farm-name-source{position:absolute;z-index:790;left:14px;bottom:14px;padding:6px 9px;border:1px solid #29493b;border-radius:8px;background:rgba(6,22,15,.88);color:#8ca89a;font-size:8px;pointer-events:none;box-shadow:0 6px 18px #0006}
@media(max-width:720px){.rx-farm-name-label{font-size:8px;max-width:150px;padding:4px 6px}.rx-farm-name-source{left:9px;bottom:9px;font-size:7px}.rx-farm-name-label.rx-name-lowzoom{font-size:7px;max-width:118px;padding:3px 5px}}
</style>
<script>
(function(){
  let nameLayer=null,timer=null,lastKey='',seq=0;
  function getMap(){try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}}
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function sourceBadge(text){let el=document.querySelector('#rxFarmNameSource');if(!el){el=document.createElement('div');el.id='rxFarmNameSource';el.className='rx-farm-name-source';document.querySelector('.main')?.appendChild(el)}if(el)el.textContent=text||''}
  function clearNames(){const m=getMap();if(m&&nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=null;sourceBadge('')}
  async function openNamedProperty(x){const m=getMap();if(!m||!x?.center)return;const lat=Number(x.center.lat),lon=Number(x.center.lon);try{const r=await fetch(`/v1/live/resolve?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`),d=await r.json();if(r.ok&&d?.property?.car_code&&typeof showProperty==='function'){d.property.name=x.name;showProperty(d.property,d.geometry);return}}catch(e){}if(typeof toast==='function')toast(`${x.name}: área pública localizada; nenhum CAR correspondente foi confirmado neste ponto.`)}
  function render(items,z){const m=getMap();if(!m)return;if(nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=L.layerGroup();const limit=z>=15?60:z>=13?35:z>=12?22:12;(items||[]).slice(0,limit).forEach(x=>{const lat=Number(x?.center?.lat),lon=Number(x?.center?.lon);if(!Number.isFinite(lat)||!Number.isFinite(lon)||!x?.name)return;const label=esc(x.name),low=z<=11?' rx-name-lowzoom':'';const icon=L.divIcon({className:'rx-farm-name-icon',html:`<div class="rx-farm-name-label${low}" title="${label}">${label}</div>`,iconSize:[1,1],iconAnchor:[0,0]});const mk=L.marker([lat,lon],{icon,keyboard:true,riseOnHover:true});mk.on('click',()=>openNamedProperty(x));mk.bindTooltip(`${esc(x.name)}${x.municipality?' · '+esc(x.municipality):''}${x.uf?' / '+esc(x.uf):''}${x.registry?' · Matrícula '+esc(x.registry):''}`,{direction:'top',offset:[0,-8]});nameLayer.addLayer(mk)});nameLayer.addTo(m)}
  async function refresh(){const m=getMap();if(!m)return;const z=m.getZoom();if(z<11){clearNames();return}const b=m.getBounds(),span=Math.max(b.getEast()-b.getWest(),b.getNorth()-b.getSouth());if(span>1.50){clearNames();return}const key=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(v=>v.toFixed(3)).join('|')+'|'+z;if(key===lastKey&&nameLayer)return;lastKey=key;const my=++seq;const u=new URL('/v1/live/property-names/viewport',location.origin);u.searchParams.set('west',b.getWest());u.searchParams.set('south',b.getSouth());u.searchParams.set('east',b.getEast());u.searchParams.set('north',b.getNorth());u.searchParams.set('limit',z>=15?'60':z>=13?'35':z>=12?'22':'12');try{const r=await fetch(u),d=await r.json();if(my!==seq)return;if(!r.ok||!d.ok){clearNames();return}render(d.items||[],z);sourceBadge(d.count?`${d.count} nome(s) público(s) · SIGEF/INCRA`:'Sem denominações públicas SIGEF nesta área')}catch(e){if(my===seq)clearNames()}}
  function schedule(){clearTimeout(timer);timer=setTimeout(refresh,360)}
  function install(){const m=getMap();if(!m){setTimeout(install,300);return}m.on('moveend',schedule);m.on('zoomend',schedule);setTimeout(refresh,650)}
  window.rxRefreshVisiblePropertyNames=refresh;if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'RX_PROPERTY_NAMES_V40' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace('</body>', UI + '<!-- RX_PROPERTY_NAMES_V40 --></body>')

print('RX_PORTAL_PROPERTY_NAMES_V40=earlier_sigef_names_controlled_density', flush=True)

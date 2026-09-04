from __future__ import annotations

import portal_v8

UI=r'''
<script>
(function(){
let nationalLayers=[],busy=false;
function q(s){return document.querySelector(s)}
function mapRef(){try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}}
function clearNational(){const m=mapRef();nationalLayers.forEach(x=>{try{m.removeLayer(x)}catch(e){}});nationalLayers=[]}
async function syncNational(){const m=mapRef(),toggle=q('#rxMineralToggle'),sel=q('#rxMineralSelect'),status=q('#rxMineralStatus');if(!m||!toggle||!sel)return;if(!toggle.checked){clearNational();return}const b=m.getBounds(),span=Math.max(b.getEast()-b.getWest(),b.getNorth()-b.getSouth());if(span<=8){clearNational();return}if(busy)return;busy=true;try{const r=await fetch(`/v1/map/minerals/layers?mineral=${encodeURIComponent(sel.value)}`),d=await r.json();clearNational();if(d.ok&&(d.layers||[]).length){(d.layers||[]).slice(0,4).forEach(x=>{nationalLayers.push(L.tileLayer.wms(d.wms_url,{layers:x.name,format:'image/png',transparent:true,opacity:.40,version:'1.1.1'}).addTo(m))});if(status)status.textContent=`${d.layers.length} camada(s) SGB para ${sel.options[sel.selectedIndex]?.text||sel.value}. Aproxime o mapa para acrescentar os processos ANM.`}else if(status)status.textContent='SGB não devolveu camadas para este mineral nesta consulta.'}catch(e){if(status)status.textContent='SGB parcial: '+e.message}finally{busy=false}}
function install(){const m=mapRef();if(!m||!q('#rxMineralToggle')){setTimeout(install,300);return}q('#rxMineralToggle').addEventListener('change',()=>setTimeout(syncNational,100));q('#rxMineralSelect').addEventListener('change',()=>setTimeout(syncNational,100));m.on('moveend',()=>setTimeout(syncNational,180));m.on('zoomend',()=>setTimeout(syncNational,180))}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'nationalLayers' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'</body>')

print('RX_RARE_EARTH_LOCATOR=national_sgb_plus_local_anm',flush=True)

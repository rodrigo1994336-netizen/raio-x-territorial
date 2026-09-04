from __future__ import annotations

import portal_v8

UI=r'''
<style>
.rx-context-icon{width:34px;height:34px;border-radius:12px;background:rgba(6,24,16,.94);border:1px solid #35594a;box-shadow:0 5px 18px #0008;display:grid;place-items:center;font-size:17px;position:relative}.rx-context-icon small{position:absolute;right:-5px;bottom:-5px;min-width:18px;height:18px;padding:0 4px;border-radius:999px;background:#63e6a5;color:#052116;border:2px solid #07150f;font-size:7px;font-weight:950;display:grid;place-items:center}.rx-context-icon.water{border-color:#4ea9e8;background:rgba(5,21,36,.94)}.rx-context-icon.water small{background:#64c5ff}.rx-context-tip{font-size:9px;line-height:1.45}.rx-context-tip b{font-size:10px}.rx-map-legend-mini{position:absolute;z-index:1650;right:14px;bottom:18px;background:rgba(7,20,14,.93);border:1px solid #244136;border-radius:11px;padding:7px 9px;color:#b9cec3;font-size:8px;box-shadow:0 8px 24px #0006}.rx-map-legend-mini span{margin-right:9px;white-space:nowrap}
@media(max-width:720px){.rx-map-legend-mini{right:10px;bottom:10px;max-width:55%;line-height:1.8}}
</style>
<script>
(function(){
let ctxLayer=null,waterMarker=null,timer=null,busy=false;const cache=new Map();
function m(){try{return (typeof map!=='undefined'&&map&&map.eachLayer)?map:null}catch(e){return null}}
function ensureLegend(){if(document.querySelector('#rxContextLegend'))return;const main=document.querySelector('.main');if(!main)return;const d=document.createElement('div');d.id='rxContextLegend';d.className='rx-map-legend-mini';d.innerHTML='<span>🐂 pecuária regional</span><span>🌾 safras</span><span>💧 água subterrânea</span><span>◆ terras raras</span>';main.appendChild(d)}
function icon(kind,count){const symbol=kind==='water'?'💧':'🐂';return L.divIcon({className:'',html:`<div class="rx-context-icon ${kind==='water'?'water':''}">${symbol}<small>${count??''}</small></div>`,iconSize:[38,38],iconAnchor:[19,19]})}
function parcelMunicipalities(){const mm=m(),out=new Map();if(!mm)return out;mm.eachLayer(l=>{const p=l?.feature?.properties||{};const code=String(p.cod_imovel||'');const mt=code.match(/^[A-Z]{2}-(\d{7})-/);if(!mt||out.has(mt[1]))return;try{const c=l.getBounds?.().getCenter?.();if(c)out.set(mt[1],{center:c,municipality:p.municipio||'',uf:p.uf||''})}catch(e){}});return out}
async function agro(mun){if(cache.has(mun))return cache.get(mun);const r=await fetch(`/v1/map/agro-context?municipality_code=${encodeURIComponent(mun)}`,{cache:'no-store'}),d=await r.json();if(r.ok)cache.set(mun,d);return d}
async function refresh(){const mm=m();if(!mm||busy)return;busy=true;ensureLegend();try{if(ctxLayer)mm.removeLayer(ctxLayer);ctxLayer=L.layerGroup().addTo(mm);const groups=parcelMunicipalities();for(const [mun,meta] of [...groups.entries()].slice(0,4)){try{const d=await agro(mun),b=d?.bovines,crops=d?.crops||[];if(b){const n=Math.round(Number(b.value)||0);const mk=L.marker(meta.center,{icon:icon('cattle',n>=100000?Math.round(n/1000)+'k':(n>=1000?Math.round(n/1000)+'k':n)),zIndexOffset:450}).addTo(ctxLayer);mk.bindPopup(`<div class="rx-context-tip"><b>🐂 Contexto pecuário regional</b><br>${meta.municipality||mun}${meta.uf?' / '+meta.uf:''}<br><b>${Number(b.value||0).toLocaleString('pt-BR')} bovinos</b> · ${b.period||'ano recente'}<br>${crops.length?'<br>🌾 Culturas regionais: '+crops.slice(0,4).map(x=>x.product).join(', '):''}<br><small>Dado municipal IBGE/PPM/PAM. Não garante aptidão de uma fazenda específica.</small></div>`)}else if(crops.length){const mk=L.marker(meta.center,{icon:L.divIcon({className:'',html:'<div class="rx-context-icon">🌾<small>'+crops.length+'</small></div>',iconSize:[38,38],iconAnchor:[19,19]}),zIndexOffset:420}).addTo(ctxLayer);mk.bindPopup(`<div class="rx-context-tip"><b>🌾 Contexto agrícola regional</b><br>${crops.slice(0,5).map(x=>x.product).join(', ')}<br><small>IBGE/PAM. O cultivo efetivo dentro do imóvel deve ser confirmado por uso/cobertura e vistoria.</small></div>`)}}catch(e){}}
const c=mm.getCenter();try{const r=await fetch(`/v1/map/groundwater-context?lat=${c.lat}&lon=${c.lng}&radius_km=20`,{cache:'no-store'}),d=await r.json();if(waterMarker)mm.removeLayer(waterMarker);waterMarker=null;if(r.ok&&d.ok&&Number(d.well_count||0)>0){waterMarker=L.marker(c,{icon:icon('water',d.well_count),zIndexOffset:400}).addTo(mm).bindPopup(`<div class="rx-context-tip"><b>💧 Evidência hidrogeológica regional</b><br>${d.well_count} poço(s) SIAGAS em até ${d.search_radius_km} km<br>Profundidade mediana: <b>${d.well_depth_median_m??'—'} m</b><br>Nível estático mediano: <b>${d.static_water_level_median_m??'—'} m</b><br><small>Poços vizinhos não garantem água na mesma profundidade dentro do imóvel.</small></div>`)}}catch(e){}
}finally{busy=false}}
function schedule(){clearTimeout(timer);timer=setTimeout(refresh,1200)}
function install(){const mm=m();if(!mm){setTimeout(install,300);return}ensureLegend();mm.on('moveend',schedule);mm.on('zoomend',schedule);setTimeout(refresh,1600)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'rxContextLegend' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'</body>')

print('RX_MAP_CONTEXT_SYMBOLS=cattle_crops_groundwater_discreet',flush=True)

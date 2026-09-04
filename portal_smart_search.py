from __future__ import annotations

import portal_v8

UI=r'''
<style>
.rx-smart-results{position:fixed;z-index:2400;top:61px;left:252px;width:min(680px,calc(100vw - 520px));background:#081711;border:1px solid #28473a;border-radius:13px;box-shadow:0 22px 60px #000b;overflow:hidden}.rx-smart-title{padding:8px 12px;color:#8da79a;font-size:8px;font-weight:850;letter-spacing:.8px;border-bottom:1px solid #20382e}.rx-smart-item{display:block;width:100%;border:0;border-bottom:1px solid #20382e;background:#081711;color:#edf8f2;text-align:left;padding:10px 12px;cursor:pointer}.rx-smart-item:hover{background:#10251b}.rx-smart-item b{font-size:10px}.rx-smart-item small{display:block;color:#91aa9e;font-size:8px;margin-top:3px}.rx-smart-badge{display:inline-block;background:#153c2a;color:#77e9ad;border-radius:999px;padding:3px 6px;font-size:7px;font-weight:900;margin-right:5px}.rx-name-label{background:rgba(5,24,16,.92);border:1px solid #58d99c;color:#ecfff5;border-radius:7px;padding:3px 6px;font-size:8px;font-weight:850;box-shadow:0 5px 15px #0008;white-space:nowrap}
@media(max-width:720px){.rx-smart-results{left:9px;right:9px;top:58px;width:auto}}
</style>
<script>
(function(){
let resultLayer=null,bound=false;
const q=s=>document.querySelector(s);
function m(){try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}}
function clear(){q('#rxSmartResults')?.remove()}
function card(items,term,title='RESULTADOS'){clear();const box=document.createElement('div');box.id='rxSmartResults';box.className='rx-smart-results';box.innerHTML=`<div class="rx-smart-title">${title} PARA “${String(term).replace(/[<>]/g,'')}”</div>`;(items||[]).forEach(x=>{const b=document.createElement('button');b.className='rx-smart-item';b.innerHTML=`<span class="rx-smart-badge">${x.type==='car'?'CAR':(x.type==='city'?'CIDADE':'SIGEF')}</span><b>${x.name||'Imóvel rural'}</b><small>${x.municipality||x.state||''}${x.uf?' / '+x.uf:''}${x.area_ha!=null?' · '+x.area_ha+' ha':''}${x.registry?' · registro '+x.registry:''}</small>`;b.onclick=()=>x.type==='city'?openCity(x):openResult(x);box.appendChild(b)});if(!(items||[]).length){const d=document.createElement('div');d.className='rx-smart-item';d.innerHTML='<small>Nenhum resultado encontrado nas bases disponíveis.</small>';box.appendChild(d)}document.body.appendChild(box)}
async function openCity(x){clear();const mm=m();if(!mm)return;mm.setView([Number(x.lat),Number(x.lon)],13);const inp=q('#q');if(inp)inp.value='';setTimeout(()=>{try{mm.fire('moveend')}catch(e){}},400)}
async function openResult(x){clear();const mm=m();if(!mm)return;if(resultLayer){try{mm.removeLayer(resultLayer)}catch(e){}}if(x.geometry){resultLayer=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#65e6a5',weight:3,fillColor:'#65e6a5',fillOpacity:.10}}).addTo(mm);const bd=resultLayer.getBounds();if(bd.isValid())mm.fitBounds(bd.pad(.18),{maxZoom:15})}else if(x.center)mm.setView([x.center.lat,x.center.lon],14);if(x.car_code&&typeof loadCar==='function'){loadCar(x.car_code).catch(e=>typeof toast==='function'&&toast(e.message));return}if(x.center){try{const r=await fetch(`/v1/live/resolve?lat=${encodeURIComponent(x.center.lat)}&lon=${encodeURIComponent(x.center.lon)}`),d=await r.json();if(r.ok&&d.property?.car_code&&typeof showProperty==='function'){d.property.name=x.name;showProperty(d.property,d.geometry);return}}catch(e){}}if(resultLayer&&x.name){try{resultLayer.bindTooltip(x.name,{permanent:true,direction:'center',className:'rx-name-label'}).openTooltip()}catch(e){}}if(typeof toast==='function')toast('Área localizada pelo SIGEF. O CAR será aberto quando houver correspondência espacial pública.')}
async function cityFallback(term){try{const r=await fetch(`/v1/live/cities?q=${encodeURIComponent(term)}`),d=await r.json();if(r.ok&&d.items?.length){card(d.items.map(x=>({...x,type:'city',name:x.name||x.display_name})),term,'MUNICÍPIOS');return true}}catch(e){}return false}
async function coordinateSearch(raw){const mth=String(raw).trim().match(/^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/);if(!mth)return false;const lat=Number(mth[1].replace(',','.')),lon=Number(mth[2].replace(',','.'));if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return false;try{const r=await fetch(`/v1/live/resolve?lat=${lat}&lon=${lon}`),d=await r.json();const mm=m();if(mm)mm.setView([lat,lon],15);if(r.ok&&d.property?.car_code&&typeof showProperty==='function'){showProperty(d.property,d.geometry);return true}if(typeof toast==='function')toast('Coordenada localizada; nenhum CAR exato foi encontrado neste ponto.');return true}catch(e){return false}}
async function search(term){const raw=String(term||'').trim();if(!raw)return;clear();if(await coordinateSearch(raw))return;if(typeof carPattern==='function'&&carPattern(raw)){if(typeof loadCar==='function')return loadCar(raw.toUpperCase());}try{const r=await fetch(`/v1/live/search/properties?q=${encodeURIComponent(raw)}&limit=20`),d=await r.json();if(r.ok&&d.items?.length){card(d.items,raw);return}if(!(await cityFallback(raw)))card([],raw)}catch(e){if(!(await cityFallback(raw)))card([],raw)}}
function install(){const go=q('#go'),inp=q('#q');if(!go||!inp){setTimeout(install,300);return}if(bound)return;bound=true;inp.placeholder='Fazenda, CAR, cidade, coordenada ou identificador';go.onclick=()=>search(inp.value);inp.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();search(inp.value)}});document.addEventListener('click',e=>{if(!e.target.closest('#rxSmartResults')&&!e.target.closest('.search'))clear()})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'rxSmartResults' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'</body>')

print('RX_SMART_SEARCH=farm_name_car_city_coordinates_identifiers',flush=True)

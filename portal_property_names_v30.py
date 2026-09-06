from __future__ import annotations

import portal_v8

UI = r'''
<style>
.rx-farm-name-icon{background:transparent;border:0;overflow:visible}
.rx-farm-name-label{display:inline-flex;align-items:center;gap:5px;transform:translate(-50%,-50%);padding:5px 8px;border-radius:8px;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;backdrop-filter:blur(5px);line-height:1.15}
.rx-name-validated{background:rgba(5,35,22,.96);border:1px solid rgba(95,226,161,.92);box-shadow:0 5px 18px rgba(0,0,0,.48);color:#f2fff8;font-size:9px;font-weight:900}
.rx-name-validated:before{content:'◆';font-size:7px;color:#63e6a4;flex:0 0 auto}.rx-name-validated:hover{background:rgba(7,48,30,.99);border-color:#9cf4c8;z-index:1000}
.rx-name-reference{background:rgba(22,31,35,.64);border:1px dashed rgba(189,205,211,.42);box-shadow:0 3px 10px rgba(0,0,0,.24);color:rgba(226,234,237,.68);font-size:8px;font-weight:650;font-style:italic;opacity:.78}
.rx-name-reference:before{content:'○';font-size:8px;color:rgba(205,216,221,.65);flex:0 0 auto}.rx-name-reference:hover{opacity:.94;background:rgba(28,39,44,.78)}
.rx-farm-name-source{position:absolute;z-index:790;left:14px;bottom:14px;max-width:min(420px,calc(100% - 28px));padding:7px 9px;border:1px solid #29493b;border-radius:8px;background:rgba(6,22,15,.90);color:#a9bbb2;font-size:8px;line-height:1.35;pointer-events:none;box-shadow:0 6px 18px #0006}
.rx-farm-name-source .rx-legend-row{display:flex;align-items:flex-start;gap:6px;margin:2px 0}.rx-farm-name-source .rx-legend-valid{color:#c9f7df;font-weight:800}.rx-farm-name-source .rx-legend-ref{color:#9eabb0}
.rx-name-origin{margin-top:6px;padding:6px 8px;border-radius:7px;border:1px solid rgba(95,226,161,.35);background:rgba(24,74,49,.10);font-size:10px;line-height:1.35;color:#426451}
@media(max-width:720px){.rx-farm-name-label{max-width:150px;padding:4px 6px}.rx-name-validated{font-size:8px}.rx-name-reference{font-size:7px}.rx-farm-name-source{left:9px;bottom:9px;font-size:7px}.rx-farm-name-label.rx-name-lowzoom{font-size:7px;max-width:118px;padding:3px 5px}}
</style>
<script>
(function(){
  let nameLayer=null,timer=null,lastKey='',seq=0;
  window.rxNameCoverageV43=window.rxNameCoverageV43||{};
  function getMap(){try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}}
  function field(){return !!window.rxFieldMode}
  function dossierOpen(){return document.body.classList.contains('rx43-dossier-open')}
  function limitFor(z){if(field())return z>=15?24:z>=13?16:z>=12?10:6;return z>=15?60:z>=13?35:z>=12?22:12}
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function sourceBadge(html){let el=document.querySelector('#rxFarmNameSource');if(!el){el=document.createElement('div');el.id='rxFarmNameSource';el.className='rx-farm-name-source';document.querySelector('.main')?.appendChild(el)}if(el)el.innerHTML=html||''}
  function clearNames(){const m=getMap();if(m&&nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=null;sourceBadge('')}
  async function bounded(input,ms){return window.rxFieldFetch?window.rxFieldFetch(input,ms):fetch(input)}
  function isValidated(x){return x?.display_kind==='VALIDATED_PROPERTY_NAME'&&x?.validation_status==='VALIDATED'&&x?.panel_name_eligible===true&&String(x?.car_code||'').trim()}
  function renderOrigin(x,id){
    const origin=String(id?.origin_label||x?.origin_label||id?.source||x?.source||'fonte pública validada').trim();
    let el=document.querySelector('#rxValidatedNameOrigin');if(!el){el=document.createElement('div');el.id='rxValidatedNameOrigin';el.className='rx-name-origin';const anchor=document.querySelector('#name')||document.querySelector('#meta');anchor?.insertAdjacentElement('afterend',el)}
    if(el)el.textContent=`Denominação validada · Origem: ${origin}`;
  }
  function clearOrigin(){document.querySelector('#rxValidatedNameOrigin')?.remove()}
  async function openValidatedProperty(x){
    const m=getMap();if(!m||!x?.center||!isValidated(x))return;
    const lat=Number(x.center.lat),lon=Number(x.center.lon),expected=String(x.car_code).trim().toUpperCase();
    try{
      const rr=await bounded(`/v1/live/resolve?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,field()?6500:9000),d=await rr.json();
      const resolved=String(d?.property?.car_code||'').trim().toUpperCase();
      if(!rr.ok||!resolved||resolved!==expected)throw new Error('car_crosscheck_mismatch');
      const ri=await bounded(`/v1/live/property-identity/${encodeURIComponent(expected)}`,field()?6500:9000),id=await ri.json();
      const identityCar=String(id?.car_code||'').trim().toUpperCase();
      const identityName=String(id?.name||'').trim();
      const contractOk=ri.ok&&id?.validation_status==='VALIDATED'&&id?.panel_name_eligible===true&&identityCar===expected&&identityName&&identityName.toLocaleLowerCase('pt-BR')===String(x.name||'').trim().toLocaleLowerCase('pt-BR');
      if(!contractOk)throw new Error('identity_contract_rejected');
      if(typeof showProperty==='function'){
        d.property.name=identityName;
        d.property.name_origin=id?.origin_label||x?.origin_label||id?.source||x?.source||'';
        d.property.name_validation_status='VALIDATED';
        d.property.name_validation_method=id?.method||x?.validation_method||'';
        d.property.name_confidence=id?.confidence||'';
        showProperty(d.property,d.geometry);
        setTimeout(()=>{const n=document.querySelector('#name');if(n)n.textContent=identityName;const t=document.querySelector('#ptitle');if(t)t.textContent=`${identityName} · ${expected}`;renderOrigin(x,id)},0);
        return;
      }
    }catch(e){}
    if(typeof toast==='function')toast(`${x.name}: a associação com o CAR não passou pela validação final; o nome não foi aplicado ao imóvel.`)
  }
  function openReference(x){
    clearOrigin();
    const osm=x?.reference_kind==='OSM_LIVE';
    const msg=osm?'Referência geográfica do OpenStreetMap — não confirmada como denominação deste imóvel.':'Referência cadastral SIGEF/INCRA — ainda não vinculada a um CAR específico.';
    if(typeof toast==='function')toast(`${x?.name||'Referência'}: ${msg}`)
  }
  function render(items,z){
    const m=getMap();if(!m)return 0;if(nameLayer){try{m.removeLayer(nameLayer)}catch(e){}}nameLayer=L.layerGroup();const limit=limitFor(z);let rendered=0;
    (items||[]).slice(0,limit).forEach(x=>{
      const lat=Number(x?.center?.lat),lon=Number(x?.center?.lon);if(!Number.isFinite(lat)||!Number.isFinite(lon)||!x?.name)return;
      const validated=isValidated(x),label=esc(x.name),low=z<=11?' rx-name-lowzoom':'',semantic=validated?' rx-name-validated':' rx-name-reference';
      const title=validated?`${label} — denominação validada para o CAR`:`${label} — referência não confirmada para o imóvel`;
      const icon=L.divIcon({className:'rx-farm-name-icon',html:`<div class="rx-farm-name-label${semantic}${low}" title="${title}">${label}</div>`,iconSize:[1,1],iconAnchor:[0,0]});
      const mk=L.marker([lat,lon],{icon,keyboard:true,riseOnHover:true});mk.on('click',()=>validated?openValidatedProperty(x):openReference(x));
      const provenance=validated?(x.origin_label||x.source||'fonte validada'):(x.reference_kind==='OSM_LIVE'?'OpenStreetMap · referência geográfica não confirmada':'SIGEF/INCRA · referência cadastral não vinculada ao CAR');
      mk.bindTooltip(`${esc(x.name)}${x.municipality?' · '+esc(x.municipality):''}${x.uf?' / '+esc(x.uf):''}<br>${esc(provenance)}`,{direction:'top',offset:[0,-8]});nameLayer.addLayer(mk);rendered++
    });nameLayer.addTo(m);return rendered
  }
  function legend(items){
    const a=items||[],v=a.filter(isValidated).length,osm=a.filter(x=>x?.display_kind==='REFERENCE'&&x?.reference_kind==='OSM_LIVE').length,sig=a.filter(x=>x?.display_kind==='REFERENCE'&&x?.reference_kind==='SIGEF_CADASTRAL').length;let rows=[];
    if(v)rows.push(`<div class="rx-legend-row rx-legend-valid">◆ ${v} denominação(ões) validada(s) — vinculada(s) ao CAR</div>`);
    if(osm)rows.push(`<div class="rx-legend-row rx-legend-ref">○ ${osm} referência(s) do OpenStreetMap — não confirmada(s) para o imóvel</div>`);
    if(sig)rows.push(`<div class="rx-legend-row rx-legend-ref">○ ${sig} referência(s) SIGEF/INCRA — não vinculada(s) ao CAR</div>`);
    return rows.join('')||'<div class="rx-legend-row rx-legend-ref">Sem denominações validadas ou referências nesta área</div>'
  }
  async function refresh(){
    const m=getMap();if(!m||dossierOpen())return;const z=m.getZoom();if(z<11){clearNames();return}
    const b=m.getBounds(),west=b.getWest(),south=b.getSouth(),east=b.getEast(),north=b.getNorth();if(!(Number.isFinite(west)&&Number.isFinite(south)&&Number.isFinite(east)&&Number.isFinite(north)&&east>west&&north>south)){lastKey='';return}
    const span=Math.max(east-west,north-south);if(span>1.50){clearNames();return}
    const key=[west,south,east,north].map(v=>v.toFixed(3)).join('|')+'|'+z+'|'+(field()?'field':'normal');if(key===lastKey&&nameLayer)return;lastKey=key;const my=++seq;
    const u=new URL('/v1/live/property-names/viewport',location.origin);u.searchParams.set('west',west);u.searchParams.set('south',south);u.searchParams.set('east',east);u.searchParams.set('north',north);u.searchParams.set('limit',String(limitFor(z)));u.searchParams.set('diagnostic','true');const visibleCar=Number(window.rxVisibleCarCountV43);if(Number.isFinite(visibleCar)&&visibleCar>=0)u.searchParams.set('car_visible',String(Math.round(visibleCar)));
    try{
      const r=await bounded(u,field()?6500:9500),d=await r.json();if(my!==seq)return;if(!r.ok||!d.ok){lastKey='';sourceBadge(nameLayer?'Referências já carregadas · atualização pendente':'Referências públicas indisponíveis agora');return}
      const rendered=render(d.items||[],z);window.rxNameCoverageV43={...(d.coverage||{}),car_visible:Number.isFinite(visibleCar)?visibleCar:(d.coverage?.car_visible??null),names_rendered:rendered,validated_rendered:(d.items||[]).filter(isValidated).length,references_rendered:(d.items||[]).filter(x=>!isValidated(x)).length,zoom:z,field_mode:field(),captured_at:new Date().toISOString()};sourceBadge(legend(d.items||[]))
    }catch(e){if(my===seq){lastKey='';sourceBadge(nameLayer?'Referências já carregadas · atualização pendente':'Referências públicas indisponíveis agora')}}
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(refresh,field()?1200:360)}
  function install(){const m=getMap();if(!m){setTimeout(install,300);return}m.on('moveend',schedule);m.on('zoomend',schedule);setTimeout(refresh,field()?1600:650)}
  window.rxRefreshVisiblePropertyNames=refresh;window.rxGetNameCoverageV43=()=>({...window.rxNameCoverageV43});if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'RX_PROPERTY_NAMES_V44_TRUTH_UI' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace('</body>', UI + '<!-- RX_PROPERTY_NAMES_V44_TRUTH_UI --></body>')

print('RX_PORTAL_PROPERTY_NAMES_V44=validated_vs_reference_truth_ui', flush=True)

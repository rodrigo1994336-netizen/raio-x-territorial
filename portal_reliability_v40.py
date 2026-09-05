from __future__ import annotations

import portal_v8


def _patch_tabs(html:str)->str:
    old_pane="function pane(id,html){const p=q('#rxPane-'+id);if(p)p.innerHTML=html}"
    new_pane="""function pane(id,html){const p=q('#rxPane-'+id);if(!p)return;window.__rxTabCacheV40=window.__rxTabCacheV40||{};const ck=(car()||'')+'|'+id,isLoading=/rx-loading|rx-premium-skeleton/.test(html),isError=/rx-error/.test(html);if(isLoading&&p.dataset.rxReady==='1')return;if(isError&&p.dataset.rxReady==='1'){let n=p.querySelector('.rx-inline-error');if(!n){n=document.createElement('div');n.className='rx-inline-error';p.prepend(n)}n.textContent='Não foi possível atualizar esta aba agora. Os dados anteriores foram mantidos.';return}p.innerHTML=html;if(!isLoading&&!isError){p.dataset.rxReady='1';window.__rxTabCacheV40[ck]=html}}"""
    if old_pane in html:html=html.replace(old_pane,new_pane,1)

    old_activate="function activate(tab){active=tab;const h=host();q('#rxPropertyTabs')?.querySelectorAll('.rx-tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));h?.classList.toggle('rx-tab-mode',tab!=='geral');h?.querySelectorAll('.rx-tab-pane').forEach(p=>p.classList.toggle('active',p.id==='rxPane-'+tab));if(tab!=='geral'&&!loaded[tab])load(tab)}"
    new_activate="""function activate(tab){active=tab;const h=host();q('#rxPropertyTabs')?.querySelectorAll('.rx-tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));h?.classList.toggle('rx-tab-mode',tab!=='geral');h?.querySelectorAll('.rx-tab-pane').forEach(p=>p.classList.toggle('active',p.id==='rxPane-'+tab));if(tab!=='geral'){window.__rxTabCacheV40=window.__rxTabCacheV40||{};const p=q('#rxPane-'+tab),cached=window.__rxTabCacheV40[(car()||'')+'|'+tab];if(p&&cached&&!p.dataset.rxReady){p.innerHTML=cached;p.dataset.rxReady='1'}if(!loaded[tab])load(tab)}}"""
    if old_activate in html:html=html.replace(old_activate,new_activate,1)

    old_load="function load(t){if(t==='clima')return clima(30);if(t==='agua')return agua();if(t==='embargos')return embargos();if(t==='safras')return safras();if(t==='certidoes')return certidoes();if(t==='mineracao')return mineracao();if(t==='agro')return agro()}"
    new_load="""let loadingV40={};function rxPrefetchV40(){const cn=navigator.connection||{},poor=!!cn.saveData||/2g/.test(cn.effectiveType||''),mid=/3g/.test(cn.effectiveType||''),seq=poor?['clima']:mid?['clima','agua','safras']:['clima','agua','safras','embargos','agro','mineracao','certidoes'];seq.forEach((t,i)=>setTimeout(()=>{if(car()===boundCar&&!loaded[t]&&!loadingV40[t])load(t)},650+i*(poor?1800:mid?1200:800)))}function load(t){if(loadingV40[t])return loadingV40[t];let p;if(t==='clima')p=clima(30);else if(t==='agua')p=agua();else if(t==='embargos')p=embargos();else if(t==='safras')p=safras();else if(t==='certidoes')p=certidoes();else if(t==='mineracao')p=mineracao();else if(t==='agro')p=agro();else return;loadingV40[t]=Promise.resolve(p).finally(()=>{delete loadingV40[t]});return loadingV40[t]}"""
    if old_load in html:html=html.replace(old_load,new_load,1)

    old_install="nav.querySelectorAll('.rx-tab').forEach(b=>b.onclick=()=>activate(b.dataset.tab))}"
    new_install="nav.querySelectorAll('.rx-tab').forEach(b=>b.onclick=()=>activate(b.dataset.tab));setTimeout(rxPrefetchV40,650)}"
    if old_install in html:html=html.replace(old_install,new_install,1)
    return html


UI=r'''
<style id="rxReliabilityV40">
@media(max-width:720px){
  body.rx-dossier-open{overflow:hidden!important;background:#07150f!important}
  body.rx-dossier-open #panel{display:block!important;position:fixed!important;inset:0!important;width:100vw!important;height:100dvh!important;max-width:none!important;z-index:10000!important;background:#07150f!important;border:0!important;border-radius:0!important;box-shadow:none!important;overflow-y:auto!important;overflow-x:hidden!important}
  body.rx-dossier-open #map,
  body.rx-dossier-open .leaflet-control-container,
  body.rx-dossier-open #card,
  body.rx-dossier-open #rxContextLegend,
  body.rx-dossier-open #rxRareMode,
  body.rx-dossier-open #rxFarmNameSource,
  body.rx-dossier-open #rxLocateBtn,
  body.rx-dossier-open #rxFilterBtn,
  body.rx-dossier-open #rxMapState,
  body.rx-dossier-open .rx-map-legend-mini,
  body.rx-dossier-open .rx-rare-mode,
  body.rx-dossier-open .rx-farm-name-icon{display:none!important;visibility:hidden!important;pointer-events:none!important}
  body.rx-dossier-open .main{overflow:hidden!important;background:#07150f!important}
  body.rx-dossier-open .phead{position:sticky!important;top:0!important;z-index:10003!important;background:#07150f!important}
  body.rx-dossier-open .rx-tabs-wrap{position:sticky!important;top:62px!important;z-index:10002!important;background:#07150f!important}
  body.rx-filter-open #panel{pointer-events:none!important;filter:none!important}
}
.rx-tab-pane[data-rx-ready="1"]{min-height:90px}
.rx-tab-pane[data-rx-ready="1"]>.rx-inline-error:first-child{margin-bottom:8px}
.rx-tab-pane .rx-loading{min-height:88px;display:grid;place-items:center}
.rx-v40-state{position:sticky;top:108px;z-index:35;margin:0 0 8px auto;width:max-content;max-width:100%;padding:6px 9px;border:1px solid #315243;border-radius:999px;background:rgba(8,25,18,.96);color:#b9d2c5;font-size:8px;font-weight:850;box-shadow:0 8px 20px #0005}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);const mobile=()=>matchMedia('(max-width:720px)').matches;
 function visible(el){return !!el&&!el.classList.contains('hidden')&&getComputedStyle(el).display!=='none'}
 function sync(){const dossier=visible(q('#panel'));document.body.classList.toggle('rx-dossier-open',dossier);const map=q('#map');if(mobile()&&dossier){if(map){map.setAttribute('aria-hidden','true');map.inert=true}}else if(map){map.removeAttribute('aria-hidden');map.inert=false}}
 function watchTabs(){document.querySelectorAll('.rx-tab-pane').forEach(p=>{if(p.dataset.rxV40Observed)return;p.dataset.rxV40Observed='1';new MutationObserver(()=>{p.querySelectorAll('.rx-card,.rx-row,.rx-kpi,.rx-note,.rx-source').forEach(el=>{el.style.maxWidth='100%';el.style.overflowWrap='anywhere'})}).observe(p,{subtree:true,childList:true})})}
 const obs=new MutationObserver(()=>{sync();watchTabs()});function start(){obs.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});sync();watchTabs()}if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
<!-- RX_RELIABILITY_V40 -->
'''

html=_patch_tabs(portal_v8.PORTAL_HTML)
portal_v8.PORTAL_HTML=html.replace('</body>',UI+'</body>')
print('RX_RELIABILITY_V40=single_surface_mobile_cached_non_destructive_tabs_prefetch_deduped',flush=True)

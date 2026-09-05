from __future__ import annotations

import portal_v8


def _patch_climate(html: str) -> str:
    """Keep climate data visible while another period loads and reuse loaded periods."""
    old = "async function clima(days=30){const c=car();pane('clima','<div class=\"rx-loading\">Consultando NASA POWER…</div>');try{const d=await j(`/v1/live/climate-detail/${encodeURIComponent(c)}?days=${days}`),r=d.recent||{},cl=d.climatology||{},dr=d.drought||{};"
    new = "async function clima(days=30){const c=car();window.__rxClimateCacheV35=window.__rxClimateCacheV35||{};const ck=c+'|'+days,holder=q('#rxPane-clima');if(holder&&holder.dataset.rxClimateReady==='1'){holder.classList.add('rx-local-refresh');let badge=holder.querySelector('.rx-period-refresh');if(!badge){badge=document.createElement('div');badge.className='rx-period-refresh';holder.prepend(badge)}badge.textContent='Atualizando período…'}else{pane('clima','<div class=\"rx-premium-skeleton\"><span></span><span></span><span></span><span></span></div>')}try{const d=window.__rxClimateCacheV35[ck]||(await j(`/v1/live/climate-detail/${encodeURIComponent(c)}?days=${days}`));window.__rxClimateCacheV35[ck]=d;const r=d.recent||{},cl=d.climatology||{},dr=d.drought||{};"
    if old in html:
        html = html.replace(old, new, 1)

    old_end = "loaded.clima=true}catch(e){pane('clima','<div class=\"rx-error\">'+esc(e.message)+'</div>')}}"
    new_end = "loaded.clima=true;const hp=q('#rxPane-clima');if(hp){hp.dataset.rxClimateReady='1';hp.classList.remove('rx-local-refresh');hp.querySelector('.rx-period-refresh')?.remove()}}catch(e){const hp=q('#rxPane-clima');if(hp&&hp.dataset.rxClimateReady==='1'){hp.classList.remove('rx-local-refresh');hp.querySelector('.rx-period-refresh')?.remove();let n=hp.querySelector('.rx-inline-error');if(!n){n=document.createElement('div');n.className='rx-inline-error';hp.prepend(n)}n.textContent='Não foi possível atualizar este período agora. Os dados anteriores foram mantidos.'}else{pane('clima','<div class=\"rx-error\">Não foi possível carregar os dados climáticos agora. Tente novamente.</div>')}}}"
    if old_end in html:
        html = html.replace(old_end, new_end, 1)

    old_ranges = "<button data-days=\"30\" class=\"${days===30?'active':''}\">30 dias</button><button data-days=\"90\" class=\"${days===90?'active':''}\">90 dias</button><button data-days=\"365\" class=\"${days===365?'active':''}\">12 meses</button>"
    new_ranges = "<button data-days=\"7\" class=\"${days===7?'active':''}\">7 dias</button><button data-days=\"30\" class=\"${days===30?'active':''}\">30 dias</button><button data-days=\"365\" class=\"${days===365?'active':''}\">1 ano</button>"
    if old_ranges in html:
        html = html.replace(old_ranges, new_ranges, 1)
    return html


def _patch_unwanted_motion(html: str) -> str:
    # Selecting a thematic filter must never unexpectedly move the user's map.
    old = "if(lastCount&&markerLayer&&!autoFitDone){const bounds=L.featureGroup(markerLayer.getLayers()).getBounds();if(bounds.isValid()){autoFitDone=true;mm.fitBounds(bounds.pad(.22),{maxZoom:11,animate:true})}}"
    if old in html:
        html = html.replace(old, "if(lastCount&&!autoFitDone){autoFitDone=true}", 1)

    # On mobile, selecting a property should not animate the map behind a full-screen dossier.
    old2 = "map.fitBounds(layer.getBounds(),{padding:[28,28],maxZoom:16})"
    if old2 in html:
        html = html.replace(old2, "if(!matchMedia('(max-width:720px)').matches)map.fitBounds(layer.getBounds(),{padding:[28,28],maxZoom:16,animate:false})", 1)
    return html


def _patch_copy(html: str) -> str:
    # Remove decorative emoji from primary controls; the visual system uses typography and state.
    replacements = {
        '⛏️ Minerais / Terras raras': 'Minerais e terras raras',
        '🐂 Agropecuária': 'Produção rural',
        '<h4>🐂 Agropecuária e Pecuária</h4>': '<h4>Produção rural</h4>',
        '<h4>🐂 Raio-X Pecuário</h4>': '<h4>Produção rural e rebanhos</h4>',
    }
    for a, b in replacements.items():
        html = html.replace(a, b)
    return html


PREMIUM_UI = r'''
<style id="rxPremiumUxV35">
:root{
  --rx-space-1:4px;--rx-space-2:8px;--rx-space-3:12px;--rx-space-4:16px;--rx-space-5:24px;
  --rx-radius-sm:10px;--rx-radius-md:14px;--rx-radius-lg:18px;
  --rx-surface:#0a1912;--rx-surface-raised:#0d2118;--rx-line:#29473a;--rx-text:#eef8f2;--rx-muted:#9fb4a9;--rx-accent:#63e6a5;
}
html,body{overscroll-behavior:none}
#pbody,#pbody *,.panel *,.card *,.rx-filter-panel *{box-sizing:border-box;min-width:0}
.panel,.pbody,.rx-tab-pane,.rx-filter-panel{overflow-x:hidden!important}
.rx-row,.row,.rx-card,.rx-kpi,.rx-mobile-kpi,.source,.stat,.rx-agro-note,.rx-filter-status,.rx-human-main,.rx-human-why,.rx-car,#meta{
  overflow-wrap:anywhere;word-break:normal;max-width:100%;
}
.rx-kpis,.rx-grid2,.rx-mobile-kpis,.sources,.grid3{grid-template-columns:repeat(2,minmax(0,1fr))}
.rx-row{grid-template-columns:minmax(110px,.8fr) minmax(0,1.4fr)}
button{touch-action:manipulation}
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--rx-accent);outline-offset:2px}
button[disabled]{opacity:.58;cursor:wait}

/* Stable dossier geometry. Leaflet controls must never sit above the dossier. */
.panel{z-index:2100!important;width:min(690px,49vw)!important;max-width:690px;box-shadow:-22px 0 60px rgba(0,0,0,.46)!important;scrollbar-gutter:stable}
.phead{min-height:62px;z-index:20!important}
.rx-tabs-wrap{top:62px!important;z-index:18!important;margin-top:-16px!important}
.pbody{max-width:100%}
body.rx-dossier-open .leaflet-right,body.rx-dossier-open .leaflet-left,body.rx-dossier-open #rxContextLegend,body.rx-dossier-open #rxRareMode,body.rx-dossier-open #rxLocateBtn,body.rx-dossier-open #rxFilterBtn{visibility:hidden!important;pointer-events:none!important}
body.rx-filter-open #rxContextLegend,body.rx-filter-open #rxRareMode,body.rx-filter-open .leaflet-control-container{visibility:hidden!important;pointer-events:none!important}

/* Consistent cards and buttons. */
.rx-human-card,.rx-mobile-hero,.rx-card,.rx-kpi,.rx-note,.section .row,.rx-filter-row{border-radius:var(--rx-radius-md)!important}
.rx-mobile-actions button,.rx-agro-open,.btn,.ghost,.rx-tab,.rx-range button{min-height:42px}
.rx-tab,.rx-range button{transition:background .14s ease,border-color .14s ease,color .14s ease,transform .08s ease}
.rx-tab:active,.rx-range button:active,.rx-mobile-actions button:active{transform:translateY(1px)}
.rx-tab.active,.rx-range button.active{box-shadow:none!important}

/* Local refresh: retain old data rather than blanking a panel. */
.rx-tab-pane{position:relative}
.rx-local-refresh{opacity:.84;transition:opacity .14s ease}
.rx-period-refresh{position:sticky;top:112px;z-index:16;margin:0 0 8px auto;width:max-content;max-width:100%;padding:6px 9px;border:1px solid #365447;border-radius:999px;background:rgba(8,24,17,.96);color:#b9d2c5;font-size:8px;font-weight:850;box-shadow:0 8px 22px #0005}
.rx-inline-error{margin:0 0 10px;padding:9px 10px;border:1px solid #705f31;border-radius:11px;background:#211c0d;color:#ffe0a0;font-size:9px;line-height:1.45}
.rx-premium-skeleton{padding:14px 2px;display:grid;gap:9px}.rx-premium-skeleton span{display:block;height:52px;border-radius:12px;background:linear-gradient(90deg,#0c2017 20%,#173326 45%,#0c2017 70%);background-size:220% 100%;animation:rxSkel 1.25s linear infinite}.rx-premium-skeleton span:first-child{height:24px;width:38%}.rx-premium-skeleton span:last-child{height:100px}@keyframes rxSkel{to{background-position:-220% 0}}

/* Map overlays should support the map, not compete with it. */
.rx-map-legend-mini{max-width:min(520px,60vw);backdrop-filter:blur(8px)}
.rx-rare-mode{backdrop-filter:blur(10px)}

/* User-facing technical errors are visually de-emphasized; technical details stay in logs. */
.rx-friendly-error{border:1px solid #6d5930;background:#201b0e;color:#ffe1a2;border-radius:12px;padding:10px;font-size:9px;line-height:1.5}

@media(min-width:721px){
  .panel{top:0!important;bottom:0!important}
  .pbody{padding:16px 18px 28px!important}
  .rx-filter-panel{max-height:calc(100vh - 130px);overflow:auto}
}

@media(max-width:720px){
  /* Analysis is a dedicated screen, not a floating sheet over the map. */
  .panel{position:absolute!important;z-index:2400!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;max-width:none!important;height:auto!important;border:0!important;border-radius:0!important;box-shadow:none!important;background:#07150f!important;overflow-y:auto!important;overscroll-behavior:contain!important}
  .phead{position:sticky!important;top:0!important;min-height:62px!important;padding:10px 12px!important;background:rgba(7,21,15,.985)!important;backdrop-filter:blur(14px)!important}
  .pbody{padding:12px 12px calc(24px + env(safe-area-inset-bottom))!important}
  .rx-tabs-wrap{position:sticky!important;top:62px!important;margin:-12px -12px 14px!important;padding:8px 10px!important;background:rgba(7,21,15,.985)!important;backdrop-filter:blur(14px)!important;border-bottom:1px solid #244136!important;scrollbar-width:none}
  .rx-tabs-wrap::-webkit-scrollbar{display:none}.rx-tabs{gap:6px!important;scroll-snap-type:x proximity}.rx-tab{scroll-snap-align:start;min-height:40px!important;padding:8px 12px!important;font-size:10px!important;border-radius:10px!important}
  .rx-human-card{padding:12px!important;margin-bottom:12px!important}.rx-human-main{font-size:11px!important;line-height:1.48!important}.rx-human-why{font-size:9px!important}
  .rx-mobile-hero{padding:14px!important;margin-bottom:12px!important;background:#0a1c14!important}.rx-mobile-hero h3{font-size:19px!important;line-height:1.2!important}.rx-mobile-hero .rx-car{font-size:9px!important;line-height:1.35!important}
  .rx-mobile-kpis{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important}.rx-mobile-kpi{padding:10px!important;min-height:72px}.rx-mobile-kpi:nth-child(3){grid-column:1/-1}.rx-mobile-kpi small{font-size:8px!important}.rx-mobile-kpi b{font-size:12px!important;line-height:1.3!important}
  .rx-mobile-actions{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important}.rx-mobile-actions .primary{grid-column:1/-1!important}.rx-mobile-actions button{min-height:46px!important;padding:10px!important;font-size:10px!important;border-radius:11px!important}
  .rx-mobile-hero [data-rx-human="1"]{display:none!important}
  .rx-kpis,.rx-grid2,.sources{grid-template-columns:repeat(2,minmax(0,1fr))!important}.rx-row{grid-template-columns:1fr!important;gap:4px!important}
  .rx-chart{overflow:hidden}.rx-temp-svg{height:140px!important}
  /* Keep the map clean in field conditions. The information remains in Filters. */
  #rxContextLegend{display:none!important}
  .rx-rare-mode{left:auto!important;right:10px!important;bottom:66px!important;min-width:0!important;max-width:210px!important;padding:7px 9px!important;border-radius:11px!important}.rx-rare-mode .rx-rare-legend,.rx-rare-mode .rx-rare-interest{display:none!important}.rx-rare-gem{width:22px!important;height:22px!important}.rx-rare-mode b{font-size:9px!important}.rx-rare-mode small{font-size:7px!important}
  /* Filters become one deliberate bottom sheet; no other map card competes with it. */
  .rx-filter-panel{position:fixed!important;z-index:2700!important;left:8px!important;right:8px!important;bottom:8px!important;top:auto!important;width:auto!important;max-height:calc(100dvh - 78px)!important;overflow:auto!important;border-radius:18px!important;padding:14px!important;box-shadow:0 -18px 70px #000c!important;overscroll-behavior:contain!important}
  body.rx-filter-open .rx-rare-mode,body.rx-filter-open #rxContextLegend,body.rx-filter-open .rx-map-state,body.rx-filter-open .rx-locate{display:none!important}
  .rx-filter-row{padding:12px!important;margin-top:9px!important}.rx-filter-row label{font-size:11px!important}.rx-filter-row select{min-height:42px!important;font-size:11px!important}
  .toast{z-index:2900!important;left:10px!important;right:10px!important;bottom:14px!important;transform:none!important;max-width:none!important;text-align:center!important}
}

@media(max-width:380px){
  .rx-kpis,.rx-grid2,.sources{grid-template-columns:1fr!important}
  .rx-mobile-actions{grid-template-columns:1fr!important}.rx-mobile-actions .primary{grid-column:auto!important}
  .rx-mobile-kpis{grid-template-columns:1fr!important}.rx-mobile-kpi:nth-child(3){grid-column:auto!important}
}
</style>
<script>
(function(){
  const q=s=>document.querySelector(s);
  const mobile=()=>matchMedia('(max-width:720px)').matches;
  function isVisible(el){return !!el&&!el.classList.contains('hidden')&&getComputedStyle(el).display!=='none'}
  function syncClasses(){
    const dossier=isVisible(q('#panel')),filters=isVisible(q('#rxFilterPanel'));
    document.body.classList.toggle('rx-dossier-open',dossier);
    document.body.classList.toggle('rx-filter-open',filters);
    // Human-reading V23 used to inject the same general summary twice on mobile.
    if(mobile())q('.rx-mobile-hero [data-rx-human="1"]')?.remove();
  }
  function friendlyText(s){
    const t=String(s||'');
    if(!/(502|503|curl|operation timed out|timeout after|http_|traceback|connection reset|name or service not known)/i.test(t))return t;
    if(/minera|anm|sgb|terras raras/i.test(t))return 'Uma das fontes de mineração está temporariamente lenta. Os resultados disponíveis continuam visíveis; tente atualizar esta consulta depois.';
    return 'Não foi possível concluir esta consulta agora. Verifique sua conexão ou tente novamente em instantes.';
  }
  function sanitize(root=document){
    root.querySelectorAll?.('.toast,.rx-error,.rx-filter-status,.rx-inline-error').forEach(el=>{const n=friendlyText(el.textContent);if(n!==el.textContent)el.textContent=n});
  }
  // Preserve the user's position in the dossier when only one climate period changes.
  let savedScroll=0;
  document.addEventListener('click',e=>{
    const range=e.target.closest?.('#rxPane-clima .rx-range button');
    if(range){const p=q('#panel');savedScroll=p?.scrollTop||0;setTimeout(()=>{if(p&&document.body.classList.contains('rx-dossier-open'))p.scrollTop=savedScroll},0)}
  },true);
  const obs=new MutationObserver(muts=>{syncClasses();for(const m of muts){if(m.target?.nodeType===1)sanitize(m.target)}});
  function start(){obs.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});syncClasses();sanitize()}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
<!-- RX_PREMIUM_UX_V35 -->
'''


html = portal_v8.PORTAL_HTML
html = _patch_climate(html)
html = _patch_unwanted_motion(html)
html = _patch_copy(html)
portal_v8.PORTAL_HTML = html.replace('</body>', PREMIUM_UI + '</body>')

print('RX_PREMIUM_UX_V35=stable_panels_overflow_safe_climate_cache', flush=True)

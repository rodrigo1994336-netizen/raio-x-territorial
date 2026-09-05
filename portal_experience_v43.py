from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException

import portal_v8
from car_resilient import CAR_RE, fetch_car_live_resilient
from property_identity_runtime import _first_name

app = portal_v8.app
SNAPSHOT_TTL_SECONDS = 600
_SNAPSHOT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _snapshot_sync(car_code: str) -> dict[str, Any]:
    code = str(car_code or '').strip().upper()
    if not CAR_RE.match(code):
        return {'ok': False, 'car_code': code, 'detail': 'invalid_car_format'}
    now = time.monotonic()
    cached = _SNAPSHOT_CACHE.get(code)
    if cached and now - cached[0] < SNAPSHOT_TTL_SECONDS:
        out = dict(cached[1]); out['cached'] = True; return out
    started = time.monotonic()
    car = fetch_car_live_resilient(code)
    if not car.get('ok'):
        return {'ok': False, 'car_code': code, 'detail': car.get('detail') or 'CAR não localizado', 'source': 'SICAR'}
    props = car.get('properties') or {}
    direct_name = _first_name(props)
    signals = []
    status = props.get('status_imovel')
    condition = props.get('condicao')
    property_type = props.get('tipo_imovel')
    if status:
        signals.append({'kind': 'car_status', 'label': 'Situação do CAR', 'value': status, 'state': 'available'})
    if condition:
        signals.append({'kind': 'car_condition', 'label': 'Condição cadastral', 'value': condition, 'state': 'available'})
    if property_type:
        signals.append({'kind': 'property_type', 'label': 'Tipo do imóvel', 'value': property_type, 'state': 'available'})
    out = {
        'ok': True,
        'car_code': code,
        'public_name': direct_name,
        'public_name_state': 'confirmed' if direct_name else 'resolving',
        'public_name_source': 'SICAR' if direct_name else None,
        'area_ha': props.get('area'),
        'municipality': props.get('municipio'),
        'uf': props.get('uf'),
        'car_status': status,
        'condition': condition,
        'property_type': property_type,
        'fiscal_modules': props.get('m_fiscal'),
        'main_signals': signals[:5],
        'productive_profile': None,
        'main_environmental_signals': [],
        'main_restrictions': [],
        'loading_state_of_deeper_sources': 'not_requested',
        'geometry': car.get('geometry'),
        'source': 'SICAR/WFS público',
        'cached': False,
        'elapsed_ms': round((time.monotonic() - started) * 1000),
        'note': 'Snapshot rápido. Fontes aprofundadas só são consultadas quando o usuário solicita a análise completa.'
    }
    _SNAPSHOT_CACHE[code] = (now, out)
    if len(_SNAPSHOT_CACHE) > 500:
        for key, _ in sorted(_SNAPSHOT_CACHE.items(), key=lambda kv: kv[1][0])[:100]:
            _SNAPSHOT_CACHE.pop(key, None)
    return out


@app.get('/v1/live/snapshot/{car_code}')
async def property_snapshot_v43(car_code: str):
    out = await asyncio.to_thread(_snapshot_sync, car_code)
    if not out.get('ok'):
        raise HTTPException(status_code=422 if out.get('detail') == 'invalid_car_format' else 502, detail=out)
    return out


V43_UI = r'''
<style id="rxExperienceV43">
:root{--rx43-panel:clamp(390px,42vw,500px);--rx43-gap:12px;--rx43-radius:16px;--rx43-surface:#091912;--rx43-surface2:#0d2118;--rx43-line:#29473a;--rx43-text:#eef8f2;--rx43-muted:#9fb4a9;--rx43-accent:#63e6a5}
html,body,#map,.main,#panel,#pbody,#rx43SnapshotHost,#rx43SnapshotHost *{box-sizing:border-box}
#card{display:none!important}
#panel{overflow-x:hidden!important}
#pbody,#pbody *{min-width:0;max-width:100%;overflow-wrap:anywhere}
.rx43-snapshot-host{padding:16px 16px 0;background:#07150f}
.rx43-snapshot{display:grid;gap:12px}
.rx43-hero{border:1px solid var(--rx43-line);background:linear-gradient(145deg,#0d2319,#091711);border-radius:18px;padding:16px;min-width:0}
.rx43-hero .eyebrow{display:block;margin-bottom:5px}.rx43-name{font-size:21px;line-height:1.18;margin:0;color:var(--rx43-text);overflow-wrap:anywhere}.rx43-place{margin-top:6px;color:var(--rx43-muted);font-size:10px;line-height:1.45}.rx43-code{margin-top:5px;color:#789385;font-size:8px;overflow-wrap:anywhere}
.rx43-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.rx43-kpi{border:1px solid var(--rx43-line);background:var(--rx43-surface);border-radius:13px;padding:10px;min-height:68px}.rx43-kpi small{display:block;color:var(--rx43-muted);font-size:7px;text-transform:uppercase;letter-spacing:.7px}.rx43-kpi b{display:block;margin-top:5px;font-size:11px;line-height:1.35}
.rx43-section{border:1px solid var(--rx43-line);background:var(--rx43-surface);border-radius:15px;padding:12px}.rx43-section h4{margin:0 0 8px;font-size:11px}.rx43-signal{display:grid;grid-template-columns:minmax(105px,.8fr) minmax(0,1.2fr);gap:8px;padding:8px 0;border-top:1px solid #1d352a;font-size:9px;line-height:1.45}.rx43-signal:first-of-type{border-top:0}.rx43-signal span{color:var(--rx43-muted)}
.rx43-state{display:flex;align-items:flex-start;gap:8px;border-radius:12px;padding:9px 10px;background:#10251b;color:#bcd1c6;font-size:9px;line-height:1.45}.rx43-dot{width:7px;height:7px;border-radius:50%;background:var(--rx43-accent);margin-top:3px;flex:0 0 auto}.rx43-state.pending .rx43-dot{background:#ffc866}.rx43-state.warn .rx43-dot{background:#ff9a78}
.rx43-actions{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}.rx43-actions button{min-height:44px;border-radius:12px;font-weight:900;cursor:pointer;padding:9px 12px}.rx43-primary{border:0;background:var(--rx43-accent);color:#052116}.rx43-secondary{border:1px solid var(--rx43-line);background:var(--rx43-surface2);color:var(--rx43-text)}
.rx43-deep-label{display:none;margin:16px 16px 0;padding-top:14px;border-top:1px solid #203b30;color:#9fb4a9;font-size:9px;font-weight:850;letter-spacing:.8px;text-transform:uppercase}.rx43-full-requested .rx43-deep-label{display:block}
body.rx43-dossier-open .rx-tabs-wrap,body.rx43-dossier-open .rx-tab-pane{display:none!important}
body.rx43-dossier-open #rxSheetBackdrop{display:none!important}
body.rx43-dossier-open #rxFilterPanel{display:none!important}
body.rx43-dossier-open .rx-adv-backdrop{z-index:12000!important}
body.rx43-dossier-open #panel{display:block!important}
body.rx43-dossier-open .toast{z-index:13000!important}
@media(min-width:721px){
 #panel{position:absolute!important;top:0!important;right:0!important;bottom:0!important;left:auto!important;width:var(--rx43-panel)!important;max-width:var(--rx43-panel)!important;height:auto!important;border-radius:0!important;border-left:1px solid var(--rx43-line)!important;box-shadow:none!important;z-index:1100!important;background:#07150f!important}
 body.rx43-dossier-open #map{width:calc(100% - var(--rx43-panel))!important;transition:none!important}
 body:not(.rx43-dossier-open) #map{width:100%!important}
 .phead{z-index:5!important}.rx43-snapshot-host{padding:16px 16px 0}.pbody{padding:16px!important}
}
@media(max-width:720px){
 body.rx43-dossier-open{overflow:hidden!important;background:#07150f!important}
 body.rx43-dossier-open .top{display:none!important}
 body.rx43-dossier-open #map,body.rx43-dossier-open .leaflet-control-container,body.rx43-dossier-open .hint,body.rx43-dossier-open #rxLocateBtn,body.rx43-dossier-open #rxFilterBtn,body.rx43-dossier-open #rxContextLegend,body.rx43-dossier-open #rxMapLegend,body.rx43-dossier-open #rxFarmNameSource,body.rx43-dossier-open #rxNetState,body.rx43-dossier-open .rx-farm-name-icon,body.rx43-dossier-open .rx-rare-mode{display:none!important;visibility:hidden!important;pointer-events:none!important}
 #panel{position:fixed!important;inset:0!important;width:100vw!important;max-width:none!important;height:100dvh!important;border:0!important;border-radius:0!important;box-shadow:none!important;z-index:10000!important;background:#07150f!important;overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain!important}
 .phead{position:sticky!important;top:0!important;z-index:10002!important;min-height:58px!important;padding:10px 12px!important;background:rgba(7,21,15,.985)!important;border-radius:0!important;backdrop-filter:blur(12px)!important}.phead .back{width:38px;height:38px;flex:0 0 auto}.phead .eyebrow{font-size:8px}
 .rx43-snapshot-host{padding:12px 12px 0}.rx43-name{font-size:19px}.rx43-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.rx43-kpi:nth-child(3){grid-column:1/-1}.rx43-signal{grid-template-columns:1fr;gap:3px}.rx43-actions{grid-template-columns:1fr}.rx43-actions button{width:100%;min-height:48px}.pbody{padding:12px 12px calc(24px + env(safe-area-inset-bottom))!important}.rx43-deep-label{margin:14px 12px 0}
}
@media(max-width:380px){.rx43-kpis{grid-template-columns:1fr}.rx43-kpi:nth-child(3){grid-column:auto}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const mobile=()=>matchMedia('(max-width:720px)').matches;
 const genericName=v=>!v||/^im[oó]vel rural/i.test(String(v).trim())||/nome n[aã]o confirmado/i.test(String(v));
 const area=v=>{const n=Number(String(v??'').replace(',','.'));return Number.isFinite(n)?n.toLocaleString('pt-BR',{maximumFractionDigits:2})+' ha':'Não informada'};
 function host(){let h=q('#rx43SnapshotHost');if(h)return h;const p=q('#panel'),body=q('#pbody');if(!p||!body)return null;h=document.createElement('div');h.id='rx43SnapshotHost';h.className='rx43-snapshot-host';p.insertBefore(h,body);const l=document.createElement('div');l.className='rx43-deep-label';l.textContent='Análise completa';p.insertBefore(l,body);return h}
 function stateText(s){if(s==='confirmed')return 'Nome público confirmado em fonte disponível.';if(s==='unresolved')return 'Nome público não confirmado. O sistema não inventa denominação.';return 'Buscando denominação pública sem bloquear o resumo.'}
 function snapshotHtml(p){const name=!genericName(p.public_name||p.name)?(p.public_name||p.name):'IMÓVEL RURAL — NOME NÃO CONFIRMADO';const signals=(p.main_signals||[]).slice(0,5);const sig=signals.length?signals.map(x=>`<div class="rx43-signal"><b>${esc(x.label||'Informação')}</b><span>${esc(x.value??'—')}</span></div>`).join(''):'<div class="rx43-signal"><b>Cadastro</b><span>Informações básicas carregadas. A análise ambiental aprofundada ainda não foi solicitada.</span></div>';const pending=p.public_name_state==='resolving';return `<div class="rx43-snapshot"><section class="rx43-hero"><span class="eyebrow">SNAPSHOT DO IMÓVEL</span><h2 class="rx43-name" id="rx43Name">${esc(name)}</h2><div class="rx43-place">${esc(p.municipality||'Município não informado')}${p.uf?' / '+esc(p.uf):''}</div><div class="rx43-code">CAR ${esc(p.car_code||'—')}</div></section><div class="rx43-kpis"><div class="rx43-kpi"><small>Área</small><b>${esc(area(p.area_ha))}</b></div><div class="rx43-kpi"><small>Situação CAR</small><b>${esc(p.car_status||p.status||'Não informada')}</b></div><div class="rx43-kpi"><small>Condição</small><b>${esc(p.condition||'Não informada')}</b></div></div><section class="rx43-section"><h4>O que sabemos agora</h4>${sig}</section><div class="rx43-state ${pending?'pending':''}" id="rx43NameState"><span class="rx43-dot"></span><span>${esc(stateText(p.public_name_state))}</span></div><div class="rx43-state"><span class="rx43-dot"></span><span>Fontes pesadas não foram disparadas. Use “Ver análise completa” somente quando precisar aprofundar.</span></div><div class="rx43-actions"><button type="button" class="rx43-primary" id="rx43Full">VER ANÁLISE COMPLETA</button><button type="button" class="rx43-secondary" id="rx43Pdf">RELATÓRIO PDF</button></div></div>`}
 function applySnapshot(p){const h=host();if(!h)return;h.innerHTML=snapshotHtml(p);q('#rx43Full')?.addEventListener('click',full);q('#rx43Pdf')?.addEventListener('click',()=>{try{window.downloadPDF?.()}catch(e){}});const title=q('#ptitle');if(title)title.textContent=!genericName(p.public_name||p.name)?(p.public_name||p.name):(p.car_code||'Imóvel rural')}
 function setLayer(g){try{if(layer)map.removeLayer(layer)}catch(e){};try{if(g){layer=L.geoJSON(g,{style:{color:'#63e6a5',weight:3.2,fillColor:'#63e6a5',fillOpacity:.12}}).addTo(map);if(!mobile()){const b=layer.getBounds();if(b.isValid())map.fitBounds(b,{paddingTopLeft:[24,24],paddingBottomRight:[Math.min(520,innerWidth*.43),24],maxZoom:16,animate:false})}}}catch(e){}}
 function close(){q('#panel')?.classList.add('hidden');document.body.classList.remove('rx43-dossier-open','rx43-full-requested','rx43-full-ready');const p=q('#pbody');if(p)p.innerHTML='';const h=host();if(h)h.innerHTML='';try{map.invalidateSize({animate:false})}catch(e){}}
 async function identity(p,token){if(!p?.car_code||!genericName(p.public_name||p.name))return;const delay=window.rxFieldMode?900:120;await new Promise(r=>setTimeout(r,delay));if(token!==window.__rx43Token)return;const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),window.rxFieldMode?5200:8500);try{const r=await fetch(`/v1/live/property-identity/${encodeURIComponent(p.car_code)}`,{signal:ctrl.signal}),d=await r.json();if(token!==window.__rx43Token)return;if(r.ok&&d?.name){p.public_name=d.name;p.name=d.name;p.public_name_state='confirmed';p.public_name_source=d.source||'SICAR/SIGEF';try{current.name=d.name}catch(e){};applySnapshot(p)}else{p.public_name_state='unresolved';applySnapshot(p)}}catch(e){if(token!==window.__rx43Token)return;const s=q('#rx43NameState');if(s){s.classList.add('warn');s.querySelector('span:last-child').textContent='Nome público ainda não pôde ser confirmado. O restante do snapshot continua disponível.'}}finally{clearTimeout(timer)}}
 async function enrich(p,token){if(!p?.car_code)return;try{const fetcher=window.rxFieldFetch?window.rxFieldFetch(`/v1/live/snapshot/${encodeURIComponent(p.car_code)}`,window.rxFieldMode?6000:9000):fetch(`/v1/live/snapshot/${encodeURIComponent(p.car_code)}`);const r=await fetcher,d=await r.json();if(token!==window.__rx43Token)return;if(r.ok){Object.assign(p,{public_name:d.public_name||p.public_name,name:d.public_name||p.name,public_name_state:d.public_name_state||p.public_name_state,area_ha:d.area_ha??p.area_ha,municipality:d.municipality||p.municipality,uf:d.uf||p.uf,car_status:d.car_status||p.status,condition:d.condition||p.condition,property_type:d.property_type||p.type,main_signals:d.main_signals||[]});applySnapshot(p);if(d.public_name_state!=='confirmed')identity(p,token)}}catch(e){if(token!==window.__rx43Token)return;const s=q('#rx43NameState');if(s){s.classList.add('warn');s.querySelector('span:last-child').textContent='Atualização do snapshot indisponível agora. Os dados já carregados foram mantidos.'}}}
 function select(p,g){const token=window.__rx43Token=(window.__rx43Token||0)+1;const base={...p,geometry:g,public_name:genericName(p?.name)?null:p?.name,public_name_state:genericName(p?.name)?'resolving':'confirmed',car_status:p?.status||p?.car_status,main_signals:[...(p?.status?[{label:'Situação do CAR',value:p.status}]:[]),...(p?.condition?[{label:'Condição cadastral',value:p.condition}]:[]),...(p?.type?[{label:'Tipo do imóvel',value:p.type}]:[])]};try{current={...base}}catch(e){window.current={...base}};setLayer(g);const panel=q('#panel');panel?.classList.remove('hidden');document.body.classList.add('rx43-dossier-open');document.body.classList.remove('rx43-full-requested','rx43-full-ready');const body=q('#pbody');if(body)body.innerHTML='';applySnapshot(base);enrich(base,token);requestAnimationFrame(()=>{try{map.invalidateSize({animate:false})}catch(e){}})}
 function full(){if(document.body.classList.contains('rx43-full-requested'))return;document.body.classList.add('rx43-full-requested');const body=q('#pbody');if(body&&!body.innerHTML.trim())body.innerHTML='<div class="rx43-state pending"><span class="rx43-dot"></span><span>Preparando análise completa. O snapshot acima permanece disponível.</span></div>';try{if(typeof window.rxProgressiveAnalyze==='function')window.rxProgressiveAnalyze();else if(typeof analyze==='function')analyze()}catch(e){if(body)body.innerHTML='<div class="rx43-state warn"><span class="rx43-dot"></span><span>Não foi possível iniciar a análise completa agora. Tente novamente.</span></div>'}}
 function install(){
   // Disable the legacy eight-tab timer without removing its API routes.
   window.rxLegacyTabsDisabledV43=true;
   window.showProperty=showProperty=select;
   const back=q('#back');if(back)back.onclick=close;
   const oldRender=(typeof renderAnalysis==='function')?renderAnalysis:null;
   if(oldRender&&!window.__rx43RenderWrapped){window.__rx43RenderWrapped=true;window.renderAnalysis=renderAnalysis=function(d){oldRender(d);document.body.classList.add('rx43-full-ready')}}
   const panel=q('#panel');if(panel)panel.setAttribute('aria-label','Snapshot e análise do imóvel');
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
 window.rx43CloseDossier=close;
})();
</script>
<!-- RX_EXPERIENCE_V43 -->
'''

html = portal_v8.PORTAL_HTML
# Keep the legacy detail routes registered but stop the old tab installer from running every 500 ms.
html = html.replace('setInterval(install,500);', 'window.rxLegacyTabsDisabledV43=true;')
# V19 remains responsible for resilient nationwide SICAR routes; remove its DOM observer loop.
html = html.replace('const obs=new MutationObserver(compactState);obs.observe(main,{childList:true,subtree:true,characterData:true});compactState();', 'compactState();')
portal_v8.PORTAL_HTML = html.replace('</body>', V43_UI + '</body>')

print('RX_EXPERIENCE_V43=snapshot_first_single_surface_no_legacy_tab_timer', flush=True)

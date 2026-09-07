from __future__ import annotations

"""V47 lazy CAR-integrity UI extension.

This module deliberately does not edit the frozen V45 renderer. It attaches after
V45 and before V46, wraps the public ``showProperty`` entry point, and injects the
Block-2 slot only after the dossier is actually opened.
"""

import portal_v8


V47_INTEGRITY_UI = r'''
<style id="rxCarIntegrityV47Style">
.rx45-integrity-slot{min-width:0}.rx45-integrity-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:7px}.rx45-integrity-badge{font-size:7px;font-weight:900;color:var(--rx45-muted);text-transform:uppercase;letter-spacing:.45px}.rx45-integrity-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-bottom:8px}.rx45-integrity-mini{padding:7px;border-radius:9px;background:#0c1d16;font-size:7px;line-height:1.4}.rx45-integrity-mini b{display:block;color:var(--rx45-text);font-size:9px;margin-top:2px}.rx45-integrity-scroll{overflow-x:auto;overscroll-behavior-x:contain}.rx45-integrity-table{min-width:360px;display:grid;grid-template-columns:1.12fr repeat(3,minmax(0,1fr));border:1px solid #1d382c;border-radius:9px;overflow:hidden}.rx45-it-cell{padding:6px 5px;border-right:1px solid #1d382c;border-top:1px solid #1d382c;font-size:6.6px;line-height:1.35;overflow-wrap:anywhere}.rx45-it-cell:nth-child(4n){border-right:0}.rx45-it-head{border-top:0;background:#0c1d16;color:var(--rx45-muted);font-weight:900;text-transform:uppercase;letter-spacing:.2px}.rx45-it-info{font-weight:800;color:#d8e9df}.rx45-integrity-note{font-size:7px;line-height:1.45;color:var(--rx45-muted);margin-top:7px}.rx45-integrity-unavailable{padding:9px;border-radius:10px;background:#0c1d16;border-left:3px solid var(--rx45-gray);font-size:8px;line-height:1.45;color:var(--rx45-muted)}
</style>
<script id="rxCarIntegrityV47">
(()=>{
 const q=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
 const fmt=(v,d=2)=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d}):'Indisponível'};
 let seq=0;const cache=new Map();
 const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();
 function status(x,label){if(!x)return `${label}: indisponível`;if(x.state==='not_configured')return `${label}: limite administrativo não configurado`;if(x.state!=='checked')return `${label}: indisponível`;const a=Number(x.outside_ha),p=Number(x.outside_pct);return Number.isFinite(a)?`${label}: ${fmt(a,4)} ha fora · ${fmt(p,2)}%`:`${label}: verificado`}
 function body(d){
   if(!d?.ok){const detail=String(d?.detail||'fonte indisponível').replace(/^RuntimeError:/,'');return `<div class="rx45-integrity-unavailable"><strong>Fonte indisponível</strong><br>${esc(detail)}<br>Não foi interpretado como ausência de ocorrência.</div>`}
   const ov=d.overlap||{},rows=d.table_rows||[],ovText=ov.state==='checked'?`${Number(ov.distinct_car_count||0)} CAR(s) · ${fmt(ov.union_area_ha,4)} ha · ${fmt(ov.property_pct,2)}%`:'Indisponível';
   const cells=rows.map(r=>`<div class="rx45-it-cell rx45-it-info">${esc(r.information)}</div><div class="rx45-it-cell">${esc(r.declared)}</div><div class="rx45-it-cell">${esc(r.inside)}</div><div class="rx45-it-cell">${esc(r.measured)}</div>`).join('');
   return `<div class="rx45-integrity-summary"><div class="rx45-integrity-mini">Sobreposição com outros CARs<b>${esc(ovText)}</b></div><div class="rx45-integrity-mini">Snapshot SICAR<b>${esc(d.snapshot||'não informado')}</b></div></div><div class="rx45-integrity-scroll"><div class="rx45-integrity-table"><div class="rx45-it-cell rx45-it-head">Informação</div><div class="rx45-it-cell rx45-it-head">Declarado no CAR</div><div class="rx45-it-cell rx45-it-head">Dentro do perímetro</div><div class="rx45-it-cell rx45-it-head">Medido por nós</div>${cells}</div></div><div class="rx45-integrity-note">${esc(status(d.municipality_boundary,'Município'))}<br>${esc(status(d.uf_boundary,'UF'))}<br>Regeneração = residual geométrico; não comprova regeneração biológica nem conclusão jurídica.</div>`;
 }
 function slot(car){const card=q(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(!card)return null;let s=card.querySelector('.rx45-integrity-slot');if(!s){s=document.createElement('section');s.className='rx45-section rx45-integrity-slot';s.innerHTML='<div class="rx45-integrity-head"><h4 style="margin:0">Consistência CAR</h4><span class="rx45-integrity-badge">Base dos Dados / SICAR</span></div><div class="rx45-integrity-unavailable">Consultando composição e sobreposições…</div>';card.querySelector('.rx45-actions')?.before(s)}return s}
 async function load(car){if(!car||currentCar()!==car)return;const my=++seq,s=slot(car);if(!s)return;if(cache.has(car)){s.innerHTML=`<div class="rx45-integrity-head"><h4 style="margin:0">Consistência CAR</h4><span class="rx45-integrity-badge">Base dos Dados / SICAR</span></div>${body(cache.get(car))}`;return}try{const r=await fetch(`/v1/live/car-integrity/${encodeURIComponent(car)}`,{cache:'no-store'}),d=await r.json();if(my!==seq||currentCar()!==car)return;if(d?.ok)cache.set(car,d);const now=slot(car);if(now)now.innerHTML=`<div class="rx45-integrity-head"><h4 style="margin:0">Consistência CAR</h4><span class="rx45-integrity-badge">Base dos Dados / SICAR</span></div>${body(d)}`}catch(e){const now=slot(car);if(now)now.innerHTML='<div class="rx45-integrity-head"><h4 style="margin:0">Consistência CAR</h4><span class="rx45-integrity-badge">Base dos Dados / SICAR</span></div><div class="rx45-integrity-unavailable"><strong>Fonte indisponível</strong><br>Consulta de integridade indisponível. Não foi interpretada como ausência.</div>'}}
 function schedule(car){[90,720,2300,9800].forEach(ms=>setTimeout(()=>{if(currentCar()===car){slot(car);load(car)}},ms))}
 function install(){if(window.rxV47IntegrityInstalled)return;window.rxV47IntegrityInstalled=true;const base=window.showProperty;if(typeof base!=='function')return;window.showProperty=function(...args){const out=base.apply(this,args),car=String(args?.[0]?.properties?.cod_imovel||args?.[0]?.properties?.id_imovel||currentCar()||'').trim().toUpperCase();if(car)schedule(car);return out};window.rxV47LoadIntegrity=()=>schedule(currentCar())}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
<!-- RX_CAR_INTEGRITY_V47 -->
'''

if "RX_CAR_INTEGRITY_V47" not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", V47_INTEGRITY_UI + "</body>")

print("RX_PORTAL_CAR_INTEGRITY_UI_V47=lazy_extension_frozen_v45_untouched", flush=True)

from __future__ import annotations

import portal_v8

UI=r'''
<style>
.rx-mobile-overview{display:none}
@media(max-width:720px){
 .rx-mobile-overview{display:block}
 .rx-mobile-hero{border:1px solid #244136;background:linear-gradient(145deg,#0d2319,#091711);border-radius:15px;padding:12px;margin-bottom:10px}
 .rx-mobile-hero .eyebrow{font-size:8px}.rx-mobile-hero h3{font-size:17px;margin:4px 0}.rx-mobile-hero .rx-car{color:#9bb1a6;font-size:9px;word-break:break-all}
 .rx-mobile-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.rx-mobile-kpi{border:1px solid #244136;background:#0b1b14;border-radius:10px;padding:8px}.rx-mobile-kpi small{display:block;color:#9bb1a6;font-size:7px;text-transform:uppercase}.rx-mobile-kpi b{font-size:10px;display:block;margin-top:3px}
 .rx-mobile-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:9px}.rx-mobile-actions button{border:1px solid #244136;background:#10251b;color:#eef8f2;border-radius:10px;padding:10px;font-weight:850;font-size:9px}.rx-mobile-actions .primary{grid-column:1/3;background:#63e6a5;color:#052116;border-color:#63e6a5}
 .rx-mobile-hint{margin-top:8px;color:#9bb1a6;font-size:8px;line-height:1.45}
 #card.rx-selected-mobile{display:none!important}
}
</style>
<script>
(function(){
 const mobile=()=>matchMedia('(max-width:720px)').matches;
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
 const area=v=>{const n=Number(String(v??'').replace(',','.'));return Number.isFinite(n)?n.toLocaleString('pt-BR',{maximumFractionDigits:3})+' ha':'—'};
 const generic=n=>!n||/^im[oó]vel rural/i.test(String(n).trim());
 function setName(name,p){
   if(!name||generic(name))return;
   p.name=name;
   try{current.name=name}catch(e){};try{window.current.name=name}catch(e){};
   const n=q('#name');if(n)n.textContent=name;
   const t=q('#ptitle');if(t)t.textContent=name;
   const h=q('#rxInstantName');if(h)h.textContent=name;
 }
 async function identity(p){
   if(!p?.car_code)return;
   window.__rxIdentityCache=window.__rxIdentityCache||{};
   let d=window.__rxIdentityCache[p.car_code];
   try{
     if(!d){const r=await fetch(`/v1/live/property-identity/${encodeURIComponent(p.car_code)}`,{cache:'no-store'});d=await r.json();if(r.ok)window.__rxIdentityCache[p.car_code]=d}
     if(d?.name)setName(d.name,p);
     else {const h=q('#rxInstantName');if(h&&generic(h.textContent))h.textContent=`Imóvel rural · ${p.municipality||'—'}/${p.uf||'—'}`}
   }catch(e){}
 }
 function instantHtml(p){
   const nm=!generic(p?.name)?p.name:`Imóvel rural · ${p?.municipality||'—'}/${p?.uf||'—'}`;
   return `<div class="rx-mobile-overview"><div class="rx-mobile-hero"><span class="eyebrow">IMÓVEL SELECIONADO</span><h3 id="rxInstantName">${esc(nm)}</h3><div class="rx-car">${esc(p?.car_code||'—')}</div><div class="rx-mobile-kpis"><div class="rx-mobile-kpi"><small>Área</small><b>${esc(area(p?.area_ha))}</b></div><div class="rx-mobile-kpi"><small>CAR</small><b>${esc(p?.status||'—')}</b></div><div class="rx-mobile-kpi"><small>Condição</small><b>${esc(p?.condition||'—')}</b></div></div><div class="rx-mobile-actions"><button class="primary" id="rxMobileFull">ABRIR ANÁLISE COMPLETA</button><button id="rxMobilePdf">PDF</button><button id="rxMobileClose">VOLTAR AO MAPA</button></div><div class="rx-mobile-hint">As abas abaixo consultam clima, água, embargos, safras, mineração e agropecuária sob demanda. O PDF não é gerado escondido ao tocar no imóvel.</div></div></div>`;
 }
 function selectLayer(g){
   try{if(typeof layer!=='undefined'&&layer)map.removeLayer(layer)}catch(e){}
   try{if(g){window.layer=layer=L.geoJSON(g,{style:{color:'#63e6a5',weight:3,fillColor:'#63e6a5',fillOpacity:.12}}).addTo(map);map.fitBounds(layer.getBounds(),{padding:[28,28],maxZoom:16})}}catch(e){}
 }
 function showInstant(p,g){
   try{window.current=current={...p,geometry:g}}catch(e){window.current={...p,geometry:g}}
   const name=!generic(p?.name)?p.name:`Imóvel rural · ${p?.municipality||'—'}/${p?.uf||'—'}`;
   const n=q('#name');if(n)n.textContent=name;const meta=q('#meta');if(meta)meta.textContent=p?.car_code||'—';const a=q('#area');if(a)a.textContent=area(p?.area_ha);const st=q('#status');if(st)st.textContent=p?.status||'—';const co=q('#condition');if(co)co.textContent=p?.condition||'—';
   selectLayer(g);
   const panel=q('#panel'),body=q('#pbody'),title=q('#ptitle');if(panel)panel.classList.remove('hidden');if(title)title.textContent=name;if(body)body.innerHTML=instantHtml(p);
   const card=q('#card');if(card)card.classList.add('rx-selected-mobile');
   q('#rxMobileClose')?.addEventListener('click',()=>{panel?.classList.add('hidden');card?.classList.remove('rx-selected-mobile')});
   q('#rxMobilePdf')?.addEventListener('click',()=>{try{downloadPDF()}catch(e){}});
   q('#rxMobileFull')?.addEventListener('click',()=>{
      const btn=q('#rxMobileFull');if(btn){btn.disabled=true;btn.textContent='CARREGANDO…'}
      try{
        if(typeof window.rxProgressiveAnalyze==='function')window.rxProgressiveAnalyze();
        else if(typeof analyze==='function')analyze();
      }catch(e){if(btn){btn.disabled=false;btn.textContent='TENTAR NOVAMENTE'}}
   });
   identity(p);
   // portal_property_tabs watches #pbody/current and installs tabs on its own.
 }
 function install(){
   if(window.__rxMobileDossierInstalled)return;window.__rxMobileDossierInstalled=true;
   const previous=window.showProperty;
   window.showProperty=showProperty=function(p,g){
     if(mobile())return showInstant(p,g);
     // Desktop keeps the previous behavior, but prefer the progressive engine over
     // hidden report generation when the progressive engine is available.
     if(previous){
       // Prevent the V18 wrapper's automatic full report by temporarily shadowing analyze.
       const oldAnalyze=window.analyze;try{window.analyze=()=>{};previous(p,g)}finally{window.analyze=oldAnalyze}
       setTimeout(()=>{try{if(typeof window.rxProgressiveAnalyze==='function')window.rxProgressiveAnalyze()}catch(e){}},0);
       return;
     }
     return showInstant(p,g);
   };
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'RX_MOBILE_DOSSIER_V20' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_MOBILE_DOSSIER_V20 --></body>')

print('RX_MOBILE_DOSSIER_V20=instant_selection_tabs_no_hidden_pdf',flush=True)

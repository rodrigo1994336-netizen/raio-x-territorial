from __future__ import annotations

import portal_v8

# Enrich the V8 viewport interaction without rewriting the large portal module.
# Names are resolved lazily on hover/click and never invented when public sources
# do not expose a defensible denomination.
OLD_PROPERTY="function propertyFromFeature(f){const p=f?.properties||{};return {car_code:p.cod_imovel||p.car_code||'',municipality:p.municipio||p.municipality||'',uf:p.uf||rxLastUf||'',area_ha:p.area??p.area_ha,status:p.status_imovel||p.status||'',condition:p.condicao||p.condition||'',type:p.tipo_imovel||p.type||'',fiscal_modules:p.m_fiscal||p.fiscal_modules}}"
NEW_PROPERTY="function propertyFromFeature(f){const p=f?.properties||{};return {car_code:p.cod_imovel||p.car_code||'',name:p.rx_name||p.name||p.nome_imovel||p.denominacao||p.nome_area||'',municipality:p.municipio||p.municipality||'',uf:p.uf||rxLastUf||'',area_ha:p.area??p.area_ha,status:p.status_imovel||p.status||'',condition:p.condicao||p.condition||'',type:p.tipo_imovel||p.type||'',fiscal_modules:p.m_fiscal||p.fiscal_modules}}"
if OLD_PROPERTY in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(OLD_PROPERTY,NEW_PROPERTY)

OLD_TOOLTIP="l.bindTooltip(`<b>${p.municipality||'Imóvel rural'}</b><br>${p.area_ha??'—'} ha`,{sticky:true});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);if(typeof showProperty==='function')showProperty(p,f.geometry)})"
NEW_TOOLTIP=r'''l.bindTooltip(`<b>${p.name||'Nome da fazenda: consultando…'}</b><br>${p.municipality||'—'}${p.uf?' / '+p.uf:''} · ${p.area_ha??'—'} ha`,{sticky:true});l.on('mouseover',async()=>{if(p.name||!p.car_code)return;window.__rxIdentityCache=window.__rxIdentityCache||{};let d=window.__rxIdentityCache[p.car_code];try{if(!d){const rr=await fetch(`/v1/live/property-identity/${encodeURIComponent(p.car_code)}`);d=await rr.json();if(rr.ok)window.__rxIdentityCache[p.car_code]=d}if(d?.name){p.name=d.name;f.properties=f.properties||{};f.properties.rx_name=d.name;l.setTooltipContent(`<b>${d.name}</b><br>${p.municipality||'—'}${p.uf?' / '+p.uf:''} · ${p.area_ha??'—'} ha`)}else{l.setTooltipContent(`<b>Nome público não localizado</b><br>${p.municipality||'—'}${p.uf?' / '+p.uf:''} · ${p.area_ha??'—'} ha`)}}catch(e){}});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);if(typeof showProperty==='function')showProperty(p,f.geometry)})'''
if OLD_TOOLTIP in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(OLD_TOOLTIP,NEW_TOOLTIP)

UI=r'''
<script>
(function(){
  const esc=s=>encodeURIComponent(String(s||'').trim());
  function validName(v){const s=String(v||'').trim();return s&&s.toLowerCase()!=='imóvel rural'&&s.toLowerCase()!=='imovel rural'&&!/^im[oó]vel rural\s*[—-]/i.test(s)}
  function identityContract(id,code){
    const nm=String(id?.name||'').trim(),car=String(id?.car_code||'').trim().toUpperCase(),expected=String(code||'').trim().toUpperCase();
    return validName(nm)&&id?.validation_status==='VALIDATED'&&id?.panel_name_eligible===true&&!!car&&car===expected;
  }
  function applyName(id,code){
    if(!identityContract(id,code))return false;
    const nm=String(id.name).trim();
    try{current.name=nm}catch(e){}
    try{window.current&& (window.current.name=nm)}catch(e){}
    const n=document.querySelector('#name');if(n)n.textContent=nm;
    const t=document.querySelector('#ptitle');if(t)t.textContent=nm+(code?' \u00b7 '+code:'');
    return true;
  }
  async function resolveIdentity(p){
    const code=String(p?.car_code||'').trim();if(!code)return null;
    window.__rxIdentityCache=window.__rxIdentityCache||{};
    if(window.__rxIdentityCache[code])return window.__rxIdentityCache[code];
    try{const r=await fetch(`/v1/live/property-identity/${esc(code)}`);const d=await r.json();if(r.ok){window.__rxIdentityCache[code]=d;return d}}catch(e){}
    return null;
  }
  const oldShow=(typeof showProperty==='function')?showProperty:null;
  if(oldShow){
    window.showProperty=showProperty=function(p,g){
      const code=String(p?.car_code||'').trim();
      const immediate={name:String(p?.name||p?.denominacao||p?.nome_imovel||p?.nome_area||'').trim(),car_code:code,validation_status:p?.name_validation_status||p?.validation_status||'',panel_name_eligible:p?.panel_name_eligible===true};
      const safeP={...(p||{})};
      if(!identityContract(immediate,code)){delete safeP.name;delete safeP.denominacao;delete safeP.nome_imovel;delete safeP.nome_area}
      oldShow(safeP,g);
      if(!applyName(immediate,code)){
        const n=document.querySelector('#name');if(n)n.textContent='Identificando fazenda\u2026';
        resolveIdentity(p).then(d=>{if(applyName(d,code)){p.name=d.name;p.name_validation_status='VALIDATED';p.panel_name_eligible=true}else{const el=document.querySelector('#name');if(el)el.textContent=`Im\u00f3vel rural \u00b7 ${p?.municipality||'\u2014'}/${p?.uf||'\u2014'}`}});
      }
      // One click must open the complete on-screen dossier. The user no longer has
      // to select the parcel and then press a second "analyze" action.
      setTimeout(()=>{try{if(typeof analyze==='function')analyze()}catch(e){}},0);
    };
  }
  function suffix(){try{return validName(current?.name)?'?property_name='+esc(current.name):''}catch(e){return ''}}
  window.downloadPDF=downloadPDF=function(){if(!current?.car_code)return;window.open(`/v1/reports/property/${esc(current.car_code)}${suffix()}`,'_blank')};
  const oldRender=(typeof renderAnalysis==='function')?renderAnalysis:null;
  if(oldRender){
    window.renderAnalysis=renderAnalysis=function(d){
      oldRender(d);
      try{
        if(validName(current?.name)){
          const h=document.querySelector('#pbody .hero h3');if(h)h.textContent=current.name;
          const t=document.querySelector('#ptitle');if(t)t.textContent=current.name+' · '+current.car_code;
        }
      }catch(e){}
    };
  }
})();
</script>
'''

if 'RX_PORTAL_IDENTITY_V18' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PORTAL_IDENTITY_V18 --></body>')

print('RX_PORTAL_IDENTITY_V18=one_click_dossier_hover_farm_name_pdf_name',flush=True)

from __future__ import annotations

import portal_v8
from territorial_production import build_territorial_production

app=portal_v8.app


@app.get('/v1/live/territorial-production/{car_code}')
async def territorial_production(car_code:str):
    return await build_territorial_production(car_code.upper())


UI=r'''
<style>
.rx-fold{margin-top:8px;border:1px solid #244136;border-radius:11px;background:#0b1b14;overflow:hidden}.rx-fold>button{width:100%;border:0;background:transparent;color:#eef8f2;text-align:left;padding:10px 11px;display:flex;justify-content:space-between;align-items:center;font-size:9px;font-weight:850;cursor:pointer}.rx-fold>button small{color:#8aa397;font-weight:650}.rx-fold-body{display:none;padding:0 11px 11px}.rx-fold.open .rx-fold-body{display:block}.rx-activity-chip{display:inline-block;margin:3px 4px 0 0;padding:4px 7px;border:1px solid #365447;border-radius:999px;color:#bdd3c7;font-size:8px}.rx-territory-head{border:1px solid #355447;border-radius:12px;padding:10px;background:#0c1e16;margin-top:11px}.rx-territory-head b{font-size:10px}.rx-territory-head p{font-size:8px;color:#9bb1a6;line-height:1.5;margin:5px 0 0}
</style>
<script>
(function(){
  const q=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
  const fmt=(v,d=0)=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{maximumFractionDigits:d}):'—'};
  let lastCar=null,loading=false;
  function car(){try{return (typeof current!=='undefined'&&current?.car_code)?current.car_code:window.current?.car_code}catch(e){return null}}
  function fold(title,summary,body){return `<div class="rx-fold"><button type="button"><span>${esc(title)}</span><small>${esc(summary||'Ver detalhes')} ▾</small></button><div class="rx-fold-body">${body}</div></div>`}
  function wire(host){host?.querySelectorAll('.rx-fold>button').forEach(b=>b.onclick=()=>b.parentElement.classList.toggle('open'))}
  function livestock(d){const rows=(d?.series||[]).filter(x=>Number(x.value)>0);if(!rows.length)return '<div class="rx-note">Sem série municipal utilizável nesta consulta.</div>';return rows.slice(0,18).map(x=>`<span class="rx-activity-chip">${esc(x.herd)} · ${fmt(x.value)} cabeças</span>`).join('')}
  function products(items){if(!(items||[]).length)return '<div class="rx-note">Nenhum dado relevante retornou nesta consulta.</div>';return (items||[]).slice(0,16).map(x=>`<div class="rx-row"><b>${esc(x.product||x.species||'Indicador')}</b><span>${fmt(x.value,2)} ${esc(x.unit||'')} · ${esc(x.period||'')}</span></div>`).join('')}
  async function loadExtra(){
    const code=car(),pane=q('#rxPane-agro');if(!code||!pane||loading||lastCar===code&&q('#rxTerritorialExtra'))return;
    loading=true;lastCar=code;
    let host=q('#rxTerritorialExtra');if(!host){host=document.createElement('div');host.id='rxTerritorialExtra';host.innerHTML='<div class="rx-territory-head"><b>Produção e território</b><p>Carregando silvicultura, outros animais, aquicultura e produção florestal sem misturar dados municipais com dados da fazenda.</p></div>';pane.appendChild(host)}
    try{
      const r=await fetch(`/v1/live/territorial-production/${encodeURIComponent(code)}`),d=await r.json();if(!r.ok)throw new Error(d.detail||'consulta indisponível');
      const sil=d.silviculture||{},eu=sil.eucalyptus_latest||{},aqua=d.aquaculture||{},forest=d.forestry_products||{},extract=d.plant_extraction||{},animal=d.animal_products||{},live=d.livestock||{};
      host.innerHTML=`<div class="rx-territory-head"><b>Produção e território</b><p>${esc(d.interpretation||'Contexto produtivo territorial.')}</p></div>
        ${fold('Silvicultura / eucalipto',eu.value!=null?`Eucalipto no município: ${fmt(eu.value)} ${eu.unit||'ha'}`:'Contexto municipal',`<div class="rx-note"><b>Leitura correta:</b> eucalipto municipal não comprova plantio dentro do imóvel. A fazenda usa a camada espacial de silvicultura separadamente.</div><div class="rx-list">${products(sil.series)}</div>`)}
        ${fold('Outros rebanhos','Bubalinos, equinos, suínos, caprinos, ovinos, aves…',livestock(live))}
        ${fold('Aquicultura','Peixes, camarão, moluscos e outros',`<div class="rx-list">${products(aqua.products)}</div>`)}
        ${fold('Produção animal','Leite, ovos, mel, lã e outros',`<div class="rx-list">${products(animal.products)}</div>`)}
        ${fold('Produção florestal','Madeira, lenha, carvão, resina e outros',`<div class="rx-list">${products(forest.products)}</div>`)}
        ${fold('Extração vegetal','Produtos extrativos registrados no município',`<div class="rx-list">${products(extract.products)}</div>`)}
        <div class="rx-source">Fontes: ${esc(d.source||'IBGE/SIDRA')}</div>`;
      wire(host);
    }catch(e){host.innerHTML=`<div class="rx-territory-head"><b>Produção e território</b><p>Algumas fontes municipais não responderam agora. Isso não significa ausência de atividade.</p></div>`}
    finally{loading=false}
  }
  function retitle(){document.querySelectorAll('.rx-tab').forEach(b=>{if(b.dataset.tab==='agro')b.textContent='Produção e território'})}
  document.addEventListener('click',e=>{const b=e.target.closest?.('.rx-tab[data-tab="agro"]');if(b){setTimeout(()=>{retitle();loadExtra()},220)}});
  setInterval(()=>{retitle();const pane=q('#rxPane-agro');if(pane?.classList.contains('active'))loadExtra()},900);
})();
</script>
<!-- RX_TERRITORIAL_PRODUCTION_V33 -->
'''

portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'</body>')
print('RX_PORTAL_TERRITORIAL_PRODUCTION_V33=simple_folded_multi_activity_view',flush=True)

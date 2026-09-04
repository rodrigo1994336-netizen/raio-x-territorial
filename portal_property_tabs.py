from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException

import portal_v8
import report_api as report_base
from car_resilient import fetch_car_live_resilient
from climate_nasa import query_climate_nasa, query_climatology_nasa, build_drought_screening
from groundwater_siagas import query_groundwater
from safras_ibge import query_safras

app=portal_v8.app


async def _car(code:str):
    car=await asyncio.to_thread(fetch_car_live_resilient,code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR indisponível')
    return car


@app.get('/v1/live/climate-detail/{car_code}')
async def climate_detail(car_code:str,days:int=30):
    car=await _car(car_code);geom=car.get('geometry');days=max(7,min(int(days),365))
    recent,clim=await asyncio.gather(asyncio.to_thread(query_climate_nasa,geom,days),asyncio.to_thread(query_climatology_nasa,geom))
    return {'ok':bool(recent.get('ok')),'car_code':car_code.upper(),'recent':recent,'climatology':clim,'drought':build_drought_screening(recent,clim)}


@app.get('/v1/live/groundwater/{car_code}')
async def groundwater_detail(car_code:str,radius_km:float=20.0):
    car=await _car(car_code)
    return await query_groundwater(car.get('geometry'),max(5.0,min(float(radius_km),50.0)))


@app.get('/v1/live/safras/{car_code}')
async def crop_context(car_code:str):
    await _car(car_code)
    return await query_safras(car_code.upper())


@app.get('/v1/live/embargos-detail/{car_code}')
async def embargos_detail(car_code:str):
    code=car_code.upper()
    try:result=await asyncio.wait_for(report_base.analyze_car(code),timeout=18)
    except asyncio.TimeoutError:result={'car':await _car(code)}
    car=result.get('car') or {}
    if not car.get('ok'):raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='Imóvel não localizado')
    ib=result.get('embargos_ibama') or {};exact=ib.get('exact') or {}
    count=exact.get('occurrence_count') if isinstance(exact,dict) else None
    if count is None:count=ib.get('occurrence_count')
    if count is None and ib.get('ok'):count=ib.get('feature_count_bbox')
    deep=None
    try:deep=report_base._cache_get(code,time.monotonic())
    except Exception:pass
    autos=(deep or {}).get('autos_ibama') or {};services=(((deep or {}).get('territorial_constraints') or {}).get('services') or {})
    ic=services.get('embargo_icmbio') or services.get('icmbio_embargo') or services.get('icmbio') or {}
    return {'ok':True,'car_code':code,'ibama':{'ok':ib.get('ok'),'count':count,'area_ha':exact.get('area_unique_ha') if isinstance(exact,dict) else None,'source':ib.get('source'),'detail':ib.get('detail')},'autos':{'ok':autos.get('ok'),'count':autos.get('occurrence_count'),'fine_total':autos.get('fine_total'),'source':autos.get('source'),'detail':autos.get('detail'),'state':'ready' if autos else 'deep_analysis_running'},'icmbio':{'ok':ic.get('ok'),'count':ic.get('occurrence_count'),'area_ha':ic.get('area_unique_ha'),'source':ic.get('source'),'detail':ic.get('detail'),'state':'ready' if ic else 'deep_analysis_running'},'note':'Fonte indisponível nunca é tratada como ausência de embargo.'}


UI=r'''
<style>
.rx-tabs-wrap{position:sticky;top:0;z-index:20;background:#07150f;border-bottom:1px solid #244136;margin:-16px -16px 14px;padding:9px 12px;overflow-x:auto;white-space:nowrap}.rx-tabs{display:flex;gap:6px;min-width:max-content}.rx-tab{border:1px solid #244136;background:#0b1b14;color:#b7cbc0;border-radius:9px;padding:8px 10px;font-size:9px;font-weight:850;cursor:pointer}.rx-tab.active{background:#63e6a5;color:#052116;border-color:#63e6a5}.rx-tab-pane{display:none}.rx-tab-pane.active{display:block}.rx-tab-mode>:not(#rxPropertyTabs):not(.rx-tab-pane){display:none!important}.rx-tab-pane h3{margin:2px 0 4px;font-size:16px}.rx-lead{margin:0 0 12px;color:#9bb1a6;font-size:9px;line-height:1.5}.rx-subtabs{display:flex;gap:5px;overflow:auto;border-bottom:1px solid #244136;margin-bottom:12px}.rx-subtab{border:0;border-bottom:2px solid transparent;background:transparent;color:#9bb1a6;padding:8px 5px;font-size:9px;font-weight:850;cursor:pointer}.rx-subtab.active{color:#63e6a5;border-color:#63e6a5}.rx-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.rx-kpi{border:1px solid #244136;background:#0b1b14;border-radius:12px;padding:10px}.rx-kpi small{display:block;color:#9bb1a6;font-size:8px}.rx-kpi b{display:block;font-size:13px;margin:4px 0 2px}.rx-chart{border:1px solid #244136;background:#0b1b14;border-radius:13px;padding:10px;margin-top:10px}.rx-chart h4{font-size:10px;margin:0 0 8px}.rx-temp-svg{display:block;width:100%;height:170px}.rx-rain-bars{height:125px;display:flex;gap:2px;align-items:flex-end}.rx-rain-bar{flex:1;min-width:2px;background:#55b8ff;border-radius:2px 2px 0 0}.rx-note{border:1px solid #365447;background:#0c1e16;border-radius:11px;padding:10px;margin-top:10px;color:#bfd0c7;font-size:9px;line-height:1.5}.rx-note.warn{border-color:#705f31;background:#211c0d}.rx-note.purple{border-color:#8d55df;background:linear-gradient(135deg,#2a1242,#11131d);box-shadow:0 0 25px #a55bff25}.rx-note.purple b{color:#e2c7ff}.rx-list{margin-top:10px}.rx-row{display:grid;grid-template-columns:minmax(120px,.9fr) 1.4fr;gap:9px;border-bottom:1px solid #1d352a;padding:8px 2px;font-size:9px}.rx-row span{color:#9bb1a6}.rx-grid2{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}.rx-card{border:1px solid #244136;background:#0b1b14;border-radius:11px;padding:9px;font-size:8px;line-height:1.45}.rx-card b{font-size:9px}.rx-pill{display:inline-block;border-radius:999px;padding:4px 7px;background:#183b2a;color:#70e9ad;font-size:8px;font-weight:850}.rx-loading{padding:40px;text-align:center;color:#9bb1a6;font-size:10px}.rx-error{border:1px solid #754640;background:#251310;color:#ffc2bd;border-radius:11px;padding:11px;font-size:9px}.rx-source{font-size:8px;color:#789385;margin-top:9px}.rx-range{display:flex;gap:6px;margin-bottom:10px}.rx-range button{border:1px solid #244136;background:#0b1b14;color:#b7cbc0;border-radius:999px;padding:6px 9px;font-size:8px;font-weight:800;cursor:pointer}.rx-range button.active{background:#63e6a5;color:#052116;border-color:#63e6a5}
@media(max-width:720px){.rx-kpis{grid-template-columns:1fr 1fr}.rx-grid2{grid-template-columns:1fr}.rx-row{grid-template-columns:1fr}.rx-temp-svg{height:145px}}
</style>
<script>
(function(){
const defs=[['geral','Geral'],['embargos','Embargos'],['clima','Clima'],['safras','Safras'],['certidoes','Certidões'],['agua','Água subterrânea'],['mineracao','Mineração'],['agro','Agropecuária']];
let active='geral',loaded={},boundCar=null;
const q=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
function car(){try{return (typeof current!=='undefined'&&current?.car_code)?current.car_code:window.current?.car_code}catch(e){return null}}
function fmt(v,d=1){if(v===null||v===undefined||v==='')return '—';const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{maximumFractionDigits:d}):String(v)}
function host(){return q('#pbody')}
function install(){const h=host(),code=car();if(!h||!code)return;if(boundCar===code&&q('#rxPropertyTabs'))return;q('#rxPropertyTabs')?.remove();h.querySelectorAll('.rx-tab-pane').forEach(x=>x.remove());h.classList.remove('rx-tab-mode');active='geral';loaded={};boundCar=code;const nav=document.createElement('div');nav.id='rxPropertyTabs';nav.className='rx-tabs-wrap';nav.innerHTML='<div class="rx-tabs">'+defs.map(([id,l])=>`<button class="rx-tab ${id==='geral'?'active':''}" data-tab="${id}">${l}</button>`).join('')+'</div>';h.insertBefore(nav,h.firstChild);defs.filter(x=>x[0]!=='geral').forEach(([id])=>{const p=document.createElement('section');p.id='rxPane-'+id;p.className='rx-tab-pane';p.innerHTML='<div class="rx-loading">Abra a aba para consultar.</div>';h.appendChild(p)});nav.querySelectorAll('.rx-tab').forEach(b=>b.onclick=()=>activate(b.dataset.tab))}
function activate(tab){active=tab;const h=host();q('#rxPropertyTabs')?.querySelectorAll('.rx-tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));h?.classList.toggle('rx-tab-mode',tab!=='geral');h?.querySelectorAll('.rx-tab-pane').forEach(p=>p.classList.toggle('active',p.id==='rxPane-'+tab));if(tab!=='geral'&&!loaded[tab])load(tab)}
function pane(id,html){const p=q('#rxPane-'+id);if(p)p.innerHTML=html}
function kpis(items){return '<div class="rx-kpis">'+items.map(x=>`<div class="rx-kpi"><small>${esc(x[0])}</small><b>${esc(x[1])}</b><small>${esc(x[2]||'')}</small></div>`).join('')+'</div>'}
async function j(url){const r=await fetch(url,{cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Fonte indisponível');return d}
function lineChart(rows){rows=(rows||[]).filter(x=>x.t_max_c!=null||x.t_min_c!=null);if(rows.length<2)return '<div class="rx-note">Série diária insuficiente para o gráfico.</div>';const vals=rows.flatMap(x=>[x.t_max_c,x.t_min_c]).filter(Number.isFinite),mn=Math.min(...vals)-2,mx=Math.max(...vals)+2,w=620,h=165,p=18,x=i=>p+i/(rows.length-1)*(w-p*2),y=v=>h-p-(v-mn)/(mx-mn||1)*(h-p*2),path=k=>rows.map((r,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(r[k]??mn).toFixed(1)).join(' ');return `<div class="rx-chart"><h4>Temperatura diária</h4><svg class="rx-temp-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${path('t_max_c')}" fill="none" stroke="#ff8b48" stroke-width="3"/><path d="${path('t_min_c')}" fill="none" stroke="#55b8ff" stroke-width="3"/></svg><div class="rx-source">Laranja: máxima · Azul: mínima</div></div>`}
function rainChart(rows){rows=rows||[];const mx=Math.max(1,...rows.map(x=>Number(x.rain_mm)||0));return `<div class="rx-chart"><h4>Precipitação diária</h4><div class="rx-rain-bars">${rows.map(x=>`<div class="rx-rain-bar" title="${esc(x.date)} · ${fmt(x.rain_mm)} mm" style="height:${Math.max(1,(Number(x.rain_mm)||0)/mx*100)}%"></div>`).join('')}</div></div>`}
async function clima(days=30){const c=car();pane('clima','<div class="rx-loading">Consultando NASA POWER…</div>');try{const d=await j(`/v1/live/climate-detail/${encodeURIComponent(c)}?days=${days}`),r=d.recent||{},cl=d.climatology||{},dr=d.drought||{};pane('clima',`<h3>Clima</h3><p class="rx-lead">Consulta climática da fazenda sem precisar abrir o relatório.</p><div class="rx-range"><button data-days="30" class="${days===30?'active':''}">30 dias</button><button data-days="90" class="${days===90?'active':''}">90 dias</button><button data-days="365" class="${days===365?'active':''}">12 meses</button></div><div class="rx-subtabs"><button class="rx-subtab active" data-s="recent">Dados climáticos</button><button class="rx-subtab" data-s="clim">Climatologia</button><button class="rx-subtab" data-s="dry">Análise de secas</button></div><div id="rxClimateSub"></div><div class="rx-source">${esc(r.source||'NASA POWER')}</div>`);q('#rxPane-clima').querySelectorAll('.rx-range button').forEach(b=>b.onclick=()=>clima(Number(b.dataset.days)));const sub=q('#rxClimateSub'),buttons=q('#rxPane-clima').querySelectorAll('.rx-subtab');function show(s){buttons.forEach(b=>b.classList.toggle('active',b.dataset.s===s));if(s==='recent')sub.innerHTML=kpis([['Chuva',fmt(r.rain_sum_mm)+' mm',r.period_start+' → '+r.period_end],['Temp. média',fmt(r.temp_avg_c)+' °C','centróide do imóvel'],['Máx. média',fmt(r.temp_max_avg_c)+' °C','período'],['Mín. média',fmt(r.temp_min_avg_c)+' °C','período']])+lineChart(r.daily)+rainChart(r.daily)+`<div class="rx-note">${esc(r.note||'')}</div>`;else if(s==='clim')sub.innerHTML=`<div class="rx-list">${(cl.months||[]).map(x=>`<div class="rx-row"><b>${esc(x.month)}</b><span>Chuva ${fmt(x.rain_mm)} mm · média ${fmt(x.t_avg_c)} °C · máx ${fmt(x.t_max_c)} °C · mín ${fmt(x.t_min_c)} °C</span></div>`).join('')||'<div class="rx-note warn">Climatologia não respondeu nesta consulta.</div>'}</div><div class="rx-note">${esc(cl.note||'')}</div>`;else sub.innerHTML=kpis([['Triagem',dr.state||'—','não é índice oficial'],['Chuva recente',fmt(dr.rain_sum_mm)+' mm','período consultado'],['Dias secos',fmt(dr.dry_day_share_pct)+'%','dias com < 1 mm'],['Período',(dr.period_start||'—')+' → '+(dr.period_end||'—'),'NASA POWER']])+`<div class="rx-note warn">${esc(dr.note||'')}</div>`}buttons.forEach(b=>b.onclick=()=>show(b.dataset.s));show('recent');loaded.clima=true}catch(e){pane('clima','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function agua(){const c=car();pane('agua','<div class="rx-loading">Consultando poços SIAGAS e contexto hidrogeológico…</div>');try{const d=await j(`/v1/live/groundwater/${encodeURIComponent(c)}?radius_km=20`);pane('agua',`<h3>Água subterrânea</h3><p class="rx-lead">Poços reais cadastrados ao redor da propriedade + estatística regional de profundidade e nível d'água.</p>${kpis([['Poços próximos',fmt(d.well_count,0),`raio ${fmt(d.search_radius_km,0)} km`],['Profundidade mediana',fmt(d.well_depth_median_m)+' m',`n=${d.well_depth_sample_n||0}`],['Nível estático mediano',fmt(d.static_water_level_median_m)+' m',`n=${d.static_water_level_sample_n||0}`],['Evidência',d.groundwater_evidence||'—','confiança '+(d.confidence||'—')]])}<div class="rx-note ${d.confidence==='baixa'?'warn':''}"><b>Leitura correta:</b> ${esc(d.interpretation||'')}</div><div class="rx-list"><div class="rx-row"><b>Aquíferos predominantes</b><span>${(d.dominant_aquifers||[]).map(x=>esc(x.name)+' ('+x.count+')').join(', ')||'—'}</span></div><div class="rx-row"><b>Nível dinâmico mediano</b><span>${fmt(d.dynamic_water_level_median_m)} m</span></div><div class="rx-row"><b>Vazão específica mediana</b><span>${fmt(d.specific_yield_median,2)}</span></div><div class="rx-row"><b>pH mediano dos registros</b><span>${fmt(d.ph_median,2)}</span></div></div><h4 style="margin:15px 0 7px">Poços mais próximos</h4><div class="rx-grid2">${(d.nearest_wells||[]).slice(0,10).map(w=>`<div class="rx-card"><b>${esc(w.name||w.id||'Poço SIAGAS')}</b><br>${fmt(w.distance_km)} km · profundidade ${fmt(w.well_depth_m)} m<br>nível estático ${fmt(w.static_level_m)} m<br>${esc(w.aquifer||'Aquífero não informado')}</div>`).join('')||'<div class="rx-note warn">Nenhum poço próximo retornou nesta consulta.</div>'}</div><div class="rx-note warn">${esc(d.drilling_note||'')}</div><div class="rx-source">${esc(d.source||'SGB/SIAGAS')}</div>`);loaded.agua=true}catch(e){pane('agua','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function embargos(){const c=car();pane('embargos','<div class="rx-loading">Consultando embargos e autos…</div>');try{const d=await j(`/v1/live/embargos-detail/${encodeURIComponent(c)}`),ib=d.ibama||{},ic=d.icmbio||{},a=d.autos||{};pane('embargos',`<h3>Embargos</h3><p class="rx-lead">Fiscalização ambiental consultável diretamente na tela.</p>${kpis([['Embargos IBAMA',ib.ok?fmt(ib.count,0):'FONTE PARCIAL',fmt(ib.area_ha)+' ha'],['Embargos ICMBio',ic.ok?fmt(ic.count,0):(ic.state==='deep_analysis_running'?'PROCESSANDO':'FONTE PARCIAL'),fmt(ic.area_ha)+' ha'],['Autos IBAMA',a.ok?fmt(a.count,0):(a.state==='deep_analysis_running'?'PROCESSANDO':'FONTE PARCIAL'),'autos de infração'],['Multas',a.ok?'R$ '+fmt(a.fine_total,2):'—','quando a fonte fornece valor']])}<div class="rx-note">${esc(d.note||'')}</div>`);loaded.embargos=true}catch(e){pane('embargos','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function safras(){const c=car();pane('safras','<div class="rx-loading">Consultando contexto agrícola municipal…</div>');try{const d=await j(`/v1/live/safras/${encodeURIComponent(c)}`);pane('safras',`<h3>Safras</h3><p class="rx-lead">Culturas e indicadores produtivos municipais. Não são atribuídos automaticamente ao imóvel.</p><div class="rx-note"><b>CONAB + IBGE/PAM:</b> ${esc(d.conab?.note||'')}</div>${(d.products||[]).map(x=>`<div class="rx-card" style="margin-top:7px"><b>${esc(x.product)}</b><br>${(x.metrics||[]).slice(0,5).map(m=>`${esc(m.measure)}: ${fmt(m.value)} ${esc(m.unit||'')}`).join('<br>')}</div>`).join('')||'<div class="rx-note warn">A fonte municipal não retornou culturas nesta consulta.</div>'}<div class="rx-source">${esc(d.source||'IBGE/PAM')}</div>`);loaded.safras=true}catch(e){pane('safras','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function certidoes(){pane('certidoes','<div class="rx-loading">Carregando central documental…</div>');try{const d=await j('/v1/integrations/premium/status');pane('certidoes',`<h3>Certidões e documentos</h3><p class="rx-lead">Tudo fica acessível dentro do imóvel. Fontes restritas permanecem preparadas — OFF até habilitação.</p><div class="rx-grid2">${(d.integrations||[]).map(x=>`<div class="rx-card"><span class="rx-pill">${x.ready?'ATIVO':'PREPARADO — OFF'}</span><br><b>${esc(x.label)}</b><br>${esc(x.note||x.state||'')}</div>`).join('')||'<div class="rx-note">Central documental carregada sem integrações listadas.</div>'}</div>`);loaded.certidoes=true}catch(e){pane('certidoes','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function mineracao(){const c=car();pane('mineracao','<div class="rx-loading">Consultando ANM + SGB…</div>');try{const d=await j(`/v1/live/critical-minerals/${encodeURIComponent(c)}`),a=d.anm||{},s=d.sgb||{},rare=d.rare_earth_signal===true;pane('mineracao',`<h3>Mineração e terras raras</h3><p class="rx-lead">Processos minerários e sinais geológicos consultáveis sem abrir o PDF.</p><div class="rx-note purple"><b>${rare?'◆ SINAL RELACIONADO A TERRAS RARAS':'◆ TRIAGEM DE TERRAS RARAS'}</b><br>${esc(d.interpretation||'Sinal geológico não comprova jazida.')}</div>${kpis([['Processos ANM',fmt(a.process_count,0),'intersectantes'],['Críticos',fmt(a.critical_process_count,0),'classificados'],['SGB',fmt((s.hit_layers||[]).length,0),'camadas com sinal'],['Terras raras',rare?'SINAL':'SEM SINAL ESPECÍFICO','nas fontes que responderam']])}<div class="rx-list">${(s.hit_layers||[]).slice(0,15).map(x=>`<div class="rx-row"><b>${esc(x.title||x.layer)}</b><span>${esc((x.minerals||[]).join(', '))} · ${fmt(x.hit_count,0)} ocorrência(s)</span></div>`).join('')}</div>`);loaded.mineracao=true}catch(e){pane('mineracao','<div class="rx-error">'+esc(e.message)+'</div>')}}
async function agro(){const c=car();pane('agro','<div class="rx-loading">Consultando pecuária e agropecuária…</div>');try{const d=await j(`/v1/live/agropecuaria/${encodeURIComponent(c)}`),ppm=d.livestock_municipal||{},sif=d.sif_chain||{};pane('agro',`<h3>Agropecuária</h3><p class="rx-lead">Rebanho regional, cadeia SIF e triagem do imóvel. Município e fazenda nunca são misturados.</p><div class="rx-grid2">${(ppm.series||[]).slice(0,12).map(x=>`<div class="rx-card"><small>MUNICÍPIO</small><br><b>${esc(x.herd)}</b><br>${fmt(x.value,0)} cabeças · ${esc(x.period||'')}</div>`).join('')}</div><div class="rx-note">Estabelecimentos SIF filtrados no município/UF: <b>${fmt(sif.count,0)}</b>.</div><div class="rx-note">Pastagem/vigor MapBiomas só exibirá percentuais quando o worker raster devolver métricas reais do polígono.</div>`);loaded.agro=true}catch(e){pane('agro','<div class="rx-error">'+esc(e.message)+'</div>')}}
function load(t){if(t==='clima')return clima(30);if(t==='agua')return agua();if(t==='embargos')return embargos();if(t==='safras')return safras();if(t==='certidoes')return certidoes();if(t==='mineracao')return mineracao();if(t==='agro')return agro()}
setInterval(install,500);
})();
</script>
'''

if 'id="rxPropertyTabs"' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'</body>')

print('RX_PROPERTY_TABS=general_embargos_climate_crops_certificates_groundwater_mining_agro',flush=True)

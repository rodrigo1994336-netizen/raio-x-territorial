from __future__ import annotations

import portal_v8
from alerts_routes import register_alert_routes

app=portal_v8.app
register_alert_routes(app)

ALERT_CENTER_UI=r'''
<style>
.rx-alert-bell{position:relative;border:1px solid #244136;background:#10251b;color:#eef8f2;width:43px;height:43px;border-radius:13px;display:grid;place-items:center;font-size:19px;cursor:pointer;flex:0 0 auto}
.rx-alert-badge{position:absolute;right:-5px;top:-6px;min-width:18px;height:18px;border-radius:10px;padding:0 5px;background:#ff5f57;color:#fff;font-size:9px;font-weight:900;display:grid;place-items:center;border:2px solid #06110d}.rx-alert-badge.zero{display:none}
.rx-alert-drawer{position:fixed;z-index:5000;top:0;right:0;bottom:0;width:min(520px,100vw);background:#07150f;border-left:1px solid #244136;box-shadow:-28px 0 80px #000b;transform:translateX(103%);transition:transform .22s ease;display:flex;flex-direction:column}.rx-alert-drawer.open{transform:translateX(0)}
.rx-alert-head{padding:16px 17px;border-bottom:1px solid #244136;display:flex;align-items:center;gap:11px;background:#091912}.rx-alert-head h3{margin:0;font-size:17px}.rx-alert-head p{margin:3px 0 0;color:#9bb1a6;font-size:10px}.rx-alert-close{margin-left:auto;border:1px solid #244136;background:#10251b;color:#eef8f2;width:36px;height:36px;border-radius:10px;cursor:pointer;font-size:18px}
.rx-alert-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;padding:12px 14px;border-bottom:1px solid #1c352b}.rx-alert-kpi{border:1px solid #244136;border-radius:11px;background:#0b1b14;padding:9px}.rx-alert-kpi small{display:block;color:#9bb1a6;font-size:8px;text-transform:uppercase}.rx-alert-kpi b{font-size:15px;display:block;margin-top:3px}.rx-alert-toolbar{display:flex;gap:7px;padding:10px 14px;border-bottom:1px solid #1c352b;overflow:auto}.rx-alert-chip{border:1px solid #244136;background:#0b1b14;color:#d8e9df;border-radius:999px;padding:7px 10px;font-size:9px;font-weight:800;cursor:pointer;white-space:nowrap}.rx-alert-chip.active{background:#63e6a5;color:#052116;border-color:#63e6a5}
.rx-alert-body{overflow:auto;padding:12px 14px 100px}.rx-alert-card{border:1px solid #244136;background:#0b1b14;border-radius:14px;padding:12px;margin-bottom:9px}.rx-alert-card.unread{border-left:4px solid #63e6a5}.rx-alert-card.critical{border-color:#7d3b39;background:#1a1211}.rx-alert-card.attention{border-color:#66522c}.rx-alert-top{display:flex;gap:8px;align-items:flex-start}.rx-alert-sev{font-size:8px;font-weight:900;border-radius:999px;padding:5px 7px;text-transform:uppercase}.rx-alert-sev.critical{background:#ff756f22;color:#ff8d88}.rx-alert-sev.attention{background:#ffc86622;color:#ffd98d}.rx-alert-sev.info{background:#63e6a522;color:#77edb3}.rx-alert-time{margin-left:auto;color:#9bb1a6;font-size:8px}.rx-alert-card h4{font-size:11px;margin:9px 0 5px;line-height:1.4}.rx-alert-car{font-size:9px;color:#9bb1a6;word-break:break-all}.rx-alert-diff{margin-top:8px;border-top:1px solid #1e382d;padding-top:7px}.rx-alert-change{font-size:9px;color:#c6d9cf;padding:3px 0}.rx-alert-change b{color:#eef8f2}.rx-alert-actions{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}.rx-alert-action{border:1px solid #244136;background:#10251b;color:#eef8f2;border-radius:9px;padding:7px 9px;font-size:8px;font-weight:800;cursor:pointer}.rx-alert-action.primary{background:#63e6a5;color:#052116;border-color:#63e6a5}
.rx-alert-empty,.rx-alert-setup{border:1px dashed #365447;border-radius:14px;padding:16px;color:#9bb1a6;font-size:10px;line-height:1.55}.rx-alert-setup b{color:#ffc866;display:block;margin-bottom:5px}.rx-alert-monitor{margin-top:12px}.rx-alert-monitor h4{margin:0 0 7px;font-size:11px}.rx-monitor-row{border:1px solid #244136;border-radius:11px;background:#0b1b14;padding:10px;margin-bottom:6px}.rx-monitor-row b{font-size:9px;word-break:break-all}.rx-monitor-row small{display:block;color:#9bb1a6;margin-top:4px;font-size:8px}.rx-alert-footer{position:absolute;left:0;right:0;bottom:0;padding:10px 14px;background:linear-gradient(transparent,#07150f 22%);padding-top:28px}.rx-alert-full{width:100%;border:0;border-radius:11px;background:#63e6a5;color:#052116;padding:11px;font-weight:900;cursor:pointer;font-size:10px}
@media(max-width:720px){.rx-alert-bell{width:39px;height:39px}.rx-alert-summary{grid-template-columns:repeat(3,1fr)}.rx-alert-drawer{width:100%}}
</style>
<script>
(function(){
const fieldLabels={
 ibama_embargo_count:'Embargos IBAMA',ibama_embargo_area_ha:'Área embargada IBAMA',icmbio_embargo_count:'Embargos ICMBio',
 prodes_count:'Ocorrências PRODES',prodes_area_ha:'Área PRODES',fire_inside_count:'Focos de calor dentro',fire_near_count:'Focos de calor próximos',
 indigenous_count:'Terra Indígena',conservation_count:'Unidade de Conservação',quilombola_count:'Território Quilombola',settlement_count:'Assentamento',
 anm_count:'Processos ANM',anm_area_ha:'Área ANM',water_inside_count:'Outorgas no imóvel',water_near_count:'Outorgas próximas',
 pivot_intersection_count:'Pivôs no imóvel',pivot_intersection_area_ha:'Área de pivôs',rare_earth_signal:'Sinal de terras raras',critical_minerals:'Minerais críticos',
 car_status:'Status do CAR',car_condition:'Condição do CAR',area_ha:'Área do CAR',sigef_candidates:'Parcelas SIGEF candidatas'
};
let filter='all',lastAlerts=[],lastSummary=null;
const q=s=>document.querySelector(s);
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function fmt(v){if(v===null||v===undefined)return '—';if(Array.isArray(v))return v.join(', ')||'—';if(typeof v==='boolean')return v?'SIM':'NÃO';return String(v)}
function when(t){if(!t)return '—';try{return new Date(t).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'})}catch(e){return t}}
function sevName(s){return s==='critical'?'CRÍTICO':s==='attention'?'ATENÇÃO':'INFORMAÇÃO'}
function currentCar(){try{return (typeof current!=='undefined'&&current&&current.car_code)?current.car_code:(window.current?.car_code||'')}catch(e){return ''}}
function install(){
 const top=q('.top'); if(!top||q('#rxAlertBell'))return;
 const bell=document.createElement('button');bell.id='rxAlertBell';bell.className='rx-alert-bell';bell.type='button';bell.title='Centro de Alertas';bell.innerHTML='🔔<span id="rxAlertBadge" class="rx-alert-badge zero">0</span>';
 const status=q('.status'); if(status)top.insertBefore(bell,status);else top.appendChild(bell);
 const d=document.createElement('aside');d.id='rxAlertDrawer';d.className='rx-alert-drawer';d.innerHTML=`
 <div class="rx-alert-head"><div><h3>Centro de Alertas</h3><p>Mudanças que podem alterar sua decisão sobre o imóvel.</p></div><button id="rxAlertClose" class="rx-alert-close">×</button></div>
 <div class="rx-alert-summary"><div class="rx-alert-kpi"><small>Não lidos</small><b id="rxUnread">0</b></div><div class="rx-alert-kpi"><small>Críticos</small><b id="rxCritical">0</b></div><div class="rx-alert-kpi"><small>Monitorados</small><b id="rxMonitors">0</b></div></div>
 <div class="rx-alert-toolbar"><button class="rx-alert-chip active" data-f="all">Todos</button><button class="rx-alert-chip" data-f="unread">Não lidos</button><button class="rx-alert-chip" data-f="critical">Críticos</button><button class="rx-alert-chip" data-f="attention">Atenção</button></div>
 <div id="rxAlertBody" class="rx-alert-body"></div>
 <div class="rx-alert-footer"><button id="rxMonitorCurrent" class="rx-alert-full">MONITORAR IMÓVEL ATUAL</button></div>`;document.body.appendChild(d);
 bell.onclick=()=>{d.classList.add('open');loadAll()};q('#rxAlertClose').onclick=()=>d.classList.remove('open');
 document.querySelectorAll('.rx-alert-chip').forEach(b=>b.onclick=()=>{document.querySelectorAll('.rx-alert-chip').forEach(x=>x.classList.remove('active'));b.classList.add('active');filter=b.dataset.f;render()});
 q('#rxMonitorCurrent').onclick=monitorCurrent;
 setInterval(loadSummary,30000);loadSummary();
}
async function loadSummary(){try{const r=await fetch('/v1/alerts/summary',{cache:'no-store'}),d=await r.json();lastSummary=d;q('#rxUnread').textContent=d.unread_count||0;q('#rxCritical').textContent=d.critical_unread_count||0;q('#rxMonitors').textContent=d.active_monitors||0;const badge=q('#rxAlertBadge'),n=d.unread_count||0;badge.textContent=n>99?'99+':n;badge.classList.toggle('zero',!n);if(!d.ok)badge.classList.remove('zero')}catch(e){}}
async function loadAll(){await loadSummary();try{const r=await fetch('/v1/alerts?limit=80',{cache:'no-store'}),d=await r.json();lastAlerts=d.alerts||[]}catch(e){lastAlerts=[]}render()}
function changes(a){const diff=a.diff||{},rows=[];Object.entries(diff).forEach(([k,v])=>{if(k==='initial_snapshot')return;const label=fieldLabels[k]||k.replaceAll('_',' ');rows.push(`<div class="rx-alert-change"><b>${esc(label)}:</b> ${esc(fmt(v?.before))} → ${esc(fmt(v?.after))}</div>`)});return rows.join('')}
function card(a){const sev=a.severity||'info';return `<div class="rx-alert-card ${sev} ${a.unread?'unread':''}" data-id="${a.id}"><div class="rx-alert-top"><span class="rx-alert-sev ${sev}">${sevName(sev)}</span><span class="rx-alert-time">${when(a.created_at)}</span></div><h4>${esc(a.message||'Mudança detectada')}</h4><div class="rx-alert-car">CAR ${esc(a.car_code)}</div><div class="rx-alert-diff">${changes(a)||'<div class="rx-alert-change">Alteração registrada no snapshot do imóvel.</div>'}</div><div class="rx-alert-actions"><button class="rx-alert-action primary" onclick="window.rxOpenAlertProperty('${esc(a.car_code)}')">VER IMÓVEL</button><button class="rx-alert-action" onclick="window.open('/v1/reports/property/${encodeURIComponent(a.car_code)}','_blank')">NOVO RELATÓRIO</button>${a.unread?`<button class="rx-alert-action" onclick="window.rxReadAlert(${a.id})">MARCAR COMO LIDO</button>`:''}</div></div>`}
function render(){const host=q('#rxAlertBody');if(!host)return;if(lastSummary&&!lastSummary.ok){host.innerHTML=`<div class="rx-alert-setup"><b>⚠ CENTRO DE ALERTAS INSTALADO — PERSISTÊNCIA PENDENTE</b>O painel já está no sistema, mas o Postgres ainda não está vinculado ao serviço público. Enquanto isso não for concluído, o Raio-X não vai fingir que está monitorando 24/7. Assim que o vínculo for ativado, este mesmo painel passa a receber os alertas reais automaticamente.<br><br><b>Eventos preparados:</b> embargos IBAMA/ICMBio, PRODES, fogo, Terra Indígena, UCs, quilombolas, assentamentos, ANM, outorgas, pivôs, CAR e terras raras.</div>`;return}let rows=lastAlerts;if(filter==='unread')rows=rows.filter(x=>x.unread);else if(filter==='critical')rows=rows.filter(x=>x.severity==='critical');else if(filter==='attention')rows=rows.filter(x=>x.severity==='attention');host.innerHTML=rows.length?rows.map(card).join(''):`<div class="rx-alert-empty"><b>Nenhum alerta nesta categoria.</b><br>Isso significa apenas que nenhuma mudança foi registrada pelo monitoramento desde o baseline das propriedades ativas.</div>`;loadMonitors(host)}
async function loadMonitors(host){try{const r=await fetch('/v1/monitoring/properties'),d=await r.json();if(!d.ok||!d.monitors?.length)return;const box=document.createElement('div');box.className='rx-alert-monitor';box.innerHTML='<h4>Imóveis monitorados</h4>'+d.monitors.map(m=>`<div class="rx-monitor-row"><b>${esc(m.car_code)}</b><small>Última verificação: ${when(m.last_checked_at)} · Última mudança: ${when(m.last_changed_at)}</small><div class="rx-alert-actions"><button class="rx-alert-action" onclick="window.rxOpenAlertProperty('${esc(m.car_code)}')">VER</button><button class="rx-alert-action" onclick="window.rxPauseMonitor('${esc(m.car_code)}')">PAUSAR</button></div></div>`).join('');host.appendChild(box)}catch(e){}}
async function monitorCurrent(){const car=currentCar(),btn=q('#rxMonitorCurrent');if(!car){btn.textContent='SELECIONE UMA PROPRIEDADE PRIMEIRO';setTimeout(()=>btn.textContent='MONITORAR IMÓVEL ATUAL',2200);return}btn.disabled=true;btn.textContent='ATIVANDO…';try{const r=await fetch(`/v1/monitoring/properties/${encodeURIComponent(car)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:'in_app'})}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Não foi possível ativar');btn.textContent='MONITORAMENTO ATIVO';await loadAll()}catch(e){btn.textContent=e.message==='monitoring_database_not_ready'?'VÍNCULO DO BANCO PENDENTE':'TENTAR NOVAMENTE'}finally{setTimeout(()=>{btn.disabled=false;if(btn.textContent!=='MONITORAMENTO ATIVO')btn.textContent='MONITORAR IMÓVEL ATUAL'},2800)}}
window.rxReadAlert=async id=>{await fetch(`/v1/alerts/${id}/read`,{method:'POST'});await loadAll()};
window.rxOpenAlertProperty=car=>{q('#rxAlertDrawer')?.classList.remove('open');const input=q('#q');if(input)input.value=car;try{if(typeof loadCar==='function')loadCar(car)}catch(e){location.href='/?car='+encodeURIComponent(car)}};
window.rxPauseMonitor=async car=>{await fetch(`/v1/monitoring/properties/${encodeURIComponent(car)}/pause`,{method:'POST'});await loadAll()};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'id="rxAlertBell"' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',ALERT_CENTER_UI+'</body>')

print('RX_ALERT_CENTER=visible_v1',flush=True)

from __future__ import annotations

import asyncio
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response

import portal_api as base
from critical_minerals import query_critical_minerals
from premium_integrations import status as premium_status
from report_api import _analyze_with_live_addons

app = base.app
APP_PORTAL_VERSION = '0.18.2-v8-operational'

# Replace portal root only; keep every production report/live/export route already registered.
app.router.routes = [r for r in app.router.routes if getattr(r, 'path', None) != '/']

EXTRA_JS = r'''
<script>
(function(){
  const originalRender = window.renderAnalysis || renderAnalysis;
  async function criticalSection(){
    if(!window.current || !current.car_code) return;
    const host=document.querySelector('#pbody'); if(!host) return;
    let box=document.querySelector('#criticalMineralsSection');
    if(!box){box=document.createElement('div');box.id='criticalMineralsSection';box.className='section';box.innerHTML='<h4>Terras raras e minerais críticos</h4><div class="row"><span>Consultando ANM + Serviço Geológico do Brasil…</span></div>';host.appendChild(box)}
    try{
      const r=await fetch(`/v1/live/critical-minerals/${encodeURIComponent(current.car_code)}`);const d=await r.json();
      if(!r.ok) throw new Error(d.detail||'consulta indisponível');
      const a=d.anm||{}, s=d.sgb||{}, codes=d.mineral_codes||[];
      const rare=d.rare_earth_signal===true;
      const title=rare?'SINAL DE INTERESSE EM TERRAS RARAS':'TRIAGEM DE MINERAIS CRÍTICOS';
      const cls=rare?'warn':'ok';
      const anmCount=a.critical_process_count||0;
      const sgbHits=(s.hit_layers||[]).length;
      box.innerHTML=`<h4>Terras raras e minerais críticos</h4><div class="row"><b class="${cls}">${title}</b><br><span>${codes.length?codes.join(', ').replaceAll('_',' '):'Nenhum sinal específico identificado nas fontes consultadas.'}</span></div><div class="row"><b>ANM</b><br><span>${anmCount} processo(s) intersectante(s) classificado(s) como mineral crítico entre os processos retornados.</span></div><div class="row"><b>SGB / GeoSGB</b><br><span>${sgbHits} camada(s) consultada(s) com sinal no ponto/amostra do imóvel. Potencial geológico não comprova jazida, recurso ou reserva economicamente explotável.</span></div>`;
    }catch(e){box.innerHTML='<h4>Terras raras e minerais críticos</h4><div class="row"><b class="warn">FONTE PARCIAL</b><br><span>'+e.message+'. O sistema não transforma indisponibilidade em resultado negativo.</span></div>'}
  }
  async function premiumSection(){
    const host=document.querySelector('#pbody'); if(!host) return;
    let box=document.querySelector('#premiumSection');
    if(!box){box=document.createElement('div');box.id='premiumSection';box.className='section';host.appendChild(box)}
    try{
      const r=await fetch('/v1/integrations/premium/status');const d=await r.json();
      const rows=(d.integrations||[]).map(x=>`<div class="source"><small>${x.label}</small><b class="${x.ready?'ok':'warn'}">${x.state}</b></div>`).join('');
      box.innerHTML=`<h4>Integrações premium</h4><div class="row"><span>Arquitetura pronta, custo zero enquanto estiver OFF. Ative somente quando houver credenciais/clientes.</span></div><div class="sources">${rows}</div>`;
    }catch(e){}
  }
  window.renderAnalysis = renderAnalysis = function(d){originalRender(d);criticalSection();premiumSection()};
})();
</script>
'''

PORTAL_HTML = base.PORTAL_HTML.replace('</body>', EXTRA_JS + '</body>')


@app.get('/', response_class=HTMLResponse)
def portal_root_v8():
    return HTMLResponse(PORTAL_HTML, headers={'Cache-Control':'no-store','X-RaioX-Portal-Version':APP_PORTAL_VERSION})


@app.head('/')
def portal_head_v8():
    return Response(status_code=200, headers={'X-RaioX-Portal-Version':APP_PORTAL_VERSION})


@app.get('/v1/live/critical-minerals/{car_code}')
async def live_critical_minerals(car_code: str):
    result=await _analyze_with_live_addons(car_code.upper())
    car=result.get('car') or {}
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou fonte indisponível')
    return await query_critical_minerals(car.get('geometry'),result.get('anm'))


@app.get('/v1/integrations/premium/status')
def premium_integrations_status():
    return premium_status()


@app.get('/v1/portal/v8/status')
def v8_status():
    return {
        'ok':True,
        'portal_version':APP_PORTAL_VERSION,
        'live_property_resolution':True,
        'real_pdf':True,
        'kml':True,
        'geojson':True,
        'critical_minerals':True,
        'premium_integrations_prepared':True,
        'monitoring_persistence':'database-link-required',
    }

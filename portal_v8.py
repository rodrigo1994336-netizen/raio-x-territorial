from __future__ import annotations

import os
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response

import portal_api as base
from critical_minerals import query_critical_minerals
from premium_integrations import status as premium_status
from report_api import _analyze_with_live_addons
from whatsapp_gateway import register_routes as register_whatsapp_routes

app = base.app
APP_PORTAL_VERSION = '0.18.4-v8-operational'

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
  async function whatsappSection(){
    const host=document.querySelector('#pbody'); if(!host) return;
    let box=document.querySelector('#whatsappSection');
    if(!box){box=document.createElement('div');box.id='whatsappSection';box.className='section';host.appendChild(box)}
    try{
      const r=await fetch('/v1/whatsapp/status');const d=await r.json();
      box.innerHTML=`<h4>WhatsApp</h4><div class="row"><b class="${d.enabled&&d.configured?'ok':'warn'}">${d.enabled&&d.configured?'ATIVO':'PREPARADO — OFF'}</b><br><span>Gateway oficial Meta Cloud API para consulta por CAR e entrega de relatório. Enquanto OFF, nenhuma mensagem é enviada e nenhum custo é disparado.</span></div>`;
    }catch(e){}
  }
  window.renderAnalysis = renderAnalysis = function(d){originalRender(d);criticalSection();premiumSection();whatsappSection()};
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


@app.get('/v1/internal/persistence/status')
def persistence_status():
    # Never expose secrets/connection strings. This endpoint only reports whether
    # a durable database binding is present in the runtime environment.
    keys=('DATABASE_URL','POSTGRES_URL','RENDER_POSTGRES_URL')
    present=[k for k in keys if bool(os.getenv(k))]
    return {
        'durable_database_binding': bool(present),
        'detected_variable_names': present,
        'driver_available': _postgres_driver_available(),
        'policy': 'No connection string or credential is returned by this endpoint.'
    }


def _postgres_driver_available():
    try:
        import psycopg  # noqa: F401
        return 'psycopg'
    except Exception:
        try:
            import psycopg2  # noqa: F401
            return 'psycopg2'
        except Exception:
            return None


@app.get('/v1/portal/v8/status')
def v8_status():
    db_binding=any(bool(os.getenv(k)) for k in ('DATABASE_URL','POSTGRES_URL','RENDER_POSTGRES_URL'))
    return {
        'ok':True,
        'portal_version':APP_PORTAL_VERSION,
        'live_property_resolution':True,
        'real_pdf':True,
        'kml':True,
        'geojson':True,
        'critical_minerals':True,
        'premium_integrations_prepared':True,
        'whatsapp_gateway_prepared':True,
        'monitoring_persistence':'database-bound' if db_binding else 'database-link-required',
    }


register_whatsapp_routes(app, _analyze_with_live_addons)

from __future__ import annotations

import json
import sys
from fastapi import Request

import portal_v8

app=portal_v8.app

REQUIRED_ROUTES=(
    '/v1/live/quick/{car_code}',
    '/v1/live/progressive/status/{car_code}',
    '/v1/mobile/report/prepare/{car_code}',
    '/v1/mobile/report/status/{car_code}',
    '/v1/alerts/summary',
    '/v1/live/critical-minerals/{car_code}',
    '/v1/live/climate-detail/{car_code}',
    '/v1/live/groundwater/{car_code}',
    '/v1/live/agropecuaria/{car_code}',
)


def _route_paths():
    return {getattr(r,'path',None) for r in app.router.routes}


@app.get('/v1/ui/readiness')
async def ui_readiness_v25():
    paths=_route_paths();missing=[x for x in REQUIRED_ROUTES if x not in paths]
    modules=('portal_progressive','portal_alerts','portal_intelligence_filters','portal_property_tabs','portal_mobile_dossier_v20','portal_pdf_v21')
    return {
        'ok':not missing,
        'state':'ready' if not missing else 'partial',
        'missing_routes':missing,
        'modules':{m:(m in sys.modules) for m in modules},
        'route_count':len(paths),
        'version':'V25',
    }


@app.post('/v1/ui/client-error')
async def ui_client_error_v25(request:Request):
    try:data=await request.json()
    except Exception:data={}
    safe={
        'kind':str(data.get('kind') or '')[:40],
        'button':str(data.get('button') or '')[:80],
        'message':str(data.get('message') or '')[:260],
        'path':str(data.get('path') or '')[:160],
    }
    print('RX_CLIENT_UI_ERROR='+json.dumps(safe,ensure_ascii=False),flush=True)
    return {'ok':True}


UI=r'''
<style>
.rx-action-flash{transform:translateY(1px)!important;filter:brightness(1.18)!important;box-shadow:0 0 0 2px rgba(99,230,165,.18)!important}
.rx-action-busy{position:relative;pointer-events:none;opacity:.78}
.rx-action-busy:after{content:'';display:inline-block;width:10px;height:10px;margin-left:7px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:rxactspin .65s linear infinite;vertical-align:-1px}@keyframes rxactspin{to{transform:rotate(360deg)}}
.rx-ui-status{position:fixed;z-index:4000;right:12px;bottom:12px;max-width:min(390px,calc(100vw - 24px));background:#081812;border:1px solid #244136;border-radius:12px;padding:9px 11px;color:#c9ddd2;font-size:9px;box-shadow:0 16px 50px #0009;display:none}.rx-ui-status.show{display:block}.rx-ui-status.bad{border-color:#754640;color:#ffc2bd}.rx-ui-status.ok{border-color:#2d6a4f;color:#a9f3cb}
</style>
<script>
(function(){
 if(window.__rxActionV25)return;window.__rxActionV25=true;
 const $=s=>document.querySelector(s);
 let statusTimer=null;
 function status(msg,kind='ok',ms=2600){let x=$('#rxUiStatus');if(!x){x=document.createElement('div');x.id='rxUiStatus';x.className='rx-ui-status';document.body.appendChild(x)}x.textContent=msg;x.className='rx-ui-status show '+kind;clearTimeout(statusTimer);statusTimer=setTimeout(()=>x.className='rx-ui-status',ms)}
 function ident(b){return b?.id||b?.dataset?.tab||b?.dataset?.s||b?.textContent?.trim().slice(0,60)||'button'}
 function report(kind,b,msg){try{fetch('/v1/ui/client-error',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,button:ident(b),message:String(msg||''),path:location.pathname}),keepalive:true})}catch(e){}}
 function flash(b){if(!b)return;b.classList.add('rx-action-flash');setTimeout(()=>b.classList.remove('rx-action-flash'),180)}
 document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;flash(b);const id=ident(b);b.dataset.rxLastClick=String(Date.now());if(/ANÁLISE COMPLETA|RAIO-X PECUÁRIO|GERAR PDF|PDF PREPARANDO|MONITORAR/i.test(b.textContent||''))status('Ação recebida: '+(b.textContent||id).trim().replace(/…/g,'')+'.','ok',1400)},true);
 window.addEventListener('error',e=>{status('Erro na interface: '+String(e.message||'falha inesperada'),'bad',6000);report('window-error',null,e.message||'error')});
 window.addEventListener('unhandledrejection',e=>{const m=e.reason?.message||e.reason||'falha assíncrona';status('A ação falhou: '+String(m),'bad',6000);report('promise-rejection',null,m)});
 const nativeFetch=window.fetch.bind(window);
 window.fetch=async function(input,init){
   try{const r=await nativeFetch(input,init);if(!r.ok&&String(input).includes('/v1/')){report('http-'+r.status,null,String(input));status('A consulta respondeu com erro '+r.status+'.','bad',4500)}return r}
   catch(e){report('network-error',null,e?.message||e);status('Falha de conexão ao executar a ação.','bad',5000);throw e}
 };
 async function readiness(){try{const r=await nativeFetch('/v1/ui/readiness',{cache:'no-store'}),d=await r.json();if(!d.ok){status('Portal carregou parcialmente. Atualize a página em alguns segundos.','bad',6500);report('readiness',null,(d.missing_routes||[]).join(','))}}catch(e){}}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',readiness);else readiness();
 window.rxUiStatus=status;
})();
</script>
'''

if 'RX_ACTION_RUNTIME_V25' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_ACTION_RUNTIME_V25 --></body>')

print('RX_ACTION_RUNTIME_V25=readiness_feedback_client_error_logging',flush=True)

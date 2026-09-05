from __future__ import annotations

import time

import portal_api as base
from fastapi.responses import JSONResponse

app = base.app
STATE = {
    'ready': False,
    'error': None,
    'started_at': time.time(),
    'ready_at': None,
    'version': 'V26',
}


@app.get('/v1/bootstrap/state')
async def bootstrap_state_v26():
    elapsed_ms = int((time.time() - STATE['started_at']) * 1000)
    return JSONResponse({
        'ok': True,
        'ready': bool(STATE['ready']),
        'error': STATE['error'],
        'elapsed_ms': elapsed_ms,
        'version': STATE['version'],
    }, headers={'Cache-Control': 'no-store'})


def mark_ready():
    STATE['ready'] = True
    STATE['error'] = None
    STATE['ready_at'] = time.time()
    print('RX_PORTAL_V26_BOOT=ready', flush=True)


def mark_failed(exc):
    STATE['ready'] = False
    STATE['error'] = f'{type(exc).__name__}:{str(exc)[:240]}'
    print('RX_PORTAL_V26_BOOT=degraded:'+STATE['error'], flush=True)


BOOT_UI = r'''
<style>
#rxBootGuard{position:fixed;inset:0;z-index:99999;background:#06110d;color:#eef8f2;display:flex;align-items:center;justify-content:center;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}
#rxBootGuard .box{width:min(430px,calc(100vw - 34px));padding:28px;border:1px solid #244136;border-radius:20px;background:#0b1b14;box-shadow:0 30px 90px #000a;text-align:center}
#rxBootGuard .mark{width:52px;height:52px;border-radius:16px;background:#63e6a5;color:#052116;font-weight:950;display:grid;place-items:center;margin:0 auto 16px;font-size:18px}
#rxBootGuard h2{font-size:18px;margin:0 0 8px}#rxBootGuard p{color:#9bb1a6;font-size:12px;line-height:1.55;margin:0 0 18px}
#rxBootGuard .bar{height:6px;border-radius:999px;background:#173026;overflow:hidden}#rxBootGuard .bar i{display:block;height:100%;width:38%;background:#63e6a5;border-radius:999px;animation:rxboot 1s ease-in-out infinite alternate}@keyframes rxboot{to{transform:translateX(165%)}}
#rxBootGuard button{display:none;margin:16px auto 0;border:0;border-radius:11px;background:#63e6a5;color:#052116;padding:10px 14px;font-weight:900}
</style>
<div id="rxBootGuard"><div class="box"><div class="mark">RX</div><h2>Inicializando o Raio-X Territorial</h2><p id="rxBootText">Carregando mapa, consultas e módulos essenciais. Isso leva apenas alguns segundos após uma atualização.</p><div class="bar"><i></i></div><button id="rxBootRetry" type="button" onclick="location.reload()">Tentar novamente</button></div></div>
<script>
(function(){
  let tries=0;
  async function tick(){
    tries++;
    try{
      const r=await fetch('/v1/bootstrap/state',{cache:'no-store'});
      if(r.ok){
        const d=await r.json();
        if(d.ready){
          const k='rx-v26-ready-reload';
          if(sessionStorage.getItem(k)!=='1'){sessionStorage.setItem(k,'1');location.reload();return;}
          document.getElementById('rxBootGuard')?.remove();
          return;
        }
        if(d.error){
          const t=document.getElementById('rxBootText');if(t)t.textContent='O portal abriu, mas um módulo opcional ainda não carregou. Tentaremos novamente automaticamente.';
        }
      }
    }catch(e){}
    if(tries>45){
      const t=document.getElementById('rxBootText');if(t)t.textContent='A inicialização está demorando mais que o normal. Você pode tentar novamente.';
      const b=document.getElementById('rxBootRetry');if(b)b.style.display='block';
    }
    setTimeout(tick,1000);
  }
  tick();
})();
</script>
'''

if 'rxBootGuard' not in base.PORTAL_HTML:
    base.PORTAL_HTML = base.PORTAL_HTML.replace('</body>', BOOT_UI + '</body>')

print('RX_PORTAL_BOOT_GUARD_V26=installed', flush=True)

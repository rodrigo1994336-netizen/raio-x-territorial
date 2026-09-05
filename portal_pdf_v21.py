from __future__ import annotations

import asyncio
import html as html_lib
import os
import time
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

import portal_v8
from property_identity_runtime import resolve_property_identity_sync

# RX_PORTAL_PDF_V43_8 — compatibility marker kept for the protected V43 contract.
app=portal_v8.app
WORKER=os.getenv('RX_REPORT_WORKER_URL','https://raio-x-territorial-report.onrender.com').rstrip('/')
TRANSIENT_STATUS={502,503,504}


def _provided_name(value:str|None)->str:
    name=' '.join(str(value or '').strip().split())[:180]
    if not name:return ''
    if name.casefold().startswith(('imóvel rural','imovel rural')):return ''
    return name


async def _report_name(car_code:str,property_name:str|None)->str:
    supplied=_provided_name(property_name)
    if supplied:return supplied
    try:
        identity=await asyncio.to_thread(resolve_property_identity_sync,str(car_code or '').upper())
        return _provided_name(identity.get('name')) if identity.get('ok') else ''
    except Exception:
        return ''


async def _wait_worker_ready(car_code:str,property_name:str='',max_wait:float=70.0)->bool:
    """Wait until the real deferred V41 report route is registered.

    The browser issues a direct no-cors wake request to Render. The portal then waits
    on a concrete V41 status route so neither full analysis nor PDF generation races
    the worker startup sequence.
    """
    code=str(car_code or '').upper();name=_provided_name(property_name)
    path=f'/v1/reports/property/{quote(code)}/status'
    deadline=time.monotonic()+max(5.0,float(max_wait));attempt=0
    async with httpx.AsyncClient(timeout=httpx.Timeout(7,connect=4),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.9.4'}) as c:
        while time.monotonic()<deadline:
            attempt+=1
            try:
                r=await c.get(WORKER+path,params={'property_name':name})
                if r.status_code==200:
                    try:data=r.json()
                    except Exception:data={}
                    if isinstance(data,dict) and data.get('state') is not None:
                        print(f'RX_PORTAL_WORKER_READY={code}:attempt_{attempt}:state_{data.get("state")}',flush=True)
                        return True
                detail=f'http_{r.status_code}'
            except Exception as exc:
                detail=type(exc).__name__
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            print(f'RX_PORTAL_WORKER_READY_WAIT={code}:{detail}:attempt_{attempt}',flush=True)
            await asyncio.sleep(min(2.0,remaining))
    print(f'RX_PORTAL_WORKER_READY_TIMEOUT={code}:attempts_{attempt}',flush=True)
    return False


async def _proxy(method:str,path:str,params=None,timeout=25,retries=0):
    last_error=None
    for attempt in range(max(0,int(retries))+1):
        try:
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.9.4'}) as c:
                r=await c.request(method,WORKER+path,params=params)
                try:data=r.json()
                except Exception:data={'detail':r.text[:300]}
            if r.status_code<400:return data
            if r.status_code in TRANSIENT_STATUS and attempt<retries:
                print(f'RX_PORTAL_WORKER_RETRY={path}:http_{r.status_code}:attempt_{attempt+1}',flush=True)
                await asyncio.sleep(2.0*(attempt+1));continue
            raise HTTPException(status_code=r.status_code,detail=data.get('detail') or 'worker indisponível')
        except HTTPException:raise
        except Exception as e:
            last_error=e
            if attempt<retries:
                print(f'RX_PORTAL_WORKER_RETRY={path}:{type(e).__name__}:attempt_{attempt+1}',flush=True)
                await asyncio.sleep(2.0*(attempt+1));continue
            raise HTTPException(status_code=502,detail=f'Worker de análise indisponível: {type(e).__name__}')
    raise HTTPException(status_code=502,detail=f'Worker de análise indisponível: {type(last_error).__name__ if last_error else "transient"}')


@app.post('/v1/mobile/report/prepare/{car_code}')
@app.get('/v1/mobile/report/prepare/{car_code}')
async def mobile_report_prepare(car_code:str,property_name:str|None=None):
    name=await _report_name(car_code,property_name)
    if not await _wait_worker_ready(car_code,name,70):
        raise HTTPException(status_code=503,detail='Motor do relatório ainda está iniciando. A geração não foi iniciada.')
    return await _proxy('POST',f'/v1/reports/property/{quote(car_code.upper())}/prepare',{'property_name':name},12,retries=1)


@app.get('/v1/mobile/report/status/{car_code}')
async def mobile_report_status(car_code:str,property_name:str|None=None):
    name=await _report_name(car_code,property_name)
    return await _proxy('GET',f'/v1/reports/property/{quote(car_code.upper())}/status',{'property_name':name},10,retries=2)


@app.get('/v1/mobile/report/open/{car_code}')
async def mobile_report_open(car_code:str,property_name:str|None=None):
    name=await _report_name(car_code,property_name)
    url=f'{WORKER}/v1/reports/property/{quote(car_code.upper())}'
    if name:url+='?property_name='+quote(name)
    return RedirectResponse(url=url,status_code=307)


@app.get('/v1/mobile/report/view/{car_code}',response_class=HTMLResponse)
async def mobile_report_view(car_code:str,property_name:str|None=None):
    code=str(car_code or '').upper()
    name=await _report_name(code,property_name)
    safe_title=html_lib.escape(name or 'Relatório territorial')
    safe_code=html_lib.escape(code)
    pdf_url=f'/v1/mobile/report/open/{quote(code)}'
    if name:pdf_url+='?property_name='+quote(name)
    safe_pdf=html_lib.escape(pdf_url,quote=True)
    page=f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{safe_title} · Raio-X Territorial</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;background:#07140e;color:#edf7f1;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}body{{display:flex;flex-direction:column;overflow:hidden}}header{{min-height:62px;padding:10px max(12px,env(safe-area-inset-right)) 10px max(12px,env(safe-area-inset-left));display:flex;align-items:center;gap:10px;border-bottom:1px solid #214033;background:#0a1d14;z-index:2}}.back,.open{{height:40px;border:1px solid #315745;border-radius:11px;padding:0 14px;background:#102a1d;color:#edf7f1;font-weight:800;font-size:12px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}}.back{{background:#65dfaa;color:#07140e;border-color:#65dfaa}}.title{{min-width:0;flex:1}}.title strong{{display:block;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.title small{{display:block;margin-top:2px;color:#92ad9f;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}iframe{{border:0;width:100%;flex:1;background:#fff}}@media(max-width:640px){{header{{min-height:58px;padding-top:max(8px,env(safe-area-inset-top))}}.open{{padding:0 10px}}.title strong{{font-size:12px}}.title small{{font-size:9px}}}}
</style></head><body><header><button class="back" type="button" onclick="returnToRaioX()">← VOLTAR AO RAIO-X</button><div class="title"><strong>{safe_title}</strong><small>{safe_code}</small></div><a class="open" href="{safe_pdf}" target="_blank" rel="noopener">ABRIR PDF</a></header><iframe src="{safe_pdf}" title="{safe_title}"></iframe><script>
function returnToRaioX(){{try{{if(window.opener&&!window.opener.closed){{window.opener.focus();window.close();return}}}}catch(e){{}}if(history.length>1){{history.back()}}else{{location.href='/'}}}}
</script><!-- RX_PDF_VIEWER_V43_9_3 --></body></html>'''
    return HTMLResponse(page,headers={'Cache-Control':'no-store'})


@app.get('/v1/live/quick/{car_code}')
async def portal_quick_proxy(car_code:str,deep:bool=False):
    # Full analysis is user-triggered. When Render suspended the report worker, wait
    # for the same real V41 route used by PDF generation before requesting deep data.
    if deep and not await _wait_worker_ready(car_code,'',70):
        raise HTTPException(status_code=503,detail='Motor da análise ainda está iniciando.')
    return await _proxy('GET',f'/v1/live/quick/{quote(car_code.upper())}',params={'deep':'1' if deep else '0'},timeout=22,retries=1)


@app.get('/v1/live/progressive/status/{car_code}')
async def portal_progress_proxy(car_code:str):
    if not await _wait_worker_ready(car_code,'',40):
        raise HTTPException(status_code=503,detail='Motor da análise ainda está iniciando.')
    return await _proxy('GET',f'/v1/live/progressive/status/{quote(car_code.upper())}',timeout=10,retries=1)


UI=r'''
<style>
#rxMobilePdf[data-pdf-state="running"]{opacity:.82;cursor:wait}
#rxMobilePdf[data-pdf-state="failed"]{border-color:#754640;color:#ffc2bd}
</style>
<script>
(function(){
 const enc=s=>encodeURIComponent(String(s||''));
 const workerWake='__RX_WORKER_WAKE_URL__';
 const getCurrent=()=>{try{return (typeof current!=='undefined'&&current)?current:window.current}catch(e){return window.current}};
 const goodName=p=>{const n=String(p?.public_name||p?.name||'').trim();return n&&!/^im[oó]vel rural/i.test(n)?n:''};
 let pollTimer=null,pollToken=0,startedAt=0;
 function btn(){return document.querySelector('#rxMobilePdf')}
 function setBtn(text,state){const b=btn();if(!b)return;b.textContent=text;b.dataset.pdfState=state||'';b.disabled=false}
 function tell(t,kind){try{if(typeof window.rxUiStatus==='function')window.rxUiStatus(t,kind||'ok',3000);else if(typeof toast==='function')toast(t)}catch(e){}}
 function wakeWorker(){try{fetch(workerWake,{mode:'no-cors',cache:'no-store'}).catch(()=>{})}catch(e){}}
 function openViewer(code,name){const url=`/v1/mobile/report/view/${enc(code)}?property_name=${enc(name)}`;const standalone=window.matchMedia?.('(display-mode: standalone)').matches||window.navigator.standalone===true;if(standalone){location.href=url;return}const w=window.open(url,'_blank');if(!w)location.href=url}
 async function status(code,name){const r=await fetch(`/v1/mobile/report/status/${enc(code)}?property_name=${enc(name)}`,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'status indisponível');return d}
 async function poll(code,name,token){
   clearTimeout(pollTimer);
   try{
     const d=await status(code,name);if(token!==pollToken)return;
     if(d.state==='ready'){
       window.__rxPdfReady={code};setBtn('ABRIR PDF','ready');
       const sec=Math.max(1,Math.round((Date.now()-startedAt)/1000));tell(`PDF pronto em ${sec}s. Toque em ABRIR PDF.`,'ok');return
     }
     if(d.state==='failed'){
       setBtn('TENTAR PDF','failed');tell('A geração do PDF falhou. Toque para tentar novamente.','bad');return
     }
     const sec=Math.max(0,Math.round((Date.now()-startedAt)/1000));setBtn(`PDF PREPARANDO · ${sec}s`,'running');
     pollTimer=setTimeout(()=>poll(code,name,token),1400);
   }catch(e){if(token!==pollToken)return;const sec=Math.max(0,Math.round((Date.now()-startedAt)/1000));setBtn(`PDF PREPARANDO · ${sec}s`,'running');pollTimer=setTimeout(()=>poll(code,name,token),2200)}
 }
 async function prepare(p){
   if(!p?.car_code)return;const code=p.car_code,name=goodName(p),token=++pollToken;startedAt=Date.now();
   wakeWorker();
   setBtn('PDF PREPARANDO · 0s','running');tell('Preparando o motor e gerando o PDF. Você não precisa tocar novamente.','ok');
   try{
     const r=await fetch(`/v1/mobile/report/prepare/${enc(code)}?property_name=${enc(name)}`,{method:'POST',cache:'no-store'});
     const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'prepare falhou');
     poll(code,name,token)
   }catch(e){setBtn('TENTAR PDF','failed');tell('Não foi possível iniciar o PDF: '+(e.message||e),'bad')}
 }
 function bindPdf(p){
   const old=btn();if(!old)return;const b=old.cloneNode(true);old.replaceWith(b);
   const code=p?.car_code;
   if(window.__rxPdfReady?.code===code)setBtn('ABRIR PDF','ready');else setBtn('GERAR PDF','idle');
   b.addEventListener('click',()=>{
     const cur=getCurrent()||p,c=cur?.car_code,name=goodName(cur);if(!c){tell('Selecione uma propriedade antes de gerar o PDF.','bad');return}
     if(b.dataset.pdfState==='running'){tell('O PDF está sendo preparado. Não é necessário tocar novamente.','ok');return}
     if(b.dataset.pdfState==='ready'||window.__rxPdfReady?.code===c){openViewer(c,name);return}
     prepare(cur)
   });
 }
 function install(){
   if(window.__rxPdfV25Installed)return;window.__rxPdfV25Installed=true;
   document.addEventListener('click',e=>{const t=e.target?.closest?.('#rx43Full,#rx43Pdf');if(t)wakeWorker()},true);
   const previous=window.showProperty;
   if(previous)window.showProperty=showProperty=function(p,g){clearTimeout(pollTimer);++pollToken;const out=previous(p,g);setTimeout(()=>bindPdf(getCurrent()||p),120);return out};
   setTimeout(()=>{const cur=getCurrent();if(cur?.car_code)bindPdf(cur)},700);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''.replace('__RX_WORKER_WAKE_URL__',WORKER+'/')

if 'RX_PDF_V43_8' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PDF_V43_8 --><!-- RX_BROWSER_WORKER_WAKE_V43_9_3 --><!-- RX_DEEP_WORKER_WAKE_V43_9_4 --></body>')

print('RX_PORTAL_PDF_V43_9_4=browser_wake_deep_and_pdf_viewer_return_to_app',flush=True)

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import portal_v8
from property_identity_runtime import resolve_property_identity_sync

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


async def _wait_worker_ready(car_code:str,property_name:str='',max_wait:float=40.0)->bool:
    """Wake the report service and wait until the deferred V41 status route exists.

    Render may accept traffic before the report extensions finish registering. A root
    200 is therefore not enough; readiness is the real PDF status route returning a
    JSON state. The wait is bounded and happens only when a report is requested.
    """
    code=str(car_code or '').upper();name=_provided_name(property_name)
    path=f'/v1/reports/property/{quote(code)}/status'
    deadline=time.monotonic()+max(5.0,float(max_wait));attempt=0
    async with httpx.AsyncClient(timeout=httpx.Timeout(7,connect=4),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.8.3'}) as c:
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
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.8.3'}) as c:
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
    if not await _wait_worker_ready(car_code,name,40):
        raise HTTPException(status_code=503,detail='Motor do relatório está iniciando. Tente novamente em alguns segundos.')
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


@app.get('/v1/live/quick/{car_code}')
async def portal_quick_proxy(car_code:str,deep:bool=False):
    return await _proxy('GET',f'/v1/live/quick/{quote(car_code.upper())}',params={'deep':'1' if deep else '0'},timeout=22,retries=1)


@app.get('/v1/live/progressive/status/{car_code}')
async def portal_progress_proxy(car_code:str):
    return await _proxy('GET',f'/v1/live/progressive/status/{quote(car_code.upper())}',timeout=10,retries=1)


UI=r'''
<style>
#rxMobilePdf[data-pdf-state="running"]{opacity:.82;cursor:wait}
#rxMobilePdf[data-pdf-state="failed"]{border-color:#754640;color:#ffc2bd}
</style>
<script>
(function(){
 const enc=s=>encodeURIComponent(String(s||''));
 const getCurrent=()=>{try{return (typeof current!=='undefined'&&current)?current:window.current}catch(e){return window.current}};
 const goodName=p=>{const n=String(p?.public_name||p?.name||'').trim();return n&&!/^im[oó]vel rural/i.test(n)?n:''};
 let pollTimer=null,pollToken=0,startedAt=0;
 function btn(){return document.querySelector('#rxMobilePdf')}
 function setBtn(text,state){const b=btn();if(!b)return;b.textContent=text;b.dataset.pdfState=state||'';b.disabled=false}
 function tell(t,kind){try{if(typeof window.rxUiStatus==='function')window.rxUiStatus(t,kind||'ok',3000);else if(typeof toast==='function')toast(t)}catch(e){}}
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
     if(b.dataset.pdfState==='ready'||window.__rxPdfReady?.code===c){window.open(`/v1/mobile/report/open/${enc(c)}?property_name=${enc(name)}`,'_blank');return}
     prepare(cur)
   });
 }
 function install(){
   if(window.__rxPdfV25Installed)return;window.__rxPdfV25Installed=true;
   const previous=window.showProperty;
   if(previous)window.showProperty=showProperty=function(p,g){clearTimeout(pollTimer);++pollToken;const out=previous(p,g);setTimeout(()=>bindPdf(getCurrent()||p),120);return out};
   setTimeout(()=>{const cur=getCurrent();if(cur?.car_code)bindPdf(cur)},700);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'RX_PDF_V43_8' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PDF_V43_8 --></body>')

print('RX_PORTAL_PDF_V43_8_3=worker_readiness_wait_named_report',flush=True)

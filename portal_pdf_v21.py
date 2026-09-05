from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import portal_v8

app=portal_v8.app
WORKER=os.getenv('RX_REPORT_WORKER_URL','https://raio-x-territorial-report.onrender.com').rstrip('/')


async def _proxy(method:str,path:str,params=None,timeout=25):
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V24'}) as c:
            r=await c.request(method,WORKER+path,params=params)
            try:data=r.json()
            except Exception:data={'detail':r.text[:300]}
        if r.status_code>=400:raise HTTPException(status_code=r.status_code,detail=data.get('detail') or 'worker indisponível')
        return data
    except HTTPException:raise
    except Exception as e:raise HTTPException(status_code=502,detail=f'Worker de análise indisponível: {type(e).__name__}')


@app.post('/v1/mobile/report/prepare/{car_code}')
@app.get('/v1/mobile/report/prepare/{car_code}')
async def mobile_report_prepare(car_code:str,property_name:str|None=None):
    return await _proxy('POST',f'/v1/reports/property/{quote(car_code.upper())}/prepare',{'property_name':property_name or ''},12)


@app.get('/v1/mobile/report/status/{car_code}')
async def mobile_report_status(car_code:str,property_name:str|None=None):
    return await _proxy('GET',f'/v1/reports/property/{quote(car_code.upper())}/status',{'property_name':property_name or ''},10)


@app.get('/v1/mobile/report/open/{car_code}')
async def mobile_report_open(car_code:str,property_name:str|None=None):
    url=f'{WORKER}/v1/reports/property/{quote(car_code.upper())}'
    if property_name:url+='?property_name='+quote(property_name)
    return RedirectResponse(url=url,status_code=307)


@app.get('/v1/live/quick/{car_code}')
async def portal_quick_proxy(car_code:str,deep:bool=False):
    return await _proxy('GET',f'/v1/live/quick/{quote(car_code.upper())}',params={'deep':'1' if deep else '0'},timeout=22)


@app.get('/v1/live/progressive/status/{car_code}')
async def portal_progress_proxy(car_code:str):
    return await _proxy('GET',f'/v1/live/progressive/status/{quote(car_code.upper())}',timeout=10)


UI=r'''
<script>
(function(){
 const enc=s=>encodeURIComponent(String(s||''));
 const getCurrent=()=>{try{return (typeof current!=='undefined'&&current)?current:window.current}catch(e){return window.current}};
 const goodName=p=>{const n=String(p?.name||'').trim();return n&&!/^im[oó]vel rural/i.test(n)?n:''};
 let pollTimer=null,prewarmTimer=null,pollToken=0,selectionToken=0;
 function btn(){return document.querySelector('#rxMobilePdf')}
 function setBtn(text,state){const b=btn();if(!b)return;b.textContent=text;b.dataset.pdfState=state||'';b.disabled=false}
 function toastSafe(t){try{if(typeof toast==='function')toast(t)}catch(e){}}
 async function status(code,name){const r=await fetch(`/v1/mobile/report/status/${enc(code)}?property_name=${enc(name)}`,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'status indisponível');return d}
 async function poll(code,name,token){
   clearTimeout(pollTimer);
   try{
     const d=await status(code,name);if(token!==pollToken)return;
     if(d.state==='ready'){window.__rxPdfReady={code};setBtn('ABRIR PDF','ready');return}
     if(d.state==='failed'){setBtn('TENTAR PDF','failed');return}
     setBtn('PDF PREPARANDO…','running');pollTimer=setTimeout(()=>poll(code,name,token),1600);
   }catch(e){if(token!==pollToken)return;setBtn('PDF PREPARANDO…','running');pollTimer=setTimeout(()=>poll(code,name,token),2400)}
 }
 async function prepare(p){
   if(!p?.car_code)return;const code=p.car_code,name=goodName(p),token=++pollToken;
   setBtn('PDF PREPARANDO…','running');
   try{await fetch(`/v1/mobile/report/prepare/${enc(code)}?property_name=${enc(name)}`,{method:'POST',cache:'no-store'}).then(async r=>{if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'prepare falhou')}})}catch(e){}
   poll(code,name,token);
 }
 function schedulePrepare(p){
   clearTimeout(prewarmTimer);const token=++selectionToken;
   prewarmTimer=setTimeout(()=>{if(token===selectionToken){const cur=getCurrent()||p;if(cur?.car_code===p?.car_code)prepare(cur)}},2800);
 }
 function bindPdf(p){
   const old=btn();if(!old)return;const b=old.cloneNode(true);old.replaceWith(b);
   const code=p?.car_code;
   if(window.__rxPdfReady?.code===code)setBtn('ABRIR PDF','ready');else setBtn('GERAR PDF','idle');
   b.addEventListener('click',async()=>{
     const cur=getCurrent()||p,c=cur?.car_code,name=goodName(cur);if(!c)return;
     if(b.dataset.pdfState==='ready'||window.__rxPdfReady?.code===c){window.open(`/v1/mobile/report/open/${enc(c)}?property_name=${enc(name)}`,'_blank');return}
     clearTimeout(prewarmTimer);
     toastSafe('Preparando o dossiê completo. Você pode continuar usando o mapa enquanto o PDF é gerado.');
     prepare(cur);
   });
 }
 function install(){
   if(window.__rxPdfV24Installed)return;window.__rxPdfV24Installed=true;
   const previous=window.showProperty;
   if(previous)window.showProperty=showProperty=function(p,g){
     ++selectionToken;clearTimeout(prewarmTimer);clearTimeout(pollTimer);++pollToken;
     const out=previous(p,g);setTimeout(()=>{const cur=getCurrent()||p;bindPdf(cur);schedulePrepare(cur)},120);return out
   };
   setTimeout(()=>{const cur=getCurrent();if(cur?.car_code){bindPdf(cur);schedulePrepare(cur)}},700);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if 'RX_PDF_V24' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PDF_V24 --></body>')

print('RX_PORTAL_PDF_V24=debounced_prewarm_worker_proxy_deep_intent',flush=True)

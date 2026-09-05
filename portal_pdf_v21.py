from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import portal_v8
from property_identity_runtime import resolve_property_identity_sync

app=portal_v8.app
WORKER=os.getenv('RX_REPORT_WORKER_URL','https://raio-x-territorial-report.onrender.com').rstrip('/')


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


async def _proxy(method:str,path:str,params=None,timeout=25):
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.8'}) as c:
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
    name=await _report_name(car_code,property_name)
    return await _proxy('POST',f'/v1/reports/property/{quote(car_code.upper())}/prepare',{'property_name':name},12)


@app.get('/v1/mobile/report/status/{car_code}')
async def mobile_report_status(car_code:str,property_name:str|None=None):
    name=await _report_name(car_code,property_name)
    return await _proxy('GET',f'/v1/reports/property/{quote(car_code.upper())}/status',{'property_name':name},10)


@app.get('/v1/mobile/report/open/{car_code}')
async def mobile_report_open(car_code:str,property_name:str|None=None):
    name=await _report_name(car_code,property_name)
    url=f'{WORKER}/v1/reports/property/{quote(car_code.upper())}'
    if name:url+='?property_name='+quote(name)
    return RedirectResponse(url=url,status_code=307)


@app.get('/v1/live/quick/{car_code}')
async def portal_quick_proxy(car_code:str,deep:bool=False):
    return await _proxy('GET',f'/v1/live/quick/{quote(car_code.upper())}',params={'deep':'1' if deep else '0'},timeout=22)


@app.get('/v1/live/progressive/status/{car_code}')
async def portal_progress_proxy(car_code:str):
    return await _proxy('GET',f'/v1/live/progressive/status/{quote(car_code.upper())}',timeout=10)


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
   setBtn('PDF PREPARANDO · 0s','running');tell('Geração do PDF iniciada. O progresso ficará visível no botão.','ok');
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
     if(b.dataset.pdfState==='running'){tell('O PDF ainda está sendo gerado.','ok');return}
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

print('RX_PORTAL_PDF_V43_8=identity_resolved_worker_delegation',flush=True)

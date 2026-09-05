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
    code=str(car_code or '').upper();name=_provided_name(property_name)
    path=f'/v1/reports/property/{quote(code)}/status'
    deadline=time.monotonic()+max(5.0,float(max_wait));attempt=0
    async with httpx.AsyncClient(timeout=httpx.Timeout(7,connect=4),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.9.5'}) as c:
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
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/V43.9.5'}) as c:
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
    attr_code=html_lib.escape(code,quote=True)
    attr_name=html_lib.escape(name,quote=True)
    pdf_url=f'/v1/mobile/report/open/{quote(code)}'
    if name:pdf_url+='?property_name='+quote(name)
    safe_pdf=html_lib.escape(pdf_url,quote=True)
    worker_attr=html_lib.escape(WORKER+'/',quote=True)
    page=f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{safe_title} · Raio-X Territorial</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;background:#07140e;color:#edf7f1;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}body{{display:flex;flex-direction:column;overflow:hidden}}header{{min-height:62px;padding:10px max(12px,env(safe-area-inset-right)) 10px max(12px,env(safe-area-inset-left));display:flex;align-items:center;gap:10px;border-bottom:1px solid #214033;background:#0a1d14;z-index:2}}.back,.open,.retry{{height:40px;border:1px solid #315745;border-radius:11px;padding:0 14px;background:#102a1d;color:#edf7f1;font-weight:800;font-size:12px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}}.back{{background:#65dfaa;color:#07140e;border-color:#65dfaa}}.title{{min-width:0;flex:1}}.title strong{{display:block;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.title small{{display:block;margin-top:2px;color:#92ad9f;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.stage{{flex:1;display:grid;place-items:center;padding:24px;text-align:center}}.stage .box{{max-width:520px}}.spin{{width:48px;height:48px;border:3px solid #204332;border-top-color:#65dfaa;border-radius:50%;animation:s 1s linear infinite;margin:0 auto 16px}}@keyframes s{{to{{transform:rotate(360deg)}}}}.stage h2{{font-size:17px;margin:0 0 7px}}.stage p{{color:#9bb1a6;font-size:11px;line-height:1.5;margin:0}}.retry{{margin-top:14px}}iframe{{border:0;width:100%;flex:1;background:#fff}}[hidden]{{display:none!important}}@media(max-width:640px){{header{{min-height:58px;padding-top:max(8px,env(safe-area-inset-top))}}.open{{padding:0 9px;font-size:10px}}.title strong{{font-size:12px}}.title small{{font-size:9px}}.back{{padding:0 10px;font-size:10px}}}}
</style></head><body data-code="{attr_code}" data-name="{attr_name}" data-worker="{worker_attr}" data-pdf="{safe_pdf}"><header><button class="back" type="button" onclick="returnToRaioX()">← VOLTAR AO RAIO-X</button><div class="title"><strong>{safe_title}</strong><small id="rxViewerStatus">Preparando relatório · {safe_code}</small></div><a class="open" id="rxOpenPdf" href="{safe_pdf}" target="_blank" rel="noopener" hidden>ABRIR PDF</a></header><main class="stage" id="rxViewerStage"><div class="box"><div class="spin"></div><h2>Gerando relatório territorial</h2><p id="rxViewerDetail">Acordando o motor e reunindo as fontes. Esta tela pode permanecer aberta.</p><button class="retry" id="rxViewerRetry" type="button" hidden>TENTAR NOVAMENTE</button></div></main><iframe id="rxPdfFrame" title="{safe_title}" hidden></iframe><script>
const body=document.body,code=body.dataset.code,name=body.dataset.name,worker=body.dataset.worker,pdf=body.dataset.pdf;
const qs=s=>document.querySelector(s),sleep=ms=>new Promise(r=>setTimeout(r,ms));
function returnToRaioX(){{try{{if(window.opener&&!window.opener.closed){{window.opener.focus();window.close();return}}}}catch(e){{}}if(history.length>1){{history.back()}}else{{location.href='/'}}}}
function wake(){{try{{fetch(worker,{{mode:'no-cors',cache:'no-store'}}).catch(()=>{{}})}}catch(e){{}}}}
function detail(t){{qs('#rxViewerDetail').textContent=t}}
async function status(){{const r=await fetch(`/v1/mobile/report/status/${{encodeURIComponent(code)}}?property_name=${{encodeURIComponent(name)}}`,{{cache:'no-store'}}),d=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(d.detail||'status indisponível');return d}}
async function start(){{
 qs('#rxViewerRetry').hidden=true;qs('#rxViewerStage').hidden=false;qs('#rxPdfFrame').hidden=true;wake();detail('Acordando o motor e preparando o relatório. Você não precisa voltar ao aplicativo.');
 try{{
  const r=await fetch(`/v1/mobile/report/prepare/${{encodeURIComponent(code)}}?property_name=${{encodeURIComponent(name)}}`,{{method:'POST',cache:'no-store'}}),d=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(d.detail||'não foi possível iniciar');
  for(let i=0;i<150;i++){{await sleep(i<8?1000:1800);const s=await status();if(s.state==='ready'){{qs('#rxViewerStatus').textContent='PDF pronto · '+code;qs('#rxOpenPdf').hidden=false;qs('#rxViewerStage').hidden=true;const f=qs('#rxPdfFrame');f.src=pdf;f.hidden=false;return}}if(s.state==='failed')throw new Error(s.detail||'geração do PDF falhou');detail('Gerando relatório · '+Math.max(1,Math.round((i+1)*(i<8?1:1.8)))+'s')}}
  throw new Error('tempo limite de geração excedido');
 }}catch(e){{qs('#rxViewerStatus').textContent='Não foi possível concluir';detail(String(e.message||e));qs('#rxViewerRetry').hidden=false}}
}}
qs('#rxViewerRetry').onclick=start;start();
</script><!-- RX_PDF_VIEWER_V43_9_3 --><!-- RX_PDF_VIEWER_AUTOSTART_V43_9_5 --></body></html>'''
    return HTMLResponse(page,headers={'Cache-Control':'no-store'})


@app.get('/v1/live/quick/{car_code}')
async def portal_quick_proxy(car_code:str,deep:bool=False):
    if deep and not await _wait_worker_ready(car_code,'',70):
        raise HTTPException(status_code=503,detail='Motor da análise ainda está iniciando.')
    return await _proxy('GET',f'/v1/live/quick/{quote(car_code.upper())}',params={'deep':'1' if deep else '0'},timeout=22,retries=1)


@app.get('/v1/live/progressive/status/{car_code}')
async def portal_progress_proxy(car_code:str):
    if not await _wait_worker_ready(car_code,'',40):
        raise HTTPException(status_code=503,detail='Motor da análise ainda está iniciando.')
    return await _proxy('GET',f'/v1/live/progressive/status/{quote(car_code.upper())}',timeout=10,retries=1)


UI=r'''
<script>
(function(){
 const enc=s=>encodeURIComponent(String(s||''));
 const workerWake='__RX_WORKER_WAKE_URL__';
 const getCurrent=()=>{try{return (typeof current!=='undefined'&&current)?current:window.current}catch(e){return window.current}};
 const goodName=p=>{const n=String(p?.public_name||p?.name||'').trim();return n&&!/^im[oó]vel rural/i.test(n)?n:''};
 function wakeWorker(){try{fetch(workerWake,{mode:'no-cors',cache:'no-store'}).catch(()=>{})}catch(e){}}
 function openViewer(code,name){const url=`/v1/mobile/report/view/${enc(code)}?property_name=${enc(name)}`;wakeWorker();const standalone=window.matchMedia?.('(display-mode: standalone)').matches||window.navigator.standalone===true;if(standalone){location.href=url;return}const w=window.open(url,'_blank');if(!w)location.href=url}
 function launchReport(){const cur=getCurrent(),code=cur?.car_code,name=goodName(cur);if(!code){try{window.rxUiStatus?.('Selecione uma propriedade antes de gerar o PDF.','bad',3000)}catch(e){}return false}openViewer(code,name);return false}
 function install(){
   document.addEventListener('click',e=>{const t=e.target?.closest?.('#rx43Full');if(t)wakeWorker()},true);
   window.downloadPDF=launchReport;try{downloadPDF=launchReport}catch(e){}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(install,0));else setTimeout(install,0);
 window.rxOpenReportViewer=launchReport;
})();
</script>
'''.replace('__RX_WORKER_WAKE_URL__',WORKER+'/')

if 'RX_PDF_V43_8' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PDF_V43_8 --><!-- RX_BROWSER_WORKER_WAKE_V43_9_3 --><!-- RX_DEEP_WORKER_WAKE_V43_9_4 --><!-- RX_UNIFIED_PDF_ACTION_V43_9_5 --></body>')

print('RX_PORTAL_PDF_V43_9_5=unified_autostart_viewer_return_to_app',flush=True)

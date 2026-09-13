"""C2a shared card helpers, defined in <head> so every body script can use them.

window.rxNum      pt-BR numbers. Missing/''/NaN -> '' (the caller hides the row);
                  real 0 -> '0,00'; 0 < |x| < 0,005 -> '< 0,01'.
window.rxDateBR   dd/mm/aaaa in America/Sao_Paulo; unparseable -> ''.
window.rxCopyCarC2 one-tap copy of the CAR code. It reports success only after the
                  clipboard write really succeeded; on failure it shows the full code as a
                  selectable fallback and never the word "copiado"; results are
                  announced through one persistent role=status region.
window.rxSigefRefC2 C2b: the SIGEF/INCRA cadastral reference block shared by the anchored card
                  and the V45 panel. Shown only for sigef_reference_state 'found' with a share
                  of the CAR >= 50% (floored to 2 decimals, never rounded up to 100,00%); when
                  the parcel is much larger than the property it also says how little of the
                  parcel the property occupies. 'unavailable' stays hidden until ONE automatic
                  retry per CAR (setTimeout, only while that selection is still open) has come
                  back; then a discreet "consulta pendente" line. 'incomplete' (an answer that a
                  retry cannot change) stays hidden. It never writes a title.
"""

from __future__ import annotations

import portal_v8

MARKER = "RX_NUMBER_FORMAT_C2"

HEAD = r'''<style id="rxCardFormatC2">
.rx-car-copy{display:block;position:relative;width:100%;margin:0;padding:6px 30px 6px 8px;border:1px solid #2a493b;border-radius:9px;background:#0c1d16;color:#e3f1e9;font:700 12px/1.3 ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;letter-spacing:0;text-align:left;white-space:normal;overflow-wrap:normal;word-break:normal;cursor:copy;-webkit-user-select:text;user-select:text}
.rx-car-copy:hover{border-color:#63e6a5}.rx-car-copy:focus-visible{outline:2px solid #63e6a5;outline-offset:2px}
.rx-car-copy::after{content:'';position:absolute;right:9px;top:50%;width:14px;height:14px;transform:translateY(-50%);background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2363e6a5' stroke-width='1.6'%3E%3Crect x='5.5' y='5.5' width='8.5' height='8.5' rx='1.5'/%3E%3Cpath d='M10.5 3.5V3a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h.5'/%3E%3C/svg%3E") center/contain no-repeat}
.rx-car-copy-wrap{display:block;position:relative;min-width:0}
.rx-copy-feedback:empty{display:none}.rx-copy-feedback{position:absolute;inset:0;z-index:2;display:flex;flex-direction:column;justify-content:center;gap:3px;padding:3px 6px;border-radius:9px;background:#0a1c14;border:1px solid #63e6a5;font:800 10px/1.25 system-ui,-apple-system,Segoe UI,sans-serif;letter-spacing:0;text-transform:none;color:#9fe9c2;pointer-events:none}
.rx-copy-feedback[data-state="fail"]{padding:0;border:0;background:transparent;pointer-events:auto}.rx-copy-feedback .rx-copy-ok{text-align:center}
.rx-copy-feedback .rx-copy-fail{position:absolute;left:0;right:0;top:100%;margin-top:3px;padding:3px 6px;border-radius:6px;background:#2d2410;color:#ffd77d;font:800 10px/1.25 system-ui,-apple-system,Segoe UI,sans-serif;text-align:center;pointer-events:none;z-index:3}
.rx-copy-fallback{display:block;box-sizing:border-box;width:100%;height:100%;margin:0;padding:6px 30px 6px 8px;border:1px solid #f5c96a;border-radius:9px;background:#07150f;color:#f4fff8;font:700 12px/1.3 ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;letter-spacing:0;text-align:left;white-space:normal;overflow-wrap:normal;word-break:normal;cursor:text;-webkit-user-select:all;user-select:all;-webkit-touch-callout:default}
.rx-copy-fallback:focus{outline:2px solid #f5c96a;outline-offset:2px}
.rx-sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0 0 0 0)!important;white-space:nowrap!important;border:0!important}
@media(max-width:720px),(pointer:coarse){.rx-car-copy{min-height:44px}}
.rx-sigef-ref{display:grid;gap:2px;min-width:0;margin:0;padding:6px 8px;border:1px solid #24473a;border-radius:9px;background:#0c1d16;color:#d3e4da;font:600 10px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0;text-align:left;overflow-wrap:anywhere}
.rx-sigef-ref>*{display:block;min-width:0}
.rx-sigef-ref-k{font-size:9px;font-weight:800;color:#a9bfb4}
.rx-sigef-ref-name{font-size:11px;font-weight:800;line-height:1.28;color:#eef8f2}
.rx-sigef-ref-pct{font-size:10px;font-weight:700;color:#9fe9c2}
.rx-sigef-ref-note,.rx-sigef-ref-more{font-size:9px;font-weight:600;color:#b9ccc2}
.rx-sigef-ref-origin{font-size:9px;font-weight:600;color:#9fb5aa}
.rx-sigef-ref-panel{padding:8px 10px;border-radius:10px}
.rx-sigef-ref-pending{min-width:0;margin:0;font:600 9px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;color:#9fb5aa;overflow-wrap:anywhere}
[data-rx-sigef-slot]:empty{display:none}
</style>
<script id="rxNumberFormatC2">
(function(){
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmts={};
 const intl=d=>fmts[d]||(fmts[d]=new Intl.NumberFormat('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d}));
 function parse(v){
   if(v===null||v===undefined||typeof v==='boolean')return null;
   let n;
   if(typeof v==='number')n=v;
   else{let s=String(v).trim();if(!s)return null;if(s.includes(','))s=s.replace(/\./g,'').replace(',','.');n=Number(s)}
   if(!Number.isFinite(n))return null;
   return n===0?0:n;
 }
 function num(v,d){
   d=Number.isInteger(d)&&d>=0&&d<=6?d:2;
   const n=parse(v);if(n===null)return '';
   const step=Math.pow(10,-d);
   if(d>0&&n!==0&&Math.abs(n)<step/2)return '< '+intl(d).format(step);
   const out=intl(d).format(n);
   return /^-0(,0+)?$/.test(out)?out.slice(1):out;
 }
 window.rxNum={parse,num,ha:(v,d)=>{const s=num(v,d===undefined?2:d);return s?s+' ha':''},int:v=>num(v,0),pct:(v,d)=>{const s=num(v,d===undefined?2:d);return s?s+'%':''}};

 let dateFmt=null;
 try{dateFmt=new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'2-digit',year:'numeric'})}catch(e){dateFmt=null}
 window.rxDateBR=function(v){
   if(v===null||v===undefined)return '';
   const s=String(v).trim();if(!s)return '';
   const d=s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
   if(d){const mo=Number(d[2]),da=Number(d[3]);return mo>=1&&mo<=12&&da>=1&&da<=31?`${d[3]}/${d[2]}/${d[1]}`:''}
   if(!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s)||!dateFmt)return '';
   const t=new Date(s.replace(' ','T'));
   return Number.isFinite(t.getTime())?dateFmt.format(t):'';
 };

 const states=new Map();
 function codeHtml(car){
   const m=String(car||'').match(/^([A-Za-z]{2}-\d{7}-)([0-9A-Fa-f]{16})([0-9A-Fa-f]{16})$/);
   return m?`${esc(m[1])}<wbr>${esc(m[2])}<wbr>${esc(m[3])}`:esc(car).replace(/-/g,'-<wbr>');
 }
 function button(car){
   const c=String(car||'').trim();if(!c)return '';
   return `<span class="rx-car-copy-wrap"><button type="button" class="rx-car-copy" data-rx-copy-car="${esc(c)}" aria-label="Copiar código do CAR ${esc(c)}" title="Copiar código do CAR">${codeHtml(c)}</button>${feedback(c)}</span>`;
 }
 function feedbackInner(car){
   const st=states.get(car);if(!st)return '';
   if(st.state==='ok')return '<span class="rx-copy-ok" aria-hidden="true">✓ Código do CAR copiado</span>';
   // Failure: the full code, laid out exactly like the button, selectable in one gesture
   // (user-select:all on a non-input keeps the long-press from picking one hyphen group).
   return `<span class="rx-copy-fallback" data-rx-copy-fallback data-rx-copy-retry="${esc(car)}" tabindex="-1" role="textbox" aria-readonly="true" aria-label="Código do CAR para copiar: ${esc(car)}">${codeHtml(car)}</span><span class="rx-copy-fail" aria-hidden="true">Toque e segure para copiar</span>`;
 }
 function feedback(car){
   const c=String(car||'').trim();if(!c)return '';
   const st=states.get(c);
   return `<span class="rx-copy-feedback" data-rx-copy-feedback data-rx-copy-for="${esc(c)}"${st?` data-state="${st.state}"`:''}>${feedbackInner(c)}</span>`;
 }
 // One persistent status region outside every re-rendered popup/panel, so screen readers
 // announce the result reliably (a live region created together with its text is often skipped).
 let liveTimer=0;
 function live(){
   let el=document.getElementById('rxCopyLiveC2');
   if(!el&&document.body){el=document.createElement('div');el.id='rxCopyLiveC2';el.className='rx-sr-only';el.setAttribute('role','status');el.setAttribute('aria-live','polite');document.body.appendChild(el)}
   return el;
 }
 function announce(msg){const el=live();if(!el)return;clearTimeout(liveTimer);el.textContent='';if(msg)liveTimer=setTimeout(()=>{el.textContent=msg},60)}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',live);else live();
 function paint(car){
   const st=states.get(car);
   document.querySelectorAll('[data-rx-copy-feedback]').forEach(box=>{
     if(box.dataset.rxCopyFor!==car)return;
     const hadFocus=box.contains(document.activeElement);
     if(st)box.dataset.state=st.state;else delete box.dataset.state;
     box.innerHTML=feedbackInner(car);
     // Never drop keyboard focus to <body> when the fallback goes away.
     if(hadFocus&&(!st||st.state!=='fail')){try{box.parentElement?.querySelector('[data-rx-copy-car]')?.focus({preventScroll:true})}catch(e){}}
   });
 }
 async function write(text){
   try{const c=navigator.clipboard;if(c&&typeof c.writeText==='function'){await c.writeText(text);return true}}catch(e){}
   const prev=document.activeElement;
   try{
     const t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');
     t.style.cssText='position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none';
     document.body.appendChild(t);t.select();t.setSelectionRange(0,text.length);
     let ok=false;try{ok=document.execCommand('copy')===true}catch(e){ok=false}
     t.remove();
     try{if(prev&&prev!==document.body&&prev.isConnected)prev.focus({preventScroll:true})}catch(e){}
     return ok;
   }catch(e){return false}
 }
 async function copy(car){
   const c=String(car||'').trim();if(!c)return false;
   const ok=await write(c);
   const ttl=ok?3200:15000;
   states.set(c,{state:ok?'ok':'fail',until:Date.now()+ttl});
   paint(c);
   announce(ok?'Código do CAR copiado':'Não foi possível copiar automaticamente. Toque e segure o código para copiar.');
   setTimeout(()=>{const st=states.get(c);if(st&&st.until<=Date.now()){states.delete(c);paint(c);announce('')}},ttl+60);
   return ok;
 }
 function selectFallback(scope,car){
   const el=[...(scope||document).querySelectorAll('[data-rx-copy-fallback]')].find(x=>x.dataset.rxCopyRetry===car);if(!el)return;
   try{el.focus({preventScroll:true});const sel=window.getSelection(),r=document.createRange();r.selectNodeContents(el);sel.removeAllRanges();sel.addRange(r)}catch(x){}
 }
 document.addEventListener('click',e=>{
   const t=e.target;if(!t||!t.closest)return;
   // A failed-copy state belongs to the card where it happened, not to a panel opened later.
   if(t.closest('[data-rx46-action="full"],[data-rx46-action="close"]')){for(const [k,v] of [...states])if(v.state==='fail')states.delete(k);return}
   const btn=t.closest('[data-rx-copy-car]'),retry=btn?null:t.closest('[data-rx-copy-retry]');if(!btn&&!retry)return;
   e.preventDefault();
   const car=btn?btn.dataset.rxCopyCar:retry.dataset.rxCopyRetry,scope=(btn||retry).closest('[data-rx-copy-scope]');
   copy(car).then(ok=>{if(!ok)selectFallback(scope,car)});
 },true);
 window.rxCopyCarC2={copy,button,code:codeHtml};
})();
</script>
<script id="rxSigefRefScriptC2">
(function(){
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const MIN=0.5,RETRY_MS=8000,WINDOW_MS=180000,ORIGIN='SIGEF/INCRA · espelho público IBAMA/PAMGIA';
 const carOf=p=>String(p?.car_code||'').trim().toUpperCase();
 function share(v){if(v===null||v===undefined||v===''||typeof v==='boolean')return null;const n=Number(v);return Number.isFinite(n)&&n>=0&&n<=1?n:null}
 // Floor to 2 decimals of a percent: 0,99996 -> 99,99% (never 100,00%).
 function pct(v){const n=share(v);if(n===null)return '';const basis=Math.floor(Math.round(n*1e6)/100),val=basis/100;return (window.rxNum?window.rxNum.num(val,2):val.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2}))+'%'}
 // Only a query that did not answer is retried; 'incomplete' is an answer a retry cannot change: hidden.
 function state(p){
   const st=String(p?.sigef_reference_state||''),r=p?.sigef_reference;
   if(st==='found'){const o=share(r?.car_overlap_ratio);return r&&String(r.label||'').trim()&&o!==null&&o>=MIN?'found':'hidden'}
   return st==='unavailable'?'unanswered':'hidden';
 }
 const retries=new Map();
 function retried(car){const e=retries.get(car);return !!e&&e.done&&Date.now()-e.at<WINDOW_MS}
 function needsRetry(p){const car=carOf(p);if(!car||state(p)!=='unanswered')return false;const e=retries.get(car);return !e||!e.done||Date.now()-e.at>=WINDOW_MS}
 // One automatic retry per CAR, shared by the card and the panel. A subscriber is served only
 // while its own selection is still open; nobody open at fire time means no request at all.
 function scheduleRetry(car,isOpen,onData){
   car=String(car||'').trim().toUpperCase();if(!car||typeof isOpen!=='function'||typeof onData!=='function')return;
   const open=s=>{try{return s.isOpen()===true}catch(x){return false}};
   let e=retries.get(car);
   if(e&&!e.done){e.subs.push({isOpen,onData});return}
   if(e&&Date.now()-e.at<WINDOW_MS)return;
   e={done:false,at:0,subs:[{isOpen,onData}]};retries.set(car,e);
   setTimeout(async()=>{
     if(!e.subs.some(open)){if(retries.get(car)===e)retries.delete(car);return}
     let d=null;
     try{const r=await fetch(`/v1/live/map-panel/${encodeURIComponent(car)}?sigef_retry=1`),j=await r.json();if(r.ok&&j?.ok)d=j}catch(x){d=null}
     e.done=true;e.at=Date.now();
     e.subs.forEach(s=>{if(open(s)){try{s.onData(d)}catch(x){}}});
   },RETRY_MS);
 }
 function html(p,variant){
   const st=state(p),panel=variant==='panel';
   if(st==='found'){
     const r=p.sigef_reference,n=Number(p.sigef_reference_others),others=Number.isInteger(n)&&n>0?n:0;
     // 200 is not all: a count taken from an answer cut short is a floor ("pelo menos").
     const floor=p.sigef_reference_others_complete===false?'pelo menos ':'';
     const more=panel&&others?`<span class="rx-sigef-ref-more">+${floor}${others} ${others===1?'outra parcela SIGEF cobre':'outras parcelas SIGEF cobrem'} metade ou mais do imóvel</span>`:'';
     const note=panel?'<span class="rx-sigef-ref-note">Referência de outro cadastro, não é o nome do CAR.</span>':'';
     // A parcel much larger than the property: say how little of it the property occupies, never imply identity.
     const po=share(r.parcel_overlap_ratio),within=po!==null&&po<MIN?`<span class="rx-sigef-ref-note" data-rx-sigef-within>o imóvel ocupa ${po<0.0001?'menos de 0,01%':pct(po)} desta parcela</span>`:'';
     return `<div class="rx-sigef-ref${panel?' rx-sigef-ref-panel':''}" data-rx-sigef-ref="found"><small class="rx-sigef-ref-k">Referência INCRA (SIGEF)</small><b class="rx-sigef-ref-name">${esc(String(r.label).trim())}</b><span class="rx-sigef-ref-pct">cobre ${pct(r.car_overlap_ratio)} do imóvel</span>${within}${note}${more}<span class="rx-sigef-ref-origin">${esc(String(r.origin||'').trim()||ORIGIN)}</span></div>`;
   }
   if(st==='unanswered'&&retried(carOf(p)))return '<div class="rx-sigef-ref-pending" data-rx-sigef-ref="pending">Referência INCRA: consulta pendente</div>';
   return '';
 }
 window.rxSigefRefC2={pct,state,html,needsRetry,scheduleRetry,retried};
})();
</script>
<!-- RX_NUMBER_FORMAT_C2 -->
'''

html = portal_v8.PORTAL_HTML
if MARKER not in html:
    if html.count("</head>") != 1:
        raise RuntimeError("c2_card_format_head_anchor_missing")
    portal_v8.PORTAL_HTML = html.replace("</head>", HEAD + "</head>", 1)

print("RX_NUMBER_FORMAT_C2=ptbr_numbers_dates_honest_car_copy_in_head", flush=True)

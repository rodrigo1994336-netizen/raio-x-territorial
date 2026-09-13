"""C2a shared card helpers, defined in <head> so every body script can use them.

window.rxNum      pt-BR numbers. Missing/''/NaN -> '' (the caller hides the row);
                  real 0 -> '0,00'; 0 < |x| < 0,005 -> '< 0,01'.
window.rxDateBR   dd/mm/aaaa in America/Sao_Paulo; unparseable -> ''.
window.rxCopyCarC2 one-tap copy of the CAR code. It reports success only after the
                  clipboard write really succeeded; on failure it shows the full code as a
                  selectable fallback and never the word "copiado"; results are
                  announced through one persistent role=status region.
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
<!-- RX_NUMBER_FORMAT_C2 -->
'''

html = portal_v8.PORTAL_HTML
if MARKER not in html:
    if html.count("</head>") != 1:
        raise RuntimeError("c2_card_format_head_anchor_missing")
    portal_v8.PORTAL_HTML = html.replace("</head>", HEAD + "</head>", 1)

print("RX_NUMBER_FORMAT_C2=ptbr_numbers_dates_honest_car_copy_in_head", flush=True)

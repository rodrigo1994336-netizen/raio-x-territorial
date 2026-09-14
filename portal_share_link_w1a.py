"""W1a: direct link to a property and card share actions.

URL contract
  /?car=<CODE>     opens the anchored card of exactly that CAR (the same path as a search).
                   <CODE> is accepted only when it matches the CAR format with a real UF:
                   UF-NNNNNNN-<32 hex>. Anything else never reaches fetch, the DOM or a
                   message: the parameter is dropped and a discreet notice says the link
                   does not carry a valid CAR code.
  #z/lat/lon       map position; applied once at boot, rewritten on moveend.
  Selecting a property writes ?car=<CODE> with history.replaceState (never pushState): the
  address bar is always shareable and the back button is not polluted. The card close (x)
  and a bare-map click drop the parameter.

Card actions (secondary row under the CTA, 44 px targets on every screen)
  Copiar link      says "Link copiado" only after the clipboard write really succeeded
                   (window.rxCopyCarC2.write); on failure a small sheet shows the link,
                   already selected, and never the word "copiado".
  WhatsApp         a real <a href="https://wa.me/?text=..."> with a short pt-BR text and the
                   link; nothing claims it was sent.

Link origin: RX_PUBLIC_BASE_URL when it is a valid https origin (set it to
https://raioxterritorial.com.br only after the domain answers), otherwise the page's own
origin, so a link never points to a host that does not answer yet.

Title rule is untouched: the card title stays the validated name or the CAR code
(window.rxCardIdentityC2). The link text never carries a property name.
"""

from __future__ import annotations

import json
import os
import re

import portal_v8

MARKER = "RX_SHARE_LINK_W1A"

UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)
# One pattern for Python (gate) and JS (browser): uppercase only, anchored, fixed length 43.
CAR_PATTERN = r"^(?:" + "|".join(UFS) + r")-\d{7}-[0-9A-F]{32}$"
_CAR_RE = re.compile(CAR_PATTERN)
_BASE_RE = re.compile(
    r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?::\d{2,5})?$"
)


def normalize_car(value: object) -> str:
    code = str(value if value is not None else "").strip().upper()
    return code if len(code) == 43 and _CAR_RE.fullmatch(code) else ""


def public_base(raw: str | None) -> str:
    value = str(raw or "").strip().rstrip("/").lower()
    return value if _BASE_RE.fullmatch(value) else ""


PUBLIC_BASE = public_base(os.getenv("RX_PUBLIC_BASE_URL"))

UI = r'''
<style id="rxShareLinkW1a">
.rx-share-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
/* .rx-share-row prefix: beats Leaflet's '.leaflet-container a' link colour inside the popup */
.rx-share-row .rx-share-btn,.rx-share-row .rx-share-btn:visited{display:flex;align-items:center;justify-content:center;gap:5px;min-width:0;min-height:44px;margin:0;padding:0 6px;border:1px solid #2a493b;border-radius:9px;background:#0c1d16;color:#dceae2;font:800 10px/1.15 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0;text-align:center;text-decoration:none;white-space:nowrap;cursor:pointer;-webkit-tap-highlight-color:transparent}
.rx-share-row .rx-share-btn:hover{border-color:#63e6a5;color:#f4fff8}.rx-share-row .rx-share-btn:focus-visible{outline:2px solid #63e6a5;outline-offset:2px}
.rx-share-btn *{pointer-events:none}.rx-share-btn svg{flex:0 0 14px;width:14px;height:14px}.rx-share-btn .rx-share-ok,.rx-share-btn[data-state="ok"] .rx-share-link{display:none}.rx-share-btn[data-state="ok"] .rx-share-ok{display:block}
.rx-share-row .rx-share-btn[data-state="ok"]{border-color:#63e6a5;background:#10251c;color:#9fe9c2}
.rx-share-state{position:fixed;z-index:1200;left:50%;top:calc(env(safe-area-inset-top,0px) + 78px);transform:translateX(-50%);display:flex;align-items:center;gap:8px;width:max-content;max-width:calc(100vw - 24px);min-height:44px;padding:4px 4px 4px 12px;border:1px solid #2a493b;border-radius:12px;background:rgba(7,21,15,.97);color:#eef8f2;box-shadow:0 10px 30px #0008;font:700 12px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif}
.rx-share-state[data-kind="busy"]{padding-right:12px}
.rx-share-state-text{min-width:0;overflow-wrap:anywhere}
.rx-share-state button{flex:0 0 auto;min-width:44px;min-height:44px;margin:0;border:0;border-radius:9px;background:transparent;color:#c5d5cd;font:800 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer}
.rx-share-state .rx-share-state-retry{padding:0 10px;border:1px solid #63e6a5;color:#9fe9c2}
.rx-share-state .rx-share-state-x{font-size:20px}
.rx-share-sheet{position:fixed;z-index:1300;left:50%;bottom:calc(env(safe-area-inset-bottom,0px) + 16px);transform:translateX(-50%);width:min(420px,calc(100vw - 24px))}
.rx-share-sheet-box{display:grid;gap:8px;padding:12px;border:1px solid #f5c96a;border-radius:14px;background:rgba(7,21,15,.985);color:#eef8f2;box-shadow:0 18px 52px #0009;font:600 12px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.rx-share-sheet-box b{font-size:13px;color:#ffd77d}.rx-share-sheet-box p{margin:0;color:#c5d5cd}
.rx-share-sheet-box textarea{box-sizing:border-box;width:100%;min-height:64px;margin:0;padding:8px;border:1px solid #2a493b;border-radius:9px;background:#0c1d16;color:#f4fff8;font:700 12px/1.35 ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;resize:none;overflow-wrap:anywhere;-webkit-user-select:all;user-select:all}
.rx-share-sheet-close{justify-self:end;min-width:88px;min-height:44px;border:1px solid #63e6a5;border-radius:9px;background:#63e6a5;color:#052116;font:900 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer}
.rx-share-state[hidden],.rx-share-state [hidden],.rx-share-sheet[hidden]{display:none!important}
</style>
<script id="rxShareLinkScriptW1a">
(function(){
 const CAR=new RegExp(__CAR_PATTERN__);
 const BASE=__PUBLIC_BASE__;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function norm(v){const s=String(v??'').trim().toUpperCase();return s.length===43&&CAR.test(s)?s:''}
 function link(car){const c=norm(car);return c?`${BASE||location.origin}/?car=${c}`:''}
 function waText(car,place){const l=link(car);if(!l)return '';const pl=[...String(place??'')].map(ch=>{const n=ch.charCodeAt(0);return n<32||n===127||ch==='<'||ch==='>'?' ':ch}).join('').replace(/\s+/g,' ').trim().slice(0,60);return `Veja este imóvel rural no Raio-X Territorial${pl?` (${pl})`:''}: ${l}`}
 function waHref(car,place){const t=waText(car,place);return t?'https://wa.me/?text='+encodeURIComponent(t):''}
 const ICON_LINK='<svg class="rx-share-link" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M6.6 9.4a3 3 0 0 0 4.2 0l2.1-2.1a3 3 0 0 0-4.2-4.2l-.9.9"/><path d="M9.4 6.6a3 3 0 0 0-4.2 0L3.1 8.7a3 3 0 0 0 4.2 4.2l.9-.9"/></svg>';
 const ICON_OK='<svg class="rx-share-ok" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5"/></svg>';
 const ICON_WA='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.8a6.2 6.2 0 0 0-5.4 9.3L1.8 14.2l3.2-.8A6.2 6.2 0 1 0 8 1.8z"/><path d="M5.9 5.2c.2-.3.5-.3.7 0l.6 1.2c.1.2 0 .4-.1.6l-.4.4c.4.9 1.1 1.6 2 2l.4-.4c.2-.1.4-.2.6-.1l1.2.6c.3.1.3.5 0 .7-.5.5-1.2.7-1.9.4A6 6 0 0 1 5.6 7.1c-.3-.7-.1-1.4.3-1.9z" fill="currentColor" stroke="none"/></svg>';
 const copied=new Map();
 const isCopied=c=>(copied.get(c)||0)>Date.now();
 // Nodes inside the buttons ignore the pointer and paint() only toggles state/text, so a click
 // target is never detached while Leaflet and the V46 guards are still reading the event.
 // The copy button carries data-rx46-action so the V46 re-render (enrichment, SIGEF retry)
 // keeps keyboard focus on it; V46 ignores action names it does not know.
 function html(car,place){const c=norm(car);if(!c)return '';const ok=isCopied(c);return `<div class="rx-share-row" data-rx-share-row><button type="button" class="rx-share-btn" data-rx46-action="share-copy" data-rx-share-copy="${c}"${ok?' data-state="ok"':''} aria-label="Copiar link deste imóvel">${ICON_LINK}${ICON_OK}<span data-rx-share-label>${ok?'Link copiado':'Copiar link'}</span></button><a class="rx-share-btn" data-rx-share-wa href="${esc(waHref(c,place))}" target="_blank" rel="noopener noreferrer" aria-label="Enviar link deste imóvel pelo WhatsApp (abre em nova aba)">${ICON_WA}<span>WhatsApp</span></a></div>`}
 function paint(c){const ok=isCopied(c);document.querySelectorAll('[data-rx-share-copy]').forEach(b=>{if(b.dataset.rxShareCopy!==c)return;if(ok)b.dataset.state='ok';else delete b.dataset.state;const t=b.querySelector('[data-rx-share-label]');if(t)t.textContent=ok?'Link copiado':'Copiar link'})}
 let liveTimer=0;
 function announce(msg){let el=document.getElementById('rxShareLiveW1a');if(!el&&document.body){el=document.createElement('div');el.id='rxShareLiveW1a';el.className='rx-sr-only';el.setAttribute('role','status');el.setAttribute('aria-live','polite');document.body.appendChild(el)}if(!el)return;clearTimeout(liveTimer);el.textContent='';if(msg)liveTimer=setTimeout(()=>{el.textContent=msg},60)}
 function sheet(l){let el=document.getElementById('rxShareSheetW1a');if(!el){el=document.createElement('div');el.id='rxShareSheetW1a';el.className='rx-share-sheet';el.setAttribute('role','dialog');el.setAttribute('aria-labelledby','rxShareSheetTitleW1a');const box=document.createElement('div');box.className='rx-share-sheet-box';const h=document.createElement('b');h.id='rxShareSheetTitleW1a';h.textContent='Não foi possível copiar automaticamente';const p=document.createElement('p');p.textContent='Copie o link abaixo (toque e segure, ou Ctrl+C):';const f=document.createElement('textarea');f.readOnly=true;f.rows=3;f.setAttribute('data-rx-share-field','');f.setAttribute('aria-label','Link do imóvel');const x=document.createElement('button');x.type='button';x.className='rx-share-sheet-close';x.textContent='Fechar';const hide=()=>{el.hidden=true;const back=el.__rxBack;el.__rxBack=null;try{if(back&&back.isConnected)back.focus({preventScroll:true})}catch(e){}};x.addEventListener('click',hide);box.append(h,p,f,x);el.appendChild(box);document.body.appendChild(el);document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!el.hidden)hide()})}const f=el.querySelector('textarea');if(el.hidden!==false||!el.__rxBack)el.__rxBack=document.activeElement&&document.activeElement!==document.body?document.activeElement:null;f.value=l;el.hidden=false;try{f.focus({preventScroll:true});f.select();f.setSelectionRange(0,l.length)}catch(e){}announce('Não foi possível copiar automaticamente. O link está selecionado para copiar.')}
 async function copyLink(c){const l=link(c);if(!l)return false;let ok=false;try{const C=window.rxCopyCarC2;ok=!!C&&typeof C.write==='function'&&(await C.write(l))===true}catch(e){ok=false}if(ok){const s=document.getElementById('rxShareSheetW1a');if(s)s.hidden=true;copied.set(c,Date.now()+2800);paint(c);announce('Link copiado');setTimeout(()=>{if(!isCopied(c)){copied.delete(c);paint(c)}},2860)}else{copied.delete(c);paint(c);sheet(l)}return ok}

 // --- address bar: replaceState only ---
 function writeUrl(mut){try{const u=new URL(location.href);mut(u);const next=u.pathname+u.search+u.hash,cur=location.pathname+location.search+location.hash;if(next!==cur)history.replaceState(history.state,'',next)}catch(e){}}
 function setCar(car){const c=norm(car);writeUrl(u=>{if(c)u.searchParams.set('car',c);else u.searchParams.delete('car')})}
 const HASH=/^#(\d{1,2})\/(-?\d{1,2}(?:\.\d{1,8})?)\/(-?\d{1,3}(?:\.\d{1,8})?)$/;
 function readHash(h){const m=String(h||'').match(HASH);if(!m)return null;const z=Number(m[1]),lat=Number(m[2]),lon=Number(m[3]);if(!(z>=3&&z<=19&&Math.abs(lat)<=85&&Math.abs(lon)<=180))return null;return {z:Math.max(4,Math.min(18,z)),lat,lon}}
 const mapRef=()=>{try{return (typeof map!=='undefined'&&map&&map.setView&&map.getCenter)?map:null}catch(e){return null}};
 let linkSeq=0,sayTimer=0,pendingCode='';
 function say(text,kind,retry){let el=document.getElementById('rxShareStateW1a');if(!text){clearTimeout(sayTimer);if(el)el.hidden=true;return}if(!el){el=document.createElement('div');el.id='rxShareStateW1a';el.className='rx-share-state';el.setAttribute('role','status');el.setAttribute('aria-live','polite');const t=document.createElement('span');t.className='rx-share-state-text';const b=document.createElement('button');b.type='button';b.className='rx-share-state-retry';b.textContent='Tentar de novo';const x=document.createElement('button');x.type='button';x.className='rx-share-state-x';x.setAttribute('aria-label','Fechar aviso');x.textContent='×';x.addEventListener('click',()=>{clearTimeout(sayTimer);el.hidden=true});el.append(t,b,x);document.body.appendChild(el)}el.dataset.kind=kind||'info';el.querySelector('.rx-share-state-text').textContent=text;const b=el.querySelector('.rx-share-state-retry');b.hidden=!retry;b.onclick=retry?()=>retry():null;el.querySelector('.rx-share-state-x').hidden=kind==='busy';el.hidden=false;clearTimeout(sayTimer);if(kind==='info')sayTimer=setTimeout(()=>{el.hidden=true},9000)}
 async function openFromLink(code){const my=++linkSeq;say('Abrindo o imóvel do link…','busy');for(let attempt=1;attempt<=2;attempt++){let r=null,d=null;try{r=await fetch(`/v1/live/car/${encodeURIComponent(code)}`,{cache:'no-store'});d=await r.json().catch(()=>null)}catch(e){r=null}if(my!==linkSeq)return;const c=d&&d.car,p=c&&c.properties;
   // Only the property that was asked for: a different code in the answer is never shown.
   if(r&&r.ok&&c&&c.ok!==false&&p&&norm(p.cod_imovel)===code){say('');if(typeof window.showProperty!=='function')return;window.showProperty({car_code:code,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal},c.geometry||null);return}
   if(r&&r.status===404){setCar('');say('O SICAR não retornou imóvel com o código deste link.','info');return}
   if(attempt===1){await new Promise(res=>setTimeout(res,4000));if(my!==linkSeq)return}}
  say('Não foi possível abrir o imóvel do link agora.','retry',()=>openFromLink(code))}

 // Selection writes the link; wraps the final entry (after V49 sanitize), keeping its flag.
 const select=window.rxV46SelectProperty;
 if(typeof select==='function'&&!select.__rxShareW1a){const wrapped=function(p,g,latlng){const out=select.apply(this,arguments);try{const c=norm(p?.car_code);if(c&&c!==pendingCode){linkSeq++;say('')}setCar(c)}catch(e){}return out};wrapped.__rxShareW1a=true;if(select.__rxIdentitySanitizedV49)wrapped.__rxIdentitySanitizedV49=true;window.rxV46SelectProperty=wrapped}
 const closeAnchor=window.rxV46CloseAnchor;
 if(typeof closeAnchor==='function'&&!closeAnchor.__rxShareW1a){const wrappedClose=function(){const out=closeAnchor.apply(this,arguments);setCar('');return out};wrappedClose.__rxShareW1a=true;window.rxV46CloseAnchor=wrappedClose}
 document.addEventListener('click',e=>{const t=e.target;if(!t||!t.closest)return;if(t.closest('.rx46-card [data-rx46-action="close"]')){setCar('');return}const btn=t.closest('[data-rx-share-copy]');if(!btn)return;e.preventDefault();copyLink(norm(btn.dataset.rxShareCopy))},true);

 function ready(){return !document.getElementById('rxBootGuard')&&window.rxV46Installed===true&&typeof window.showProperty==='function'&&!!mapRef()}
 function whenReady(fn,t0){if(ready()){fn();return}if(Date.now()-t0>120000)return;setTimeout(()=>whenReady(fn,t0),150)}
 function boot(){
  let params=null;try{params=new URLSearchParams(location.search)}catch(e){params=new URLSearchParams('')}
  const all=params.getAll('car'),asked=all.length>0,code=all.length===1?norm(all[0]):'',pos=readHash(location.hash);
  pendingCode=code;
  whenReady(()=>{const m=mapRef();if(pos){try{m.setView([pos.lat,pos.lon],pos.z,{animate:false})}catch(e){}}
   let timer=0;m.on('moveend',()=>{clearTimeout(timer);timer=setTimeout(()=>{try{const c=m.getCenter();writeUrl(u=>{u.hash=`#${Math.round(m.getZoom())}/${c.lat.toFixed(5)}/${c.lng.toFixed(5)}`})}catch(e){}},400)});
   if(!asked)return;
   if(!code){setCar('');say('O link não traz um código CAR válido.','info');return}
   openFromLink(code).finally(()=>{pendingCode=''})},Date.now());
 }
 window.rxShareW1a={norm,link,waText,waHref,html,copy:copyLink,readHash};
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
</script>
<!-- RX_SHARE_LINK_W1A -->
'''

_html = portal_v8.PORTAL_HTML
if MARKER not in _html:
    if _html.count("</body>") != 1:
        raise RuntimeError("w1a_share_link_body_anchor_missing")
    block = UI.replace("__CAR_PATTERN__", json.dumps(CAR_PATTERN)).replace("__PUBLIC_BASE__", json.dumps(PUBLIC_BASE))
    portal_v8.PORTAL_HTML = _html.replace("</body>", block + "</body>", 1)

print(f"RX_SHARE_LINK_W1A=car_query_hash_position_copy_whatsapp base:{PUBLIC_BASE or 'page_origin'}", flush=True)

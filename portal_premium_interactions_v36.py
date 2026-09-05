from __future__ import annotations

import portal_v8


def _patch_progressive(html: str) -> str:
    old = "if(body)body.innerHTML='<div class=\"rx-quick-loader\"><div class=\"spin\"></div><h3>Montando a leitura inicial…</h3><p>O CAR entra primeiro. O aprofundamento só começou porque você pediu a análise completa.</p><div class=\"rx-bars\"><i></i><i></i><i></i></div></div>';"
    new = "if(body){body.classList.add('rx-analyzing');progressBox('Preparando análise completa','Os dados essenciais permanecem disponíveis enquanto as fontes aprofundadas são consultadas.','running')}"
    if old in html:
        html = html.replace(old, new, 1)

    old_ready = "progressBox('Leitura inicial pronta',`Resultado essencial em ${secs} s. O aprofundamento continua em segundo plano.`,'running');"
    new_ready = "if(body)body.classList.remove('rx-analyzing');progressBox('Leitura inicial pronta',`Resultado essencial em ${secs} s. O aprofundamento continua em segundo plano.`,'running');"
    if old_ready in html:
        html = html.replace(old_ready, new_ready, 1)

    old_error = "if(body)body.innerHTML=`<div class=\"row\"><b class=\"warn\">FONTE PRINCIPAL LENTA</b><br><span>${String(e.message||e)}. Tente novamente; nenhuma conclusão será inventada.</span></div>`;"
    new_error = "if(body)body.classList.remove('rx-analyzing');progressBox('Análise aprofundada temporariamente indisponível','Os dados essenciais permanecem disponíveis. Tente novamente em instantes.','warn');"
    if old_error in html:
        html = html.replace(old_error, new_error, 1)
    return html


INTERACTION_UI = r'''
<style id="rxPremiumInteractionsV36">
.rx-analyzing{position:relative}.rx-analyzing:after{content:'';position:absolute;top:0;left:0;width:3px;height:48px;border-radius:999px;background:#63e6a5;animation:rxAnalyzePulse 1.1s ease-in-out infinite}@keyframes rxAnalyzePulse{50%{opacity:.28;height:26px}}
.rx-progress{position:relative!important;z-index:12!important;margin:0 0 12px!important;box-shadow:none!important}
.rx-filter-close{margin-left:auto;border:1px solid #355044;background:#0d2118;color:#d9ebe1;border-radius:9px;padding:7px 9px;font-size:9px;font-weight:850;cursor:pointer;min-height:36px}
.rx-filter-heading{display:flex;align-items:flex-start;gap:10px}.rx-filter-heading>div{min-width:0;flex:1}.rx-sheet-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.46);backdrop-filter:blur(2px);z-index:2600;display:none}.rx-sheet-backdrop.show{display:block}
@media(max-width:720px){.rx-progress{position:sticky!important;top:112px!important;background:#0b1b14!important}.rx-filter-panel{z-index:2700!important}.rx-sheet-backdrop{z-index:2650}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 function friendly(s){const t=String(s||'');if(!/(502|503|curl|operation timed out|timeout after|http_|traceback|connection reset)/i.test(t))return t;return 'Uma fonte pública está temporariamente lenta. Os dados já confirmados permanecem disponíveis; tente atualizar esta consulta depois.'}
 function clean(){document.querySelectorAll('.rx-rare-interest,.rx-filter-status,.rx-error,.toast').forEach(el=>{const v=friendly(el.textContent);if(v!==el.textContent)el.textContent=v})}
 function ensureFilterChrome(){
   const p=q('#rxFilterPanel');if(!p)return;
   let back=q('#rxSheetBackdrop');if(!back){back=document.createElement('div');back.id='rxSheetBackdrop';back.className='rx-sheet-backdrop';document.body.appendChild(back);back.onclick=()=>{p.classList.add('hidden');sync()}}
   if(!p.querySelector('.rx-filter-close')){
     const h=p.querySelector('h4');if(h){const wrap=document.createElement('div');wrap.className='rx-filter-heading';const copy=document.createElement('div');h.parentNode.insertBefore(wrap,h);wrap.appendChild(copy);copy.appendChild(h);const desc=p.querySelector('p');if(desc)copy.appendChild(desc);const b=document.createElement('button');b.type='button';b.className='rx-filter-close';b.textContent='Fechar';b.setAttribute('aria-label','Fechar filtros');b.onclick=()=>{p.classList.add('hidden');sync()};wrap.appendChild(b)}
   }
   sync();
 }
 function sync(){const p=q('#rxFilterPanel'),back=q('#rxSheetBackdrop');const open=!!p&&!p.classList.contains('hidden')&&getComputedStyle(p).display!=='none';back?.classList.toggle('show',open);document.body.classList.toggle('rx-filter-open',open)}
 const obs=new MutationObserver(()=>{ensureFilterChrome();clean();sync()});
 function start(){obs.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});ensureFilterChrome();clean()}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
<!-- RX_PREMIUM_INTERACTIONS_V36 -->
'''

html = _patch_progressive(portal_v8.PORTAL_HTML)
portal_v8.PORTAL_HTML = html.replace('</body>', INTERACTION_UI + '</body>')

print('RX_PREMIUM_INTERACTIONS_V36=nonblocking_deep_analysis_and_modal_discipline', flush=True)

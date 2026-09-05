from __future__ import annotations

import portal_v8

UI = r'''
<style id="rxLayoutGuardV37">
@media(min-width:721px){
  body.rx-dossier-open .map{transition:width .16s ease}
  body:not(.rx-dossier-open) .map{width:100%!important}
}
.rx-overflow-corrected{max-width:100%!important;overflow-wrap:anywhere!important;word-break:break-word!important;overflow-x:hidden!important}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const desktop=()=>matchMedia('(min-width:721px)').matches;
 let lastSplit=null,layoutTimer=null;
 function mapObj(){try{return (typeof map!=='undefined'&&map&&map.invalidateSize)?map:null}catch(e){return null}}
 function visible(el){return !!el&&!el.classList.contains('hidden')&&getComputedStyle(el).display!=='none'}
 function applySplit(){
   const panel=q('#panel'),mapEl=q('#map')||q('.map');
   if(!mapEl)return;
   const open=desktop()&&visible(panel);
   const key=open?Math.round(panel.getBoundingClientRect().width):0;
   if(lastSplit===key)return;lastSplit=key;
   if(open){mapEl.style.width=`calc(100% - ${key}px)`;document.body.classList.add('rx-dossier-open')}
   else{mapEl.style.width='100%';if(!visible(panel))document.body.classList.remove('rx-dossier-open')}
   requestAnimationFrame(()=>{try{mapObj()?.invalidateSize({animate:false})}catch(e){}});
 }
 function guardOverflow(){
   const sels=['#pbody','.rx-human-card','.rx-mobile-hero','.rx-card','.rx-kpi','.rx-mobile-kpi','.rx-note','.rx-row','.row','.source','.rx-filter-row','.rx-filter-status'];
   document.querySelectorAll(sels.join(',')).forEach(el=>{
      if(el.clientWidth>0&&el.scrollWidth>el.clientWidth+3)el.classList.add('rx-overflow-corrected');
   });
 }
 function schedule(){clearTimeout(layoutTimer);layoutTimer=setTimeout(()=>{applySplit();guardOverflow()},50)}
 const obs=new MutationObserver(schedule);
 function start(){obs.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});window.addEventListener('resize',schedule,{passive:true});schedule()}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
</script>
<!-- RX_LAYOUT_GUARD_V37 -->
'''

portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace('</body>', UI + '</body>')
print('RX_LAYOUT_GUARD_V37=desktop_split_and_overflow_runtime_guard', flush=True)

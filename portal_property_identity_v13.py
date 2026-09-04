from __future__ import annotations

import portal_v8

UI=r'''
<script>
(function(){
  const esc=s=>encodeURIComponent(String(s||'').trim());
  function validName(v){const s=String(v||'').trim();return s&&s.toLowerCase()!=='imóvel rural'&&s.toLowerCase()!=='imovel rural'&&!/^im[oó]vel rural\s*[—-]/i.test(s)}
  const oldShow=(typeof showProperty==='function')?showProperty:null;
  if(oldShow){
    window.showProperty=showProperty=function(p,g){
      oldShow(p,g);
      const nm=String(p?.name||p?.denominacao||p?.nome_imovel||'').trim();
      if(validName(nm)){
        try{current.name=nm}catch(e){}
        const n=document.querySelector('#name');if(n)n.textContent=nm;
        const t=document.querySelector('#ptitle');if(t)t.textContent=nm+(p?.car_code?' · '+p.car_code:'');
      }
    };
  }
  function suffix(){try{return validName(current?.name)?'?property_name='+esc(current.name):''}catch(e){return ''}}
  window.downloadPDF=downloadPDF=function(){if(!current?.car_code)return;window.open(`/v1/reports/property/${esc(current.car_code)}${suffix()}`,'_blank')};
  const oldRender=(typeof renderAnalysis==='function')?renderAnalysis:null;
  if(oldRender){
    window.renderAnalysis=renderAnalysis=function(d){
      oldRender(d);
      try{
        if(validName(current?.name)){
          const h=document.querySelector('#pbody .hero h3');if(h)h.textContent=current.name;
          const t=document.querySelector('#ptitle');if(t)t.textContent=current.name+' · '+current.car_code;
        }
      }catch(e){}
    };
  }
})();
</script>
'''

if 'property_name='+"'" not in portal_v8.PORTAL_HTML and 'RX_PORTAL_IDENTITY_V13' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_PORTAL_IDENTITY_V13 --></body>')

print('RX_PORTAL_IDENTITY_V13=farm_name_preserved_to_pdf',flush=True)

from __future__ import annotations

import portal_v8

VISUAL = r'''
<style>
.rx-map-legend{position:absolute;z-index:786;left:14px;bottom:52px;display:flex;align-items:center;gap:7px;padding:6px 9px;border:1px solid #2b473a;border-radius:9px;background:rgba(6,22,15,.88);color:#9fb5aa;font-size:8px;box-shadow:0 6px 18px #0005;pointer-events:none}.rx-map-legend i{width:10px;height:10px;border-radius:3px;border:1px solid rgba(255,255,255,.42)}.rx-map-legend span{white-space:nowrap}
@media(max-width:720px){.rx-map-legend{left:9px;bottom:66px;max-width:72vw;gap:5px;padding:5px 7px;font-size:7px}.rx-map-legend span:nth-of-type(2){display:none}}
</style>
<script>
(function(){
  const palette=[
    ['#4f8a70','#7fc9a6'],
    ['#6f8060','#a3b98c'],
    ['#587a8a','#88b3c7'],
    ['#85745b','#b9a381'],
    ['#6f6e8b','#a6a4c6'],
    ['#7d675f','#b6988e'],
    ['#527a72','#80ada1']
  ];
  function hash(s){let h=2166136261;for(const ch of String(s||'')){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return Math.abs(h>>>0)}
  window.rxParcelStyle=function(feature){
    const p=feature?.properties||{},key=p.cod_imovel||p.car_code||p.municipio||'';
    const c=palette[hash(key)%palette.length];
    return {color:c[1],weight:1.45,opacity:.88,fillColor:c[0],fillOpacity:.105};
  };
  function legend(){
    if(document.querySelector('#rxMapLegend'))return;
    const main=document.querySelector('.main');if(!main)return;
    const el=document.createElement('div');el.id='rxMapLegend';el.className='rx-map-legend';
    el.innerHTML='<i style="background:#4f8a70"></i><i style="background:#587a8a"></i><i style="background:#85745b"></i><span>Imóveis rurais diferenciados visualmente</span><span>• seleção destacada em verde</span>';
    main.appendChild(el);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',legend);else legend();
})();
</script>
<!-- RX_MAP_VISUAL_V32 -->
'''

html=portal_v8.PORTAL_HTML
html=html.replace(
    "rxParcelLayer=L.geoJSON(d,{style:{color:'#48d995',weight:1.4,fillColor:'#48d995',fillOpacity:.075},onEachFeature:",
    "rxParcelLayer=L.geoJSON(d,{style:(f)=>(window.rxParcelStyle?window.rxParcelStyle(f):{color:'#48d995',weight:1.4,fillColor:'#48d995',fillOpacity:.075}),onEachFeature:"
)
portal_v8.PORTAL_HTML=html.replace('</body>',VISUAL+'</body>')
print('RX_MAP_VISUAL_V32=muted_property_palette_selected_green',flush=True)

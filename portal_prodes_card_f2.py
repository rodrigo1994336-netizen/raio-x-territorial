"""F2: a linha PRODES do cartão do mapa mostra a mesma leitura do relatório.

O cartão (v45) é rápido e só consulta o CAR: o PRODES aparece ali como "não consultada".
Quando a análise completa termina (renderAnalysis, com summary['prodes']['reading']), esta
extensão escreve na linha PRODES do cartão o texto de prodes_reading_f2 (panel.status/reason),
sem compor texto no navegador e sem consultar o PRODES no clique (isso é decisão do dono).

Regras: só atualiza o cartão do MESMO imóvel da análise; consulta pendente aparece como
"Consulta pendente" (nunca "nenhum"); nenhum número é calculado aqui.
"""
from __future__ import annotations

import portal_v8

MARKER = "RX_PRODES_CARD_F2"

UI = r'''
<style id="rxProdesCardF2">
.rx45-check[data-source="prodes"][data-rx-f2="1"]{align-items:flex-start!important;padding:8px!important;grid-column:1/-1}
</style>
<script>
(function(){
 const norm=v=>String(v||'').trim().toUpperCase();
 const DOTS=new Set(['diligence','checked_clear','source_failed']);
 // Pura: o que a linha PRODES do cartão deste imóvel mostra, ou null quando não deve mudar.
 function rxProdesCardUpdate(analysis,cardCar){
  const a=analysis||{},reading=(a.prodes||{}).reading,panel=reading&&reading.panel;
  if(!panel||panel.id!=='prodes'||!DOTS.has(panel.dot))return null;
  const car=norm(((a.car||{}).properties||{}).cod_imovel);
  if(!car||car!==norm(cardCar))return null;
  const status=String(panel.status||'').trim();
  if(!status)return null;
  const answered=panel.audit_state==='ANSWERED_HIT'||panel.audit_state==='ANSWERED_CLEAR';
  return {state:panel.dot,label:'PRODES',status,reason:String(panel.reason||''),meta:'Fonte: INPE / TerraBrasilis / PRODES',answered,key:[panel.dot,status,panel.reason||''].join('|')};
 }
 window.rxProdesCardUpdate=rxProdesCardUpdate;
 const byCar={};
 const currentCar=()=>norm((window.current||{}).car_code);
 function apply(car){
  const u=byCar[car];if(!u)return;
  const card=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(!card)return;
  const el=card.querySelector('.rx45-check[data-source="prodes"]');
  if(!el||typeof window.rxV48UpdateComplianceSource!=='function'||el.dataset.rxF2Key===u.key)return;
  window.rxV48UpdateComplianceSource('prodes',u);
  el.dataset.rxF2='1';el.dataset.rxF2Key=u.key;
  // A nova tentativa do PRODES é da análise completa; o botão genérico não tem ação para esta fonte.
  el.querySelector('.rx48-retry')?.remove();
 }
 function remember(d){
  const a=(d||{}).analysis||{},car=norm(((a.car||{}).properties||{}).cod_imovel);
  const u=rxProdesCardUpdate(a,car);if(!u)return;
  // O cartão pode ser redesenhado enquanto a análise termina: reaplica por pouco tempo, sem observador global.
  byCar[car]=u;apply(car);[300,1500,4000].forEach(ms=>setTimeout(()=>{if(currentCar()===car)apply(car)},ms));
 }
 function install(){
  if(window.__rxProdesCardF2)return;
  const base=window.renderAnalysis;
  if(typeof base!=='function'){const n=(window.__rxProdesCardF2Tries||0)+1;window.__rxProdesCardF2Tries=n;if(n<=20)setTimeout(install,150);return}
  window.__rxProdesCardF2=true;
  const wrapped=function(d){try{return base.apply(this,arguments)}finally{try{remember(d)}catch(e){}}};
  window.renderAnalysis=wrapped;try{renderAnalysis=wrapped}catch(e){}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if MARKER not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", UI + f"<!-- {MARKER} --></body>")

print("RX_PORTAL_PRODES_CARD_F2=reading_panel_same_car_only", flush=True)

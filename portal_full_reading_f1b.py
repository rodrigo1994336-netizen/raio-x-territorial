"""F1B — "Ver análise completa" mostra a leitura no painel do imóvel.

Antes: o botão chamava a análise progressiva legada, que desenhava o resultado dentro de
``#pbody`` — um contêiner que o painel V45 esconde (``display:none``). O cliente esperava até
25 s e nada mudava; e o resultado legado trazia textos internos ("PREPARADO — OFF",
"BACKEND DE ALERTAS INDISPONÍVEL", números como "14.496704 ha") e ainda disparava consultas
escondidas (premium, WhatsApp, monitoramento, minerais críticos).

Agora (este módulo, carregado depois de todos os remendos do painel):

* ``window.rxProgressiveAnalyze`` passa a ser esta leitura. O botão do painel (#rx45Full) e o
  "VER ANÁLISE COMPLETA" do cartão do mapa começam a MESMA consulta, uma por imóvel
  (single-flight por CAR): ``/v1/live/quick/{car}?deep=1`` e, enquanto o motor aprofunda,
  ``/v1/live/progressive/status/{car}``.
* O resultado aparece numa seção visível do próprio painel, logo depois de "Conformidade":
  estado carregando, estado de erro ("Consulta pendente" + "Consultar de novo") e, pronto,
  UMA LINHA POR PERGUNTA com resposta Sim / Não / Consulta pendente, número em pt-BR,
  fonte e data.
* Regra das respostas (função pura ``rows``): "Sim" ou "Não" só quando a fonte respondeu
  (``ok === true`` e contagem numérica real). Qualquer outra coisa é "Consulta pendente".
  Consulta cortada no teto (200 não é todos) com zero achado também é pendente. Fonte fora
  da cobertura (ex.: outorgas IDE-Sisema fora de MG) não aparece.
* A resposta tem de ser do mesmo CAR; resposta de outro imóvel nunca é mostrada.
* Consulta que não respondeu tenta de novo sozinha UMA vez; depois fica "Consulta pendente".
* Nada é escrito em ``#pbody``.
"""

from __future__ import annotations

import portal_v8

MARKER = "RX_FULL_READING_F1B"

FULL_READING_UI = r'''
<style id="rxFullReadingStyleF1b">
.rx-f1b-full{scroll-margin:12px}
.rx-f1b-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.rx-f1b-head h4{margin:0!important}
.rx-f1b-when{font-size:9px;color:var(--rx45-muted,#9fb5aa)}
.rx-f1b-rows{display:grid}
.rx-f1b-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;padding:8px 0;border-top:1px solid #1d382c;align-items:start}
.rx-f1b-row:first-child{border-top:0}
.rx-f1b-q{font-size:10.5px;line-height:1.35;font-weight:700;color:var(--rx45-text,#eef8f2);min-width:0;overflow-wrap:anywhere}
.rx-f1b-a{font-size:10px;line-height:1.2;font-weight:900;padding:3px 9px;border-radius:999px;white-space:nowrap;border:1px solid var(--rx45-line,#2a493b);background:#10271d;color:#e6f3ec}
.rx-f1b-a.sim.attention{background:#4b3917;border-color:#6d5424;color:#ffd77d}
.rx-f1b-a.pendente{background:transparent;border-style:dashed;color:var(--rx45-muted,#9fb5aa);font-weight:700}
.rx-f1b-d{grid-column:1/-1;font-size:9px;line-height:1.45;color:var(--rx45-muted,#9fb5aa);overflow-wrap:anywhere}
.rx-f1b-row.pendente .rx-f1b-q{color:var(--rx45-muted,#9fb5aa);font-weight:600}
.rx-f1b-state{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:10px;line-height:1.45;color:var(--rx45-muted,#9fb5aa)}
.rx-f1b-state>span{display:flex;align-items:center;gap:9px;min-width:0}
.rx-f1b-spin{flex:none;width:14px;height:14px;border-radius:50%;border:2px solid #2a493b;border-top-color:var(--rx45-green,#63e6a5);animation:rxF1bSpin .9s linear infinite}
@keyframes rxF1bSpin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.rx-f1b-spin{animation:none}}
.rx-f1b-retry{flex:none;min-height:44px;padding:0 12px;border-radius:10px;border:1px solid var(--rx45-line,#2a493b);background:transparent;color:var(--rx45-text,#eef8f2);font:700 10px system-ui,sans-serif;cursor:pointer}
.rx-f1b-retry:focus-visible{outline:2px solid var(--rx45-green,#63e6a5);outline-offset:2px}
</style>
<script id="rxFullReadingScriptF1b">
(function(){
 if(window.rxFullReadingF1b)return;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const norm=c=>String(c||'').trim().toUpperCase();
 const isNum=v=>typeof v==='number'&&Number.isFinite(v);
 const int=n=>Number(n).toLocaleString('pt-BR');
 const ha=v=>{if(window.rxNum&&typeof window.rxNum.ha==='function')return window.rxNum.ha(v);return isNum(v)?v.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+' ha':''};
 const plural=(n,one,many)=>`${int(n)} ${n===1?one:many}`;
 const YES='sim',NO='nao',PEND='pendente',LABEL={sim:'Sim',nao:'Não',pendente:'Consulta pendente'};

 // Pure: analysis -> one row per question. "Sim"/"Não" only from a source that answered
 // (ok===true and a real count); anything else is "Consulta pendente". Never a guess.
 function rows(analysis){
  const a=analysis&&typeof analysis==='object'?analysis:{},out=[];
  const add=(id,q,answer,detail,source,attention)=>out.push({id,q,answer,detail:detail||'',source:source||'',attention:!!attention});
  const exact=(id,obj,q,source,one,many)=>{if(!obj||typeof obj!=='object')return;const ex=obj.exact||{},n=ex.occurrence_count;
   if(obj.ok===true&&ex.available===true&&isNum(n)&&n>=0){if(n>0){const area=isNum(ex.area_unique_ha)&&ex.area_unique_ha>0?` · ${ha(ex.area_unique_ha)} dentro do imóvel`:'';add(id,q,YES,plural(n,one,many)+area,source,true)}else add(id,q,NO,'',source,true)}
   else add(id,q,PEND,'',source,true)};
  exact('embargo_ibama',a.embargos_ibama,'Há embargo do IBAMA no imóvel?','IBAMA — áreas embargadas','área embargada','áreas embargadas');
  const au=a.autos_ibama;
  if(au&&typeof au==='object'){const q='Há auto de infração ambiental do IBAMA no imóvel?',src='IBAMA — autos de infração',n=au.occurrence_count,capped=isNum(au.feature_count_bbox)&&au.feature_count_bbox>=2000;
   if(au.ok===true&&isNum(n)&&n>=0&&!(capped&&n===0)){if(n>0)add('autos_ibama',q,YES,(capped?'pelo menos ':'')+plural(n,'auto localizado no imóvel','autos localizados no imóvel'),src,true);else add('autos_ibama',q,NO,'',src,true)}
   else add('autos_ibama',q,PEND,'',src,true)}
  const pr=a.prodes&&a.prodes.reading;
  if(pr&&typeof pr==='object'){const q='Há desmatamento mapeado pelo PRODES dentro do imóvel?',src='INPE — PRODES',ins=pr.inside||{};
   if(pr.state==='found'&&isNum(ins.count)&&ins.count>0){const yrs=(Array.isArray(ins.years)?ins.years:[]).filter(isNum);add('prodes',q,YES,[plural(ins.count,'polígono','polígonos'),isNum(ins.area_ha)&&ins.area_ha>0?ha(ins.area_ha):'',yrs.length?(yrs.length>1?'anos ':'ano ')+yrs.slice(-4).join(', '):''].filter(Boolean).join(' · '),src,true)}
   else if(pr.state==='not_found')add('prodes',q,NO,'',src,true);
   else add('prodes',q,PEND,'',src,true)}
  const TC=[['terra_indigena','O imóvel sobrepõe Terra Indígena?'],['unidade_conservacao','O imóvel sobrepõe Unidade de Conservação?'],['quilombola','O imóvel sobrepõe Território Quilombola?'],['assentamento','O imóvel sobrepõe assentamento do INCRA?'],['embargo_icmbio','Há embargo do ICMBio no imóvel?'],['floresta_publica','O imóvel sobrepõe Floresta Pública?'],['sitio_arqueologico','Há sítio arqueológico cadastrado no imóvel?']];
  const tc=a.territorial_constraints;
  if(tc&&typeof tc==='object'){const sv=tc.services&&typeof tc.services==='object'?tc.services:null;
   for(const [k,q] of TC){const s=sv?sv[k]:null;
    if(!sv){add(k,q,PEND,'','',true);continue}
    if(!s||typeof s!=='object')continue;
    const n=s.occurrence_count,src=String(s.source||'').trim();
    if(s.ok===true&&isNum(n)&&n>=0){if(n>0)add(k,q,YES,isNum(s.area_unique_ha)&&s.area_unique_ha>0?`${ha(s.area_unique_ha)} sobrepostos ao imóvel`:plural(n,'registro','registros'),src,true);else add(k,q,NO,'',src,true)}
    else add(k,q,PEND,'',src,true)}}
  exact('anm',a.anm,'Há processo minerário da ANM sobre o imóvel?','ANM — SIGMINE','processo','processos');
  const f=a.fire_live;
  if(f&&typeof f==='object'){const q='Há foco de queimada no imóvel agora?',src='INPE — Programa Queimadas',n=f.inside_count;
   if(f.ok===true&&isNum(n)&&n>=0){const r=f.radius_km,near=f.near_count,m=/(\d+)\s+arquivos de 10 minutos/i.exec(String(f.window_note||''));
    const around=isNum(near)&&isNum(r)&&r>0?(near>0?`${plural(near,'foco','focos')} a até ${int(r)} km`:`nenhum foco a até ${int(r)} km`):'';
    add('fire',q,n>0?YES:NO,[n>0?plural(n,'foco dentro do imóvel','focos dentro do imóvel'):'',around,m?`${int(Number(m[1]))} boletins de 10 min mais recentes`:''].filter(Boolean).join(' · '),src,true)}
   else add('fire',q,PEND,'',src,true)}
  const w=a.water_mg;
  if(w&&typeof w==='object'&&!/outside_source_coverage/.test(String(w.detail||''))){const q='Há outorga de uso de água no imóvel?',src='IGAM e ANA — outorgas',n=w.inside_count;
   if(w.ok===true&&isNum(n)&&n>=0)add('water',q,n>0?YES:NO,n>0?plural(n,'outorga com ponto no imóvel','outorgas com ponto no imóvel'):'',src,false);
   else add('water',q,PEND,'',src,false)}
  const pv=a.pivots_ana;
  if(pv&&typeof pv==='object'){const q='Há pivô central de irrigação no imóvel?',src='ANA — pivôs centrais',n=pv.intersection_count,partial=isNum(pv.parsed_feature_count)&&isNum(pv.feature_count_bbox)&&pv.parsed_feature_count<pv.feature_count_bbox;
   if(pv.ok===true&&isNum(n)&&n>=0&&!(partial&&n===0)){const yr=isNum(pv.reference_year)?`mapeamento de ${pv.reference_year}`:'';
    add('pivot',q,n>0?YES:NO,[n>0?plural(n,'pivô','pivôs'):'',n>0&&isNum(pv.intersection_area_unique_ha)&&pv.intersection_area_unique_ha>0?`${ha(pv.intersection_area_unique_ha)} dentro do imóvel`:'',yr].filter(Boolean).join(' · '),src,false)}
   else add('pivot',q,PEND,'',src,false)}
  // Answered first; pending questions stay listed, quietly, at the end.
  return out.filter(x=>x.answer!==PEND).concat(out.filter(x=>x.answer===PEND));
 }

 const when=at=>{try{const d=new Date(at);return d.toLocaleDateString('pt-BR')+', '+d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}catch(e){return ''}};
 function rowHtml(r){const cls=r.answer+(r.answer===YES&&r.attention?' attention':'');const meta=[r.detail,r.source?`Fonte: ${r.source}`:''].filter(Boolean).join(' · ');
  return `<div class="rx-f1b-row ${esc(r.answer)}" data-rx-f1b-row="${esc(r.id)}" data-answer="${esc(r.answer)}"><div class="rx-f1b-q">${esc(r.q)}</div><div class="rx-f1b-a ${esc(cls)}">${esc(LABEL[r.answer]||LABEL.pendente)}</div>${meta?`<div class="rx-f1b-d">${esc(meta)}</div>`:''}</div>`}
 // Pure: state -> inner HTML of the section.
 function html(e){
  const head=extra=>`<div class="rx-f1b-head"><h4>Análise completa</h4>${extra||''}</div>`;
  if(!e||e.phase==='loading')return head()+'<div class="rx-f1b-state" role="status"><span><i class="rx-f1b-spin" aria-hidden="true"></i><span>Consultando embargos, desmatamento, áreas protegidas, mineração, queimadas e água. Pode levar até 1 minuto.</span></span></div>';
  if(e.phase==='ready'&&Array.isArray(e.rows)&&e.rows.length)return head(e.at?`<span class="rx-f1b-when">Resposta recebida em ${esc(when(e.at))}</span>`:'')+`<div class="rx-f1b-rows">${e.rows.map(rowHtml).join('')}</div>`;
  return head()+'<div class="rx-f1b-state" data-rx-f1b-pending="1"><span><span><strong>Consulta pendente.</strong> As fontes oficiais não responderam agora; nada foi presumido.</span></span><button type="button" class="rx-f1b-retry" data-rx-f1b-retry>Consultar de novo</button></div>';
 }

 const memo=new Map(),READY_TTL=10*60*1000,FAIL_TTL=60*1000,QUICK_TIMEOUT_MS=95000,DEEP_WAIT_MS=110000,HARD_LIMIT_MS=150000;
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const currentCar=()=>norm((window.current||{}).car_code);
 function card(car){const sel=`.rx45-panel-card[data-car="${CSS.escape(car)}"]`;return document.querySelector('#rx43SnapshotHost '+sel)||document.querySelector(sel)}
 function slot(c){let s=c.querySelector('[data-rx-full-slot]');if(s)return s;s=document.createElement('section');s.className='rx45-section rx-f1b-full';s.setAttribute('data-rx-full-slot','');s.setAttribute('aria-live','polite');
  const comp=c.querySelector('.rx45-compliance'),sec=comp&&comp.closest('.rx45-section'),act=c.querySelector('.rx45-actions');
  if(sec&&sec.parentNode===c)sec.after(s);else if(act&&act.parentNode===c)act.before(s);else c.appendChild(s);return s}
 function button(c,e){const b=c.querySelector('#rx45Full');if(!b)return;if(e.phase==='loading'){b.textContent='CONSULTANDO FONTES…';b.setAttribute('aria-busy','true')}else{b.removeAttribute('aria-busy');b.textContent=e.phase==='failed'?'CONSULTAR DE NOVO':'VER ANÁLISE COMPLETA'}}
 function paint(car,scroll){const e=memo.get(car),c=card(car);if(!e||!c)return;const s=slot(c);s.dataset.phase=e.phase;s.innerHTML=html(e);button(c,e);if(scroll){try{s.scrollIntoView({block:'start',behavior:'smooth'})}catch(x){}}}
 // The answer must be about THIS property; anything else is not an answer.
 const sameCar=(a,car)=>!!a&&typeof a==='object'&&norm(((a.car||{}).properties||{}).cod_imovel)===car;
 async function fetchJson(url,ms){const ctrl=new AbortController(),t=setTimeout(()=>ctrl.abort(),ms);try{const r=await fetch(url,{cache:'no-store',signal:ctrl.signal});let d=null;try{d=await r.json()}catch(x){}return {ok:r.ok,d}}finally{clearTimeout(t)}}
 async function attempt(car,alive,hardUntil){
  const enc=encodeURIComponent(car);
  const first=await fetchJson(`/v1/live/quick/${enc}?deep=1`,QUICK_TIMEOUT_MS);
  if(!first.ok||!first.d)return null;
  const ds=first.d.deep_state||{},done=ds.state==='ready'?ds.analysis:(first.d.mode==='quick-cache'?first.d.analysis:null);
  if(done)return sameCar(done,car)?done:null;
  if(ds.state==='failed')return null;
  // The engine keeps working: poll its state, stop on its answer, on its failure or after 3 status errors in a row.
  const until=Math.min(Date.now()+DEEP_WAIT_MS,hardUntil);let misses=0;
  for(let i=0;Date.now()<until;i++){
   await sleep(i<4?1500:2500);if(!alive())return null;
   const st=await fetchJson(`/v1/live/progressive/status/${enc}`,20000).catch(()=>({ok:false,d:null}));
   if(st.ok&&st.d&&st.d.state==='ready')return sameCar(st.d.analysis,car)?st.d.analysis:null;
   if(st.d&&st.d.state==='failed')return null;
   misses=st.ok?0:misses+1;if(misses>=3)return null;
  }
  return null;
 }
 function usable(e){if(!e)return false;if(e.phase==='loading')return true;return Date.now()-e.at<(e.phase==='ready'?READY_TTL:FAIL_TTL)}
 // Only for a property whose panel is open: a click that did not open the panel never wakes the engine.
 function start(car,opts){car=norm(car);opts=opts||{};if(!car||!card(car))return null;let e=memo.get(car);
  if(opts.force||!usable(e)){const token={};e={phase:'loading',at:Date.now(),rows:null,token};memo.set(car,e);const mine=e;
   // Alive while this is still the query for the car AND its panel is open. A closed panel stops the
   // polling and forgets the entry, so opening the property again asks again.
   const alive=()=>memo.get(car)===mine&&!!card(car);
   (async()=>{const hardUntil=Date.now()+HARD_LIMIT_MS;let a=null;
    for(let i=0;i<2&&!a;i++){
     if(i){if(Date.now()>hardUntil-30000)break;await sleep(3000)}
     if(!alive()){if(memo.get(car)===mine)memo.delete(car);return}
     try{a=await attempt(car,alive,hardUntil)}catch(x){a=null}
     if(memo.get(car)!==mine)return;
    }
    if(!a&&!card(car)){memo.delete(car);return}
    const list=a?rows(a):[];mine.phase=list.length?'ready':'failed';mine.rows=list.length?list:null;mine.at=Date.now();paint(car,false)})();}
  paint(car,!!opts.scroll);return e}
 function fromButton(){const car=currentCar();if(!car)return;const e=memo.get(car);start(car,{scroll:true,force:!!e&&e.phase==='failed'})}
 function install(){
  window.rxProgressiveAnalyze=fromButton;
  const legacy=document.querySelector('#analyze');if(legacy)legacy.onclick=fromButton;
  document.addEventListener('rx45:panel-rendered',ev=>{const car=norm(ev&&ev.detail&&ev.detail.car);if(car&&memo.has(car))paint(car,false)});
  document.addEventListener('click',ev=>{const t=ev.target;if(!t||!t.closest)return;
   const retry=t.closest('[data-rx-f1b-retry]');if(retry){ev.preventDefault();const car=norm(retry.closest('.rx45-panel-card')?.dataset.car);if(car)start(car,{force:true,scroll:false});return}
   const cta=t.closest('[data-rx46-action="full"]');if(cta){const car=norm(cta.closest('.rx46-card')?.dataset.car||currentCar());if(car)setTimeout(()=>start(car,{scroll:false}),0)}},true);
 }
 window.rxFullReadingF1b={rows,html,start,peek:car=>memo.get(norm(car))||null,LABEL};
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
<!-- RX_FULL_READING_F1B -->
'''


def install() -> None:
    if MARKER in portal_v8.PORTAL_HTML:
        return
    if portal_v8.PORTAL_HTML.count("</body>") != 1:
        raise RuntimeError("f1b_full_reading_body_anchor_missing")
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", FULL_READING_UI + "</body>")


install()
print("RX_FULL_READING_F1B=panel_visible_reading one_query_per_car rows:sim_nao_pendente", flush=True)

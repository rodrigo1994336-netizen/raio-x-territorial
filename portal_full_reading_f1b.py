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
  da cobertura (ex.: outorgas IDE-Sisema fora de MG) não aparece. PRODES com camada que não
  respondeu mostra "pelo menos N … nas camadas que responderam", todos os anos (ou o intervalo)
  e o que é posterior a 31/07/2019.
* Dado velho nunca passa por atual: na PRIMEIRA resposta do motor só vale o resultado do cache
  (``mode === 'quick-cache'``). Em qualquer outro modo o motor acabou de agendar uma rodada nova e o
  ``deep_state`` ainda pode ser o "pronto" da rodada ANTERIOR: a leitura pergunta o estado e espera a
  rodada nova. A data mostrada é a em que o motor produziu o dado (``completed_at``); sem ela, só a
  hora de uma rodada vista em andamento. Queimadas trazem a hora do boletim do INPE.
* A resposta tem de ser do mesmo CAR; resposta de outro imóvel nunca é mostrada.
* Consulta que não respondeu tenta de novo sozinha UMA vez; depois fica "Consulta pendente".
  Leitura pronta com pendências: UMA nova consulta automática quando o cache do motor vence
  (~190 s), juntando só as linhas que passaram a responder; depois, "Consultar de novo" discreto.
* A caixa "ver fontes e datas" lê esta leitura (``window.rxFullReadingF1b.peek``).
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
.rx-f1b-state,.rx-f1b-fill{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:10px;line-height:1.45;color:var(--rx45-muted,#9fb5aa)}
.rx-f1b-fill{font-size:9.5px;padding-top:6px;border-top:1px dashed #1d382c}
.rx-f1b-state>span,.rx-f1b-fill>span{display:flex;align-items:center;gap:9px;min-width:0}
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
 const when=at=>{try{const d=new Date(at);return d.toLocaleDateString('pt-BR')+', '+d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}catch(e){return ''}};
 const clock=at=>{try{return new Date(at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}catch(e){return ''}};
 // INPE names each 10-minute bulletin by its time in UTC: focos_10min_YYYYMMDD_HHMM.csv
 function bulletin(name){const m=/focos_10min_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(String(name||''));if(!m)return null;const t=Date.UTC(+m[1],+m[2]-1,+m[3],+m[4],+m[5]);return isNum(t)?t:null}
 // Every year, never a cut list: up to five by name, more as a count with the first and the last.
 const yearList=ys=>ys.length>1?ys.slice(0,-1).join(', ')+' e '+ys[ys.length-1]:String(ys[0]);
 function years(list){const ys=[...new Set((Array.isArray(list)?list:[]).filter(isNum))].sort((a,b)=>a-b);if(!ys.length)return '';if(ys.length===1)return `ano PRODES ${ys[0]}`;return ys.length<=5?`anos PRODES ${yearList(ys)}`:`${ys.length} anos PRODES entre ${ys[0]} e ${ys[ys.length-1]}`}

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
  if(pr&&typeof pr==='object'){const q='Há desmatamento mapeado pelo PRODES dentro do imóvel?',src='INPE — PRODES',ins=pr.inside||{},post=pr.post_cutoff_inside||{},full=pr.complete===true;
   if(pr.state==='found'&&isNum(ins.count)&&ins.count>0){
    // A layer that failed or came cut makes every number a floor (200 não é todos), said once, both ends.
    const postText=isNum(post.count)&&post.count>0?`${plural(post.count,'posterior','posteriores')} a 31/07/2019 (${years(post.years)||'PRODES'}${isNum(post.area_ha)&&post.area_ha>0?'; '+ha(post.area_ha):''})`:(full&&post.count===0?'nenhum posterior a 31/07/2019':'');
    add('prodes',q,YES,[(full?'':'pelo menos ')+plural(ins.count,'polígono','polígonos'),isNum(ins.area_ha)&&ins.area_ha>0?ha(ins.area_ha):'',years(ins.years),postText,full?'':'nas camadas que responderam'].filter(Boolean).join(' · '),src,true)}
   else if(pr.state==='not_found'&&full)add('prodes',q,NO,'',src,true);
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
  if(f&&typeof f==='object'){const q='Há foco de queimada recente no imóvel?',src='INPE — Programa Queimadas',n=f.inside_count,b=bulletin(f.latest_file);
   // An answer about fire is only an answer with the time of the bulletin it read.
   if(f.ok===true&&isNum(n)&&n>=0&&b!==null){const r=f.radius_km,near=f.near_count,m=/(\d+)\s+arquivos de 10 minutos/i.exec(String(f.window_note||''));
    const around=isNum(near)&&isNum(r)&&r>0?(near>0?`${plural(near,'foco','focos')} a até ${int(r)} km`:`nenhum foco a até ${int(r)} km`):'';
    add('fire',q,n>0?YES:NO,[n>0?plural(n,'foco dentro do imóvel','focos dentro do imóvel'):'',around,`${m?int(Number(m[1]))+' boletins de 10 min do INPE':'boletins de 10 min do INPE'}, o mais recente de ${when(b)}`].filter(Boolean).join(' · '),src,true)}
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
  return order(out);
 }
 // Answered first; pending questions stay listed, quietly, at the end.
 const order=list=>list.filter(x=>x.answer!==PEND).concat(list.filter(x=>x.answer===PEND));
 // Pure: a later reading only fills what was pending. A row that answered is never replaced by a
 // later failure, and a later "pending" never hides an answer.
 function merge(old,fresh){const byId=new Map((fresh||[]).map(r=>[r.id,r]));let changed=false;
  const out=(old||[]).map(r=>{const n=byId.get(r.id);if(r.answer===PEND&&n&&n.answer!==PEND){changed=true;return n}return r});
  for(const n of fresh||[])if(n.answer!==PEND&&!out.some(r=>r.id===n.id)){out.push(n);changed=true}
  return {changed,rows:order(out)}}
 const pending=e=>!!e&&Array.isArray(e.rows)&&e.rows.some(r=>r.answer===PEND);

 function rowHtml(r){const cls=r.answer+(r.answer===YES&&r.attention?' attention':'');const meta=[r.detail,r.source?`Fonte: ${r.source}`:''].filter(Boolean).join(' · ');
  return `<div class="rx-f1b-row ${esc(r.answer)}" data-rx-f1b-row="${esc(r.id)}" data-answer="${esc(r.answer)}"><div class="rx-f1b-q">${esc(r.q)}</div><div class="rx-f1b-a ${esc(cls)}">${esc(LABEL[r.answer]||LABEL.pendente)}</div>${meta?`<div class="rx-f1b-d">${esc(meta)}</div>`:''}</div>`}
 function fillHtml(e){if(!pending(e))return '';const f=e.fill||{};
  if(f.state==='running')return '<div class="rx-f1b-fill" role="status" data-rx-f1b-filling><span><i class="rx-f1b-spin" aria-hidden="true"></i><span>Consultando de novo as fontes pendentes…</span></span></div>';
  if(f.state==='scheduled'&&isNum(f.when))return `<div class="rx-f1b-fill" role="status" data-rx-f1b-fill-at><span>Nova tentativa das consultas pendentes às ${esc(clock(f.when))}.</span></div>`;
  return '<div class="rx-f1b-fill"><span>Há consultas pendentes.</span><button type="button" class="rx-f1b-retry" data-rx-f1b-fill>Consultar de novo</button></div>'}
 // Pure: state -> inner HTML of the section.
 function html(e){
  const head=extra=>`<div class="rx-f1b-head"><h4>Análise completa</h4>${extra||''}</div>`;
  if(!e||e.phase==='loading')return head()+'<div class="rx-f1b-state" role="status"><span><i class="rx-f1b-spin" aria-hidden="true"></i><span>Consultando embargos, desmatamento, áreas protegidas, mineração, queimadas e água. Pode levar alguns minutos.</span></span></div>';
  if(e.phase==='ready'&&Array.isArray(e.rows)&&e.rows.length){
   // The time the engine produced the data, never the time the browser received it; unknown -> not shown.
   const stamp=[isNum(e.at)?`Consulta feita em ${when(e.at)}`:'',isNum(e.refilled)?`pendências consultadas de novo em ${when(e.refilled)}`:''].filter(Boolean).join(' · ');
   return head(stamp?`<span class="rx-f1b-when">${esc(stamp)}</span>`:'')+`<div class="rx-f1b-rows">${e.rows.map(rowHtml).join('')}</div>`+fillHtml(e)}
  return head()+'<div class="rx-f1b-state" data-rx-f1b-pending="1"><span><span><strong>Consulta pendente.</strong> As fontes oficiais não responderam agora; nada foi presumido.</span></span><button type="button" class="rx-f1b-retry" data-rx-f1b-retry>Consultar de novo</button></div>';
 }

 // QUICK covers the portal proxy budget (engine wake-up wait 70 s + 22 s + one retry); STATUS is above the
 // status proxy's single 8 s try. ENGINE_CACHE_MS: the engine keeps a finished analysis for 180 s.
 // DEEP_WAIT_MS: measured 14/09/2026 on the free engine woken by the click (Altamira/PA): ~45 s to wake, then
 // the deep run still "running" at 110 s (the old limit gave up at 155 s with the engine working); it finished
 // ~130 s after the wake. While the engine keeps answering "running", the reading keeps waiting, up to the limit.
 const memo=new Map(),READY_TTL=10*60*1000,PARTIAL_TTL=3*60*1000,FAIL_TTL=60*1000,QUICK_TIMEOUT_MS=120000,STATUS_TIMEOUT_MS=20000,DEEP_WAIT_MS=240000,HARD_LIMIT_MS=300000,ENGINE_CACHE_MS=190000;
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const currentCar=()=>norm((window.current||{}).car_code);
 function card(car){const sel=`.rx45-panel-card[data-car="${CSS.escape(car)}"]`;return document.querySelector('#rx43SnapshotHost '+sel)||document.querySelector(sel)}
 function slot(c){let s=c.querySelector('[data-rx-full-slot]');if(s)return s;s=document.createElement('section');s.className='rx45-section rx-f1b-full';s.setAttribute('data-rx-full-slot','');s.setAttribute('aria-live','polite');
  const comp=c.querySelector('.rx45-compliance'),sec=comp&&comp.closest('.rx45-section'),act=c.querySelector('.rx45-actions');
  if(sec&&sec.parentNode===c)sec.after(s);else if(act&&act.parentNode===c)act.before(s);else c.appendChild(s);return s}
 // The main button keeps its name in every state: it opens the section (where loading, pending and the
 // section's own "Consultar de novo" live); loading is announced by aria-busy, never by a new label.
 function button(c,e){const b=c.querySelector('#rx45Full');if(!b)return;b.textContent='VER ANÁLISE COMPLETA';if(e.phase==='loading')b.setAttribute('aria-busy','true');else b.removeAttribute('aria-busy')}
 function paint(car,scroll){const e=memo.get(car),c=card(car);if(!e||!c)return;const s=slot(c);s.dataset.phase=e.phase;s.innerHTML=html(e);button(c,e);try{if(typeof window.rxV48RefreshAudit==='function')window.rxV48RefreshAudit(c)}catch(x){}if(scroll){try{s.scrollIntoView({block:'start',behavior:'smooth'})}catch(x){}}}
 // The answer must be about THIS property; anything else is not an answer.
 const sameCar=(a,car)=>!!a&&typeof a==='object'&&norm(((a.car||{}).properties||{}).cod_imovel)===car;
 // When the engine produced the answer (completed_at). A time in the future beyond clock drift is not a time.
 function producedAt(...objs){for(const o of objs){const v=o&&typeof o==='object'?(o.completed_at||(o.progressive&&o.progressive.completed_at)):null;if(v){const t=Date.parse(v);if(isNum(t)&&t<=Date.now()+300000)return t}}return null}
 async function fetchJson(url,ms){const ctrl=new AbortController(),t=setTimeout(()=>ctrl.abort(),ms);try{const r=await fetch(url,{cache:'no-store',signal:ctrl.signal});let d=null;try{d=await r.json()}catch(x){}return {ok:r.ok,d}}finally{clearTimeout(t)}}
 async function attempt(car,alive,hardUntil){
  const enc=encodeURIComponent(car);
  const first=await fetchJson(`/v1/live/quick/${enc}?deep=1`,QUICK_TIMEOUT_MS);
  // An engine answer that says it did not work (ok:false) is no reason to wait for a run.
  if(!first.ok||!first.d||first.d.ok===false)return null;
  const d=first.d,ds=d.deep_state||{};
  // Only a cache hit answers on the first response. On any other mode the engine has just scheduled a new run
  // and deep_state may still be the PREVIOUS run's "ready": showing it would pass old data as current.
  if(d.mode==='quick-cache'){const done=ds.state==='ready'&&ds.analysis?ds.analysis:d.analysis;if(done)return sameCar(done,car)?{analysis:done,at:producedAt(ds,done,d.analysis)}:null}
  // The engine keeps working: poll its state, stop on its answer, on its failure or after 3 status errors in a row.
  const until=Math.min(Date.now()+DEEP_WAIT_MS,hardUntil);let misses=0,running=false;
  for(let i=0;Date.now()<until;i++){
   await sleep(i<4?1500:2500);if(!alive())return null;
   const st=await fetchJson(`/v1/live/progressive/status/${enc}`,STATUS_TIMEOUT_MS).catch(()=>({ok:false,d:null}));
   if(st.ok&&st.d&&st.d.state==='ready'){if(!sameCar(st.d.analysis,car))return null;const t=producedAt(st.d,st.d.analysis);
    // Without completed_at the arrival is the production time only for a run this attempt saw running.
    return {analysis:st.d.analysis,at:t!==null?t:(running?Date.now():null)}}
   if(st.ok&&st.d&&st.d.state==='running')running=true;
   if(st.d&&st.d.state==='failed')return null;
   misses=st.ok?0:misses+1;if(misses>=3)return null;
  }
  return null;
 }
 function usable(e){if(!e)return false;if(e.phase==='loading')return true;const f=e.fill||{};if(f.state==='scheduled'||f.state==='running')return true;
  return Date.now()-e.done<(e.phase==='ready'?(pending(e)?PARTIAL_TTL:READY_TTL):FAIL_TTL)}
 // Pending sources of a finished reading ask again ONCE by themselves (start() is the only automatic caller), when the engine cache holding the
 // pending answer has expired (earlier the engine would hand back the same answer); "Consultar de novo"
 // asks again at that same moment. Only rows that came to answer change. Nothing runs for a closed panel.
 function fill(car,mine,manual){
  const f=mine.fill||{};if(f.state==='scheduled'||f.state==='running'||mine.phase!=='ready'||!pending(mine))return;
  const base=isNum(mine.fillBase)?mine.fillBase:(isNum(mine.at)?mine.at:mine.done),at=Math.max(Date.now(),base+ENGINE_CACHE_MS);
  mine.fill={state:'scheduled',when:at};paint(car,false);
  setTimeout(async()=>{
   if(memo.get(car)!==mine||(mine.fill||{}).state!=='scheduled')return;
   if(!card(car)){mine.fill={state:'idle'};return}
   mine.fill={state:'running',when:at};paint(car,false);
   const alive=()=>memo.get(car)===mine&&!!card(car);let res=null;
   try{res=await attempt(car,alive,Date.now()+HARD_LIMIT_MS)}catch(x){res=null}
   if(memo.get(car)!==mine)return;
   if(res){const m=merge(mine.rows,rows(res.analysis));mine.rows=m.rows;if(m.changed&&isNum(res.at))mine.refilled=res.at}
   mine.fillBase=res&&isNum(res.at)?res.at:Date.now();mine.fill={state:'idle',tried:true};mine.done=Date.now();paint(car,false);
  },Math.max(0,at-Date.now()));
 }
 // Only for a property whose panel is open: a click that did not open the panel never wakes the engine.
 function start(car,opts){car=norm(car);opts=opts||{};if(!car||!card(car))return null;let e=memo.get(car);
  if(opts.force||!usable(e)){const token={};e={phase:'loading',at:null,done:Date.now(),rows:null,token};memo.set(car,e);const mine=e;
   // Alive while this is still the query for the car AND its panel is open. A closed panel stops the
   // polling and forgets the entry, so opening the property again asks again.
   const alive=()=>memo.get(car)===mine&&!!card(car);
   (async()=>{const hardUntil=Date.now()+HARD_LIMIT_MS;let res=null;
    for(let i=0;i<2&&!res;i++){
     if(i){if(Date.now()>hardUntil-30000)break;await sleep(3000)}
     if(!alive()){if(memo.get(car)===mine)memo.delete(car);return}
     try{res=await attempt(car,alive,hardUntil)}catch(x){res=null}
     if(memo.get(car)!==mine)return;
    }
    if(!res&&!card(car)){memo.delete(car);return}
    const list=res?rows(res.analysis):[];mine.phase=list.length?'ready':'failed';mine.rows=list.length?list:null;mine.at=list.length&&isNum(res.at)?res.at:null;mine.done=Date.now();paint(car,false);
    if(mine.phase==='ready')fill(car,mine,false)})();}
  paint(car,!!opts.scroll);return e}
 function fromButton(){const car=currentCar();if(car)start(car,{scroll:true})}
 function install(){
  window.rxProgressiveAnalyze=fromButton;
  const legacy=document.querySelector('#analyze');if(legacy)legacy.onclick=fromButton;
  document.addEventListener('rx45:panel-rendered',ev=>{const car=norm(ev&&ev.detail&&ev.detail.car);if(car&&memo.has(car))paint(car,false)});
  document.addEventListener('click',ev=>{const t=ev.target;if(!t||!t.closest)return;
   const retry=t.closest('[data-rx-f1b-retry]');if(retry){ev.preventDefault();const car=norm(retry.closest('.rx45-panel-card')?.dataset.car);if(car)start(car,{force:true,scroll:false});return}
   const again=t.closest('[data-rx-f1b-fill]');if(again){ev.preventDefault();const car=norm(again.closest('.rx45-panel-card')?.dataset.car),e=car&&memo.get(car);if(e)fill(car,e,true);return}
   const cta=t.closest('[data-rx46-action="full"]');if(cta){const car=norm(cta.closest('.rx46-card')?.dataset.car||currentCar());if(car)setTimeout(()=>start(car,{scroll:false}),0)}},true);
 }
 window.rxFullReadingF1b={rows,html,merge,bulletin,start,fill,peek:car=>memo.get(norm(car))||null,LABEL};
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
print("RX_FULL_READING_F1B=panel_visible_reading one_query_per_car rows:sim_nao_pendente fresh_only fill_pending_once", flush=True)

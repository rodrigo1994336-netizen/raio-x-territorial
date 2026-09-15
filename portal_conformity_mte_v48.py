from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_map_panel_v45 as v45
from car_resilient import CAR_RE
from mte_slave_labor_v48 import query_mte_slave_labor
import portal_panel_sources_f2

app = portal_v8.app
_BASE_PANEL_SYNC = v45._panel_sync

# Approved V48 sources that are not implemented yet. They belong to the audit
# catalog only and MUST NOT become default conformity rows or denominator entries before working.
_HIDDEN_PLANNED = [
    {"id": "ibama_embargo_area", "label": "IBAMA — embargos por área", "source": "IBAMA"},
    {"id": "ibama_auto_area", "label": "IBAMA — autos por área", "source": "IBAMA"},
    {"id": "icmbio_embargo_area", "label": "ICMBio — embargos por área", "source": "ICMBio"},
    {"id": "icmbio_auto_area", "label": "ICMBio — autos por área", "source": "ICMBio"},
    {"id": "incra_settlement", "label": "INCRA — assentamento rural", "source": "INCRA"},
    {"id": "incra_quilombola", "label": "INCRA — território quilombola", "source": "INCRA"},
]


def _panel_sync_v48(car_code: str, *, cancel_event=None) -> dict[str, Any]:
    # The map-panel route always passes cancel_event; forward it so F1 can stop curl.
    base = _BASE_PANEL_SYNC(car_code, cancel_event=cancel_event)
    if not base.get("ok"):
        return dict(base)

    # The canonical registry already contains every implemented source.
    # This wrapper enriches MTE presentation only; it never changes cardinality.
    out = dict(base)
    out["sources"] = [dict(x) for x in (base.get("sources") or [])]
    compliance = [dict(x) for x in (base.get("compliance_sources") or [])]
    mte = next((x for x in compliance if str(x.get("id") or "") == "mte_slave_labor"), None)
    if mte is None:
        raise RuntimeError("mte_source_missing_from_canonical_registry")
    mte.update({
        "state": "on_demand",
        "reason": "Aguardando consulta nominal. O CAR não fornece CPF/CNPJ do proprietário com segurança.",
    })
    out["compliance_sources"] = compliance
    audit = dict(base.get("source_audit") or {})
    audit["visible_compliance"] = len(out["compliance_sources"])
    audit["hidden_planned_count"] = len(_HIDDEN_PLANNED)
    audit["hidden_planned_sources"] = [dict(x) for x in _HIDDEN_PLANNED]
    audit["deep_sources_requested"] = False
    out["source_audit"] = audit
    return out


# Existing /v1/live/map-panel/{car_code} endpoint resolves this global at call time.
v45._panel_sync = _panel_sync_v48


@app.get("/v1/live/conformity/mte/{car_code}")
async def conformity_mte_v48(car_code: str):
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        raise HTTPException(status_code=422, detail="invalid_car_format")
    # V48 Source 1 deliberately passes no owner identity. The current public CAR
    # contract does not expose a trustworthy holder CPF/CNPJ and no substitute is inferred.
    return await asyncio.to_thread(query_mte_slave_labor, None, None)


UI = r'''
<style id="rxConformityMteV48">
.rx45-check[data-source="mte_slave_labor"]{align-items:flex-start!important;min-height:54px!important;padding:8px!important;grid-column:1/-1}
.rx48-check-body{min-width:0;display:grid;gap:3px;line-height:1.35}.rx48-check-label{font-weight:850;color:#e5f3eb;font-size:8px}.rx48-check-status{font-size:7px;font-weight:900;letter-spacing:.35px;text-transform:uppercase;color:#a7bbb0}.rx48-check-reason{font-size:7px;color:#91a69b;line-height:1.38}.rx48-check-meta{font-size:6.5px;color:#6f887b;line-height:1.35}.rx45-dot.checking{background:var(--rx45-yellow)}.rx45-dot.source_failed{background:var(--rx45-gray)}.rx48-retry{display:inline-flex;align-items:center;margin-top:4px;min-height:44px;padding:0 12px;border-radius:10px;border:1px solid var(--rx45-line);background:transparent;color:var(--rx45-text);font:700 10px system-ui,sans-serif;cursor:pointer}.rx45-dot.blocked_missing_owner_identity{background:var(--rx45-gray)}.rx45-dot.checked_clear{background:var(--rx45-green)}.rx45-dot.checked_hit{background:var(--rx45-red)}
.rx48-audit-box{display:none;margin-top:8px;padding:8px;border:1px solid #1d382c;border-radius:10px;background:#091a13}.rx48-audit-box.open{display:grid;gap:5px}.rx48-audit-item{display:grid;grid-template-columns:minmax(90px,.85fr) minmax(0,1.15fr);gap:8px;padding:5px 0;border-top:1px solid #173126;font-size:7px;line-height:1.4}.rx48-audit-item:first-child{border-top:0}.rx48-audit-item b{color:#dcebe3}.rx48-audit-item span{color:#8fa69a}.rx48-audit-item em{display:block;font-style:normal;font-weight:900;font-size:6.5px;letter-spacing:.25px;text-transform:uppercase;color:#a8bbb1;margin-bottom:2px}
@media(max-width:390px){.rx45-check[data-source="mte_slave_labor"]{min-height:0!important}.rx48-check-label{font-size:8.5px}.rx48-check-reason{font-size:7.5px}.rx48-audit-item{grid-template-columns:1fr;gap:2px}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmtDate=v=>{if(!v)return '';const s=String(v);const m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:s};
 const fmtQuery=v=>{if(!v)return 'não realizada';try{return new Date(v).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})}catch(e){return String(v)}};
 function body(el,label,status,reason,meta){el.innerHTML=`<i class="rx45-dot ${esc(el.dataset.state||'not_consulted')}"></i><span class="rx48-check-body"><span class="rx48-check-label">${esc(label)}</span><span class="rx48-check-status">${esc(status)}</span><span class="rx48-check-reason">${esc(reason)}</span>${meta?`<span class="rx48-check-meta">${esc(meta)}</span>`:''}</span>`}
 const ANSWERED_STATES=new Set(['ANSWERED_CLEAR','ANSWERED_HIT']);
 function canonicalAuditState(state,answered){if(state==='checking')return 'QUERYING';if(state==='source_failed')return 'FAILED';if(state==='blocked_missing_owner_identity')return 'BLOCKED';if(state==='checked_clear')return 'ANSWERED_CLEAR';if(answered)return 'ANSWERED_HIT';return 'NOT_QUERIED'}
 function auditModel(card){const a=card?.__rxSourceAudit;if(!a||a.available!==true||!Array.isArray(a.registry)||!a.registry.length)return null;const ids=a.registry.map(x=>String(x?.id||''));if(new Set(ids).size!==ids.length||ids.some(x=>!x))return null;return a}
 function updateCounter(card){if(!card)return;const node=card.querySelector('.rx45-audit-count');if(!node)return;const a=auditModel(card),button=node.querySelector('#rx45Audit');let text=card.dataset.auditFailed==='1'?'Consulta às fontes oficiais pendente · ':'Consultando fontes oficiais… · ';if(a){const responded=a.registry.filter(x=>ANSWERED_STATES.has(x.state)).length;a.responded=responded;a.total=a.registry.length;text=''}node.childNodes.forEach(n=>{if(n.nodeType===3)n.remove()});node.insertBefore(document.createTextNode(text),button||null)}
 function setState(el,state,label,status,reason,meta,answered){el.dataset.state=state;el.dataset.answered=answered?'1':'0';const card=el.closest('.rx45-panel-card'),a=auditModel(card),auditState=canonicalAuditState(state,answered);el.dataset.auditState=auditState;if(a){const source=a.registry.find(x=>x.id===el.dataset.source);if(source)source.state=auditState;else card.__rxSourceAudit=null}body(el,label,status,reason,meta);if(state==='source_failed'){const rb=document.createElement('button');rb.type='button';rb.className='rx48-retry';rb.dataset.rx48Retry=el.dataset.source||'';rb.textContent='Consultar de novo';el.querySelector('.rx48-check-body')?.appendChild(rb)}const dot=el.querySelector('.rx45-dot');if(dot)dot.className='rx45-dot '+state;updateCounter(card)}
 const MTE_ID='mte_slave_labor',MTE_LABEL='MTE — Trabalho Escravo',MTE_SOURCE='Fonte: Ministério do Trabalho e Emprego';
 const mteMeta=d=>`Fonte: MTE${d.data_date?` · dado/publicação: ${fmtDate(d.data_date)}`:''} · consulta: ${fmtQuery(d.queried_at)}`;
 // F2: same vocabulary as portal_panel_sources_f2.classify_mte — only a recognised answer leaves "pending".
 function mteView(d){if(!d||typeof d!=='object'||d.ok!==true)return null;if(d.state==='blocked_missing_owner_identity')return {state:d.state,status:'NÃO VERIFICADA',answered:false};if(d.answered===true&&d.state==='checked_clear')return {state:d.state,status:'SEM OCORRÊNCIA',answered:true};if(d.answered===true&&d.state==='checked_hit')return {state:d.state,status:'OCORRÊNCIA LOCALIZADA',answered:true};return null}
 const mtePause=ms=>new Promise(res=>setTimeout(res,ms));
 async function mteQuery(car,alive,attempts=2){for(let i=0;i<attempts;i++){if(i){await mtePause(4000);if(!alive())return {abandoned:true}}try{const r=await fetch(`/v1/live/conformity/mte/${encodeURIComponent(car)}`),d=await r.json();if(r.ok&&mteView(d))return {done:true,data:d}}catch(e){}}return {done:false}}
 function mtePaint(card,e){const el=card.querySelector('.rx45-check[data-source="mte_slave_labor"]');if(!el)return;if(e.phase==='checking'){setState(el,'checking',MTE_LABEL,'CONSULTANDO','Validando a lista oficial do MTE. Nenhum resultado será presumido antes da resposta.',MTE_SOURCE,false);return}const v=e.phase==='done'?mteView(e.data):null;if(v){setState(el,v.state,MTE_LABEL,v.status,e.data.reason,mteMeta(e.data),v.answered);return}setState(el,'source_failed',MTE_LABEL,'CONSULTA PENDENTE','A fonte oficial não respondeu agora. Nenhum resultado foi presumido.',MTE_SOURCE,false)}
 // F2: driven by the rx45:panel-rendered event; the per-CAR memory repaints a re-render without a new query.
 function enhance(card){const S=window.rxPanelSourcesF2;if(!card||!S)return;const el=card.querySelector('.rx45-check[data-source="mte_slave_labor"]');if(!el)return;card.dataset.rx48Enhanced='1';S.settle(MTE_ID,card.dataset.car,mteQuery,mtePaint)}
 function enhanceCar(car){if(!car)return;const card=q(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(card)enhance(card)}
 function auditItem(label,status,detail){return `<div class="rx48-audit-item"><b>${esc(label)}</b><span><em>${esc(status)}</em>${esc(detail)}</span></div>`}
 // F1B: the box lists only what THIS consultation knows — the CAR, each source that answered, is pending or is
 // being asked, and, once "Análise completa" ran, its own answers (the same Sim/Não/pendente of the panel,
 // with the time the data was produced). A source nobody asked is not listed (campo vazio não aparece), and
 // registry sources that the full analysis answers are listed once, from the analysis.
 const READING_SOURCES=new Set(['embargo','prodes','indigenous_land','conservation_unit','public_forest']);
 const READING_STATUS={sim:'RESPONDEU · COM OCORRÊNCIA',nao:'RESPONDEU · SEM OCORRÊNCIA',pendente:'CONSULTA PENDENTE'};
 function readingOf(card){try{const F=window.rxFullReadingF1b;return F&&card?.dataset?.car?F.peek(card.dataset.car):null}catch(e){return null}}
 function buildAudit(card){let box=card.querySelector('.rx48-audit-box');if(!box){box=document.createElement('div');box.className='rx48-audit-box';card.querySelector('.rx45-audit-count')?.after(box)}const a=auditModel(card),R=readingOf(card),items=[];
  if(!a){items.push(auditItem('Fontes','CONSULTA PENDENTE','A lista de fontes desta consulta não pôde ser montada agora. Nada foi presumido.'))}
  else{a.registry.forEach(source=>{
   if(source.id==='car'){items.push(auditItem(source.label,'RESPONDEU','Perímetro e atributos cadastrais do CAR.'));return}
   if(R&&READING_SOURCES.has(source.id))return;
   const row=card.querySelector(`.rx45-check[data-source="${CSS.escape(source.id)}"]`);
   if(!row||!row.dataset.state)return;
   const status=row.querySelector('.rx48-check-status')?.textContent?.trim();if(!status)return;
   items.push(auditItem(source.label,status,row.querySelector('.rx48-check-reason')?.textContent?.trim()||''))})}
  if(R&&R.phase==='loading')items.push(auditItem('Análise completa','CONSULTANDO','Embargos, desmatamento, áreas protegidas, mineração, queimadas e água.'));
  else if(R&&R.phase==='ready'&&Array.isArray(R.rows))R.rows.forEach(r=>items.push(auditItem(r.source||r.q,READING_STATUS[r.answer]||READING_STATUS.pendente,r.q+(Number.isFinite(R.at)?` · consulta: ${fmtQuery(R.at)}`:''))));
  else if(R)items.push(auditItem('Análise completa','CONSULTA PENDENTE','As fontes oficiais não responderam agora. Nada foi presumido.'));
  box.innerHTML=items.join('');return box}
 // Called by the full analysis when it repaints: an open box follows it.
 window.rxV48RefreshAudit=function(card){const box=card?.querySelector?.('.rx48-audit-box.open');if(box)buildAudit(card)};
 window.rxV48Audit={build:buildAudit};
 function toggleAudit(card,button){if(!card)return;const box=buildAudit(card),open=!box.classList.contains('open');box.classList.toggle('open',open);button?.setAttribute('aria-expanded',open?'true':'false')}
 function interceptAudit(e){const button=e.target.closest?.('#rx45Audit');if(!button)return;const card=button.closest('.rx45-panel-card');if(!card)return;e.preventDefault();e.stopImmediatePropagation();toggleAudit(card,button)}
 function install(){if(window.__rx48MteEventWired)return;window.__rx48MteEventWired=true;document.addEventListener('click',interceptAudit,true);window.rxPanelSourcesF2?.onRendered(enhanceCar)}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
 if(!window.__rx48RetryWired){window.__rx48RetryWired=1;document.addEventListener('click',e=>{const b=e.target.closest?.('[data-rx48-retry="mte_slave_labor"]');if(!b)return;e.preventDefault();const card=b.closest('.rx45-panel-card'),S=window.rxPanelSourcesF2;if(!card||!S)return;S.forget(MTE_ID,card.dataset.car);S.settle(MTE_ID,card.dataset.car,(car,alive)=>mteQuery(car,alive,1),mtePaint)})}window.rxV48UpdateComplianceSource=function(id,result,card){const el=(card&&card.querySelector?card:document).querySelector(`.rx45-check[data-source="${id}"]`);if(!el)return;setState(el,result.state||'not_consulted',result.label||id,result.status||'NÃO CONSULTADA',result.reason||'',result.meta||'',!!result.answered)};
})();
</script>
'''

portal_panel_sources_f2.install()  # the shared per-CAR runtime must precede the row scripts
if "RX_CONFORMITY_MTE_V48" not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", UI + "<!-- RX_CONFORMITY_MTE_V48 --></body>")

print("RX_PORTAL_CONFORMITY_MTE_V48=canonical11_mte_future6_audit_only_truth_idempotent", flush=True)

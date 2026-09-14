from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_map_panel_v45 as v45
from car_resilient import CAR_RE
import sinaflor_authorization_hardening_v48  # noqa: F401 — patches the source engine deliberately
from sinaflor_authorization_v48 import query_sinaflor_authorization
import portal_panel_sources_f2

app = portal_v8.app
_BASE_PANEL_SYNC = v45._panel_sync


def _panel_sync_sinaflor_v48(car_code: str, *, cancel_event=None) -> dict[str, Any]:
    # The map-panel route always passes cancel_event; forward it so F1 can stop curl.
    base = _BASE_PANEL_SYNC(car_code, cancel_event=cancel_event)
    if not base.get("ok"):
        return dict(base)

    # The canonical registry already contains SINAFLOR. Enrich its row only;
    # never increment or reconstruct the denominator in this wrapper.
    out = dict(base)
    out["sources"] = [dict(x) for x in (base.get("sources") or [])]
    compliance = [dict(x) for x in (base.get("compliance_sources") or [])]
    sinaflor = next((x for x in compliance if str(x.get("id") or "") == "sinaflor"), None)
    if sinaflor is None:
        raise RuntimeError("sinaflor_source_missing_from_canonical_registry")
    sinaflor.update({
        "state": "on_demand",
        "reason": "Aguardando confronto espacial com a camada pública IBAMA/PAMGIA.",
    })
    out["compliance_sources"] = compliance
    audit = dict(base.get("source_audit") or {})
    audit["visible_compliance"] = len(out["compliance_sources"])
    out["source_audit"] = audit
    return out


v45._panel_sync = _panel_sync_sinaflor_v48


@app.get("/v1/live/conformity/sinaflor/{car_code}")
async def conformity_sinaflor_v48(car_code: str):
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        raise HTTPException(status_code=422, detail="invalid_car_format")
    return await asyncio.to_thread(query_sinaflor_authorization, code)


# MTE owns the shared audit UI and now renders every canonical registry row
# generically. SINAFLOR must not patch or duplicate that registry/audit renderer.

UI = r'''
<style id="rxConformitySinaflorV48">
.rx45-check[data-source="sinaflor"]{align-items:flex-start!important;min-height:54px!important;padding:8px!important;grid-column:1/-1}
.rx45-dot.checked_authorization_overlap{background:var(--rx45-green)}
.rx45-dot.checked_authorization_overlap_unconfirmed,.rx45-dot.checked_spatial_record_unconfirmed{background:var(--rx45-yellow)}
@media(max-width:390px){.rx45-check[data-source="sinaflor"]{min-height:0!important}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const fmtDate=v=>{if(!v)return 'não publicada pela camada';const s=String(v),m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:s};
 const fmtQuery=v=>{if(!v)return 'não realizada';try{return new Date(v).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})}catch(e){return String(v)}};
 const ha=v=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:4}):'—'};
 function row(card){return card?.querySelector('.rx45-check[data-source="sinaflor"]')||null}
 const SF_ID='sinaflor',SF_LABEL='SINAFLOR — Supressão';
 // F2: same vocabulary as portal_panel_sources_f2.classify_sinaflor — only an answer the source marked as answered leaves "pending".
 function view(d){if(!d||typeof d!=='object'||d.ok!==true||d.answered!==true)return null;const first=(d.matches||[])[0]||{},state=d.state;
   if(state==='checked_clear')return {state,status:'SEM AUTORIZAÇÃO SINAFLOR LOCALIZADA',reason:d.reason};
   if(state==='checked_spatial_record_unconfirmed'){const num=first.authorization_number?`ASV ${first.authorization_number}: `:'';return {state,status:'REGISTRO ESPACIAL · VÍNCULO NÃO CONFIRMADO',reason:`${num}intersecta espacialmente o CAR, mas a geometria publicada é territorial ampla e não identifica este CAR. O imóvel não foi declarado como autorizado.`}}
   if(state==='checked_authorization_overlap'){const num=first.authorization_number?`ASV ${first.authorization_number}`:'Autorização';return {state,status:'AUTORIZAÇÃO SINAFLOR LOCALIZADA',reason:`${num} com interseção espacial confirmada${first.overlap_ha!=null?` (${ha(first.overlap_ha)} ha no CAR)`:''}. A validade para eventual desmatamento depende também da data do evento.`}}
   if(state==='checked_authorization_overlap_unconfirmed'){const num=first.authorization_number?`ASV ${first.authorization_number}: `:'';return {state,status:'AUTORIZAÇÃO LOCALIZADA · VIGÊNCIA NÃO CONFIRMADA',reason:`${num}há vínculo espacial/local, mas vigência/status não confirmam cobertura atual. Não foi concluído que eventual desmatamento estava autorizado.`}}
   return null}
 function paint(card,e){const el=row(card),U=window.rxV48UpdateComplianceSource;if(!el||typeof U!=='function')return;if(e.phase==='checking'){U(SF_ID,{state:'checking',label:SF_LABEL,status:'CONSULTANDO',reason:'Confrontando o perímetro do CAR com os polígonos públicos ASV/UAS do SINAFLOR. BBOX não conta como ocorrência.',meta:'Fonte: IBAMA/PAMGIA',answered:false},card);return}const d=e.data||{},v=e.phase==='done'?view(d):null;if(v){U(SF_ID,{state:v.state,label:SF_LABEL,status:v.status,reason:v.reason,meta:`Fonte: IBAMA/PAMGIA · dado: ${fmtDate(d.data_date)} · consulta: ${fmtQuery(d.queried_at)}`,answered:true},card);return}
   // Not answered: no data date is claimed for a query that did not return.
   U(SF_ID,{state:'source_failed',label:SF_LABEL,status:'CONSULTA PENDENTE',reason:'A fonte oficial não respondeu agora. Nenhum resultado foi presumido.',meta:`Fonte: IBAMA/PAMGIA${d.queried_at?` · consulta: ${fmtQuery(d.queried_at)}`:''}`,answered:false},card)}
 const sfPause=ms=>new Promise(res=>setTimeout(res,ms));
 async function query(car,alive,attempts=2){let last=null;for(let i=0;i<attempts;i++){if(i){await sfPause(4000);if(!alive())return {abandoned:true}}try{const r=await fetch(`/v1/live/conformity/sinaflor/${encodeURIComponent(car)}`),d=await r.json();if(r.ok&&d&&typeof d==='object')last=d;if(r.ok&&view(d))return {done:true,data:d}}catch(e){}}return {done:false,data:last}}
 // F2: driven by the rx45:panel-rendered event; the per-CAR memory repaints a re-render without a new query.
 function load(card){const S=window.rxPanelSourcesF2;if(!card||!S||!row(card))return;S.settle(SF_ID,card.dataset.car,query,paint)}
 if(!window.__rx48SinaflorRetryWired){window.__rx48SinaflorRetryWired=1;document.addEventListener('click',e=>{const b=e.target.closest?.('[data-rx48-retry="sinaflor"]');if(!b)return;e.preventDefault();const card=b.closest('.rx45-panel-card'),S=window.rxPanelSourcesF2;if(!card||!S)return;S.forget(SF_ID,card.dataset.car);S.settle(SF_ID,card.dataset.car,(car,alive)=>query(car,alive,1),paint)})}
 function enhanceCar(car){if(!car)return;const card=q(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(card&&row(card))load(card)}
 function install(){if(window.__rx48SinaflorEventWired)return;window.__rx48SinaflorEventWired=true;window.rxPanelSourcesF2?.onRendered(enhanceCar)}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

portal_panel_sources_f2.install()  # idempotent; MTE normally injected it already
if "RX_CONFORMITY_SINAFLOR_V48" not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", UI + "<!-- RX_CONFORMITY_SINAFLOR_V48 --></body>")

print("RX_PORTAL_CONFORMITY_SINAFLOR_V48=canonical11_exact_spatial_broad_scope_guard_future6_audit_only", flush=True)

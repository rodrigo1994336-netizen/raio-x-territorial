from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_map_panel_v45 as v45
from car_resilient import CAR_RE
import sinaflor_authorization_hardening_v48  # noqa: F401 — patches the source engine deliberately
from sinaflor_authorization_v48 import query_sinaflor_authorization

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
 const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();
 const fmtDate=v=>{if(!v)return 'não publicada pela camada';const s=String(v),m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:s};
 const fmtQuery=v=>{if(!v)return 'não realizada';try{return new Date(v).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})}catch(e){return String(v)}};
 const ha=v=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:4}):'—'};
 function row(card){return card?.querySelector('.rx45-check[data-source="sinaflor"]')||null}
 function apply(card,d){const el=row(card);if(!el||typeof window.rxV48UpdateComplianceSource!=='function')return;let status='FONTE INDISPONÍVEL',reason=d.reason||'A consulta não foi concluída. Nenhuma ausência foi presumida.',answered=false,state=d.state||'source_failed';const first=(d.matches||[])[0]||{};
   if(d.ok&&d.answered&&state==='checked_clear'){status='SEM AUTORIZAÇÃO SINAFLOR LOCALIZADA';reason=d.reason;answered=true}
   else if(d.ok&&d.answered&&state==='checked_spatial_record_unconfirmed'){status='REGISTRO ESPACIAL · VÍNCULO NÃO CONFIRMADO';const num=first.authorization_number?`ASV ${first.authorization_number}: `:'';reason=`${num}intersecta espacialmente o CAR, mas a geometria publicada é territorial ampla e não identifica este CAR. O imóvel não foi declarado como autorizado.`;answered=true}
   else if(d.ok&&d.answered&&state==='checked_authorization_overlap'){status='AUTORIZAÇÃO SINAFLOR LOCALIZADA';const num=first.authorization_number?`ASV ${first.authorization_number}`:'Autorização';reason=`${num} com interseção espacial confirmada${first.overlap_ha!=null?` (${ha(first.overlap_ha)} ha no CAR)`:''}. A validade para eventual desmatamento depende também da data do evento.`;answered=true}
   else if(d.ok&&d.answered&&state==='checked_authorization_overlap_unconfirmed'){status='AUTORIZAÇÃO LOCALIZADA · VIGÊNCIA NÃO CONFIRMADA';const num=first.authorization_number?`ASV ${first.authorization_number}: `:'';reason=`${num}há vínculo espacial/local, mas vigência/status não confirmam cobertura atual. Não foi concluído que eventual desmatamento estava autorizado.`;answered=true}
   const meta=`Fonte: IBAMA/PAMGIA · dado: ${fmtDate(d.data_date)} · consulta: ${fmtQuery(d.queried_at)}`;
   window.rxV48UpdateComplianceSource('sinaflor',{state,label:'SINAFLOR — Supressão',status,reason,meta,answered});
 }
 async function load(card){const el=row(card);if(!el||el.dataset.rx48SinaflorLoaded==='1')return;el.dataset.rx48SinaflorLoaded='1';window.rxV48UpdateComplianceSource?.('sinaflor',{state:'checking',label:'SINAFLOR — Supressão',status:'CONSULTANDO',reason:'Confrontando o perímetro do CAR com os polígonos públicos ASV/UAS do SINAFLOR. BBOX não conta como ocorrência.','meta':'Fonte: IBAMA/PAMGIA',answered:false});const car=card.dataset.car||'';try{const r=await fetch(`/v1/live/conformity/sinaflor/${encodeURIComponent(car)}`),d=await r.json();if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);apply(card,d)}catch(e){apply(card,{ok:false,answered:false,state:'source_failed',reason:'A fonte oficial SINAFLOR/PAMGIA não respondeu de forma utilizável. Nenhuma ausência foi presumida.',queried_at:new Date().toISOString(),detail:e.message})}}
 function enhanceCar(car){if(!car)return;const card=q(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(card&&row(card))load(card)}
 function schedule(car){[120,720,2200,9800,10800].forEach(ms=>setTimeout(()=>{if(currentCar()===car)enhanceCar(car)},ms))}
 function install(){if(window.__rx48SinaflorShowWrapped)return;const base=window.showProperty;if(typeof base!=='function'){const n=(window.__rx48SinaflorInstallAttempts||0)+1;window.__rx48SinaflorInstallAttempts=n;if(n<=4)setTimeout(install,140);return}window.__rx48SinaflorShowWrapped=true;const wrapped=function(...args){const out=base.apply(this,args),p=args?.[0]||{},car=String(p.car_code||p?.properties?.cod_imovel||p?.properties?.id_imovel||currentCar()||'').trim().toUpperCase();if(car)schedule(car);return out};window.showProperty=wrapped;try{showProperty=wrapped}catch(e){}setTimeout(()=>enhanceCar(currentCar()),320)}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
'''

if "RX_CONFORMITY_SINAFLOR_V48" not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", UI + "<!-- RX_CONFORMITY_SINAFLOR_V48 --></body>")

print("RX_PORTAL_CONFORMITY_SINAFLOR_V48=canonical11_exact_spatial_broad_scope_guard_future6_audit_only", flush=True)

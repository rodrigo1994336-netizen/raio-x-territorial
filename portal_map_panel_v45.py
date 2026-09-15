from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException, Request

import portal_v8
from car_resilient import CAR_RE, fetch_car_live_resilient
from property_identity_runtime import (
    NEGATIVE_TTL_SECONDS,
    SIGEF_REFERENCE_MIN_OVERLAP,
    forget_unanswered as _forget_identity_unanswered,
    resolve_property_identity_sync,
    sigef_reference_rank,
)
from source_audit_registry_v49 import build_source_audit, compliance_sources as audit_compliance_sources
from external_process_lifecycle import ManagedOperationTimeout, RequestDisconnected, install_shutdown_cleanup, run_sync_with_request_lifecycle

app = portal_v8.app
install_shutdown_cleanup(app)
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 600
# F2: the reference comes from the official INCRA Acervo Fundiário (SIGEF and SNCI), never the PAMGIA mirror.
SIGEF_REFERENCE_ORIGIN = "Acervo Fundiário do INCRA (SIGEF)"
INCRA_REFERENCE_KINDS = ("SIGEF_CADASTRAL", "SNCI_CADASTRAL")


def _unanswered_key(code: str) -> str:
    return f"{code}|unanswered"


def forget_unanswered(car_code: str) -> None:
    """The explicit client retry: drop the brief memory of a reference query that did not answer."""
    code = str(car_code or "").strip().upper()
    _CACHE.pop(_unanswered_key(code), None)
    _forget_identity_unanswered(code)


def _first(props: dict[str, Any], *keys: str):
    for key in keys:
        value = props.get(key)
        if value not in (None, "", "null"):
            return value
    return None


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _sigef_reference(identity: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
    """C2b: the SIGEF/INCRA parcel that best coincides with the CAR (never the title).

    Only parcels covering at least half of the CAR qualify; among them the one with the highest
    min(share of the CAR, share of the parcel) wins, so an enclosing settlement never hides the lot's own parcel.
    Returns (reference, state, others). state: found | none | incomplete | unavailable | not_queried.
    An answer cut short (or partly unmeasurable) without a qualifying parcel is 'incomplete', never 'none'.
    """
    state = str(identity.get("sigef_state") or "")
    if state == "not_queried":
        return None, "not_queried", 0
    if state != "answered":  # unavailable, cancelled or unknown: the query did not answer
        return None, "unavailable", 0
    pool = identity.get("sigef_reference_candidates")
    if pool is None:
        pool = identity.get("candidates") or []
    strong = []
    for item in pool:
        if str(item.get("reference_kind") or "SIGEF_CADASTRAL") not in INCRA_REFERENCE_KINDS:
            continue
        overlap = _num(item.get("overlap_ratio"))
        label = str(item.get("name") or "").strip()
        if label and overlap is not None and SIGEF_REFERENCE_MIN_OVERLAP <= overlap <= 1:
            strong.append((overlap, label, item))
    incomplete = bool(identity.get("sigef_incomplete", identity.get("sigef_truncated")))
    if not strong:
        return None, ("incomplete" if incomplete else "none"), 0
    strong.sort(key=lambda x: sigef_reference_rank(x[2]), reverse=True)
    overlap, label, best = strong[0]
    total = max(len(strong), int(identity.get("sigef_reference_candidate_count") or 0))
    kind = str(best.get("reference_kind") or "SIGEF_CADASTRAL")
    reference = {
        "label": label,
        "kind": kind,
        "origin": str(best.get("origin") or "").strip() or SIGEF_REFERENCE_ORIGIN,
        "detail": best.get("detail") or None,
        "certification": best.get("certification") if kind == "SNCI_CADASTRAL" else None,
        "car_overlap_ratio": overlap,
        "parcel_overlap_ratio": _num(best.get("parcel_overlap_ratio")),
        "incra_property_code": best.get("property_code"),
        "parcel_code": best.get("parcel_code"),
    }
    return reference, "found", total - 1


def _panel_sync(car_code: str, *, cancel_event=None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        return {"ok": False, "car_code": code, "detail": "invalid_car_format"}
    now = time.monotonic()
    cached = _CACHE.get(code)
    if cached and now - cached[0] < _TTL_SECONDS:
        out = dict(cached[1])
        out["cached"] = True
        return out
    # A reference query that did not answer is remembered briefly (never as an answer) so a burst of
    # clicks does not repeat SICAR + SIGEF; the explicit retry (?sigef_retry=1) forgets it first.
    missed = _CACHE.get(_unanswered_key(code))
    if missed and now - missed[0] < NEGATIVE_TTL_SECONDS:
        out = dict(missed[1])
        out["cached"] = True
        return out

    car = fetch_car_live_resilient(code, cancel_event=cancel_event)
    if not car.get("ok"):
        return {
            "ok": False,
            "car_code": code,
            "detail": car.get("detail") or "CAR não localizado",
            "source": "SICAR/WFS público",
        }

    props = car.get("properties") or {}
    # The SICAR answer just fetched is handed over, so identity does not ask SICAR a second time.
    identity = resolve_property_identity_sync(code, cancel_event=cancel_event, car=car)
    if identity.get("cancelled") or (cancel_event is not None and cancel_event.is_set()):
        return {"ok": False, "car_code": code, "cancelled": True, "detail": "request_cancelled"}
    identity_ok = bool(identity.get("ok"))
    validation_status = str(identity.get("validation_status") or "").strip().upper() or None
    # C2a: a name reaches the panel only when the identity is VALIDATED, eligible and non-empty.
    name_eligible = (
        identity_ok
        and validation_status == "VALIDATED"
        and identity.get("panel_name_eligible") is True
        and bool(str(identity.get("name") or "").strip())
    )

    area_ha = _num(_first(props, "area", "num_area", "area_ha"))
    modules = _num(_first(props, "m_fiscal", "mod_fiscal", "modulos_fiscais"))
    municipality = _first(props, "municipio", "nom_munici", "nome_municipio")
    uf = _first(props, "uf", "cod_estado", "sigla_uf") or code[:2]
    status = _first(props, "status_imovel", "ind_status", "status")
    condition = _first(props, "condicao", "des_condic")
    property_type = _first(props, "tipo_imovel", "ind_tipo", "tipo")
    created = _first(props, "dat_criacao", "dat_criaca", "data_criacao", "dt_criacao")
    updated = _first(props, "dat_atuali", "data_atualizacao", "dt_atualizacao")

    # C2b: geographic_references carries OpenStreetMap names only. SIGEF names travel in
    # sigef_reference with their overlap and origin, never as bare names.
    sigef_reference, sigef_reference_state, sigef_reference_others = _sigef_reference(identity)
    refs = identity.get("geographic_reference_names") or []
    refs = list(dict.fromkeys(str(x).strip() for x in refs if str(x).strip()))[:3]
    if sigef_reference:
        refs = [x for x in refs if x.casefold() != sigef_reference["label"].casefold()]

    # The counter has exactly one canonical implemented-source registry.
    # Identity resolution is a process, not a source, and never enters it.
    source_audit = build_source_audit({"car": "ANSWERED_HIT"})
    compliance_sources = audit_compliance_sources(source_audit)
    sources = [
        {
            "id": "car",
            "label": "CAR / SICAR",
            "state": "available",
            "detail": "Perímetro e atributos cadastrais públicos disponíveis.",
        }
    ]

    out = {
        "ok": True,
        "car_code": code,
        "validated_name": identity.get("name") if name_eligible else None,
        "validated_name_state": "validated" if name_eligible else "unresolved",
        "name_validation_status": validation_status,
        "panel_name_eligible": bool(name_eligible),
        "validated_name_source": (
            identity.get("origin_label") or identity.get("source")
        ) if name_eligible else None,
        "municipality": municipality,
        "uf": uf,
        "area_ha": area_ha,
        "area_m2": round(area_ha * 10000, 2) if area_ha is not None else None,
        "fiscal_modules": modules,
        "car_status": status,
        "condition": condition,
        "property_type": property_type,
        "created_at": created,
        "updated_at": updated,
        "geographic_references": refs,
        "sigef_reference": sigef_reference,
        "sigef_reference_state": sigef_reference_state,
        "sigef_reference_others": sigef_reference_others,
        # 200 is not all: when the SIGEF answer was cut short, the count is a floor, not a total.
        "sigef_reference_others_complete": not bool(identity.get("sigef_incomplete", identity.get("sigef_truncated"))),
        "sigef_reference_truncated": bool(identity.get("sigef_truncated")),
        "geometry": car.get("geometry"),
        "bbox": car.get("bbox"),
        "sources": sources,
        "compliance_sources": compliance_sources,
        "source_audit": source_audit,
        "risk": {
            "state": "not_classified",
            "label": "RISCO NÃO CLASSIFICADO",
            "detail": (
                "As fontes de restrição ainda não foram consultadas neste painel rápido. "
                "Fonte não consultada não significa ausência de ocorrência."
            ),
        },
        "source": "SICAR/WFS público + resolvedor auditado de identidade",
        "cached": False,
    }
    # A reference query that did not answer is never cached as an answer, only remembered briefly.
    # 'incomplete' is an answer that cannot change on retry: it is cached like any other.
    if sigef_reference_state == "unavailable":
        _CACHE[_unanswered_key(code)] = (time.monotonic(), out)
        return out
    _CACHE.pop(_unanswered_key(code), None)
    _CACHE[code] = (now, out)
    if len(_CACHE) > 500:
        for key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:100]:
            _CACHE.pop(key, None)
    return out


@app.get("/v1/live/map-panel/{car_code}")
async def map_panel_v45(car_code: str, request: Request, sigef_retry: int = 0):
    if sigef_retry:
        forget_unanswered(car_code)
    try:
        out = await run_sync_with_request_lifecycle(request, _panel_sync, car_code, timeout_seconds=None)
    except RequestDisconnected:
        raise HTTPException(status_code=499, detail='client_disconnected')
    except ManagedOperationTimeout:
        raise HTTPException(status_code=504, detail='map_panel_timeout')
    if not out.get("ok"):
        raise HTTPException(
            status_code=422 if out.get("detail") == "invalid_car_format" else 502,
            detail=out,
        )
    return out


V45_PANEL_UI = r'''
<style id="rxMapPanelV45">
:root{--rx45-panel:410px;--rx45-bg:rgba(7,21,15,.965);--rx45-card:#0b2118;--rx45-card2:#102a1e;--rx45-line:#2a493b;--rx45-text:#eef8f2;--rx45-muted:#9fb5aa;--rx45-green:#63e6a5;--rx45-yellow:#f5c96a;--rx45-red:#ff927a;--rx45-gray:#72847b}
body.rx43-dossier-open #panel{background:transparent!important;pointer-events:none!important}
#rx43SnapshotHost{pointer-events:auto!important}
.rx45-panel-card,.rx45-panel-card *{box-sizing:border-box}
.rx45-panel-card{color:var(--rx45-text);background:var(--rx45-bg);border:1px solid var(--rx45-line);border-radius:20px;padding:14px;box-shadow:0 18px 52px rgba(0,0,0,.38);backdrop-filter:blur(16px);display:grid;gap:11px;overflow:hidden;overflow:clip}
.rx45-top{display:flex;gap:8px;align-items:flex-start}.rx45-title{min-width:0;flex:1}.rx45-eyebrow{font-size:8px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--rx45-green)}.rx45-title h2{font-size:19px;line-height:1.18;margin:4px 0 0;overflow-wrap:anywhere}.rx45-place{font-size:10px;color:var(--rx45-muted);margin-top:5px}.rx45-close{width:34px;height:34px;border-radius:11px;border:1px solid var(--rx45-line);background:#10271d;color:var(--rx45-text);font-size:18px;cursor:pointer;flex:0 0 auto}
.rx45-tools{display:flex;gap:5px;flex:0 0 auto}.rx45-tool{height:30px;border:1px solid var(--rx45-line);border-radius:9px;background:#10271d;color:var(--rx45-text);padding:0 8px;font-size:7px;font-weight:900;cursor:pointer}
.rx45-title h2.rx45-title-code{margin-top:6px;overflow-wrap:normal}.rx45-code-row{margin-top:6px}.rx45-name-note{font-size:8px;color:var(--rx45-muted);line-height:1.45;margin-top:4px}
.rx45-risk{border:1px solid var(--rx45-line);border-left:4px solid var(--rx45-gray);background:var(--rx45-card);border-radius:12px;padding:9px 10px}
.rx45-audit-unavailable{display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid var(--rx45-line);background:var(--rx45-card);border-radius:12px;padding:6px 6px 6px 10px;font-size:10px;line-height:1.4;color:var(--rx45-muted)}.rx45-audit-retry{flex:none;min-height:44px;padding:0 12px;border-radius:10px;border:1px solid var(--rx45-line);background:transparent;color:var(--rx45-text);font:700 10px system-ui,sans-serif;cursor:pointer}.rx45-audit-retry:focus-visible{outline:2px solid var(--rx45-green);outline-offset:2px}.rx45-panel-card[data-audit-available="0"]{border-color:var(--rx45-line)}.rx45-risk strong{display:block;font-size:9px;letter-spacing:.45px}.rx45-risk span{display:block;color:var(--rx45-muted);font-size:8px;line-height:1.45;margin-top:4px}.rx45-risk.clear{border-left-color:var(--rx45-green)}.rx45-risk.diligence{border-left-color:var(--rx45-yellow)}.rx45-risk.probable_impediment{border-left-color:var(--rx45-red)}
.rx45-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.rx45-kpi{background:var(--rx45-card);border:1px solid var(--rx45-line);border-radius:12px;padding:9px;min-height:58px}.rx45-kpi small{display:block;font-size:7px;letter-spacing:.6px;text-transform:uppercase;color:var(--rx45-muted)}.rx45-kpi b{display:block;font-size:11px;line-height:1.3;margin-top:4px}.rx45-kpi.wide{grid-column:1/-1}
.rx45-section{border:1px solid var(--rx45-line);background:var(--rx45-card);border-radius:13px;padding:10px}.rx45-section h4{font-size:9px;margin:0 0 7px;text-transform:uppercase;letter-spacing:.7px}.rx45-row{display:grid;grid-template-columns:minmax(105px,.8fr) minmax(0,1.2fr);gap:8px;padding:6px 0;border-top:1px solid #1d382c;font-size:8px;line-height:1.45}.rx45-row:first-of-type{border-top:0}.rx45-row span{color:var(--rx45-muted)}
.rx45-compliance{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.rx45-check{display:flex;align-items:center;gap:6px;min-width:0;padding:7px;border-radius:9px;background:#0c1d16;font-size:8px}.rx45-dot{width:7px;height:7px;border-radius:50%;background:var(--rx45-gray);flex:0 0 auto}.rx45-dot.clear,.rx45-dot.available,.rx45-dot.validated{background:var(--rx45-green)}.rx45-dot.diligence{background:var(--rx45-yellow)}.rx45-dot.probable_impediment{background:var(--rx45-red)}.rx45-dot.not_consulted,.rx45-dot.on_demand,.rx45-dot.unresolved{background:var(--rx45-gray)}
.rx45-audit-count{font-size:8px;color:var(--rx45-muted);margin-top:8px;line-height:1.4}.rx45-audit-link{border:0;background:transparent;color:var(--rx45-green);font-size:8px;font-weight:800;padding:0;cursor:pointer}
.rx45-reference{padding:7px 8px;border-radius:10px;background:#0c1d16;color:var(--rx45-muted);font-size:8px;line-height:1.45}.rx45-reference strong{color:#d8e9df}
.rx45-actions{position:sticky;bottom:0;z-index:4;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:8px;margin:0 -14px -14px;padding:10px 14px 14px;background:rgba(7,21,15,.97);border-top:1px solid var(--rx45-line);border-radius:0 0 19px 19px}.rx45-actions button{min-height:44px;border-radius:11px;font-size:10px;font-weight:900;letter-spacing:.2px;cursor:pointer;padding:8px 10px;line-height:1.15}.rx45-actions .primary{border:1px solid var(--rx45-green);background:var(--rx45-green);color:#052116}.rx45-actions .rx45-pdf{border:1px solid var(--rx45-line);background:#10271d;color:var(--rx45-text)}.rx45-actions button:focus-visible{outline:2px solid #fff;outline-offset:2px}.rx45-actions .primary[aria-busy="true"]{background:#10271d;color:var(--rx45-green);cursor:progress}
.rx45-check[data-audit-state="NOT_QUERIED"]:not([data-state]){display:none!important}
/* F1B: 44 px touch targets without growing the drawing (the hit area grows, not the button) */.rx45-tool,.rx45-close,.rx45-audit-link{position:relative}.rx45-tool::before,.rx45-close::before{content:"";position:absolute;left:50%;top:50%;width:max(100%,44px);height:max(100%,44px);transform:translate(-50%,-50%)}.rx45-audit-link::before{content:"";position:absolute;left:-6px;right:-6px;top:-8px;bottom:-25px}
@media(min-width:721px){
 body.rx43-dossier-open #map{width:100%!important}
 #panel{position:absolute!important;inset:0!important;width:100%!important;max-width:none!important;height:100%!important;border:0!important;box-shadow:none!important;background:transparent!important;overflow:visible!important;z-index:1100!important;pointer-events:none!important}
 #panel>.phead,#panel>#pbody,#panel>.rx43-deep-label{display:none!important}
 #rx43SnapshotHost{position:absolute!important;top:14px!important;right:14px!important;width:min(var(--rx45-panel),calc(100vw - 28px))!important;max-height:calc(100% - 28px)!important;padding:0!important;background:transparent!important;overflow:auto!important;overscroll-behavior:contain;scrollbar-width:thin}
}
@media(max-width:720px){
 body.rx43-dossier-open{overflow:hidden!important}
 body.rx43-dossier-open .top{display:flex!important}
 body.rx43-dossier-open #map{display:block!important;visibility:visible!important;pointer-events:auto!important;width:100%!important;height:100%!important}
 body.rx43-dossier-open .leaflet-control-container{display:block!important;visibility:visible!important}
 #panel{position:fixed!important;inset:0!important;width:100%!important;height:100%!important;background:transparent!important;pointer-events:none!important;overflow:visible!important}
 #panel>.phead,#panel>#pbody,#panel>.rx43-deep-label{display:none!important}
 #rx43SnapshotHost{position:absolute!important;left:8px!important;right:8px!important;bottom:calc(8px + env(safe-area-inset-bottom))!important;top:auto!important;width:auto!important;max-height:78dvh!important;padding:0!important;background:transparent!important;overflow:auto!important;overscroll-behavior:contain}
 .rx45-panel-card{border-radius:18px;padding:12px}.rx45-actions{margin:0 -12px -12px;padding:10px 12px 12px;border-radius:0 0 17px 17px}.rx45-row{grid-template-columns:1fr;gap:2px}.rx45-tools{gap:4px}.rx45-tool{padding:0 7px}.rx45-compliance{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:390px){.rx45-compliance{grid-template-columns:1fr}.rx45-tools{flex-direction:column}.rx45-tool{height:27px}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=(v,d=2)=>window.rxNum?window.rxNum.num(v,d):'';
 // F1B: the card enrichment (V46) and this panel ask /map-panel ONCE per property: one in-flight request is shared
 // and an answer that came back ok is reused for 2 min. Failures are never remembered (the C3 retries still ask).
 if(!window.rxMapPanelOnce){const inflight=new Map(),memo=new Map(),TTL=120000,copy=x=>x==null?x:JSON.parse(JSON.stringify(x));window.rxMapPanelOnce=function(car){car=String(car||'').trim().toUpperCase();const m=memo.get(car);if(m&&Date.now()-m.at<TTL)return Promise.resolve({ok:true,d:copy(m.d)});let p=inflight.get(car);if(!p){p=fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`).then(async r=>{let d=null;try{d=await r.json()}catch(e){}if(r.ok&&d&&d.ok===true)memo.set(car,{at:Date.now(),d});return {ok:r.ok,d}}).finally(()=>inflight.delete(car));inflight.set(car,p)}return p.then(x=>({ok:x.ok,d:copy(x.d)}))}}
 const ident=p=>{if(window.rxCardIdentityC2)return window.rxCardIdentityC2(p||{});const car=String(p?.car_code||'').trim().toUpperCase();return {named:false,title:car,code:car,place:''}};
 const text=v=>(v===null||v===undefined||v==='')?'Não informado':String(v);
 const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();
 let activeCar='',activeData=null,busy=false;
 function complianceRows(items){return (items||[]).map(x=>`<div class="rx45-check" data-source="${esc(x.id||'')}" data-audit-state="${esc(x.audit_state||'NOT_QUERIED')}"><i class="rx45-dot ${esc(x.state||'not_consulted')}"></i><span>${esc(x.label||'Fonte')}</span></div>`).join('')}
 function panelHtml(p){
   const id=ident(p),named=id.named,car=id.code,C=window.rxCopyCarC2,codeButton=C?C.button(car):esc(car);
   const refs=(p.geographic_references||[]).filter(Boolean);
   // C2b: the SIGEF/INCRA reference (with its share and origin) and the OSM names are separate blocks.
   const sigef=window.rxSigefRefC2?window.rxSigefRefC2.html(p,'panel'):'';
   const ref=`<div class="rx45-sigef-slot" data-rx-sigef-slot>${sigef}</div>${refs.length?`<div class="rx45-reference" data-rx45-osm-reference><strong>Referência geográfica (OpenStreetMap)</strong><br>${refs.map(esc).join(' · ')}<br><span>Contexto cartográfico. Não é denominação do CAR.</span></div>`:''}`;
   const dates=[p.created_at?`Cadastro: ${esc(text(p.created_at))}`:'',p.updated_at?`Atualização: ${esc(text(p.updated_at))}`:''].filter(Boolean).join(' · ')||'Datas não informadas nesta fonte';
   const audit=p.source_audit;
   const auditOk=!!audit&&audit.available===true&&Array.isArray(audit.registry)&&audit.registry.length>0&&Number.isInteger(audit.responded)&&Number.isInteger(audit.total)&&audit.total===audit.registry.length;
   const count=auditOk?'':(p.__rxAuditFailed?'Consulta às fontes oficiais pendente':'Consultando fontes oficiais…');
   const area=window.rxNum?window.rxNum.ha(p.area_ha):'',modules=fmt(p.fiscal_modules,2);
   const kpis=[area?`<div class="rx45-kpi"><small>Área CAR</small><b>${esc(area)}</b></div>`:'',modules?`<div class="rx45-kpi"><small>Módulos fiscais</small><b>${esc(modules)}</b></div>`:'',`<div class="rx45-kpi"><small>Situação CAR</small><b>${esc(text(p.car_status))}</b></div>`].filter(Boolean);
   if(kpis.length%2)kpis[kpis.length-1]=kpis[kpis.length-1].replace('class="rx45-kpi"','class="rx45-kpi wide"');
   const cond=String(p.condition??'').trim();
   const cadastro=`${cond?`<div class="rx45-row"><b>Condição</b><span>${esc(cond)}</span></div>`:''}<div class="rx45-row"><b>Tipo do imóvel</b><span>${esc(text(p.property_type))}</span></div>`;
   const auditBanner=(auditOk||!p.__rxAuditFailed)?'':`<div class="rx45-audit-unavailable" data-rx-audit-pending="1"><span>As fontes oficiais não responderam agora. Os dados do CAR continuam visíveis.</span><button type="button" class="rx45-audit-retry" data-rx45-retry>Consultar de novo</button></div>`;
   return `<div class="rx45-panel-card" data-audit-available="${auditOk?'1':'0'}"${!auditOk&&p.__rxAuditFailed?' data-audit-failed="1"':''} data-car="${esc(car)}" data-rx-copy-scope${named?' data-rx-named="1"':''}><div class="rx45-top"><div class="rx45-title"><div class="rx45-eyebrow">${named?'DENOMINAÇÃO VALIDADA':'CÓDIGO DO CAR'}</div>${named?`<h2>${esc(id.title)}</h2>`:`<h2 class="rx45-title-code">${codeButton}</h2>`}${id.place?`<div class="rx45-place">${esc(id.place)}</div>`:''}${named&&car?`<div class="rx45-code-row">${codeButton}</div>`:''}<div class="rx45-name-note">${named?esc(p.validated_name_source||'Denominação validada por protocolo de identidade.'):'O painel não inventa denominação e não herda nomes OSM/SIGEF.'}</div></div><div class="rx45-tools"><button class="rx45-tool" id="rx45Kml" type="button">KML</button><button class="rx45-tool" id="rx45Png" type="button">PNG</button></div><button class="rx45-close" id="rx45Close" aria-label="Fechar" type="button">×</button></div>${auditBanner}${kpis.length||dates?`<div class="rx45-grid">${kpis.join('')}${dates?`<div class="rx45-kpi wide"><small>Datas</small><b>${dates}</b></div>`:''}</div>`:''}${cadastro.includes('rx45-row')?`<section class="rx45-section"><h4>Cadastro</h4>${cadastro}</section>`:''}<section class="rx45-section"><h4>Conformidade</h4><div class="rx45-compliance">${complianceRows(p.compliance_sources)}</div><div class="rx45-audit-count">${count?esc(count)+' · ':''}<button type="button" class="rx45-audit-link" id="rx45Audit">ver fontes e datas</button></div></section>${ref}<div class="rx45-actions"><button class="primary" id="rx45Full" type="button">VER ANÁLISE COMPLETA</button><button class="rx45-pdf" id="rx45Pdf" type="button">GERAR PDF</button></div></div>`;
 }
 function geometry(){return activeData?.geometry||(window.current||{}).geometry||null}
 function coordsKml(g){
   const ring=(arr)=>arr.map(x=>`${Number(x[0]).toFixed(7)},${Number(x[1]).toFixed(7)},0`).join(' ');
   if(!g)return '';
   if(g.type==='Polygon')return g.coordinates.map((r,i)=>i?`<innerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></innerBoundaryIs>`:`<outerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></outerBoundaryIs>`).join('');
   if(g.type==='MultiPolygon')return g.coordinates.map(poly=>`<Polygon>${poly.map((r,i)=>i?`<innerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></innerBoundaryIs>`:`<outerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></outerBoundaryIs>`).join('')}</Polygon>`).join('');
   return '';
 }
 function downloadKml(){const g=geometry();if(!g)return;const body=coordsKml(g);if(!body)return;const multi=g.type==='MultiPolygon';const name=ident(activeData).title||'imovel';const geom=multi?`<MultiGeometry>${body}</MultiGeometry>`:`<Polygon>${body}</Polygon>`;const xml=`<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>${esc(name)}</name>${geom}</Placemark></Document></kml>`;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([xml],{type:'application/vnd.google-earth.kml+xml'}));a.download=`${activeData?.car_code||'imovel'}.kml`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
 function flatten(g){const out=[];if(!g)return out;const walk=v=>{if(Array.isArray(v)&&typeof v[0]==='number'&&typeof v[1]==='number')out.push(v);else if(Array.isArray(v))v.forEach(walk)};walk(g.coordinates);return out}
 function downloadPng(){const g=geometry(),pts=flatten(g);if(!pts.length)return;const W=1200,H=800,pad=70,x=pts.map(p=>+p[0]),y=pts.map(p=>+p[1]),minX=Math.min(...x),maxX=Math.max(...x),minY=Math.min(...y),maxY=Math.max(...y),sx=(W-2*pad)/Math.max(maxX-minX,1e-9),sy=(H-2*pad)/Math.max(maxY-minY,1e-9),s=Math.min(sx,sy);const cv=document.createElement('canvas');cv.width=W;cv.height=H;const c=cv.getContext('2d');c.fillStyle='#07150f';c.fillRect(0,0,W,H);c.strokeStyle='#63e6a5';c.fillStyle='rgba(99,230,165,.16)';c.lineWidth=5;const drawRing=r=>{c.beginPath();r.forEach((p,i)=>{const px=(p[0]-minX)*s+pad,py=H-pad-(p[1]-minY)*s;i?c.lineTo(px,py):c.moveTo(px,py)});c.closePath();c.fill();c.stroke()};if(g.type==='Polygon')g.coordinates.forEach(drawRing);else if(g.type==='MultiPolygon')g.coordinates.forEach(poly=>poly.forEach(drawRing));c.fillStyle='#eef8f2';c.font='700 28px system-ui';const pid=ident(activeData);c.fillText(pid.title||'',pad,38);c.fillStyle='#9fb5aa';c.font='18px ui-monospace';c.fillText(pid.named?pid.code:(pid.place||''),pad,64);const a=document.createElement('a');a.href=cv.toDataURL('image/png');a.download=`${activeData?.car_code||'imovel'}.png`;a.click()}
 function runFull(){try{if(typeof window.rxProgressiveAnalyze==='function')window.rxProgressiveAnalyze();else if(typeof analyze==='function')analyze()}catch(e){}}
 function bind(){q('#rx45Close')?.addEventListener('click',()=>window.rx43CloseDossier?.());q('#rx45Full')?.addEventListener('click',runFull);q('#rx45Audit')?.addEventListener('click',runFull);q('#rx45Kml')?.addEventListener('click',downloadKml);q('#rx45Png')?.addEventListener('click',downloadPng);q('#rx45Pdf')?.addEventListener('click',()=>{try{window.downloadPDF?.()}catch(e){}})}
 function render(p){const h=q('#rx43SnapshotHost');if(!h||!p)return;h.innerHTML=panelHtml(p);const card=h.querySelector('.rx45-panel-card');if(card)card.__rxSourceAudit=(p.source_audit&&Array.isArray(p.source_audit.registry))?JSON.parse(JSON.stringify(p.source_audit)):null;bind();if(card)document.dispatchEvent(new CustomEvent('rx45:panel-rendered',{detail:{car:card.dataset.car||''}}))}
 async function load(car){if(!car||busy)return;if(car===activeCar&&activeData){activeData=fresher(car,activeData);render(activeData);sigefFollowUp(car,activeData);return}busy=true;try{const {ok:rOk,d}=await window.rxMapPanelOnce(car);if(rOk&&d?.ok&&currentCar()===car){window.rxC3PanelFail?.(car,true);activeCar=car;activeData=d;render(d);sigefFollowUp(car,d)}else window.rxC3PanelFail?.(car,false)}catch(e){window.rxC3PanelFail?.(car,false)}finally{busy=false}}
 // C2b: ONE automatic retry of an unanswered reference while this panel is open. Only the reference
 // slot is repainted, so the sections other modules add to the panel are never wiped.
 const refFields=x=>({sigef_reference:x.sigef_reference??null,sigef_reference_state:x.sigef_reference_state,sigef_reference_others:x.sigef_reference_others,sigef_reference_others_complete:x.sigef_reference_others_complete,sigef_reference_truncated:x.sigef_reference_truncated});
 // C2b: this panel's own copy never overrides a newer answer the card already holds for the same CAR.
 function fresher(car,data){const R=window.rxSigefRefC2,cur=window.current||{};if(!R||String(cur.car_code||'').trim().toUpperCase()!==car||!cur.sigef_reference_state)return data;return R.state(data)==='unanswered'&&R.state(cur)!=='unanswered'?{...data,...refFields(cur)}:data}
 function sigefFollowUp(car,d){const R=window.rxSigefRefC2;if(!R||!R.needsRetry(d))return;const open=()=>currentCar()===car&&document.body.classList.contains('rx43-dossier-open')&&!q('#panel')?.classList.contains('hidden')&&!!q(`#rx43SnapshotHost .rx45-panel-card[data-car="${CSS.escape(car)}"]`);R.scheduleRetry(car,open,fresh=>{if(!open())return;let data=(activeCar===car&&activeData)?activeData:{...d};if(fresh)data={...data,...refFields(fresh)};if(activeCar===car)activeData=data;const slot=q(`#rx43SnapshotHost .rx45-panel-card[data-car="${CSS.escape(car)}"] [data-rx-sigef-slot]`);if(slot)slot.innerHTML=R.html(data,'panel')})}
 function inspect(){const car=currentCar();const h=q('#rx43SnapshotHost');if(!car||!h)return;if(h.querySelector('.rx45-panel-card'))return;load(car)}
 const rxC3Fails={};window.rxC3PanelFail=(car,ok)=>{if(ok){rxC3Fails[car]=0;return}rxC3Fails[car]=(rxC3Fails[car]||0)+1;if(rxC3Fails[car]>=3&&currentCar()===car&&!(activeCar===car&&activeData))render({...(window.current||{}),__rxAuditFailed:true})};if(!window.__rxC3RetryWired){window.__rxC3RetryWired=1;document.addEventListener('click',e=>{const b=e.target.closest?.('[data-rx45-retry]');if(!b)return;e.preventDefault();const car=b.closest('.rx45-panel-card')?.dataset.car||currentCar();if(!car)return;rxC3Fails[car]=0;render({...(window.current||{}),__rxAuditFailed:false});load(car)})}function settle(car){[40,500,1800,9500].forEach(ms=>setTimeout(()=>{if(currentCar()===car)load(car)},ms))}
 function install(){
   const panel=q('#panel');if(panel)panel.setAttribute('aria-label','Painel compacto e auditado do imóvel');
   const base=window.showProperty;
   if(typeof base==='function'&&!window.__rx45ShowWrapped){
     window.__rx45ShowWrapped=true;
     const wrapped=function(p,g){const result=base(p,g);const car=String(p?.car_code||'').trim().toUpperCase();if(car)settle(car);return result};
     window.showProperty=wrapped;
     try{showProperty=wrapped}catch(e){}
   }
   setTimeout(inspect,250);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
<!-- RX_MAP_PANEL_V45 -->
'''

portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", V45_PANEL_UI + "</body>")

print("RX_MAP_PANEL_V45=compact_spec_hierarchy_truthful_gray_sources_kml_png_no_global_observer", flush=True)

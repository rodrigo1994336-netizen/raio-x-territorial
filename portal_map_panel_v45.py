from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException

import portal_v8
from car_resilient import CAR_RE, fetch_car_live_resilient
from property_identity_runtime import resolve_property_identity_sync

app = portal_v8.app
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 600


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


def _panel_sync(car_code: str) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        return {"ok": False, "car_code": code, "detail": "invalid_car_format"}
    now = time.monotonic()
    cached = _CACHE.get(code)
    if cached and now - cached[0] < _TTL_SECONDS:
        out = dict(cached[1])
        out["cached"] = True
        return out

    car = fetch_car_live_resilient(code)
    if not car.get("ok"):
        return {
            "ok": False,
            "car_code": code,
            "detail": car.get("detail") or "CAR não localizado",
            "source": "SICAR/WFS público",
        }

    props = car.get("properties") or {}
    identity = resolve_property_identity_sync(code)
    identity_ok = bool(identity.get("ok"))
    name_eligible = (
        identity_ok
        and bool(identity.get("panel_name_eligible"))
        and bool(identity.get("name"))
    )

    area_ha = _num(_first(props, "area", "num_area", "area_ha"))
    modules = _num(_first(props, "m_fiscal", "mod_fiscal", "modulos_fiscais"))
    municipality = _first(props, "municipio", "nom_munici", "nome_municipio")
    uf = _first(props, "uf", "cod_estado", "sigla_uf") or code[:2]
    status = _first(props, "status_imovel", "ind_status", "status")
    condition = _first(props, "condicao", "des_condic")
    property_type = _first(props, "tipo_imovel", "ind_tipo", "tipo")
    created = _first(props, "dat_criaca", "data_criacao", "dt_criacao")
    updated = _first(props, "dat_atuali", "data_atualizacao", "dt_atualizacao")

    refs = identity.get("geographic_reference_names") or []
    if not refs:
        refs = [
            x.get("name")
            for x in (identity.get("candidates") or [])
            if x.get("name")
        ]
    refs = list(dict.fromkeys(str(x).strip() for x in refs if str(x).strip()))[:3]

    # V45 is a fast panel. Deep restriction sources stay explicitly gray until
    # they are actually queried. "Not consulted" is never converted to green.
    compliance_sources = [
        {"id": "embargo", "label": "Embargos", "state": "not_consulted"},
        {"id": "prodes", "label": "PRODES", "state": "not_consulted"},
        {"id": "indigenous_land", "label": "Terra Indígena", "state": "not_consulted"},
        {"id": "legal_reserve", "label": "Reserva Legal", "state": "not_consulted"},
        {"id": "conservation_unit", "label": "Un. Conservação", "state": "not_consulted"},
        {"id": "registry", "label": "Matrícula", "state": "not_consulted"},
        {"id": "public_forest", "label": "Floresta Pública", "state": "not_consulted"},
        {"id": "snci", "label": "SNCI", "state": "not_consulted"},
    ]

    sources = [
        {
            "id": "car",
            "label": "CAR / SICAR",
            "state": "available",
            "detail": "Perímetro e atributos cadastrais públicos disponíveis.",
        },
        {
            "id": "denomination",
            "label": "Denominação",
            "state": "validated" if name_eligible else "diligence",
            "detail": (
                f"Validada — {identity.get('origin_label') or identity.get('source') or 'fonte pública'}"
                if name_eligible
                else "Nenhuma denominação validada para este CAR. Referências geográficas não são promovidas."
            ),
        },
    ]
    responded_count = 1 + (1 if identity_ok else 0)
    total_source_count = len(sources) + len(compliance_sources)

    out = {
        "ok": True,
        "car_code": code,
        "validated_name": identity.get("name") if name_eligible else None,
        "validated_name_state": "validated" if name_eligible else "unresolved",
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
        "geometry": car.get("geometry"),
        "bbox": car.get("bbox"),
        "sources": sources,
        "compliance_sources": compliance_sources,
        "source_audit": {
            "responded": responded_count,
            "total": total_source_count,
            "deep_sources_requested": False,
        },
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
    _CACHE[code] = (now, out)
    if len(_CACHE) > 500:
        for key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:100]:
            _CACHE.pop(key, None)
    return out


@app.get("/v1/live/map-panel/{car_code}")
async def map_panel_v45(car_code: str):
    out = await asyncio.to_thread(_panel_sync, car_code)
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
.rx45-panel-card{color:var(--rx45-text);background:var(--rx45-bg);border:1px solid var(--rx45-line);border-radius:20px;padding:14px;box-shadow:0 18px 52px rgba(0,0,0,.38);backdrop-filter:blur(16px);display:grid;gap:11px;overflow:hidden}
.rx45-top{display:flex;gap:8px;align-items:flex-start}.rx45-title{min-width:0;flex:1}.rx45-eyebrow{font-size:8px;font-weight:900;letter-spacing:1px;text-transform:uppercase;color:var(--rx45-green)}.rx45-title h2{font-size:19px;line-height:1.18;margin:4px 0 0;overflow-wrap:anywhere}.rx45-place{font-size:10px;color:var(--rx45-muted);margin-top:5px}.rx45-close{width:34px;height:34px;border-radius:11px;border:1px solid var(--rx45-line);background:#10271d;color:var(--rx45-text);font-size:18px;cursor:pointer;flex:0 0 auto}
.rx45-tools{display:flex;gap:5px;flex:0 0 auto}.rx45-tool{height:30px;border:1px solid var(--rx45-line);border-radius:9px;background:#10271d;color:var(--rx45-text);padding:0 8px;font-size:7px;font-weight:900;cursor:pointer}
.rx45-code{font:700 8px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;color:#789789;overflow-wrap:anywhere}.rx45-name-note{font-size:8px;color:var(--rx45-muted);line-height:1.45;margin-top:4px}
.rx45-risk{border:1px solid var(--rx45-line);border-left:4px solid var(--rx45-gray);background:var(--rx45-card);border-radius:12px;padding:9px 10px}.rx45-risk strong{display:block;font-size:9px;letter-spacing:.45px}.rx45-risk span{display:block;color:var(--rx45-muted);font-size:8px;line-height:1.45;margin-top:4px}.rx45-risk.clear{border-left-color:var(--rx45-green)}.rx45-risk.diligence{border-left-color:var(--rx45-yellow)}.rx45-risk.probable_impediment{border-left-color:var(--rx45-red)}
.rx45-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.rx45-kpi{background:var(--rx45-card);border:1px solid var(--rx45-line);border-radius:12px;padding:9px;min-height:58px}.rx45-kpi small{display:block;font-size:7px;letter-spacing:.6px;text-transform:uppercase;color:var(--rx45-muted)}.rx45-kpi b{display:block;font-size:11px;line-height:1.3;margin-top:4px}.rx45-kpi.wide{grid-column:1/-1}
.rx45-section{border:1px solid var(--rx45-line);background:var(--rx45-card);border-radius:13px;padding:10px}.rx45-section h4{font-size:9px;margin:0 0 7px;text-transform:uppercase;letter-spacing:.7px}.rx45-row{display:grid;grid-template-columns:minmax(105px,.8fr) minmax(0,1.2fr);gap:8px;padding:6px 0;border-top:1px solid #1d382c;font-size:8px;line-height:1.45}.rx45-row:first-of-type{border-top:0}.rx45-row span{color:var(--rx45-muted)}
.rx45-compliance{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.rx45-check{display:flex;align-items:center;gap:6px;min-width:0;padding:7px;border-radius:9px;background:#0c1d16;font-size:8px}.rx45-dot{width:7px;height:7px;border-radius:50%;background:var(--rx45-gray);flex:0 0 auto}.rx45-dot.clear,.rx45-dot.available,.rx45-dot.validated{background:var(--rx45-green)}.rx45-dot.diligence{background:var(--rx45-yellow)}.rx45-dot.probable_impediment{background:var(--rx45-red)}.rx45-dot.not_consulted,.rx45-dot.on_demand,.rx45-dot.unresolved{background:var(--rx45-gray)}
.rx45-audit-count{font-size:8px;color:var(--rx45-muted);margin-top:8px;line-height:1.4}.rx45-audit-link{border:0;background:transparent;color:var(--rx45-green);font-size:8px;font-weight:800;padding:0;cursor:pointer}
.rx45-reference{padding:7px 8px;border-radius:10px;background:#0c1d16;color:var(--rx45-muted);font-size:8px;line-height:1.45}.rx45-reference strong{color:#d8e9df}
.rx45-actions{display:grid;gap:5px}.rx45-actions .primary{min-height:42px;border-radius:11px;border:1px solid var(--rx45-green);background:var(--rx45-green);color:#052116;font-size:8px;font-weight:900;cursor:pointer;padding:8px}.rx45-pdf-link{border:0;background:transparent;color:var(--rx45-muted);font-size:8px;text-decoration:underline;text-underline-offset:2px;cursor:pointer;justify-self:center;padding:2px 6px}
@media(min-width:721px){
 body.rx43-dossier-open #map{width:100%!important}
 #panel{position:absolute!important;inset:0!important;width:100%!important;max-width:none!important;height:100%!important;border:0!important;box-shadow:none!important;background:transparent!important;overflow:visible!important;z-index:1100!important;pointer-events:none!important}
 #panel>.phead,#panel>#pbody,#panel>.rx43-deep-label{display:none!important}
 #rx43SnapshotHost{position:absolute!important;top:14px!important;right:14px!important;width:min(var(--rx45-panel),calc(100vw - 28px))!important;max-height:calc(100vh - 28px)!important;padding:0!important;background:transparent!important;overflow:auto!important;scrollbar-width:thin}
}
@media(max-width:720px){
 body.rx43-dossier-open{overflow:hidden!important}
 body.rx43-dossier-open .top{display:flex!important}
 body.rx43-dossier-open #map{display:block!important;visibility:visible!important;pointer-events:auto!important;width:100%!important;height:100%!important}
 body.rx43-dossier-open .leaflet-control-container{display:block!important;visibility:visible!important}
 #panel{position:fixed!important;inset:0!important;width:100%!important;height:100%!important;background:transparent!important;pointer-events:none!important;overflow:visible!important}
 #panel>.phead,#panel>#pbody,#panel>.rx43-deep-label{display:none!important}
 #rx43SnapshotHost{position:absolute!important;left:8px!important;right:8px!important;bottom:calc(8px + env(safe-area-inset-bottom))!important;top:auto!important;width:auto!important;max-height:78dvh!important;padding:0!important;background:transparent!important;overflow:auto!important;overscroll-behavior:contain}
 .rx45-panel-card{border-radius:18px;padding:12px}.rx45-row{grid-template-columns:1fr;gap:2px}.rx45-tools{gap:4px}.rx45-tool{padding:0 7px}.rx45-compliance{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:390px){.rx45-compliance{grid-template-columns:1fr}.rx45-tools{flex-direction:column}.rx45-tool{height:27px}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=(v,d=2)=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d}):'Não informado'};
 const text=v=>(v===null||v===undefined||v==='')?'Não informado':String(v);
 const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();
 let activeCar='',activeData=null,busy=false;
 function complianceRows(items){return (items||[]).map(x=>`<div class="rx45-check" data-source="${esc(x.id||'')}"><i class="rx45-dot ${esc(x.state||'not_consulted')}"></i><span>${esc(x.label||'Fonte')}</span></div>`).join('')}
 function panelHtml(p){
   const named=!!p.validated_name;
   const name=named?p.validated_name:'IMÓVEL RURAL';
   const refs=(p.geographic_references||[]).filter(Boolean);
   const ref=refs.length?`<div class="rx45-reference"><strong>REFERÊNCIA GEOGRÁFICA / CADASTRAL</strong><br>${refs.map(esc).join(' · ')}<br><span>Contexto cartográfico. Não é denominação do CAR.</span></div>`:'';
   const dates=[p.created_at?`Cadastro: ${esc(text(p.created_at))}`:'',p.updated_at?`Atualização: ${esc(text(p.updated_at))}`:''].filter(Boolean).join(' · ')||'Datas não informadas nesta fonte';
   const risk=p.risk||{state:'not_classified',label:'RISCO NÃO CLASSIFICADO',detail:'Fontes aprofundadas ainda não consultadas.'};
   const audit=p.source_audit||{};
   const count=(Number.isFinite(Number(audit.responded))&&Number.isFinite(Number(audit.total)))?`${Number(audit.responded)} de ${Number(audit.total)} fontes responderam`:'Auditoria de fontes disponível na análise completa';
   return `<div class="rx45-panel-card" data-car="${esc(p.car_code)}"><div class="rx45-top"><div class="rx45-title"><div class="rx45-eyebrow">${named?'DENOMINAÇÃO VALIDADA':'IDENTIDADE DO IMÓVEL'}</div><h2>${esc(name)}</h2><div class="rx45-place">${esc(text(p.municipality))}${p.uf?' / '+esc(p.uf):''}</div><div class="rx45-code">CAR ${esc(p.car_code)}</div><div class="rx45-name-note">${named?esc(p.validated_name_source||'Denominação validada por protocolo de identidade.'):'O painel não inventa denominação e não herda nomes OSM/SIGEF.'}</div></div><div class="rx45-tools"><button class="rx45-tool" id="rx45Kml" type="button">KML</button><button class="rx45-tool" id="rx45Png" type="button">PNG</button></div><button class="rx45-close" id="rx45Close" aria-label="Fechar" type="button">×</button></div><div class="rx45-risk ${esc(risk.state||'not_classified')}"><strong>${esc(risk.label||'RISCO NÃO CLASSIFICADO')}</strong><span>${esc(risk.detail||'')}</span></div><div class="rx45-grid"><div class="rx45-kpi"><small>Área CAR</small><b>${fmt(p.area_ha,4)} ha</b></div><div class="rx45-kpi"><small>Área</small><b>${fmt(p.area_m2,2)} m²</b></div><div class="rx45-kpi"><small>Módulos fiscais</small><b>${fmt(p.fiscal_modules,4)}</b></div><div class="rx45-kpi"><small>Situação CAR</small><b>${esc(text(p.car_status))}</b></div><div class="rx45-kpi wide"><small>Datas</small><b>${dates}</b></div></div><section class="rx45-section"><h4>Cadastro</h4><div class="rx45-row"><b>Condição</b><span>${esc(text(p.condition))}</span></div><div class="rx45-row"><b>Tipo do imóvel</b><span>${esc(text(p.property_type))}</span></div></section><section class="rx45-section"><h4>Conformidade</h4><div class="rx45-compliance">${complianceRows(p.compliance_sources)}</div><div class="rx45-audit-count">${esc(count)} · <button type="button" class="rx45-audit-link" id="rx45Audit">ver auditoria</button></div></section>${ref}<div class="rx45-actions"><button class="primary" id="rx45Full" type="button">VER ANÁLISE COMPLETA</button><button class="rx45-pdf-link" id="rx45Pdf" type="button">gerar PDF</button></div></div>`;
 }
 function geometry(){return activeData?.geometry||(window.current||{}).geometry||null}
 function coordsKml(g){
   const ring=(arr)=>arr.map(x=>`${Number(x[0]).toFixed(7)},${Number(x[1]).toFixed(7)},0`).join(' ');
   if(!g)return '';
   if(g.type==='Polygon')return g.coordinates.map((r,i)=>i?`<innerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></innerBoundaryIs>`:`<outerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></outerBoundaryIs>`).join('');
   if(g.type==='MultiPolygon')return g.coordinates.map(poly=>`<Polygon>${poly.map((r,i)=>i?`<innerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></innerBoundaryIs>`:`<outerBoundaryIs><LinearRing><coordinates>${ring(r)}</coordinates></LinearRing></outerBoundaryIs>`).join('')}</Polygon>`).join('');
   return '';
 }
 function downloadKml(){const g=geometry();if(!g)return;const body=coordsKml(g);if(!body)return;const multi=g.type==='MultiPolygon';const name=activeData?.validated_name||activeData?.car_code||'imovel';const geom=multi?`<MultiGeometry>${body}</MultiGeometry>`:`<Polygon>${body}</Polygon>`;const xml=`<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>${esc(name)}</name>${geom}</Placemark></Document></kml>`;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([xml],{type:'application/vnd.google-earth.kml+xml'}));a.download=`${activeData?.car_code||'imovel'}.kml`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
 function flatten(g){const out=[];if(!g)return out;const walk=v=>{if(Array.isArray(v)&&typeof v[0]==='number'&&typeof v[1]==='number')out.push(v);else if(Array.isArray(v))v.forEach(walk)};walk(g.coordinates);return out}
 function downloadPng(){const g=geometry(),pts=flatten(g);if(!pts.length)return;const W=1200,H=800,pad=70,x=pts.map(p=>+p[0]),y=pts.map(p=>+p[1]),minX=Math.min(...x),maxX=Math.max(...x),minY=Math.min(...y),maxY=Math.max(...y),sx=(W-2*pad)/Math.max(maxX-minX,1e-9),sy=(H-2*pad)/Math.max(maxY-minY,1e-9),s=Math.min(sx,sy);const cv=document.createElement('canvas');cv.width=W;cv.height=H;const c=cv.getContext('2d');c.fillStyle='#07150f';c.fillRect(0,0,W,H);c.strokeStyle='#63e6a5';c.fillStyle='rgba(99,230,165,.16)';c.lineWidth=5;const drawRing=r=>{c.beginPath();r.forEach((p,i)=>{const px=(p[0]-minX)*s+pad,py=H-pad-(p[1]-minY)*s;i?c.lineTo(px,py):c.moveTo(px,py)});c.closePath();c.fill();c.stroke()};if(g.type==='Polygon')g.coordinates.forEach(drawRing);else if(g.type==='MultiPolygon')g.coordinates.forEach(poly=>poly.forEach(drawRing));c.fillStyle='#eef8f2';c.font='700 28px system-ui';c.fillText(activeData?.validated_name||'IMÓVEL RURAL',pad,38);c.fillStyle='#9fb5aa';c.font='18px ui-monospace';c.fillText(activeData?.car_code||'',pad,64);const a=document.createElement('a');a.href=cv.toDataURL('image/png');a.download=`${activeData?.car_code||'imovel'}.png`;a.click()}
 function runFull(){try{if(typeof window.rxProgressiveAnalyze==='function')window.rxProgressiveAnalyze();else if(typeof analyze==='function')analyze()}catch(e){}}
 function bind(){q('#rx45Close')?.addEventListener('click',()=>window.rx43CloseDossier?.());q('#rx45Full')?.addEventListener('click',runFull);q('#rx45Audit')?.addEventListener('click',runFull);q('#rx45Kml')?.addEventListener('click',downloadKml);q('#rx45Png')?.addEventListener('click',downloadPng);q('#rx45Pdf')?.addEventListener('click',()=>{try{window.downloadPDF?.()}catch(e){}})}
 function render(p){const h=q('#rx43SnapshotHost');if(!h||!p)return;h.innerHTML=panelHtml(p);bind()}
 async function load(car){if(!car||busy)return;if(car===activeCar&&activeData){render(activeData);return}busy=true;try{const r=await fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`),d=await r.json();if(r.ok&&d?.ok){activeCar=car;activeData=d;render(d)}}catch(e){}finally{busy=false}}
 function inspect(){const car=currentCar();const h=q('#rx43SnapshotHost');if(!car||!h)return;if(h.querySelector('.rx45-panel-card'))return;load(car)}
 function settle(car){[40,500,1800,9500].forEach(ms=>setTimeout(()=>{if(currentCar()===car)load(car)},ms))}
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

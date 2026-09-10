from __future__ import annotations

import html as html_lib
import json
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from shapely.geometry import shape

import portal_v8
import portal_map_panel_v45
from sicar_canonical_snapshot_v48 import query_canonical_property_geometry

app = portal_v8.app
_ORIGINAL_PANEL_SYNC = portal_map_panel_v45._panel_sync


def _bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    if not geometry:
        return None
    try:
        bounds = shape(geometry).bounds
        if len(bounds) != 4:
            return None
        return [float(x) for x in bounds]
    except Exception:
        return None


def _panel_sync_single_geometry(car_code: str) -> dict[str, Any]:
    """Keep WFS only for non-geometric convenience attributes.

    Geometry, snapshot, normalization notice and export eligibility come solely
    from the canonical BigQuery geometry contract.
    """
    base = _ORIGINAL_PANEL_SYNC(car_code)
    if not base.get("ok"):
        return base
    canonical = query_canonical_property_geometry(car_code)
    out = dict(base)
    out["geometry"] = None
    out["bbox"] = None
    out["geometry_available"] = False
    out["geometry_source"] = "basedosdados.br_sfb_sicar.area_imovel · snapshot canônico por UF"
    out["geometry_snapshot"] = canonical.get("snapshot")
    out["geometry_snapshot_label"] = canonical.get("snapshot_label")
    out["geometry_state"] = canonical.get("state")
    out["geometry_normalization"] = canonical.get("geometry_normalization")
    out["geometry_notice"] = None

    if canonical.get("ok") and canonical.get("geometry"):
        geometry = canonical["geometry"]
        norm = canonical.get("geometry_normalization") or {}
        out["geometry"] = geometry
        out["bbox"] = _bbox(geometry)
        out["geometry_available"] = True
        out["geometry_state"] = canonical.get("state") or "canonical"
        out["geometry_notice"] = norm.get("user_notice")
    else:
        out["geometry_user_message"] = canonical.get("user_message")
        out["geometry_detail"] = canonical.get("detail")

    out["source"] = (
        "Atributos cadastrais auxiliares: SICAR/WFS público; geometria: "
        "Base dos Dados/SICAR no snapshot canônico normalizado."
    )
    return out


portal_map_panel_v45._panel_sync = _panel_sync_single_geometry


def _kml_coordinates(ring: list[list[float]]) -> str:
    return " ".join(f"{float(p[0]):.7f},{float(p[1]):.7f},0" for p in ring if len(p) >= 2)


def _polygon_kml(coords: list[Any]) -> str:
    chunks: list[str] = []
    for i, ring in enumerate(coords or []):
        tag = "outerBoundaryIs" if i == 0 else "innerBoundaryIs"
        chunks.append(
            f"<{tag}><LinearRing><coordinates>{_kml_coordinates(ring)}</coordinates>"
            f"</LinearRing></{tag}>"
        )
    return "".join(chunks)


def _geometry_kml(geometry: dict[str, Any]) -> str:
    typ = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if typ == "Polygon":
        return f"<Polygon>{_polygon_kml(coords)}</Polygon>"
    if typ == "MultiPolygon":
        return "<MultiGeometry>" + "".join(
            f"<Polygon>{_polygon_kml(poly)}</Polygon>" for poly in coords
        ) + "</MultiGeometry>"
    raise RuntimeError(f"canonical_export_unexpected_geometry_type:{typ}")


@app.get("/v1/exports/canonical-property/{car_code}/kml")
async def canonical_property_kml_v48(car_code: str):
    data = query_canonical_property_geometry(car_code)
    if not data.get("ok") or not data.get("geometry_available") or not data.get("geometry"):
        raise HTTPException(status_code=422, detail={
            "state": data.get("state"),
            "message": data.get("user_message") or "Geometria canônica indisponível para exportação.",
            "snapshot": data.get("snapshot"),
        })
    geometry = data["geometry"]
    body = _geometry_kml(geometry)
    car = str(data.get("car_code") or car_code).strip().upper()
    notice = str((data.get("geometry_normalization") or {}).get("user_notice") or "")
    description = f"<description>{html_lib.escape(notice)}</description>" if notice else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
        f"<name>{html_lib.escape(car)}</name>{description}{body}"
        '</Placemark></Document></kml>'
    )
    return Response(
        content=xml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={
            "Content-Disposition": f'attachment; filename="{car}.kml"',
            "X-RX-Geometry-Normalization": str((data.get("geometry_normalization") or {}).get("normalization_version") or ""),
            "X-RX-Geometry-Snapshot": str(data.get("snapshot") or ""),
        },
    )


# Patch the already-loaded V45 panel renderer. Exports are not merely disabled:
# they are absent when canonical geometry is absent, and the normalization notice
# appears only on properties that actually required dimensional normalization.
html = portal_v8.PORTAL_HTML
_tools = '<div class="rx45-tools"><button class="rx45-tool" id="rx45Kml" type="button">KML</button><button class="rx45-tool" id="rx45Png" type="button">PNG</button></div>'
_tools_new = '${p.geometry_available===false?\'\':`<div class="rx45-tools"><button class="rx45-tool" id="rx45Kml" type="button">KML</button><button class="rx45-tool" id="rx45Png" type="button">PNG</button></div>`}'
if html.count(_tools) != 1:
    raise RuntimeError("v48_geometry_truth_v45_tools_injection_missing")
html = html.replace(_tools, _tools_new, 1)

_risk = '<div class="rx45-risk ${esc(risk.state||\'not_classified\')}">'
_risk_new = '${p.geometry_notice?`<div class="rx-v48-geometry-notice">${esc(p.geometry_notice)}</div>`:\'\'}<div class="rx45-risk ${esc(risk.state||\'not_classified\')}">'
if html.count(_risk) != 1:
    raise RuntimeError("v48_geometry_truth_v45_notice_injection_missing")
html = html.replace(_risk, _risk_new, 1)

# V47 integrity body already exposes the canonical snapshot. Put the geometry
# declaration immediately beside that provenance, only when the backend says the
# normalization was actually applied.
_audit = '${rxV48AuditPanel()}'
_audit_new = '${d?.geometry_normalization?.user_notice?`<div class="rx-v48-geometry-notice">${esc(d.geometry_normalization.user_notice)}</div>`:\'\'}${rxV48AuditPanel()}'
if html.count(_audit) < 1:
    raise RuntimeError("v48_geometry_truth_integrity_notice_injection_missing")
html = html.replace(_audit, _audit_new, 1)

_ui = r'''
<style id="rxGeometryTruthV48Style">
.rx-v48-geometry-notice{padding:8px 10px;border:1px solid #5b4925;border-left:4px solid var(--rx45-yellow,#f5c96a);border-radius:10px;background:#211b0f;color:#f3e6bd;font-size:8px;line-height:1.45;font-weight:750}
</style>
<script id="rxGeometryTruthV48Client">
(()=>{
 const EMPTY={type:'FeatureCollection',features:[]};
 const carOf=p=>String(p?.car_code||p?.cod_imovel||p?.id_imovel||'').trim().toUpperCase();
 async function canonical(car){const r=await fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`,{cache:'no-store'});const d=await r.json();return {r,d}}
 function installCanonicalSelection(attempt=0){
   const base=window.rxV46SelectProperty;
   if(typeof base!=='function'){if(attempt<80)setTimeout(()=>installCanonicalSelection(attempt+1),100);return}
   if(base.__rxCanonicalGeometry)return;
   const wrapped=async function(p,g,latlng){
     const car=carOf(p);if(!car)return base(p,EMPTY,latlng);
     try{
       const {r,d}=await canonical(car);
       if(!r.ok||!d?.ok){
         const msg=d?.detail?.message||d?.geometry_user_message||d?.detail||'Geometria canônica indisponível.';
         window.rxUiStatus?.(String(msg),'bad',5200);
         return base({...p,...(d||{}),geometry:null,geometry_available:false},EMPTY,latlng);
       }
       const cg=d.geometry_available&&d.geometry?d.geometry:EMPTY;
       return base({...p,...d,geometry:d.geometry||null},cg,latlng);
     }catch(e){window.rxUiStatus?.('Geometria canônica indisponível. O perímetro não foi presumido.','bad',5200);return base({...p,geometry:null,geometry_available:false},EMPTY,latlng)}
   };
   wrapped.__rxCanonicalGeometry=true;
   window.rxV46SelectProperty=wrapped;
 }
 // V46's anchor had a legacy WFS-backed export URL. Capture that action before
 // its own listener and route KML to the same canonical geometry used on screen.
 document.addEventListener('click',e=>{
   const b=e.target.closest?.('[data-rx46-action="kml"]');if(!b)return;
   e.preventDefault();e.stopImmediatePropagation();
   const cur=window.current||{},car=carOf(cur);
   if(!car)return;
   if(cur.geometry_available===false){window.rxUiStatus?.(cur.geometry_user_message||'Este imóvel não possui geometria canônica exportável.','bad',5200);return}
   window.open(`/v1/exports/canonical-property/${encodeURIComponent(car)}/kml`,'_blank','noopener,noreferrer');
 },true);
 const scrub=()=>document.querySelectorAll('.rx46-card').forEach(card=>{const car=String(card.dataset.car||'').toUpperCase(),cur=window.current||{};if(car&&car===carOf(cur)&&cur.geometry_available===false){card.querySelector('[data-rx46-action="kml"]')?.remove()}});
 new MutationObserver(scrub).observe(document.documentElement,{subtree:true,childList:true});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>installCanonicalSelection());else installCanonicalSelection();
})();
</script>
<!-- RX_GEOMETRY_TRUTH_V48 -->
'''

html = html.replace("</body>", _ui + "</body>", 1)
portal_v8.PORTAL_HTML = html

print("RX_GEOMETRY_TRUTH_V48=canonical_analysis_map_kml_png_contract", flush=True)

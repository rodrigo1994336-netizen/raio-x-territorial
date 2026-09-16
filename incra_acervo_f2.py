"""F2: certificação SIGEF e SNCI pela base oficial do INCRA (Acervo Fundiário, WFS i3Geo).

Why this module exists: the report and the map card used to read only the IBAMA/PAMGIA
mirror layer ``lim_imovel_sigef_publico_a/10`` (public land, frozen in April 2022) and told
the client "SIGEF consultado · 0 parcela(s)" for properties whose private SIGEF parcel covers
~100% of the CAR. A mirror can confirm presence, never absence.

This module asks the source itself, per CAR polygon, for the four official layers of the UF:

    imoveiscertificados_privado_<uf>   SNCI (certificações 2004-2015, base fechada)
    imoveiscertificados_publico_<uf>   SNCI
    certificada_sigef_particular_<uf>  SIGEF (atualização diária)
    certificada_sigef_publico_<uf>     SIGEF

Rules (measured live on 13/09/2026, see tests/fixtures/f2_incra_sigef_snci/README.md):
- WFS 1.0.0 GetFeature, outputFormat=GML2, bbox only (JSON output and OGC filters are refused
  with HTTP 200 + ServiceExceptionReport; GetCapabilities is not needed and its XML is invalid).
- HTTP 200 with an empty body, an exception report, invalid XML, a root that is not a
  FeatureCollection, or features outside the requested box are NOT answers.
- count == maxFeatures is not "all": the box is split in four (two levels) and, if still cut,
  the layer is pending.
- Absence ("not_found") needs every layer of the family to have answered completely.
  Presence ("found") needs only the layer that returned the parcel.
- Only thin border slivers are ignored (small for the CAR, small for the parcel, < 5 m wide):
  small certified parcels inside a large CAR are presence, never "Não há".
- A CAR inside a much larger certified perimeter never gets the "CERTIFICADO" seal.
- Personal data never leaves the parser: rt, art, cod_profissional_credenciado, num_processo,
  registro_matricula and registro_data are not read (white list of fields).

Public API:
    enabled()                               RX_INCRA_ACERVO_ENABLED (default on)
    query_incra_acervo(geometry, uf, ...)   consultation, explicit states per family
    query_for_result(result, ...)           same, taking the report ``result['car']``
    acervo_for_report(result, ...)          the report's answer: reuses a complete one, asks otherwise (never raises)
    land_payload(acervo)                    fields for the client, pt-BR, found/not_found/pending
    apply_to_report_payload(payload, acervo) rewrites every SIGEF/SNCI line of the report payload
    identity_candidates(geometry, bbox, uf) C2b-compatible reference candidates for the card
    summary_item(acervo)                    small dict for the legacy analysis summary
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from external_process_lifecycle import in_current_scope

try:  # defusedxml when installed; otherwise any DTD/entity is refused before parsing (see parse_wfs_gml)
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # pragma: no cover - depends on the environment
    _xml_fromstring = ET.fromstring
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

I3GEO_URL = "https://acervofundiario.incra.gov.br/i3geo/ogc.php"
ENV_FLAG = "RX_INCRA_ACERVO_ENABLED"
SOURCE_LABEL = "INCRA — Acervo Fundiário (SIGEF e SNCI)"
ORIGIN_LABEL = {"sigef": "Acervo Fundiário do INCRA (SIGEF)", "snci": "Acervo Fundiário do INCRA (SNCI)"}
REFERENCE_KIND = {"sigef": "SIGEF_CADASTRAL", "snci": "SNCI_CADASTRAL"}

UFS = frozenset("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split())
# (family, layer key, theme prefix)
LAYERS = (
    ("snci", "privado", "imoveiscertificados_privado"),
    ("snci", "publico", "imoveiscertificados_publico"),
    ("sigef", "particular", "certificada_sigef_particular"),
    ("sigef", "publico", "certificada_sigef_publico"),
)
FAMILIES = ("sigef", "snci")

MAX_FEATURES = 500          # server cap not reached by a 5.000 request that returned 823 features
BBOX_PAD_DEG = 0.0005
MAX_SPLIT_DEPTH = 2
BBOX_TOLERANCE_DEG = 0.01   # a feature outside the requested box (+tolerance) means a wrong answer
# Timeouts from the live measurement (p50 ~450 ms, worst warm 774 ms, 4 layers in parallel 1,5 s cold).
CONNECT_TIMEOUT_S = 5
MAX_TIME_S = 8
HARD_TIMEOUT_S = 10
RETRIES = 1
MAX_BODY_BYTES = 30_000_000

# A neighbour's border sliver is ignored only when it is small for the CAR AND small for the parcel AND thin.
# A parcel lying inside a large CAR (small for the CAR, whole for itself) or a compact overlap is never dropped:
# otherwise a large property with many small certified parcels would read "Não há certificação".
# Measured on the 10 recorded border intersections (tests/fixtures/f2_incra_sigef_snci): mean width <= 0,90 m,
# <= 0,06% of the CAR and <= 0,04% of the parcel; the real parcels are 236-646 m wide.
MIN_SHARE = 0.01            # border candidate: below 1% of the CAR ...
BORDER_PARCEL_SHARE = 0.5   # ... and below half of the parcel ...
SLIVER_WIDTH_M = 5.0        # ... and mean width (2 x area / perimeter) below 5 m
COVERS_SHARE = 0.90         # at least 90% of the CAR: "cobre o imóvel"
WITHIN_PARCEL_SHARE = 0.5   # the CAR occupies less than half of the certified perimeter: never the "CERTIFICADO" seal
WITHIN_STATUS = "EM PERÍMETRO CERTIFICADO"
UA = "Raio-X-Territorial/f2-incra-acervo"

BRT = timezone(timedelta(hours=-3))
_GML = "{http://www.opengis.net/gml}"
_OFF_VALUES = {"0", "false", "off", "no", "nao", "não", "disabled", "desligado"}

# White list: nothing else is ever read from a feature (LGPD).
_FIELDS = {
    "snci": ("num_certificacao", "data_certificacao", "qtd_area_peca_tecnica", "cod_imovel_rural", "nome_imovel", "sr"),
    "sigef": ("parcela_codigo", "codigo_imovel", "status", "situacao_informada", "data_aprovacao", "nome_area", "codigo_municipio"),
}
_GENERIC_NAMES = {
    "IMOVEL RURAL", "IMÓVEL RURAL", "AREA CERTIFICADA SIGEF", "ÁREA CERTIFICADA SIGEF",
    "SEM DENOMINACAO", "SEM DENOMINAÇÃO", "FAZENDA", "SITIO", "SÍTIO",
}


# ---------------------------------------------------------------------------------------------
# switch and transport
# ---------------------------------------------------------------------------------------------

def enabled(env: dict[str, str] | None = None) -> bool:
    """RX_INCRA_ACERVO_ENABLED, default on. Off never becomes zero: every family is pending."""
    value = (os.environ if env is None else env).get(ENV_FLAG)
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip().casefold() not in _OFF_VALUES


def getfeature_url(theme: str, bbox: tuple[float, float, float, float], max_features: int = MAX_FEATURES) -> str:
    params = {
        "tema": theme, "service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": theme,
        "outputFormat": "GML2", "srsName": "EPSG:4326", "maxFeatures": str(int(max_features)),
        "bbox": ",".join(f"{float(v):.6f}" for v in bbox),
    }
    return I3GEO_URL + "?" + urlencode(params)


def curl_fetch(url: str, *, cancel_event=None) -> dict[str, Any]:
    """HTTPS with certificate verification (no -k), managed and cancellable like every portal curl."""
    import br_bridge
    from external_process_lifecycle import ManagedProcessCancelled, run_managed_process

    args = [
        "curl", "-sS", "-L", "--fail", "--proto", "=https", "--proto-redir", "=https",
        "--connect-timeout", str(CONNECT_TIMEOUT_S), "--max-time", str(MAX_TIME_S),
        "--max-filesize", str(MAX_BODY_BYTES), "-A", UA, url,
    ]
    try:
        # Pela Ponte no Brasil quando configurada (br_bridge); sem ela, a mesma chamada de antes.
        proc = br_bridge.run_curl(args, timeout_seconds=HARD_TIMEOUT_S, cancel_event=cancel_event,
                                  runner=run_managed_process)
    except ManagedProcessCancelled:
        return {"ok": False, "cancelled": True, "detail": "request_cancelled", "body": b""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"process_timeout_after_{HARD_TIMEOUT_S}s", "body": b""}
    except OSError as exc:
        return {"ok": False, "detail": f"curl_unavailable:{type(exc).__name__}", "body": b""}
    if proc.returncode:
        err = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:200]
        return {"ok": False, "detail": f"curl_exit_{proc.returncode}:{err}", "body": proc.stdout or b""}
    return {"ok": True, "body": proc.stdout or b""}


# ---------------------------------------------------------------------------------------------
# parsing (pure)
# ---------------------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ring(el) -> list[tuple[float, float]]:
    coords = el.find(f".//{_GML}coordinates")
    if coords is None or not (coords.text or "").strip():
        raise ValueError("ring_without_coordinates")
    pts = []
    for pair in coords.text.split():
        xy = pair.split(",")
        x, y = float(xy[0]), float(xy[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("non_finite_coordinate")
        pts.append((x, y))
    if len(pts) < 4:
        raise ValueError("ring_too_short")
    return pts


def _gml_geometry(el):
    from shapely.geometry import MultiPolygon, Polygon

    polys = []
    for poly in el.iter(f"{_GML}Polygon"):
        outer = poly.find(f"{_GML}outerBoundaryIs")
        if outer is None:
            raise ValueError("polygon_without_outer_ring")
        polys.append(Polygon(_ring(outer), [_ring(i) for i in poly.findall(f"{_GML}innerBoundaryIs")]))
    if not polys:
        raise ValueError("no_polygon")
    geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return _valid(geom)


def _valid(geom):
    if geom.is_valid:
        return geom
    try:
        from shapely import make_valid
    except ImportError:  # shapely < 2
        from shapely.validation import make_valid
    return make_valid(geom)


def parse_wfs_gml(body: bytes | None, family: str, *, max_features: int = MAX_FEATURES,
                  bbox: tuple[float, float, float, float] | None = None) -> dict[str, Any]:
    """One WFS answer -> {'answered': bool, 'features': [(props, geom|None)], 'truncated', 'detail'}.

    Trying is not answering: an empty body, an exception report, invalid XML or a root that is
    not a FeatureCollection is a failure, never "0 features".
    """
    if not body:
        return {"answered": False, "detail": "empty_body", "features": []}
    head = body[:4096].decode("utf-8", "ignore")
    if "ExceptionReport" in head or "ServiceException" in head:
        text = re.sub(r"\s+", " ", body.decode("utf-8", "ignore"))
        return {"answered": False, "detail": "service_exception:" + text[-160:], "features": []}
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        # No DTD, no entity expansion (XXE / billion laughs): a WFS answer never needs one.
        return {"answered": False, "detail": "xml_doctype_refused", "features": []}
    try:
        root = _xml_fromstring(body)
    except Exception as exc:
        return {"answered": False, "detail": f"xml_invalid:{type(exc).__name__}", "features": []}
    if _local(root.tag) != "FeatureCollection":
        return {"answered": False, "detail": f"not_feature_collection:{_local(root.tag)}", "features": []}
    fields = _FIELDS[family]
    features: list[tuple[dict[str, str], Any]] = []
    unmeasurable = 0
    for member in root.findall(f"{_GML}featureMember"):
        nodes = list(member)
        if not nodes:
            unmeasurable += 1
            continue
        props: dict[str, str] = {}
        geom = None
        geom_el = None
        for child in nodes[0]:
            tag = _local(child.tag)
            if tag == "msGeometry":
                geom_el = child
            elif tag in fields:
                props[tag] = (child.text or "").strip()
        try:
            if geom_el is None:
                raise ValueError("no_geometry")
            geom = _gml_geometry(geom_el)
            if geom.is_empty:
                raise ValueError("empty_geometry")
        except Exception:
            geom = None
            unmeasurable += 1
        features.append((props, geom))
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        t = BBOX_TOLERANCE_DEG
        for _props, geom in features:
            if geom is None:
                continue
            gx0, gy0, gx1, gy1 = geom.bounds
            if gx1 < x0 - t or gx0 > x1 + t or gy1 < y0 - t or gy0 > y1 + t:
                # Axis order swapped or the server ignored the box: the whole answer is unreliable.
                return {"answered": False, "detail": "feature_outside_requested_bbox", "features": []}
    return {
        "answered": True, "features": features, "count": len(features),
        "truncated": len(features) >= int(max_features), "unmeasurable": unmeasurable,
    }


# ---------------------------------------------------------------------------------------------
# consultation
# ---------------------------------------------------------------------------------------------

def _split(bbox):
    x0, y0, x1, y1 = bbox
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return ((x0, y0, mx, my), (mx, y0, x1, my), (x0, my, mx, y1), (mx, my, x1, y1))


def _feature_key(family: str, props: dict[str, str], geom) -> tuple:
    code = props.get("num_certificacao") if family == "snci" else props.get("parcela_codigo")
    return (code or "", geom.wkb if geom is not None else b"")


def fetch_layer(theme: str, family: str, bbox, *, fetch: Callable[..., dict[str, Any]] | None = None,
                cancel_event=None, depth: int = 0, max_features: int = MAX_FEATURES) -> dict[str, Any]:
    """One official layer for one box: retried once when it did not answer, split when cut short."""
    fetch = fetch or curl_fetch
    url = getfeature_url(theme, bbox, max_features)
    last: dict[str, Any] = {"answered": False, "detail": "not_requested", "features": []}
    requests = 0
    for attempt in range(RETRIES + 1):
        if cancel_event is not None and cancel_event.is_set():
            return {"answered": False, "cancelled": True, "detail": "request_cancelled", "features": [], "requests": requests}
        raw = fetch(url, cancel_event=cancel_event)
        requests += 1
        if raw.get("cancelled"):
            return {"answered": False, "cancelled": True, "detail": "request_cancelled", "features": [], "requests": requests}
        if not raw.get("ok"):
            last = {"answered": False, "detail": str(raw.get("detail") or "transport_failed")[:200], "features": []}
        else:
            last = parse_wfs_gml(raw.get("body"), family, max_features=max_features, bbox=bbox)
            if last.get("answered"):
                break
        if attempt < RETRIES:
            time.sleep(0.3)
    last["requests"] = requests
    if not last.get("answered") or not last.get("truncated"):
        return last
    # 200 is not all: split the box, merge the quadrants without duplicates.
    if depth >= MAX_SPLIT_DEPTH:
        return {"answered": False, "detail": "truncated_after_split", "features": [], "requests": requests}
    merged: dict[tuple, tuple[dict[str, str], Any]] = {}
    unmeasurable = 0
    for quad in _split(bbox):
        part = fetch_layer(theme, family, quad, fetch=fetch, cancel_event=cancel_event, depth=depth + 1, max_features=max_features)
        requests += int(part.get("requests") or 0)
        if not part.get("answered"):
            part["requests"] = requests
            return part
        unmeasurable += int(part.get("unmeasurable") or 0)
        for props, geom in part["features"]:
            merged.setdefault(_feature_key(family, props, geom), (props, geom))
    features = list(merged.values())
    return {"answered": True, "features": features, "count": len(features), "truncated": False,
            "unmeasurable": unmeasurable, "split": True, "requests": requests}


def _geod():
    from pyproj import Geod

    return Geod(ellps="GRS80")


def area_ha(geom, geod=None) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    geod = geod or _geod()
    return abs(geod.geometry_area_perimeter(geom)[0]) / 10000.0


def floor_share(value: float) -> float:
    """0..1 share floored at 6 decimals with integer arithmetic: below 100% never reads as 100%."""
    v = float(value)
    if not math.isfinite(v):
        v = 0.0
    v = min(max(v, 0.0), 1.0)
    return (int(round(v * 1_000_000_000)) // 1000) / 1_000_000


def _car_shape(car_geometry):
    from shapely.geometry import shape

    geom = _valid(shape(car_geometry))
    if geom.is_empty:
        raise ValueError("empty_car_geometry")
    return geom


def _polygonal(geom):
    """Only the areal part of an intersection (lines and points of a touching border have no area)."""
    from shapely.geometry import MultiPolygon, Polygon

    if geom is None or geom.is_empty:
        return Polygon()
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = []
    for part in getattr(geom, "geoms", ()):
        part = _polygonal(part)
        if not part.is_empty:
            polys.extend(part.geoms if part.geom_type == "MultiPolygon" else [part])
    return MultiPolygon(polys) if polys else Polygon()


def is_border_sliver(car_share: float, parcel_share: float, mean_width_m: float) -> bool:
    """A neighbour's border: small for the CAR, small for the parcel and thin. Anything else is an overlap."""
    return car_share < MIN_SHARE and parcel_share < BORDER_PARCEL_SHARE and mean_width_m < SLIVER_WIDTH_M


def measure_features(car, features, family: str, layer: str, geod=None) -> dict[str, Any]:
    """Real intersection with the CAR (geodesic GRS80). Only thin border slivers are dropped (is_border_sliver)."""
    geod = geod or _geod()
    car_ha = area_ha(car, geod)
    rows, failed, borders = [], 0, 0
    for props, geom in features:
        if geom is None:
            continue
        try:
            if not car.intersects(geom):
                continue
            inter = _polygonal(car.intersection(geom))
            if inter.is_empty:
                continue  # touching along a line or a point: no area in common
            area_m2, perimeter_m = geod.geometry_area_perimeter(inter)
            area_m2 = abs(area_m2)
            if area_m2 <= 0:
                continue
            inter_ha = area_m2 / 10000.0
            mean_width_m = 2.0 * area_m2 / perimeter_m if perimeter_m > 0 else 0.0
            parcel_ha = area_ha(geom, geod)
            if car_ha <= 0:
                raise ValueError("car_without_area")
            car_ratio = inter_ha / car_ha
            # 100% only when the CAR lies entirely inside the parcel; otherwise strictly below 100%.
            if car_ratio >= 1.0 and not car.difference(geom).is_empty:
                car_ratio = 0.999999
            parcel_ratio = inter_ha / parcel_ha if parcel_ha > 0 else 0.0
        except Exception:
            failed += 1
            continue
        share, parcel_share = floor_share(car_ratio), floor_share(parcel_ratio)
        if is_border_sliver(car_ratio, parcel_ratio, mean_width_m):
            borders += 1
            continue
        rows.append({
            "family": family, "layer": layer, "props": props, "geometry": geom,
            "car_share": share, "parcel_share": parcel_share,
            "intersection_ha": round(inter_ha, 4), "parcel_area_ha": round(parcel_ha, 4),
            "mean_width_m": round(mean_width_m, 2),
        })
    rows.sort(key=lambda r: (r["car_share"], r["parcel_share"]), reverse=True)
    return {"rows": rows, "failed": failed, "borders": borders, "car_area_ha": round(car_ha, 4)}


def _union_share(car, rows, geod=None) -> float:
    if not rows:
        return 0.0
    from shapely.ops import unary_union

    geod = geod or _geod()
    try:
        union = unary_union([r["geometry"] for r in rows])
        inter = car.intersection(union)
        ratio = area_ha(inter, geod) / max(area_ha(car, geod), 1e-12)
        if ratio >= 1.0 and not car.difference(union).is_empty:
            ratio = 0.999999
        return floor_share(ratio)
    except Exception:
        return max(r["car_share"] for r in rows)


def _pending_family(detail: str) -> dict[str, Any]:
    return {"state": "pending", "rows": [], "union_share": None, "layers": {}, "detail": detail}


def query_incra_acervo(car_geometry: dict[str, Any] | None, uf: str | None, *, fetch=None, cancel_event=None,
                       env: dict[str, str] | None = None, parallel: bool = True, now: datetime | None = None) -> dict[str, Any]:
    """Official SIGEF and SNCI for one CAR polygon. Each family: found | not_found | pending."""
    checked = (now or datetime.now(timezone.utc)).astimezone(BRT)
    base = {"source": SOURCE_LABEL, "uf": str(uf or "").upper(), "checked_at": checked.isoformat(timespec="seconds"),
            "enabled": enabled(env)}
    if not base["enabled"]:
        return {**base, "ok": False, "detail": "incra_acervo_disabled",
                "sigef": _pending_family("disabled"), "snci": _pending_family("disabled")}
    code = base["uf"]
    if code not in UFS:
        return {**base, "ok": False, "detail": "invalid_uf",
                "sigef": _pending_family("invalid_uf"), "snci": _pending_family("invalid_uf")}
    try:
        car = _car_shape(car_geometry)
    except Exception as exc:
        detail = f"car_geometry:{type(exc).__name__}"
        return {**base, "ok": False, "detail": detail, "sigef": _pending_family(detail), "snci": _pending_family(detail)}
    x0, y0, x1, y1 = car.bounds
    bbox = (x0 - BBOX_PAD_DEG, y0 - BBOX_PAD_DEG, x1 + BBOX_PAD_DEG, y1 + BBOX_PAD_DEG)

    def one(layer):
        family, key, prefix = layer
        try:
            return layer, fetch_layer(f"{prefix}_{code.lower()}", family, bbox, fetch=fetch, cancel_event=cancel_event)
        except Exception as exc:  # trying is not answering: an unexpected failure is a layer that did not answer
            return layer, {"answered": False, "detail": f"exception:{type(exc).__name__}", "features": []}

    if parallel:
        with ThreadPoolExecutor(max_workers=len(LAYERS)) as pool:
            answers = list(pool.map(in_current_scope(one), LAYERS))
    else:
        answers = [one(layer) for layer in LAYERS]
    if cancel_event is not None and cancel_event.is_set():
        return {**base, "ok": False, "cancelled": True, "detail": "request_cancelled",
                "sigef": _pending_family("cancelled"), "snci": _pending_family("cancelled")}

    geod = _geod()
    out = {**base, "ok": True, "bbox": [round(v, 6) for v in bbox], "car_area_ha": round(area_ha(car, geod), 4)}
    for family in FAMILIES:
        rows, layers, complete = [], {}, True
        for (fam, key, prefix), answer in answers:
            if fam != family:
                continue
            info = {"theme": f"{prefix}_{code.lower()}", "answered": bool(answer.get("answered")),
                    "requests": int(answer.get("requests") or 0)}
            if not answer.get("answered"):
                info["detail"] = answer.get("detail")
                complete = False
            else:
                measured = measure_features(car, answer["features"], family, key, geod)
                info.update(count_in_box=int(answer.get("count") or 0), rows=len(measured["rows"]),
                            border_slivers=measured["borders"],
                            unmeasurable=int(answer.get("unmeasurable") or 0) + measured["failed"],
                            split=bool(answer.get("split")))
                if info["unmeasurable"]:
                    complete = False
                rows.extend(measured["rows"])
            layers[key] = info
        rows.sort(key=lambda r: (r["car_share"], r["parcel_share"]), reverse=True)
        if rows:
            state = "found"
        elif complete:
            state = "not_found"
        else:
            state = "pending"
        out[family] = {"state": state, "complete": complete, "rows": rows, "layers": layers,
                       "union_share": _union_share(car, rows, geod) if rows else (0.0 if state == "not_found" else None)}
        if not complete:
            # Asked, not concluded: never read as "not asked" (pending_reason).
            out[family]["detail"] = "layers_incomplete:" + ",".join(
                f"{k}:{v.get('detail') or 'unmeasurable'}" for k, v in layers.items() if not v.get("answered") or v.get("unmeasurable"))[:200]
    out["ok"] = any(out[f]["state"] != "pending" for f in FAMILIES)
    return out


def query_for_result(result: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Convenience for the report: takes ``result['car']`` (geometry + UF from the CAR code or properties)."""
    car = (result or {}).get("car") or {}
    props = car.get("properties") or {}
    uf = props.get("uf") or str(props.get("cod_imovel") or "")[:2]
    return query_incra_acervo(car.get("geometry"), uf, **kwargs)


def is_complete(acervo: dict[str, Any] | None) -> bool:
    """Both families answered by every layer (found or not_found). Only such an answer may be reused."""
    if not isinstance(acervo, dict) or not acervo.get("enabled", True) or acervo.get("cancelled"):
        return False
    for family in FAMILIES:
        data = acervo.get(family)
        if not isinstance(data, dict) or data.get("state") not in ("found", "not_found") or data.get("complete") is False:
            return False
    return True


def acervo_for_report(result: dict[str, Any], **kwargs) -> dict[str, Any]:
    """The report's INCRA answer: a complete previous answer is reused; anything else is asked now.

    Never raises: an unexpected failure is a consultation that did not finish (pending), never an absence.
    """
    previous = (result or {}).get("incra_acervo")
    if is_complete(previous):
        return previous
    try:
        return query_for_result(result, **kwargs)
    except Exception as exc:  # trying is not answering
        detail = f"exception:{type(exc).__name__}"
        return {"source": SOURCE_LABEL, "enabled": enabled(kwargs.get("env")), "ok": False, "detail": detail,
                "sigef": _pending_family(detail), "snci": _pending_family(detail)}


# ---------------------------------------------------------------------------------------------
# presentation (pure): pt-BR, explicit state, no personal data
# ---------------------------------------------------------------------------------------------

def _num_br(value: float, digits: int = 2) -> str:
    text = f"{float(value):,.{digits}f}"
    return text.translate(str.maketrans(",.", ".,"))


def pct_floor_text(share: float | None) -> str:
    """0,998500 -> '99,85%'; 1 -> '100%'; floored, never rounded up."""
    if share is None:
        return ""
    basis = math.floor(round(float(share) * 1_000_000) / 100)
    if basis % 100 == 0:
        return f"{basis // 100}%"
    return _num_br(basis / 100, 2) + "%"


def pct_text(share: float | None) -> str:
    """pct_floor_text, but a real overlap below 0,01% never reads as '0%'."""
    text = pct_floor_text(share)
    if share is not None and float(share) > 0 and text == "0%":
        return "menos de 0,01%"
    return text


def _date_br(value: str | None) -> str:
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def _float(value) -> float | None:
    try:
        v = float(str(value).replace(",", "."))
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _clean_name(value) -> str | None:
    s = " ".join(str(value or "").split())
    if len(s) < 3 or s.upper() in _GENERIC_NAMES:
        return None
    return s[:180]


_SIGEF_STATUS = {"CERTIFICADA": "certificada", "REGISTRADA": "certificada e registrada em cartório"}


def _item_view(row: dict[str, Any]) -> dict[str, Any]:
    props = row["props"]
    if row["family"] == "snci":
        declared = _float(props.get("qtd_area_peca_tecnica"))
        area = declared if declared is not None else row["parcel_area_ha"]
        return {
            "family": "snci", "layer": row["layer"], "certification": props.get("num_certificacao") or None,
            "property_code": props.get("cod_imovel_rural") or None, "date": _date_br(props.get("data_certificacao")) or None,
            "area_ha": area, "area_text": f"{_num_br(area)} ha", "car_share": row["car_share"],
            "car_pct_text": pct_text(row["car_share"]), "parcel_share": row["parcel_share"],
        }
    status = str(props.get("status") or "").strip().upper()
    return {
        "family": "sigef", "layer": row["layer"], "parcel_code": props.get("parcela_codigo") or None,
        "property_code": props.get("codigo_imovel") or None, "status": status or None,
        "status_text": _SIGEF_STATUS.get(status, status.lower()) or None,
        "date": _date_br(props.get("data_aprovacao")) or None, "area_ha": row["parcel_area_ha"],
        "area_text": f"{_num_br(row['parcel_area_ha'])} ha", "car_share": row["car_share"],
        "car_pct_text": pct_text(row["car_share"]), "parcel_share": row["parcel_share"],
    }


_NOT_ASKED_DETAILS = ("not_queried", "disabled", "invalid_uf", "car_geometry")
# Not asked (not wired, switched off, no usable CAR): nothing was asked, so nothing "did not answer".
# Asked but not finished (layer down, cut short, unreadable geometry, cancelled): the consultation is not concluded.
PENDING_READING = {
    "not_asked": "Consulta ao INCRA não realizada nesta emissão; isso não indica ausência de certificação.",
    "incomplete": "Consulta ao INCRA não concluída nesta emissão; isso não indica ausência de certificação.",
}
PARTIAL_READING = "Consulta parcial: uma camada do INCRA não respondeu por completo, então pode haver outros registros."


def pending_reason(data: dict[str, Any] | None) -> str:
    """'not_asked' only when nothing reached INCRA; any layer asked (or a failure while asking) is 'incomplete'."""
    data = data or {}
    detail = str(data.get("detail") or "not_queried")
    if data.get("layers") or not detail.startswith(_NOT_ASKED_DETAILS):
        return "incomplete"
    return "not_asked"


def _family_view(family: str, data: dict[str, Any] | None, checked_text: str) -> dict[str, Any]:
    data = data or _pending_family("not_queried")
    state = data.get("state") if data.get("state") in ("found", "not_found", "pending") else "pending"
    items = [_item_view(r) for r in data.get("rows") or []] if state == "found" else []
    name = "SIGEF" if family == "sigef" else "SNCI"
    base = "SIGEF (INCRA)" if family == "sigef" else "SNCI (INCRA)"
    partial = state == "found" and data.get("complete") is False
    view: dict[str, Any] = {"state": state, "items": items[:5], "count": len(items) if state == "found" else None,
                            "count_is_minimum": partial, "partial": partial}
    if state == "pending":
        reason = pending_reason(data)
        text = f"Certificação {name}: consulta pendente."
        view.update(text=text, status="CONSULTA PENDENTE", coverage=None, car_share=None, pending_reason=reason,
                    row=[base, "CONSULTA PENDENTE", "—", PENDING_READING[reason]])
        return view
    if state == "not_found":
        text = ("Não há parcela SIGEF certificada sobre o imóvel." if family == "sigef"
                else "Não há certificação SNCI sobre o imóvel.")
        when = f" (consulta de {checked_text})" if checked_text else ""
        view.update(text=text, status="SEM CERTIFICAÇÃO", coverage=None, car_share=0.0,
                    row=[base, "SEM CERTIFICAÇÃO", "0", text[:-1] + " na base oficial do INCRA" + when + "."])
        return view
    best = items[0]
    n = len(items)
    count_text = f"pelo menos {n}" if partial else str(n)
    union = data.get("union_share")
    union = best["car_share"] if union is None else max(float(union), best["car_share"])
    union_text = ("pelo menos " if partial else "") + pct_text(union)
    if best["car_share"] >= COVERS_SHARE:
        # The CAR is a small piece of a much larger certified perimeter: the seal would suggest this property is certified.
        within = best["parcel_share"] < WITHIN_PARCEL_SHARE
        coverage = "within" if within else "covers"
        if family == "snci":
            parts = ["Certificação SNCI (INCRA) de perímetro maior que o imóvel" if within else "Certificado no SNCI (INCRA)"]
            if best.get("certification"):
                parts.append(f"nº {best['certification']}")
            if best.get("date"):
                parts.append(best["date"])
        else:
            status_text = best.get("status_text") or "certificada"
            parts = [f"Parcela SIGEF {status_text} (INCRA) de perímetro maior que o imóvel" if within
                     else f"Parcela {status_text} no SIGEF (INCRA)"]
            if best.get("date"):
                parts.append(f"aprovada em {best['date']}")
        # The certified area is the parcel's, not the CAR's: say so, so it is never read as the property area.
        area_phrase = f"área certificada de {best['area_text']}" if family == "snci" else f"parcela de {best['area_text']}"
        parts += [area_phrase, f"cobre {best['car_pct_text']} do imóvel"]
        if within:
            parts.append(f"o imóvel ocupa {pct_text(best['parcel_share'])} da área certificada")
        text = " · ".join(parts)
        status = WITHIN_STATUS if within else "CERTIFICADO"
    elif union >= COVERS_SHARE:
        within = all(i["parcel_share"] < WITHIN_PARCEL_SHARE for i in items)
        coverage = "within" if within else "covers"
        noun = "Parcelas SIGEF certificadas" if family == "sigef" else "Certificações SNCI"
        text = f"{noun} cobrem {union_text} do imóvel ({count_text} registros)"
        if within:
            text += "; o imóvel ocupa menos da metade de cada perímetro certificado"
        status = WITHIN_STATUS if within else "CERTIFICADO"
    else:
        coverage = "partial"
        plural = n > 1 or partial
        if family == "sigef":
            noun = "Parcelas SIGEF certificadas sobrepostas" if plural else "Parcela SIGEF certificada sobreposta"
        else:
            noun = "Certificações SNCI sobrepostas" if plural else "Certificação SNCI sobreposta"
        text = f"{noun} a {union_text} do imóvel"
        if plural:
            text += f" ({count_text} {'registro' if n == 1 else 'registros'})"
        status = "SOBREPOSIÇÃO PARCIAL"
    reading = text + "." + (" Não equivale a matrícula." if family == "sigef" else "") + (" " + PARTIAL_READING if partial else "")
    view.update(text=text + ".", status=status, coverage=coverage, car_share=union,
                row=[base, status, count_text, reading])
    return view


def land_payload(acervo: dict[str, Any] | None) -> dict[str, Any]:
    """Client fields for SIGEF and SNCI with explicit state. None (not wired) or disabled -> pending."""
    acervo = acervo if isinstance(acervo, dict) else {}
    checked_text = ""
    try:
        if acervo.get("checked_at") and acervo.get("enabled", True):
            checked_text = datetime.fromisoformat(str(acervo["checked_at"])).astimezone(BRT).strftime("%d/%m/%Y")
    except Exception:
        checked_text = ""
    sigef = _family_view("sigef", acervo.get("sigef"), checked_text)
    snci = _family_view("snci", acervo.get("snci"), checked_text)
    registry = "Matrícula, ônus e titularidade dependem de certidão do cartório de registro de imóveis e não são inferidos do CAR."
    if sigef["state"] == "pending" and snci["state"] == "pending":
        summary = "Certificação SIGEF e SNCI (INCRA): consulta pendente. " + registry
    else:
        summary = f"{sigef['text']} {snci['text']} {registry}"
    any_pending = "pending" in (sigef["state"], snci["state"])
    return {
        "source": SOURCE_LABEL, "enabled": bool(acervo.get("enabled", True)) if acervo else enabled(),
        "checked_at": checked_text or None, "sigef": sigef, "snci": snci, "summary": summary,
        "fundiario_text": (
            ("Certificação SIGEF/SNCI: consulta pendente; " if sigef["state"] == "pending" and snci["state"] == "pending"
             else f"SIGEF: {sigef['status'].lower()}; SNCI: {snci['status'].lower()}; ")
            + "matrícula não consultada"
        ),
        "any_pending": any_pending,
    }


def family_answered(acervo: dict[str, Any] | None, family: str) -> bool:
    """The family answered completely: not_found (always complete) or found with every layer answering.

    A presence found while another layer of the family did not answer is a partial consultation, not a
    consulted core source ("200 não é todos").
    """
    data = (acervo or {}).get(family) if isinstance(acervo, dict) else None
    if not isinstance(data, dict):
        return False
    return data.get("state") == "not_found" or (data.get("state") == "found" and data.get("complete") is not False)


def _is_certification_row(row) -> bool:
    return isinstance(row, (list, tuple)) and bool(row) and str(row[0]).strip().upper().startswith(("SIGEF", "SNCI"))


def _is_certification_source(src) -> bool:
    name = str((src or {}).get("name") or "").strip().casefold() if isinstance(src, dict) else ""
    return "sigef" in name or name.startswith("snci") or "acervo fundiário" in name


_SNCI_ATTENTION = "Matrícula, titularidade registral e SNCI ainda não foram consultados neste ciclo."
_COVERAGE_LEVEL = {"covers": "ok", "within": "info", "partial": "info"}


def _compliance_level(fam_view: dict[str, Any]) -> str:
    if fam_view["state"] == "pending":
        return "neutral"
    if fam_view["state"] == "not_found":
        return "info"
    return _COVERAGE_LEVEL.get(fam_view.get("coverage"), "info")


def apply_to_report_payload(payload: dict[str, Any], acervo: dict[str, Any] | None) -> dict[str, Any]:
    """Rewrite every SIGEF/SNCI line of the report payload from the official answer.

    Idempotent. Without an answer (not wired, disabled, failed) every line says "consulta pendente":
    the PAMGIA mirror is never used to say "0 parcela" or "não possui".
    """
    view = land_payload(acervo)
    sigef, snci = view["sigef"], view["snci"]
    land = payload.setdefault("land", {})
    land["summary"] = view["summary"]
    land["certifications"] = [sigef["row"], snci["row"]] + [r for r in land.get("certifications") or [] if not _is_certification_row(r)]
    matrix = []
    for row in land.get("matrix") or []:
        if _is_certification_row(row):
            r = list(row) + [""] * (4 - len(row))
            fam_view = snci if str(r[0]).strip().upper().startswith("SNCI") else sigef
            result = fam_view["status"] if fam_view["state"] != "found" else f"{fam_view['status']} · {fam_view['items'][0]['car_pct_text']}"
            matrix.append([r[0], result, r[2] or "-", "Não equivale a matrícula imobiliária"])
        else:
            matrix.append(row)
    land["matrix"] = matrix
    compliance, placed = [], False
    for item in payload.get("compliance") or []:
        label = str((item or {}).get("label") or "").strip().upper() if isinstance(item, dict) else ""
        if label in ("SIGEF", "SNCI"):
            if not placed:
                compliance.append({"label": "SIGEF", "text": sigef["text"], "badge": sigef["status"], "level": _compliance_level(sigef)})
                compliance.append({"label": "SNCI", "text": snci["text"], "badge": snci["status"], "level": _compliance_level(snci)})
                placed = True
            continue
        compliance.append(item)
    if "compliance" in payload:
        payload["compliance"] = compliance
    for row in payload.get("executive_summary_rows") or []:
        if isinstance(row, list) and row and row[0] == "Fundiário" and len(row) > 1:
            row[1] = view["fundiario_text"]
    for cat in (payload.get("conclusion") or {}).get("categories") or []:
        if isinstance(cat, dict) and cat.get("label") == "Fundiário":
            cat["text"] = f"{sigef['text']} {snci['text']} Matrícula e titularidade registral pendentes."
    if isinstance(payload.get("attention_points"), list):
        payload["attention_points"] = [
            ("Matrícula e titularidade registral ainda não foram consultadas neste ciclo." if x == _SNCI_ATTENTION else x)
            for x in payload["attention_points"]
        ]
    sources = [s for s in payload.get("sources") or [] if not _is_certification_source(s)]
    when = f" Consulta de {view['checked_at']}." if view.get("checked_at") else ""
    for family, fam_view in (("sigef", sigef), ("snci", snci)):
        name = "INCRA — Acervo Fundiário (SIGEF)" if family == "sigef" else "INCRA — Acervo Fundiário (SNCI)"
        if fam_view["state"] == "pending":
            sources.append({"name": name, "status": "CONSULTA PENDENTE", "level": "attention",
                            "description": PENDING_READING[fam_view["pending_reason"]]})
        elif fam_view["partial"]:
            sources.append({"name": name, "status": "PARCIAL", "level": "attention",
                            "description": "Certificações consultadas na base oficial do INCRA e cruzadas com o perímetro do CAR. "
                                           + PARTIAL_READING + when})
        else:
            sources.append({"name": name, "status": "CONSULTADA", "level": "ok",
                            "description": "Certificações consultadas na base oficial do INCRA e cruzadas com o perímetro do CAR." + when})
    payload["sources"] = sources
    payload["incra_acervo_f2"] = {k: view[k] for k in ("source", "checked_at", "sigef", "snci")}
    return payload


def summary_item(acervo: dict[str, Any] | None) -> dict[str, Any]:
    """Legacy analysis summary: never the mirror's envelope count; a partial answer carries a minimum, never a total."""
    data = (acervo or {}).get("sigef") if isinstance(acervo, dict) else None
    if not isinstance(data, dict) or data.get("state") not in ("found", "not_found"):
        return {"ok": None, "state": "pending", "source": SOURCE_LABEL}
    rows = data.get("rows") or []
    if data.get("state") == "found" and data.get("complete") is False:
        return {"ok": True, "state": "found", "complete": False, "occurrence_count_min": len(rows), "source": SOURCE_LABEL}
    return {"ok": True, "state": data["state"], "complete": True, "occurrence_count": len(rows), "source": SOURCE_LABEL}


# ---------------------------------------------------------------------------------------------
# card (C2b-compatible reference candidates)
# ---------------------------------------------------------------------------------------------

def reference_candidates(acervo: dict[str, Any]) -> dict[str, Any]:
    """C2b contract: {'ok','items','count','truncated','truncated_relevant','partial'}.

    ok is True only when the answer can support the card: every layer answered, or at least one
    parcel covering half of the CAR was found (then 'partial' marks the count of others as a floor).
    """
    if not isinstance(acervo, dict) or acervo.get("cancelled"):
        return {"ok": False, "cancelled": bool((acervo or {}).get("cancelled")), "detail": "request_cancelled", "items": []}
    if not acervo.get("enabled", True):
        return {"ok": False, "detail": "incra_acervo_disabled", "items": []}
    items = []
    complete = True
    for family in FAMILIES:
        data = acervo.get(family) or {}
        if data.get("state") == "pending" or not data.get("complete", False):
            complete = False
        for row in data.get("rows") or []:
            view = _item_view(row)
            props = row["props"]
            name = _clean_name(props.get("nome_area") if family == "sigef" else props.get("nome_imovel"))
            if family == "snci":
                label = name or (f"Certificação SNCI nº {view['certification']}" if view.get("certification") else "Certificação SNCI")
                detail = " · ".join(x for x in (f"nº {view['certification']}" if view.get("certification") else "", view.get("date") or "") if x)
            else:
                label = name or "Parcela SIGEF certificada"
                detail = " · ".join(x for x in ((view.get("status_text") or "").capitalize(), f"aprovada em {view['date']}" if view.get("date") else "") if x)
            items.append({
                "name": label, "overlap_ratio": row["car_share"], "parcel_overlap_ratio": row["parcel_share"],
                "area_ratio": round(row["parcel_area_ha"] / acervo["car_area_ha"], 4) if acervo.get("car_area_ha") else None,
                "score": row["car_share"], "parcel_code": view.get("parcel_code") or view.get("certification"),
                "property_code": view.get("property_code"), "certification": view.get("certification"),
                "detail": detail or None, "uf": acervo.get("uf"),
                "source": ORIGIN_LABEL[family], "origin": ORIGIN_LABEL[family],
                "display_kind": "REFERENCE", "validation_status": "UNVALIDATED", "panel_name_eligible": False,
                "reference_kind": REFERENCE_KIND[family], "map_anchor": "CADASTRAL_REFERENCE",
                "origin_label": ORIGIN_LABEL[family] + " — referência cadastral ainda não vinculada ao CAR",
            })
    items.sort(key=lambda x: x["score"], reverse=True)
    strong = any(x["overlap_ratio"] >= 0.5 for x in items)
    if not complete and not strong:
        detail = "; ".join(
            f"{family}:{key}:{info.get('detail')}" for family in FAMILIES
            for key, info in ((acervo.get(family) or {}).get("layers") or {}).items() if not info.get("answered")
        ) or str(acervo.get("detail") or "incra_acervo_pending")
        return {"ok": False, "detail": detail[:300], "items": []}
    return {"ok": True, "items": items, "count": len(items), "truncated": False, "truncated_relevant": False,
            "partial": not complete, "source": SOURCE_LABEL}


def identity_candidates(car_geometry, bbox=None, uf: str | None = None, *, cancel_event=None, fetch=None,
                        env: dict[str, str] | None = None) -> dict[str, Any]:
    """The card's INCRA reference (SIGEF + SNCI) for one CAR, in the C2b candidates contract."""
    acervo = query_incra_acervo(car_geometry, uf, fetch=fetch, cancel_event=cancel_event, env=env)
    return reference_candidates(acervo)


print("RX_INCRA_ACERVO_F2=official_sigef_snci_states_found_not_found_pending", flush=True)

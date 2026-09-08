from __future__ import annotations

"""V48 Source 2 — SINAFLOR authorization spatial truth.

The product question is deliberately narrower than a legal conclusion:
"is there a public SINAFLOR ASV/UAS authorization polygon that actually
intersects this CAR?"

Truth rules:
- The official IBAMA/PAMGIA polygon layer is the source of authorization geometry.
- ArcGIS envelope hits are candidates only; they never count as an authorization.
- A match requires positive-area CAR × authorization geometry intersection.
- No match in SINAFLOR is NOT promoted to "illegal deforestation" or proof that
  no authorization exists in another competent system.
- Source/query/CAR failures are fail-closed and never become green/zero.
- Authorization status and validity are facts displayed separately from geometry.
"""

import datetime as dt
import json
import time
import unicodedata
from typing import Any
from urllib.parse import urlencode

import deploy_app
from shapely.geometry import shape
try:
    from shapely import make_valid as _make_valid
except Exception:  # pragma: no cover - shapely < 2 compatibility
    from shapely.validation import make_valid as _make_valid

from sicar_integrity_v47 import area_ha_grs80

SOURCE_NAME = "IBAMA — SINAFLOR / PAMGIA"
LAYER_URL = (
    "https://pamgia.ibama.gov.br/server/rest/services/"
    "SINAFLORGEO/sinaflor_proj_externo_merge_a/FeatureServer/6"
)
QUERY_URL = LAYER_URL + "/query"
SOURCE_PAGE = (
    "https://www.gov.br/ibama/pt-br/assuntos/biodiversidade/"
    "flora-e-madeira/sistema-nacional-de-controle-da-origem-dos-produtos-florestais-sinaflor"
)
SRID = 4674
MAX_CANDIDATES = 2000
_METADATA_TTL = 21600.0
_METADATA_CACHE: tuple[float, dict[str, Any]] | None = None

# Relevant to the user-facing question "autorização de supressão". PMFS/POA and
# planted-forest exploitation are not silently treated as clear-cut authorization.
_RELEVANT_ACTIVITY_KEYS = (
    "autorizacao de supressao de vegetacao",
    "uso alternativo do solo",
)
_OUT_FIELDS = ",".join(
    (
        "objectid",
        "origem",
        "nu_recibo",
        "nu_autorizacao",
        "tipo_emp",
        "nm_orgao",
        "nm_empreendimento",
        "tipo_atividade",
        "municipio",
        "dt_valid_inicio",
        "dt_valid_fim",
        "status_autorizacao",
        "nu_car_imovel",
        "uf",
        "area_pamgia_ha",
        "bioma_pamgia",
        "dt_atualizacao",
    )
)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _fold(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _relevant_activity(value: Any) -> bool:
    folded = _fold(value)
    return any(key in folded for key in _RELEVANT_ACTIVITY_KEYS)


def _date_value(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(float(value) / 1000.0, tz=dt.timezone.utc).date()
        text = str(value).strip()
        if text.isdigit() and len(text) >= 10:
            return dt.datetime.fromtimestamp(float(text) / 1000.0, tz=dt.timezone.utc).date()
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _date_iso(value: Any) -> str | None:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else None


def _valid_geom(raw: Any):
    if not isinstance(raw, dict):
        return None
    try:
        geom = shape(raw)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = _make_valid(geom)
        if geom.is_empty:
            return None
        return geom
    except Exception:
        return None


def _status_active(value: Any) -> bool | None:
    folded = _fold(value)
    if not folded:
        return None
    if any(token in folded for token in ("cancel", "suspens", "vencid", "expir", "inativ", "encerr")):
        return False
    if any(token in folded for token in ("ativa", "ativo", "valida", "valido", "emitida", "vigente")):
        return True
    return None


def _temporal_status(props: dict[str, Any], on_date: dt.date) -> dict[str, Any]:
    start = _date_value(props.get("dt_valid_inicio"))
    end = _date_value(props.get("dt_valid_fim"))
    status_active = _status_active(props.get("status_autorizacao"))
    within = None
    if start or end:
        within = (start is None or on_date >= start) and (end is None or on_date <= end)
    current = None
    if status_active is False or within is False:
        current = False
    elif status_active is True and within is not False:
        current = True
    elif within is True and status_active is not False:
        current = True
    return {
        "validity_start": start.isoformat() if start else None,
        "validity_end": end.isoformat() if end else None,
        "status_active": status_active,
        "within_validity_on_query_date": within,
        "currently_confirmed": current,
    }


def evaluate_features(
    car_geometry: dict[str, Any],
    features: list[dict[str, Any]],
    *,
    car_code: str,
    query_date: dt.date | None = None,
) -> dict[str, Any]:
    """Pure exact geometry evaluation used by deterministic and live gates."""
    car = _valid_geom(car_geometry)
    if car is None:
        return {"ok": False, "detail": "car_geometry_invalid", "matches": []}
    on_date = query_date or _utc_now().date()
    car_area = area_ha_grs80(car)
    matches: list[dict[str, Any]] = []
    relevant_candidates = 0

    for feature in features or []:
        props = dict(feature.get("properties") or feature.get("attributes") or {})
        if not _relevant_activity(props.get("tipo_atividade")):
            continue
        relevant_candidates += 1
        auth = _valid_geom(feature.get("geometry"))
        if auth is None:
            continue
        try:
            if not car.intersects(auth):
                continue
            inter = car.intersection(auth)
            if inter.is_empty:
                continue
            overlap_ha = float(area_ha_grs80(inter) or 0.0)
            if overlap_ha <= 1e-8:
                continue
        except Exception:
            continue

        temporal = _temporal_status(props, on_date)
        reported_car = str(props.get("nu_car_imovel") or "").strip().upper() or None
        matches.append(
            {
                "authorization_number": props.get("nu_autorizacao") or None,
                "receipt_number": props.get("nu_recibo") or None,
                "activity_type": props.get("tipo_atividade") or None,
                "issuing_agency": props.get("nm_orgao") or None,
                "enterprise": props.get("nm_empreendimento") or None,
                "status": props.get("status_autorizacao") or None,
                "municipality": props.get("municipio") or None,
                "uf": props.get("uf") or None,
                "reported_car": reported_car,
                "reported_car_matches": bool(reported_car and reported_car == car_code.upper()),
                "source_area_ha": props.get("area_pamgia_ha"),
                "overlap_ha": round(overlap_ha, 6),
                "overlap_pct_of_car": round((overlap_ha / car_area * 100.0), 4) if car_area else None,
                "data_updated_at": _date_iso(props.get("dt_atualizacao")),
                **temporal,
            }
        )

    matches.sort(key=lambda item: item.get("overlap_ha") or 0.0, reverse=True)
    return {
        "ok": True,
        "candidate_count": len(features or []),
        "relevant_candidate_count": relevant_candidates,
        "match_count": len(matches),
        "matches": matches,
        "car_area_ha": round(float(car_area or 0.0), 6),
    }


def _metadata() -> dict[str, Any]:
    global _METADATA_CACHE
    now = time.monotonic()
    if _METADATA_CACHE and now - _METADATA_CACHE[0] < _METADATA_TTL:
        return dict(_METADATA_CACHE[1])
    raw = deploy_app._curl(LAYER_URL + "?f=pjson", True)
    if not raw.get("ok"):
        result = {"ok": False, "detail": raw.get("detail") or "sinaflor_metadata_unavailable"}
    else:
        data = raw.get("json") or {}
        if data.get("error"):
            result = {"ok": False, "detail": "sinaflor_metadata_arcgis_error", "error": data.get("error")}
        else:
            editing = data.get("editingInfo") or {}
            last_edit = editing.get("lastEditDate") or editing.get("dataLastEditDate")
            result = {
                "ok": True,
                "data_date": _date_iso(last_edit),
                "layer_name": data.get("name"),
                "max_record_count": data.get("maxRecordCount"),
                "source_spatial_reference": ((data.get("extent") or {}).get("spatialReference") or {}).get("latestWkid")
                or ((data.get("extent") or {}).get("spatialReference") or {}).get("wkid"),
            }
    _METADATA_CACHE = (now, result)
    return dict(result)


def _query_candidates(bbox: list[float]) -> dict[str, Any]:
    if not bbox or len(bbox) != 4:
        return {"ok": False, "detail": "car_bbox_missing"}
    params = {
        "f": "geojson",
        "where": "1=1",
        "geometry": ",".join(f"{float(v):.10f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": str(SRID),
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": str(SRID),
        "resultRecordCount": str(MAX_CANDIDATES),
    }
    raw = deploy_app._curl(QUERY_URL + "?" + urlencode(params), True)
    if not raw.get("ok"):
        return {"ok": False, "detail": raw.get("detail") or "sinaflor_query_unavailable"}
    data = raw.get("json") or {}
    if data.get("error"):
        return {"ok": False, "detail": "sinaflor_arcgis_query_error", "error": data.get("error")}
    features = data.get("features") or []
    if data.get("exceededTransferLimit") or len(features) >= MAX_CANDIDATES:
        # Never call a truncated candidate set a negative answer.
        return {
            "ok": False,
            "detail": "sinaflor_candidate_limit_reached_fail_closed",
            "candidate_count": len(features),
        }
    return {"ok": True, "features": features, "candidate_count": len(features)}


def query_sinaflor_authorization(car_code: str) -> dict[str, Any]:
    queried_at = _iso_now()
    code = str(car_code or "").strip().upper()
    try:
        from car_resilient import CAR_RE, fetch_car_live_resilient
    except Exception as exc:
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": None,
            "reason": f"Dependência do CAR indisponível: {type(exc).__name__}.",
            "detail": "car_resolver_dependency_unavailable",
        }
    if not CAR_RE.match(code):
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": None,
            "reason": "Código CAR inválido; a checagem espacial não foi executada.",
            "detail": "invalid_car_format",
        }

    metadata = _metadata()
    if not metadata.get("ok"):
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": metadata.get("data_date"),
            "reason": "A fonte oficial SINAFLOR/PAMGIA não respondeu com metadados utilizáveis. Nenhuma ausência foi presumida.",
            "detail": metadata.get("detail"),
        }

    car = fetch_car_live_resilient(code)
    if not car.get("ok") or not car.get("geometry") or not car.get("bbox"):
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": metadata.get("data_date"),
            "reason": "O perímetro do CAR não pôde ser obtido; a checagem espacial SINAFLOR não foi concluída.",
            "detail": car.get("detail") or "car_geometry_unavailable",
        }

    candidates = _query_candidates(car["bbox"])
    if not candidates.get("ok"):
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": metadata.get("data_date"),
            "reason": "A consulta espacial à camada oficial SINAFLOR/PAMGIA falhou. Nenhuma ausência foi presumida.",
            "detail": candidates.get("detail"),
            "candidate_count": candidates.get("candidate_count"),
        }

    exact = evaluate_features(car["geometry"], candidates.get("features") or [], car_code=code)
    if not exact.get("ok"):
        return {
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "queried_at": queried_at,
            "data_date": metadata.get("data_date"),
            "reason": "A geometria não pôde ser confrontada com segurança. Nenhuma ausência foi presumida.",
            "detail": exact.get("detail"),
        }

    matches = exact.get("matches") or []
    data_dates = [m.get("data_updated_at") for m in matches if m.get("data_updated_at")]
    data_date = max(data_dates) if data_dates else metadata.get("data_date")
    base = {
        "ok": True,
        "answered": True,
        "source": SOURCE_NAME,
        "source_page": SOURCE_PAGE,
        "source_layer": LAYER_URL,
        "queried_at": queried_at,
        "data_date": data_date,
        "car_code": code,
        "candidate_count": exact.get("candidate_count"),
        "relevant_candidate_count": exact.get("relevant_candidate_count"),
        "match_count": exact.get("match_count"),
        "matches": matches,
        "car_area_ha": exact.get("car_area_ha"),
        "method": "ArcGIS envelope candidates + exact positive-area intersection in EPSG:4674 measured on GRS80",
    }
    if not matches:
        return {
            **base,
            "state": "checked_clear",
            "reason": (
                "Nenhum polígono público de ASV/UAS do SINAFLOR foi localizado intersectando o imóvel. "
                "Isso não prova ausência de autorização emitida fora do SINAFLOR ou em sistema estadual competente."
            ),
        }

    current_confirmed = [m for m in matches if m.get("currently_confirmed") is True]
    non_current = [m for m in matches if m.get("currently_confirmed") is False]
    unknown = [m for m in matches if m.get("currently_confirmed") is None]
    if current_confirmed:
        state = "checked_authorization_overlap"
        reason = (
            f"{len(matches)} autorização(ões) ASV/UAS com interseção espacial positiva foram localizadas; "
            f"{len(current_confirmed)} têm vigência/status compatíveis com a data da consulta. "
            "A existência da autorização não substitui a comparação temporal com o evento de desmatamento."
        )
    else:
        state = "checked_authorization_overlap_unconfirmed"
        reason = (
            f"{len(matches)} autorização(ões) ASV/UAS intersectam o imóvel, mas nenhuma teve vigência/status atual confirmados "
            f"({len(non_current)} não atuais; {len(unknown)} indeterminadas). "
            "Não foi concluído que eventual desmatamento estava autorizado."
        )
    return {**base, "state": state, "reason": reason}


print("RX_SINAFLOR_AUTH_V48=official_pamgia_polygon_exact_intersection_fail_closed", flush=True)

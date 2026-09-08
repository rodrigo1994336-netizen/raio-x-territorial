from __future__ import annotations

"""Truth hardening for V48 SINAFLOR spatial authorization.

PAMGIA can contain broad territorial geometries that spatially intersect a CAR
without identifying that CAR as an authorized property. This layer prevents
those geometries from being promoted to property-level authorization.
"""

import datetime as dt
import time
from typing import Any
from urllib.parse import urlencode

from shapely.geometry import shape
try:
    from shapely import make_valid as _make_valid
except Exception:  # pragma: no cover
    from shapely.validation import make_valid as _make_valid

import sinaflor_authorization_v48 as sf
from sicar_integrity_v47 import area_ha_grs80

# A polygon of 100,000 ha or more is treated as territorial-scale unless the
# SINAFLOR record itself corroborates the exact CAR. This is intentionally
# conservative: such a geometry may be useful context, but is not sufficient to
# assert that a 15-ha property is itself covered by an authorization.
BROAD_TERRITORIAL_SCOPE_HA = 100_000.0


def _valid_geom(raw: Any):
    if not isinstance(raw, dict):
        return None
    try:
        geom = shape(raw)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = _make_valid(geom)
        return None if geom.is_empty else geom
    except Exception:
        return None


def _temporal_status(props: dict[str, Any], on_date: dt.date) -> dict[str, Any]:
    start = sf._date_value(props.get("dt_valid_inicio"))
    end = sf._date_value(props.get("dt_valid_fim"))
    status_folded = sf._fold(props.get("status_autorizacao"))
    if any(token in status_folded for token in ("cancel", "suspens", "vencid", "expir", "inativ", "encerr")):
        status_active = False
    elif any(token in status_folded for token in ("ativa", "ativo", "vigente", "valida", "valido")):
        status_active = True
    else:
        # "Autorização Emitida" proves issuance, not current validity.
        status_active = None

    within = None
    if end is not None:
        within = (start is None or on_date >= start) and on_date <= end
    elif start is not None:
        # Start date without an end date is not enough to confirm current validity.
        within = None

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
    car_municipality: str | None = None,
    car_uf: str | None = None,
) -> dict[str, Any]:
    car = _valid_geom(car_geometry)
    if car is None:
        return {"ok": False, "detail": "car_geometry_invalid", "matches": []}
    on_date = query_date or sf._utc_now().date()
    car_area = float(area_ha_grs80(car) or 0.0)
    matches: list[dict[str, Any]] = []
    relevant_candidates = 0

    for feature in features or []:
        props = dict(feature.get("properties") or feature.get("attributes") or {})
        if not sf._relevant_activity(props.get("tipo_atividade")):
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
            authorization_geom_ha = float(area_ha_grs80(auth) or 0.0)
        except Exception:
            continue

        reported_car = str(props.get("nu_car_imovel") or "").strip().upper() or None
        exact_car = bool(reported_car and reported_car == car_code.upper())
        broad_scope = bool(authorization_geom_ha >= BROAD_TERRITORIAL_SCOPE_HA and not exact_car)
        if exact_car:
            binding = "exact_car_reference"
            binding_confirmed = True
        elif broad_scope:
            binding = "broad_territorial_geometry_unconfirmed"
            binding_confirmed = False
        else:
            binding = "exact_local_spatial_intersection"
            binding_confirmed = True

        source_municipality = str(props.get("municipio") or "").strip() or None
        source_uf = str(props.get("uf") or "").strip().upper() or None
        municipality_matches = None if not source_municipality or not car_municipality else sf._fold(source_municipality) == sf._fold(car_municipality)
        uf_matches = None if not source_uf or not car_uf else source_uf == str(car_uf).strip().upper()

        matches.append(
            {
                "authorization_number": props.get("nu_autorizacao") or None,
                "receipt_number": props.get("nu_recibo") or None,
                "activity_type": props.get("tipo_atividade") or None,
                "issuing_agency": props.get("nm_orgao") or None,
                "enterprise": props.get("nm_empreendimento") or None,
                "status": props.get("status_autorizacao") or None,
                "municipality": source_municipality,
                "uf": source_uf,
                "municipality_matches_car": municipality_matches,
                "uf_matches_car": uf_matches,
                "reported_car": reported_car,
                "reported_car_matches": exact_car,
                "source_area_ha": props.get("area_pamgia_ha"),
                "authorization_geometry_ha": round(authorization_geom_ha, 6),
                "broad_territorial_scope": broad_scope,
                "property_binding": binding,
                "property_binding_confirmed": binding_confirmed,
                "overlap_ha": round(overlap_ha, 6),
                "overlap_pct_of_car": round(overlap_ha / car_area * 100.0, 4) if car_area else None,
                "data_updated_at": sf._date_iso(props.get("dt_atualizacao")),
                **_temporal_status(props, on_date),
            }
        )

    matches.sort(key=lambda item: item.get("overlap_ha") or 0.0, reverse=True)
    confirmed = [m for m in matches if m.get("property_binding_confirmed") is True]
    unconfirmed = [m for m in matches if m.get("property_binding_confirmed") is False]
    return {
        "ok": True,
        "candidate_count": len(features or []),
        "relevant_candidate_count": relevant_candidates,
        "match_count": len(matches),
        "confirmed_match_count": len(confirmed),
        "unconfirmed_match_count": len(unconfirmed),
        "matches": matches,
        "car_area_ha": round(car_area, 6),
    }


def _latest_data_date() -> str | None:
    params = {
        "f": "json",
        "where": "dt_atualizacao IS NOT NULL",
        "outFields": "dt_atualizacao",
        "returnGeometry": "false",
        "orderByFields": "dt_atualizacao DESC",
        "resultRecordCount": "1",
    }
    raw = sf.deploy_app._curl(sf.QUERY_URL + "?" + urlencode(params), True)
    if not raw.get("ok"):
        return None
    data = raw.get("json") or {}
    if data.get("error"):
        return None
    features = data.get("features") or []
    if not features:
        return None
    props = features[0].get("attributes") or features[0].get("properties") or {}
    return sf._date_iso(props.get("dt_atualizacao"))


def metadata() -> dict[str, Any]:
    now = time.monotonic()
    if sf._METADATA_CACHE and now - sf._METADATA_CACHE[0] < sf._METADATA_TTL:
        return dict(sf._METADATA_CACHE[1])
    raw = sf.deploy_app._curl(sf.LAYER_URL + "?f=pjson", True)
    if not raw.get("ok"):
        result = {"ok": False, "detail": raw.get("detail") or "sinaflor_metadata_unavailable"}
    else:
        data = raw.get("json") or {}
        if data.get("error"):
            result = {"ok": False, "detail": "sinaflor_metadata_arcgis_error", "error": data.get("error")}
        else:
            editing = data.get("editingInfo") or {}
            last_edit = editing.get("lastEditDate") or editing.get("dataLastEditDate")
            data_date = sf._date_iso(last_edit) or _latest_data_date()
            result = {
                "ok": True,
                "data_date": data_date,
                "data_date_status": "published" if data_date else "not_published_by_layer",
                "layer_name": data.get("name"),
                "max_record_count": data.get("maxRecordCount"),
                "source_spatial_reference": ((data.get("extent") or {}).get("spatialReference") or {}).get("latestWkid")
                or ((data.get("extent") or {}).get("spatialReference") or {}).get("wkid"),
            }
    sf._METADATA_CACHE = (now, result)
    return dict(result)


def query_sinaflor_authorization(car_code: str) -> dict[str, Any]:
    queried_at = sf._iso_now()
    code = str(car_code or "").strip().upper()
    try:
        from car_resilient import CAR_RE, fetch_car_live_resilient
    except Exception as exc:
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": None, "data_date_status": "unavailable", "reason": f"Dependência do CAR indisponível: {type(exc).__name__}.", "detail": "car_resolver_dependency_unavailable"}
    if not CAR_RE.match(code):
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": None, "data_date_status": "unavailable", "reason": "Código CAR inválido; a checagem espacial não foi executada.", "detail": "invalid_car_format"}

    meta = metadata()
    if not meta.get("ok"):
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": meta.get("data_date"), "data_date_status": meta.get("data_date_status", "unavailable"), "reason": "A fonte oficial SINAFLOR/PAMGIA não respondeu com metadados utilizáveis. Nenhuma ausência foi presumida.", "detail": meta.get("detail")}

    car = fetch_car_live_resilient(code)
    if not car.get("ok") or not car.get("geometry") or not car.get("bbox"):
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": meta.get("data_date"), "data_date_status": meta.get("data_date_status"), "reason": "O perímetro do CAR não pôde ser obtido; a checagem espacial SINAFLOR não foi concluída.", "detail": car.get("detail") or "car_geometry_unavailable"}

    candidates = sf._query_candidates(car["bbox"])
    if not candidates.get("ok"):
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": meta.get("data_date"), "data_date_status": meta.get("data_date_status"), "reason": "A consulta espacial à camada oficial SINAFLOR/PAMGIA falhou. Nenhuma ausência foi presumida.", "detail": candidates.get("detail"), "candidate_count": candidates.get("candidate_count")}

    props = car.get("properties") or {}
    exact = evaluate_features(
        car["geometry"],
        candidates.get("features") or [],
        car_code=code,
        car_municipality=props.get("municipio") or props.get("nom_munici"),
        car_uf=props.get("uf") or code[:2],
    )
    if not exact.get("ok"):
        return {"ok": False, "answered": False, "state": "source_failed", "source": sf.SOURCE_NAME, "source_page": sf.SOURCE_PAGE, "queried_at": queried_at, "data_date": meta.get("data_date"), "data_date_status": meta.get("data_date_status"), "reason": "A geometria não pôde ser confrontada com segurança. Nenhuma ausência foi presumida.", "detail": exact.get("detail")}

    matches = exact.get("matches") or []
    record_dates = [m.get("data_updated_at") for m in matches if m.get("data_updated_at")]
    data_date = max(record_dates) if record_dates else meta.get("data_date")
    data_date_status = "published" if data_date else meta.get("data_date_status", "not_published_by_layer")
    base = {
        "ok": True,
        "answered": True,
        "source": sf.SOURCE_NAME,
        "source_page": sf.SOURCE_PAGE,
        "source_layer": sf.LAYER_URL,
        "queried_at": queried_at,
        "data_date": data_date,
        "data_date_status": data_date_status,
        "car_code": code,
        "candidate_count": exact.get("candidate_count"),
        "relevant_candidate_count": exact.get("relevant_candidate_count"),
        "match_count": exact.get("match_count"),
        "confirmed_match_count": exact.get("confirmed_match_count"),
        "unconfirmed_match_count": exact.get("unconfirmed_match_count"),
        "matches": matches,
        "car_area_ha": exact.get("car_area_ha"),
        "method": "ArcGIS envelope candidates + exact positive-area intersection in EPSG:4674/GRS80 + broad-scope binding guard",
    }
    if not matches:
        return {**base, "state": "checked_clear", "reason": "Nenhum polígono público de ASV/UAS do SINAFLOR foi localizado intersectando o imóvel. Isso não prova ausência de autorização emitida fora do SINAFLOR ou em sistema estadual competente."}

    confirmed = [m for m in matches if m.get("property_binding_confirmed") is True]
    if not confirmed:
        return {**base, "state": "checked_spatial_record_unconfirmed", "reason": f"{len(matches)} registro(s) SINAFLOR intersectam espacialmente o CAR, mas o vínculo com este imóvel não pôde ser confirmado. A geometria publicada é de escopo territorial amplo e não traz o CAR coincidente; não foi declarado que o imóvel está autorizado."}

    current = [m for m in confirmed if m.get("currently_confirmed") is True]
    if current:
        return {**base, "state": "checked_authorization_overlap", "reason": f"{len(confirmed)} autorização(ões) ASV/UAS com vínculo espacial/local confirmado foram localizadas; {len(current)} têm vigência/status compatíveis com a data da consulta. Isso ainda não substitui a comparação temporal com o evento de desmatamento."}
    return {**base, "state": "checked_authorization_overlap_unconfirmed", "reason": f"{len(confirmed)} autorização(ões) ASV/UAS com vínculo espacial/local foram localizadas, mas a vigência/status atual não pôde ser confirmada. Não foi concluído que eventual desmatamento estava autorizado."}


# Patch the V48 source module deliberately; portal/gates import this hardening before use.
sf.evaluate_features = evaluate_features
sf._metadata = metadata
sf.query_sinaflor_authorization = query_sinaflor_authorization

print("RX_SINAFLOR_AUTH_HARDENING_V48=broad_scope_never_property_authorization_temporal_fail_closed", flush=True)

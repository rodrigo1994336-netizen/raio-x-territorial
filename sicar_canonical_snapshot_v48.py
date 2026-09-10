from __future__ import annotations

import json
from typing import Any

import sicar_integrity_v47 as sicar
import sicar_canonical_manifest_v48 as canonical
import sicar_geometry_normalization_v48 as geometry_contract

_ORIGINAL_QUERY = sicar.query_car_integrity_v47
_ORIGINAL_BUILD = sicar.build_integrity_from_records


def _fetch_property_canonical(client: Any, car_code: str, uf: str) -> dict[str, Any] | None:
    snapshot = canonical.canonical_snapshot_for_uf(uf)
    if snapshot is None:
        raise RuntimeError(f"canonical_snapshot_unavailable:{uf}")
    sql = f"""
    WITH source_rows AS (
      SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area, status,
             data_atualizacao, geometria
      FROM `{sicar.DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND id_imovel=@car_code AND data_extracao=@snapshot
    ), per_car_source AS (
      SELECT
        id_imovel,
        ARRAY_AGG(
          STRUCT(data_extracao, sigla_uf, id_municipio, area, status, data_atualizacao)
          ORDER BY data_atualizacao DESC NULLS LAST, id_municipio ASC
          LIMIT 1
        )[OFFSET(0)] AS attrs,
        COUNT(*) AS source_row_count,
        COUNTIF(geometria IS NOT NULL) AS geometry_row_count,
        ST_UNION_AGG(geometria) AS source_geometry
      FROM source_rows
      GROUP BY id_imovel
    )
    {geometry_contract.normalization_ctes('per_car_source')}
    SELECT
      attrs.data_extracao AS data_extracao,
      attrs.sigla_uf AS sigla_uf,
      attrs.id_municipio AS id_municipio,
      id_imovel,
      attrs.area AS area,
      attrs.status AS status,
      source_row_count,
      geometry_row_count,
      source_geometry_type,
      render_geometry_type,
      source_geometry_fingerprint,
      render_geometry_fingerprint,
      discarded_line_components,
      discarded_line_length_m,
      discarded_point_components,
      polygon_area_before_m2,
      polygon_area_after_m2,
      polygon_area_difference_m2,
      polygon_area_tolerance_m2,
      has_no_polygonal_component,
      discarded_nonpolygon_components,
      normalization_applied,
      ST_ASGEOJSON(render_geometry) AS geometry_geojson
    FROM normalized_geometry_metrics
    LIMIT 1
    """
    rows = sicar._query(client, sql, {"uf": uf, "car_code": car_code, "snapshot": snapshot})
    return rows[0] if rows else None


def _normalization_payload(property_record: dict[str, Any]) -> dict[str, Any]:
    applied = bool(property_record.get("normalization_applied"))
    discarded = bool(property_record.get("discarded_nonpolygon_components"))
    return {
        **geometry_contract.normalization_contract_fields(),
        "source_geometry_type": property_record.get("source_geometry_type"),
        "render_geometry_type": property_record.get("render_geometry_type"),
        "source_geometry_fingerprint": property_record.get("source_geometry_fingerprint"),
        "render_geometry_fingerprint": property_record.get("render_geometry_fingerprint"),
        "normalization_applied": applied,
        "discarded_nonpolygon_components": discarded,
        "discarded_line_components": int(property_record.get("discarded_line_components") or 0),
        "discarded_line_length_m": float(property_record.get("discarded_line_length_m") or 0.0),
        "discarded_point_components": int(property_record.get("discarded_point_components") or 0),
        "polygon_area_before_m2": float(property_record.get("polygon_area_before_m2") or 0.0),
        "polygon_area_after_m2": float(property_record.get("polygon_area_after_m2") or 0.0),
        "polygon_area_difference_m2": float(property_record.get("polygon_area_difference_m2") or 0.0),
        "polygon_area_tolerance_m2": float(property_record.get("polygon_area_tolerance_m2") or 0.0),
        "user_notice": geometry_contract.NORMALIZATION_USER_NOTICE if applied and discarded else None,
    }


def _build_integrity_canonical(
    car_code: str,
    property_record: dict[str, Any],
    theme_records: dict[str, dict[str, Any]],
    overlap_record: dict[str, Any] | None = None,
    municipality_boundary: dict[str, Any] | None = None,
    uf_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = property_record.get("data_extracao")
    snapshot_text = str(snapshot) if snapshot is not None else ""
    if int(property_record.get("source_row_count") or 0) > 0 and int(property_record.get("geometry_row_count") or 0) == 0:
        return {
            "ok": False,
            "state": "no_geometry_published",
            "car_code": car_code,
            "detail": "property_has_no_published_geometry_in_canonical_snapshot",
            "source": sicar.DATASET,
            "snapshot": snapshot_text,
            "snapshot_scope": "uf_canonical",
            "user_message": geometry_contract.no_geometry_user_message(canonical.date_pt(snapshot)),
            "geometry_available": False,
            "geometry_normalization": {**geometry_contract.normalization_contract_fields(), "normalization_applied": False},
        }
    if bool(property_record.get("has_no_polygonal_component")):
        return {
            "ok": False,
            "state": "no_polygonal_geometry",
            "car_code": car_code,
            "detail": "published_geometry_has_no_polygonal_component",
            "source": sicar.DATASET,
            "snapshot": snapshot_text,
            "snapshot_scope": "uf_canonical",
            "user_message": "A geometria publicada para este imóvel não contém componente poligonal utilizável pelo Raio-X.",
            "geometry_available": False,
            "geometry_normalization": _normalization_payload(property_record),
        }
    difference = float(property_record.get("polygon_area_difference_m2") or 0.0)
    tolerance = float(property_record.get("polygon_area_tolerance_m2") or 0.0)
    if difference > tolerance:
        return {
            "ok": False,
            "state": "geometry_normalization_failed_closed",
            "car_code": car_code,
            "detail": f"polygon_area_invariant_failed:{difference}>{tolerance}",
            "source": sicar.DATASET,
            "snapshot": snapshot_text,
            "snapshot_scope": "uf_canonical",
            "geometry_available": False,
            "geometry_normalization": _normalization_payload(property_record),
        }
    out = _ORIGINAL_BUILD(
        car_code,
        property_record,
        theme_records,
        overlap_record,
        municipality_boundary,
        uf_boundary,
    )
    if out.get("ok"):
        out = dict(out)
        out["geometry_available"] = True
        out["geometry_normalization"] = _normalization_payload(property_record)
    return out


def _freshness_fields(uf: str, snapshot) -> dict[str, Any]:
    note = canonical.staleness_note(snapshot)
    return {
        "snapshot_label": canonical.base_label(uf, snapshot),
        "snapshot_age_days": canonical.age_days(snapshot),
        "snapshot_staleness_note": note,
    }


def query_canonical_property_geometry(car_code: str, *, client: Any | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not sicar.CAR_RE.match(code):
        return {"ok": False, "state": "invalid", "car_code": code, "detail": "invalid_car_format", "source": sicar.DATASET}
    uf = code[:2]
    snapshot = canonical.canonical_snapshot_for_uf(uf)
    if snapshot is None:
        return {"ok": False, "state": "unavailable", "car_code": code, "detail": "canonical_snapshot_unavailable", "source": sicar.DATASET}
    own_client = client
    try:
        if own_client is None:
            own_client = sicar._client()
        record = _fetch_property_canonical(own_client, code, uf)
        freshness = _freshness_fields(uf, snapshot)
        if not record:
            return {
                "ok": False,
                "state": "not_present_in_canonical_snapshot",
                "car_code": code,
                "detail": "car_not_present_in_canonical_snapshot",
                "source": sicar.DATASET,
                "snapshot": snapshot.isoformat(),
                "snapshot_scope": "uf_canonical",
                "user_message": f"Este imóvel não consta na base de {canonical.UF_NAMES.get(uf, uf)} de {canonical.date_pt(snapshot)}.",
                **freshness,
            }
        source_rows = int(record.get("source_row_count") or 0)
        geometry_rows = int(record.get("geometry_row_count") or 0)
        if source_rows > 0 and geometry_rows == 0:
            return {
                "ok": False,
                "state": "no_geometry_published",
                "car_code": code,
                "detail": "property_has_no_published_geometry_in_canonical_snapshot",
                "source": sicar.DATASET,
                "snapshot": snapshot.isoformat(),
                "snapshot_scope": "uf_canonical",
                "user_message": geometry_contract.no_geometry_user_message(canonical.date_pt(snapshot)),
                "geometry_available": False,
                **freshness,
            }
        norm = _normalization_payload(record)
        if bool(record.get("has_no_polygonal_component")):
            return {
                "ok": False,
                "state": "no_polygonal_geometry",
                "car_code": code,
                "detail": "published_geometry_has_no_polygonal_component",
                "source": sicar.DATASET,
                "snapshot": snapshot.isoformat(),
                "snapshot_scope": "uf_canonical",
                "geometry_available": False,
                "geometry_normalization": norm,
                **freshness,
            }
        if float(norm["polygon_area_difference_m2"]) > float(norm["polygon_area_tolerance_m2"]):
            return {
                "ok": False,
                "state": "geometry_normalization_failed_closed",
                "car_code": code,
                "detail": "polygon_area_invariant_failed",
                "source": sicar.DATASET,
                "snapshot": snapshot.isoformat(),
                "snapshot_scope": "uf_canonical",
                "geometry_available": False,
                "geometry_normalization": norm,
                **freshness,
            }
        raw = record.get("geometry_geojson")
        geometry = json.loads(raw) if isinstance(raw, str) and raw else raw
        return {
            "ok": True,
            "state": "normalized" if norm.get("normalization_applied") else "canonical",
            "car_code": code,
            "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(),
            "snapshot_scope": "uf_canonical",
            "geometry_available": geometry is not None,
            "geometry": geometry,
            "geometry_normalization": norm,
            "id_municipio": record.get("id_municipio"),
            "sigla_uf": record.get("sigla_uf"),
            "area": record.get("area"),
            "status": record.get("status"),
            **freshness,
        }
    except Exception as exc:
        return {
            "ok": False,
            "state": "unavailable",
            "car_code": code,
            "detail": f"{type(exc).__name__}:{str(exc)[:220]}",
            "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(),
            "snapshot_scope": "uf_canonical",
            **_freshness_fields(uf, snapshot),
        }


def query_car_integrity_v48(car_code: str, *, client: Any | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not sicar.CAR_RE.match(code):
        return _ORIGINAL_QUERY(code, client=client)
    uf = code[:2]
    try:
        snapshot = canonical.canonical_snapshot_for_uf(uf)
    except Exception as exc:
        return {"ok": False, "state": "unavailable", "car_code": code, "detail": str(exc), "source": sicar.DATASET}
    if snapshot is None:
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_unavailable", "source": sicar.DATASET,
            "user_message": f"Base do CAR de {canonical.UF_NAMES.get(uf, uf)}: indisponível.",
            "snapshot_scope": "uf_canonical",
        }
    freshness = _freshness_fields(uf, snapshot)
    out = _ORIGINAL_QUERY(code, client=client)
    if not out.get("ok") and out.get("detail") == "car_not_found_in_basedosdados":
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "car_not_present_in_canonical_snapshot", "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(), "snapshot_scope": "uf_canonical",
            "user_message": f"Este imóvel não consta na base de {canonical.UF_NAMES.get(uf, uf)} de {canonical.date_pt(snapshot)}.",
            **freshness,
        }
    out = dict(out)
    out["snapshot_scope"] = "uf_canonical"
    out.update(freshness)
    if out.get("ok") and str(out.get("snapshot") or "") != snapshot.isoformat():
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_runtime_mismatch", "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(), "snapshot_scope": "uf_canonical",
            **freshness,
        }
    return out


sicar._fetch_property = _fetch_property_canonical
sicar.build_integrity_from_records = _build_integrity_canonical
sicar.query_car_integrity_v47 = query_car_integrity_v48
print("RX_SICAR_CANONICAL_SNAPSHOT_V48=single_normalized_geometry_contract_fail_closed", flush=True)

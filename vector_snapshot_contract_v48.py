from __future__ import annotations

"""V48 canonical SICAR snapshot contract for vector-map pilot.

This module intentionally does not mutate the V47 production path. It creates
an isolated V48 path where map and analysis are both pinned to one exact SICAR
snapshot. There is no MAX(data_extracao), latest-date lookup, or date fallback.
"""

import datetime as dt
from typing import Any

import sicar_integrity_v47 as sicar
import sicar_overlap_hardening_v47  # noqa: F401 - keeps duplicate geometry hardening active

SNAPSHOT_DATE = dt.date(2026, 8, 4)
SNAPSHOT_ID = "sicar-2026-08-04"
SOURCE = sicar.DATASET
SCHEMA_VERSION = "v48-vector-manifest-1"


class SnapshotContractError(RuntimeError):
    pass


def canonical_manifest(*, status: str = "pilot", uf_versions: dict[str, str] | None = None,
                       uf_fingerprints: dict[str, str] | None = None,
                       overview_version: str | None = None,
                       generated_at: str | None = None,
                       published_at: str | None = None) -> dict[str, Any]:
    snapshot = SNAPSHOT_DATE.isoformat()
    return {
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_date": snapshot,
        "source": "SICAR / Base dos Dados",
        "source_version": snapshot,
        "analysis_dataset": SOURCE,
        "analysis_snapshot": snapshot,
        "map_snapshot": snapshot,
        "overview_version": overview_version,
        "uf_versions": dict(uf_versions or {}),
        "uf_fingerprints": dict(uf_fingerprints or {}),
        "generated_at": generated_at,
        "published_at": published_at,
        "schema_version": SCHEMA_VERSION,
        "status": status,
    }


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    required = {
        "snapshot_id", "snapshot_date", "source", "source_version",
        "analysis_dataset", "analysis_snapshot", "map_snapshot",
        "overview_version", "uf_versions", "uf_fingerprints", "generated_at",
        "published_at", "schema_version", "status",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise SnapshotContractError(f"manifest_missing_fields:{','.join(missing)}")
    target = SNAPSHOT_DATE.isoformat()
    if manifest.get("analysis_dataset") != SOURCE:
        raise SnapshotContractError("manifest_analysis_dataset_mismatch")
    if manifest.get("snapshot_date") != target:
        raise SnapshotContractError("manifest_snapshot_date_mismatch")
    if manifest.get("analysis_snapshot") != target:
        raise SnapshotContractError("manifest_analysis_snapshot_mismatch")
    if manifest.get("map_snapshot") != target:
        raise SnapshotContractError("manifest_map_snapshot_mismatch")
    if manifest.get("analysis_snapshot") != manifest.get("map_snapshot"):
        raise SnapshotContractError("manifest_map_analysis_snapshot_divergence")
    if manifest.get("snapshot_id") != SNAPSHOT_ID:
        raise SnapshotContractError("manifest_snapshot_id_mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotContractError("manifest_schema_version_mismatch")
    return manifest


def property_at_snapshot_sql() -> str:
    return f"""
    WITH exact_rows AS (
      SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area,
             data_atualizacao, geometria
      FROM `{SOURCE}.area_imovel`
      WHERE sigla_uf=@uf
        AND id_imovel=@car_code
        AND data_extracao=@snapshot
    ), attrs AS (
      SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area
      FROM exact_rows
      ORDER BY data_atualizacao DESC NULLS LAST
      LIMIT 1
    ), geom AS (
      SELECT ST_UNION_AGG(geometria) AS geometria
      FROM exact_rows
      WHERE geometria IS NOT NULL
    )
    SELECT attrs.data_extracao, attrs.sigla_uf, attrs.id_municipio,
           attrs.id_imovel, attrs.area,
           ST_ASGEOJSON(geom.geometria) AS geometry_geojson
    FROM attrs CROSS JOIN geom
    WHERE geom.geometria IS NOT NULL
    """


def fetch_property_at_snapshot(client: Any, car_code: str, uf: str,
                               snapshot: dt.date = SNAPSHOT_DATE) -> dict[str, Any] | None:
    if snapshot != SNAPSHOT_DATE:
        raise SnapshotContractError("noncanonical_snapshot_requested")
    rows = sicar._query(
        client,
        property_at_snapshot_sql(),
        {"uf": uf, "car_code": car_code, "snapshot": snapshot},
    )
    return rows[0] if rows else None


def query_car_integrity_v48(car_code: str, *, client: Any | None = None,
                            manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if manifest is None:
        manifest = canonical_manifest()
    try:
        validate_manifest(manifest)
    except Exception as exc:
        return {
            "ok": False,
            "state": "unavailable",
            "car_code": code,
            "detail": f"snapshot_contract:{type(exc).__name__}:{str(exc)[:180]}",
            "source": SOURCE,
        }
    if not sicar.CAR_RE.match(code):
        return {"ok": False, "state": "invalid", "car_code": code,
                "detail": "invalid_car_format", "source": SOURCE}

    uf = code[:2]
    own_client = client
    try:
        if own_client is None:
            own_client = sicar._client()
        prop = fetch_property_at_snapshot(own_client, code, uf, SNAPSHOT_DATE)
        if not prop:
            return {
                "ok": False,
                "state": "unavailable",
                "car_code": code,
                "detail": "car_not_found_in_canonical_snapshot",
                "source": SOURCE,
                "snapshot": SNAPSHOT_DATE.isoformat(),
            }
        themes = sicar._fetch_themes(own_client, code, uf, SNAPSHOT_DATE)
        overlap = sicar._fetch_overlap(own_client, code, uf, SNAPSHOT_DATE)
        municipality_table = sicar._configured_boundary_table("RX_ADMIN_MUNICIPALITY_TABLE")
        uf_table = sicar._configured_boundary_table("RX_ADMIN_UF_TABLE")
        municipality = sicar._fetch_boundary(
            own_client, municipality_table, "id_municipio",
            str(prop.get("id_municipio") or ""), uf,
        )
        uf_boundary = sicar._fetch_boundary(own_client, uf_table, "sigla_uf", uf, uf)
        out = sicar.build_integrity_from_records(
            code, prop, themes, overlap, municipality, uf_boundary
        )
        actual = str(out.get("snapshot") or "")
        expected = SNAPSHOT_DATE.isoformat()
        if actual != expected:
            return {
                "ok": False,
                "state": "unavailable",
                "car_code": code,
                "detail": f"analysis_snapshot_divergence:{actual}:{expected}",
                "source": SOURCE,
                "snapshot": actual or None,
            }
        out["snapshot_contract"] = {
            "snapshot_id": SNAPSHOT_ID,
            "analysis_snapshot": expected,
            "map_snapshot": expected,
            "equal": True,
        }
        return out
    except Exception as exc:
        return {
            "ok": False,
            "state": "unavailable",
            "car_code": code,
            "detail": f"{type(exc).__name__}:{str(exc)[:220]}",
            "source": SOURCE,
            "snapshot": SNAPSHOT_DATE.isoformat(),
        }


def assert_contract_static() -> None:
    sql = property_at_snapshot_sql()
    assert "data_extracao=@snapshot" in sql
    assert "MAX(data_extracao)" not in sql.upper()
    assert "ST_UNION_AGG(geometria)" in sql
    manifest = validate_manifest(canonical_manifest())
    assert manifest["analysis_snapshot"] == manifest["map_snapshot"] == "2026-08-04"
    divergent = canonical_manifest()
    divergent["map_snapshot"] = "2026-09-01"
    try:
        validate_manifest(divergent)
    except SnapshotContractError:
        pass
    else:
        raise AssertionError("divergent map snapshot was accepted")


assert_contract_static()
print("RX_V48_SNAPSHOT_CONTRACT=2026-08-04_MAP_EQUALS_ANALYSIS_FAIL_CLOSED", flush=True)

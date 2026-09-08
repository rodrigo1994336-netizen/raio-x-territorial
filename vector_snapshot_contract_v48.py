from __future__ import annotations

"""V48 SICAR vector contract: canonical snapshot is selected per UF.

There is no national snapshot date and no latest-date lookup by CAR. For each UF,
the immutable content-addressed manifest supplies one canonical date. Map and
analysis must both use exactly that date. Missing UF/date/CAR fails closed.
"""

import datetime as dt
from typing import Any

import sicar_integrity_v47 as sicar
import sicar_overlap_hardening_v47  # noqa: F401
import sicar_canonical_manifest_v48 as canonical

SOURCE = sicar.DATASET
SCHEMA_VERSION = "v48-vector-manifest-2"
SNAPSHOT_SCOPE = "uf_canonical"


class SnapshotContractError(RuntimeError):
    pass


def _canonical_date(uf: str) -> dt.date:
    try:
        snapshot = canonical.canonical_snapshot_for_uf(uf)
    except Exception as exc:
        raise SnapshotContractError(str(exc)) from exc
    if snapshot is None:
        raise SnapshotContractError(f"canonical_snapshot_unavailable:{uf}")
    return snapshot


def canonical_manifest(uf: str, *, status: str = "candidate",
                       uf_version: str | None = None,
                       uf_fingerprint: str | None = None,
                       generated_at: str | None = None,
                       published_at: str | None = None) -> dict[str, Any]:
    code = str(uf or "").upper().strip()
    snapshot = _canonical_date(code).isoformat()
    return {
        "snapshot_scope": SNAPSHOT_SCOPE,
        "uf": code,
        "snapshot_id": f"sicar-{code.lower()}-{snapshot}",
        "snapshot_date": snapshot,
        "source": "SICAR / Base dos Dados",
        "source_version": snapshot,
        "analysis_dataset": SOURCE,
        "analysis_snapshot": snapshot,
        "map_snapshot": snapshot,
        "uf_version": uf_version,
        "uf_fingerprint": uf_fingerprint,
        "generated_at": generated_at,
        "published_at": published_at,
        "schema_version": SCHEMA_VERSION,
        "status": status,
    }


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    required = {
        "snapshot_scope", "uf", "snapshot_id", "snapshot_date", "source",
        "source_version", "analysis_dataset", "analysis_snapshot", "map_snapshot",
        "uf_version", "uf_fingerprint", "generated_at", "published_at",
        "schema_version", "status",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise SnapshotContractError(f"manifest_missing_fields:{','.join(missing)}")
    uf = str(manifest.get("uf") or "").upper().strip()
    target = _canonical_date(uf).isoformat()
    if manifest.get("snapshot_scope") != SNAPSHOT_SCOPE:
        raise SnapshotContractError("manifest_snapshot_scope_mismatch")
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
    if manifest.get("snapshot_id") != f"sicar-{uf.lower()}-{target}":
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
                               snapshot: dt.date | None = None) -> dict[str, Any] | None:
    code = str(uf or "").upper().strip()
    expected = _canonical_date(code)
    if snapshot is not None and snapshot != expected:
        raise SnapshotContractError("noncanonical_snapshot_requested")
    rows = sicar._query(
        client,
        property_at_snapshot_sql(),
        {"uf": code, "car_code": car_code, "snapshot": expected},
    )
    return rows[0] if rows else None


def query_car_integrity_v48(car_code: str, *, client: Any | None = None,
                            manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not sicar.CAR_RE.match(code):
        return {"ok": False, "state": "invalid", "car_code": code,
                "detail": "invalid_car_format", "source": SOURCE}
    uf = code[:2]
    try:
        if manifest is None:
            manifest = canonical_manifest(uf)
        validate_manifest(manifest)
        snapshot = _canonical_date(uf)
    except Exception as exc:
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": f"snapshot_contract:{type(exc).__name__}:{str(exc)[:180]}",
            "source": SOURCE,
        }

    own_client = client
    try:
        if own_client is None:
            own_client = sicar._client()
        prop = fetch_property_at_snapshot(own_client, code, uf, snapshot)
        if not prop:
            return {
                "ok": False, "state": "unavailable", "car_code": code,
                "detail": "car_not_present_in_canonical_snapshot", "source": SOURCE,
                "snapshot": snapshot.isoformat(), "snapshot_scope": SNAPSHOT_SCOPE,
                "user_message": f"Este imóvel não consta na base de {canonical.UF_NAMES.get(uf, uf)} de {canonical.date_pt(snapshot)}.",
            }
        themes = sicar._fetch_themes(own_client, code, uf, snapshot)
        overlap = sicar._fetch_overlap(own_client, code, uf, snapshot)
        municipality_table = sicar._configured_boundary_table("RX_ADMIN_MUNICIPALITY_TABLE")
        uf_table = sicar._configured_boundary_table("RX_ADMIN_UF_TABLE")
        municipality = sicar._fetch_boundary(
            own_client, municipality_table, "id_municipio",
            str(prop.get("id_municipio") or ""), uf,
        )
        uf_boundary = sicar._fetch_boundary(own_client, uf_table, "sigla_uf", uf, uf)
        out = sicar.build_integrity_from_records(code, prop, themes, overlap, municipality, uf_boundary)
        actual = str(out.get("snapshot") or "")
        expected = snapshot.isoformat()
        if actual != expected:
            return {
                "ok": False, "state": "unavailable", "car_code": code,
                "detail": f"analysis_snapshot_divergence:{actual}:{expected}",
                "source": SOURCE, "snapshot": actual or None,
            }
        out["snapshot_scope"] = SNAPSHOT_SCOPE
        out["snapshot_label"] = canonical.base_label(uf, snapshot)
        out["snapshot_contract"] = {
            "uf": uf,
            "analysis_snapshot": expected,
            "map_snapshot": expected,
            "equal": True,
        }
        return out
    except Exception as exc:
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": f"{type(exc).__name__}:{str(exc)[:220]}",
            "source": SOURCE, "snapshot": snapshot.isoformat(),
        }


def assert_contract_static() -> None:
    sql = property_at_snapshot_sql()
    assert "data_extracao=@snapshot" in sql
    assert "MAX(DATA_EXTRACAO)" not in sql.upper()
    assert "ST_UNION_AGG(geometria)" in sql
    assert SNAPSHOT_SCOPE == "uf_canonical"
    assert "2026-08-04" not in canonical_manifest.__doc__ if canonical_manifest.__doc__ else True


assert_contract_static()
print("RX_V48_SNAPSHOT_CONTRACT=PER_UF_MAP_EQUALS_ANALYSIS_FAIL_CLOSED", flush=True)

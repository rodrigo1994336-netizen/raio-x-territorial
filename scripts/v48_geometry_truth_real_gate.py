from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sicar_canonical_manifest_v48 as canonical
import sicar_geometry_normalization_v48 as contract

PROJECT = "metodo-afp-plataforma"
SOURCE_TABLE = "basedosdados.br_sfb_sicar.area_imovel"
MAX_BYTES_PER_QUERY = 2 * 1024**3
OUT = REPO_ROOT / "artifacts" / "v48_geometry_truth_real_gate.json"


def die(message: str) -> None:
    raise RuntimeError(message)


def case_sql() -> str:
    return f"""
    WITH source_rows AS (
      SELECT id_imovel, data_atualizacao, geometria
      FROM `{SOURCE_TABLE}`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot AND id_imovel=@car_code
    ), per_car_source AS (
      SELECT
        id_imovel,
        COUNT(*) AS source_row_count,
        COUNTIF(geometria IS NOT NULL) AS geometry_row_count,
        ST_UNION_AGG(geometria) AS source_geometry
      FROM source_rows
      GROUP BY id_imovel
    )
    {contract.normalization_ctes('per_car_source')}
    SELECT
      id_imovel,
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
      normalization_applied
    FROM normalized_geometry_metrics
    """


def ma_no_geometry_sql() -> str:
    return f"""
    WITH source_rows AS (
      SELECT id_imovel, geometria
      FROM `{SOURCE_TABLE}`
      WHERE sigla_uf='MA' AND data_extracao=@snapshot
    ), per_car_source AS (
      SELECT
        id_imovel,
        COUNT(*) AS source_row_count,
        COUNTIF(geometria IS NOT NULL) AS geometry_row_count,
        ST_UNION_AGG(geometria) AS source_geometry
      FROM source_rows
      GROUP BY id_imovel
    )
    {contract.normalization_ctes('per_car_source')}
    SELECT
      id_imovel,
      source_row_count,
      geometry_row_count,
      source_geometry_type,
      render_geometry_type,
      source_geometry_fingerprint,
      render_geometry_fingerprint,
      has_no_polygonal_component
    FROM normalized_geometry_metrics
    WHERE geometry_row_count=0
    ORDER BY id_imovel
    """


def query_with_guard(client: Any, bigquery: Any, sql: str, params: list[Any]) -> tuple[list[Any], dict[str, int]]:
    dry_cfg = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry = client.query(sql, job_config=dry_cfg)
    dry_bytes = int(dry.total_bytes_processed or 0)
    if dry_bytes > MAX_BYTES_PER_QUERY:
        die(f"real_gate_dryrun_guard:{dry_bytes}>{MAX_BYTES_PER_QUERY}")
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        use_query_cache=False,
        maximum_bytes_billed=MAX_BYTES_PER_QUERY,
    )
    job = client.query(sql, job_config=cfg)
    rows = list(job.result(timeout=1800))
    return rows, {
        "dry_run_bytes": dry_bytes,
        "bytes_processed": int(job.total_bytes_processed or 0),
        "bytes_billed": int(job.total_bytes_billed or 0),
    }


def row_payload(row: Any) -> dict[str, Any]:
    keys = (
        "id_imovel",
        "source_row_count",
        "geometry_row_count",
        "source_geometry_type",
        "render_geometry_type",
        "source_geometry_fingerprint",
        "render_geometry_fingerprint",
        "discarded_line_components",
        "discarded_line_length_m",
        "discarded_point_components",
        "polygon_area_before_m2",
        "polygon_area_after_m2",
        "polygon_area_difference_m2",
        "polygon_area_tolerance_m2",
        "has_no_polygonal_component",
        "discarded_nonpolygon_components",
        "normalization_applied",
    )
    out: dict[str, Any] = {}
    for key in keys:
        try:
            value = row[key]
        except Exception:
            continue
        if isinstance(value, (dt.date, dt.datetime)):
            value = value.isoformat()
        out[key] = value
    return out


def main() -> None:
    from google.cloud import bigquery

    if PROJECT != "metodo-afp-plataforma":
        die("project_contract_changed")
    manifest = canonical.load_manifest()
    if len(manifest.get("ufs") or {}) != 27:
        die("canonical_manifest_not_27_ufs")

    provenance = contract.geometrycollection_sentinel_provenance()
    if contract.GEOMETRYCOLLECTION_REGRESSION_UF != "MG":
        die("geometrycollection_sentinel_uf_must_be_mg")
    if not re.fullmatch(r"MG-\d{7}-[0-9A-F]{32}", contract.GEOMETRYCOLLECTION_REGRESSION_CAR):
        die("geometrycollection_sentinel_invalid_car_format")
    if provenance.get("source_audit_run_id") != 34519865245:
        die("geometrycollection_sentinel_audit_run_provenance_drift")
    if provenance.get("source_audit_content_fingerprint_sha256") != "2c718fe0c6f5798430f65eb32a9bb9a9e0b51710ee6de34ce2dd72aac83830cd":
        die("geometrycollection_sentinel_audit_fingerprint_provenance_drift")
    if provenance.get("discovery_run_id") != 34527676691:
        die("geometrycollection_sentinel_discovery_run_provenance_drift")
    if provenance.get("discovery_content_fingerprint_sha256") != "3ab26311d90b56e0d57a40680d3d1c363431810c251c2f7bd99554edb62c2ed1":
        die("geometrycollection_sentinel_discovery_fingerprint_provenance_drift")

    client = bigquery.Client(project=PROJECT)
    cases = (
        (
            "curvelo_polygon",
            "MG",
            contract.CURVELO_POLYGON_REGRESSION_SNAPSHOT,
            contract.CURVELO_POLYGON_REGRESSION_CAR,
        ),
        (
            "real_geometrycollection",
            contract.GEOMETRYCOLLECTION_REGRESSION_UF,
            contract.GEOMETRYCOLLECTION_REGRESSION_SNAPSHOT,
            contract.GEOMETRYCOLLECTION_REGRESSION_CAR,
        ),
    )
    evidence: dict[str, Any] = {}
    total_dry = total_processed = total_billed = 0

    for name, uf, snapshot_text, car in cases:
        canonical_snapshot = canonical.canonical_snapshot_for_uf(uf)
        if canonical_snapshot is None or canonical_snapshot.isoformat() != snapshot_text:
            die(f"sentinel_snapshot_not_canonical:{name}:{canonical_snapshot}!={snapshot_text}")
        params = [
            bigquery.ScalarQueryParameter("uf", "STRING", uf),
            bigquery.ScalarQueryParameter("snapshot", "DATE", dt.date.fromisoformat(snapshot_text)),
            bigquery.ScalarQueryParameter("car_code", "STRING", car),
        ]
        rows, usage = query_with_guard(client, bigquery, case_sql(), params)
        total_dry += usage["dry_run_bytes"]
        total_processed += usage["bytes_processed"]
        total_billed += usage["bytes_billed"]
        if len(rows) != 1:
            die(f"sentinel_not_exactly_one:{name}:{len(rows)}")
        payload = row_payload(rows[0])
        payload.update({"uf": uf, "snapshot": snapshot_text, "usage": usage})
        evidence[name] = payload

    curvelo = evidence["curvelo_polygon"]
    if curvelo.get("source_geometry_type") != "ST_Polygon":
        die(f"curvelo_source_type_changed:{curvelo.get('source_geometry_type')}")
    if curvelo.get("render_geometry_type") != "ST_Polygon":
        die(f"curvelo_render_type_changed:{curvelo.get('render_geometry_type')}")
    if bool(curvelo.get("normalization_applied")):
        die("curvelo_must_not_be_marked_normalized")
    if int(curvelo.get("geometry_row_count") or 0) <= 0:
        die("curvelo_geometry_missing")

    mixed = evidence["real_geometrycollection"]
    if mixed.get("source_geometry_type") != "ST_GeometryCollection":
        die(f"real_gc_source_type_changed:{mixed.get('source_geometry_type')}")
    if mixed.get("render_geometry_type") not in {"ST_Polygon", "ST_MultiPolygon"}:
        die(f"real_gc_render_type_invalid:{mixed.get('render_geometry_type')}")
    if not bool(mixed.get("normalization_applied")):
        die("real_gc_normalization_not_applied")
    if not bool(mixed.get("discarded_nonpolygon_components")):
        die("real_gc_nonpolygon_discard_not_declared")
    discarded_parts = int(mixed.get("discarded_line_components") or 0) + int(mixed.get("discarded_point_components") or 0)
    if discarded_parts <= 0:
        die("real_gc_discard_metrics_empty")
    if not mixed.get("source_geometry_fingerprint") or not mixed.get("render_geometry_fingerprint"):
        die("real_gc_dual_fingerprints_missing")
    if mixed.get("source_geometry_fingerprint") == mixed.get("render_geometry_fingerprint"):
        die("real_gc_source_render_fingerprints_unexpectedly_equal")
    if float(mixed.get("polygon_area_difference_m2") or 0.0) > float(mixed.get("polygon_area_tolerance_m2") or 0.0):
        die("real_gc_polygon_area_invariant_failed")

    ma_snapshot = canonical.canonical_snapshot_for_uf("MA")
    if ma_snapshot is None:
        die("ma_canonical_snapshot_missing")
    ma_params = [bigquery.ScalarQueryParameter("snapshot", "DATE", ma_snapshot)]
    ma_rows, ma_usage = query_with_guard(client, bigquery, ma_no_geometry_sql(), ma_params)
    total_dry += ma_usage["dry_run_bytes"]
    total_processed += ma_usage["bytes_processed"]
    total_billed += ma_usage["bytes_billed"]
    if len(ma_rows) != 1:
        die(f"ma_no_geometry_expected_1_found_{len(ma_rows)}")
    ma = row_payload(ma_rows[0])
    if int(ma.get("source_row_count") or 0) <= 0 or int(ma.get("geometry_row_count") or -1) != 0:
        die("ma_no_geometry_classification_invalid")
    if ma.get("source_geometry_type") is not None or ma.get("render_geometry_type") is not None:
        die("ma_no_geometry_must_not_gain_geometry_type")
    ma.update({
        "uf": "MA",
        "snapshot": ma_snapshot.isoformat(),
        "user_message": contract.no_geometry_user_message(canonical.date_pt(ma_snapshot)),
        "usage": ma_usage,
    })
    evidence["ma_no_geometry"] = ma

    payload = {
        "schema_version": "v48-geometry-truth-real-gate-2",
        **contract.normalization_contract_fields(),
        "geometrycollection_sentinel_provenance": provenance,
        "project": PROJECT,
        "sentinels": evidence,
        "total_dry_run_bytes": total_dry,
        "total_bytes_processed": total_processed,
        "total_bytes_billed": total_billed,
        "national_generation_executed": False,
        "active_json_updated": False,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    payload["content_fingerprint_sha256"] = hashlib.sha256(raw).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")

    print("RX_V48_GEOMETRY_TRUTH_REAL_GATE=PASS")
    print(f"RX_V48_REAL_CURVELO={curvelo['id_imovel']}:{curvelo['source_geometry_type']}->{curvelo['render_geometry_type']}")
    print(f"RX_V48_REAL_GEOMETRYCOLLECTION={mixed['id_imovel']}:{mixed['source_geometry_type']}->{mixed['render_geometry_type']}")
    print(f"RX_V48_REAL_GEOMETRYCOLLECTION_SOURCE_AUDIT_RUN={provenance['source_audit_run_id']}")
    print(f"RX_V48_REAL_GEOMETRYCOLLECTION_SOURCE_AUDIT_FINGERPRINT={provenance['source_audit_content_fingerprint_sha256']}")
    print(f"RX_V48_REAL_GEOMETRYCOLLECTION_DISCOVERY_RUN={provenance['discovery_run_id']}")
    print(f"RX_V48_REAL_MA_NO_GEOMETRY={ma['id_imovel']}")
    print(f"RX_V48_GEOMETRY_TRUTH_TOTAL_BILLED_BYTES={total_billed}")
    print(f"RX_V48_GEOMETRY_TRUTH_FINGERPRINT={payload['content_fingerprint_sha256']}")
    print("RX_V48_NATIONAL_GENERATION_EXECUTED=NO")


if __name__ == "__main__":
    main()

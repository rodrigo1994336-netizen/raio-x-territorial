from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT = "metodo-afp-plataforma"
SOURCE_TABLE = "basedosdados.br_sfb_sicar.area_imovel"
MAX_BYTES_PER_UF = 2 * 1024**3
SCHEMA_VERSION = "v48-canonical-geometry-type-audit-2"
OUT = REPO_ROOT / "artifacts" / "v48_geometry_type_audit.json"
CURVELO_CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
V47_PRODUCTION_COMMIT = "ee11f50b5db669b80977bdecb2258bebcf26065c"
V47_EXPORT_PANEL_BLOB = "67fcc022c8fe763f5fc282972b7f066aa7d3b3fb"
V47_CAR_RESILIENT_BLOB = "300b5c742f0c63dad58b2ed8ce3534a7d9135a46"


def die(message: str) -> None:
    raise RuntimeError(message)


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def current_export_semantics() -> dict[str, Any]:
    panel_path = REPO_ROOT / "portal_map_panel_v45.py"
    resolver_path = REPO_ROOT / "car_resilient.py"
    panel = panel_path.read_text(encoding="utf-8")
    resolver = resolver_path.read_text(encoding="utf-8")
    panel_blob = git_blob_sha1(panel_path)
    resolver_blob = git_blob_sha1(resolver_path)

    if panel_blob != V47_EXPORT_PANEL_BLOB:
        die(f"v47_export_panel_blob_changed:{panel_blob}")
    if resolver_blob != V47_CAR_RESILIENT_BLOB:
        die(f"v47_car_resilient_blob_changed:{resolver_blob}")
    required_panel_tokens = (
        "function coordsKml(g){",
        "if(g.type==='Polygon')",
        "if(g.type==='MultiPolygon')",
        "const body=coordsKml(g);if(!body)return;",
        "walk(g.coordinates);return out",
        "const g=geometry(),pts=flatten(g);if(!pts.length)return;",
    )
    for token in required_panel_tokens:
        if token not in panel:
            die(f"v47_export_contract_token_missing:{token}")
    if "'geometry':f.get('geometry')" not in resolver:
        die("v47_wfs_geometry_passthrough_contract_missing")

    return {
        "production_commit": V47_PRODUCTION_COMMIT,
        "portal_map_panel_git_blob_sha1": panel_blob,
        "car_resilient_git_blob_sha1": resolver_blob,
        "geometry_source": "SICAR/WFS GeoJSON via fetch_car_live_resilient; not the BigQuery canonical analysis geometry",
        "wfs_geometry_normalized_before_export": False,
        "cross_source_equivalence_to_bigquery_proven": False,
        "kml_supported_geometry_types": ["Polygon", "MultiPolygon"],
        "geometrycollection_kml_standard_geojson": "no_file_generated: coordsKml returns empty and downloadKml returns",
        "png_drawn_geometry_types": ["Polygon", "MultiPolygon"],
        "geometrycollection_png_standard_geojson": "no_file_generated: GeometryCollection uses geometries, not coordinates; flatten yields zero points and downloadPng returns",
        "geometrycollection_nonpolygon_parts_exported_as_perimeter": False,
        "failure_mode_for_standard_geometrycollection": "silent_no_download",
    }


def query_sql() -> str:
    return f"""
    WITH base AS (
      SELECT id_imovel, data_atualizacao, geometria
      FROM `{SOURCE_TABLE}`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot
    ), per_car AS (
      SELECT id_imovel, ST_UNION_AGG(geometria) AS geometria
      FROM base
      WHERE geometria IS NOT NULL
      GROUP BY id_imovel
    ), type_counts AS (
      SELECT ST_GEOMETRYTYPE(geometria) AS geometry_type, COUNT(*) AS car_count
      FROM per_car
      GROUP BY geometry_type
    ), source_summary AS (
      SELECT
        COUNT(*) AS source_row_count,
        COUNT(DISTINCT id_imovel) AS distinct_car_count,
        COUNT(DISTINCT IF(geometria IS NOT NULL, id_imovel, NULL)) AS cars_with_geometry,
        COUNTIF(geometria IS NULL) AS null_geometry_rows
      FROM base
    )
    SELECT
      t.geometry_type,
      t.car_count,
      s.source_row_count,
      s.distinct_car_count,
      s.cars_with_geometry,
      s.null_geometry_rows,
      (SELECT COUNT(*) FROM base WHERE id_imovel=@curvelo_car) AS curvelo_source_row_count,
      (
        SELECT ST_GEOMETRYTYPE(geometria)
        FROM base
        WHERE id_imovel=@curvelo_car AND geometria IS NOT NULL
        ORDER BY data_atualizacao DESC NULLS LAST
        LIMIT 1
      ) AS curvelo_v47_selected_row_geometry_type,
      (
        SELECT ST_GEOMETRYTYPE(geometria)
        FROM per_car
        WHERE id_imovel=@curvelo_car
        LIMIT 1
      ) AS curvelo_post_union_geometry_type
    FROM type_counts t
    CROSS JOIN source_summary s
    ORDER BY t.geometry_type
    """


def static_contract() -> None:
    sql = query_sql().upper()
    if PROJECT != "metodo-afp-plataforma":
        die("project_contract_changed")
    if MAX_BYTES_PER_UF != 2 * 1024**3:
        die("per_uf_guard_changed")
    if not CURVELO_CAR.startswith("MG-3120904-"):
        die("curvelo_reference_changed")
    if "DATA_EXTRACAO=@SNAPSHOT" not in sql or "SIGLA_UF=@UF" not in sql:
        die("canonical_predicate_missing")
    if "ST_UNION_AGG(GEOMETRIA)" not in sql or "ST_GEOMETRYTYPE(GEOMETRIA)" not in sql:
        die("post_union_geometry_type_contract_missing")
    if "@CURVELO_CAR" not in sql or "CURVELO_V47_SELECTED_ROW_GEOMETRY_TYPE" not in sql or "CURVELO_POST_UNION_GEOMETRY_TYPE" not in sql:
        die("curvelo_geometry_type_contract_missing")
    if "ST_ASGEOJSON" in sql or "ST_ASTEXT" in sql or "ST_ASBINARY" in sql:
        die("full_geometry_serialization_forbidden")
    current_export_semantics()


def run() -> dict[str, Any]:
    from google.cloud import bigquery
    import sicar_canonical_manifest_v48 as canonical

    manifest = canonical.load_manifest()
    entries = manifest.get("ufs") or {}
    if len(entries) != 27:
        die(f"expected_27_ufs_found_{len(entries)}")
    if str(entries["SP"].get("snapshot")) != "2026-06-02":
        die("sp_canonical_snapshot_changed")
    if str(entries["MG"].get("snapshot")) != "2026-08-04":
        die("mg_canonical_snapshot_changed")

    client = bigquery.Client(project=PROJECT)
    plans: dict[str, dict[str, Any]] = {}
    results: dict[str, Any] = {}
    national_types: dict[str, int] = {}
    total_dry = total_processed = total_billed = 0
    curvelo_reference: dict[str, Any] | None = None

    # Phase 1: dry-run every UF before any real query.
    for uf in sorted(entries):
        entry = entries[uf]
        if entry.get("status") != "canonical":
            die(f"noncanonical_uf:{uf}")
        snapshot_text = str(entry.get("snapshot") or "")
        snapshot = dt.date.fromisoformat(snapshot_text)
        expected_rows = int((((entry.get("tables") or {}).get("area_imovel") or {}).get("row_count")) or 0)
        params = [
            bigquery.ScalarQueryParameter("uf", "STRING", uf),
            bigquery.ScalarQueryParameter("snapshot", "DATE", snapshot),
            bigquery.ScalarQueryParameter("curvelo_car", "STRING", CURVELO_CAR),
        ]
        dry_cfg = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
        dry_job = client.query(query_sql(), job_config=dry_cfg)
        dry_bytes = int(dry_job.total_bytes_processed or 0)
        total_dry += dry_bytes
        plans[uf] = {
            "snapshot": snapshot_text,
            "expected_rows": expected_rows,
            "params": params,
            "dry_run_bytes": dry_bytes,
            "within_guard": dry_bytes <= MAX_BYTES_PER_UF,
        }
        print(f"RX_V48_GEOMTYPE_DRYRUN uf={uf} snapshot={snapshot_text} bytes={dry_bytes} within_guard={'YES' if dry_bytes <= MAX_BYTES_PER_UF else 'NO'}")

    # Phase 2: execute only UFs whose own dry-run is within the approved guard.
    for uf in sorted(plans):
        plan = plans[uf]
        if not plan["within_guard"]:
            results[uf] = {
                "status": "guard_exceeded_no_real_query",
                "snapshot": plan["snapshot"],
                "manifest_area_imovel_row_count": plan["expected_rows"],
                "dry_run_bytes": plan["dry_run_bytes"],
                "bytes_processed": 0,
                "bytes_billed": 0,
            }
            continue

        cfg = bigquery.QueryJobConfig(
            query_parameters=plan["params"],
            use_legacy_sql=False,
            use_query_cache=False,
            maximum_bytes_billed=MAX_BYTES_PER_UF,
        )
        job = client.query(query_sql(), job_config=cfg)
        rows = list(job.result(timeout=1800))
        if not rows:
            die(f"geometry_type_empty:{uf}")
        source_row_count = int(rows[0]["source_row_count"] or 0)
        distinct_car_count = int(rows[0]["distinct_car_count"] or 0)
        cars_with_geometry = int(rows[0]["cars_with_geometry"] or 0)
        null_rows = int(rows[0]["null_geometry_rows"] or 0)
        types = {str(r["geometry_type"]): int(r["car_count"] or 0) for r in rows}
        typed_car_count = sum(types.values())
        if typed_car_count != cars_with_geometry:
            die(f"geometry_type_count_reconciliation_failed:{uf}:{typed_car_count}!={cars_with_geometry}")
        for typ, count in types.items():
            national_types[typ] = national_types.get(typ, 0) + count
        processed = int(job.total_bytes_processed or 0)
        billed = int(job.total_bytes_billed or 0)
        total_processed += processed
        total_billed += billed
        results[uf] = {
            "status": "counted",
            "snapshot": plan["snapshot"],
            "manifest_area_imovel_row_count": plan["expected_rows"],
            "source_row_count": source_row_count,
            "distinct_car_count": distinct_car_count,
            "cars_with_geometry": cars_with_geometry,
            "untyped_distinct_cars": distinct_car_count - cars_with_geometry,
            "null_geometry_rows": null_rows,
            "geometry_types_after_union_by_car": types,
            "dry_run_bytes": plan["dry_run_bytes"],
            "bytes_processed": processed,
            "bytes_billed": billed,
        }
        print(f"RX_V48_GEOMTYPE_RESULT uf={uf} types={json.dumps(types, sort_keys=True)} distinct_cars={distinct_car_count} cars_with_geometry={cars_with_geometry} null_rows={null_rows}")

        if uf == "MG":
            curvelo_rows = int(rows[0]["curvelo_source_row_count"] or 0)
            selected_type = rows[0]["curvelo_v47_selected_row_geometry_type"]
            union_type = rows[0]["curvelo_post_union_geometry_type"]
            if curvelo_rows <= 0 or not selected_type or not union_type:
                die("curvelo_geometry_type_not_resolved_at_canonical_snapshot")
            curvelo_reference = {
                "car_code": CURVELO_CAR,
                "snapshot": plan["snapshot"],
                "source_row_count": curvelo_rows,
                "v47_selected_row_geometry_type": str(selected_type),
                "post_union_geometry_type": str(union_type),
            }
            print(
                "RX_V48_CURVELO_GEOMETRY_TYPE",
                f"snapshot={plan['snapshot']}",
                f"source_rows={curvelo_rows}",
                f"v47_selected={selected_type}",
                f"post_union={union_type}",
            )

    guard_exceeded = [uf for uf, item in results.items() if item["status"] != "counted"]
    if "MG" in results and results["MG"]["status"] == "counted" and curvelo_reference is None:
        die("curvelo_reference_missing_after_mg_count")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "canonical_manifest_fingerprint": str(manifest.get("content_fingerprint_sha256") or ""),
        "per_uf_guard_bytes": MAX_BYTES_PER_UF,
        "query_semantics": "ST_GEOMETRYTYPE after ST_UNION_AGG per id_imovel at canonical UF snapshot",
        "full_geometry_serialized": False,
        "ufs": results,
        "national_geometry_types_counted_ufs_only": dict(sorted(national_types.items())),
        "guard_exceeded_ufs": guard_exceeded,
        "curvelo_reference": curvelo_reference or {
            "car_code": CURVELO_CAR,
            "status": "not_counted_because_mg_guard_exceeded",
        },
        "v47_current_kml_png_semantics": current_export_semantics(),
        "total_dry_run_bytes": total_dry,
        "total_bytes_processed": total_processed,
        "total_bytes_billed": total_billed,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["content_fingerprint_sha256"] = hashlib.sha256(raw).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    counted = sum(1 for x in results.values() if x["status"] == "counted")
    print(f"RX_V48_GEOMTYPE_AUDIT_COUNTED_UFS={counted}/27")
    print(f"RX_V48_GEOMTYPE_AUDIT_GUARD_EXCEEDED={','.join(guard_exceeded) if guard_exceeded else 'NONE'}")
    print(f"RX_V48_GEOMTYPE_AUDIT_TOTAL_BILLED_BYTES={total_billed}")
    print(f"RX_V48_GEOMTYPE_AUDIT_FINGERPRINT={payload['content_fingerprint_sha256']}")
    if guard_exceeded:
        print("RX_V48_GEOMTYPE_AUDIT=PARTIAL_FAIL_CLOSED")
        raise RuntimeError("geometry_type_audit_guard_exceeded:" + ",".join(guard_exceeded))
    print("RX_V48_GEOMTYPE_AUDIT=PASS")
    return payload


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-check", action="store_true")
    args = parser.parse_args()
    static_contract()
    if args.import_check:
        import sicar_canonical_manifest_v48  # noqa: F401
        print("RX_V48_GEOMTYPE_IMPORT_CHECK=PASS")
        return
    run()


if __name__ == "__main__":
    cli()

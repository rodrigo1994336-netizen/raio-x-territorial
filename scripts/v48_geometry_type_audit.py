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
SCHEMA_VERSION = "v48-canonical-geometry-type-audit-1"
OUT = REPO_ROOT / "artifacts" / "v48_geometry_type_audit.json"


def die(message: str) -> None:
    raise RuntimeError(message)


def query_sql() -> str:
    return f"""
    WITH base AS (
      SELECT id_imovel, geometria
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
        COUNTIF(geometria IS NULL) AS null_geometry_rows,
        COUNT(DISTINCT IF(geometria IS NULL, id_imovel, NULL)) AS null_geometry_cars
      FROM base
    )
    SELECT
      t.geometry_type,
      t.car_count,
      s.source_row_count,
      s.null_geometry_rows,
      s.null_geometry_cars
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
    if "DATA_EXTRACAO=@SNAPSHOT" not in sql or "SIGLA_UF=@UF" not in sql:
        die("canonical_predicate_missing")
    if "ST_UNION_AGG(GEOMETRIA)" not in sql or "ST_GEOMETRYTYPE(GEOMETRIA)" not in sql:
        die("post_union_geometry_type_contract_missing")
    if "ST_ASGEOJSON" in sql or "ST_ASTEXT" in sql or "ST_ASBINARY" in sql:
        die("full_geometry_serialization_forbidden")


def run() -> dict[str, Any]:
    from google.cloud import bigquery
    import sicar_canonical_manifest_v48 as canonical

    manifest = canonical.load_manifest()
    entries = manifest.get("ufs") or {}
    if len(entries) != 27:
        die(f"expected_27_ufs_found_{len(entries)}")
    if str(entries["SP"].get("snapshot")) != "2026-06-02":
        die("sp_canonical_snapshot_changed")

    client = bigquery.Client(project=PROJECT)
    plans: dict[str, dict[str, Any]] = {}
    results: dict[str, Any] = {}
    national_types: dict[str, int] = {}
    total_dry = total_processed = total_billed = 0

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
        null_rows = int(rows[0]["null_geometry_rows"] or 0)
        null_cars = int(rows[0]["null_geometry_cars"] or 0)
        types = {str(r["geometry_type"]): int(r["car_count"] or 0) for r in rows}
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
            "null_geometry_rows": null_rows,
            "null_geometry_cars": null_cars,
            "geometry_types_after_union_by_car": types,
            "dry_run_bytes": plan["dry_run_bytes"],
            "bytes_processed": processed,
            "bytes_billed": billed,
        }
        print(f"RX_V48_GEOMTYPE_RESULT uf={uf} types={json.dumps(types, sort_keys=True)} null_rows={null_rows} null_cars={null_cars}")

    guard_exceeded = [uf for uf, item in results.items() if item["status"] != "counted"]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "canonical_manifest_fingerprint": str(manifest.get("content_fingerprint_sha256") or ""),
        "per_uf_guard_bytes": MAX_BYTES_PER_UF,
        "query_semantics": "ST_GEOMETRYTYPE after ST_UNION_AGG per id_imovel at canonical UF snapshot",
        "full_geometry_serialized": False,
        "ufs": results,
        "national_geometry_types_counted_ufs_only": dict(sorted(national_types.items())),
        "guard_exceeded_ufs": guard_exceeded,
        "total_dry_run_bytes": total_dry,
        "total_bytes_processed": total_processed,
        "total_bytes_billed": total_billed,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["content_fingerprint_sha256"] = hashlib.sha256(raw).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"RX_V48_GEOMTYPE_AUDIT_COUNTED_UFS={sum(1 for x in results.values() if x['status'] == 'counted')}/27")
    print(f"RX_V48_GEOMTYPE_AUDIT_GUARD_EXCEEDED={','.join(guard_exceeded) if guard_exceeded else 'NONE'}")
    print(f"RX_V48_GEOMTYPE_AUDIT_TOTAL_BILLED_BYTES={total_billed}")
    print(f"RX_V48_GEOMTYPE_AUDIT_FINGERPRINT={payload['content_fingerprint_sha256']}")
    print("RX_V48_GEOMTYPE_AUDIT=PASS" if not guard_exceeded else "RX_V48_GEOMTYPE_AUDIT=PARTIAL_FAIL_CLOSED")
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

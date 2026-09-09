from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sicar_canonical_manifest_v48 as canonical

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
MAX_BYTES = 2 * 1024**3
EXPECTED_UFS = tuple(sorted(canonical.UF_NAMES))


def fail(message: str) -> None:
    raise AssertionError(message)


def count_sql(dates: list[str]) -> str:
    if not dates:
        fail("canonical_dates_empty")
    literals = ",".join(f"DATE '{value}'" for value in dates)
    return f"""
    SELECT sigla_uf,
           data_extracao,
           COUNT(*) AS row_count,
           COUNT(DISTINCT id_imovel) AS distinct_car_count
    FROM `{DATASET}.area_imovel`
    WHERE data_extracao IN ({literals})
      AND sigla_uf IN UNNEST(@ufs)
    GROUP BY sigla_uf, data_extracao
    ORDER BY sigla_uf, data_extracao
    """


def dry_run(client: Any, bigquery: Any, dates: list[str]) -> int:
    params = [bigquery.ArrayQueryParameter("ufs", "STRING", list(EXPECTED_UFS))]
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        dry_run=True,
        use_query_cache=False,
        use_legacy_sql=False,
    )
    job = client.query(count_sql(dates), job_config=cfg)
    dry_bytes = int(job.total_bytes_processed or 0)
    print(f"RX_V48_CAR_COUNTS_DRYRUN_BYTES={dry_bytes}")
    print(f"RX_V48_CAR_COUNTS_MAX_BYTES_BILLED={MAX_BYTES}")
    print(f"RX_V48_CAR_COUNTS_DRYRUN_WITHIN_GUARD={'YES' if dry_bytes <= MAX_BYTES else 'NO'}")
    return dry_bytes


def execute_real_query(client: Any, bigquery: Any, dates: list[str], dry_bytes: int) -> tuple[list[dict[str, Any]], int, int]:
    if dry_bytes > MAX_BYTES:
        fail(f"canonical CAR count exceeds 2GiB guard:{dry_bytes}>{MAX_BYTES}")
    params = [bigquery.ArrayQueryParameter("ufs", "STRING", list(EXPECTED_UFS))]
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        use_query_cache=False,
        maximum_bytes_billed=MAX_BYTES,
    )
    job = client.query(count_sql(dates), job_config=cfg)
    rows = [dict(row.items()) for row in job.result(timeout=300)]
    processed = int(job.total_bytes_processed or 0)
    billed = int(job.total_bytes_billed or 0)
    print(f"RX_V48_CAR_COUNTS_REAL_QUERY_BYTES_PROCESSED={processed}")
    print(f"RX_V48_CAR_COUNTS_REAL_QUERY_BYTES_BILLED={billed}")
    print("RX_V48_CAR_COUNTS_REAL_QUERY_EXECUTED=YES")
    return rows, processed, billed


def canonical_context() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    manifest = canonical.load_manifest()
    ufs = manifest.get("ufs") or {}
    if set(ufs) != set(EXPECTED_UFS):
        fail("canonical manifest must contain exactly 27 UFs")
    canonical_dates = sorted({
        str(item.get("snapshot"))
        for item in ufs.values()
        if item.get("status") == "canonical"
    })
    return manifest, ufs, canonical_dates


def write_dry_run_evidence(manifest: dict[str, Any], dates: list[str], dry_bytes: int) -> None:
    payload = {
        "schema_version": "v48-sicar-canonical-car-counts-dryrun-1",
        "mode": "dry_run_only",
        "source": f"{DATASET}.area_imovel",
        "canonical_manifest_path": canonical.PINNED_MANIFEST_PATH,
        "canonical_manifest_fingerprint_sha256": manifest.get("content_fingerprint_sha256"),
        "canonical_dates": dates,
        "maximum_bytes_billed": MAX_BYTES,
        "dry_run_bytes": dry_bytes,
        "dry_run_within_guard": dry_bytes <= MAX_BYTES,
        "real_data_query_executed": False,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["content_fingerprint_sha256"] = hashlib.sha256(raw).hexdigest()
    out = Path("artifacts/v48_canonical_car_counts_dryrun.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("RX_V48_CAR_COUNTS_REAL_QUERY_EXECUTED=NO")
    print("RX_V48_CANONICAL_CAR_COUNTS_DRYRUN=PASS")


def build_real_evidence(
    manifest: dict[str, Any],
    ufs: dict[str, Any],
    canonical_dates: list[str],
    rows: list[dict[str, Any]],
    dry_bytes: int,
    processed: int,
    billed: int,
) -> None:
    by_key = {
        (str(row.get("sigla_uf") or "").upper(), str(row.get("data_extracao") or "")): row
        for row in rows
    }
    counts: dict[str, Any] = {}
    national_distinct = 0
    national_rows = 0
    for uf in EXPECTED_UFS:
        item = ufs[uf]
        if item.get("status") != "canonical":
            fail(f"{uf}: canonical snapshot unavailable")
        snapshot = str(item.get("snapshot") or "")
        row = by_key.get((uf, snapshot))
        if not row:
            fail(f"{uf}: no area_imovel rows at canonical snapshot {snapshot}")
        row_count = int(row.get("row_count") or 0)
        distinct_count = int(row.get("distinct_car_count") or 0)
        expected_row_count = int((((item.get("tables") or {}).get("area_imovel") or {}).get("row_count")) or 0)
        if row_count != expected_row_count:
            fail(f"{uf}: canonical area_imovel row-count drift:{row_count}!={expected_row_count}")
        if distinct_count <= 0 or distinct_count > row_count:
            fail(f"{uf}: invalid distinct CAR count:{distinct_count}/{row_count}")
        duplicate_rows = row_count - distinct_count
        counts[uf] = {
            "snapshot": snapshot,
            "row_count": row_count,
            "distinct_car_count": distinct_count,
            "duplicate_rows": duplicate_rows,
        }
        national_rows += row_count
        national_distinct += distinct_count
        print(
            "RX_V48_CANONICAL_CAR_COUNT",
            f"uf={uf}", f"snapshot={snapshot}", f"rows={row_count}",
            f"distinct={distinct_count}", f"duplicates={duplicate_rows}",
        )

    payload = {
        "schema_version": "v48-sicar-canonical-car-counts-1",
        "source": f"{DATASET}.area_imovel",
        "canonical_manifest_path": canonical.PINNED_MANIFEST_PATH,
        "canonical_manifest_fingerprint_sha256": manifest.get("content_fingerprint_sha256"),
        "canonical_dates": canonical_dates,
        "maximum_bytes_billed": MAX_BYTES,
        "dry_run_bytes": dry_bytes,
        "real_query_bytes_processed": processed,
        "real_query_bytes_billed": billed,
        "real_data_query_executed": True,
        "national_row_count_sum": national_rows,
        "national_distinct_car_count_sum": national_distinct,
        "ufs": counts,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(raw).hexdigest()
    payload["content_fingerprint_sha256"] = fingerprint
    out = Path("artifacts/v48_canonical_car_counts.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"RX_V48_CANONICAL_CAR_COUNTS_UFS={len(counts)}/27")
    print(f"RX_V48_CANONICAL_CAR_COUNTS_NATIONAL_DISTINCT={national_distinct}")
    print(f"RX_V48_CANONICAL_CAR_COUNTS_FINGERPRINT={fingerprint}")
    print("RX_V48_CANONICAL_CAR_COUNTS=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--import-check", action="store_true")
    mode.add_argument("--dry-run-only", action="store_true")
    mode.add_argument("--execute-real", action="store_true")
    args = parser.parse_args()

    if args.import_check:
        print("RX_V48_CANONICAL_CAR_COUNTS_IMPORT_CHECK=PASS")
        return

    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        fail(f"unexpected GCP project:{project or '<missing>'}")

    from google.cloud import bigquery

    manifest, ufs, canonical_dates = canonical_context()
    client = bigquery.Client(project=PROJECT)
    dry_bytes = dry_run(client, bigquery, canonical_dates)

    if args.dry_run_only:
        write_dry_run_evidence(manifest, canonical_dates, dry_bytes)
        return

    rows, processed, billed = execute_real_query(client, bigquery, canonical_dates, dry_bytes)
    build_real_evidence(manifest, ufs, canonical_dates, rows, dry_bytes, processed, billed)


if __name__ == "__main__":
    main()

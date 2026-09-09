from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EXPECTED_UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
CANONICAL_FP = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
SCHEMA = "v48-national-generation-summary-1"


def parse_rfc3339(value: Any) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_seconds(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", raw)
    return float(match.group(1)) if match else None


def load_results(input_dir: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not input_dir.exists():
        return found
    for path in sorted(input_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("schema_version") != "v48-national-uf-result-1":
            continue
        uf = str(data.get("uf") or "").upper()
        if uf not in EXPECTED_UFS:
            continue
        if uf in found:
            raise RuntimeError(f"duplicate_uf_result:{uf}")
        found[uf] = data
    return found


def uf_summary(uf: str, result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"uf": uf, "status": "missing_result", "published": False}
    status = str(result.get("status") or "failed")
    commit = result.get("commit") or {}
    batch = result.get("batch") or {}
    metrics = commit.get("run_metrics") or {}
    source = metrics.get("source") or {}
    tippecanoe = metrics.get("tippecanoe") or {}
    pmtiles = commit.get("pmtiles") or {}
    published = status == "success" and commit.get("status") == "committed"
    return {
        "uf": uf,
        "status": status,
        "published": published,
        "snapshot": result.get("snapshot") or commit.get("snapshot_date"),
        "publication_state": result.get("publication_state"),
        "source_fingerprint_sha256": commit.get("source_fingerprint_sha256"),
        "pmtiles_object": pmtiles.get("object"),
        "pmtiles_sha256": pmtiles.get("sha256"),
        "pmtiles_size_bytes": int(pmtiles.get("size_bytes") or 0) if published else None,
        "feature_count": int(commit.get("feature_count") or 0) if published else None,
        "source_row_count": int(commit.get("source_row_count_actual") or 0) if published else None,
        "bigquery_dry_run_bytes": source.get("dry_run_bytes"),
        "bigquery_processed_bytes": source.get("total_bytes_processed"),
        "bigquery_billed_bytes": source.get("total_bytes_billed"),
        "bigquery_seconds": source.get("elapsed_seconds"),
        "tiles_seconds": tippecanoe.get("elapsed_seconds"),
        "worker_total_seconds": metrics.get("worker_total_elapsed_seconds"),
        "batch_job_id": batch.get("job_id"),
        "batch_region": batch.get("region"),
        "batch_state": batch.get("state"),
        "batch_run_duration": batch.get("run_duration"),
        "batch_run_seconds": duration_seconds(batch.get("run_duration")),
        "batch_create_time": batch.get("create_time"),
        "batch_update_time": batch.get("update_time"),
        "recovery_mode": (commit.get("recovery") or {}).get("mode"),
        "original_run_metrics_available": (commit.get("recovery") or {}).get("original_run_metrics_available"),
        "error": result.get("error"),
    }


def main(input_dir: Path, output: Path) -> dict[str, Any]:
    results = load_results(input_dir)
    per_uf = [uf_summary(uf, results.get(uf)) for uf in EXPECTED_UFS]
    published = [item for item in per_uf if item["published"]]
    failed = [item["uf"] for item in per_uf if not item["published"]]
    pmtiles_total = sum(int(item.get("pmtiles_size_bytes") or 0) for item in published)
    bq_processed_total = sum(int(item.get("bigquery_processed_bytes") or 0) for item in published)
    bq_billed_total = sum(int(item.get("bigquery_billed_bytes") or 0) for item in published)
    worker_seconds_total = sum(float(item.get("worker_total_seconds") or 0) for item in published)
    batch_seconds_known = [float(item["batch_run_seconds"]) for item in per_uf if item.get("batch_run_seconds") is not None]
    starts = [parse_rfc3339(item.get("batch_create_time")) for item in per_uf]
    ends = [parse_rfc3339(item.get("batch_update_time")) for item in per_uf]
    starts = [x for x in starts if x is not None]
    ends = [x for x in ends if x is not None]
    wall_seconds = (max(ends) - min(starts)).total_seconds() if starts and ends else None
    payload = {
        "schema_version": SCHEMA,
        "canonical_manifest_fingerprint": CANONICAL_FP,
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "expected_ufs": 27,
        "result_files_found": len(results),
        "published_ufs": len(published),
        "failed_ufs": failed,
        "all_27_published": len(published) == 27,
        "totals": {
            "pmtiles_size_bytes": pmtiles_total,
            "bigquery_processed_bytes": bq_processed_total,
            "bigquery_billed_bytes": bq_billed_total,
            "worker_seconds_sum": round(worker_seconds_total, 3),
            "batch_run_seconds_sum_known": round(sum(batch_seconds_known), 3),
            "batch_run_seconds_known_ufs": len(batch_seconds_known),
            "national_wall_seconds_batch_span": round(wall_seconds, 3) if wall_seconds is not None else None,
        },
        "per_uf": per_uf,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"RX_V48_NATIONAL_RESULT_FILES={len(results)}/27")
    print(f"RX_V48_NATIONAL_PUBLISHED_UFS={len(published)}/27")
    print(f"RX_V48_NATIONAL_FAILED_UFS={','.join(failed) if failed else 'NONE'}")
    print(f"RX_V48_NATIONAL_PMTILES_TOTAL_BYTES={pmtiles_total}")
    print(f"RX_V48_NATIONAL_BQ_BILLED_TOTAL_BYTES={bq_billed_total}")
    print(f"RX_V48_NATIONAL_BATCH_SECONDS_SUM_KNOWN={round(sum(batch_seconds_known), 3)}")
    print(f"RX_V48_NATIONAL_WALL_SECONDS={round(wall_seconds, 3) if wall_seconds is not None else 'NA'}")
    print(f"RX_V48_NATIONAL_ALL_27_PUBLISHED={'YES' if len(published) == 27 else 'NO'}")
    return payload


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="artifacts/uf-results")
    parser.add_argument("--output", default="artifacts/v48_national_generation_summary.json")
    parser.add_argument("--import-check", action="store_true")
    args = parser.parse_args()
    if args.import_check:
        print("RX_V48_NATIONAL_FINALIZE_IMPORT_CHECK=PASS")
        return
    main(Path(args.input_dir), Path(args.output))


if __name__ == "__main__":
    cli()

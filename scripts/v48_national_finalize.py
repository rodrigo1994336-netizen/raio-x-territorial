from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

EXPECTED_UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
CANONICAL_FP = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
GEOMETRY_NORMALIZATION_VERSION = "v48-polygonal-extraction-1"
SCHEMA = "v48-national-generation-summary-2"


def _none_default(value: Any, default: Any) -> Any:
    return default if value is None else value


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


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


def artifact_attempt(path: Path) -> int:
    for part in reversed(path.parts):
        match = re.search(r"-a(\d+)$", part)
        if match:
            return int(match.group(1))
    return 0


def load_results(input_dir: Path) -> dict[str, dict[str, Any]]:
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    if not input_dir.exists():
        return {}
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
        attempt = artifact_attempt(path)
        current = selected.get(uf)
        if current is None or attempt > current[0]:
            selected[uf] = (attempt, data)
        elif attempt == current[0]:
            raise RuntimeError(f"duplicate_uf_result_same_attempt:{uf}:a{attempt}")
    return {uf: data for uf, (_, data) in selected.items()}


def _geometry_evidence(commit: dict[str, Any]) -> dict[str, Any]:
    source = ((commit.get("run_metrics") or {}).get("source") or {})
    records = list(source.get("normalization_records") or [])
    normalized = int(_first_not_none(commit.get("normalization_applied_count"), source.get("normalization_applied_count"), 0))
    no_geometry = int(_first_not_none(commit.get("no_geometry_car_count"), source.get("no_geometry_car_count"), 0))
    no_polygon = int(_first_not_none(commit.get("no_polygonal_car_count"), source.get("no_polygonal_car_count"), 0))
    distinct = int(_first_not_none(commit.get("distinct_car_count"), source.get("distinct_car_count"), 0))
    features = int(_first_not_none(commit.get("feature_count"), source.get("feature_count"), 0))

    valid = (
        commit.get("analysis_geometry_normalization") == GEOMETRY_NORMALIZATION_VERSION
        and commit.get("map_geometry_normalization") == GEOMETRY_NORMALIZATION_VERSION
        and bool(commit.get("source_geometry_set_fingerprint_sha256"))
        and bool(commit.get("render_geometry_set_fingerprint_sha256"))
        and distinct > 0
        and features + no_geometry + no_polygon == distinct
        and len(records) == normalized
    )

    line_components = 0
    line_length_m = 0.0
    point_components = 0
    area_before_m2 = 0.0
    area_after_m2 = 0.0
    area_difference_m2 = 0.0
    normalized_ids: list[str] = []
    for record in records:
        car = str(record.get("car_code") or "")
        if not car:
            valid = False
            continue
        normalized_ids.append(car)
        line_components += int(_none_default(record.get("discarded_line_components"), 0))
        line_length_m += float(_none_default(record.get("discarded_line_length_m"), 0.0))
        point_components += int(_none_default(record.get("discarded_point_components"), 0))
        before = float(_none_default(record.get("polygon_area_before_m2"), 0.0))
        after = float(_none_default(record.get("polygon_area_after_m2"), 0.0))
        difference = float(_none_default(record.get("polygon_area_difference_m2"), 0.0))
        tolerance = float(_none_default(record.get("polygon_area_tolerance_m2"), 0.0))
        area_before_m2 += before
        area_after_m2 += after
        area_difference_m2 += difference
        if difference > tolerance:
            valid = False
        if not record.get("source_geometry_fingerprint") or not record.get("render_geometry_fingerprint"):
            valid = False
    if len(set(normalized_ids)) != len(normalized_ids):
        valid = False

    no_geometry_ids = list(_first_not_none(commit.get("no_geometry_car_ids"), source.get("no_geometry_car_ids"), []))
    no_polygon_ids = list(_first_not_none(commit.get("no_polygonal_car_ids"), source.get("no_polygonal_car_ids"), []))
    if len(no_geometry_ids) != no_geometry or len(no_polygon_ids) != no_polygon:
        valid = False

    return {
        "valid": valid,
        "normalization_version": commit.get("analysis_geometry_normalization"),
        "source_geometry_set_fingerprint_sha256": commit.get("source_geometry_set_fingerprint_sha256"),
        "render_geometry_set_fingerprint_sha256": commit.get("render_geometry_set_fingerprint_sha256"),
        "distinct_car_count": distinct,
        "feature_count": features,
        "normalization_applied_count": normalized,
        "normalized_car_ids": normalized_ids,
        "no_geometry_car_count": no_geometry,
        "no_geometry_car_ids": no_geometry_ids,
        "no_polygonal_car_count": no_polygon,
        "no_polygonal_car_ids": no_polygon_ids,
        "discarded_line_components": line_components,
        "discarded_line_length_m": round(line_length_m, 6),
        "discarded_point_components": point_components,
        "normalized_polygon_area_before_m2": round(area_before_m2, 6),
        "normalized_polygon_area_after_m2": round(area_after_m2, 6),
        "normalized_polygon_area_difference_m2": round(area_difference_m2, 9),
        "normalization_records": records,
    }


def uf_summary(uf: str, result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"uf": uf, "status": "missing_result", "published": False, "geometry_evidence_valid": False}
    status = str(result.get("status") or "failed")
    commit = result.get("commit") or {}
    batch = result.get("batch") or {}
    metrics = commit.get("run_metrics") or {}
    source = metrics.get("source") or {}
    tippecanoe = metrics.get("tippecanoe") or {}
    pmtiles = commit.get("pmtiles") or {}
    geometry = _geometry_evidence(commit) if commit else {"valid": False}
    structurally_published = status == "success" and commit.get("status") == "committed"
    published = structurally_published and bool(geometry.get("valid"))
    return {
        "uf": uf,
        "status": status if published else ("geometry_evidence_invalid" if structurally_published else status),
        "published": published,
        "snapshot": result.get("snapshot") or commit.get("snapshot_date"),
        "publication_state": result.get("publication_state"),
        "source_fingerprint_sha256": commit.get("source_fingerprint_sha256"),
        "pmtiles_object": pmtiles.get("object"),
        "pmtiles_sha256": pmtiles.get("sha256"),
        "pmtiles_size_bytes": int(_none_default(pmtiles.get("size_bytes"), 0)) if published else None,
        "feature_count": int(_none_default(commit.get("feature_count"), 0)) if published else None,
        "source_row_count": int(_none_default(commit.get("source_row_count_actual"), 0)) if published else None,
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
        "max_retry_count": batch.get("max_retry_count"),
        "attempts_total": batch.get("attempts_total"),
        "retry_count_observed": batch.get("retry_count_observed"),
        "task_attempts": batch.get("task_attempts") or [],
        "reuse_proof": result.get("reuse_proof"),
        "recovery_mode": (commit.get("recovery") or {}).get("mode"),
        "original_run_metrics_available": (commit.get("recovery") or {}).get("original_run_metrics_available"),
        "geometry_evidence_valid": bool(geometry.get("valid")),
        "geometry": geometry,
        "error": result.get("error"),
    }


def main(input_dir: Path, output: Path) -> dict[str, Any]:
    results = load_results(input_dir)
    per_uf = [uf_summary(uf, results.get(uf)) for uf in EXPECTED_UFS]
    published = [item for item in per_uf if item["published"]]
    failed = [item["uf"] for item in per_uf if not item["published"]]
    pmtiles_total = sum(int(_none_default(item.get("pmtiles_size_bytes"), 0)) for item in published)
    bq_processed_total = sum(int(_none_default(item.get("bigquery_processed_bytes"), 0)) for item in published)
    bq_billed_total = sum(int(_none_default(item.get("bigquery_billed_bytes"), 0)) for item in published)
    worker_seconds_total = sum(float(_none_default(item.get("worker_total_seconds"), 0)) for item in published)
    batch_seconds_known = [float(item["batch_run_seconds"]) for item in per_uf if item.get("batch_run_seconds") is not None]
    retries_total = sum(int(_none_default(item.get("retry_count_observed"), 0)) for item in per_uf)
    reused_ufs = [item["uf"] for item in per_uf if item.get("publication_state") == "COMPLETE_REUSED"]
    starts = [parse_rfc3339(item.get("batch_create_time")) for item in per_uf]
    ends = [parse_rfc3339(item.get("batch_update_time")) for item in per_uf]
    starts = [x for x in starts if x is not None]
    ends = [x for x in ends if x is not None]
    wall_seconds = (max(ends) - min(starts)).total_seconds() if starts and ends else None

    geometries = [item.get("geometry") or {} for item in published]
    national_geometry = {
        "normalization_version": GEOMETRY_NORMALIZATION_VERSION,
        "distinct_car_count": sum(int(_none_default(g.get("distinct_car_count"), 0)) for g in geometries),
        "feature_count": sum(int(_none_default(g.get("feature_count"), 0)) for g in geometries),
        "normalization_applied_count": sum(int(_none_default(g.get("normalization_applied_count"), 0)) for g in geometries),
        "no_geometry_car_count": sum(int(_none_default(g.get("no_geometry_car_count"), 0)) for g in geometries),
        "no_geometry_car_ids": [car for g in geometries for car in (g.get("no_geometry_car_ids") or [])],
        "no_polygonal_car_count": sum(int(_none_default(g.get("no_polygonal_car_count"), 0)) for g in geometries),
        "no_polygonal_car_ids": [car for g in geometries for car in (g.get("no_polygonal_car_ids") or [])],
        "discarded_line_components": sum(int(_none_default(g.get("discarded_line_components"), 0)) for g in geometries),
        "discarded_line_length_m": round(sum(float(_none_default(g.get("discarded_line_length_m"), 0.0)) for g in geometries), 6),
        "discarded_point_components": sum(int(_none_default(g.get("discarded_point_components"), 0)) for g in geometries),
        "normalized_polygon_area_before_m2": round(sum(float(_none_default(g.get("normalized_polygon_area_before_m2"), 0.0)) for g in geometries), 6),
        "normalized_polygon_area_after_m2": round(sum(float(_none_default(g.get("normalized_polygon_area_after_m2"), 0.0)) for g in geometries), 6),
        "normalized_polygon_area_difference_m2": round(sum(float(_none_default(g.get("normalized_polygon_area_difference_m2"), 0.0)) for g in geometries), 9),
    }

    payload = {
        "schema_version": SCHEMA,
        "canonical_manifest_fingerprint": CANONICAL_FP,
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "expected_ufs": 27,
        "result_files_selected": len(results),
        "published_ufs": len(published),
        "failed_ufs": failed,
        "all_27_published": len(published) == 27,
        "geometry_evidence_all_published_ufs_valid": all(item.get("geometry_evidence_valid") for item in published),
        "geometry": national_geometry,
        "totals": {
            "pmtiles_size_bytes": pmtiles_total,
            "bigquery_processed_bytes": bq_processed_total,
            "bigquery_billed_bytes": bq_billed_total,
            "worker_seconds_sum": round(worker_seconds_total, 3),
            "batch_run_seconds_sum_known": round(sum(batch_seconds_known), 3),
            "batch_run_seconds_known_ufs": len(batch_seconds_known),
            "national_wall_seconds_batch_span": round(wall_seconds, 3) if wall_seconds is not None else None,
            "retry_count_observed_total": retries_total,
            "complete_reused_ufs": reused_ufs,
        },
        "per_uf": per_uf,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"RX_V48_NATIONAL_RESULT_UFS={len(results)}/27")
    print(f"RX_V48_NATIONAL_PUBLISHED_UFS={len(published)}/27")
    print(f"RX_V48_NATIONAL_FAILED_UFS={','.join(failed) if failed else 'NONE'}")
    print(f"RX_V48_NATIONAL_PMTILES_TOTAL_BYTES={pmtiles_total}")
    print(f"RX_V48_NATIONAL_BQ_BILLED_TOTAL_BYTES={bq_billed_total}")
    print(f"RX_V48_NATIONAL_NORMALIZED_CARS={national_geometry['normalization_applied_count']}")
    print(f"RX_V48_NATIONAL_NO_GEOMETRY_CARS={national_geometry['no_geometry_car_count']}")
    print(f"RX_V48_NATIONAL_NO_POLYGON_CARS={national_geometry['no_polygonal_car_count']}")
    print(f"RX_V48_NATIONAL_DISCARDED_LINES={national_geometry['discarded_line_components']}")
    print(f"RX_V48_NATIONAL_DISCARDED_LINE_LENGTH_M={national_geometry['discarded_line_length_m']}")
    print(f"RX_V48_NATIONAL_DISCARDED_POINTS={national_geometry['discarded_point_components']}")
    print(f"RX_V48_NATIONAL_NORMALIZED_AREA_BEFORE_M2={national_geometry['normalized_polygon_area_before_m2']}")
    print(f"RX_V48_NATIONAL_NORMALIZED_AREA_AFTER_M2={national_geometry['normalized_polygon_area_after_m2']}")
    print(f"RX_V48_NATIONAL_NORMALIZED_AREA_DIFFERENCE_M2={national_geometry['normalized_polygon_area_difference_m2']}")
    print(f"RX_V48_NATIONAL_BATCH_SECONDS_SUM_KNOWN={round(sum(batch_seconds_known), 3)}")
    print(f"RX_V48_NATIONAL_RETRIES_OBSERVED={retries_total}")
    print(f"RX_V48_NATIONAL_COMPLETE_REUSED_UFS={','.join(reused_ufs) if reused_ufs else 'NONE'}")
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

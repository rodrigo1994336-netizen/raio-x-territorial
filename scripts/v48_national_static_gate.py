from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CANONICAL_FP = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
GEOMETRY_NORMALIZATION_VERSION = "v48-polygonal-extraction-1"
EXPECTED_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
PAID_SCRIPTS = (
    "scripts/v48_national_worker.py",
    "scripts/v48_national_batch_submit.py",
    "scripts/v48_national_finalize.py",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def compile_file(path: str) -> None:
    ast.parse(text(path), filename=path)


def import_gate() -> None:
    for path in PAID_SCRIPTS:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / path), "--import-check"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30, check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        require(proc.returncode == 0, f"real_import_failed:{path}:{output[-2000:]}")
        require("IMPORT_CHECK=PASS" in output, f"real_import_marker_missing:{path}")
        print(f"RX_V48_NATIONAL_IMPORT_GATE=PASS script={path}")


def main() -> None:
    files = (
        "scripts/v48_national_worker.py",
        "scripts/v48_national_batch_submit.py",
        "scripts/v48_national_finalize.py",
        "scripts/v48_national_recovery_contract.py",
        "sicar_geometry_normalization_v48.py",
        ".github/workflows/v48-national-car-quadrica.yml",
        ".github/workflows/v48-geometry-truth-gate.yml",
    )
    for path in files[:5]:
        compile_file(path)
    import_gate()

    config = json.loads(text("config/v48_uf_orchestration_contract.json"))
    require(config.get("nationalEnabled") is True, "national authorization not enabled")
    require(config.get("canonicalManifestFingerprint") == CANONICAL_FP, "canonical fingerprint config mismatch")
    require(config.get("snapshotScope") == "uf_canonical", "snapshot scope must be uf_canonical")
    require(config.get("analysisMapRule") == "analysis_snapshot == map_snapshot == canonical_snapshot_for_uf", "analysis/map rule mismatch")
    require(config.get("geometryNormalizationVersion") == GEOMETRY_NORMALIZATION_VERSION, "geometry normalization version mismatch")
    require(
        config.get("analysisMapGeometryRule")
        == "analysis_geometry_normalization == map_geometry_normalization == v48-polygonal-extraction-1",
        "analysis/map geometry normalization invariant mismatch",
    )
    require("without published geometry" in str(config.get("noGeometryRule") or ""), "explicit no-geometry rule missing")
    require(config.get("batchRegion") == "us-central1", "national Batch region must be us-central1")
    require(config.get("machineType") == "e2-standard-8", "machine type mismatch")
    require(config.get("provisioningModel") == "SPOT", "provisioning must be Spot")
    require(config.get("maxRetryCount") == 0, "Batch retries must be zero")
    require(config.get("oneBatchJobPerUf") is True, "one Batch job per UF invariant missing")
    matrix = config.get("githubMatrix") or {}
    require(matrix.get("failFast") is False and matrix.get("maxParallel") == 3, "matrix must be fail-fast false / maxParallel 3")
    require(matrix.get("rerunMode") == "failed_jobs_only", "isolated rerun contract missing")
    bq = config.get("bigQuery") or {}
    require(bq.get("maximumBytesBilledPerUf") == 2 * 1024**3, "per-UF BigQuery guard must be 2 GiB")
    require(bq.get("exactSnapshotOnly") is True and bq.get("latestFallbackAllowed") is False, "snapshot fallback must be disabled")
    require(config.get("activeJsonMutationAllowed") is False, "active.json mutation must remain blocked")

    import sicar_canonical_manifest_v48 as canonical
    import sicar_geometry_normalization_v48 as geometry_contract
    require(geometry_contract.NORMALIZATION_VERSION == GEOMETRY_NORMALIZATION_VERSION, "shared geometry contract version mismatch")
    require("ST_DUMP(source_geometry, 2)" in geometry_contract.normalization_ctes("per_car_source"), "shared ST_DUMP dimension-2 normalization missing")
    manifest = canonical.load_manifest()
    require(manifest.get("content_fingerprint_sha256") == CANONICAL_FP, "pinned canonical manifest fingerprint mismatch")
    ufs = manifest.get("ufs") or {}
    require(set(ufs) == EXPECTED_UFS, "canonical manifest must contain exactly 27 UFs")
    require(all((ufs[uf] or {}).get("status") == "canonical" for uf in EXPECTED_UFS), "all 27 UFs must be canonical")
    require((ufs["SP"] or {}).get("snapshot") == "2026-06-02", "SP canonical snapshot changed")

    worker = text("scripts/v48_national_worker.py")
    require("data_extracao=@snapshot" in worker, "worker exact snapshot predicate missing")
    require("MAX(data_extracao)" not in worker, "worker latest-date fallback detected")
    require("maximum_bytes_billed=MAX_BQ_BYTES" in worker and "dry_run=True" in worker, "BigQuery hard guard/dry-run missing")
    require("MAX_BQ_BYTES = 2 * 1024**3" in worker, "worker byte guard mismatch")
    require("PAGE_SIZE = 10_000" in worker, "worker page size mismatch")
    require("canonical_area_imovel_row_count_drift" in worker, "source row-count drift fail-closed missing")
    require("geometry_contract.normalization_ctes('per_car_source')" in worker, "worker does not consume shared geometry normalization")
    require("no_geometry_published" in worker and "no_geometry_car_ids" in worker, "explicit no-geometry classification/evidence missing")
    require("no_polygonal_component" in worker and "no_polygonal_car_ids" in worker, "no-polygon classification/evidence missing")
    require("normalization_records" in worker, "per-CAR normalization evidence missing")
    require("discarded_line_length_m" in worker and "discarded_point_components" in worker, "discarded dimensional metrics missing")
    require("source_geometry_set_fingerprint_sha256" in worker and "render_geometry_set_fingerprint_sha256" in worker, "dual geometry-set fingerprints missing")
    require("analysis_geometry_normalization" in worker and "map_geometry_normalization" in worker, "map/analysis geometry identity missing")
    require("unexpected_geometry_type" not in worker, "obsolete GeometryCollection rejection remains")
    require("analysis_snapshot" in worker and "map_snapshot" in worker, "map/analysis snapshot identity missing")
    require("if_generation_match=0" in worker, "immutable upload precondition missing")
    require("prepared-receipt" in worker and "commit-manifest" in worker, "three-phase publication files missing")
    require("active.json" not in worker, "worker must not touch active.json")

    submit = text("scripts/v48_national_batch_submit.py")
    require('REGION = "us-central1"' in submit, "submitter must use us-central1")
    require('"machineType": "e2-standard-8"' in submit, "submitter machine mismatch")
    require('"provisioningModel": "SPOT"' in submit, "submitter Spot invariant missing")
    require('"maxRetryCount": 0' in submit, "submitter retry count must be zero")
    require('"maxRunDuration": "14400s"' in submit, "Batch max duration mismatch")
    require("RECONCILE_MANIFEST_ONLY" in submit and "orphan_pmtiles_without_receipt_cannot_be_reconciled" in submit, "recovery paths missing")
    require("active.json" not in submit, "submitter must not touch active.json")
    require("DELETE" not in submit.upper(), "national submitter must not delete Batch/GCS resources")

    recovery = text("scripts/v48_national_recovery_contract.py")
    require("RECONCILE_MANIFEST_ONLY" in recovery and "active_json_updated" in recovery, "recovery invariant missing")
    require("recovery_requires_full_geometry_evidence_rebuild" in recovery, "evidence-poor recovery must fail closed")
    require("geometry_evidence_preserved" in recovery, "complete recovery evidence marker missing")

    finalizer = text("scripts/v48_national_finalize.py")
    for token in (
        "geometry_evidence_valid",
        "normalization_records",
        "discarded_line_components",
        "discarded_line_length_m",
        "discarded_point_components",
        "normalized_polygon_area_before_m2",
        "normalized_polygon_area_after_m2",
        "normalized_polygon_area_difference_m2",
        "no_geometry_car_ids",
        "no_polygonal_car_ids",
    ):
        require(token in finalizer, f"national finalizer geometry evidence missing:{token}")
    require("features + no_geometry + no_polygon == distinct" in finalizer, "national finalizer geometry reconciliation missing")
    require("len(records) == normalized" in finalizer, "national finalizer per-CAR normalization reconciliation missing")

    workflow = text(".github/workflows/v48-national-car-quadrica.yml")
    require("workflow_dispatch:" in workflow, "national workflow must be manually dispatched")
    require("\n  push:" not in workflow and "\n  schedule:" not in workflow and "\n  pull_request:" not in workflow, "national workflow gained automatic trigger")
    require("fail-fast: false" in workflow and "max-parallel: 3" in workflow, "workflow concurrency/fail-fast mismatch")
    for uf in sorted(EXPECTED_UFS):
        require(re.search(rf"\b{uf}\b", workflow) is not None, f"workflow matrix missing UF:{uf}")
    require("GENERATE_27_UFS" in workflow, "explicit national confirmation token missing")
    require("actions/download-artifact@v4" in workflow and "v48_national_finalize.py" in workflow, "real-results finalizer missing")
    require("active.json" not in workflow, "workflow must not mutate active.json")

    truth_workflow = text(".github/workflows/v48-geometry-truth-gate.yml")
    require("workflow_dispatch:" in truth_workflow, "geometry truth workflow must be manual")
    require("\n  push:" not in truth_workflow and "\n  schedule:" not in truth_workflow, "geometry truth workflow gained automatic trigger")
    require("v48_national_batch_submit.py" not in truth_workflow and "GENERATE_27_UFS" not in truth_workflow, "geometry truth gate must not start national generation")

    permanent = text("REGRA_PERMANENTE_WORKFLOW_DISPATCH.md")
    require("gate afirma invariante, nunca estágio" in permanent, "invariant-only gate rule missing")
    require("nenhum workflow cujo arquivo comece por `v48-` pode usar gatilho `push`" in permanent, "V48 no-push rule missing")
    print("RX_V48_NATIONAL_STATIC_GATE=PASS")


if __name__ == "__main__":
    main()

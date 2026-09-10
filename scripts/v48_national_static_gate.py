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
        ".github/workflows/v48-national-car-quadrica.yml",
    )
    for path in files[:4]:
        compile_file(path)
    import_gate()

    config = json.loads(text("config/v48_uf_orchestration_contract.json"))
    require(config.get("nationalEnabled") is True, "national authorization not enabled")
    require(config.get("canonicalManifestFingerprint") == CANONICAL_FP, "canonical fingerprint config mismatch")
    require(config.get("snapshotScope") == "uf_canonical", "snapshot scope must be uf_canonical")
    require(config.get("analysisMapRule") == "analysis_snapshot == map_snapshot == canonical_snapshot_for_uf", "analysis/map rule mismatch")
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
    require("analysis_snapshot" in worker and "map_snapshot" in worker, "map/analysis identity missing")
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

    workflow = text(".github/workflows/v48-national-car-quadrica.yml")
    require("workflow_dispatch:" in workflow, "national workflow must be manually dispatched")
    require("\n  push:" not in workflow and "\n  schedule:" not in workflow and "\n  pull_request:" not in workflow, "national workflow gained automatic trigger")
    require("fail-fast: false" in workflow and "max-parallel: 3" in workflow, "workflow concurrency/fail-fast mismatch")
    for uf in sorted(EXPECTED_UFS):
        require(re.search(rf"\b{uf}\b", workflow) is not None, f"workflow matrix missing UF:{uf}")
    require("GENERATE_27_UFS" in workflow, "explicit national confirmation token missing")
    require("actions/download-artifact@v4" in workflow and "v48_national_finalize.py" in workflow, "real-results finalizer missing")
    require("active.json" not in workflow, "workflow must not mutate active.json")

    permanent = text("REGRA_PERMANENTE_WORKFLOW_DISPATCH.md")
    require("gate afirma invariante, nunca estágio" in permanent, "invariant-only gate rule missing")
    require("nenhum workflow cujo arquivo comece por `v48-` pode usar gatilho `push`" in permanent, "V48 no-push rule missing")
    print("RX_V48_NATIONAL_STATIC_GATE=PASS")


if __name__ == "__main__":
    main()

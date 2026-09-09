from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
TABLES = {
    "area_imovel", "vegetacao_nativa", "reserva_legal", "app",
    "uso_restrito", "area_consolidada", "hidrografia", "area_pousio",
}
SELECTION_RULE = "latest_date_all_8_tables_have_partition_and_rows_per_uf"
MANIFEST_NAME_RE = re.compile(r"^sicar-canonical-snapshots-v1-([0-9a-f]{64})\.json$")
PAID_WORKFLOW_SCRIPTS = (
    "scripts/v48_canonical_car_counts.py",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def file_path(path: str) -> Path:
    return REPO_ROOT / path


def text(path: str) -> str:
    return file_path(path).read_text(encoding="utf-8")


def compile_gate(path: str) -> None:
    ast.parse(text(path), filename=path)


def paid_script_import_gate() -> None:
    for path in PAID_WORKFLOW_SCRIPTS:
        proc = subprocess.run(
            [sys.executable, str(file_path(path)), "--import-check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = (proc.stdout or "").strip()
        error = (proc.stderr or "").strip()
        require(
            proc.returncode == 0,
            f"paid workflow script import failed:{path}:exit={proc.returncode}:stderr={error}",
        )
        require("IMPORT_CHECK=PASS" in output, f"paid workflow script import marker missing:{path}")
        print(f"RX_V48_PAID_SCRIPT_IMPORT_GATE=PASS script={path}")


def pinned_manifest_path() -> Path:
    tree = ast.parse(text("sicar_canonical_manifest_v48.py"))
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PINNED_MANIFEST_PATH":
                    value = ast.literal_eval(node.value)
    require(isinstance(value, str), "PINNED_MANIFEST_PATH must be a literal string")
    require(bool(value.strip()), "canonical snapshot manifest is not pinned yet")
    relative = Path(value.strip())
    require(MANIFEST_NAME_RE.match(relative.name) is not None, "pinned manifest must be content-addressed")
    return REPO_ROOT / relative


def static_contract() -> None:
    for path in (
        "sicar_canonical_manifest_v48.py",
        "sicar_canonical_snapshot_v48.py",
        "portal_car_snapshot_ui_v48.py",
        "portal_car_integrity_v47.py",
        "vector_snapshot_contract_v48.py",
        "scripts/v48_canonical_snapshot_audit.py",
        "scripts/v48_canonical_car_counts.py",
    ):
        compile_gate(path)

    paid_script_import_gate()

    manifest_runtime = text("sicar_canonical_manifest_v48.py")
    require("sicar-canonical-snapshots-v1-([0-9a-f]{64})" in manifest_runtime, "content-addressed manifest filename contract missing")
    require("canonical_snapshot_manifest_fingerprint_mismatch" in manifest_runtime, "runtime manifest fingerprint gate missing")
    require("STALE_DAYS_THRESHOLD = 60" in manifest_runtime, "60-day transparency threshold missing")
    require("Esta base está mais antiga que a das demais unidades da federação." in manifest_runtime, "neutral stale-data message missing")
    require("atualizada há" in manifest_runtime, "dynamic snapshot age label missing")

    runtime = text("sicar_canonical_snapshot_v48.py")
    require("MAX(data_extracao)" not in runtime, "canonical runtime must never choose latest date per CAR")
    require("data_extracao=@snapshot" in runtime, "canonical runtime must pin exact manifest snapshot")
    require("car_not_present_in_canonical_snapshot" in runtime, "missing CAR must have explicit canonical absence state")
    require("Este imóvel não consta na base de" in runtime, "client absence language missing")
    require("snapshot_staleness_note" in runtime and "snapshot_age_days" in runtime, "freshness fields missing")

    vector = text("vector_snapshot_contract_v48.py")
    require("SNAPSHOT_SCOPE = \"uf_canonical\"" in vector, "vector contract must be scoped per UF")
    require("SNAPSHOT_DATE = dt.date(2026, 8, 4)" not in vector, "national fixed snapshot date still present")
    require("map_snapshot" in vector and "analysis_snapshot" in vector, "map-analysis equality contract missing")

    route = text("portal_car_integrity_v47.py")
    require("import sicar_canonical_snapshot_v48" in route, "portal route is not bound to canonical contract")
    require("import portal_car_snapshot_ui_v48" in route, "canonical client wording patch is not loaded")

    ui = text("portal_car_snapshot_ui_v48.py")
    require("Snapshot SICAR:" in ui, "UI patch must target the legacy wording explicitly")
    require("d?.user_message" in ui, "UI patch must render explicit canonical absence message")
    require("d.snapshot_label" in ui, "UI patch must render canonical UF/date label")
    require("Ver auditoria · datas das bases por estado" in ui, "27-UF provenance panel missing")
    require("rxV48AgeDays" in ui, "27-UF panel must calculate age dynamically")

    policy = text("docs/v48_sicar_freshness_policy.md")
    require("120 dias" in policy and "SICAR oficial" in policy, "São Paulo 120-day source-review trigger not recorded")
    require("0/8 em 01/08/2026" in policy and "04/08/2026" in policy, "São Paulo August absence evidence not recorded")

    permanent = text("REGRA_PERMANENTE_WORKFLOW_DISPATCH.md")
    require("não pode depender do diretório corrente" in permanent, "workflow script path-independence rule missing")
    require("--import-check" in permanent, "real import gate permanent rule missing")

    audit = text("scripts/v48_canonical_snapshot_audit.py")
    require(SELECTION_RULE in audit, "selection rule not explicit")
    require("INFORMATION_SCHEMA.PARTITIONS" in audit, "metadata-first partition discovery missing")
    require("meta.time_partitioning" in audit, "table partition-field metadata gate missing")
    require("field != \"data_extracao\"" in audit, "data_extracao partition contract missing")
    require("COUNT(*) AS row_count" in audit, "lightweight row-presence confirmation missing")
    require("COUNTIF(geometria" not in audit, "canonical date selection must not scan geometry")
    require("COUNT(DISTINCT id_imovel)" not in audit, "canonical date selection must not scan id_imovel")
    require("dry_run=True" in audit, "real query must be preceded by dry-run")
    require("maximum_bytes_billed=MAX_BYTES" in audit, "real query must enforce maximum bytes billed")
    require("MAX_BYTES = 2 * 1024**3" in audit, "BigQuery hard guard must be exactly 2 GiB")

    counts = text("scripts/v48_canonical_car_counts.py")
    require("Path(__file__).resolve().parents[1]" in counts, "CAR count script must resolve repository root explicitly")
    require("COUNT(DISTINCT id_imovel) AS distinct_car_count" in counts, "canonical CAR counts must deduplicate id_imovel")
    require("MAX_BYTES = 2 * 1024**3" in counts, "CAR count hard guard must be exactly 2 GiB")
    require("dry_run=True" in counts and "maximum_bytes_billed=MAX_BYTES" in counts, "CAR count query must dry-run then enforce hard guard")
    require("canonical area_imovel row-count drift" in counts, "CAR count must fail closed on manifest/source drift")
    require("--import-check" in counts and "--dry-run-only" in counts and "--execute-real" in counts, "CAR count execution modes missing")
    require('"real_data_query_executed": False' in counts, "dry-run evidence must prove no real query")

    workflow = text(".github/workflows/v48-canonical-snapshot-audit.yml")
    require("workflow_dispatch:" in workflow, "manual canonical workflow must use workflow_dispatch")
    require("\n  push:" not in workflow and "\n  pull_request:" not in workflow and "\n  schedule:" not in workflow, "manual canonical workflow gained an unauthorized trigger")
    require("--require-manifest" in workflow, "count workflow must prove pinned manifest before query")
    require("python scripts/v48_canonical_car_counts.py --dry-run-only" in workflow, "next CAR-count workflow must be dry-run only")
    require("--execute-real" not in workflow, "real CAR-count query must not be enabled before dry-run review")
    require("REAL_QUERY_EXECUTED=NO" in workflow, "workflow must verify no real CAR-count query executed")
    print("RX_V48_CANONICAL_SNAPSHOT_STATIC_GATE=PASS")


def manifest_gate() -> None:
    manifest = pinned_manifest_path()
    require(manifest.exists(), f"immutable manifest missing:{manifest}")
    match = MANIFEST_NAME_RE.match(manifest.name)
    require(match is not None, "immutable manifest filename invalid")
    filename_hash = match.group(1)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    require(data.get("schema_version") == "v48-sicar-canonical-snapshots-1", "manifest schema mismatch")
    require(data.get("selection_rule") == SELECTION_RULE, "manifest rule mismatch")
    require(set(data.get("required_tables") or []) == TABLES, "manifest required-table set mismatch")
    ufs = data.get("ufs") or {}
    require(set(ufs) == EXPECTED_UFS, "manifest must declare all 27 UFs")

    for uf in sorted(EXPECTED_UFS):
        item = ufs[uf]
        status = item.get("status")
        require(status in {"canonical", "unavailable"}, f"{uf}: invalid status")
        if status == "unavailable":
            require(item.get("snapshot") is None, f"{uf}: unavailable UF cannot carry snapshot")
            continue
        snapshot = str(item.get("snapshot") or "")
        dt.date.fromisoformat(snapshot)
        tables = item.get("tables") or {}
        require(set(tables) == TABLES, f"{uf}: canonical snapshot must prove 8/8 tables")
        for table, evidence in tables.items():
            require(int(evidence.get("row_count") or 0) > 0, f"{uf}/{table}: empty partition")

    declared = str(data.get("content_fingerprint_sha256") or "")
    payload = dict(data)
    payload.pop("content_fingerprint_sha256", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    actual = hashlib.sha256(raw).hexdigest()
    require(actual == declared, "manifest content fingerprint mismatch")
    require(filename_hash == declared, "manifest filename hash does not match content fingerprint")
    print("RX_V48_CANONICAL_SNAPSHOT_MANIFEST_GATE=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-manifest", action="store_true")
    args = parser.parse_args()
    static_contract()
    if args.require_manifest:
        manifest_gate()


if __name__ == "__main__":
    main()

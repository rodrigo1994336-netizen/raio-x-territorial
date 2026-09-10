from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
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
PUSH_TRIGGER_RE = re.compile(r"(?m)^\s*push\s*:")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def file_path(path: str) -> Path:
    return REPO_ROOT / path


def text(path: str) -> str:
    return file_path(path).read_text(encoding="utf-8")


def compile_gate(path: str) -> None:
    ast.parse(text(path), filename=path)


def pinned_manifest_path() -> Path:
    tree = ast.parse(text("sicar_canonical_manifest_v48.py"))
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PINNED_MANIFEST_PATH":
                    value = ast.literal_eval(node.value)
    require(isinstance(value, str) and bool(value.strip()), "canonical snapshot manifest is not pinned")
    relative = Path(value.strip())
    require(MANIFEST_NAME_RE.match(relative.name) is not None, "pinned manifest must be content-addressed")
    return REPO_ROOT / relative


def no_push_gate() -> None:
    offenders = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("v48-*.yml")):
        if PUSH_TRIGGER_RE.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    require(not offenders, f"V48 workflow push triggers forbidden:{','.join(offenders)}")
    print("RX_V48_PUSH_TRIGGER_OFFENDERS=NONE")


def static_contract() -> None:
    for path in (
        "sicar_canonical_manifest_v48.py",
        "sicar_canonical_snapshot_v48.py",
        "portal_car_snapshot_ui_v48.py",
        "portal_car_integrity_v47.py",
        "vector_snapshot_contract_v48.py",
        "scripts/v48_canonical_snapshot_audit.py",
        "scripts/v48_geometry_type_audit.py",
    ):
        compile_gate(path)

    no_push_gate()

    manifest_runtime = text("sicar_canonical_manifest_v48.py")
    require("sicar-canonical-snapshots-v1-([0-9a-f]{64})" in manifest_runtime, "content-addressed manifest filename contract missing")
    require("canonical_snapshot_manifest_fingerprint_mismatch" in manifest_runtime, "runtime manifest fingerprint gate missing")
    require("STALE_DAYS_THRESHOLD = 60" in manifest_runtime, "60-day transparency threshold missing")
    require("Esta base está mais antiga que a das demais unidades da federação." in manifest_runtime, "neutral stale-data message missing")
    require("atualizada há" in manifest_runtime, "dynamic snapshot age label missing")
    require(bool(pinned_manifest_path()), "canonical manifest must remain pinned")

    runtime = text("sicar_canonical_snapshot_v48.py")
    require("MAX(data_extracao)" not in runtime, "canonical runtime must never choose latest date per CAR")
    require("data_extracao=@snapshot" in runtime, "canonical runtime must pin exact manifest snapshot")
    require("car_not_present_in_canonical_snapshot" in runtime, "missing CAR must have explicit canonical absence state")
    require("Este imóvel não consta na base de" in runtime, "client absence language missing")
    require("snapshot_staleness_note" in runtime and "snapshot_age_days" in runtime, "freshness fields missing")

    vector = text("vector_snapshot_contract_v48.py")
    require('SNAPSHOT_SCOPE = "uf_canonical"' in vector, "vector contract must be scoped per UF")
    require("SNAPSHOT_DATE = dt.date(2026, 8, 4)" not in vector, "national fixed snapshot date still present")
    require("map_snapshot" in vector and "analysis_snapshot" in vector, "map-analysis equality contract missing")

    route = text("portal_car_integrity_v47.py")
    require("import sicar_canonical_snapshot_v48" in route, "portal route is not bound to canonical contract")
    require("import portal_car_snapshot_ui_v48" in route, "canonical client wording patch is not loaded")

    ui = text("portal_car_snapshot_ui_v48.py")
    require("d.snapshot_label" in ui, "UI patch must render canonical UF/date label")
    require("Ver auditoria · datas das bases por estado" in ui, "27-UF provenance panel missing")
    require("rxV48AgeDays" in ui, "27-UF panel must calculate age dynamically")

    freshness_policy = text("docs/v48_sicar_freshness_policy.md")
    require("120 dias" in freshness_policy and "SICAR oficial" in freshness_policy, "São Paulo 120-day source-review trigger not recorded")
    require("0/8 em 01/08/2026" in freshness_policy and "04/08/2026" in freshness_policy, "São Paulo August absence evidence not recorded")

    geometry_policy = text("docs/v48_geometry_semantics_policy.md")
    require("analysis_geometry_normalization == map_geometry_normalization" in geometry_policy, "shared analysis-map geometry normalization invariant missing")
    require("ST_DUMP(..., 2)" in geometry_policy, "approved dimensional extraction policy missing")
    require("source_geometry_fingerprint" in geometry_policy and "render_geometry_fingerprint" in geometry_policy, "dual geometry fingerprint policy missing")
    require("FAIL_CLOSED" in geometry_policy and "polígono vazio" in geometry_policy, "geometry fail-closed policy incomplete")

    permanent = text("REGRA_PERMANENTE_WORKFLOW_DISPATCH.md")
    require("não pode depender do diretório corrente" in permanent, "workflow script path-independence rule missing")
    require("gate afirma invariante, nunca estágio" in permanent, "gate invariant-not-stage rule missing")
    require("nenhum workflow cujo arquivo comece por `v48-` pode usar gatilho `push`" in permanent, "V48 no-push rule missing")

    canonical_workflow = text(".github/workflows/v48-canonical-snapshot-audit.yml")
    require("workflow_dispatch:" in canonical_workflow, "canonical workflow must remain manual")
    require("v48_canonical_car_counts.py" not in canonical_workflow, "canceled canonical CAR count stage is still callable")
    require("v48_geometry_type_audit.py" in canonical_workflow, "geometry-type diagnostic is not wired into canonical audit workflow")
    print("RX_V48_CANONICAL_SNAPSHOT_STATIC_GATE=PASS")


def manifest_gate() -> None:
    manifest = pinned_manifest_path()
    require(manifest.exists(), f"immutable manifest missing:{manifest}")
    match = MANIFEST_NAME_RE.match(manifest.name)
    require(match is not None, "immutable manifest filename invalid")
    filename_hash = match.group(1)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    require(data.get("schema_version") == "v48-sicar-canonical-snapshots-1", "manifest schema mismatch")
    require(data.get("selection_rule") == SELECTION_RULE, "manifest selection rule mismatch")
    require(set(data.get("required_tables") or []) == TABLES, "manifest required-table set mismatch")
    ufs = data.get("ufs") or {}
    require(set(ufs) == EXPECTED_UFS, "manifest must declare all 27 UFs")
    for uf in sorted(EXPECTED_UFS):
        item = ufs[uf]
        require(item.get("status") == "canonical", f"{uf}: canonical status required")
        snapshot = str(item.get("snapshot") or "")
        dt.date.fromisoformat(snapshot)
        tables = item.get("tables") or {}
        require(set(tables) == TABLES, f"{uf}: canonical snapshot must prove 8/8 tables")
        for table, evidence in tables.items():
            require(int(evidence.get("row_count") or 0) > 0, f"{uf}/{table}: empty partition")
    require(ufs["SP"]["snapshot"] == "2026-06-02", "SP canonical snapshot must remain 2026-06-02")
    require(ufs["MG"]["snapshot"] == "2026-08-04", "MG canonical snapshot must remain Curvelo-compatible 2026-08-04")
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

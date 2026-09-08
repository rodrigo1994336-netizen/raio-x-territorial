from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path

EXPECTED_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
TABLES = {
    "area_imovel", "vegetacao_nativa", "reserva_legal", "app",
    "uso_restrito", "area_consolidada", "hidrografia", "area_pousio",
}
MANIFEST = Path("car/manifests/sicar-canonical-snapshots-v1.json")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def compile_gate(path: str) -> None:
    ast.parse(text(path), filename=path)


def static_contract() -> None:
    for path in (
        "sicar_canonical_snapshot_v48.py",
        "portal_car_snapshot_ui_v48.py",
        "portal_car_integrity_v47.py",
        "scripts/v48_canonical_snapshot_audit.py",
    ):
        compile_gate(path)

    runtime = text("sicar_canonical_snapshot_v48.py")
    require("MAX(data_extracao)" not in runtime, "canonical runtime must never choose latest date per CAR")
    require("data_extracao=@snapshot" in runtime, "canonical runtime must pin exact manifest snapshot")
    require("car_not_present_in_canonical_snapshot" in runtime, "missing CAR must have explicit canonical absence state")
    require("Este imóvel não consta na base de" in runtime, "client absence language missing")
    require("Base do CAR de" in runtime, "client snapshot language missing")

    route = text("portal_car_integrity_v47.py")
    require("import sicar_canonical_snapshot_v48" in route, "portal route is not bound to canonical contract")
    require("import portal_car_snapshot_ui_v48" in route, "canonical client wording patch is not loaded")

    ui = text("portal_car_snapshot_ui_v48.py")
    require("Snapshot SICAR:" in ui, "UI patch must target the legacy wording explicitly")
    require("d?.user_message" in ui, "UI patch must render explicit canonical absence message")
    require("d.snapshot_label" in ui, "UI patch must render canonical UF/date label")

    audit = text("scripts/v48_canonical_snapshot_audit.py")
    require("latest_date_all_8_tables_have_rows_and_usable_geometry_per_uf" in audit, "selection rule not explicit")
    require("MAX_BYTES = 50 * 1024**3" in audit, "BigQuery cost guard missing")
    require("geometry_count" in audit and "row_count" in audit, "partition completeness evidence incomplete")
    print("RX_V48_CANONICAL_SNAPSHOT_STATIC_GATE=PASS")


def manifest_gate() -> None:
    require(MANIFEST.exists(), f"immutable manifest missing:{MANIFEST}")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require(data.get("schema_version") == "v48-sicar-canonical-snapshots-1", "manifest schema mismatch")
    require(data.get("selection_rule") == "latest_date_all_8_tables_have_rows_and_usable_geometry_per_uf", "manifest rule mismatch")
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
            require(int(evidence.get("geometry_count") or 0) > 0, f"{uf}/{table}: no usable geometry")
        require(int(item.get("area_imovel_distinct_car_count") or 0) > 0, f"{uf}: CAR count missing")

    fingerprint = str(data.get("content_fingerprint_sha256") or "")
    payload = dict(data)
    payload.pop("content_fingerprint_sha256", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest() == fingerprint, "manifest fingerprint mismatch")
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

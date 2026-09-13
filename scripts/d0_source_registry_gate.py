from __future__ import annotations

from source_audit_registry_v49 import (
    ANSWERED_STATES,
    SOURCE_COUNT,
    SOURCE_IDS,
    SOURCE_STATES,
    audit_from_registry,
    build_source_audit,
    compliance_sources,
)

EXPECTED_IDS = (
    "car", "embargo", "prodes", "indigenous_land", "legal_reserve",
    "conservation_unit", "registry", "public_forest", "snci",
    "mte_slave_labor", "sinaflor",
)


def registry_contract() -> None:
    assert SOURCE_IDS == EXPECTED_IDS, SOURCE_IDS
    assert SOURCE_COUNT == len(EXPECTED_IDS) == len(set(EXPECTED_IDS))
    assert SOURCE_STATES == frozenset({
        "NOT_QUERIED", "QUERYING", "ANSWERED_CLEAR", "ANSWERED_HIT", "BLOCKED", "FAILED"
    })
    assert ANSWERED_STATES == frozenset({"ANSWERED_CLEAR", "ANSWERED_HIT"})
    audit = build_source_audit({"car": "ANSWERED_HIT"})
    assert audit["available"] is True
    assert audit["responded"] == 1
    assert audit["total"] == SOURCE_COUNT
    assert [row["id"] for row in audit["registry"]] == list(EXPECTED_IDS)
    assert len(compliance_sources(audit)) == SOURCE_COUNT - 1

    states = {source_id: "NOT_QUERIED" for source_id in SOURCE_IDS}
    states.update({"car": "ANSWERED_HIT", "prodes": "ANSWERED_CLEAR", "sinaflor": "FAILED"})
    audit = build_source_audit(states)
    assert audit["responded"] == 2, audit
    assert audit["total"] == SOURCE_COUNT, audit

    broken = [dict(row) for row in audit["registry"]]
    broken.append(dict(broken[-1]))
    try:
        audit_from_registry(broken)
    except ValueError as exc:
        assert "source_registry_contract_drift" in str(exc)
    else:
        raise AssertionError("duplicate source accepted")


def identity_contract() -> None:
    import property_identity_runtime as identity

    old_conflict = identity.seed.conflict_by_car
    old_by_car = identity.seed.by_car
    try:
        identity.seed.conflict_by_car = lambda _code: {"names": ["A", "B"]}
        identity.seed.by_car = lambda _code: None
        ambiguous = identity._seed_identity("MG-TEST", [])
        assert ambiguous["ok"] is False and ambiguous["validation_status"] == "AMBIGUOUS", ambiguous

        identity.seed.conflict_by_car = lambda _code: None
        identity.seed.by_car = lambda _code: {"name": "Fazenda Teste", "osm_id": 1, "lat": -18.0, "lon": -44.0}
        unvalidated = identity._seed_identity("MG-TEST", [])
        assert unvalidated["ok"] is False and unvalidated["validation_status"] == "UNVALIDATED", unvalidated
    finally:
        identity.seed.conflict_by_car = old_conflict
        identity.seed.by_car = old_by_car


def main() -> None:
    registry_contract()
    identity_contract()
    ui_contract()
    print(f"D0_SOURCE_REGISTRY_GATE=PASS implemented_sources={SOURCE_COUNT} unique={len(set(SOURCE_IDS))}")
    print("D0_IDENTITY_OK_GATE=PASS unresolved_states_never_ok_true")


def ui_contract() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    panel = (root / "portal_map_panel_v45.py").read_text(encoding="utf-8")
    mte = (root / "portal_conformity_mte_v48.py").read_text(encoding="utf-8")
    sina = (root / "portal_conformity_sinaflor_v48.py").read_text(encoding="utf-8")
    anchor = (root / "portal_map_v46_anchor_state.py").read_text(encoding="utf-8")
    exact = "NÃO FOI POSSÍVEL CONFERIR AS FONTES NESTA CONSULTA."
    assert exact in panel
    assert "Os dados cadastrais do CAR permanecem visíveis, mas a conformidade não foi avaliada. Tente novamente." in panel
    assert "responded:1,total:10" not in anchor
    assert "rx48Total||11" not in mte and "m?Number(m[2]):11" not in mte
    assert 'audit["total"] =' not in mte and 'audit["total"] =' not in sina
    assert "resolução de identidade" not in mte.casefold()


if __name__ == "__main__":
    main()

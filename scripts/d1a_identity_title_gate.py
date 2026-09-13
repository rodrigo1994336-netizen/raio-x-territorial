from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAR = "MG-3152006-BB48D05173F540CD9703B23088C3ABF4"
REFERENCE = "LOTE 01 - PROJETO DE ASSENTAMENTO PAULISTA"
IDENTITY_FIELDS = (
    "name", "rx_name", "denominacao", "nome_imovel", "nome_area",
    "nome_fazenda", "nome_propriedade",
)
REFERENCE_FIELDS = (
    "reference_label", "reference_source", "reference_kind", "reference_overlap",
)


def title_contract(payload: dict) -> bool:
    name = str(payload.get("name") or "").strip()
    car = str(payload.get("car_code") or "").strip().upper()
    status = str(payload.get("name_validation_status") or payload.get("validation_status") or "").strip().upper()
    return bool(name and car and status == "VALIDATED" and payload.get("panel_name_eligible") is True)


def sanitize_payload(payload: dict) -> dict:
    safe = dict(payload)
    if not title_contract(safe):
        for field in IDENTITY_FIELDS:
            safe.pop(field, None)
    return safe


def source_contract() -> None:
    smart = (ROOT / "portal_smart_search.py").read_text(encoding="utf-8")
    legacy = (ROOT / "portal_property_identity_v13.py").read_text(encoding="utf-8")
    guard = (ROOT / "portal_identity_title_guard_v49.py").read_text(encoding="utf-8")
    names = (ROOT / "portal_property_names_v30.py").read_text(encoding="utf-8")
    site = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")

    assert "d.property.name=x.name" not in smart
    for field in REFERENCE_FIELDS:
        assert f"d.property.{field}=" in smart, field
    assert "showProperty(d.property,d.geometry)" in smart
    assert "function identityContract(id,code)" in legacy
    assert "validation_status==='VALIDATED'" in legacy
    assert "panel_name_eligible===true" in legacy
    assert "function contract(p)" in guard and "function sanitize(p)" in guard
    assert "status==='VALIDATED'" in guard
    assert "p?.panel_name_eligible===true" in guard
    assert "window.rxIdentitySanitizeV49=sanitize" in guard
    assert "delete safe.name" in guard
    assert "delete safe.reference_" not in guard
    assert "portal_identity_title_guard_v49" in site
    # C1: the map no longer draws names, so it has no path that writes a name into the card.
    assert "RX_NAMES_ON_CLICK_ONLY_C1" in names
    assert "d.property." not in names and "showProperty" not in names


def known_case_contract() -> None:
    reference_payload = {
        "name": REFERENCE,
        "car_code": CAR,
        "validation_status": "UNRESOLVED",
        "panel_name_eligible": False,
        "reference_label": REFERENCE,
        "reference_source": "SIGEF/INCRA",
        "reference_kind": "SIGEF_CADASTRAL",
        "reference_overlap": 0.9995,
    }
    safe = sanitize_payload(reference_payload)
    assert "name" not in safe
    assert safe["reference_label"] == REFERENCE
    assert safe["reference_source"] == "SIGEF/INCRA"
    assert safe["reference_kind"] == "SIGEF_CADASTRAL"
    assert safe["reference_overlap"] == 0.9995
    validated_payload = dict(reference_payload)
    validated_payload.update({"name": REFERENCE, "validation_status": "VALIDATED", "panel_name_eligible": True})
    assert sanitize_payload(validated_payload).get("name") == REFERENCE


def main() -> None:
    source_contract()
    known_case_contract()
    print(f"D1A_IDENTITY_TITLE_GATE=PASS car={CAR}")
    print("D1A_REFERENCE_FIELDS_SURVIVE=PASS")
    print(f"D1A_REFERENCE_REJECTED_AS_CAR_TITLE={REFERENCE}")


if __name__ == "__main__":
    main()

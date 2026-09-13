"""C2b: the SIGEF/INCRA reference reaches the map panel with its overlap, origin and honest state.

Deterministic (no network): SICAR, SIGEF and OSM are patched. The measured case below is the
live answer of /v1/live/property-identity/MG-3152006-BB48D05173F540CD9703B23088C3ABF4 on
13/09/2026 (SIGEF/INCRA, espelho publico IBAMA/PAMGIA, 35 candidates). overlap_ratio is the
share of the CAR area covered by the parcel; area_ratio is parcel area / CAR area.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import property_identity_runtime as identity
import portal_map_panel_v45 as panel

CAR = "MG-3152006-BB48D05173F540CD9703B23088C3ABF4"
LABEL = "PROJETO DE ASSENTAMENTO PAULISTA"
ORIGIN = "SIGEF/INCRA · espelho público IBAMA/PAMGIA"
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
CAR_ROW = {
    "ok": True,
    "properties": {"cod_imovel": CAR, "municipio": "Pompéu", "uf": "MG", "area": 1243.5656},
    "geometry": SQUARE,
    "bbox": [0, 0, 1, 1],
}
# (name, overlap_ratio, area_ratio, centroid_inside, score) exactly as measured.
MEASURED = (
    (LABEL, 0.9995, 0.9999, True, 1.1395),
    ("RESERVA LEGAL - " + LABEL, 0.2203, 0.2203, False, 0.2203),
    ("LOTE 20 - " + LABEL, 0.022, 0.022, True, 0.102),
    ("LOTE 26 - " + LABEL, 0.0302, 0.0302, False, 0.0302),
    ("LOTE 29 - " + LABEL, 0.0269, 0.0269, False, 0.0269),
)


def candidate(name, overlap, area_ratio=1.0, inside=False, score=None, code="0d94a58a"):
    return {
        "name": name, "overlap_ratio": overlap, "area_ratio": area_ratio,
        "parcel_overlap_ratio": round(overlap / area_ratio, 6) if area_ratio else None,
        "centroid_inside": inside, "score": overlap if score is None else score,
        "parcel_code": code, "property_code": "4170920078203", "registry": None,
        "municipality": "Pompéu", "uf": "MG",
        "source": "SIGEF/INCRA — espelho público IBAMA/PAMGIA",
        "display_kind": "REFERENCE", "validation_status": "UNVALIDATED", "panel_name_eligible": False,
        "reference_kind": "SIGEF_CADASTRAL", "map_anchor": "CADASTRAL_REFERENCE",
        "origin_label": "SIGEF/INCRA — referência cadastral ainda não vinculada ao CAR",
    }


def run_panel(sig, car_row=CAR_ROW, sigef_mock=None):
    identity._CACHE.clear()
    panel._CACHE.clear()
    sigef = sigef_mock or MagicMock(return_value=sig)
    no_osm = {"ok": True, "chosen": None, "items": [], "names": [], "conflict": False}
    with patch.object(identity, "fetch_car_live_resilient", return_value=car_row), \
            patch.object(identity, "_sigef_candidates", sigef), \
            patch.object(identity, "_osm_identity_candidate", return_value=no_osm), \
            patch.object(panel, "fetch_car_live_resilient", return_value=car_row):
        return panel._panel_sync(CAR), sigef


def candidates_contract():
    """The real _sigef_candidates keeps both shares, never rounds up to 100% and reports truncation."""
    parcel_a = {"type": "Polygon", "coordinates": [[[0, 0], [0.9999996, 0], [0.9999996, 1], [0, 1], [0, 0]]]}
    parcel_b = {"type": "Polygon", "coordinates": [[[0.5, 0], [3, 0], [3, 1], [0.5, 1], [0.5, 0]]]}
    raw = {"ok": True, "json": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": parcel_a, "properties": {"nome_area": "PARCELA QUASE TOTAL", "parcela_co": "a"}},
        {"type": "Feature", "geometry": parcel_b, "properties": {"nome_area": "PARCELA VIZINHA", "parcela_co": "b"}},
    ], "exceededTransferLimit": True, "properties": {"exceededTransferLimit": True}}}
    with patch.object(identity, "_curl", return_value=raw):
        out = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
    assert out["ok"] is True and out["truncated"] is True, out
    by = {x["name"]: x for x in out["items"]}
    near = by["PARCELA QUASE TOTAL"]
    assert 0.9999 <= near["overlap_ratio"] < 1.0, near
    assert 0.9999 <= near["parcel_overlap_ratio"] <= 1.0, near
    side = by["PARCELA VIZINHA"]
    assert abs(side["overlap_ratio"] - 0.5) < 1e-6 and abs(side["parcel_overlap_ratio"] - 0.2) < 1e-6, side
    with patch.object(identity, "_curl", return_value={"ok": True, "json": {"features": []}}):
        assert identity._sigef_candidates(SQUARE, [0, 0, 1, 1])["truncated"] is False


def measured_case_contract():
    items = [candidate(n, o, a, i, s) for n, o, a, i, s in MEASURED]
    result, _ = run_panel({"ok": True, "items": items, "count": 35, "truncated": False})
    ref = result["sigef_reference"]
    assert result["sigef_reference_state"] == "found", result
    assert ref["label"] == LABEL and ref["car_overlap_ratio"] == 0.9995, ref
    assert ref["kind"] == "SIGEF_CADASTRAL" and ref["origin"] == ORIGIN, ref
    assert ref["incra_property_code"] == "4170920078203" and ref["parcel_code"] == "0d94a58a", ref
    assert abs(ref["parcel_overlap_ratio"] - round(0.9995 / 0.9999, 6)) < 1e-9, ref
    assert result["sigef_reference_others"] == 0, result
    # Another registry's name is a reference, never the CAR name or title.
    assert result["validated_name"] is None and result["panel_name_eligible"] is False, result
    assert LABEL not in result["geographic_references"], result
    assert CAR in panel._CACHE and CAR in identity._CACHE
    assert identity._CACHE[CAR][1]["sigef_state"] == "answered"


def unavailable_contract():
    for sig in ({"ok": False, "detail": "timeout", "items": []}, {"items": []}):
        result, _ = run_panel(sig)
        assert result["sigef_reference_state"] == "unavailable", (sig, result)
        assert result["sigef_reference"] is None, result
        # Nothing cached, so the single automatic retry can really succeed.
        assert CAR not in panel._CACHE and CAR not in identity._CACHE, (sig, result)


def truncated_contract():
    # 200 is not all: a truncated answer without a qualifying parcel is not "none".
    result, _ = run_panel({"ok": True, "items": [candidate("LOTE", 0.30)], "count": 80, "truncated": True})
    assert result["sigef_reference_state"] in ("unavailable", "incomplete"), result
    assert result["sigef_reference_state"] != "none" and result["sigef_reference"] is None, result
    assert CAR not in panel._CACHE, result
    result, _ = run_panel({"ok": True, "items": [candidate(LABEL, 0.9995)], "count": 80, "truncated": True})
    assert result["sigef_reference_state"] == "found" and result["sigef_reference"]["label"] == LABEL, result


def threshold_contract():
    result, _ = run_panel({"ok": True, "items": [candidate("LOTE 49", 0.49)], "count": 1, "truncated": False})
    assert result["sigef_reference_state"] == "none" and result["sigef_reference"] is None, result
    assert "LOTE 49" not in result["geographic_references"], result
    # Highest overlap wins, not the score; the others above 50% are counted, not named.
    items = [candidate("A", 0.60, 1.0, True, 0.74), candidate("B", 0.70, 1.4, False, 0.70), candidate("C", 0.55)]
    result, _ = run_panel({"ok": True, "items": items, "count": 3, "truncated": False})
    assert result["sigef_reference"]["label"] == "B" and result["sigef_reference"]["car_overlap_ratio"] == 0.70, result
    assert result["sigef_reference_others"] == 2, result


def not_queried_contract():
    named = dict(CAR_ROW, properties=dict(CAR_ROW["properties"], nome_imovel="Nome cadastral de teste"))
    result, sigef = run_panel({"ok": True, "items": []}, car_row=named)
    assert not sigef.called
    assert result["sigef_reference_state"] == "not_queried" and result["sigef_reference"] is None, result
    assert identity._CACHE[CAR][1]["sigef_state"] == "not_queried"


def main():
    try:
        candidates_contract()
        measured_case_contract()
        unavailable_contract()
        truncated_contract()
        threshold_contract()
        not_queried_contract()
    finally:
        identity._CACHE.clear()
        panel._CACHE.clear()
    print(f"RX_C2B_SIGEF_REFERENCE=PASS car={CAR} label={LABEL} car_overlap=0.9995")


main()

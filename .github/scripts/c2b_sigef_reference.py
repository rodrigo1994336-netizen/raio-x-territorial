"""C2b: the SIGEF/INCRA reference reaches the map panel with its overlap, origin and honest state.

Deterministic (no network): SICAR, SIGEF and OSM are patched. The measured case below is the
live answer of /v1/live/property-identity/MG-3152006-BB48D05173F540CD9703B23088C3ABF4 on
13/09/2026 (SIGEF/INCRA, espelho publico IBAMA/PAMGIA, 35 candidates), at the 6-decimal floored
precision the server stores. overlap_ratio is the share of the CAR area covered by the parcel,
parcel_overlap_ratio the share of the parcel covered by the CAR, area_ratio parcel area / CAR area.
"""
from __future__ import annotations

import math
import threading
import time
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
MEASURED_OVERLAP = 0.999498
MEASURED_PARCEL_OVERLAP = 0.999572
# (name, overlap_ratio, parcel_overlap_ratio, area_ratio, centroid_inside, score) as measured.
MEASURED = (
    (LABEL, MEASURED_OVERLAP, MEASURED_PARCEL_OVERLAP, 0.9999, True, 1.1395),
    ("RESERVA LEGAL - " + LABEL, 0.220331, 1.0, 0.2203, False, 0.2203),
    ("LOTE 20 - " + LABEL, 0.021996, 1.0, 0.022, True, 0.102),
    ("LOTE 26 - " + LABEL, 0.030202, 0.999761, 0.0302, False, 0.0302),
    ("LOTE 29 - " + LABEL, 0.026883, 0.999035, 0.0269, False, 0.0269),
)
ARCGIS_ERROR = {"error": {"code": 400, "extendedCode": -2147467259, "message": "Unable to complete operation.", "details": []}}
NO_OSM = {"ok": True, "chosen": None, "items": [], "names": [], "conflict": False}


def candidate(name, overlap, area_ratio=1.0, inside=False, score=None, code="0d94a58a", parcel=None):
    return {
        "name": name, "overlap_ratio": overlap, "area_ratio": area_ratio,
        "parcel_overlap_ratio": parcel if parcel is not None else (round(min(overlap / area_ratio, 1.0), 6) if area_ratio else None),
        "centroid_inside": inside, "score": overlap if score is None else score,
        "parcel_code": code, "property_code": "4170920078203", "registry": None,
        "municipality": "Pompéu", "uf": "MG",
        "source": "SIGEF/INCRA — espelho público IBAMA/PAMGIA",
        "display_kind": "REFERENCE", "validation_status": "UNVALIDATED", "panel_name_eligible": False,
        "reference_kind": "SIGEF_CADASTRAL", "map_anchor": "CADASTRAL_REFERENCE",
        "origin_label": "SIGEF/INCRA — referência cadastral ainda não vinculada ao CAR",
    }


def clear():
    identity._CACHE.clear()
    panel._CACHE.clear()


def run_panel(sig, car_row=CAR_ROW, sigef_mock=None, keep_cache=False, id_fetch=None):
    if not keep_cache:
        clear()
    sigef = sigef_mock or MagicMock(return_value=sig)
    fetch = id_fetch or MagicMock(return_value=car_row)
    with patch.object(identity, "fetch_car_live_resilient", fetch), \
            patch.object(identity, "_sigef_candidates", sigef), \
            patch.object(identity, "_osm_identity_candidate", return_value=NO_OSM), \
            patch.object(panel, "fetch_car_live_resilient", return_value=car_row):
        return panel._panel_sync(CAR), sigef


def feature(name, geom, area=None, code="p"):
    props = {"nome_area": name, "parcela_co": code}
    if area is not None:
        props["Shape__Area"] = area
    return {"type": "Feature", "geometry": geom, "properties": props}


def box(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def share_contract():
    """Stored shares floor at 6 decimals without float drift and never become 100% below 100%."""
    for k in range(0, 1_000_001):
        assert identity._share(k / 1_000_000) == k / 1_000_000, k
    assert identity._share(0.5005) == 0.5005 and identity._share(0.9999996) < 1.0
    assert identity._share(float("nan")) == 0.0 and identity._share(1.7) == 1.0


def candidates_contract():
    """The real _sigef_candidates keeps both shares, never rounds up to 100% and reports truncation."""
    parcel_a = box(0, 0, 0.9999996, 1)
    parcel_b = box(0.5, 0, 3, 1)
    raw = {"ok": True, "json": {"type": "FeatureCollection", "features": [
        feature("PARCELA QUASE TOTAL", parcel_a, code="a"), feature("PARCELA VIZINHA", parcel_b, code="b"),
    ], "exceededTransferLimit": True, "properties": {"exceededTransferLimit": True}}}
    curl = MagicMock(return_value=raw)
    with patch.object(identity, "_curl", curl):
        out = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
    url = curl.call_args[0][0]
    assert "orderByFields=Shape__Area+DESC" in url and "Shape__Area" in url.split("outFields=", 1)[1], url
    assert out["ok"] is True and out["truncated"] is True and out["truncated_relevant"] is True, out
    by = {x["name"]: x for x in out["items"]}
    near = by["PARCELA QUASE TOTAL"]
    assert 0.9999 <= near["overlap_ratio"] < 1.0, near
    assert 0.9999 <= near["parcel_overlap_ratio"] <= 1.0, near
    side = by["PARCELA VIZINHA"]
    assert abs(side["overlap_ratio"] - 0.5) < 1e-6 and abs(side["parcel_overlap_ratio"] - 0.2) < 1e-6, side
    with patch.object(identity, "_curl", return_value={"ok": True, "json": {"features": []}}):
        empty = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
        assert empty["ok"] is True and empty["truncated"] is False, empty


def arcgis_error_contract():
    """HTTP 200 + {"error":...} (overload, token required, rejected query) is not an answer."""
    for body in (ARCGIS_ERROR, [], {"type": "FeatureCollection"}, {"features": None}):
        with patch.object(identity, "_curl", return_value={"ok": True, "json": body}):
            sig = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
        assert sig["ok"] is False and sig["items"] == [], (body, sig)
    clear()
    curl = MagicMock(return_value={"ok": True, "json": ARCGIS_ERROR})
    with patch.object(identity, "_curl", curl), \
            patch.object(identity, "fetch_car_live_resilient", return_value=CAR_ROW), \
            patch.object(identity, "_osm_identity_candidate", return_value=NO_OSM), \
            patch.object(panel, "fetch_car_live_resilient", return_value=CAR_ROW):
        result = panel._panel_sync(CAR)
        assert result["sigef_reference_state"] == "unavailable" and result["sigef_reference"] is None, result
        assert CAR not in panel._CACHE and CAR not in identity._CACHE, "an error reply was cached as an answer"
        # A burst of clicks shares the attempt instead of repeating SICAR + SIGEF.
        again = panel._panel_sync(CAR)
        assert again["sigef_reference_state"] == "unavailable" and curl.call_count == 1, curl.call_count
        # The explicit client retry forgets that memory, so it really asks SIGEF again.
        panel.forget_unanswered(CAR)
        panel._panel_sync(CAR)
        assert curl.call_count == 2, curl.call_count
    clear()


def measured_case_contract():
    items = [candidate(n, o, a, i, s, parcel=po) for n, o, po, a, i, s in MEASURED]
    result, _ = run_panel({"ok": True, "items": items, "count": 35, "truncated": False})
    ref = result["sigef_reference"]
    assert result["sigef_reference_state"] == "found", result
    assert ref["label"] == LABEL and ref["car_overlap_ratio"] == MEASURED_OVERLAP, ref
    assert ref["parcel_overlap_ratio"] == MEASURED_PARCEL_OVERLAP, ref
    assert ref["kind"] == "SIGEF_CADASTRAL" and ref["origin"] == ORIGIN, ref
    assert ref["incra_property_code"] == "4170920078203" and ref["parcel_code"] == "0d94a58a", ref
    assert result["sigef_reference_others"] == 0 and result["sigef_reference_others_complete"] is True, result
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
        # Nothing cached as an answer, so the single automatic retry can really succeed.
        assert CAR not in panel._CACHE and CAR not in identity._CACHE, (sig, result)
    # The retry after the source recovers finds the reference.
    panel.forget_unanswered(CAR)
    result, _ = run_panel({"ok": True, "items": [candidate(LABEL, 0.9995)], "count": 1, "truncated": False}, keep_cache=True)
    assert result["sigef_reference_state"] == "found", result


def sicar_failure_contract():
    """A SICAR failure inside identity is remembered briefly, never for the hour of an answer."""
    clear()
    failing = MagicMock(return_value={"ok": False, "not_found": True, "detail": "CAR não localizado após múltiplas estratégias"})
    with patch.object(identity, "fetch_car_live_resilient", failing):
        out = identity.resolve_property_identity_sync(CAR)
    assert out["ok"] is False and out["sigef_state"] == "unavailable" and CAR not in identity._CACHE, out
    # The map panel hands its own SICAR answer over: identity never asks SICAR again nor reuses that failure.
    fetch = MagicMock(return_value={"ok": False, "detail": "timeout"})
    result, _ = run_panel({"ok": True, "items": [candidate(LABEL, 0.9995)], "count": 1, "truncated": False}, id_fetch=fetch, keep_cache=True)
    assert not fetch.called, "identity fetched SICAR a second time"
    assert result["sigef_reference_state"] == "found", ("a remembered SICAR failure hid a fresh answer", result)


def truncated_contract():
    # 200 is not all: a truncated answer without a qualifying parcel is not "none".
    result, _ = run_panel({"ok": True, "items": [candidate("LOTE", 0.30)], "count": 80, "truncated": True})
    assert result["sigef_reference_state"] == "incomplete", result
    assert result["sigef_reference"] is None, result
    # A retry cannot change a capped answer: it is cached like any answer, never retried forever.
    assert CAR in panel._CACHE, result
    result, _ = run_panel({"ok": True, "items": [candidate(LABEL, 0.9995), candidate("B", 0.6)], "count": 80, "truncated": True})
    assert result["sigef_reference_state"] == "found" and result["sigef_reference"]["label"] == LABEL, result
    assert result["sigef_reference_others"] == 1 and result["sigef_reference_others_complete"] is False, result


def truncation_bound_contract():
    """Largest parcels first: a capped page whose smallest parcel is below the threshold is a complete answer."""
    tiny = [feature(f"LOTE {i}", box(0.001 * i, 0, 0.001 * i + 0.0005, 0.01), area=0.2 - i * 0.001) for i in range(80)]
    tiny[0] = feature("LOTE GRANDE", box(0, 0, 0.4, 1), area=0.4)
    raw = {"ok": True, "json": {"features": tiny, "exceededTransferLimit": True}}
    with patch.object(identity, "_curl", return_value=raw):
        sig = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
    assert sig["truncated"] is True and sig["truncated_relevant"] is False, sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "none", result
    # Unsorted (or no Shape__Area): the cut cannot be proven harmless.
    shuffled = list(reversed(tiny))
    with patch.object(identity, "_curl", return_value={"ok": True, "json": {"features": shuffled, "exceededTransferLimit": True}}):
        assert identity._sigef_candidates(SQUARE, [0, 0, 1, 1])["truncated_relevant"] is True
    # The smallest returned parcel could still cover half of the CAR: still incomplete.
    big = [feature(f"GLEBA {i}", box(2 + i, 2, 3 + i, 3), area=1.0) for i in range(80)]
    with patch.object(identity, "_curl", return_value={"ok": True, "json": {"features": big, "exceededTransferLimit": True}}):
        sig = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
    assert sig["truncated_relevant"] is True, sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "incomplete", result


def invalid_geometry_contract():
    """A self-intersecting CAR is repaired before measuring; an unmeasurable parcel never yields 'none'."""
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    raw = {"ok": True, "json": {"features": [feature("METADE ESQUERDA", box(0, 0, 0.5, 1), area=0.5)]}}
    with patch.object(identity, "_curl", return_value=raw):
        sig = identity._sigef_candidates(bowtie, [0, 0, 1, 1])
    assert sig["ok"] is True and sig["partial"] is False, sig
    assert [round(x["overlap_ratio"], 6) for x in sig["items"]] == [0.5], sig
    broken = {"ok": True, "json": {"features": [feature("PARCELA ILEGIVEL", {"type": "Polygon", "coordinates": [[[0, 0], [1]]]})]}}
    with patch.object(identity, "_curl", return_value=broken):
        sig = identity._sigef_candidates(SQUARE, [0, 0, 1, 1])
    assert sig["ok"] is True and sig["partial"] is True and sig["items"] == [], sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "incomplete", result


def threshold_contract():
    result, _ = run_panel({"ok": True, "items": [candidate("LOTE 49", 0.49)], "count": 1, "truncated": False})
    assert result["sigef_reference_state"] == "none" and result["sigef_reference"] is None, result
    assert "LOTE 49" not in result["geographic_references"], result
    # The parcel that best coincides with the CAR wins, not the score; the others at 50% or more are counted.
    items = [candidate("A", 0.60, 1.0, True, 0.74), candidate("B", 0.70, 1.4, False, 0.70), candidate("C", 0.55)]
    result, _ = run_panel({"ok": True, "items": items, "count": 3, "truncated": False})
    assert result["sigef_reference"]["label"] == "A" and result["sigef_reference"]["car_overlap_ratio"] == 0.60, result
    assert result["sigef_reference_others"] == 2, result
    # A settlement enclosing a lot covers 100% of the lot's CAR; the lot's own parcel is the reference.
    items = [candidate("PROJETO DE ASSENTAMENTO BREJAO", 1.0, 75.0, True, 1.08, parcel=0.013232),
             candidate("LOTE 12 DO PROJETO DE ASSENTAMENTO BREJAO", 0.999999, 1.0, True, 1.139999, parcel=0.999999)]
    result, _ = run_panel({"ok": True, "items": items, "count": 2, "truncated": False})
    assert result["sigef_reference"]["label"].startswith("LOTE 12"), result
    assert result["sigef_reference_others"] == 1, result


def not_queried_contract():
    named = dict(CAR_ROW, properties=dict(CAR_ROW["properties"], nome_imovel="Nome cadastral de teste"))
    result, sigef = run_panel({"ok": True, "items": []}, car_row=named)
    assert not sigef.called
    assert result["sigef_reference_state"] == "not_queried" and result["sigef_reference"] is None, result
    assert identity._CACHE[CAR][1]["sigef_state"] == "not_queried"


def cancelled_contract():
    clear()
    cancel = threading.Event()

    def sigef(*_args, **_kwargs):
        cancel.set()
        return {"ok": False, "cancelled": True, "items": []}

    with patch.object(identity, "_sigef_candidates", side_effect=sigef), \
            patch.object(identity, "fetch_car_live_resilient", return_value=CAR_ROW), \
            patch.object(panel, "fetch_car_live_resilient", return_value=CAR_ROW):
        out = identity.resolve_property_identity_sync(CAR, cancel_event=cancel)
        assert out["cancelled"] is True and out["sigef_state"] == "cancelled", out
        cancel.clear()
        result = panel._panel_sync(CAR, cancel_event=cancel)
    assert result["ok"] is False and result["cancelled"] is True, result
    assert not identity._CACHE and not panel._CACHE, (identity._CACHE, panel._CACHE)


def single_flight_contract():
    """Concurrent requests for one CAR share one SIGEF attempt."""
    clear()
    calls = []

    def slow(*_args, **_kwargs):
        calls.append(1)
        time.sleep(0.5)
        return {"ok": False, "detail": "timeout", "items": []}

    results = []
    with patch.object(identity, "_sigef_candidates", side_effect=slow), \
            patch.object(identity, "fetch_car_live_resilient", return_value=CAR_ROW), \
            patch.object(identity, "_osm_identity_candidate", return_value=NO_OSM):
        threads = [threading.Thread(target=lambda: results.append(identity.resolve_property_identity_sync(CAR))) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert len(calls) == 1 and len(results) == 3, (calls, results)
    assert all(r["sigef_state"] == "unavailable" for r in results), results
    assert not identity._INFLIGHT, identity._INFLIGHT
    clear()


def main():
    try:
        share_contract()
        candidates_contract()
        arcgis_error_contract()
        measured_case_contract()
        unavailable_contract()
        sicar_failure_contract()
        truncated_contract()
        truncation_bound_contract()
        invalid_geometry_contract()
        threshold_contract()
        not_queried_contract()
        cancelled_contract()
        single_flight_contract()
    finally:
        clear()
    assert math.floor(MEASURED_OVERLAP * 10000) / 100 == 99.94
    print(f"RX_C2B_SIGEF_REFERENCE=PASS car={CAR} label={LABEL} car_overlap={MEASURED_OVERLAP}")


main()

"""C2b: the SIGEF/INCRA reference reaches the map panel with its overlap, origin and honest state.

Deterministic (no network): SICAR, the INCRA Acervo Fundiário transport and OSM are patched. The
measured ranking case below is the live answer of
/v1/live/property-identity/MG-3152006-BB48D05173F540CD9703B23088C3ABF4 on 13/09/2026 (then read from
the PAMGIA mirror, 35 candidates), at the 6-decimal floored precision the server stores; the ranking
contract does not depend on the source. F2: the real _sigef_candidates now asks the official INCRA
layers (SIGEF particular/público, SNCI privado/público) through incra_acervo_f2.
overlap_ratio is the share of the CAR area covered by the parcel,
parcel_overlap_ratio the share of the parcel covered by the CAR, area_ratio parcel area / CAR area.
"""
from __future__ import annotations

import math
import threading
import time
from unittest.mock import MagicMock, patch

import incra_acervo_f2 as acervo
import property_identity_runtime as identity
import portal_map_panel_v45 as panel

CAR = "MG-3152006-BB48D05173F540CD9703B23088C3ABF4"
LABEL = "PROJETO DE ASSENTAMENTO PAULISTA"
ORIGIN = "Acervo Fundiário do INCRA (SIGEF)"
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
SERVICE_EXCEPTION = (b"<?xml version='1.0' encoding=\"UTF-8\" ?><ServiceExceptionReport version=\"1.2.0\">"
                     b"<ServiceException>msWFSGetFeature(): WFS server error. Invalid or Unsupported FILTER</ServiceException></ServiceExceptionReport>")
NO_OSM = {"ok": True, "chosen": None, "items": [], "names": [], "conflict": False}


def candidate(name, overlap, area_ratio=1.0, inside=False, score=None, code="0d94a58a", parcel=None):
    return {
        "name": name, "overlap_ratio": overlap, "area_ratio": area_ratio,
        "parcel_overlap_ratio": parcel if parcel is not None else (round(min(overlap / area_ratio, 1.0), 6) if area_ratio else None),
        "centroid_inside": inside, "score": overlap if score is None else score,
        "parcel_code": code, "property_code": "4170920078203", "registry": None,
        "municipality": "Pompéu", "uf": "MG",
        "source": ORIGIN,
        "display_kind": "REFERENCE", "validation_status": "UNVALIDATED", "panel_name_eligible": False,
        "reference_kind": "SIGEF_CADASTRAL", "map_anchor": "CADASTRAL_REFERENCE",
        "origin_label": ORIGIN + " — referência cadastral ainda não vinculada ao CAR",
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


def box(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def gml(theme, parcels):
    """A GML2 FeatureCollection as the INCRA i3Geo WFS writes it. parcels: (fields, GeoJSON Polygon | raw GML)."""
    members = []
    for fields, geom in parcels:
        if isinstance(geom, str):
            geom_xml = geom
        else:
            coords = " ".join(f"{x},{y}" for x, y in geom["coordinates"][0])
            geom_xml = ('<gml:Polygon srsName="EPSG:4326"><gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>'
                        f"{coords}</gml:coordinates></gml:LinearRing></gml:outerBoundaryIs></gml:Polygon>")
        props = "".join(f"<ms:{k}>{v}</ms:{k}>" for k, v in fields.items())
        members.append(f"<gml:featureMember><ms:{theme}><ms:msGeometry>{geom_xml}</ms:msGeometry>{props}</ms:{theme}></gml:featureMember>")
    head = ('<?xml version="1.0" encoding="UTF-8" ?><wfs:FeatureCollection xmlns:ms="http://www.omsug.ca/osgis2004" '
            'xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml">'
            "<gml:boundedBy><gml:null>missing</gml:null></gml:boundedBy>")
    return (head + "".join(members) + "</wfs:FeatureCollection>").encode("utf-8")


def incra(by_theme=None, default=None, calls=None):
    """Fake INCRA transport: body per theme prefix (a callable(url) is allowed), empty FeatureCollection otherwise."""
    by_theme = by_theme or {}

    def fetch(url, cancel_event=None):
        if calls is not None:
            calls.append(url)
        theme = url.split("tema=", 1)[1].split("&", 1)[0]
        for prefix, body in by_theme.items():
            if theme.startswith(prefix):
                body = body(url) if callable(body) else body
                return body if isinstance(body, dict) else {"ok": True, "body": body}
        if default is not None:
            return default if isinstance(default, dict) else {"ok": True, "body": default}
        return {"ok": True, "body": gml(theme, [])}
    return fetch


def sigef_fields(name, code):
    return {"parcela_codigo": code, "codigo_imovel": "9501811074685", "status": "CERTIFICADA", "data_aprovacao": "2025-06-18",
            "nome_area": name, "rt": "SENTINELA", "art": "SENTINELA", "registro_matricula": "SENTINELA"}


def candidates(fetch, geom=SQUARE):
    with patch.object(acervo, "curl_fetch", fetch), patch.object(acervo.time, "sleep", lambda _s: None):
        return identity._sigef_candidates(geom, [0, 0, 1, 1], uf="MG")


def share_contract():
    """Stored shares floor at 6 decimals without float drift and never become 100% below 100%."""
    for k in range(0, 1_000_001):
        assert identity._share(k / 1_000_000) == k / 1_000_000, k
    assert identity._share(0.5005) == 0.5005 and identity._share(0.9999996) < 1.0
    assert identity._share(float("nan")) == 0.0 and identity._share(1.7) == 1.0
    assert acervo.floor_share(0.9999996) < 1.0 and acervo.floor_share(0.5005) == 0.5005


def candidates_contract():
    """The real _sigef_candidates asks the official INCRA layers, keeps both shares and never rounds up to 100%."""
    calls = []
    body = gml("certificada_sigef_particular_mg", [
        (sigef_fields("PARCELA QUASE TOTAL", "a"), box(0, 0, 0.9999996, 1)),
        (sigef_fields("PARCELA VIZINHA", "b"), box(0.5, 0, 3, 1)),
    ])
    out = candidates(incra({"certificada_sigef_particular": body}, calls=calls))
    themes = sorted(u.split("tema=", 1)[1].split("&", 1)[0] for u in calls)
    assert themes == ["certificada_sigef_particular_mg", "certificada_sigef_publico_mg", "imoveiscertificados_privado_mg", "imoveiscertificados_publico_mg"], themes
    assert all("outputFormat=GML2" in u and "bbox=" in u and "maxFeatures=500" in u and "pamgia" not in u for u in calls), calls
    assert out["ok"] is True and out["truncated"] is False and out["partial"] is False, out
    by = {x["name"]: x for x in out["items"]}
    near = by["PARCELA QUASE TOTAL"]
    assert 0.9999 <= near["overlap_ratio"] < 1.0, near
    assert 0.9999 <= near["parcel_overlap_ratio"] <= 1.0, near
    side = by["PARCELA VIZINHA"]
    # Geodesic GRS80 areas: edges are geodesics, so a lon/lat box share differs from the planar one by < 0.02%.
    assert abs(side["overlap_ratio"] - 0.5) < 2e-4 and abs(side["parcel_overlap_ratio"] - 0.2) < 2e-4, side
    assert near["reference_kind"] == "SIGEF_CADASTRAL" and near["origin"] == ORIGIN, near
    assert "SENTINELA" not in repr(out), "a personal/registry field reached the card candidates"
    empty = candidates(incra())
    assert empty["ok"] is True and empty["items"] == [] and empty["truncated"] is False, empty


def service_error_contract():
    """HTTP 200 with an empty body, an exception report or invalid XML is not an answer."""
    for body in (b"", SERVICE_EXCEPTION, b"<html>erro</html", b"<ows:ExceptionReport xmlns:ows='x'>WFS request not enabled</ows:ExceptionReport>"):
        sig = candidates(incra(default=body))
        assert sig["ok"] is False and sig["items"] == [], (body, sig)
    clear()
    calls = []
    fetch = incra(default=SERVICE_EXCEPTION, calls=calls)
    with patch.object(acervo, "curl_fetch", fetch), patch.object(acervo.time, "sleep", lambda _s: None), \
            patch.object(identity, "fetch_car_live_resilient", return_value=CAR_ROW), \
            patch.object(identity, "_osm_identity_candidate", return_value=NO_OSM), \
            patch.object(panel, "fetch_car_live_resilient", return_value=CAR_ROW):
        result = panel._panel_sync(CAR)
        assert result["sigef_reference_state"] == "unavailable" and result["sigef_reference"] is None, result
        assert CAR not in panel._CACHE and CAR not in identity._CACHE, "an error reply was cached as an answer"
        first = len(calls)
        assert first == 8, calls  # 4 layers, one automatic retry each
        # A burst of clicks shares the attempt instead of repeating SICAR + INCRA.
        again = panel._panel_sync(CAR)
        assert again["sigef_reference_state"] == "unavailable" and len(calls) == first, len(calls)
        # The explicit client retry forgets that memory, so it really asks INCRA again.
        panel.forget_unanswered(CAR)
        panel._panel_sync(CAR)
        assert len(calls) == 2 * first, len(calls)
    clear()


def disabled_contract():
    """RX_INCRA_ACERVO_ENABLED off: nothing is asked and the card is pending, never 'none'."""
    calls = []
    with patch.dict("os.environ", {"RX_INCRA_ACERVO_ENABLED": "0"}):
        sig = candidates(incra(calls=calls))
    assert sig["ok"] is False and sig["detail"] == "incra_acervo_disabled" and not calls, (sig, calls)
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "unavailable", result


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


def truncation_split_contract():
    """count == maxFeatures: the box is split; still cut after two levels -> not answered, never 'none'."""
    lots = [(sigef_fields(f"LOTE {i}", f"l{i}"),
             box(0.001 * (i % 100), 0.001 * (i // 100), 0.001 * (i % 100) + 0.0005, 0.001 * (i // 100) + 0.0005)) for i in range(500)]
    full = gml("certificada_sigef_particular_mg", lots)
    sig = candidates(incra({"certificada_sigef_particular": full}))
    assert sig["ok"] is False and sig["items"] == [] and "truncated_after_split" in sig["detail"], sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "unavailable", result
    # First answer cut, quadrants complete: merged once each (a parcel crossing quadrants is not counted twice).
    whole = gml("certificada_sigef_particular_mg", [(sigef_fields("GLEBA INTEIRA", "g"), box(0, 0, 1, 1))])
    first = [True]

    def body(_url):
        if first[0]:
            first[0] = False
            return full
        return whole
    sig = candidates(incra({"certificada_sigef_particular": body}))
    assert sig["ok"] is True and [x["name"] for x in sig["items"]] == ["GLEBA INTEIRA"], sig


def invalid_geometry_contract():
    """A self-intersecting CAR is repaired before measuring; an unmeasurable parcel never yields 'none'."""
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    half = gml("certificada_sigef_particular_mg", [(sigef_fields("METADE ESQUERDA", "m"), box(0, 0, 0.5, 1))])
    sig = candidates(incra({"certificada_sigef_particular": half}), geom=bowtie)
    assert sig["ok"] is True and sig["partial"] is False, sig
    assert [round(x["overlap_ratio"], 3) for x in sig["items"]] == [0.5], sig
    broken_geom = ("<gml:Polygon><gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>0,0 1"
                   "</gml:coordinates></gml:LinearRing></gml:outerBoundaryIs></gml:Polygon>")
    broken = gml("certificada_sigef_particular_mg", [(sigef_fields("PARCELA ILEGIVEL", "x"), broken_geom)])
    sig = candidates(incra({"certificada_sigef_particular": broken}))
    assert sig["ok"] is False and sig["items"] == [], sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "unavailable", result


def snci_contract():
    """F2: an SNCI certification is an INCRA reference too, with its own kind, origin and number (never the title)."""
    fields = {"num_certificacao": "061308000091-60", "data_certificacao": "2013-08-06 10:29:03", "qtd_area_peca_tecnica": "178.7390",
              "nome_imovel": "IMOVEL DE REFERENCIA 01", "cod_profissional_credenciado": "SENTINELA", "num_processo": "SENTINELA"}
    snci = gml("imoveiscertificados_privado_mg", [(fields, box(0, 0, 1, 1))])
    sig = candidates(incra({"imoveiscertificados_privado": snci}))
    assert sig["ok"] is True and len(sig["items"]) == 1 and "SENTINELA" not in repr(sig), sig
    result, _ = run_panel(sig)
    ref = result["sigef_reference"]
    assert result["sigef_reference_state"] == "found" and ref["kind"] == "SNCI_CADASTRAL", result
    assert ref["origin"] == "Acervo Fundiário do INCRA (SNCI)" and ref["certification"] == "061308000091-60", ref
    assert ref["car_overlap_ratio"] == 1.0 and ref["detail"] == "nº 061308000091-60 · 06/08/2013", ref
    assert ref["label"] == "IMOVEL DE REFERENCIA 01", ref
    assert result["validated_name"] is None and result["panel_name_eligible"] is False, result
    # One layer did not answer but a parcel covering the CAR was found: shown, the count of others is a floor.
    sig = candidates(incra({"imoveiscertificados_privado": snci, "certificada_sigef_publico": {"ok": False, "detail": "timeout"}}))
    assert sig["ok"] is True and sig["partial"] is True, sig
    result, _ = run_panel(sig)
    assert result["sigef_reference_state"] == "found" and result["sigef_reference_others_complete"] is False, result


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
        service_error_contract()
        disabled_contract()
        measured_case_contract()
        unavailable_contract()
        sicar_failure_contract()
        truncated_contract()
        truncation_split_contract()
        invalid_geometry_contract()
        snci_contract()
        threshold_contract()
        not_queried_contract()
        cancelled_contract()
        single_flight_contract()
    finally:
        clear()
    assert math.floor(MEASURED_OVERLAP * 10000) / 100 == 99.94
    print(f"RX_C2B_SIGEF_REFERENCE=PASS car={CAR} label={LABEL} car_overlap={MEASURED_OVERLAP}")


main()

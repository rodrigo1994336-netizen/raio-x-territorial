"""F2 gate: SIGEF and SNCI come from the official INCRA base; the PAMGIA mirror never says "none".

Usage (offline, no network: every socket and every curl subprocess is refused):
  PYTHONPATH=. python scripts/f2_incra_sigef_snci_gate.py            # everything
  PYTHONPATH=. python scripts/f2_incra_sigef_snci_gate.py --no-card  # skip the card contract (no portal import)

Fixtures in tests/fixtures/f2_incra_sigef_snci/ are live answers recorded on 13/09/2026 (see README.md),
with personal and registry fields replaced by a sentinel and property names pseudonymised.

Positive control: on the code before F2 this gate fails in mirror_never_absence_contract (the report
prints "0 parcela(s)" and "CONSULTADO" from the empty public mirror), in card_contract (the card still
asks the mirror) and in all_places_contract.
"""
from __future__ import annotations

import copy
import json
import os
import re
import socket
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "f2_incra_sigef_snci"
MANIFEST = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
SENTINEL = "SENTINELA-LGPD-REMOVIDO"
PERSONAL_KEYS = ("rt", "art", "cod_profissional_credenciado", "num_processo", "registro_matricula", "registro_data")
TEST_CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
SNCI_CAR = "MG-3120904-528DEBA144FF4CEE994BA14D1ABD0E71"
# Words that state an absence. From the mirror they are always wrong; only the official answer may say them.
ABSENCE = re.compile(r"\b0 parcela|parcela\(s\) candidata|n[ãa]o h[áa] (?:parcela|certifica)|sem certifica|n[ãa]o possui", re.I)


# ---------------------------------------------------------------------------------------------
# offline guard and fixture transport
# ---------------------------------------------------------------------------------------------

class NetworkRefused(RuntimeError):
    pass


def _refuse(*_args, **_kwargs):
    raise NetworkRefused("network is disabled in the offline F2 gate")


_REAL_POPEN = subprocess.Popen


class _NoCurlPopen(_REAL_POPEN):
    def __init__(self, args, *a, **k):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if "curl" in str(first).lower():
            raise NetworkRefused(f"curl refused in the offline F2 gate: {args!r}"[:200])
        super().__init__(args, *a, **k)


def offline():
    socket.socket.connect = _refuse
    socket.create_connection = _refuse
    subprocess.Popen = _NoCurlPopen


def car(label):
    return json.loads((FIX / f"car_{label}.geojson").read_text(encoding="utf-8"))


def fixture_fetch(calls=None, override=None):
    """Serve the recorded body for (theme, bbox); an unknown request is a failure, never an empty answer."""
    boxes = {label: entry["bbox"] for label, entry in MANIFEST["cars"].items()}

    def fetch(url, cancel_event=None):
        q = parse_qs(urlsplit(url).query)
        theme = q["tema"][0]
        bbox = [float(v) for v in q["bbox"][0].split(",")]
        if calls is not None:
            calls.append(theme)
        if override and theme in override:
            value = override[theme]
            return value(url) if callable(value) else value
        for label, recorded in boxes.items():
            if all(abs(a - b) <= 2e-6 for a, b in zip(bbox, recorded)):
                path = FIX / f"{label}__{theme}.gml"
                if path.exists():
                    return {"ok": True, "body": path.read_bytes()}
        return {"ok": False, "detail": f"no_fixture:{theme}:{bbox}"}
    return fetch


def acervo_for(label, **kwargs):
    import incra_acervo_f2 as acervo

    feature = car(label)
    with patch.object(acervo.time, "sleep", lambda _s: None):
        return acervo.query_incra_acervo(feature["geometry"], "MG", fetch=kwargs.pop("fetch", None) or fixture_fetch(), **kwargs)


def texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from texts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from texts(v)


def jsonable(obj):
    """Result without shapely geometries, for the LGPD scan."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items() if k != "geometry"}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj if isinstance(obj, (str, int, float, bool, type(None))) else repr(type(obj))


# ---------------------------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------------------------

def switch_contract():
    import incra_acervo_f2 as acervo

    assert acervo.enabled({}) is True and acervo.enabled({"RX_INCRA_ACERVO_ENABLED": ""}) is True
    for off in ("0", "false", "OFF", "no", "não"):
        assert acervo.enabled({"RX_INCRA_ACERVO_ENABLED": off}) is False, off
    assert acervo.enabled({"RX_INCRA_ACERVO_ENABLED": "1"}) is True
    calls = []
    out = acervo.query_incra_acervo(car("vizinho_snci")["geometry"], "MG", fetch=fixture_fetch(calls), env={"RX_INCRA_ACERVO_ENABLED": "0"})
    assert not calls and out["enabled"] is False, (calls, out)
    assert out["sigef"]["state"] == "pending" and out["snci"]["state"] == "pending", out
    view = acervo.land_payload(out)
    assert view["sigef"]["status"] == "CONSULTA PENDENTE" and view["snci"]["status"] == "CONSULTA PENDENTE", view
    assert not any(ABSENCE.search(t) for t in texts(view)), view


def parser_controls_contract():
    """Real negative controls: none of them may become an answer (and therefore a zero)."""
    import incra_acervo_f2 as acervo

    for name in ("control__rj_certificada_sigef_publico.xml", "control__unknown_theme.xml", "control__json_output.xml"):
        parsed = acervo.parse_wfs_gml((FIX / name).read_bytes(), "sigef")
        assert parsed["answered"] is False and parsed["features"] == [], (name, parsed)
    assert acervo.parse_wfs_gml(b"", "snci")["detail"] == "empty_body"
    assert "service_exception" in acervo.parse_wfs_gml((FIX / "control__json_output.xml").read_bytes(), "snci")["detail"]
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs">&a;</wfs:FeatureCollection>'
    assert acervo.parse_wfs_gml(evil, "sigef")["detail"] == "xml_doctype_refused"
    assert acervo.parse_wfs_gml(b"<html><body>erro</body></html>", "sigef")["answered"] is False
    body = (FIX / "vizinho_sigef2025__certificada_sigef_particular_mg.gml").read_bytes()
    bbox = MANIFEST["cars"]["vizinho_sigef2025"]["bbox"]
    ok = acervo.parse_wfs_gml(body, "sigef", bbox=bbox)
    assert ok["answered"] and ok["count"] >= 1 and not ok["truncated"], ok
    # A feature outside the requested box (axis order swapped, box ignored) invalidates the answer.
    far = acervo.parse_wfs_gml(body, "sigef", bbox=(10.0, 10.0, 10.1, 10.1))
    assert far["answered"] is False and far["detail"] == "feature_outside_requested_bbox", far
    # count == maxFeatures is not "all".
    capped = acervo.parse_wfs_gml(body, "sigef", max_features=ok["count"], bbox=bbox)
    assert capped["answered"] and capped["truncated"], capped
    # LGPD: only white-listed fields are read.
    for props, _geom in ok["features"]:
        assert not set(props) & set(PERSONAL_KEYS) and SENTINEL not in json.dumps(props), props
    empty = acervo.parse_wfs_gml((FIX / "curvelo_teste__imoveiscertificados_privado_mg.gml").read_bytes(), "snci")
    assert empty["answered"] is True and empty["count"] == 0 and empty["truncated"] is False, empty


def real_cases_contract():
    """Recorded cases: Curvelo without certification, neighbour with SNCI ~100%, neighbours with private SIGEF ~99,9%."""
    import incra_acervo_f2 as acervo

    cur = acervo_for("curvelo_teste")
    assert cur["sigef"]["state"] == "not_found" and cur["snci"]["state"] == "not_found", (cur["sigef"], cur["snci"])
    assert all(i["answered"] for f in ("sigef", "snci") for i in cur[f]["layers"].values()), cur
    view = acervo.land_payload(cur)
    assert view["sigef"]["text"] == "Não há parcela SIGEF certificada sobre o imóvel." and view["sigef"]["status"] == "SEM CERTIFICAÇÃO", view["sigef"]
    assert view["snci"]["text"] == "Não há certificação SNCI sobre o imóvel.", view["snci"]

    snci = acervo_for("vizinho_snci")
    assert snci["snci"]["state"] == "found" and snci["sigef"]["state"] == "not_found", (snci["snci"]["state"], snci["sigef"]["state"])
    best = snci["snci"]["rows"][0]
    assert best["props"].get("num_certificacao") == "061308000091-60" and best["layer"] == "privado", best["props"]
    assert 0.9999 <= best["car_share"] <= 1.0, best["car_share"]
    view = acervo.land_payload(snci)["snci"]
    assert view["status"] == "CERTIFICADO" and view["coverage"] == "covers", view
    for piece in ("Certificado no SNCI (INCRA)", "nº 061308000091-60", "06/08/2013", "178,74 ha"):
        assert piece in view["text"], (piece, view["text"])
    assert re.search(r"cobre (?:100|99,99)% do imóvel", view["text"]), view["text"]

    for label, date, status_text, low in (("vizinho_sigef2025", "18/06/2025", "certificada", 0.998),
                                          ("vizinho_sigef2021", "28/05/2021", "certificada e registrada em cartório", 0.999)):
        res = acervo_for(label)
        assert res["sigef"]["state"] == "found" and res["snci"]["state"] == "not_found", (label, res["sigef"]["state"], res["snci"]["state"])
        row = res["sigef"]["rows"][0]
        assert row["layer"] == "particular" and low <= row["car_share"] < 1.0, (label, row["layer"], row["car_share"])
        view = acervo.land_payload(res)["sigef"]
        assert view["status"] == "CERTIFICADO" and f"aprovada em {date}" in view["text"] and status_text in view["text"], (label, view)
        assert re.search(r"cobre 99,\d\d% do imóvel", view["text"]), (label, view["text"])
        # The public mirror the report used to read has nothing for this property.
        mirror = json.loads((FIX / f"{label}__pamgia_sigef_publico_10.json").read_text(encoding="utf-8"))
        assert mirror["ok"] is True and mirror["features"] == [], mirror
    assert pct_floor_contract() is None


def pct_floor_contract():
    import incra_acervo_f2 as acervo

    assert acervo.pct_floor_text(1.0) == "100%" and acervo.pct_floor_text(0.999999) == "99,99%"
    assert acervo.pct_floor_text(0.998478) == "99,84%" and acervo.pct_floor_text(0.5) == "50%" and acervo.pct_floor_text(0.37) == "37%"
    assert acervo.floor_share(0.99999999) == 0.999999


def _legacy_result(label, acervo=None):
    feature = car(label)
    props = dict(feature["properties"])
    props.update(status_imovel="AT", condicao="Aguardando análise", tipo_imovel="IRU")
    from shapely.geometry import shape

    result = {
        "car": {"ok": True, "properties": props, "geometry": feature["geometry"], "bbox": list(shape(feature["geometry"]).bounds)},
        # What deploy_app.query_sigef returns for this CAR today: the public mirror answered with nothing.
        "sigef": {"ok": True, "feature_count": 0, "features": json.loads((FIX / f"{label}__pamgia_sigef_publico_10.json").read_text(encoding="utf-8"))["features"],
                  "source": "IBAMA/PAMGIA espelho público SIGEF-INCRA"},
        "embargos_ibama": {"ok": True, "feature_count": 0, "exact": {"occurrence_count": 0}},
        "anm": {"ok": True, "feature_count": 0, "exact": {"occurrence_count": 0}},
        "prodes": {"ok": True, "feature_count": 0, "exact": {"occurrence_count": 0, "area_unique_ha": 0}},
    }
    if acervo is not None:
        result["incra_acervo"] = acervo
    return result


def _report_payload(result):
    import live_report_adapter
    import report_ptbr_v50
    import report_truth_guard_v16

    payload = live_report_adapter.build_live_payload(result, "RX-F2-GATE", "2026-09-13T21:00:00+00:00", "")
    payload = report_truth_guard_v16.comprehensive_truth_guard(payload, result)
    return payload, report_ptbr_v50.client_payload(payload)


def _cert_rows(client):
    return {str(r[0]).split(" ")[0].upper(): r for r in client["land"]["certifications"]}


def mirror_never_absence_contract():
    """An empty public mirror never becomes '0 parcela' / 'não possui' anywhere in the report."""
    for label in MANIFEST["cars"]:
        payload, client = _report_payload(_legacy_result(label))
        leaks = sorted({t for t in texts(client) if ABSENCE.search(t)})
        assert not leaks, (label, leaks)
        rows = _cert_rows(client)
        assert rows["SIGEF"][1] == "CONSULTA PENDENTE" and rows["SNCI"][1] == "CONSULTA PENDENTE", (label, rows)
        sources = {s["name"]: s["status"] for s in client["sources"] if "INCRA" in s["name"] or "SIGEF" in s["name"] or "SNCI" in s["name"]}
        assert sources and all(v == "CONSULTA PENDENTE" for v in sources.values()), (label, sources)
        assert "SIGEF" not in payload["conclusion"]["coverage"]["consulted_core"], payload["conclusion"]["coverage"]
        compliance = {c["label"]: c["badge"] for c in client.get("compliance") or [] if c.get("label") in ("SIGEF", "SNCI")}
        assert compliance.get("SIGEF") == "CONSULTA PENDENTE", compliance
    # RX_INCRA_ACERVO_ENABLED off: the report is pending, never zero, and nothing is asked.
    calls = []
    with patch.dict(os.environ, {"RX_INCRA_ACERVO_ENABLED": "off"}):
        off = acervo_for("vizinho_sigef2025", fetch=fixture_fetch(calls))
    _payload, client = _report_payload(_legacy_result("vizinho_sigef2025", off))
    assert not calls and not any(ABSENCE.search(t) for t in texts(client)), calls
    assert _cert_rows(client)["SIGEF"][1] == "CONSULTA PENDENTE"
    # The legacy analysis summary no longer carries the mirror envelope count.
    import deploy_app

    item = deploy_app._safe_summary(_legacy_result("vizinho_sigef2025"))["sigef"]
    assert item["ok"] is None and item["state"] == "pending" and "feature_count_bbox" not in item, item


def official_report_contract():
    """With the official answer wired, the report says what INCRA says."""
    _p, client = _report_payload(_legacy_result("vizinho_snci", acervo_for("vizinho_snci")))
    rows = _cert_rows(client)
    assert rows["SNCI"][1] == "CERTIFICADO" and "061308000091-60" in rows["SNCI"][3], rows
    assert rows["SIGEF"][1] == "SEM CERTIFICAÇÃO", rows
    assert "061308000091-60" in client["land"]["summary"], client["land"]["summary"]
    sources = {s["name"]: s["status"] for s in client["sources"] if "Acervo" in s["name"]}
    assert sources == {"INCRA — Acervo Fundiário (SIGEF)": "CONSULTADA", "INCRA — Acervo Fundiário (SNCI)": "CONSULTADA"}, sources
    assert not any("PAMGIA" in t or "espelho" in t.lower() for t in texts(client["land"])), client["land"]
    payload, client = _report_payload(_legacy_result("vizinho_sigef2021", acervo_for("vizinho_sigef2021")))
    rows = _cert_rows(client)
    assert rows["SIGEF"][1] == "CERTIFICADO" and "28/05/2021" in rows["SIGEF"][3], rows
    assert "SIGEF" in payload["conclusion"]["coverage"]["consulted_core"]
    # Idempotent: applying twice changes nothing.
    import incra_acervo_f2 as acervo

    again = acervo.apply_to_report_payload(copy.deepcopy(payload), _legacy_result("vizinho_sigef2021", acervo_for("vizinho_sigef2021"))["incra_acervo"])
    assert again["land"] == payload["land"] and again["sources"] == payload["sources"], "apply_to_report_payload is not idempotent"


def partial_failure_contract():
    """A layer that did not answer makes absence pending; presence found in another layer stays found."""
    import incra_acervo_f2 as acervo

    down = {"ok": False, "detail": "curl_exit_28:timeout"}
    res = acervo_for("curvelo_teste", fetch=fixture_fetch(override={"imoveiscertificados_privado_mg": down}))
    assert res["snci"]["state"] == "pending" and res["sigef"]["state"] == "not_found", (res["snci"], res["sigef"]["state"])
    res = acervo_for("curvelo_teste", fetch=fixture_fetch(override={"certificada_sigef_publico_mg": {"ok": True, "body": b""}}))
    assert res["sigef"]["state"] == "pending", res["sigef"]
    res = acervo_for("vizinho_sigef2025", fetch=fixture_fetch(override={"certificada_sigef_publico_mg": down}))
    assert res["sigef"]["state"] == "found" and res["sigef"]["complete"] is False, res["sigef"]["state"]

    def explode(_url):
        raise RuntimeError("unexpected transport failure")
    res = acervo_for("curvelo_teste", fetch=fixture_fetch(override={"imoveiscertificados_publico_mg": explode}))
    assert res["snci"]["state"] == "pending" and res["sigef"]["state"] == "not_found", (res["snci"]["state"], res["sigef"]["state"])
    # Retry once: a first failure followed by the real answer is an answer.
    attempts = []

    def flaky(url):
        attempts.append(url)
        if len(attempts) == 1:
            return down
        return {"ok": True, "body": (FIX / "vizinho_snci__imoveiscertificados_privado_mg.gml").read_bytes()}
    res = acervo_for("vizinho_snci", fetch=fixture_fetch(override={"imoveiscertificados_privado_mg": flaky}))
    assert len(attempts) == 2 and res["snci"]["state"] == "found", (len(attempts), res["snci"]["state"])
    # Cut short even after splitting the box: pending, never not_found.
    body = (FIX / "curvelo_teste__certificada_sigef_particular_mg.gml").read_bytes()
    capped = acervo.fetch_layer("certificada_sigef_particular_mg", "sigef", tuple(MANIFEST["cars"]["curvelo_teste"]["bbox"]),
                                fetch=lambda url, cancel_event=None: {"ok": True, "body": body}, max_features=0)
    assert capped["answered"] is False and capped["detail"] == "truncated_after_split" and capped["requests"] == 3, capped
    # Unmeasurable geometry: pending.
    broken = body.replace(b"</gml:boundedBy>\n</wfs:FeatureCollection>", b"</gml:boundedBy><gml:featureMember><ms:x><ms:msGeometry><gml:Polygon><gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>1,2</gml:coordinates></gml:LinearRing></gml:outerBoundaryIs></gml:Polygon></ms:msGeometry></ms:x></gml:featureMember></wfs:FeatureCollection>")
    assert broken != body, "fixture shape changed: cannot build the unmeasurable control"
    res = acervo_for("curvelo_teste", fetch=fixture_fetch(override={"certificada_sigef_particular_mg": {"ok": True, "body": broken}}))
    assert res["sigef"]["state"] == "pending", res["sigef"]
    # Cancelled: nothing is concluded.
    import threading

    ev = threading.Event()
    ev.set()
    res = acervo_for("vizinho_snci", cancel_event=ev)
    assert res.get("cancelled") and res["snci"]["state"] == "pending", res


def lgpd_contract():
    import incra_acervo_f2 as acervo

    for label in MANIFEST["cars"]:
        res = acervo_for(label)
        dumped = json.dumps(jsonable(res), ensure_ascii=False) + json.dumps(acervo.land_payload(res), ensure_ascii=False)
        dumped += json.dumps(acervo.reference_candidates(res), ensure_ascii=False)
        assert SENTINEL not in dumped, label
        for key in PERSONAL_KEYS:
            assert f'"{key}"' not in dumped, (label, key)
        _p, client = _report_payload(_legacy_result(label, res))
        assert SENTINEL not in json.dumps(client, ensure_ascii=False, default=str), label
        # Names of another registry never enter the report (they may carry a person's name).
        assert "IMOVEL DE REFERENCIA" not in json.dumps(client, ensure_ascii=False, default=str), label


def card_contract():
    """The card's INCRA reference is official (SIGEF + SNCI), a reference, never a title; C2b thresholds kept."""
    from unittest.mock import MagicMock

    import incra_acervo_f2 as acervo
    import portal_map_panel_v45 as panel
    import property_identity_runtime as identity

    no_osm = {"ok": True, "chosen": None, "items": [], "names": [], "conflict": False}

    def panel_for(label, env=None):
        feature = car(label)
        from shapely.geometry import shape

        code = feature["properties"]["cod_imovel"]
        row = {"ok": True, "properties": feature["properties"], "geometry": feature["geometry"], "bbox": list(shape(feature["geometry"]).bounds)}
        identity._CACHE.clear()
        panel._CACHE.clear()
        calls = []
        with patch.object(acervo, "curl_fetch", fixture_fetch(calls)), patch.object(acervo.time, "sleep", lambda _s: None), \
                patch.dict(os.environ, env or {}), \
                patch.object(identity, "fetch_car_live_resilient", MagicMock(return_value=row)), \
                patch.object(identity, "_osm_identity_candidate", return_value=no_osm), \
                patch.object(panel, "fetch_car_live_resilient", return_value=row):
            out = panel._panel_sync(code)
        identity._CACHE.clear()
        panel._CACHE.clear()
        return out, calls

    out, calls = panel_for("vizinho_snci")
    assert sorted(set(calls)) == ["certificada_sigef_particular_mg", "certificada_sigef_publico_mg", "imoveiscertificados_privado_mg", "imoveiscertificados_publico_mg"], calls
    ref = out["sigef_reference"]
    assert out["sigef_reference_state"] == "found" and ref["kind"] == "SNCI_CADASTRAL", out
    assert ref["certification"] == "061308000091-60" and ref["origin"] == "Acervo Fundiário do INCRA (SNCI)", ref
    assert 0.9999 <= ref["car_overlap_ratio"] <= 1.0 and acervo.pct_floor_text(ref["car_overlap_ratio"]) in ("100%", "99,99%"), ref
    assert out["validated_name"] is None and out["panel_name_eligible"] is False, out
    for label in ("vizinho_sigef2025", "vizinho_sigef2021"):
        out, _ = panel_for(label)
        ref = out["sigef_reference"]
        assert out["sigef_reference_state"] == "found" and ref["kind"] == "SIGEF_CADASTRAL" and 0.998 <= ref["car_overlap_ratio"] < 1.0, (label, out)
        assert ref["origin"] == "Acervo Fundiário do INCRA (SIGEF)" and "aprovada em" in (ref["detail"] or ""), ref
    # Official answer without certification: nothing is shown (hidden), and no text claims absence.
    out, _ = panel_for("curvelo_teste")
    assert out["sigef_reference_state"] == "none" and out["sigef_reference"] is None, out
    assert not any(ABSENCE.search(t) for t in texts(out)), out
    # Switch off: pending (retryable), never 'none'.
    out, calls = panel_for("vizinho_snci", env={"RX_INCRA_ACERVO_ENABLED": "0"})
    assert out["sigef_reference_state"] == "unavailable" and not calls, (out["sigef_reference_state"], calls)
    # The shared card script labels the kind and keeps the C2b floor/50% rules.
    fmt = (ROOT / "portal_card_format_c2.py").read_text(encoding="utf-8")
    for needle in ("Referência INCRA (SNCI)", "Referência INCRA (SIGEF)", "const MIN=0.5", "Math.floor(Math.round(n*1e6)/100)", "data-rx-sigef-detail"):
        assert needle in fmt, needle
    assert "espelho público IBAMA/PAMGIA" not in fmt and "outra parcela SIGEF cobre" not in fmt


def all_places_contract():
    """Every place that used the mirror count for the client now goes through incra_acervo_f2."""
    forbidden = {
        "live_report_adapter.py": ("sigef_count", "parcela(s) candidata(s)", "espelho PAMGIA"),
        "report_truth_guard_v16.py": ("sigef_count", "parcela(s) candidata(s)"),
        "report_ptbr_v50.py": ("{n} parcela(s)",),
        "property_identity_runtime.py": ("SIGEF_MIRROR", "espelho público IBAMA/PAMGIA"),
        "portal_map_panel_v45.py": ("espelho público IBAMA/PAMGIA",),
        "portal_card_format_c2.py": ("espelho público IBAMA/PAMGIA",),
    }
    required = {
        "live_report_adapter.py": "incra_acervo_f2.apply_to_report_payload",
        "report_truth_guard_v16.py": "incra_acervo_f2.apply_to_report_payload",
        "property_identity_runtime.py": "incra_acervo_f2.identity_candidates",
        "deploy_app.py": "incra_acervo_f2.summary_item",
    }
    problems = []
    for name, needles in forbidden.items():
        src = (ROOT / name).read_text(encoding="utf-8")
        problems += [f"{name}: {n}" for n in needles if n in src]
    for name, needle in required.items():
        if needle not in (ROOT / name).read_text(encoding="utf-8"):
            problems.append(f"{name}: missing {needle}")
    assert not problems, problems


CONTRACTS = [switch_contract, parser_controls_contract, real_cases_contract, mirror_never_absence_contract,
             official_report_contract, partial_failure_contract, lgpd_contract, card_contract, all_places_contract]


def main(argv):
    offline()
    os.environ.setdefault("RX_RELEASE", "OFF")
    os.environ.pop("RX_INCRA_ACERVO_ENABLED", None)
    selected = [c for c in CONTRACTS if not ("--no-card" in argv and c is card_contract)]
    failures = []
    for contract in selected:
        try:
            contract()
            print(f"PASS {contract.__name__}", flush=True)
        except Exception as exc:  # collect all: the positive control must show every broken rule
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            failures.append((contract.__name__, detail))
            print(f"FAIL {contract.__name__}: {detail[:900]}", flush=True)
    if failures:
        print(f"RX_F2_INCRA_SIGEF_SNCI_GATE=FAIL {len(failures)}/{len(selected)}: " + ", ".join(n for n, _ in failures), flush=True)
        return 1
    print(f"RX_F2_INCRA_SIGEF_SNCI_GATE=PASS {len(selected)} contracts test_car={TEST_CAR} snci_car={SNCI_CAR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

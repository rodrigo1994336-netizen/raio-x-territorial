"""F2 gate: clima comparado ao normal da época, climatologia com rótulo certo e satélite do PRODES sem atributo errado.

Usage:
  PYTHONPATH=. python scripts/f2_clima_landsat_gate.py             # offline, fixtures only
  PYTHONPATH=. python scripts/f2_clima_landsat_gate.py --capture   # re-grava as respostas do catálogo Landsat (rede)

Every check runs on its own and prints PASS/FAIL, so the positive control
(running this same script over origin/main) shows WHICH defect each rule sees.
Fixtures in tests/fixtures/f2_clima_landsat/ are real responses recorded on
13/09/2026 (Curvelo/MG, CAR MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F); no
personal data. External-source checks validate the rule, never the value.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "f2_clima_landsat"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS: list = []


def check(fn):
    CHECKS.append(fn)
    return fn


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def climatology_via_module():
    """Parse the recorded POWER climatology through climate_nasa itself (network stubbed)."""
    import climate_nasa

    raw = load("power_climatology_curvelo.json")
    original = climate_nasa._curl_json
    climate_nasa._curl_json = lambda url, max_time=60: {"ok": True, "json": raw, "bytes": 0}
    try:
        return climate_nasa.query_climatology_nasa({"type": "Point", "coordinates": [-44.181978, -18.891297]})
    finally:
        climate_nasa._curl_json = original


def series():
    return load("power_daily_prectotcorr_curvelo_1991_2025.json")["properties"]["parameter"]["PRECTOTCORR"]


def recent_from_series(values: dict, start: date, days: int) -> dict:
    daily = []
    for k in range(days):
        d = start + timedelta(k)
        daily.append({"date": d.strftime("%Y%m%d"), "rain_mm": values[d.strftime("%Y%m%d")]})
    rain = [x["rain_mm"] for x in daily]
    return {
        "ok": True, "requested_days": days, "available_days": days,
        "period_start": daily[0]["date"], "period_end": daily[-1]["date"],
        "rain_sum_mm": round(sum(rain), 3), "dry_days_lt_1mm": sum(1 for v in rain if v < 1.0), "daily": daily,
    }


def alerted(drought: dict) -> bool:
    text = str(drought.get("state") or "")
    return bool(drought.get("alert")) or bool(re.search(r"aten|abaixo|seca", text, re.I))


# --------------------------------------------------------------------------- clima

@check
def clima_curvelo_nao_da_alerta_com_chuva_acima_do_normal():
    import climate_nasa

    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    assert recent["rain_sum_mm"] == 31.46 and recent["available_days"] == 30, "fixture changed"
    drought = climate_nasa.build_drought_screening(recent, climatology_via_module())
    assert not alerted(drought), f"choveu 1,9x o normal e a regra alerta: {drought.get('state')!r}"
    assert drought.get("state") == "acima do normal", drought
    assert drought.get("status") == "found" and drought.get("method") == "ratio_climatology", drought
    assert 16.0 <= drought["normal_mm"] <= 17.0, drought["normal_mm"]  # 19 d x 0,25 + 11 d x 1,07 mm/dia
    assert "31,5 mm" in drought["summary"] and "31.46" not in drought["summary"], drought["summary"]


@check
def clima_mesma_janela_em_35_anos_nao_vira_alerta_da_estacao():
    import climate_nasa

    values, clim = series(), climatology_via_module()
    hits, answered = [], 0
    for year in range(1991, 2026):
        recent = recent_from_series(values, date(year, 8, 13), 30)
        drought = climate_nasa.build_drought_screening(recent, clim)
        answered += bool(drought.get("state"))
        if alerted(drought):
            hits.append((year, drought.get("state")))
    assert answered == 35, f"only {answered} of 35 years got a state: an empty result proves nothing"
    assert not hits, f"13/08-11/09 é estação seca: a regra alertou em {len(hits)} de 35 anos: {hits[:5]}"


@check
def clima_seca_real_de_janeiro_2014_e_detectada():
    import climate_nasa

    values, clim = series(), climatology_via_module()
    recent = recent_from_series(values, date(2014, 1, 1), 30)
    assert recent["rain_sum_mm"] < 50, recent["rain_sum_mm"]  # janeiro mais seco da série (mediana ~205 mm)
    drought = climate_nasa.build_drought_screening(recent, clim)
    assert alerted(drought) and drought.get("state") == "abaixo do normal", drought


@check
def clima_seca_sintetica_com_chuva_espalhada_e_detectada():
    import climate_nasa

    clim = {"ok": True, "months": [{"month": "JAN", "rain_mm_day": 6.0, "rain_mm": 6.0}]}
    daily = [{"date": (date(2026, 1, 1) + timedelta(k)).strftime("%Y%m%d"), "rain_mm": 4.0 if k % 2 == 0 else 0.0} for k in range(30)]
    recent = {"ok": True, "available_days": 30, "period_start": "20260101", "period_end": "20260130",
              "rain_sum_mm": 60.0, "dry_days_lt_1mm": 15, "daily": daily}
    drought = climate_nasa.build_drought_screening(recent, clim)
    assert alerted(drought), f"60 mm quando o normal é 180 mm tem de alertar: {drought.get('state')!r}"
    wet = {**recent, "daily": [{**x, "rain_mm": 7.0} for x in daily], "rain_sum_mm": 210.0, "dry_days_lt_1mm": 0}
    assert not alerted(climate_nasa.build_drought_screening(wet, clim))


@check
def clima_sem_normal_ou_sem_chuva_recente_nao_inventa_estado():
    import climate_nasa

    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    for clim in ({"ok": False, "detail": "timeout"}, {}, {"ok": True, "months": []}):
        drought = climate_nasa.build_drought_screening(recent, clim)
        assert drought.get("status") == "pending" and not drought.get("state") and not alerted(drought), (clim, drought)
    failed = climate_nasa.build_drought_screening({"ok": False, "detail": "curl"}, climatology_via_module())
    assert failed.get("status") == "pending" and not failed.get("state"), failed
    zero = {**recent, "rain_sum_mm": 0.0, "daily": [{**x, "rain_mm": 0.0} for x in recent["daily"]]}
    z = climate_nasa.build_drought_screening(zero, climatology_via_module())
    assert z.get("status") == "found" and z.get("rain_sum_mm") == 0.0, "zero é medição, não ausência"


@check
def clima_janela_curta_nao_recebe_estado():
    import climate_nasa

    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    week = {**recent, "available_days": 7, "daily": recent["daily"][-7:], "period_start": recent["daily"][-7]["date"]}
    drought = climate_nasa.build_drought_screening(week, climatology_via_module())
    assert drought.get("status") == "not_found" and not drought.get("state"), drought


@check
def clima_percentil_na_serie_longa_e_limites_da_razao_conferidos():
    from climate_normal_f2 import build_rain_vs_normal, history_from_power_payload

    raw = load("power_daily_prectotcorr_curvelo_1991_2025.json")
    history = history_from_power_payload(raw, 1991, 2025)
    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    item = build_rain_vs_normal(recent, climatology_via_module(), history)
    assert item["method"] == "percentile_series" and item["reference"] == "1991–2025" and item["reference_years"] == 35, item
    assert item["state_code"] == "above_normal" and 85 <= item["percentile"] <= 92, item  # 31 dos 35 anos choveram menos

    values, clim = series(), climatology_via_module()
    bad, seen = [], {"below_normal": 0, "above_normal": 0, "normal": 0, "dry_season": 0}
    for n in (30, 90):
        for offset in range(0, 365, 5):
            d0 = date(2001, 1, 1) + timedelta(offset)
            sums = {}
            for year in range(1991, 2026):
                start = date(year, d0.month, d0.day)
                if start + timedelta(n - 1) > date(2025, 12, 31):
                    continue
                sums[year] = sum(values[(start + timedelta(k)).strftime("%Y%m%d")] for k in range(n))
            for year, observed in sums.items():
                ref = [v for y, v in sums.items() if y != year]
                pct = (sum(1 for r in ref if r < observed) + 0.5 * sum(1 for r in ref if r == observed)) / len(ref) * 100
                res = build_rain_vs_normal(recent_from_series(values, date(year, d0.month, d0.day), n), clim)
                seen[res["state_code"]] += 1
                if res["state_code"] == "below_normal" and pct > 30:
                    bad.append(("abaixo", n, d0.strftime("%d/%m"), year, round(pct)))
                if res["state_code"] == "above_normal" and pct < 70:
                    bad.append(("acima", n, d0.strftime("%d/%m"), year, round(pct)))
    assert min(seen.values()) >= 50, f"calibration sample too thin to mean anything: {seen}"
    assert not bad, f"razão contra a climatologia discorda da série real: {bad[:6]} ({len(bad)})"


@check
def climatologia_no_pdf_rotula_recorde_e_mm_por_dia():
    import live_report_adapter_v13 as v13

    derived = load("power_t2m_daily_2001_2020_curvelo_monthly_check.json")["months"]
    raw = load("power_climatology_curvelo.json")["properties"]["parameter"]
    for month, stats in derived.items():  # the label is only true if the value IS the record
        assert raw["T2M_MAX"][month] == stats["abs_max"] and raw["T2M_MIN"][month] == stats["abs_min"], month

    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    payload = v13._patch_climate_full({}, {"climate_nasa": recent}, climatology_via_module())
    rows = payload["water"]["rain_rows"]
    clim_rows = [r for r in rows if str(r[0]).startswith("Climatologia")]
    assert len(clim_rows) == 12, clim_rows
    for label, text in clim_rows:
        text = str(text)
        if re.search(r"máx|mín", text):
            assert "recorde" in text, f"{label}: máx/mín da climatologia é recorde e saiu sem rótulo: {text!r}"
        assert "mm/dia" in text and "mm no mês" in text, text
        assert "—" not in text and not re.search(r"\d\.\d", text), text
    sep = dict((str(a), str(b)) for a, b in clim_rows)["Climatologia SEP"]
    assert "máx 40,2 °C" in sep and "recorde 2001–2020" in sep and "≈ 32 mm no mês" in sep, sep
    labels = [str(r[0]) for r in rows]
    assert not any("seca" in x.lower() for x in labels), labels
    assert ["Chuva recente comparada ao normal"] == [x for x in labels if "normal" in x.lower()], labels
    # what reaches the PDF goes through the R1 normalizer; the R1 lint must stay clean on it
    sys.path.insert(0, str(ROOT / "scripts"))
    from r1_report_ptbr_gate import lint_text
    from report_ptbr_v50 import normalize_text

    rendered = normalize_text("\n".join(f"{a}: {b}" for a, b in rows if str(a).startswith(("Climatologia", "Chuva recente"))))
    assert "recorde 2001–2020: máx 40,2 °C" in rendered, rendered
    hits = lint_text(rendered)
    assert not hits, hits


@check
def climatologia_no_portal_rotula_mm_por_dia_e_recorde():
    src = (ROOT / "portal_property_tabs.py").read_text(encoding="utf-8")
    body = src[src.index("async function clima("):src.index("async function agua(")]
    helpers = src[src.index("function rainChart(") - 3000:src.index("function rainChart(")]
    assert not re.search(r"x\.rain_mm\)\}\s*mm(?!/)", body), "climatologia (mm/dia) impressa como 'mm'"
    assert not re.search(r"máx \$\{fmt\(x\.t_max_c\)\}", body), "recorde da climatologia impresso como máxima comum"
    assert "dry_day_share_pct" not in body, "dias secos não podem voltar a ser o gatilho da triagem"
    assert "climLine(x,cl.period)" in body and "normalPane(dr)" in body, "clima tab not wired to the F2 helpers"
    assert "mm/dia" in helpers and "recorde " in helpers and "rain_mm_month" in helpers, "helpers missing"
    v35 = (ROOT / "portal_premium_ux_v35.py").read_text(encoding="utf-8")
    import ast

    anchors = {}
    for node in ast.walk(ast.parse(v35)):
        if isinstance(node, ast.FunctionDef) and node.name == "_patch_climate":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
                    anchors[sub.targets[0].id] = sub.value.value
    for name in ("old", "old_end", "old_ranges"):
        assert src.count(anchors[name]) == 1, f"âncora {name} do portal_premium_ux_v35 deixou de casar"


# --------------------------------------------------------------------------- PRODES / Landsat

def prodes_result(lookups=None):
    props = load("prodes_cerrado_curvelo_feature_properties.json")["features"]
    result = {
        "car": {"properties": {"cod_imovel": "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F", "municipio": "Curvelo", "uf": "MG", "area": 14.795}},
        "prodes": {"ok": True, "exact": {"occurrence_count": len(props), "area_unique_ha": 1.0,
                                          "occurrences": [{"area_intersection_ha": 0.5, "properties": p} for p in props]}},
    }
    if lookups is not None:
        result["prodes_image_lookups"] = lookups
    return result


def stac_post_from_fixture():
    recorded = load("planetary_computer_landsat_218073_by_date.json")["responses"]

    def post(url, body):
        day = body["datetime"][:10]
        return recorded.get(day)
    return post


@check
def prodes_pdf_nao_imprime_satelite_do_wfs():
    import live_report_adapter as a

    text = json.dumps(a.build_live_payload(prodes_result(), "RX-F2", "2026-09-13T00:00:00+00:00", "map.png"), ensure_ascii=False)
    assert "Landsat8" not in text and "/OLI" not in text, "atributo satellite/sensor do WFS (Landsat8/OLI em 2004) chegou ao relatório"
    assert "02/07/2002" in text and "31/07/2004" in text, "a data da imagem (certa na fonte) continua no relatório, em pt-BR"


@check
def prodes_pdf_mostra_satelite_so_com_prova_do_catalogo():
    import live_report_adapter as a
    from prodes_image_platform_f2 import query_platforms_for_occurrences

    rows = a._extract_prodes_occurrences(prodes_result())
    lookups = query_platforms_for_occurrences(rows, post=stac_post_from_fixture())
    text = json.dumps(a.build_live_payload(prodes_result(lookups), "RX-F2", "2026-09-13T00:00:00+00:00", "map.png"), ensure_ascii=False)
    assert "Landsat8" not in text, text
    for expected in ("02/07/2002 · Landsat 7 (ETM+)", "31/07/2004 · Landsat 5 (TM)"):
        assert expected in text, f"{expected!r} missing"


@check
def api_resumo_nao_expoe_satelite_do_wfs():
    src = (ROOT / "deploy_app.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if "exact_occurrences" in l)
    assert "'satellite'" not in line and "'sensor'" not in line, "API summary still copies the WFS satellite/sensor attribute"


@check
def landsat_catalogo_regra_de_prova():
    import prodes_image_platform_f2 as lp

    expected = {
        "2002-07-02": "Landsat 7", "2004-07-31": "Landsat 5", "2006-07-21": "Landsat 5",
        "2010-08-01": "Landsat 5", "2014-08-12": "Landsat 8", "2020-09-13": "Landsat 8", "2021-09-16": "Landsat 8",
    }
    post = stac_post_from_fixture()
    for day, platform in expected.items():
        res = lp.query_landsat_platform(day, "218/73", None, post)
        assert res["status"] == "found" and res["platform"] == platform, (day, res)
    body = lp.search_body(date(2004, 7, 31), ("218", "073"))
    assert body["query"]["landsat:wrs_path"]["eq"] == "218" and body["query"]["landsat:wrs_row"]["eq"] == "073"
    assert lp.wrs_path_row("218/73") == ("218", "073")
    d = date(2004, 7, 31)
    assert lp.platform_from_stac({"features": []}, d)["status"] == "not_found"
    two = {"features": [{"id": "a", "properties": {"platform": "landsat-5", "instruments": ["tm"], "datetime": "2004-07-31T12:00:00Z"}},
                        {"id": "b", "properties": {"platform": "landsat-7", "instruments": ["etm+"], "datetime": "2004-07-31T12:10:00Z"}}]}
    assert lp.platform_from_stac(two, d)["status"] == "not_found", "ambiguous date must not pick a satellite"
    other_day = {"features": [{"id": "c", "properties": {"platform": "landsat-8", "instruments": ["oli"], "datetime": "2004-08-01T12:00:00Z"}}]}
    assert lp.platform_from_stac(other_day, d)["status"] == "not_found"
    assert lp.query_landsat_platform("2004-07-31", "218/73", None, lambda u, b: None)["status"] == "pending"

    def boom(u, b):
        raise TimeoutError("x")
    assert lp.query_landsat_platform("2004-07-31", "218/73", None, boom)["status"] == "pending"
    pending = lp.prodes_image_payload([{"year": 2004, "image_date": "2004-07-31", "path_row": "218/73"}],
                                      {"2004-07-31|218/073": {"status": "pending"}})[0]
    assert pending["text"] == "31/07/2004" and pending["platform"] is None and pending["platform_status"] == "pending", pending
    assert lp.image_row({"image_date": None}) is None, "campo vazio não aparece"


# --------------------------------------------------------------------------- capture (network, manual)

def capture() -> int:
    import prodes_image_platform_f2 as lp

    props = load("prodes_cerrado_curvelo_feature_properties.json")["features"]
    responses = {}
    for p in props:
        day = lp.image_day(p["image_date"])
        responses[day.isoformat()] = lp._curl_post_json(lp.STAC_SEARCH, lp.search_body(day, lp.wrs_path_row(p["path_row"])))
    out = {"source": lp.STAC_SEARCH, "recorded": date.today().isoformat(), "request": "search_body(date, path/row) do módulo",
           "responses": dict(sorted(responses.items()))}
    (FIX / "planetary_computer_landsat_218073_by_date.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("captured", len(responses))
    return 0


def main() -> int:
    if "--capture" in sys.argv:
        return capture()
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # each rule reports on its own
            failed += 1
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip().splitlines()[-1]
            print(f"FAIL {fn.__name__}: {detail[:400]}")
    print(f"RX_F2_CLIMA_LANDSAT_GATE={'PASS' if not failed else 'FAIL'} ({len(CHECKS) - failed}/{len(CHECKS)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

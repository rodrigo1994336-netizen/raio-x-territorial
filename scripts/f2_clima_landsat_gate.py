"""F2 gate: chuva comparada ao normal (ou à média) da época, climatologia com rótulo certo e satélite do PRODES só com prova.

Usage:
  PYTHONPATH=. RX_RELEASE=OFF python scripts/f2_clima_landsat_gate.py   # offline: regras + controles positivos
  PYTHONPATH=. python scripts/f2_clima_landsat_gate.py --capture        # regrava as respostas do catálogo Landsat (rede)

Every rule runs on its own and prints PASS/FAIL. After the rules, every
POSITIVE CONTROL reintroduces one known defect into a copy of the real module
(literal source replacement, the defect the review found) and re-runs the rules
that must catch it: the control passes only if those rules FAIL with the
expected reason. A control whose snippet no longer matches the source fails
too, so no control goes silently inert. The portal text is checked by running
the portal's own JS (after the portal_premium_ux_v35 patch) in Node.js, which
ubuntu-latest has; without node that rule fails, it is never skipped.

Fixtures in tests/fixtures/f2_clima_landsat/ are real responses recorded on
13/09/2026 (Curvelo/MG, CAR MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F); no
personal data. External-source checks validate the rule, never the value.
"""
from __future__ import annotations

import ast
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
import importlib
import inspect
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import types

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "f2_clima_landsat"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS: list = []
SRC_OVERRIDE: dict[str, str] = {}
CURVELO_LONLAT = (-44.181978, -18.891297)
SERIES_START = date(1991, 1, 1)
_CACHE: dict = {}


def check(fn):
    CHECKS.append(fn)
    return fn


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def read_src(rel: str) -> str:
    """Source text as the rules see it (a positive control may substitute a mutated copy)."""
    return SRC_OVERRIDE.get(rel) or (ROOT / rel).read_bytes().decode("utf-8")


def write_fixture(path: Path, obj) -> None:
    """Fixtures are UTF-8 with LF on every OS (text mode on Windows would write CRLF)."""
    path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def half_up(value: float) -> int:
    """The gate's own rounding (independent of the module under test)."""
    return int(Decimal(repr(float(value))).quantize(Decimal("1"), ROUND_HALF_UP))


def ptbr_number(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def climatology_via_module():
    """Parse the recorded POWER climatology through climate_nasa itself (network stubbed)."""
    import climate_nasa

    raw = load("power_climatology_curvelo.json")
    original = climate_nasa._curl_json
    climate_nasa._curl_json = lambda url, max_time=60: {"ok": True, "json": raw, "bytes": 0}
    try:
        return climate_nasa.query_climatology_nasa({"type": "Point", "coordinates": list(CURVELO_LONLAT)})
    finally:
        climate_nasa._curl_json = original


def series_rows() -> list[dict]:
    """Daily PRECTOTCORR 1991–2025 as the API's daily rows, built once."""
    if "rows" not in _CACHE:
        values = load("power_daily_prectotcorr_curvelo_1991_2025.json")["properties"]["parameter"]["PRECTOTCORR"]
        rows, d = [], SERIES_START
        while d <= date(2025, 12, 31):
            key = d.strftime("%Y%m%d")
            rows.append({"date": key, "rain_mm": values[key]})
            d += timedelta(1)
        _CACHE["rows"] = rows
    return _CACHE["rows"]


def recent_from_series(start: date, days: int) -> dict:
    i = (start - SERIES_START).days
    daily = series_rows()[i:i + days]
    assert len(daily) == days, (start, days)
    rain = [x["rain_mm"] for x in daily]
    return {
        "ok": True, "requested_days": days, "available_days": days,
        "period_start": daily[0]["date"], "period_end": daily[-1]["date"],
        "rain_sum_mm": round(sum(rain), 3), "dry_days_lt_1mm": sum(1 for v in rain if v < 1.0), "daily": daily,
    }


def synthetic_recent(total_mm: float, days: int = 30, start: date = date(2026, 1, 1)) -> dict:
    daily = [{"date": (start + timedelta(k)).strftime("%Y%m%d"), "rain_mm": total_mm if k == 0 else 0.0} for k in range(days)]
    return {"ok": True, "available_days": days, "period_start": daily[0]["date"], "period_end": daily[-1]["date"],
            "rain_sum_mm": total_mm, "daily": daily}


def calibration_cases() -> list[tuple]:
    """(days, dd/mm, year, leave-one-out percentile, start) for 30/60/90/120-day windows every 5 days, 1991–2025."""
    if "cases" not in _CACHE:
        rows, cases = series_rows(), []
        for n in (30, 60, 90, 120):
            for offset in range(0, 365, 5):
                d0 = date(2001, 1, 1) + timedelta(offset)
                sums = {}
                for year in range(1991, 2026):
                    i = (date(year, d0.month, d0.day) - SERIES_START).days
                    if i + n <= len(rows):
                        sums[year] = sum(r["rain_mm"] for r in rows[i:i + n])
                for year, observed in sums.items():
                    ref = [v for y, v in sums.items() if y != year]
                    pct = (sum(1 for r in ref if r < observed) + 0.5 * sum(1 for r in ref if r == observed)) / len(ref) * 100
                    cases.append((n, d0.strftime("%d/%m"), year, pct, date(year, d0.month, d0.day)))
        _CACHE["cases"] = cases
    return _CACHE["cases"]


def alerted(drought: dict) -> bool:
    text = str(drought.get("state") or "")
    return bool(drought.get("alert")) or bool(re.search(r"aten|abaixo do normal|seca", text, re.I))


def ratio_text_problem(res: dict) -> str | None:
    """Ratio method: the state must follow from the numbers the client reads in the same sentence."""
    s = str(res.get("summary") or "")
    m_rain = re.match(r"(\d[\d.]*,\d) mm em (\d+) dias", s)
    m_mean = re.search(r"A média desses dias na climatologia NASA POWER[^é]*é (\d[\d.]*,\d) mm", s)
    m_pct = re.search(r"choveu (\d+)% dessa média", s)
    if res.get("method") != "ratio_climatology" or not (m_rain and m_mean):
        return f"frase da razão sem chuva/média legíveis: {res.get('method')} {s!r}"
    rain, mean, days = ptbr_number(m_rain.group(1)), ptbr_number(m_mean.group(1)), int(m_rain.group(2))
    code = res.get("state_code")
    if "dentro do normal" in f"{res.get('state')} {s}":
        return f"razão diz 'dentro do normal': {s!r}"
    if mean <= 0:
        return None if not m_pct else f"percentual sobre média zero: {s!r}"
    if not m_pct:
        return f"sem percentual impresso: {s!r}"
    pct = int(m_pct.group(1))
    if pct != res.get("ratio_pct") or pct != half_up(rain / mean * 100):
        return f"percentual impresso {pct}% não é {rain}/{mean} nem ratio_pct={res.get('ratio_pct')}"
    if pct < 40:
        expected = ("below_normal",) if mean / days >= 1.5 - 0.05 / days else ("dry_season",)
        if mean / days >= 1.5 - 0.05 / days and mean / days < 1.5 + 0.05 / days:
            expected = ("below_normal", "dry_season")  # the 1,5 mm/dia cut sits inside the printed rounding
    elif pct > 150 and rain - mean >= 10.0:
        expected = ("above_normal",)
    elif pct < 90:
        expected = ("below_average",)
    elif pct <= 110:
        expected = ("near_average",)
    else:
        expected = ("above_average",)
    return None if code in expected else f"impresso {pct}% (chuva {rain} mm, média {mean} mm) e estado {code!r}"


def percentile_text_problem(res: dict) -> str | None:
    """Series method: the printed percentile and median decide the state."""
    s = str(res.get("summary") or "")
    m_rain = re.match(r"(\d[\d.]*,\d) mm em (\d+) dias", s)
    m_med = re.search(r"a chuva mediana é (\d[\d.]*,\d) mm; choveu (mais|menos) que em (\d+)% dos anos", s)
    if res.get("method") != "percentile_series" or not (m_rain and m_med):
        return f"frase do percentil sem números legíveis: {res.get('method')} {s!r}"
    rain, median = ptbr_number(m_rain.group(1)), ptbr_number(m_med.group(1))
    pct = int(m_med.group(3)) if m_med.group(2) == "mais" else 100 - int(m_med.group(3))
    if pct != res.get("percentile"):
        return f"percentil impresso {pct} diferente do percentil do estado {res.get('percentile')!r}"
    if pct <= 20:
        expected = ("below_normal", "dry_season")
    elif pct >= 80 and rain - median >= 10.0:
        expected = ("above_normal",)
    else:
        expected = ("normal",)
    return None if res.get("state_code") in expected else f"impresso percentil {pct} (chuva {rain}, mediana {median}) e estado {res.get('state_code')!r}"


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
    assert ratio_text_problem(drought) is None, ratio_text_problem(drought)


@check
def clima_mesma_janela_em_35_anos_nao_vira_alerta_da_estacao():
    import climate_nasa

    clim = climatology_via_module()
    hits, answered = [], 0
    for year in range(1991, 2026):
        drought = climate_nasa.build_drought_screening(recent_from_series(date(year, 8, 13), 30), clim)
        answered += bool(drought.get("state"))
        if alerted(drought):
            hits.append((year, drought.get("state")))
    assert answered == 35, f"only {answered} of 35 years got a state: an empty result proves nothing"
    assert not hits, f"13/08-11/09 é estação seca: a regra alertou em {len(hits)} de 35 anos: {hits[:5]}"


@check
def clima_seca_real_de_janeiro_2014_e_detectada():
    import climate_nasa

    recent = recent_from_series(date(2014, 1, 1), 30)
    assert recent["rain_sum_mm"] < 50, recent["rain_sum_mm"]  # janeiro mais seco da série (mediana ~205 mm)
    drought = climate_nasa.build_drought_screening(recent, climatology_via_module())
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
def clima_percentil_na_serie_longa_e_texto_coerente():
    from climate_normal_f2 import build_rain_vs_normal, history_from_power_payload

    history = history_from_power_payload(load("power_daily_prectotcorr_curvelo_1991_2025.json"), 1991, 2025)
    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    item = build_rain_vs_normal(recent, climatology_via_module(), history)
    assert item["method"] == "percentile_series" and item["reference"] == "1991–2025" and item["reference_years"] == 35, item
    assert item["state_code"] == "above_normal" and 85 <= item["percentile"] <= 92, item  # 31 dos 35 anos choveram menos
    assert percentile_text_problem(item) is None, percentile_text_problem(item)


@check
def clima_razao_contra_a_media_confere_com_a_serie_real():
    """30/60/90/120 dias a cada 5 dias × 35 anos: a razão nunca chama de normal o que a série diz que não é."""
    from climate_normal_f2 import build_rain_vs_normal

    clim = climatology_via_module()
    seen, bad, dry_decile, wet_decile = Counter(), [], 0, 0
    for n, d0, year, pct, start in calibration_cases():
        res = build_rain_vs_normal(recent_from_series(start, n), clim)
        code = res.get("state_code")
        seen[code] += 1
        where = (n, d0, year, round(pct), res.get("ratio_pct"), res.get("state"))
        if res.get("status") != "found" or res.get("method") != "ratio_climatology":
            bad.append(("janela sem estado pela razão", where))
            continue
        if code == "normal" or "dentro do normal" in f"{res.get('state')} {res.get('summary')}":
            bad.append(("razão diz 'dentro do normal'", where))
        if code == "below_normal" and pct > 30:
            bad.append(("abaixo do normal fora dos 30% mais secos", where))
        if code == "above_normal" and pct < 70:
            bad.append(("acima do normal fora dos 30% mais chuvosos", where))
        if pct <= 10:
            dry_decile += 1
            if code not in ("below_normal", "below_average", "dry_season"):
                bad.append(("entre os 10% mais secos e rotulado sem 'abaixo'", where))
        if pct >= 90:
            wet_decile += 1
            if code not in ("above_normal", "above_average"):
                bad.append(("entre os 10% mais chuvosos e rotulado sem 'acima'", where))
        problem = ratio_text_problem(res)
        if problem:
            bad.append(("texto e estado discordam", where, problem))
    ratio_states = ("below_normal", "dry_season", "below_average", "near_average", "above_average", "above_normal")
    assert dry_decile >= 500 and wet_decile >= 500, f"amostra dos decis extremos fina demais: {dry_decile}, {wet_decile}"
    kinds = Counter(b[0] for b in bad)
    assert not bad, f"razão contra a média discorda da série real: {dict(kinds)}; exemplos {bad[:3]}"
    assert min(seen[s] for s in ratio_states) >= 50, f"calibration sample too thin to mean anything: {dict(seen)}"


@check
def clima_texto_e_estado_usam_o_mesmo_numero():
    from climate_normal_f2 import build_rain_vs_normal

    jan = {"ok": True, "period": "2001–2020", "months": [{"month": "JAN", "rain_mm_day": 6.0}]}
    a, b = build_rain_vs_normal(synthetic_recent(71.9), jan), build_rain_vs_normal(synthetic_recent(72.8), jan)
    assert a["ratio_pct"] == b["ratio_pct"] == 40 and a["state_code"] == b["state_code"], (
        f"mesmo percentual impresso, estados diferentes: {a['state']!r} {a['summary']!r} / {b['state']!r} {b['summary']!r}")
    problems = []
    dry = {"ok": True, "period": "2001–2020", "months": [{"month": "JAN", "rain_mm_day": 0.2}]}
    for clim, top in ((jan, 3200), (dry, 400)):
        for tenth in range(0, top + 1):
            p = ratio_text_problem(build_rain_vs_normal(synthetic_recent(tenth / 10), clim))
            if p:
                problems.append(p)
    # percentile: 20 to 40 reference years make every rounding boundary reachable (7 of 34 years = 20,6 %)
    for years in (20, 21, 34, 35, 40):
        totals = [100.0 + 7.3 * k for k in range(years)]
        hist_series = {}
        for k, total in enumerate(totals):
            for dd in range(30):
                hist_series[date(2025 - k, 1, 1 + dd).strftime("%Y%m%d")] = total if dd == 0 else 0.0
        history = {"ok": True, "first_year": 2026 - years, "last_year": 2025, "series": hist_series}
        ordered = sorted(totals)
        probes = ordered + [(x + y) / 2 for x, y in zip(ordered, ordered[1:])] + [ordered[0] - 5, ordered[-1] + 20]
        for total in probes:
            res = build_rain_vs_normal(synthetic_recent(round(total, 2)), jan, history)
            p = percentile_text_problem(res)
            if p:
                problems.append(p)
    assert not problems, f"{len(problems)} frases contradizem o estado: {problems[:4]}"


@check
def climatologia_no_pdf_rotula_recorde_e_mm_por_dia():
    import live_report_adapter_v13 as v13
    from climate_normal_f2 import history_from_power_payload

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
    compare = [x for x in labels if "normal" in x.lower() or "média" in x.lower()]
    assert compare == ["Chuva recente comparada à média da época"], f"sem série longa a comparação é contra a média: {labels}"
    ratio_text = dict((str(a), str(b)) for a, b in rows)[compare[0]]
    assert "dentro do normal" not in ratio_text and "média" in ratio_text, ratio_text

    history = history_from_power_payload(load("power_daily_prectotcorr_curvelo_1991_2025.json"), 1991, 2025)
    with_series = v13._patch_climate_full({}, {"climate_nasa": recent, "climate_rain_history": history}, climatology_via_module())
    rows2 = with_series["water"]["rain_rows"]
    compare2 = [str(r[0]) for r in rows2 if "normal" in str(r[0]).lower() or "média" in str(r[0]).lower()]
    assert compare2 == ["Chuva recente comparada ao normal"], compare2
    assert with_series["water"]["drought_screening"]["method"] == "percentile_series"
    # what reaches the PDF goes through the R1 normalizer; the R1 lint must stay clean on it
    sys.path.insert(0, str(ROOT / "scripts"))
    from r1_report_ptbr_gate import lint_text
    from report_ptbr_v50 import normalize_text

    for rs in (rows, rows2):
        rendered = normalize_text("\n".join(f"{a}: {b}" for a, b in rs if str(a).startswith(("Climatologia", "Chuva recente"))))
        assert "recorde 2001–2020: máx 40,2 °C" in rendered, rendered
        hits = lint_text(rendered)
        assert not hits, hits


@check
def climatologia_no_portal_rotula_mm_por_dia_e_recorde():
    src = read_src("portal_property_tabs.py")
    body = src[src.index("async function clima("):src.index("async function agua(")]
    helpers = src[src.index("function rainChart(") - 3500:src.index("function rainChart(")]
    assert not re.search(r"x\.rain_mm\)\}\s*mm(?!/)", body), "climatologia (mm/dia) impressa como 'mm'"
    assert not re.search(r"máx \$\{fmt\(x\.t_max_c\)\}", body), "recorde da climatologia impresso como máxima comum"
    assert "dry_day_share_pct" not in body, "dias secos não podem voltar a ser o gatilho da triagem"
    assert "climLine(x,cl.period)" in body and "normalPane(dr)" in body, "clima tab not wired to the F2 helpers"
    assert "mm/dia" in helpers and "recorde " in helpers and "rain_mm_month" in helpers, "helpers missing"
    anchors = {}
    for node in ast.walk(ast.parse(read_src("portal_premium_ux_v35.py"))):
        if isinstance(node, ast.FunctionDef) and node.name == "_patch_climate":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
                    anchors[sub.targets[0].id] = sub.value.value
    for name in ("old", "old_end", "old_ranges"):
        assert src.count(anchors[name]) == 1, f"âncora {name} do portal_premium_ux_v35 deixou de casar"


def portal_normal_pane_texts(cases: dict) -> dict:
    """Run the portal's own normalPane (HTML after the v35 patch) in Node and return the visible text."""
    node = shutil.which("node")
    assert node, "Node.js não encontrado: esta regra roda o JS do próprio portal (ubuntu-latest tem node)"
    src = read_src("portal_property_tabs.py")
    ui = src[src.index("UI=r'''") + len("UI=r'''"):]
    ui = ui[:ui.index("'''")]
    fn = next(n for n in ast.parse(read_src("portal_premium_ux_v35.py")).body
              if isinstance(n, ast.FunctionDef) and n.name == "_patch_climate")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "portal_premium_ux_v35.py", "exec"), ns)
    patched = ns["_patch_climate"](ui)
    assert 'data-days="7"' in patched and "1 ano</button>" in patched and "__rxClimateCacheV35" in patched, "v35 deixou de remendar a aba Clima"
    picked = []
    for prefix in ("const q=s=>", "function fmt(", "function kpis(", "function dt(", "const NORMAL_MSG=", "function normalPane("):
        found = [line for line in patched.splitlines() if line.startswith(prefix)]
        assert len(found) == 1, f"{prefix!r} casou {len(found)}x no HTML remendado pelo v35"
        picked.append(found[0])
    js = "\n".join(picked) + (
        "\nconst cases=JSON.parse(require('fs').readFileSync(0,'utf8'));const out={};"
        "for(const [k,v] of Object.entries(cases)){out[k]=normalPane(v).replace(/<[^>]+>/g,' ').replace(/\\s+/g,' ').trim()}"
        "process.stdout.write(JSON.stringify(out));")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "normal_pane.js"
        script.write_bytes(js.encode("utf-8"))
        proc = subprocess.run([node, str(script)], input=json.dumps(cases).encode("utf-8"), capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "ignore")[-400:]
    return json.loads(proc.stdout.decode("utf-8"))


@check
def portal_aba_clima_texto_honesto_depois_do_v35():
    import climate_nasa
    from climate_normal_f2 import build_rain_vs_normal, history_from_power_payload

    clim = climatology_via_module()
    recent = load("production_climate_detail_curvelo_30d.json")["recent"]
    week = {**recent, "available_days": 7, "daily": recent["daily"][-7:], "period_start": recent["daily"][-7]["date"]}
    history = history_from_power_payload(load("power_daily_prectotcorr_curvelo_1991_2025.json"), 1991, 2025)
    jan = {"ok": True, "period": "2001–2020", "months": [{"month": "JAN", "rain_mm_day": 6.0}]}
    cases = {  # the served buttons are 7 dias / 30 dias / 1 ano (portal_premium_ux_v35)
        "7 dias": climate_nasa.build_drought_screening(week, clim),
        "30 dias": climate_nasa.build_drought_screening(recent, clim),
        "30 dias com série": climate_nasa.build_drought_screening(recent, clim, history),
        "1 ano": climate_nasa.build_drought_screening(recent_from_series(date(2024, 9, 12), 365), clim),
        "pendente": climate_nasa.build_drought_screening(recent, {"ok": False, "detail": "timeout"}),
        "abaixo da média": build_rain_vs_normal(synthetic_recent(100.0), jan),
    }
    assert cases["7 dias"]["reason"] == "janela_curta" and cases["1 ano"]["reason"] == "janela_longa_sem_serie_historica", cases
    texts = portal_normal_pane_texts(cases)
    assert "30 dias ou mais" in texts["7 dias"] and "pendente" not in texts["7 dias"].lower(), texts["7 dias"]
    assert "até 120 dias" in texts["1 ano"] and "pendente" not in texts["1 ano"].lower(), f"limite do método mostrado como pendente: {texts['1 ano']!r}"
    assert "pendente" in texts["pendente"].lower(), texts["pendente"]
    for key in ("30 dias", "abaixo da média"):
        t = texts[key]
        assert "Média da época" in t and "Normal da época" not in t and "dentro do normal" not in t.lower() and "% da média" in t, t
    assert texts["30 dias"].startswith("Triagem Acima do normal"), texts["30 dias"]
    assert texts["abaixo da média"].startswith("Triagem Abaixo da média da época"), texts["abaixo da média"]
    assert "Normal da época" in texts["30 dias com série"] and "dos anos" in texts["30 dias com série"], texts["30 dias com série"]


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


def reading_format_result(shift_days: int = 0):
    """result['prodes'] as f2/prodes_leitura (prodes_reading_f2.apply_reading_to_result) leaves it: 'exact' keeps
    only {id, area_intersection_ha, properties{year, image_date, layer}} and the WFS calculation moves to 'exact_raw'."""
    result = prodes_result()
    raw = [{"id": f"yearly_deforestation.{i}", **occ} for i, occ in enumerate(result["prodes"]["exact"]["occurrences"])]
    reading = []
    for occ in raw:
        day = date.fromisoformat(occ["properties"]["image_date"]) + timedelta(shift_days)
        reading.append({"id": occ["id"], "area_intersection_ha": occ["area_intersection_ha"],
                        "properties": {"year": occ["properties"]["year"], "image_date": day.isoformat(),
                                       "layer": "prodes-cerrado-nb:yearly_deforestation"}})
    result["prodes"]["exact_raw"] = {**result["prodes"]["exact"], "occurrences": raw}
    result["prodes"]["exact"] = {"available": True, "occurrence_count": len(reading), "area_unique_ha": 1.0, "occurrences": reading}
    return result


def stac_post_from_fixture():
    recorded = load("planetary_computer_landsat_218073_by_date.json")["responses"]

    def post(url, body):
        return recorded.get(body["datetime"][:10])
    return post


def prodes_rows_of(payload) -> list[tuple]:
    return [tuple(r) for r in payload["environment"]["prodes"]["rows"]]


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
    lookups = query_platforms_for_occurrences(rows, lonlat=CURVELO_LONLAT, post=stac_post_from_fixture())
    text = json.dumps(a.build_live_payload(prodes_result(lookups), "RX-F2", "2026-09-13T00:00:00+00:00", "map.png"), ensure_ascii=False)
    assert "Landsat8" not in text, text
    for expected in ("02/07/2002 · Landsat 7 (ETM+)", "31/07/2004 · Landsat 5 (TM)"):
        assert expected in text, f"{expected!r} missing"


@check
def prodes_leitura_mantem_orbita_pelo_calculo_bruto():
    import live_report_adapter as a
    from prodes_image_platform_f2 import query_platforms_for_occurrences

    result = reading_format_result()
    rows = a._extract_prodes_occurrences(result)
    assert len(rows) == 8 and all(r["path_row"] == "218/73" for r in rows), f"órbita perdida no formato da leitura PRODES: {rows[:2]}"
    result["prodes_image_lookups"] = query_platforms_for_occurrences(rows, lonlat=CURVELO_LONLAT, post=stac_post_from_fixture())
    text = json.dumps(a.build_live_payload(result, "RX-F2", "2026-09-13T00:00:00+00:00", "map.png"), ensure_ascii=False)
    assert "02/07/2002 · Landsat 7 (ETM+)" in text and "31/07/2004 · Landsat 5 (TM)" in text, "satélite com prova sumiu no formato da leitura"
    shifted = a._extract_prodes_occurrences(reading_format_result(shift_days=1))
    assert all(r["path_row"] is None for r in shifted), "órbita de outra imagem emprestada para a ocorrência"
    no_raw = reading_format_result()
    no_raw["prodes"].pop("exact_raw")
    assert all(r["path_row"] is None for r in a._extract_prodes_occurrences(no_raw))


@check
def prodes_pdf_corta_por_ocorrencia_inteira():
    import live_report_adapter as a

    base = load("prodes_cerrado_curvelo_feature_properties.json")["features"][0]
    occ = []
    for k, year in enumerate((2002, 2003, 2004, 2005)):
        props = {**base, "year": year, "image_date": None if k == 0 else f"{year}-07-15"}
        occ.append({"area_intersection_ha": 0.5, "properties": props})
    result = prodes_result()
    result["prodes"]["exact"]["occurrences"] = occ
    rows = prodes_rows_of(a.build_live_payload(result, "RX-F2", "2026-09-13T00:00:00+00:00", "map.png"))
    tail = rows[[r[0] for r in rows].index("Fonte") + 1:]
    labels = [r[0] for r in tail]
    for i, label in enumerate(labels):
        if label == "Ano":
            assert i + 1 < len(labels) and labels[i + 1] == "Área intersectada", f"ano sem a área no corte do PDF: {labels}"
        elif label == "Data da imagem":
            assert labels[i - 1] == "Área intersectada", labels
    assert labels.count("Ano") == 3, labels
    assert ("Data da imagem", "15/07/2003") in tail and ("Data da imagem", "15/07/2004") in tail, tail


@check
def api_resumo_nao_expoe_satelite_do_wfs():
    src = read_src("deploy_app.py")
    line = next(l for l in src.splitlines() if "exact_occurrences" in l)
    assert "'satellite'" not in line and "'sensor'" not in line, "API summary still copies the WFS satellite/sensor attribute"


@check
def landsat_catalogo_regra_de_prova():
    import prodes_image_platform_f2 as lp

    expected = {
        "2002-07-02": "Landsat 7", "2004-07-31": "Landsat 5", "2006-07-21": "Landsat 5",
        "2010-08-01": "Landsat 5", "2014-08-12": "Landsat 8", "2020-09-13": "Landsat 8", "2021-09-16": "Landsat 8",
    }
    recorded, calls = stac_post_from_fixture(), []

    def post(url, body):
        calls.append(body)
        return recorded(url, body)
    for day, platform in expected.items():
        res = lp.query_landsat_platform(day, "218/73", CURVELO_LONLAT, post)
        assert res["status"] == "found" and res["platform"] == platform and res["path_row"] == "218/073", (day, res)
    body = calls[0]
    assert body["query"]["landsat:wrs_path"]["eq"] == "218" and body["query"]["landsat:wrs_row"]["eq"] == "073", body
    assert body["intersects"]["coordinates"] == list(CURVELO_LONLAT), body
    # only a WRS-2 orbit plus the property centroid can prove the scene: otherwise the catalog is not even asked
    for path_row, lonlat in ((None, CURVELO_LONLAT), ("", CURVELO_LONLAT), ("0/73", CURVELO_LONLAT), ("234/73", CURVELO_LONLAT),
                             ("218/249", CURVELO_LONLAT), ("23KMA", CURVELO_LONLAT), ("218/73", None)):
        calls.clear()
        res = lp.query_landsat_platform("2004-07-31", path_row, lonlat, post)
        assert res["status"] == "not_found" and not calls, f"sem órbita WRS-2 válida ou sem centróide não se consulta: {(path_row, lonlat, res, len(calls))}"
    d, pr = date(2004, 7, 31), ("218", "073")

    def scene(sid, platform, inst, when, path="218", row="073"):
        return {"id": sid, "properties": {"platform": platform, "instruments": [inst], "datetime": when,
                                          "landsat:wrs_path": path, "landsat:wrs_row": row}}
    assert lp.platform_from_stac({"features": []}, d, pr)["status"] == "not_found"
    two = {"features": [scene("a", "landsat-5", "tm", "2004-07-31T12:00:00Z"), scene("b", "landsat-7", "etm+", "2004-07-31T12:10:00Z")]}
    assert lp.platform_from_stac(two, d, pr)["status"] == "not_found", "ambiguous date must not pick a satellite"
    other_day = {"features": [scene("c", "landsat-8", "oli", "2004-08-01T12:00:00Z")]}
    assert lp.platform_from_stac(other_day, d, pr)["status"] == "not_found"
    other_orbit = {"features": [scene("e", "landsat-7", "etm+", "2004-07-31T12:45:00Z", "219", "073")]}
    assert lp.platform_from_stac(other_orbit, d, pr)["status"] == "not_found", "cena de outra órbita sobre o centróide não prova o satélite"
    res = lp.query_landsat_platform("2004-07-31", "218/73", CURVELO_LONLAT, lambda u, b: other_orbit)
    assert res["status"] == "not_found", f"cena de outra órbita sobre o centróide não prova o satélite: {res}"
    assert lp.query_landsat_platform("2004-07-31", "218/73", CURVELO_LONLAT, lambda u, b: None)["status"] == "pending"

    def boom(u, b):
        raise TimeoutError("x")
    assert lp.query_landsat_platform("2004-07-31", "218/73", CURVELO_LONLAT, boom)["status"] == "pending"
    pending = lp.prodes_image_payload([{"year": 2004, "image_date": "2004-07-31", "path_row": "218/73"}],
                                      {"2004-07-31|218/073": {"status": "pending"}})[0]
    assert pending["text"] == "31/07/2004" and pending["platform"] is None and pending["platform_status"] == "pending", pending
    no_orbit = lp.prodes_image_payload([{"year": 2004, "image_date": "2004-07-31", "path_row": None}])[0]
    assert no_orbit["platform_status"] == "not_found" and no_orbit["text"] == "31/07/2004", f"sem órbita é limite do dado, não consulta pendente: {no_orbit}"
    wrong = {"status": "found", "image_date": "2004-07-31", "platform": "Landsat 7", "instrument": "ETM+", "path_row": "219/073"}
    assert lp.prodes_image_text({"image_date": "2004-07-31", "path_row": "218/73"}, wrong) == "31/07/2004", "achado de outra órbita rotulou a ocorrência"
    assert lp.image_row({"image_date": None}) is None, "campo vazio não aparece"


def crlf_files(paths) -> list[str]:
    return [p.name for p in paths if b"\r" in Path(p).read_bytes()]


@check
def fixtures_em_lf_e_captura_grava_lf():
    files = sorted(FIX.glob("*.json"))
    assert len(files) >= 6, f"fixtures sumiram: {files}"
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "crlf.json"
        sample.write_bytes(b'{\r\n "a": 1\r\n}\r\n')
        assert crlf_files([sample]) == ["crlf.json"], "o detector de CRLF não enxerga CRLF"
        out = Path(tmp) / "captured.json"
        write_fixture(out, {"a": ["b", 1]})
        assert crlf_files([out]) == [], "write_fixture grava CRLF"
    assert "write_fixture(" in inspect.getsource(capture), "a captura não usa write_fixture"
    bad = crlf_files(files)
    assert not bad, f"fixtures com CRLF: {bad}"


# --------------------------------------------------------------------------- positive controls (mutations)

class mutated:
    """Swap a module (and every name other modules imported from it) for a copy with literal replacements."""

    def __init__(self, rel: str, replacements: list[tuple[str, str]], module: bool = True):
        self.rel, self.replacements, self.module, self.undo = rel, replacements, module, []

    def __enter__(self):
        try:
            return self._enter()
        except BaseException:
            self.__exit__()
            raise

    def _enter(self):
        path = ROOT / self.rel
        src = path.read_bytes().decode("utf-8")
        for old, new in self.replacements:
            if src.count(old) != 1:
                raise AssertionError(f"controle desatualizado: trecho casa {src.count(old)}x em {self.rel}: {old[:90]!r}")
            src = src.replace(old, new)
        SRC_OVERRIDE[self.rel] = src
        if not self.module:
            return self
        name = self.rel[:-3].replace("/", ".")
        old_mod = importlib.import_module(name)
        new_mod = types.ModuleType(name)
        new_mod.__file__ = str(path)
        sys.modules[name] = new_mod
        self.undo.append((sys.modules, name, old_mod))
        exec(compile(src, str(path), "exec"), new_mod.__dict__)
        own = {id(v): k for k, v in vars(old_mod).items() if callable(v) and getattr(v, "__module__", None) == name}
        for mod in list(sys.modules.values()):
            d = getattr(mod, "__dict__", None)
            if mod is new_mod or not isinstance(d, dict):
                continue
            for attr, val in list(d.items()):
                if val is old_mod:
                    self.undo.append((d, attr, val))
                    d[attr] = new_mod
                elif id(val) in own and vars(old_mod).get(own[id(val)]) is val and own[id(val)] in vars(new_mod):
                    self.undo.append((d, attr, val))
                    d[attr] = vars(new_mod)[own[id(val)]]
        return self

    def __exit__(self, *exc):
        for d, attr, val in reversed(self.undo):
            d[attr] = val
        SRC_OVERRIDE.pop(self.rel, None)
        return False


CONTROLS = [
    ("razão volta a chamar 40–150% da média de 'dentro do normal' (revisão, alta)", "climate_normal_f2.py",
     [("RATIO_NEAR_LOW_PCT = 90", "RATIO_NEAR_LOW_PCT = 40"), ("RATIO_NEAR_HIGH_PCT = 110", "RATIO_NEAR_HIGH_PCT = 150"),
      ('"near_average": "perto da média da época",', '"near_average": "dentro do normal",')],
     {"clima_razao_contra_a_media_confere_com_a_serie_real": "dentro do normal"}, True),
    ("limite do alerta sobe de 40% para 60% da média (precisão da calibração)", "climate_normal_f2.py",
     [("RATIO_LOW_PCT = 40", "RATIO_LOW_PCT = 60")],
     {"clima_razao_contra_a_media_confere_com_a_serie_real": "abaixo do normal fora dos 30% mais secos"}, True),
    ("estado pela razão sem arredondar, texto arredondado (revisão, média)", "climate_normal_f2.py",
     [("        elif ratio_pct < RATIO_LOW_PCT:", "        elif shown_rain / shown_mean * 100 < RATIO_LOW_PCT:")],
     {"clima_texto_e_estado_usam_o_mesmo_numero": "mesmo percentual impresso"}, True),
    ("percentil do estado com uma casa, texto inteiro", "climate_normal_f2.py",
     [("            pct = half_up((below + 0.5 * ties) / len(values) * 100)",
       "            pct = round((below + 0.5 * ties) / len(values) * 100, 1)")],
     {"clima_texto_e_estado_usam_o_mesmo_numero": "percentil impresso"}, True),
    ("cena de qualquer órbita na data vira prova (revisão, baixa)", "prodes_image_platform_f2.py",
     [(" or _scene_orbit(props) != tuple(path_row)", "")],
     {"landsat_catalogo_regra_de_prova": "outra órbita"}, True),
    ("busca só pelo centróide volta a consultar o catálogo", "prodes_image_platform_f2.py",
     [('    if not pr:\n        return {"status": "not_found", "reason": "sem_orbita_wrs2", "image_date": day.isoformat()}\n    if not lonlat:\n'
       '        return {"status": "not_found", "reason": "sem_centroide_do_imovel", "image_date": day.isoformat()}\n',
       "    if not pr and not lonlat:\n        return {\"status\": \"not_found\", \"reason\": \"sem_orbita_ponto_nem_centroide\", \"image_date\": day.isoformat()}\n")],
     {"landsat_catalogo_regra_de_prova": "não se consulta"}, True),
    ("órbita fora da faixa WRS-2 aceita", "prodes_image_platform_f2.py",
     [("    if not m or not (1 <= int(m.group(1)) <= 233 and 1 <= int(m.group(2)) <= 248):", "    if not m:")],
     {"landsat_catalogo_regra_de_prova": "não se consulta"}, True),
    ("ocorrência sem órbita fica 'pendente'", "prodes_image_platform_f2.py",
     [('("pending" if key and not key.endswith("|") else "not_found")', '("pending" if key else "not_found")')],
     {"landsat_catalogo_regra_de_prova": "não consulta pendente"}, True),
    ("relatório ignora a prova do catálogo (regra 11 sem ModuleNotFoundError)", "live_report_adapter.py",
     [("        rows[-1]['image_lookup'] = lookups.get(prodes_lookup_key(rows[-1]) or '')", "        rows[-1]['image_lookup'] = None")],
     {"prodes_pdf_mostra_satelite_so_com_prova_do_catalogo": "missing",
      "prodes_leitura_mantem_orbita_pelo_calculo_bruto": "satélite com prova sumiu"}, True),
    ("leitura PRODES perde a órbita (revisão, média: conflito com f2/prodes_leitura)", "live_report_adapter.py",
     [("        if not path_row and item.get('id') in raw_orbits:", "        if False:")],
     {"prodes_leitura_mantem_orbita_pelo_calculo_bruto": "órbita perdida"}, True),
    ("órbita do cálculo bruto emprestada sem conferir a data", "live_report_adapter.py",
     [("            path_row = raw_path_row if same_image else None", "            path_row = raw_path_row")],
     {"prodes_leitura_mantem_orbita_pelo_calculo_bruto": "emprestada"}, True),
    ("corte do PDF volta a ser por linha (revisão, baixa)", "live_report_adapter.py",
     [("    for r in prodes_rows[:3]:", "    for r in prodes_rows[:8]:"), ("                ] + exact_rows,\n", "                ] + exact_rows[:9],\n")],
     {"prodes_pdf_corta_por_ocorrencia_inteira": "ano sem a área"}, True),
    ("aba Clima chama o limite de 120 dias de 'pendente' (revisão, baixa)", "portal_property_tabs.py",
     [(",janela_longa_sem_serie_historica:'A comparação com a média da época está disponível para períodos de até 120 dias.'", "")],
     {"portal_aba_clima_texto_honesto_depois_do_v35": "limite do método mostrado como pendente"}, False),
]


def run_controls() -> int:
    by_name = {fn.__name__: fn for fn in CHECKS}
    failed = 0
    for title, rel, replacements, expected, module in CONTROLS:
        try:
            outcomes = {}
            with mutated(rel, replacements, module):
                for name in expected:
                    try:
                        by_name[name]()
                        outcomes[name] = None
                    except Exception as exc:
                        outcomes[name] = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            missed = [n for n, why in expected.items() if not outcomes[n] or why not in outcomes[n]]
            if missed:
                failed += 1
                print(f"FAIL controle '{title}': a mutação passou em {missed}: {[str(outcomes[n])[:200] for n in missed]}")
            else:
                first = next(iter(expected))
                print(f"PASS controle '{title}': {list(expected)} reprovou — {outcomes[first].splitlines()[-1][:160]}")
        except Exception as exc:
            failed += 1
            print(f"FAIL controle '{title}': {''.join(traceback.format_exception_only(type(exc), exc)).strip()[:300]}")
    return failed


# --------------------------------------------------------------------------- capture (network, manual)

def capture() -> int:
    import prodes_image_platform_f2 as lp

    props = load("prodes_cerrado_curvelo_feature_properties.json")["features"]
    responses = {}
    for p in props:
        day = lp.image_day(p["image_date"])
        responses[day.isoformat()] = lp._curl_post_json(lp.STAC_SEARCH, lp.search_body(day, lp.wrs_path_row(p["path_row"]), CURVELO_LONLAT))
    out = {"source": lp.STAC_SEARCH, "recorded": date.today().isoformat(),
           "request": "search_body(data, órbita WRS-2, centróide do imóvel) do módulo", "responses": dict(sorted(responses.items()))}
    write_fixture(FIX / "planetary_computer_landsat_218073_by_date.json", out)
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
    control_failed = run_controls()
    ok = not failed and not control_failed
    print(f"RX_F2_CLIMA_LANDSAT_GATE={'PASS' if ok else 'FAIL'} (regras {len(CHECKS) - failed}/{len(CHECKS)}, "
          f"controles positivos {len(CONTROLS) - control_failed}/{len(CONTROLS)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

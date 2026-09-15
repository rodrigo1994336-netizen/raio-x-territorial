"""F1B gate: nothing written over the map, the full analysis shown in the panel, one call per source.

Boots the real portal composition in-process (sitecustomize -> deferred modules -> boot guard ready),
takes the FINAL portal HTML and checks, without any external network:

1B.1  hover: no Leaflet tooltip on the CAR parcels (no bindTooltip/setTooltipContent/L.tooltip other
      than the user's own "Sua localização" marker, no municipality/area tooltip builder);
1B.2  "Ver análise completa": the last word on window.rxProgressiveAnalyze is the F1B reading; the F1B
      reading renders into a section of the visible panel card (never #pbody, never renderAnalysis) and
      no CSS hides that section; the internal wording ("PREPARADO — OFF", "BACKEND DE ALERTAS
      INDISPONÍVEL", "RISCO NÃO CLASSIFICADO", "N de M fontes responderam") is absent from every
      script that paints the panel; in node (scripts/f1b_tela_harness.js) the served F1B script answers
      only Sim / Não / Consulta pendente, "Sim"/"Não" only from a source that answered, pt-BR numbers,
      escaped source text, and never shows an answer about another CAR; on the first engine answer only a
      cache hit counts (after the engine cache expires, the previous run's "ready" is never shown), the time
      shown is the time the data was produced (completed_at, or a run seen running; fire: the INPE bulletin);
      PRODES shows every year (or the count and range), the post-31/07/2019 part and "pelo menos … nas
      camadas que responderam" when a layer failed; pending rows are asked again once by themselves and
      only rows that came to answer change; the "ver fontes e datas" box lists what this consultation knows
      and follows the full analysis (no "NÃO CONSULTADA", no "pendente de implementação");
      the engine side (report_v9_patch + report_quick_v22) runs in scripts/f1b_motor_estado_check.py;
1B.3  deduplication: the served F1B reading asks /v1/live/quick ONCE for five starts + clicks +
      re-renders, retries a worker that did not answer exactly once, stops polling when the panel
      closes; the served CAR-integrity script asks once per property (twice when the base did not
      answer: one automatic retry) through the shared per-CAR runtime; /map-panel is shared by the card
      and the panel; the action bar is sticky with 44 px buttons and the panel is capped to its own
      container (not 100vh, which pushed the buttons off a 1440x900 screen).

Positive control of every rule: the gate mutates the served code to reintroduce each defect (tooltip,
legacy progressive analysis, hidden section, #pbody writer, internal wording, integrity timers,
broken single-flight, unbounded retry, other-CAR answer, "ok" not required, sticky bar and 100vh,
PDF as a small link, plain /map-panel fetch) and requires the rule's check to FAIL on the mutant.
A check that passes on its mutant fails the gate.

Part 2 (1B.6 UF by the IBGE borders and municipality search without Nominatim, 1B.7 card painted at once
with the SICAR dates and without jumping, 1B.8 report engine wake with tab and server locks) lives in
scripts/f1b_parte2_checks.py (+ scripts/f1b_parte2_harness.js), run from main() with its own positive controls.

Run: PYTHONPATH=. python scripts/f1b_tela_gate.py   (needs node on PATH; re-runs itself with the
portal env). The real screens (hover, calls per source measured in a browser, button position at
375/768/1440 and 1440x900) are in scripts/f1b_tela_smoke.py (local, needs SICAR: not in CI).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "f1b_tela_harness.js"
RELEASE = "V8_OPERATIONAL_ZERO_COST"
INTERNAL = ("PREPARADO — OFF", "BACKEND DE ALERTAS INDISPONÍVEL", "RISCO NÃO CLASSIFICADO", "fontes responderam",
            "PENDENTE DE IMPLEMENTAÇÃO", "integra o contador")
FAILURES: list[str] = []


def check(ok: bool, label: str) -> bool:
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    if not ok:
        FAILURES.append(label)
    return ok


def reexec_with_portal_env() -> None:
    if os.environ.get("RX_RELEASE") == RELEASE and os.environ.get("F1B_GATE_CHILD") == "1":
        return
    env = dict(os.environ, RX_RELEASE=RELEASE, F1B_GATE_CHILD="1", PYTHONUTF8="1")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    sys.exit(subprocess.call([sys.executable, str(Path(__file__).resolve())], env=env, cwd=str(ROOT)))


def boot_portal() -> str:
    sys.path.insert(0, str(ROOT))
    import sitecustomize  # noqa: F401
    import portal_api  # noqa: F401
    import portal_boot_guard_v26 as guard

    deadline = time.time() + 60
    while time.time() < deadline and not guard.STATE.get("ready") and not guard.STATE.get("error"):
        time.sleep(0.1)
    assert guard.STATE.get("ready") is True, ("portal boot failed", guard.STATE)
    import portal_v8

    return portal_v8.PORTAL_HTML


def enclosing_script(html: str, token: str) -> str:
    i = html.index(token)
    start = html.rindex("<script", 0, i)
    start = html.index(">", start) + 1
    end = html.index("</script>", i)
    return html[start:end]


def region(html: str, start: str, end: str) -> str:
    a = html.index(start)
    return html[a:html.index(end, a) + len(end)]


def panel_scripts(html: str) -> dict[str, str]:
    finders = {
        "v45_panel": lambda: region(html, '<style id="rxMapPanelV45">', "<!-- RX_MAP_PANEL_V45 -->"),
        "mte": lambda: enclosing_script(html, "const MTE_ID='mte_slave_labor'"),
        "sinaflor": lambda: region(html, "<!-- RX_CONFORMITY_MTE_V48 -->", "<!-- RX_CONFORMITY_SINAFLOR_V48 -->"),
        "integrity": lambda: enclosing_script(html, "window.rxV47IntegrityInstalled=true"),
        "f1b": lambda: enclosing_script(html, "window.rxFullReadingF1b={"),
    }
    out = {}
    for name, find in finders.items():
        try:
            out[name] = find()
        except ValueError:
            continue  # a missing painter is judged by the other rules; here only wording is checked
    return out


# ------------------------------------------------------------------ static rules (html -> problems)
def rule_no_map_tooltip(html: str) -> list[str]:
    problems = []
    tips = re.findall(r"bindTooltip\(([^)]{0,80})\)", html)
    extra = [t for t in tips if t.strip() != "'Sua localização'"]
    if extra:
        problems.append(f"parcel tooltip bound: {extra[:3]}")
    for token in ("setTooltipContent", "L.tooltip(", "rxTipC2", "openTooltip("):
        if token in html:
            problems.append(f"tooltip code present: {token}")
    return problems


def rule_reading_visible(html: str) -> list[str]:
    problems = []
    if html.count("<!-- RX_FULL_READING_F1B -->") != 1:
        return ["F1B reading not injected exactly once"]
    assigns = re.findall(r"window\.rxProgressiveAnalyze=([A-Za-z_$][\w$]*)", html)
    if not assigns or assigns[-1] != "fromButton":
        problems.append(f"last window.rxProgressiveAnalyze assignment is not the F1B reading: {assigns}")
    f1b = enclosing_script(html, "window.rxFullReadingF1b={")
    if html.index("window.rxProgressiveAnalyze=progressiveAnalyze") > html.index("window.rxFullReadingF1b={"):
        problems.append("legacy progressive analysis loads after the F1B reading")
    for token in ("pbody", "renderAnalysis", "#ptitle"):
        if token in f1b:
            problems.append(f"F1B reading touches the hidden legacy dossier: {token}")
    if ".rx45-panel-card[data-car=" not in f1b or "[data-rx-full-slot]" not in f1b:
        problems.append("F1B reading does not render into the panel card")
    if "window.rxProgressiveAnalyze();else if(typeof analyze==='function')analyze()" not in html:
        problems.append("panel button no longer routes through window.rxProgressiveAnalyze")
    if "[data-rx46-action=\"full\"]" not in f1b:
        problems.append("card CTA does not start the reading")
    for style in re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S):
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
            if ("data-rx-full-slot" in sel or "rx-f1b-full" in sel or "rx-f1b-rows" in sel or "rx-f1b-row" in sel) and re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", body):
                problems.append(f"CSS hides the reading: {sel.strip()[:80]}")
    return problems


def rule_no_internal_text(html: str) -> list[str]:
    problems = []
    for name, text in panel_scripts(html).items():
        for token in INTERNAL:
            if token in text:
                problems.append(f"{name}: {token}")
    return problems


def rule_dedup_static(html: str) -> list[str]:
    problems = []
    integ = enclosing_script(html, "window.rxV47IntegrityInstalled=true")
    if re.search(r"\[\s*90\s*,\s*720\s*,\s*2300\s*,\s*9800\s*\]", integ):
        problems.append("integrity refetch timers are back")
    if "S.settle(SOURCE_ID,car,query,paint)" not in integ or "rx45:panel-rendered" not in integ:
        problems.append("integrity does not go through the shared per-CAR runtime")
    if integ.count("/v1/live/car-integrity/") != 1:
        problems.append("integrity fetch appears more than once")
    if html.count("window.rxMapPanelOnce=function(car)") != 1:
        problems.append("shared /map-panel request is not defined exactly once")
    plain = html.count("fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`)")
    # one inside rxMapPanelOnce, one in the V46 fallback branch used only when the helper is missing
    if plain != 2 or "window.rxMapPanelOnce?window.rxMapPanelOnce(car):" not in html or "await window.rxMapPanelOnce(car)" not in html:
        problems.append(f"card or panel asks /map-panel on its own (plain fetches: {plain})")
    return problems


def rule_actions_on_screen(html: str) -> list[str]:
    problems = []
    v45 = region(html, '<style id="rxMapPanelV45">', "<!-- RX_MAP_PANEL_V45 -->")
    if ".rx45-actions{position:sticky;bottom:0" not in v45:
        problems.append("action bar is not sticky")
    if ".rx45-actions button{min-height:44px" not in v45:
        problems.append("action buttons below 44 px")
    if "max-height:calc(100vh - 28px)" in v45 or "max-height:calc(100% - 28px)!important" not in v45:
        problems.append("desktop panel capped by the viewport instead of its container")
    if "overflow:clip" not in v45:
        problems.append("card still clips the sticky bar")
    if '<button class="rx45-pdf" id="rx45Pdf" type="button">GERAR PDF</button>' not in v45 or "rx45-pdf-link" in v45:
        problems.append("PDF is not a 44 px button")
    if '.rx45-check[data-audit-state="NOT_QUERIED"]:not([data-state]){display:none' not in v45:
        problems.append("unconsulted placeholder chips still shown")
    return problems


# ------------------------------------------------------------------ node harness
def run_harness(f1b: str, f2: str, integrity: str, audit: str = "") -> dict:
    node = shutil.which("node")
    assert node, "node not found on PATH"
    # Times are shown in the viewer's local time: the harness runs in Brazil's time zone on every machine.
    proc = subprocess.run([node, str(HARNESS)], input=json.dumps({"f1b": f1b, "f2": f2, "integrity": integrity, "audit": audit}),
                          capture_output=True, text=True, encoding="utf-8", timeout=120, env=dict(os.environ, TZ="America/Sao_Paulo"))
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"fatal": (proc.stdout or "")[-800:] + (proc.stderr or "")[-800:]}


def judge_harness(r: dict) -> list[str]:
    p = []
    if r.get("fatal"):
        return [f"harness crashed: {r['fatal'][:300]}"]
    if not r.get("api"):
        return ["F1B api missing"]
    rows = {x["id"]: x for x in r.get("rows") or []}
    labels = {"sim", "nao", "pendente"}
    if any(x["answer"] not in labels for x in rows.values()):
        p.append("answer outside Sim/Não/Consulta pendente")
    exp = {"embargo_ibama": "nao", "anm": "sim", "autos_ibama": "nao", "prodes": "sim", "fire": "nao", "terra_indigena": "nao",
           "unidade_conservacao": "sim", "embargo_icmbio": "pendente", "floresta_publica": "pendente", "water": "sim", "pivot": "nao"}
    for k, v in exp.items():
        if (rows.get(k) or {}).get("answer") != v:
            p.append(f"row {k}: expected {v}, got {(rows.get(k) or {}).get('answer')}")
    if "1.234,57 ha" not in (rows.get("anm") or {}).get("detail", ""):
        p.append(f"pt-BR area missing: {(rows.get('anm') or {}).get('detail')}")
    prodes = (rows.get("prodes") or {}).get("detail", "")
    if "anos PRODES 2006 e 2021" not in prodes or "1 posterior a 31/07/2019 (ano PRODES 2021; 1,20 ha)" not in prodes or "pelo menos" in prodes:
        p.append(f"PRODES years or post-cutoff missing: {prodes}")
    p9 = r.get("rows_prodes_9y") or {}
    if p9.get("answer") != "sim" or "9 anos PRODES entre 2008 e 2025" not in p9.get("detail", "") or "20 polígonos" not in p9.get("detail", "") \
            or "9 posteriores a 31/07/2019" not in p9.get("detail", "") or "anos PRODES 2021, 2023" in p9.get("detail", ""):
        p.append(f"PRODES years cut (only the last ones shown): {p9.get('detail')}")
    pi = r.get("rows_prodes_incomplete") or {}
    if pi.get("answer") != "sim" or not pi.get("detail", "").startswith("pelo menos 20 polígonos") or "nas camadas que responderam" not in pi.get("detail", "") \
            or "nenhum posterior" in pi.get("detail", ""):
        p.append(f"incomplete PRODES shown as the total: {pi.get('detail')}")
    if (r.get("rows_prodes_not_found_incomplete") or {}).get("answer") != "pendente":
        p.append("incomplete PRODES without findings answered Não")
    fire = (rows.get("fire") or {}).get("detail", "")
    if "o mais recente de 14/09/2026, 20:20" not in fire:
        p.append(f"fire answer without the INPE bulletin time: {fire}")
    if (r.get("rows_fire_undated") or {}).get("answer") != "pendente":
        p.append("fire answered without the time of the bulletin it read")
    order = [x["answer"] for x in r["rows"]]
    if "pendente" in order and any(a != "pendente" for a in order[order.index("pendente"):]):
        p.append("pending questions are not listed last")
    if (r.get("rows_capped") or {}).get("answer") != "pendente":
        p.append("capped zero (200 não é todos) answered Não")
    if (r.get("rows_exact_unavailable") or {}).get("answer") != "pendente":
        p.append("unavailable exact answer became Não")
    if (r.get("rows_ok_missing") or {}).get("answer") != "pendente":
        p.append("source without ok===true answered")
    if r.get("rows_water_outside") != 0:
        p.append("source outside its coverage is shown")
    if (r.get("rows_pivot_partial") or {}).get("answer") != "pendente":
        p.append("partially parsed zero answered Não")
    if r.get("rows_tc_failed") != ["pendente", "pendente"]:
        p.append(f"failed constraints not pending: {r.get('rows_tc_failed')}")
    if (r.get("rows_prodes_pending") or {}).get("answer") != "pendente":
        p.append("PRODES pending answered")
    if r.get("rows_empty") != 0:
        p.append("empty analysis produced rows")
    html_ready = r.get("html_ready") or ""
    for token in ("Consulta feita em", ">Sim<", ">Não<", ">Consulta pendente<", "Fonte: "):
        if token not in html_ready:
            p.append(f"ready html missing {token!r}")
    everything = "".join(str(r.get(k) or "") for k in ("html_ready", "html_ready_unstamped", "html_loading", "html_failed", "html_fill_idle"))
    if "Resposta recebida em" in everything:
        p.append("arrival time shown as the time of the data ('Resposta recebida em')")
    if "Consulta feita em" in (r.get("html_ready_unstamped") or ""):
        p.append("time of the data shown without a known production time")
    if "<img" in html_ready or "&lt;img src=x onerror=alert(1)&gt;" not in html_ready:
        p.append("source text not escaped")
    if re.search(r"\d\.\d{3,}\s*ha", html_ready):
        p.append("non pt-BR number in ready html")
    for token in INTERNAL:
        if token in html_ready or token in (r.get("html_failed") or "") or token in (r.get("html_loading") or ""):
            p.append(f"internal text in reading html: {token}")
    loading = r.get("html_loading") or ""
    if "rx-f1b-spin" not in loading or "Pode levar alguns minutos" not in loading or "1 minuto" in loading:
        p.append("loading state missing or promising a time the code does not keep")
    if "data-rx-f1b-retry" not in (r.get("html_failed") or "") or "Consulta pendente" not in (r.get("html_failed") or ""):
        p.append("failed state without quiet pending + retry")
    if "Nova tentativa das consultas pendentes às" not in (r.get("html_fill_scheduled") or "") or "Consultando de novo as fontes pendentes" not in (r.get("html_fill_running") or "") \
            or "data-rx-f1b-fill" not in (r.get("html_fill_idle") or ""):
        p.append("pending rows without their retry states (scheduled, running, Consultar de novo)")
    if "rx-f1b-fill" in (r.get("html_complete") or ""):
        p.append("retry offered for a reading without pending rows")
    mg = r.get("merge") or {}
    if mg.get("answers", {}).get("embargo_ibama") != "nao":
        p.append(f"merge replaced an answer by a later failure: {mg}")
    if not mg.get("changed") or mg.get("answers", {}).get("embargo_icmbio") != "nao" or mg.get("answers", {}).get("floresta_publica") != "pendente" or not mg.get("pending_last"):
        p.append(f"merge did not fill only the pending rows: {mg}")
    s = r.get("single") or {}
    if s.get("quick") != 1:
        p.append(f"single-flight broken: quick asked {s.get('quick')}x")
    if s.get("phase") != "ready" or not s.get("slot_in_card") or "Consulta feita em 14/09/2026, 20:00" not in (s.get("slot_html") or ""):
        p.append(f"reading not painted in the panel card with the time of the data: {s.get('phase')} in_card={s.get('slot_in_card')}")
    if s.get("at") != s.get("stamp_ms"):
        p.append(f"cache answer shown with the answer time, not the time of the data: at={s.get('at')} stamp={s.get('stamp_ms')}")
    if s.get("pbody"):
        p.append("reading wrote into #pbody")
    if s.get("button") != "VER ANÁLISE COMPLETA" or not s.get("scrolled"):
        p.append(f"button/scroll after ready wrong: {s.get('button')} scrolled={s.get('scrolled')}")
    pol = r.get("polling") or {}
    if pol.get("quick") != 1 or pol.get("status") != 3 or pol.get("phase") != "ready" or not pol.get("at_recent"):
        p.append(f"deep polling wrong: {pol}")
    if pol.get("loading_button") != "VER ANÁLISE COMPLETA" or pol.get("loading_busy") != "true" or pol.get("ready_busy") is not None:
        p.append(f"main button renamed while loading (or busy state wrong): {pol.get('loading_button')!r} busy={pol.get('loading_busy')}/{pol.get('ready_busy')}")
    st = r.get("stale") or {}
    if "2 processos" not in (st.get("first_anm") or "") or "7 processos" not in (st.get("anm") or "") or (st.get("status") or 0) < 2:
        p.append(f"previous run shown as current after the engine cache expired: {st.get('first_anm')} -> {st.get('anm')} status={st.get('status')}")
    elif st.get("at") != st.get("new_stamp_ms"):
        p.append(f"time of the data wrong after the cache expired: at={st.get('at')} expected={st.get('new_stamp_ms')}")
    pa = r.get("partial") or {}
    if pa.get("quick_after_auto") != 2 or pa.get("answers", {}).get("embargo_icmbio") != "nao":
        p.append(f"pending rows not retried once by themselves: quick={pa.get('quick_after_auto')} answers={pa.get('answers')}")
    if pa.get("embargo_ibama") != "nao" or pa.get("answers", {}).get("floresta_publica") != "pendente":
        p.append(f"merge after the automatic retry changed an answer: {pa.get('answers')}")
    if "data-rx-f1b-fill" not in (pa.get("slot") or "") or "pendências consultadas de novo em" not in (pa.get("slot") or "") or not pa.get("refilled"):
        p.append("after the automatic retry: no 'Consultar de novo' or no time of the retry")
    if pa.get("quick_after_manual") != 3:
        p.append(f"'Consultar de novo' did not ask again: {pa.get('quick_after_manual')}")
    if pa.get("closed_quick") != 1:
        p.append(f"automatic retry for a closed panel: quick={pa.get('closed_quick')}")
    f = r.get("failure") or {}
    if f.get("quick") != 2 or f.get("phase") != "failed":
        p.append(f"worker failure not exactly one automatic retry: {f}")
    if "data-rx-f1b-retry" not in (f.get("slot") or "") or f.get("button") != "VER ANÁLISE COMPLETA":
        p.append(f"failure not shown as quiet pending with its own retry (main button stays VER ANÁLISE COMPLETA): {f.get('button')}")
    if f.get("after_rerender") != 2:
        p.append(f"failed reading re-asked on re-render: {f.get('after_rerender')}")
    if (f.get("after_force") or 0) <= 2:
        p.append("explicit retry did not ask again")
    se = r.get("status_errors") or {}
    if se.get("quick") != 2 or se.get("status") != 6 or se.get("phase") != "failed":
        p.append(f"status errors not bounded (3 per attempt, one retry): {se}")
    o = r.get("other_car") or {}
    if o.get("phase") != "failed" or o.get("rows") or "data-rx-f1b-row" in (o.get("slot") or ""):
        p.append(f"answer about another CAR was shown: {o.get('phase')}")
    c = r.get("closed") or {}
    if c.get("entry") is not None or (c.get("status") or 0) > 3 or c.get("quick") != 1:
        p.append(f"closed panel kept polling: {c}")
    if (r.get("integrity_ok") or {}).get("fetches") != 1:
        p.append(f"integrity asked {(r.get('integrity_ok') or {}).get('fetches')}x for an answered property")
    if (r.get("integrity_fail") or {}).get("fetches") != 2 or (r.get("integrity_fail") or {}).get("phase") != "failed":
        p.append(f"integrity failure not exactly one automatic retry: {r.get('integrity_fail')}")
    a = r.get("audit") or {}
    if a.get("fatal") or not a.get("api"):
        p.append(f"audit box script not runnable: {a.get('fatal')}")
    else:
        for when_, text in (("before", a.get("before") or ""), ("after", a.get("after") or ""), ("rebuilt", a.get("rebuilt") or "")):
            leaked = [t for t in ("NÃO CONSULTADA", "PENDENTE DE IMPLEMENTAÇÃO", "implementação", "contador", "Reserva Legal", "Matrícula") if t in text]
            if leaked:
                p.append(f"audit lists sources nobody asked or internal text ({when_}): {leaked}")
            if "CAR / SICAR" not in text or "MTE — Trabalho Escravo" not in text or "NÃO VERIFICADA" not in text:
                p.append(f"audit lost the sources that answered ({when_}): {text[:160]}")
        for text in (a.get("after") or "", a.get("rebuilt") or ""):
            if "IBAMA — áreas embargadas RESPONDEU · COM OCORRÊNCIA" not in text or "INPE — PRODES RESPONDEU · COM OCORRÊNCIA" not in text \
                    or "consulta: 14/09/2026" not in text or " Embargos " in f" {text} ":
                p.append(f"audit contradicts the full analysis (embargo/PRODES answered): {text[:240]}")
                break
    return p


def scripts_for_harness(html: str) -> tuple[str, str, str, str]:
    return (enclosing_script(html, "window.rxFullReadingF1b={"), enclosing_script(html, "if(window.rxPanelSourcesF2)return;"),
            enclosing_script(html, "window.rxV47IntegrityInstalled=true"), enclosing_script(html, "const MTE_ID='mte_slave_labor'"))


def mutate(text: str, old: str, new: str, label: str) -> str:
    assert text.count(old) >= 1, f"positive control anchor missing ({label}): {old[:70]}"
    return text.replace(old, new)


def main() -> int:
    reexec_with_portal_env()
    html = boot_portal()
    rules = {
        "1B.1 no tooltip over the map": rule_no_map_tooltip,
        "1B.2 reading visible in the panel": rule_reading_visible,
        "1B.2 no internal wording in panel scripts": rule_no_internal_text,
        "1B.3 one request per source (static)": rule_dedup_static,
        "1B.3 action bar on screen, 44 px": rule_actions_on_screen,
    }
    for label, rule in rules.items():
        try:
            problems = rule(html)
        except Exception as exc:  # the served code lacks what the rule reads: that is a failure, not a crash
            problems = [f"rule could not read the served code: {type(exc).__name__}: {exc}"]
        check(not problems, f"{label} {problems if problems else ''}".rstrip())

    try:
        f1b, f2, integ, audit = scripts_for_harness(html)
    except Exception as exc:
        check(False, f"served F1B/panel scripts not found: {type(exc).__name__}: {exc}")
        print("F1B_TELA_GATE=FAIL " + json.dumps(FAILURES, ensure_ascii=False), flush=True)
        return 1
    result = run_harness(f1b, f2, integ, audit)
    problems = judge_harness(result)
    check(not problems, f"1B.2/1B.3 served scripts in node {problems if problems else ''}".rstrip())
    print("F1B_HARNESS_EVIDENCE=" + json.dumps({k: result.get(k) for k in ("single", "polling", "stale", "partial", "failure", "status_errors", "closed", "integrity_ok", "integrity_fail")}, ensure_ascii=False)[:2500], flush=True)
    print("F1B_AUDIT_EVIDENCE=" + json.dumps(result.get("audit"), ensure_ascii=False)[:900], flush=True)

    # ------------------------------------------------------------ positive controls
    loader_anchor = "l.on('mouseover',()=>{"
    html_mutants = [
        ("1B.1 tooltip", rule_no_map_tooltip, mutate(html, loader_anchor, "l.bindTooltip(`${p.municipality} · ${p.area_ha} ha`,{sticky:true});" + loader_anchor, "tooltip")),
        ("1B.2 legacy progressive wins", rule_reading_visible, mutate(html, "window.rxProgressiveAnalyze=fromButton;", "", "prog")),
        ("1B.2 hidden section", rule_reading_visible, html.replace("</body>", "<style>.rx45-panel-card [data-rx-full-slot]{display:none!important}</style></body>")),
        ("1B.2 #pbody writer", rule_reading_visible, mutate(html, "function slot(c){", "function slot(c){const legacy=document.querySelector('#pbody');", "pbody")),
        ("1B.2 risk label", rule_no_internal_text, mutate(html, '<div class="rx45-actions">', '<div class="rx45-risk"><strong>RISCO NÃO CLASSIFICADO</strong></div><div class="rx45-actions">', "risk")),
        ("1B.2 counter", rule_no_internal_text, mutate(html, "text=''}node.childNodes", "text=`${responded} de ${a.total} fontes responderam nesta consulta · `}node.childNodes", "counter")),
        ("1B.3 integrity timers", rule_dedup_static, mutate(html, "function schedule(car){ensure(car);setTimeout(()=>ensure(car),250)}", "function schedule(car){[90,720,2300,9800].forEach(ms=>setTimeout(()=>fetch(`/v1/live/car-integrity/${encodeURIComponent(car)}`),ms))}", "timers")),
        ("1B.3 plain map-panel fetch", rule_dedup_static, mutate(html, "busy=true;try{const {ok:rOk,d}=await window.rxMapPanelOnce(car);", "busy=true;try{const r0=await fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`),rOk=r0.ok,d=await r0.json();", "mappanel")),
        ("1B.3 not sticky", rule_actions_on_screen, mutate(html, ".rx45-actions{position:sticky;bottom:0;", ".rx45-actions{", "sticky")),
        ("1B.3 100vh cap", rule_actions_on_screen, mutate(html, "max-height:calc(100% - 28px)!important", "max-height:calc(100vh - 28px)!important", "vh")),
        ("1B.3 small PDF link", rule_actions_on_screen, mutate(html, '<button class="rx45-pdf" id="rx45Pdf" type="button">GERAR PDF</button>', '<button class="rx45-pdf-link" id="rx45Pdf" type="button">gerar PDF</button>', "pdf")),
    ]
    # Each mutant must fail for ITS reason (the expected problem text), not for any problem at all.
    reason = {
        "1B.1 tooltip": "parcel tooltip bound", "1B.2 legacy progressive wins": "last window.rxProgressiveAnalyze",
        "1B.2 hidden section": "CSS hides the reading", "1B.2 #pbody writer": "hidden legacy dossier: pbody",
        "1B.2 risk label": "v45_panel: RISCO", "1B.2 counter": "mte: fontes responderam",
        "1B.3 integrity timers": "integrity refetch timers", "1B.3 plain map-panel fetch": "asks /map-panel on its own",
        "1B.3 not sticky": "not sticky", "1B.3 100vh cap": "capped by the viewport", "1B.3 small PDF link": "PDF is not a 44 px button",
        "single-flight broken": "single-flight broken", "unbounded automatic retry": "worker failure not exactly one automatic retry",
        "answer for another CAR shown": "another CAR", "unbounded polling on status errors": "status errors not bounded",
        "ok===true not required": "source without ok===true answered", "capped zero answered": "capped zero",
        "reading into #pbody": "reading wrote into #pbody", "polling after the panel closed": "closed panel kept polling",
        "integrity re-asks on every render": "integrity asked",
        "previous run accepted on the first answer": "previous run shown as current",
        "PRODES years cut to the last four": "PRODES years cut",
        "incomplete PRODES as the total": "incomplete PRODES shown as the total",
        "arrival time as the time of the data (poll)": "time of the data wrong after the cache expired",
        "arrival time as the time of the data (cache)": "cache answer shown with the answer time",
        "fire without the bulletin time": "fire answered without the time of the bulletin",
        "no automatic retry of pending rows": "pending rows not retried once by themselves",
        "merge overwrites answers": "merge replaced an answer by a later failure",
        "automatic retry for a closed panel": "automatic retry for a closed panel",
        "failure turns the main button into a retry": "main button stays VER ANÁLISE COMPLETA",
        "loading renames the main button": "main button renamed while loading",
        "audit lists unasked sources": "audit lists sources nobody asked",
        "audit ignores the full analysis": "audit contradicts the full analysis",
    }
    for label, rule, mutant in html_mutants:
        got = rule(mutant)
        check(any(reason[label] in x for x in got), f"positive control catches: {label} (for its reason: {reason[label]!r}; got {got[:2]})")

    js_mutants = [
        ("single-flight broken", "f1b", "if(opts.force||!usable(e)){", "if(true){"),
        ("unbounded automatic retry", "f1b", "for(let i=0;i<2&&!res;i++){", "for(let i=0;i<5&&!res;i++){"),
        ("answer for another CAR shown", "f1b", "if(done)return sameCar(done,car)?{analysis:done,at:producedAt(ds,done,d.analysis)}:null}", "if(done)return {analysis:done,at:producedAt(ds,done,d.analysis)}}"),
        ("previous run accepted on the first answer", "f1b", "if(d.mode==='quick-cache'){const done=", "if(d.mode==='quick-cache'||ds.state==='ready'){const done="),
        ("PRODES years cut to the last four", "f1b", "return ys.length<=5?`anos PRODES ${yearList(ys)}`:`${ys.length} anos PRODES entre ${ys[0]} e ${ys[ys.length-1]}`", "return `anos PRODES ${yearList(ys.slice(-4))}`"),
        ("incomplete PRODES as the total", "f1b", "post=pr.post_cutoff_inside||{},full=pr.complete===true;", "post=pr.post_cutoff_inside||{},full=pr.state==='found'||pr.complete===true;"),
        ("arrival time as the time of the data (poll)", "f1b", "return {analysis:st.d.analysis,at:t!==null?t:(running?Date.now():null)}", "return {analysis:st.d.analysis,at:Date.now()}"),
        ("arrival time as the time of the data (cache)", "f1b", "{analysis:done,at:producedAt(ds,done,d.analysis)}:null}", "{analysis:done,at:Date.now()}:null}"),
        ("fire without the bulletin time", "f1b", "if(f.ok===true&&isNum(n)&&n>=0&&b!==null){", "if(f.ok===true&&isNum(n)&&n>=0){"),
        ("no automatic retry of pending rows", "f1b", "if(mine.phase==='ready')fill(car,mine,false)", "if(false)fill(car,mine,false)"),
        ("merge overwrites answers", "f1b", "if(r.answer===PEND&&n&&n.answer!==PEND){changed=true;return n}", "if(n){changed=true;return n}"),
        ("automatic retry for a closed panel", "f1b", "if(!card(car)){mine.fill={state:'idle'};return}", ""),
        ("failure turns the main button into a retry", "f1b", "b.textContent='VER ANÁLISE COMPLETA';", "b.textContent=e.phase==='failed'?'CONSULTAR DE NOVO':'VER ANÁLISE COMPLETA';"),
        ("loading renames the main button", "f1b", "b.textContent='VER ANÁLISE COMPLETA';", "b.textContent=e.phase==='loading'?'CONSULTANDO FONTES…':'VER ANÁLISE COMPLETA';"),
        ("audit lists unasked sources", "audit", "if(!row||!row.dataset.state)return;", "if(!row||!row.dataset.state){items.push(auditItem(source.label,'NÃO CONSULTADA','Estado desta fonte nesta consulta.'));return}"),
        ("audit ignores the full analysis", "audit", "else if(R&&R.phase==='ready'&&Array.isArray(R.rows))", "else if(false)"),
        ("unbounded polling on status errors", "f1b", "if(misses>=3)return null;", ""),
        ("ok===true not required", "f1b", "if(obj.ok===true&&ex.available===true&&", "if(obj.ok!==false&&ex.available!==false&&"),
        ("capped zero answered", "f1b", "&&!(capped&&n===0)", ""),
        ("reading into #pbody", "f1b", "const s=slot(c);s.dataset.phase", "const s=document.querySelector('#pbody');s.dataset.phase"),
        ("polling after the panel closed", "f1b", "const alive=()=>memo.get(car)===mine&&!!card(car);", "const alive=()=>memo.get(car)===mine;"),
        ("integrity re-asks on every render", "f2", "if(!usable(e)){", "if(true){"),
    ]
    for label, which, old, new in js_mutants:
        parts = {"f1b": f1b, "f2": f2, "integrity": integ, "audit": audit}
        parts[which] = mutate(parts[which], old, new, label)
        mres = run_harness(parts["f1b"], parts["f2"], parts["integrity"], parts["audit"])
        got = judge_harness(mres)
        check(any(reason[label] in x for x in got), f"positive control catches: {label} (for its reason: {reason[label]!r}; got {got[:2]})")

    # ------------------------------------------------------------ the report engine never hands out the previous run as current
    # (own process: the engine modules with the analysis replaced by a counter and a 1 s cache; its positive controls inside)
    env = dict(os.environ, RX_RELEASE="OFF", PYTHONUTF8="1")
    env.pop("F1B_GATE_CHILD", None)
    motor = subprocess.run([sys.executable, str(ROOT / "scripts" / "f1b_motor_estado_check.py")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, env=env, cwd=str(ROOT))
    lines = [x for x in (motor.stdout or "").splitlines() if x.startswith(("PASS ", "FAIL ", "F1B_MOTOR"))]
    for line in lines:
        print("  engine: " + line[:400], flush=True)
    check(motor.returncode == 0 and any(x == "F1B_MOTOR_ESTADO=PASS" for x in lines),
          "1B.2 engine state after the cache expiry is never the previous run; completed_at is the time of the data"
          + ("" if motor.returncode == 0 else f" (exit {motor.returncode}: {(motor.stderr or '')[-300:]})"))

    # ------------------------------------------------------------ part 2: 1B.6, 1B.7, 1B.8
    try:
        import f1b_parte2_checks

        f1b_parte2_checks.run_all(html, check)
    except Exception as exc:  # a part-2 rule that cannot run is a failure, never a skip
        check(False, f"F1B part 2 checks could not run: {type(exc).__name__}: {exc}")

    print("F1B_TELA_GATE=" + ("PASS" if not FAILURES else "FAIL " + json.dumps(FAILURES, ensure_ascii=False)), flush=True)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

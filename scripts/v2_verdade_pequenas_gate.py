"""V2 gate: two small truths the client reads.

Usage:
  PYTHONPATH=. python scripts/v2_verdade_pequenas_gate.py        (needs Node for the screen rule)

1. Hora do boletim do INPE. INPE names each 10-minute fire file by its time in UTC
   (focos_10min_YYYYMMDD_HHMM.csv). The PDF printed that hour as it is, three hours
   ahead of Brasília; the screen converted with the device clock, so a device (or a
   server-side browser) outside Brasília showed another hour than the PDF. Both now
   show Brasília time (fixed UTC-3: Brazil has had no daylight saving time since 2019).

2. Falha do SICAR não é "CAR não localizado". car_resilient answered not_found=True
   after every strategy failed in transport, and every route turned that into 404.
   Absence now needs SICAR to have answered an exact query with a FeatureCollection;
   a lookup that never got an answer is a pending consultation: 503 + Retry-After.
   The portal search (loadCar) threw the JSON detail, which the page printed raw as
   "A ação falhou: {...attempts...}"; it now shows "Consulta ao SICAR pendente" or,
   only for SICAR's own answer, "CAR não localizado no SICAR."

Offline: SICAR is replaced by fakes (failure, empty answer, service exception, hit).

Positive control: every rule runs again against a copy of the module with the fix
reverted by one string replacement, and must fail there.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"


def load(name: str, mutations: list[tuple[str, str]] | None = None, *, as_main: bool = False) -> types.ModuleType:
    """Import ROOT/<name>.py under a private name, optionally with the fix reverted."""
    src = (ROOT / f"{name}.py").read_text(encoding="utf-8")
    for old, new in mutations or []:
        assert src.count(old) == 1, f"mutation anchor for {name} must appear exactly once: {old[:80]!r}"
        src = src.replace(old, new)
    mod = types.ModuleType(f"{name}_v2gate")
    mod.__file__ = str(ROOT / f"{name}.py")
    if as_main:
        # car_resilient patches report_api/portal_api at import unless run as __main__.
        mod.__name__ = "__main__"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------- 1. hora do boletim

BULLETIN_PDF = {
    "Último arquivo processado: focos_10min_20260913_1820.csv.": "Último arquivo processado: arquivo de 10 minutos de 13/09/2026 15:20.",
    # 01:20 UTC is still the previous day in Brasília.
    "arquivo: focos_10min_20260914_0120.csv": "arquivo: arquivo de 10 minutos de 13/09/2026 22:20",
    # Not a real time: left as it is, never invented.
    "focos_10min_20260914_2590.csv": "focos_10min_20260914_2590.csv",
}


def rule_bulletin_pdf(mod) -> list[str]:
    problems = []
    for raw, expected in BULLETIN_PDF.items():
        got = mod.normalize_text(raw)
        if got != expected:
            problems.append(f"normalize_text({raw!r}) = {got!r}, expected {expected!r}")
    return problems


PDF_MUTATION = [("datetime(y, mo, d, hh, mi, tzinfo=timezone.utc).astimezone(BRT)", "datetime(y, mo, d, hh, mi, tzinfo=timezone.utc)")]


def _f1b_script(src: str) -> str:
    parts = []
    for pattern in (r"^ const isNum=.*;$", r"^ const when=at=>.*;$", r"^ function bulletin\(name\)\{.*\}$"):
        m = re.search(pattern, src, re.M)
        if not m:
            raise AssertionError(f"f1b helper not found: {pattern}")
        parts.append(m.group(0))
    return "\n".join(parts)


def rule_bulletin_screen(src: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        return ["Node is required for the screen rule"]
    js = _f1b_script(src) + """
const cases={'focos_10min_20260914_2320.csv':'14/09/2026, 20:20','focos_10min_20260914_0120.csv':'13/09/2026, 22:20'};
const out={};for(const k in cases)out[k]=when(bulletin(k));
process.stdout.write(JSON.stringify({out,cases}));
"""
    problems = []
    # A device (or a server-side browser) outside Brasília must still read Brasília time.
    for tz in ("UTC", "America/Manaus"):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "when.js"
            path.write_text(js, encoding="utf-8")
            run = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60, env=dict(os.environ, TZ=tz))
        if run.returncode:
            problems.append(f"node failed ({tz}): {run.stderr[:300]}")
            continue
        data = json.loads(run.stdout)
        for name, expected in data["cases"].items():
            if data["out"][name] != expected:
                problems.append(f"TZ={tz}: bulletin {name} shown as {data['out'][name]!r}, expected {expected!r} (Brasília)")
    return problems


SCREEN_MUTATION = [("try{return f({timeZone:'America/Sao_Paulo'})}", "try{return f({})}")]


# ---------------------------------------------------------------- 2. SICAR: falha x ausência

TIMEOUT = {"ok": False, "timed_out": True, "detail": "process_timeout_after_11s", "bytes": 0}
EMPTY = {"ok": True, "bytes": 52, "json": {"type": "FeatureCollection", "features": []}}
SERVICE_EXCEPTION = {"ok": True, "bytes": 96, "json": {"exceptions": [{"code": "NoApplicableCode", "text": "java.lang.OutOfMemoryError"}]}}
HIT = {"ok": True, "bytes": 300, "json": {"type": "FeatureCollection", "features": [
    {"id": "sicar_imoveis_mg.1", "properties": {"cod_imovel": CAR, "municipio": "Curvelo", "uf": "MG"},
     "geometry": {"type": "Polygon", "coordinates": [[[-44.4, -18.7], [-44.39, -18.7], [-44.39, -18.69], [-44.4, -18.7]]]}}]}}


def _fake_req(plan):
    calls = []

    def req(params, cancel_event=None):
        calls.append(params)
        municipality_scan = "propertyName" in params
        answer = plan(len(calls), municipality_scan)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return req, calls


def rule_sicar_lookup(mod) -> list[str]:
    problems = []

    def run(label, plan):
        req, calls = _fake_req(plan)
        mod._req = req
        out = mod.fetch_car_live_resilient(CAR)
        return out, calls

    out, calls = run("all transport failures", lambda n, scan: TIMEOUT)
    if out.get("ok") or out.get("not_found") is True:
        problems.append(f"every SICAR attempt timed out and the lookup says not_found: {out.get('not_found')!r} {out.get('detail')!r}")
    if "pendente" not in str(out.get("detail") or "").lower():
        problems.append(f"a failed lookup must read as a pending consultation, got {out.get('detail')!r}")
    if not calls:
        problems.append("the fake SICAR was never asked")

    out, _ = run("raised exceptions", lambda n, scan: OSError("connection reset"))
    if out.get("not_found") is True:
        problems.append("exceptions on every attempt became not_found")

    out, _ = run("service exception JSON", lambda n, scan: SERVICE_EXCEPTION)
    if out.get("not_found") is True:
        problems.append("a GeoServer exception document (JSON without features) became not_found")

    out, _ = run("only the municipality scan answered", lambda n, scan: EMPTY if scan else TIMEOUT)
    if out.get("not_found") is True:
        problems.append("the municipality scan alone (it can stop early) proved absence")

    out, _ = run("exact query answered empty", lambda n, scan: EMPTY)
    if out.get("not_found") is not True or out.get("ok"):
        problems.append(f"SICAR answered the exact query with no feature: must be not_found, got {out!r}")

    out, _ = run("first attempt timed out, second answered empty", lambda n, scan: TIMEOUT if n == 1 else EMPTY)
    if out.get("not_found") is not True:
        problems.append("an exact answer after a failed attempt is still an answer (not_found expected)")

    out, _ = run("hit", lambda n, scan: HIT)
    if not out.get("ok"):
        problems.append(f"a SICAR hit must resolve: {out!r}")

    invalid = mod.fetch_car_live_resilient("MG-123")
    if invalid.get("not_found") is not True:
        problems.append("an impossible CAR format is not_found without asking SICAR")
    return problems


SICAR_MUTATION = [("'ok':False,'source':'SICAR','not_found':answered,", "'ok':False,'source':'SICAR','not_found':True,")]


def rule_http_error(helper) -> list[str]:
    problems = []
    pending = helper.lookup_http_error({"ok": False, "not_found": False, "detail": "x"})
    if pending.status_code not in (502, 503):
        problems.append(f"SICAR failure must be 502/503, got {pending.status_code}")
    if pending.status_code == 503 and not (pending.headers or {}).get("Retry-After"):
        problems.append("503 without Retry-After")
    if "pendente" not in str(pending.detail).lower() or "não localizado" in str(pending.detail).lower():
        problems.append(f"failure detail must say pending, never not located: {pending.detail!r}")
    absent = helper.lookup_http_error({"ok": False, "not_found": True})
    if absent.status_code != 404 or "localizado" not in str(absent.detail):
        problems.append(f"SICAR answered without the property: 404 'não localizado' expected, got {absent.status_code} {absent.detail!r}")
    return problems


HTTP_MUTATION = [("if car.get('not_found') is True:", "if car.get('not_found') is not None:")]


def rule_report_api(mod) -> list[str]:
    from fastapi import HTTPException

    problems = []

    async def call(car):
        async def analyze_car(code):
            return {"car": car}

        mod.analyze_car = analyze_car
        try:
            await mod._analyze_uncached(CAR)
        except HTTPException as exc:
            return exc
        return None

    exc = asyncio.run(call({"ok": False, "source": "SICAR", "not_found": False, "detail": "x", "attempts": [{"strategy": "wfs1_equal", "ok": False}]}))
    if exc is None or exc.status_code != 503 or not (exc.headers or {}).get("Retry-After"):
        problems.append(f"report_api: SICAR failure must be 503 with Retry-After, got {getattr(exc, 'status_code', None)} {getattr(exc, 'headers', None)}")
    elif "pendente" not in str(exc.detail).lower():
        problems.append(f"report_api: failure detail must say pending: {exc.detail!r}")
    exc = asyncio.run(call({"ok": False, "source": "SICAR", "not_found": True, "detail": "x"}))
    if exc is None or exc.status_code != 404:
        problems.append(f"report_api: SICAR answered without the property must be 404, got {getattr(exc, 'status_code', None)}")
    return problems


REPORT_API_MUTATION = [("raise lookup_http_error(car)\n    result=await _retry_failed_core(result)",
                        "raise HTTPException(status_code=404 if car.get('not_found') else 502,detail=_safe_summary(result))\n    result=await _retry_failed_core(result)")]


# Routes of the report service and the portal lookup that answer a failed SICAR lookup.
ROUTE_FILES = ("report_api.py", "report_quick_v22.py", "heavy_live_api_v20.py", "portal_car_resilient.py")
OLD_EXPRESSION = re.compile(r"status_code=404 if car\.get\('not_found'\) else 502")


def rule_portal_search(src: str) -> list[str]:
    """The portal search (loadCar) with SICAR failing, absent, or a 404 that is not SICAR's answer."""
    node = shutil.which("node")
    if not node:
        return ["Node is required for the portal search rule"]
    m = re.search(r"^async function loadCar\(code\)\{.*$", src, re.M)
    if not m:
        return ["portal_api.py: loadCar not found"]
    js = m.group(0) + """
const cases=[
 ['sicar_failed',503,{detail:{car:{ok:false,source:'SICAR',not_found:false,detail:'Consulta ao SICAR pendente',attempts:[{strategy:'wfs1_equal',ok:false,detail:'curl: (28) Operation timed out'}]}}},'pending'],
 ['sicar_answered_absent',404,{detail:{car:{ok:false,source:'SICAR',not_found:true,detail:'CAR não localizado',attempts:[{strategy:'wfs1_equal',ok:true}]}}},'absent'],
 ['404_not_from_sicar',404,{detail:'Not Found'},'pending'],
 ['proxy_html_502',502,null,'pending'],
];
(async()=>{const out={};for(const [name,status,body,want] of cases){const shown=[];let rejected=null;
 globalThis.toast=t=>shown.push(String(t));globalThis.showProperty=()=>shown.push('SHOWN');
 globalThis.fetch=async()=>({ok:false,status,json:async()=>{if(body===null)throw new SyntaxError('Unexpected token <');return body}});
 try{await loadCar('MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F')}catch(e){rejected=String(e&&e.message||e)}
 out[name]={want,shown,rejected}}
 process.stdout.write(JSON.stringify(out))})();
"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "loadcar.js"
        path.write_text(js, encoding="utf-8")
        run = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if run.returncode:
        return [f"node failed: {run.stderr[:300]}"]
    problems = []
    for name, r in json.loads(run.stdout).items():
        last = r["shown"][-1] if r["shown"] else ""
        if r["rejected"] is not None:
            problems.append(f"{name}: loadCar rejects ({r['rejected'][:120]!r}); the page shows it raw as 'A ação falhou'")
        if "{" in last or "attempts" in last:
            problems.append(f"{name}: technical text shown to the client: {last[:120]!r}")
        if r["want"] == "pending" and ("não localizado" in last.lower() or "pendente" not in last.lower()):
            problems.append(f"{name}: a lookup SICAR did not answer must read as pending, shown {last[:120]!r}")
        if r["want"] == "absent" and "não localizado" not in last.lower():
            problems.append(f"{name}: SICAR answered without the property: expected 'não localizado', shown {last[:120]!r}")
    return problems


PORTAL_SEARCH_OLD = "let d=await r.json();if(!r.ok)throw new Error((d.detail&&JSON.stringify(d.detail))||'CAR não localizado');"


def rule_routes_static(sources: dict[str, str]) -> list[str]:
    problems = []
    for name in ROUTE_FILES:
        src = sources.get(name, "")
        if OLD_EXPRESSION.search(src):
            problems.append(f"{name}: still turns any lookup failure into 404/502 by itself")
        if "lookup_http_error(" not in src:
            problems.append(f"{name}: does not use sicar_lookup_http.lookup_http_error")
    pdf = sources.get("report_pdf_cache_v21.py", "")
    if pdf and pdf.count("except HTTPException:raise") < 2:
        problems.append("report_pdf_cache_v21.py: the PDF routes hide a 404/503 behind 'Falha ao gerar relatório'")
    return problems


def main() -> int:
    failures = 0

    def check(label, problems):
        nonlocal failures
        if problems:
            failures += 1
            print(f"FAIL {label}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"PASS {label}")

    def control(label, problems):
        nonlocal failures
        if problems:
            print(f"PASS control ({label}): the reverted fix is caught ({len(problems)} problem(s))")
        else:
            failures += 1
            print(f"FAIL control ({label}): the rule does not see the old defect")

    f1b_src = (ROOT / "portal_full_reading_f1b.py").read_text(encoding="utf-8")
    route_sources = {name: (ROOT / name).read_text(encoding="utf-8") for name in ROUTE_FILES + ("report_pdf_cache_v21.py",)}

    check("1 PDF: INPE bulletin hour in Brasília", rule_bulletin_pdf(load("report_ptbr_v50")))
    control("PDF prints the UTC hour", rule_bulletin_pdf(load("report_ptbr_v50", PDF_MUTATION)))
    check("1 screen: INPE bulletin hour in Brasília on any device clock", rule_bulletin_screen(f1b_src))
    mutated = f1b_src
    for old, new in SCREEN_MUTATION:
        assert mutated.count(old) == 1, old
        mutated = mutated.replace(old, new)
    control("screen follows the device clock", rule_bulletin_screen(mutated))

    check("2 SICAR lookup: failure is not absence", rule_sicar_lookup(load("car_resilient", as_main=True)))
    control("lookup says not_found after failures", rule_sicar_lookup(load("car_resilient", SICAR_MUTATION, as_main=True)))
    check("2 HTTP: 404 only for an answer, 503 + Retry-After for a failure", rule_http_error(load("sicar_lookup_http")))
    control("helper returns 404 for a failure", rule_http_error(load("sicar_lookup_http", HTTP_MUTATION)))
    check("2 report_api: analysis route", rule_report_api(load("report_api")))
    control("report_api old 404/502 expression", rule_report_api(load("report_api", REPORT_API_MUTATION)))
    check("2 routes use the helper (report service, portal lookup, PDF routes)", rule_routes_static(route_sources))
    reverted = dict(route_sources)
    reverted["report_quick_v22.py"] = reverted["report_quick_v22.py"].replace(
        "raise lookup_http_error(car)", "raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR temporariamente indisponível.')")
    reverted["report_pdf_cache_v21.py"] = reverted["report_pdf_cache_v21.py"].replace("except HTTPException:raise\n", "")
    control("quick route and PDF routes reverted", rule_routes_static(reverted))
    portal_src = (ROOT / "portal_api.py").read_text(encoding="utf-8")
    check("2 portal search: pending or not located, never raw JSON", rule_portal_search(portal_src))
    anchor = re.search(r"let d=await r\.json\(\)\.catch\(\(\)=>null\);if\(!r\.ok\)\{.*?return false\}", portal_src)
    assert anchor, "portal search anchor for the positive control"
    control("portal search throws the JSON detail", rule_portal_search(portal_src.replace(anchor.group(0), PORTAL_SEARCH_OLD)))

    print(f"RX_V2_VERDADE_PEQUENAS_GATE={'PASS' if not failures else 'FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

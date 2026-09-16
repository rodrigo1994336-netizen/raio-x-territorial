"""V2 gate: small truths the client reads.

Usage:
  PYTHONPATH=. python scripts/v2_verdade_pequenas_gate.py        (needs Node)

1. Hora em Brasília, dita uma vez. INPE names each 10-minute fire file by its time in UTC
   (focos_10min_YYYYMMDD_HHMM.csv). PDF and screen show Brasília time from the tz database
   (America/Sao_Paulo), whatever the device clock, and say "(horário de Brasília)" once per
   block: MT, MS, RO and AM run one hour behind it, AC two. The next-retry clock on the
   screen uses the same zone. In a daylight-saving date (jan/2018) PDF and screen agree.

2. Falha do SICAR nunca vira imóvel inexistente. Absence needs SICAR to answer an exact query
   with NO feature. A timeout, an exception document, features of another property (filter
   ignored), a malformed item or a layer answering empty to the whole municipality keep the
   lookup pending: 503 + Retry-After, "Consulta ao SICAR pendente". An impossible code is 422.
   Checked on the real compositions (sitecustomize boot + TestClient, SICAR replaced by a
   fake): report service (/meta, PDF, prepare -> status, quick) and portal (/v1/live/car body
   read by the link, exports, tabs, PDF viewer page run in node, link reader, F1B reading).

Positive control: every rule runs again with its fix reverted (a string replacement in the
module source, applied at import time in a fresh process for the booted chains, or in the
extracted script) and must fail there. A reverted fix that still passes fails the gate.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
PORTAL_RELEASE = "V8_OPERATIONAL_ZERO_COST"
NOTE = "(horário de Brasília)"
PENDING = "Consulta ao SICAR pendente: o SICAR não respondeu agora. Tente de novo em instantes."
NOT_FOUND = "CAR não localizado no SICAR."
INVALID = "Código CAR inválido."
CODE_RX = re.compile(r"[A-Z]{2}-\d{7}-[0-9A-F]{32}")


def gate_code(n: int, kind: str) -> str:
    """A distinct valid CAR per case; the last hex digit tells the fake SICAR how to answer."""
    return f"MG-3120904-{n:031X}{ {'fail': 'A', 'other': 'B', 'empty': 'E'}[kind] }"


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


def node_json(js: str, env: dict | None = None) -> tuple[dict | None, str]:
    node = shutil.which("node")
    if not node:
        return None, "Node is required"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "rule.js"
        path.write_text(js, encoding="utf-8")
        run = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120, env=dict(os.environ, **(env or {})))
    if run.returncode:
        return None, f"node failed: {run.stderr[-400:]}"
    try:
        return json.loads(run.stdout), ""
    except Exception:
        return None, f"node output is not JSON: {run.stdout[-300:]}"


# ================================================================ 1. hora em Brasília

BULLETIN_PDF = {
    "Último arquivo processado: focos_10min_20260913_1820.csv.": f"Último arquivo processado: arquivo de 10 minutos de 13/09/2026 15:20 {NOTE}.",
    # 01:20 UTC is still the previous day in Brasília.
    "arquivo: focos_10min_20260914_0120.csv": f"arquivo: arquivo de 10 minutos de 13/09/2026 22:20 {NOTE}",
    # Not a real time: left as it is, never invented, no note.
    "focos_10min_20260914_2590.csv": "focos_10min_20260914_2590.csv",
    "Gerado em: 2026-09-13T21:27:57.820727+00:00": f"Gerado em: 13/09/2026 18:27 {NOTE}",
    # Once per block: the second time of the same paragraph is not labelled again.
    "Último arquivo processado: focos_10min_20260913_1820.csv. Consulta: 2026-09-13T21:27:57Z":
        f"Último arquivo processado: arquivo de 10 minutos de 13/09/2026 15:20 {NOTE}. Consulta: 13/09/2026 18:27",
    # Daylight saving time of 2017/2018: Brasília was UTC-2 (tz database, not a fixed UTC-3).
    "focos_10min_20180115_1200.csv": f"arquivo de 10 minutos de 15/01/2018 10:00 {NOTE}",
}


def rule_bulletin_pdf(mod) -> list[str]:
    problems = []
    for raw, expected in BULLETIN_PDF.items():
        got = mod.normalize_text(raw)
        if got != expected:
            problems.append(f"normalize_text({raw!r}) = {got!r}, expected {expected!r}")
        again = mod.normalize_text(got)
        if again != got:
            problems.append(f"normalizing twice changes the text (note repeated?): {again!r}")
    return problems


PDF_MUTATIONS = {
    "PDF prints the UTC hour": [("datetime(y, mo, d, hh, mi, tzinfo=timezone.utc).astimezone(BRT)", "datetime(y, mo, d, hh, mi, tzinfo=timezone.utc)")],
    "PDF without the Brasília note": [('    zone["noted"] = True\n    return text + BRT_NOTE', '    zone["noted"] = True\n    return text')],
    "PDF note on every time": [('    if zone is None or zone.get("noted") or text == m.group(0):', '    if zone is None or text == m.group(0):')],
    "PDF with a fixed UTC-3 instead of the tz database": [('    BRT = ZoneInfo("America/Sao_Paulo")', "    BRT = timezone(timedelta(hours=-3))")],
}


def _f1b_helpers(src: str) -> str:
    parts = []
    for pattern in (r"^ const isNum=.*;$", r"^ const TZ=.*;$", r"^ const when=at=>.*;$", r"^ const clock=at=>.*;$", r"^ function bulletin\(name\)\{.*\}$"):
        m = re.search(pattern, src, re.M)
        if not m:
            raise AssertionError(f"f1b helper not found: {pattern}")
        parts.append(m.group(0))
    return "\n".join(parts)


def rule_bulletin_screen(src: str, pdf_mod) -> list[str]:
    # The PDF hour of a daylight-saving instant is the reference the screen must match.
    m = re.search(r"(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})", pdf_mod.normalize_text("focos_10min_20180115_1200.csv"))
    dst = f"{m.group(1)}, {m.group(2)}" if m else "PDF gave no time"
    js = _f1b_helpers(src) + """
const out={
 late:when(bulletin('focos_10min_20260914_2320.csv'))+BRT_NOTE,
 early:when(bulletin('focos_10min_20260914_0120.csv')),
 retry:clock(Date.UTC(2026,8,14,23,25))+BRT_NOTE,
 dst:when(bulletin('focos_10min_20180115_1200.csv')),
};
process.stdout.write(JSON.stringify(out));
"""
    expected = {"late": f"14/09/2026, 20:20 {NOTE}", "early": "13/09/2026, 22:20", "retry": f"20:25 {NOTE}", "dst": dst}
    problems = []
    # A device (or a server-side browser) outside Brasília must still read Brasília time.
    for tz in ("UTC", "America/Manaus"):
        out, err = node_json(js, {"TZ": tz})
        if out is None:
            problems.append(f"TZ={tz}: {err}")
            continue
        for key, want in expected.items():
            if out.get(key) != want:
                problems.append(f"TZ={tz}: {key} shown as {out.get(key)!r}, expected {want!r} (Brasília, same as the PDF)")
    return problems


SCREEN_MUTATIONS = {
    "screen follows the device clock": [("return {timeZone:'America/Sao_Paulo'}}catch(e){return null}", "return {}}catch(e){return null}")],
    "retry clock in device time": [("toLocaleTimeString('pt-BR',{...(TZ||{}),hour:'2-digit',minute:'2-digit'})}catch(e){return ''}};", "toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}catch(e){return ''}};")],
    "screen without the note": [("BRT_NOTE=TZ?' (horário de Brasília)':''", "BRT_NOTE=''")],
}


def mutate_text(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        assert text.count(old) == 1, f"mutation anchor must appear exactly once: {old[:90]!r} ({text.count(old)})"
        text = text.replace(old, new)
    return text


# ================================================================ 2. SICAR: falha x ausência

TIMEOUT = {"ok": False, "timed_out": True, "detail": "process_timeout_after_11s", "bytes": 0}
EMPTY = {"ok": True, "bytes": 52, "json": {"type": "FeatureCollection", "features": []}}
SERVICE_EXCEPTION = {"ok": True, "bytes": 96, "json": {"exceptions": [{"code": "NoApplicableCode", "text": "java.lang.OutOfMemoryError"}]}}
OTHER_CODE = "MG-3120904-0123456789ABCDEF0123456789ABCDEF"
POLY = {"type": "Polygon", "coordinates": [[[-44.4, -18.7], [-44.39, -18.7], [-44.39, -18.69], [-44.4, -18.7]]]}
OTHER_PROPERTY = {"ok": True, "bytes": 300, "json": {"type": "FeatureCollection", "features": [
    {"id": "sicar_imoveis_mg.9", "properties": {"cod_imovel": OTHER_CODE, "municipio": "Curvelo", "uf": "MG"}, "geometry": POLY}]}}
MALFORMED = {"ok": True, "bytes": 20, "json": {"type": "FeatureCollection", "features": ["x"]}}
SCAN_ALIVE = {"ok": True, "bytes": 120, "json": {"type": "FeatureCollection", "features": [{"id": "sicar_imoveis_mg.9", "properties": {"cod_imovel": OTHER_CODE}}]}}


def hit(code: str) -> dict:
    return {"ok": True, "bytes": 300, "json": {"type": "FeatureCollection", "features": [
        {"id": "sicar_imoveis_mg.1", "properties": {"cod_imovel": code, "municipio": "Curvelo", "uf": "MG"}, "geometry": POLY}]}}


def rule_sicar_lookup(mod) -> list[str]:
    problems = []

    def run(plan, code=CAR):
        calls = []

        def req(params, cancel_event=None):
            calls.append(params)
            answer = plan(len(calls), "propertyName" in params)
            if isinstance(answer, Exception):
                raise answer
            return answer

        mod._req = req
        return mod.fetch_car_live_resilient(code), calls

    def pending(label, plan):
        out, calls = run(plan)
        if not calls:
            problems.append(f"{label}: the fake SICAR was never asked")
        if out.get("ok") or out.get("not_found") is True:
            problems.append(f"{label}: must stay pending, got not_found={out.get('not_found')!r} ok={out.get('ok')!r}")
        elif "pendente" not in str(out.get("detail") or "").lower():
            problems.append(f"{label}: a pending lookup must say so, got {out.get('detail')!r}")

    pending("every SICAR attempt timed out", lambda n, scan: TIMEOUT)
    pending("exceptions on every attempt", lambda n, scan: OSError("connection reset"))
    pending("GeoServer exception document (JSON without features)", lambda n, scan: SERVICE_EXCEPTION)
    pending("only the municipality scan answered (it can stop early)", lambda n, scan: EMPTY if scan else TIMEOUT)
    pending("filter ignored: the first exact query returns another property, the rest time out", lambda n, scan: OTHER_PROPERTY if n == 1 else TIMEOUT)
    pending("filter ignored on every exact query", lambda n, scan: SCAN_ALIVE if scan else OTHER_PROPERTY)
    pending("another property, then empty answers", lambda n, scan: OTHER_PROPERTY if n == 1 else (SCAN_ALIVE if scan else EMPTY))
    pending("malformed item in the answer", lambda n, scan: SCAN_ALIVE if scan else MALFORMED)
    pending("layer answers empty to everything (the municipality too)", lambda n, scan: EMPTY)

    out, _ = run(lambda n, scan: SCAN_ALIVE if scan else EMPTY)
    if out.get("not_found") is not True or out.get("ok"):
        problems.append(f"SICAR answered the exact query with no feature (layer alive): must be not_found, got {out.get('not_found')!r} {out.get('detail')!r}")
    exact = [a for a in out.get("attempts") or [] if str(a.get("strategy", "")).startswith("wfs")]
    if not exact or any(a.get("features") != 0 for a in exact):
        problems.append(f"attempts must carry features: 0 for an empty exact answer (the link reader checks it): {exact[:2]}")

    out, _ = run(lambda n, scan: TIMEOUT if n == 1 else (TIMEOUT if scan else EMPTY))
    if out.get("not_found") is not True:
        problems.append("an empty exact answer after a failed attempt is still an answer (not_found expected)")

    out, _ = run(lambda n, scan: hit(CAR))
    if not out.get("ok"):
        problems.append(f"a SICAR hit must resolve: {out.get('detail')!r}")

    invalid = mod.fetch_car_live_resilient("MG-123")
    if invalid.get("detail") != "invalid_car_format":
        problems.append(f"an impossible CAR format is invalid_car_format without asking SICAR: {invalid}")
    return problems


SICAR_ANCHOR = "not_found=answered_empty and not answered_other and not layer_empty"
SICAR_MUTATIONS = {
    "lookup says not_found after failures": [(SICAR_ANCHOR, "not_found=True")],
    "any FeatureCollection proves absence": [(SICAR_ANCHOR, "not_found=answered_empty and not layer_empty"),
                                             ("answered_empty=answered_empty or count==0", "answered_empty=answered_empty or count is not None")],
    "a layer empty for the whole municipality proves absence": [(SICAR_ANCHOR, "not_found=answered_empty and not answered_other")],
}


def rule_http_error(helper) -> list[str]:
    problems = []
    pending = helper.lookup_http_error({"ok": False, "not_found": False, "detail": "x"})
    if pending.status_code != 503 or not (pending.headers or {}).get("Retry-After"):
        problems.append(f"SICAR failure must be 503 + Retry-After, got {pending.status_code} {pending.headers}")
    if pending.detail != PENDING:
        problems.append(f"failure detail must say pending, never not located: {pending.detail!r}")
    absent = helper.lookup_http_error({"ok": False, "not_found": True})
    if absent.status_code != 404 or absent.detail != NOT_FOUND:
        problems.append(f"SICAR answered without the property: 404 {NOT_FOUND!r} expected, got {absent.status_code} {absent.detail!r}")
    invalid = helper.lookup_http_error({"ok": False, "not_found": True, "detail": "invalid_car_format"})
    if invalid.status_code != 422 or invalid.detail != INVALID:
        problems.append(f"an impossible code is not a SICAR answer: 422 {INVALID!r} expected, got {invalid.status_code} {invalid.detail!r}")
    return problems


HTTP_MUTATIONS = {
    "helper returns 404 for a failure": [("if car.get('not_found') is True:", "if car.get('not_found') is not None:")],
    "helper says not located for an impossible code": [("    if car.get('detail') == 'invalid_car_format':\n        return HTTPException(status_code=422, detail=invalid_detail)\n", "")],
}


def rule_deploy_fetch(mod) -> list[str]:
    problems = []

    def run(raw):
        mod._curl = lambda *a, **k: raw
        return mod.fetch_car_live(CAR)

    out = run(SERVICE_EXCEPTION)
    if out.get("ok") or out.get("not_found") is True:
        problems.append(f"deploy_app.fetch_car_live: JSON without features became {out}")
    out = run(OTHER_PROPERTY)
    if out.get("ok") or out.get("not_found") is True:
        problems.append(f"deploy_app.fetch_car_live: another property's feature became {out.get('ok')!r}/{out.get('not_found')!r}")
    out = run(EMPTY)
    if out.get("not_found") is not True:
        problems.append(f"deploy_app.fetch_car_live: empty FeatureCollection must be not_found, got {out}")
    out = run(hit(CAR))
    if not out.get("ok") or (out.get("properties") or {}).get("cod_imovel") != CAR:
        problems.append(f"deploy_app.fetch_car_live: a hit must resolve: {out}")
    return problems


DEPLOY_MUTATIONS = {
    "fetch_car_live treats JSON without features as absence (fs or [])": [(
        "    fs=r['json'].get('features') if isinstance(r.get('json'),dict) else None\n"
        "    # Only a FeatureCollection is SICAR answering: an exception document is a failed lookup, not absence.\n"
        "    if not isinstance(fs,list):return {'ok':False,'source':'SICAR','detail':'sicar_answer_without_features','bytes':r.get('bytes')}\n",
        "    fs=r['json'].get('features') or []\n")],
    "fetch_car_live takes the first feature whatever its code": [(
        "    if not exact:return {'ok':False,'source':'SICAR','detail':'sicar_answer_other_property','bytes':r.get('bytes')}\n    f=exact[0]",
        "    exact=fs\n    f=exact[0]")],
}


def rule_portal_search(src: str) -> list[str]:
    """The portal search (loadCar) with SICAR failing, absent, or an answer that cannot be read."""
    m = re.search(r"^async function loadCar\(code\)\{.*$", src, re.M)
    if not m:
        return ["portal_api.py: loadCar not found"]
    js = m.group(0) + """
const cases=[
 ['sicar_failed',503,{detail:{car:{ok:false,source:'SICAR',not_found:false,detail:'Consulta ao SICAR pendente',attempts:[{strategy:'wfs1_equal',ok:false,detail:'curl: (28) Operation timed out'}]}}},'pending'],
 ['sicar_answered_absent',404,{detail:{car:{ok:false,source:'SICAR',not_found:true,detail:'CAR não localizado',attempts:[{strategy:'wfs1_equal',ok:true,features:0}]}}},'absent'],
 ['404_not_from_sicar',404,{detail:'Not Found'},'pending'],
 ['proxy_html_502',502,null,'pending'],
 ['200_unreadable_body',200,null,'pending'],
 ['200_without_car',200,{},'pending'],
];
(async()=>{const out={};for(const [name,status,body,want] of cases){const shown=[];let rejected=null;
 globalThis.toast=t=>shown.push(String(t));globalThis.showProperty=()=>shown.push('SHOWN');
 globalThis.fetch=async()=>({ok:status<400,status,json:async()=>{if(body===null)throw new SyntaxError('Unexpected token <');return body}});
 try{await loadCar('MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F')}catch(e){rejected=String(e&&e.message||e)}
 out[name]={want,shown,rejected}}
 process.stdout.write(JSON.stringify(out))})();
"""
    data, err = node_json(js)
    if data is None:
        return [err]
    problems = []
    for name, r in data.items():
        last = r["shown"][-1] if r["shown"] else ""
        if r["rejected"] is not None:
            problems.append(f"{name}: loadCar rejects ({r['rejected'][:120]!r}); the page shows it raw")
        if "{" in last or "attempts" in last or "Cannot read" in last:
            problems.append(f"{name}: technical text shown to the client: {last[:120]!r}")
        if r["want"] == "pending" and ("não localizado" in last.lower() or "pendente" not in last.lower()):
            problems.append(f"{name}: a lookup SICAR did not answer must read as pending, shown {last[:120]!r}")
        if r["want"] == "absent" and "não localizado" not in last.lower():
            problems.append(f"{name}: SICAR answered without the property: expected 'não localizado', shown {last[:120]!r}")
    return problems


PORTAL_SEARCH_MUTATIONS = {
    "portal search throws the JSON detail": [("let d=await r.json().catch(()=>null);if(!r.ok){", "let d=await r.json();if(!r.ok)throw new Error((d.detail&&JSON.stringify(d.detail))||'CAR não localizado');if(false){")],
    "portal search reads d.car of an unreadable body": [("if(!d||!d.car){toast('Consulta ao SICAR pendente. Tente de novo em instantes.');return false}", "")],
}


# ================================================================ booted chains (child processes)

class _MutatingLoader(importlib.abc.Loader):
    def __init__(self, path: Path, pairs, applied: set, name: str):
        self.path, self.pairs, self.applied, self.name = path, pairs, applied, name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        src = self.path.read_text(encoding="utf-8")
        for old, new in self.pairs:
            assert src.count(old) == 1, f"mutation anchor for {self.name} must appear exactly once: {old[:90]!r}"
            src = src.replace(old, new)
        self.applied.add(self.name)
        module.__file__ = str(self.path)
        exec(compile(src, str(self.path), "exec"), module.__dict__)


class _MutatingFinder(importlib.abc.MetaPathFinder):
    """Applies the reverted fix to a module of the chain at import time (no file on disk is touched)."""

    def __init__(self, mutations: dict, applied: set):
        self.mutations, self.applied = mutations, applied

    def find_spec(self, name, path, target=None):
        if name not in self.mutations:
            return None
        file = ROOT / f"{name}.py"
        return importlib.util.spec_from_loader(name, _MutatingLoader(file, self.mutations[name], self.applied, name), origin=str(file))


def _fake_sicar():
    """Answers by the last hex digit of the code in the query: A timeout, B another property, E empty."""
    def req(params, cancel_event=None):
        m = CODE_RX.search(json.dumps(params))
        kind = m.group(0)[-1] if m else None
        if "propertyName" in params:
            return SCAN_ALIVE if kind != "A" else TIMEOUT
        if kind == "E":
            return EMPTY
        if kind == "B":
            return OTHER_PROPERTY
        return TIMEOUT
    return req


def _offline_curl(*a, **k):
    return {"ok": False, "detail": "v2_gate_offline", "bytes": 0}


def _wait(cond, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return bool(cond())


def _expect(problems: list, label: str, resp, status: int, detail: str | None, retry: bool | None = None):
    body = resp.text
    if resp.status_code != status:
        problems.append(f"{label}: HTTP {resp.status_code}, expected {status} ({body[:160]})")
        return
    if detail is not None:
        try:
            got = resp.json().get("detail")
        except Exception:
            got = body[:160]
        if got != detail:
            problems.append(f"{label}: detail {got!r}, expected {detail!r}")
    if retry is True and not resp.headers.get("retry-after"):
        problems.append(f"{label}: 503 without Retry-After")
    for token in ("HTTPException", "Falha ao gerar relatório", "Traceback"):
        if token in body:
            problems.append(f"{label}: technical text in the answer: {token}")


def child_report() -> dict:
    import report_api  # noqa: F401  (sitecustomize loads the report chain after it)

    out: dict[str, list[str]] = {}
    if not _wait(lambda: all(m in sys.modules for m in ("report_quick_v22", "report_pdf_cache_v21", "heavy_live_api_v20")), 120):
        return {"boot": ["report chain did not load (sitecustomize RX_REPORT_V41_RUNTIME)"]}
    time.sleep(0.5)
    import car_resilient
    import deploy_app
    import report_pdf_cache_v21
    from fastapi.testclient import TestClient

    car_resilient._req = _fake_sicar()
    deploy_app._curl = _offline_curl
    with TestClient(report_api.app) as c:
        p = out.setdefault("report_meta", [])
        _expect(p, "meta, SICAR failing", c.get(f"/v1/reports/property/{gate_code(1, 'fail')}/meta"), 503, PENDING, True)
        _expect(p, "meta, SICAR answered empty", c.get(f"/v1/reports/property/{gate_code(2, 'empty')}/meta"), 404, NOT_FOUND)
        _expect(p, "meta, filter ignored", c.get(f"/v1/reports/property/{gate_code(3, 'other')}/meta"), 503, PENDING, True)
        _expect(p, "meta, impossible code", c.get("/v1/reports/property/MG-123/meta"), 422, INVALID)
        p = out.setdefault("report_pdf", [])
        _expect(p, "PDF, SICAR failing", c.get(f"/v1/reports/property/{gate_code(4, 'fail')}"), 503, PENDING, True)
        _expect(p, "PDF, SICAR answered empty", c.get(f"/v1/reports/property/{gate_code(5, 'empty')}"), 404, NOT_FOUND)
        p = out.setdefault("report_quick", [])
        _expect(p, "quick, SICAR failing", c.get(f"/v1/live/quick/{gate_code(6, 'fail')}"), 503, PENDING, True)
        _expect(p, "quick, SICAR answered empty", c.get(f"/v1/live/quick/{gate_code(7, 'empty')}"), 404, NOT_FOUND)
        _expect(p, "quick, impossible code", c.get("/v1/live/quick/MG-123"), 422, INVALID)
        p = out.setdefault("report_job_status", [])
        for n, kind, want_status, want_detail in ((8, "fail", 503, PENDING), (9, "empty", 404, NOT_FOUND)):
            code = gate_code(n, kind)
            prep = c.post(f"/v1/reports/property/{code}/prepare")
            if prep.status_code != 200:
                p.append(f"prepare {kind}: HTTP {prep.status_code}")
                continue
            state: dict = {}

            def done():
                state.update(c.get(f"/v1/reports/property/{code}/status").json())
                return state.get("state") in ("failed", "ready")

            if not _wait(done, 30):
                p.append(f"status {kind}: job never finished: {state}")
                continue
            if state.get("state") != "failed" or state.get("status_code") != want_status or state.get("detail") != want_detail:
                p.append(f"status {kind}: state={state.get('state')} status_code={state.get('status_code')} detail={state.get('detail')!r}, "
                         f"expected failed/{want_status}/{want_detail!r}")
            if want_status == 503 and not isinstance(state.get("retry_after"), int):
                p.append(f"status {kind}: pending job without retry_after: {state.get('retry_after')!r}")
            if "HTTPException" in json.dumps(state, ensure_ascii=False):
                p.append(f"status {kind}: technical text in the job state")
        report_pdf_cache_v21._CACHE.clear()
    return out


def child_portal() -> dict:
    import sitecustomize  # noqa: F401
    import portal_api  # noqa: F401
    import portal_boot_guard_v26 as guard

    if not _wait(lambda: guard.STATE.get("ready") or guard.STATE.get("error"), 90) or guard.STATE.get("ready") is not True:
        return {"boot": [f"portal boot failed: {guard.STATE}"]}
    import car_resilient
    import deploy_app
    import portal_v8
    from fastapi.testclient import TestClient

    car_resilient._req = _fake_sicar()
    deploy_app._curl = _offline_curl
    c = TestClient(portal_api.app)
    out: dict[str, list[str]] = {}

    p = out.setdefault("portal_car_body", [])
    r = c.get(f"/v1/live/car/{gate_code(11, 'fail')}")
    _expect(p, "/v1/live/car, SICAR failing", r, 503, None, True)
    car = ((r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("detail") or {})
    car = car.get("car") if isinstance(car, dict) else None
    if not isinstance(car, dict) or car.get("not_found") is not False or not car.get("attempts") or "pendente" not in str(car.get("detail")).lower():
        p.append(f"/v1/live/car 503 body must keep detail.car (not_found false, attempts, pending): {r.text[:200]}")
    r = c.get(f"/v1/live/car/{gate_code(12, 'empty')}")
    _expect(p, "/v1/live/car, SICAR answered empty", r, 404, None)
    car = (r.json().get("detail") or {}).get("car") if r.status_code == 404 and isinstance(r.json().get("detail"), dict) else None
    if not isinstance(car, dict) or car.get("not_found") is not True or not any(a.get("features") == 0 for a in car.get("attempts") or []):
        p.append(f"/v1/live/car 404 body must keep detail.car (not_found true, attempts with features 0): {r.text[:200]}")
    _expect(p, "/v1/live/car, filter ignored", c.get(f"/v1/live/car/{gate_code(13, 'other')}"), 503, None, True)
    r = c.get("/v1/live/car/MG-123")
    _expect(p, "/v1/live/car, impossible code", r, 422, None)
    detail = r.json().get("detail") if r.status_code == 422 else None
    if not (isinstance(detail, dict) and (detail.get("car") or {}).get("detail") == "invalid_car_format"):
        p.append(f"/v1/live/car 422 body must keep detail.car.detail invalid_car_format: {r.text[:160]}")

    p = out.setdefault("portal_exports", [])
    for path in ("/v1/exports/property/{c}/kml", "/v1/exports/property/{c}/geojson"):
        _expect(p, f"{path}, SICAR failing", c.get(path.format(c=gate_code(14, "fail"))), 503, PENDING, True)
    _expect(p, "KML, SICAR answered empty", c.get(f"/v1/exports/property/{gate_code(15, 'empty')}/kml"), 404, NOT_FOUND)
    p = out.setdefault("portal_tabs", [])
    for path in ("/v1/live/embargos-detail/{c}", "/v1/live/climate-detail/{c}", "/v1/live/groundwater/{c}"):
        _expect(p, f"{path}, SICAR failing", c.get(path.format(c=gate_code(16, "fail"))), 503, PENDING, True)

    view = c.get(f"/v1/mobile/report/view/{gate_code(17, 'fail')}", params={"property_name": "Fazenda Gate"})
    scripts = re.findall(r"<script>(.*?)</script>", view.text, re.S)
    viewer = scripts[-1] if view.status_code == 200 and scripts else ""
    out["viewer"] = rule_viewer(viewer) if viewer else [f"viewer page not served: {view.status_code}"]
    for label, pairs in VIEWER_MUTATIONS.items():
        out[f"viewer@{label}"] = rule_viewer(mutate_text(viewer, pairs)) if viewer else ["no viewer"]

    html = portal_v8.PORTAL_HTML
    w1a = link_reader(html)
    out["link_reader"] = rule_link_reader(w1a)
    for label, pairs in LINK_MUTATIONS.items():
        out[f"link_reader@{label}"] = rule_link_reader(mutate_text(w1a, pairs)) if w1a else ["no link reader"]

    f1b_gate = _f1b_gate()
    f1b, f2, integrity, audit = f1b_gate.scripts_for_harness(html)
    out["f1b_notes"] = rule_f1b_notes(f1b_gate, f1b, f2, integrity, audit)
    for label, pairs in F1B_MUTATIONS.items():
        out[f"f1b_notes@{label}"] = rule_f1b_notes(f1b_gate, mutate_text(f1b, pairs), f2, integrity, audit)
    return out


def child_main(kind: str, mutations: dict) -> None:
    applied: set = set()
    if mutations:
        sys.meta_path.insert(0, _MutatingFinder({k: [tuple(x) for x in v] for k, v in mutations.items()}, applied))
    try:
        result = child_report() if kind == "report" else child_portal()
    except Exception as exc:  # a crash is reported, never a pass
        result = {"crash": [f"{type(exc).__name__}: {str(exc)[:400]}"]}
    missing = set(mutations) - applied
    if missing:
        result["mutation_not_applied"] = [f"module never imported through the mutation: {sorted(missing)}"]
    sys.stdout.write("\nV2_CHILD_RESULT=" + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    os._exit(0)


def run_child(kind: str, mutations: dict | None = None) -> dict:
    env = dict(os.environ, PYTHONUTF8="1", RX_RASTERIO_RUNTIME_INSTALL="off")
    env["RX_RELEASE"] = PORTAL_RELEASE if kind == "portal" else "OFF"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", kind, json.dumps(mutations or {})],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, env=env, cwd=str(ROOT))
    m = re.search(r"^V2_CHILD_RESULT=(.*)$", proc.stdout, re.M)
    if not m:
        return {"crash": [f"child {kind} gave no result (exit {proc.returncode}): {(proc.stdout or '')[-600:]} {(proc.stderr or '')[-900:]}"]}
    if "RX_PORTAL_V46_EXTENSION=failed" in proc.stdout or "RX_REPORT_V41_RUNTIME=failed" in proc.stdout:
        return {"boot": ["deferred boot failed: " + (re.search(r"RX_(?:PORTAL_V46_EXTENSION|REPORT_V41_RUNTIME)=failed.*", proc.stdout).group(0)[:300])]}
    return json.loads(m.group(1))


# ---------------------------------------------------------------- PDF viewer page (node)

VIEWER_HARNESS = r"""
const SCRIPT=%SCRIPT%;
const PENDING=%PENDING%, NOT_FOUND=%NOT_FOUND%;
const scenarios={
 sicar_pending_twice:[{state:'failed',status_code:503,retry_after:30,detail:PENDING},{state:'failed',status_code:503,retry_after:30,detail:PENDING}],
 sicar_pending_then_ready:[{state:'failed',status_code:503,retry_after:30,detail:PENDING},{state:'ready'}],
 not_found:[{state:'failed',status_code:404,detail:NOT_FOUND}],
 build_failed:[{state:'failed',detail:"RuntimeError:boom at report_engine_v9 line 12"}],
};
(async()=>{const out={};
 for(const [name,rounds] of Object.entries(scenarios)){
  const els={};const el=s=>els[s]||(els[s]={hidden:s==='#rxViewerRetry'||s==='#rxOpenPdf'||s==='#rxPdfFrame',textContent:'',src:'',onclick:null,
    classList:{set:new Set(),toggle(c,on){on?this.set.add(c):this.set.delete(c)},add(c){this.set.add(c)},remove(c){this.set.delete(c)}}});
  const calls={prepare:0,status:0};const delays=[];let round=-1;
  const ctx={document:{body:{dataset:{code:'MG-3120904-00000000000000000000000000000011',name:'Fazenda Gate',worker:'https://worker.example/',pdf:'/v1/mobile/report/open/X'}},querySelector:el},
   window:{opener:null},history:{length:1},location:{href:''},console,
   setTimeout:(f,ms)=>{delays.push(ms);setImmediate(f);return 0},
   fetch:async(url,opt)=>{const u=String(url);
    if(u.includes('/prepare/')){calls.prepare++;round++;return {ok:true,status:200,headers:{get:()=>null},json:async()=>({ok:true,state:'running'})}}
    if(u.includes('/status/')){calls.status++;const s=rounds[Math.min(round,rounds.length-1)];return {ok:true,status:200,headers:{get:()=>null},json:async()=>({ok:true,...s})}}
    return {ok:true,status:200,headers:{get:()=>null},json:async()=>({})}}};
  const vm=require('vm');vm.createContext(ctx);
  try{vm.runInContext(SCRIPT,ctx)}catch(e){out[name]={crash:String(e)};continue}
  for(let i=0;i<4000;i++)await new Promise(r=>setImmediate(r));
  out[name]={calls,delays:delays.filter(d=>d>=3000),status:el('#rxViewerStatus').textContent,title:el('#rxViewerTitle').textContent,
   detail:el('#rxViewerDetail').textContent,retry_visible:el('#rxViewerRetry').hidden===false,frame:el('#rxPdfFrame').hidden===false&&!!el('#rxPdfFrame').src,
   spinning:!el('.stage .box').classList.set.has('rx-idle')};
 }
 process.stdout.write(JSON.stringify(out));
})();
"""


def rule_viewer(script: str) -> list[str]:
    js = VIEWER_HARNESS.replace("%SCRIPT%", json.dumps(script)).replace("%PENDING%", json.dumps(PENDING)).replace("%NOT_FOUND%", json.dumps(NOT_FOUND))
    data, err = node_json(js)
    if data is None:
        return [f"viewer: {err}"]
    problems = []
    for name, r in data.items():
        if "crash" in r:
            problems.append(f"viewer {name}: script crashed: {r['crash'][:200]}")
            continue
        shown = " | ".join(str(r.get(k) or "") for k in ("status", "title", "detail"))
        for token in ("HTTPException", "RuntimeError", ":503", ":404", "Error", "Não foi possível concluir"):
            if token in shown:
                problems.append(f"viewer {name}: technical or loud text on screen ({token}): {shown[:200]!r}")
    a = data.get("sicar_pending_twice") or {}
    if a.get("calls", {}).get("prepare") != 2 or 30000 not in (a.get("delays") or []):
        problems.append(f"viewer: a pending SICAR lookup must retry once by itself after Retry-After (prepare calls {a.get('calls')}, waits {a.get('delays')})")
    if "pendente" not in str(a.get("status", "")).lower() or not a.get("retry_visible") or a.get("spinning"):
        problems.append(f"viewer: after the automatic retry, a quiet pending state with 'Tentar novamente' is expected: {a}")
    b = data.get("sicar_pending_then_ready") or {}
    if not b.get("frame"):
        problems.append(f"viewer: the PDF must open when the automatic retry succeeds: {b}")
    n = data.get("not_found") or {}
    if n.get("status") != NOT_FOUND or n.get("calls", {}).get("prepare") != 1:
        problems.append(f"viewer: SICAR answered without the property: {NOT_FOUND!r} without prefix and without retrying expected: {n}")
    g = data.get("build_failed") or {}
    if not g.get("retry_visible") or g.get("calls", {}).get("prepare") != 1:
        problems.append(f"viewer: a failed build is a quiet pending with a retry button: {g}")
    return problems


VIEWER_MUTATIONS = {
    "viewer prints the job detail and never retries": [
        ("if(s.state==='failed'){fail={code:Number(s.status_code)||0,after:Number(s.retry_after)||0};break}", "if(s.state==='failed')throw new Error(s.detail||'geração do PDF falhou');"),
        ("}catch(e){fail={code:0,after:0}}", "}catch(e){qs('#rxViewerStatus').textContent='Não foi possível concluir';detail(String(e.message||e));qs('#rxViewerRetry').hidden=false;return}"),
    ],
    "viewer without the automatic retry": [("if(fail.code===503&&!autoRetried){", "if(false){")],
}


# ---------------------------------------------------------------- link reader (W1a) mirror

def link_reader(html: str) -> str:
    exact = re.search(r"^ const EXACT=new Set\(.*\);$", html, re.M)
    fn = re.search(r"^ function sicarSaidNotFound\(r,d\)\{.*\}$", html, re.M)
    return (exact.group(0) + "\n" + fn.group(0)) if exact and fn else ""


def rule_link_reader(js: str) -> list[str]:
    if not js:
        return ["link reader (W1a) sicarSaidNotFound not found in the served HTML"]
    exact = ["wfs1_equal", "wfs1_in", "wfs2_equal", "wfs1_like_exact", "wfs1_ogc_filter"]
    harness = js + """
const EX=%EXACT%;const att=f=>EX.map(s=>Object.assign({strategy:s},f(s)));
const body=(nf,attempts,detail)=>({detail:{car:{ok:false,source:'SICAR',not_found:nf,detail:detail||'x',attempts}}});
const cases={
 empty_answer:[404,body(true,att(()=>({ok:true,features:0}))),true],
 all_failed:[404,body(true,att(()=>({ok:false,features:null}))),false],
 filter_ignored:[404,body(true,att(s=>s==='wfs1_equal'?{ok:true,features:3}:{ok:false,features:null})),false],
 other_then_empty:[404,body(true,att(s=>s==='wfs1_equal'?{ok:true,features:1}:{ok:true,features:0})),false],
 answered_without_count:[404,body(true,att(()=>({ok:true}))),false],
 pending_503:[503,body(false,att(()=>({ok:true,features:0}))),false],
 invalid_422:[422,body(true,[], 'invalid_car_format'),true],
};
const out={};for(const [k,[st,d,want]] of Object.entries(cases))out[k]={got:sicarSaidNotFound({status:st},d),want};
process.stdout.write(JSON.stringify(out));
""".replace("%EXACT%", json.dumps(exact))
    data, err = node_json(harness)
    if data is None:
        return [f"link reader: {err}"]
    return [f"link reader {k}: sicarSaidNotFound={v['got']!r}, expected {v['want']!r}" for k, v in data.items() if v["got"] != v["want"]]


LINK_MUTATIONS = {
    "link reader trusts any ok exact attempt": [
        ("return ex.some(a=>a.ok===true&&a.features===0)&&!ex.some(a=>Number(a.features)>0)", "return ex.some(a=>a.ok===true)")],
}


# ---------------------------------------------------------------- F1B reading: Brasília note once per block

def _f1b_gate():
    spec = importlib.util.spec_from_file_location("f1b_tela_gate_for_v2", ROOT / "scripts" / "f1b_tela_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rule_f1b_notes(f1b_gate, f1b: str, f2: str, integrity: str, audit: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        return ["Node is required"]
    # Manaus (UTC-4): the reading must still show Brasília time and say so.
    proc = subprocess.run([node, str(f1b_gate.HARNESS)], input=json.dumps({"f1b": f1b, "f2": f2, "integrity": integrity, "audit": audit}),
                          capture_output=True, text=True, encoding="utf-8", timeout=120, env=dict(os.environ, TZ="America/Manaus"))
    try:
        r = json.loads(proc.stdout)
    except Exception:
        return [f"f1b harness failed: {(proc.stdout or '')[-300:]} {(proc.stderr or '')[-300:]}"]
    problems = []
    fire = next((x.get("detail", "") for x in r.get("rows") or [] if x.get("id") == "fire"), "")
    if f"o mais recente de 14/09/2026, 20:20 {NOTE}" not in fire or fire.count(NOTE) != 1:
        problems.append(f"fire row: bulletin time in Brasília with the note once expected: {fire!r}")
    stamp = re.search(r'<span class="rx-f1b-when">(.*?)</span>', r.get("html_ready") or "")
    if not stamp or stamp.group(1).count(NOTE) != 1:
        problems.append(f"consultation stamp without the note (once): {stamp.group(1) if stamp else None!r}")
    if not re.search(r"às \d{2}:\d{2} " + re.escape(NOTE) + r"\.", r.get("html_fill_scheduled") or ""):
        problems.append("next retry time without the note")
    return problems


F1B_MUTATIONS = {
    "stamp without the note": [("esc(stamp+BRT_NOTE)", "esc(stamp)")],
    "fire row without the note": [("o mais recente de ${when(b)}${BRT_NOTE}`", "o mais recente de ${when(b)}`")],
    "retry clock without the note": [("esc(clock(f.when)+BRT_NOTE)", "esc(clock(f.when))")],
    "reading in the device clock": [("return {timeZone:'America/Sao_Paulo'}}catch(e){return null}", "return {}}catch(e){return null}")],
}


# ================================================================ main

REPORT_CHAIN_MUTATIONS = {
    "meta route catches the lookup answer as 502": ({"report_pdf_cache_v21": [(
        "        except HTTPException:raise\n        except Exception as e:raise HTTPException(status_code=502,detail=f'Falha ao gerar relatório: {type(e).__name__}')\n    else:\n        st=_CACHE[key];meta=st['meta'];result=",
        "        except Exception as e:raise HTTPException(status_code=502,detail=f'Falha ao gerar relatório: {type(e).__name__}')\n        except HTTPException:raise\n    else:\n        st=_CACHE[key];meta=st['meta'];result=")]},
        ("report_meta",)),
    "PDF route hides the lookup answer": ({"report_pdf_cache_v21": [("        try:_,meta=await task\n        except HTTPException:raise\n", "        try:_,meta=await task\n")]},
                                          ("report_pdf",)),
    "job state stores 'HTTPException:503: ...'": ({"report_pdf_cache_v21": [("    except HTTPException as e:\n        # The lookup answer", "    except ImportError as e:\n        # The lookup answer")]},
                                                  ("report_job_status",)),
    "quick route turns any failure into 404/502": ({"report_quick_v22": [("            raise lookup_http_error(car)",
                                                                          "            raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou SICAR temporariamente indisponível.')")]},
                                                   ("report_quick",)),
    "analysis route turns any failure into 404/502": ({"report_api": [("    if not car.get('ok'): raise lookup_http_error(car)\n    result=await _retry_failed_core(result)",
                                                                       "    if not car.get('ok'): raise HTTPException(status_code=404 if car.get('not_found') else 502,detail=_safe_summary(result))\n    result=await _retry_failed_core(result)")]},
                                                      ("report_meta", "report_pdf", "report_job_status")),
}

PORTAL_CHAIN_MUTATIONS = {
    "portal lookup routes reverted (body lost, exports and tabs say not located)": ({
        "portal_car_resilient": [("raise lookup_http_error(car,not_found_detail=body,pending_detail=body,invalid_detail=body)", "raise lookup_http_error(car)")],
        # As duas exportações passaram a buscar o CAR pelo mesmo _car_para_exportar (dentro do escopo de
        # cancelamento): a âncora é a resposta honesta dele, e a mutação agora atinge GeoJSON e KML de uma vez.
        "portal_api": [("        raise lookup_http_error(car)\n    return car", "        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail='CAR não localizado ou SICAR indisponível.')\n    return car")],
        "portal_property_tabs": [("    if not car.get('ok'):raise lookup_http_error(car)\n",
                                  "    if not car.get('ok'):raise __import__('fastapi').HTTPException(status_code=404 if car.get('not_found') else 502,detail='Imóvel não localizado')\n")],
    }, ("portal_car_body", "portal_exports", "portal_tabs")),
}


def main() -> int:
    failures = 0

    def check(label, problems):
        nonlocal failures
        if problems:
            failures += 1
            print(f"FAIL {label}")
            for p in problems[:12]:
                print(f"  - {p}")
        else:
            print(f"PASS {label}")

    def control(label, problems):
        nonlocal failures
        # A mutant that only crashes the rule proves nothing about the rule: it must fail by behavior.
        if problems and all(re.search(r"crashed|node failed|not JSON|did not run|Traceback", str(p)) for p in problems):
            failures += 1
            print(f"FAIL control ({label}): the mutant crashed instead of failing the rule: {str(problems[0])[:160]}")
        elif problems:
            print(f"PASS control ({label}): the reverted fix is caught ({len(problems)} problem(s)): {str(problems[0])[:110]}")
        else:
            failures += 1
            print(f"FAIL control ({label}): the rule does not see the old defect")

    # 1. hora em Brasília
    pdf = load("report_ptbr_v50")
    check("1 PDF: Brasília time (tz database), note once per block", rule_bulletin_pdf(pdf))
    for label, pairs in PDF_MUTATIONS.items():
        control(label, rule_bulletin_pdf(load("report_ptbr_v50", pairs)))
    f1b_src = (ROOT / "portal_full_reading_f1b.py").read_text(encoding="utf-8")
    check("1 screen: Brasília time on any device clock, same hour as the PDF in daylight saving time", rule_bulletin_screen(f1b_src, pdf))
    for label, pairs in SCREEN_MUTATIONS.items():
        control(label, rule_bulletin_screen(mutate_text(f1b_src, pairs), pdf))

    # 2. SICAR: failure is never absence
    check("2 SICAR lookup: only an empty exact answer proves absence", rule_sicar_lookup(load("car_resilient", as_main=True)))
    for label, pairs in SICAR_MUTATIONS.items():
        control(label, rule_sicar_lookup(load("car_resilient", pairs, as_main=True)))
    check("2 HTTP: 422 impossible code, 404 only for an answer, 503 + Retry-After for a failure", rule_http_error(load("sicar_lookup_http")))
    for label, pairs in HTTP_MUTATIONS.items():
        control(label, rule_http_error(load("sicar_lookup_http", pairs)))
    check("2 deploy_app.fetch_car_live: exception document and another property are not absence", rule_deploy_fetch(load("deploy_app")))
    for label, pairs in DEPLOY_MUTATIONS.items():
        control(label, rule_deploy_fetch(load("deploy_app", pairs)))
    portal_src = (ROOT / "portal_api.py").read_text(encoding="utf-8")
    check("2 portal search: pending or not located, never raw JSON", rule_portal_search(portal_src))
    for label, pairs in PORTAL_SEARCH_MUTATIONS.items():
        control(label, rule_portal_search(mutate_text(portal_src, pairs)))

    # Booted chains: report service
    base = run_child("report")
    for name in ("boot", "crash"):
        if name in base:
            check(f"report chain {name}", base[name])
    for name in ("report_meta", "report_pdf", "report_quick", "report_job_status"):
        check(f"2 report chain: {name}", base.get(name, ["check did not run"]))
    for label, (mutations, targets) in REPORT_CHAIN_MUTATIONS.items():
        mutant = run_child("report", mutations)
        if any(k in mutant for k in ("boot", "crash", "mutation_not_applied")):
            failures += 1
            print(f"FAIL control ({label}): mutant did not run as a rule failure: {mutant}")
            continue
        for target in targets:
            control(f"{label} -> {target}", mutant.get(target, []))

    # Booted chains: portal
    base = run_child("portal")
    for name in ("boot", "crash"):
        if name in base:
            check(f"portal chain {name}", base[name])
    for name in ("portal_car_body", "portal_exports", "portal_tabs", "viewer", "link_reader", "f1b_notes"):
        check(f"2 portal chain: {name}", base.get(name, ["check did not run"]))
    expected_controls = [("viewer", VIEWER_MUTATIONS), ("link_reader", LINK_MUTATIONS), ("f1b_notes", F1B_MUTATIONS)]
    for rule, mutations in expected_controls:
        for label in mutations:
            key = f"{rule}@{label}"
            if key not in base:
                failures += 1
                print(f"FAIL control ({label} -> {rule}): did not run")
                continue
            control(f"{label} -> {rule}", base[key])
    for label, (mutations, targets) in PORTAL_CHAIN_MUTATIONS.items():
        mutant = run_child("portal", mutations)
        if any(k in mutant for k in ("boot", "crash", "mutation_not_applied")):
            failures += 1
            print(f"FAIL control ({label}): mutant did not run as a rule failure: {mutant}")
            continue
        for target in targets:
            control(f"{label} -> {target}", mutant.get(target, []))

    print(f"RX_V2_VERDADE_PEQUENAS_GATE={'PASS' if not failures else 'FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        child_main(sys.argv[2], json.loads(sys.argv[3]))
    raise SystemExit(main())

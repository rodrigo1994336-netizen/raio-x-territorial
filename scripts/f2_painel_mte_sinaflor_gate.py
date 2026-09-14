"""Gate F2 — linhas MTE e SINAFLOR do painel nunca ficam carregando para sempre.

Roda offline (sem portal, sem SICAR, sem PAMGIA). Três camadas:

1. estática — o render do painel V45 emite ``rx45:panel-rendered`` (depois das
   âncoras do ``portal_map_v46_anchor_state``, que precisam casar uma vez só);
   MTE/SINAFLOR não dependem mais de timers fixos;
2. pura — ``portal_panel_sources_f2.classify_*`` sobre respostas reais gravadas
   em ``tests/fixtures/f2_painel_mte_sinaflor`` (sem dado pessoal);
3. navegador — Chromium sem rede monta o painel com os scripts REAIS extraídos
   dos módulos e simula: map-panel 12 s atrasado; re-render repetido; fonte
   fora do ar -> "CONSULTA PENDENTE" + "Consultar de novo" -> nova consulta.

Uso:
  python scripts/f2_painel_mte_sinaflor_gate.py                 # árvore atual
  python scripts/f2_painel_mte_sinaflor_gate.py --ref origin/main  # controle positivo (deve reprovar)
  python scripts/f2_painel_mte_sinaflor_gate.py --record http://127.0.0.1:8206/  # regrava fixtures
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "f2_painel_mte_sinaflor"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OTHER_CAR = "MG-3120904-00000000000000000000000000000000"
ORIGIN = "http://rx-f2.test"
EVENT = "rx45:panel-rendered"
MAP_PANEL_DELAY_S = 12.0  # legacy timers stop at ~10.8 s
UNRESOLVED = ("", "on_demand", "checking")

PERSONAL_KEYS = {
    "enterprise", "establishment", "owner_name", "owner_document", "cpf", "cnpj", "holder",
    "responsible", "technical_responsible", "validated_name", "sigef_reference",
    "sigef_reference_others", "geographic_references", "name", "employer",
}
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
CNPJ_RE = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")

failures: list[str] = []


def check(ok: bool, label: str, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f" :: {detail}"))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------- sources
def read_source(rel: str, ref: str | None) -> str | None:
    if ref is None:
        path = ROOT / rel
        return path.read_text(encoding="utf-8") if path.exists() else None
    res = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], capture_output=True)
    return res.stdout.decode("utf-8") if res.returncode == 0 else None


def string_assign(source: str | None, name: str) -> str:
    if not source:
        return ""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return ""


def once_pairs(source: str) -> list[tuple[str, str]]:
    pairs = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "once" and len(node.args) >= 2:
            a, b = node.args[0], node.args[1]
            if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
                pairs.append((a.value, b.value))
    return pairs


def load_sources(ref: str | None) -> dict[str, str]:
    v45_src = read_source("portal_map_panel_v45.py", ref)
    anchors_src = read_source("portal_map_v46_anchor_state.py", ref) or ""
    v45 = string_assign(v45_src, "V45_PANEL_UI")
    applied, render_anchor = 0, 0
    for old, new in once_pairs(anchors_src):
        hits = v45.count(old)
        if "function render(p)" in old:
            render_anchor += 1
            check(hits == 1, "static: anchor v46 on V45 render matches exactly once", hits)
        if hits == 1:
            v45 = v45.replace(old, new, 1)
            applied += 1
    check(render_anchor == 1 and applied >= 5, "static: V46 anchors applied on the V45 panel", (render_anchor, applied))
    return {
        "v45": v45,
        "runtime": string_assign(read_source("portal_panel_sources_f2.py", ref), "RUNTIME_JS"),
        "mte": string_assign(read_source("portal_conformity_mte_v48.py", ref), "UI"),
        "sinaflor": string_assign(read_source("portal_conformity_sinaflor_v48.py", ref), "UI"),
    }


def static_checks(src: dict[str, str]) -> None:
    render = re.search(r"function render\(p\)\{.*?\n", src["v45"])
    check(bool(render) and EVENT in render.group(0), "static: V45 render emits rx45:panel-rendered", render.group(0)[:160] if render else None)
    for key in ("mte", "sinaflor"):
        check(bool(src[key]), f"static: {key} UI extracted")
        timers = re.findall(r"\[\d+(?:,\d+){2,}\]\.forEach", src[key])
        check(not timers, f"static: {key} row does not depend on fixed timers", timers)
        check("rxPanelSourcesF2" in src[key], f"static: {key} row listens to the panel render event")
    check(bool(src["runtime"]) and EVENT in src["runtime"], "static: shared per-CAR runtime present")


# ---------------------------------------------------------------- fixtures / pure
def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def personal_data_hits(value, path="$"):
    hits = []
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in PERSONAL_KEYS and v not in (None, "", [], {}):
                hits.append(f"{path}.{k}")
            hits += personal_data_hits(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hits += personal_data_hits(v, f"{path}[{i}]")
    elif isinstance(value, str) and (CPF_RE.search(value) or CNPJ_RE.search(value)):
        hits.append(path)
    return hits


def pure_checks() -> dict[str, dict]:
    sys.path.insert(0, str(ROOT))
    import portal_panel_sources_f2 as f2

    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    check(len(names) >= 5, "fixtures: recorded responses present", names)
    for name in names:
        hits = personal_data_hits(fixture(name))
        check(not hits, f"fixtures: {name} carries no personal data", hits)

    mte_ok, mte_fail = fixture("mte_blocked_real.json"), fixture("mte_source_failed_real.json")
    sf_ok, sf_fail = fixture("sinaflor_spatial_unconfirmed_real.json"), fixture("sinaflor_source_failed_real.json")
    expected = {
        "mte_blocked": (f2.classify_mte(mte_ok), "blocked", "blocked_missing_owner_identity", False),
        "mte_failed": (f2.classify_mte(mte_fail), "pending", "source_failed", False),
        "sinaflor_found": (f2.classify_sinaflor(sf_ok), "found", "checked_spatial_record_unconfirmed", True),
        "sinaflor_failed": (f2.classify_sinaflor(sf_fail), "pending", "source_failed", False),
    }
    for label, (got, state, source_state, answered) in expected.items():
        check((got["state"], got["source_state"], got["answered"]) == (state, source_state, answered), f"pure: {label}", got)

    # Never fake the answer: the same real payload without the source's own "answered" flag is pending.
    check(f2.classify_sinaflor({**sf_ok, "answered": False})["state"] == "pending", "pure: sinaflor unanswered payload stays pending")
    check(f2.classify_sinaflor({**sf_ok, "state": "future_state"})["state"] == "pending", "pure: sinaflor unknown state stays pending")
    check(f2.classify_mte({**mte_ok, "ok": False})["state"] == "pending", "pure: mte not-ok stays pending")
    check(f2.classify_mte(None)["state"] == "pending" and f2.classify_sinaflor("x")["state"] == "pending", "pure: garbage is pending")
    clear = {**mte_ok, "answered": True, "state": "checked_clear", "match_count": 0}
    check(f2.classify_mte(clear)["state"] == "not_found", "pure: mte clear is not_found (zero is an answer only when answered)")

    def boom(*_a):
        raise RuntimeError("source crashed")

    raw = f2.query_panel_sources(CAR, mte_query=boom, sinaflor_query=lambda car: sf_ok)
    payload = f2.panel_sources_payload(raw)
    check(payload["conformity_mte"]["state"] == "pending" and payload["conformity_sinaflor"]["state"] == "found",
          "pure: payload keeps a crashed source as pending", payload)
    check(payload["conformity_sinaflor"]["authorization_numbers"] == ["20319201908550"], "pure: payload carries the public act number", payload)
    check(not personal_data_hits(payload), "pure: payload carries no personal data", personal_data_hits(payload))
    return {k: v[0] for k, v in expected.items()}


# ---------------------------------------------------------------- browser
HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>f2</title></head><body>
<div id="panel"></div><div id="rx43SnapshotHost"></div>
<script>window.current={};window.showProperty=function(p,g){window.current={...(p||{})}};</script>
{scripts}
</body></html>"""

ROW_JS = """car=>{const p=document.querySelector('#rx43SnapshotHost .rx45-panel-card[data-car="'+car+'"]');
 const r=id=>{const el=p&&p.querySelector('.rx45-check[data-source="'+id+'"]');return el?{state:el.dataset.state||'',answered:el.dataset.answered||'',
  status:(el.querySelector('.rx48-check-status')?.textContent||'').trim(),retry:!!el.querySelector('[data-rx48-retry]'),text:(el.innerText||'').replace(/\\s+/g,' ').trim()}:null};
 return {panel:!!p,rows:p?p.querySelectorAll('.rx45-check').length:0,mte:r('mte_slave_labor'),sinaflor:r('sinaflor')}}"""


class Sources:
    """Route table: what each /v1/live endpoint answers, counted per source."""

    def __init__(self):
        self.counts = {"mte": 0, "sinaflor": 0, "map": 0}
        self.mode = {"mte": "ok", "sinaflor": "ok"}
        self.map_delay = 0.0
        self.source_delay = 0.2


async def build_page(browser, src: dict[str, str], sources: Sources, fixtures: dict):
    ctx = await browser.new_context(viewport={"width": 375, "height": 812})
    page = await ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    scripts = "\n".join(x for x in (src["v45"], src["runtime"], src["mte"], src["sinaflor"]) if x)
    html = HARNESS.replace("{scripts}", scripts)

    async def handle(route):
        url = route.request.url
        if url.rstrip("/") == ORIGIN:
            return await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        for key, path in (("mte", "/v1/live/conformity/mte/"), ("sinaflor", "/v1/live/conformity/sinaflor/")):
            if path in url:
                sources.counts[key] += 1
                await asyncio.sleep(sources.source_delay)
                mode = sources.mode[key]
                if mode == "abort":
                    return await route.abort("failed")
                body = fixtures[f"{key}_{mode}"]
                try:
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                except Exception:
                    return None
        if "/v1/live/map-panel/" in url:
            sources.counts["map"] += 1
            await asyncio.sleep(sources.map_delay)
            car = url.rsplit("/", 1)[-1].split("?")[0]
            try:
                return await route.fulfill(status=200, content_type="application/json", body=json.dumps({**fixtures["map_panel"], "car_code": car}))
            except Exception:
                return None
        return await route.abort("failed")

    await page.route("**/*", handle)
    await page.goto(ORIGIN + "/", wait_until="load")
    return ctx, page, errors


async def open_panel(page, car):
    # What V46 "VER ANÁLISE COMPLETA" does: legacy open + immediate shell (no MTE/SINAFLOR rows yet).
    await page.evaluate("""car=>{const p={car_code:car,area_ha:14.8,car_status:'AT',property_type:'IRU'};window.current={...p};
      window.showProperty(p,null);window.rxV46RenderV45Immediate&&window.rxV46RenderV45Immediate(p)}""", car)


async def wait_rows(page, car, predicate, seconds):
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    state = None
    while loop.time() < end:
        state = await page.evaluate(ROW_JS, car)
        if predicate(state):
            return True, state
        await asyncio.sleep(0.1)
    return False, state


def resolved(state):
    return bool(state and state["mte"] and state["sinaflor"] and state["mte"]["state"] not in UNRESOLVED and state["sinaflor"]["state"] not in UNRESOLVED)


def matches(row, expected):
    return bool(row) and row["state"] == expected["source_state"] and row["answered"] == ("1" if expected["answered"] else "0") and row["status"] == expected["status"]


async def browser_checks(src: dict[str, str], expected: dict[str, dict]) -> None:
    from playwright.async_api import async_playwright

    fixtures = {
        "mte_ok": fixture("mte_blocked_real.json"),
        "mte_failed": fixture("mte_source_failed_real.json"),
        "sinaflor_ok": fixture("sinaflor_spatial_unconfirmed_real.json"),
        "sinaflor_failed": fixture("sinaflor_source_failed_real.json"),
        "map_panel": fixture("map_panel_real.json"),
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # B1 — map-panel slower than every legacy timer.
            s = Sources()
            s.map_delay, s.source_delay = MAP_PANEL_DELAY_S, 1.0
            ctx, page, errors = await build_page(browser, src, s, fixtures)
            await open_panel(page, CAR)
            ok, st = await wait_rows(page, CAR, resolved, MAP_PANEL_DELAY_S + 8)
            check(ok, "browser B1: map-panel 12 s late -> MTE and SINAFLOR resolve", st)
            if ok:
                check(matches(st["mte"], expected["mte_blocked"]), "browser B1: MTE row = classify_mte(real)", st["mte"])
                check(matches(st["sinaflor"], expected["sinaflor_found"]), "browser B1: SINAFLOR row = classify_sinaflor(real)", st["sinaflor"])
            check(not errors, "browser B1: no page errors", errors)
            await ctx.close()

            # B2 — fast panel, legacy re-render timers (0.5/1.8/9.5 s) and explicit re-renders: one query per source.
            s = Sources()
            s.map_delay, s.source_delay = 0.2, 3.0
            ctx, page, errors = await build_page(browser, src, s, fixtures)
            await open_panel(page, CAR)
            ok, st = await wait_rows(page, CAR, resolved, 10)
            check(ok, "browser B2: rows resolve with a fast panel", st)
            await page.wait_for_timeout(8000)  # past the last legacy re-render
            for _ in range(3):
                await open_panel(page, CAR)  # same CAR re-rendered from the payload V45 already holds
                await page.wait_for_timeout(150)
            ok2, st2 = await wait_rows(page, CAR, resolved, 2)
            check(ok2 and matches(st2["sinaflor"], expected["sinaflor_found"]), "browser B2: re-render repaints the remembered answer", st2)
            check(s.counts["sinaflor"] == 1 and s.counts["mte"] == 1, "browser B2: re-render never re-queries a source", s.counts)
            check(not errors, "browser B2: no page errors", errors)
            await ctx.close()

            # B3 — sources down: one automatic retry, then pending + "Consultar de novo"; the click queries again.
            s = Sources()
            s.map_delay, s.source_delay = 0.2, 0.2
            s.mode = {"mte": "abort", "sinaflor": "failed"}
            ctx, page, errors = await build_page(browser, src, s, fixtures)
            await open_panel(page, CAR)
            pending = lambda x: bool(x and x["mte"] and x["sinaflor"] and x["mte"]["retry"] and x["sinaflor"]["retry"])
            ok, st = await wait_rows(page, CAR, pending, 12)
            check(ok, "browser B3: failed sources end as pending with 'Consultar de novo'", st)
            if ok:
                for key in ("mte", "sinaflor"):
                    row = st[key]
                    check(row["state"] == "source_failed" and row["answered"] == "0" and row["status"] == "CONSULTA PENDENTE",
                          f"browser B3: {key} pending row is honest", row)
                    check("dado:" not in row["text"].lower(), f"browser B3: {key} pending row claims no data date", row["text"])
                check(s.counts["mte"] == 2 and s.counts["sinaflor"] == 2, "browser B3: exactly one automatic retry per source", s.counts)
                s.mode = {"mte": "ok", "sinaflor": "ok"}
                await page.locator('[data-rx48-retry="sinaflor"]').click()
                await page.locator('[data-rx48-retry="mte_slave_labor"]').click()
                ok, st = await wait_rows(page, CAR, lambda x: resolved(x) and x["sinaflor"]["state"] != "source_failed" and x["mte"]["state"] != "source_failed", 8)
                check(ok and matches(st["sinaflor"], expected["sinaflor_found"]) and matches(st["mte"], expected["mte_blocked"]),
                      "browser B3: 'Consultar de novo' queries again and resolves", st)
            check(not errors, "browser B3: no page errors", errors)
            await ctx.close()

            # B4 — another CAR has its own memory (no answer leaks between properties).
            s = Sources()
            s.map_delay, s.source_delay = 0.2, 0.5
            ctx, page, errors = await build_page(browser, src, s, fixtures)
            await open_panel(page, CAR)
            ok, _ = await wait_rows(page, CAR, resolved, 8)
            await open_panel(page, OTHER_CAR)
            ok2, st2 = await wait_rows(page, OTHER_CAR, resolved, 8)
            check(ok and ok2 and s.counts["sinaflor"] == 2, "browser B4: each CAR is queried on its own", (s.counts, st2))
            check(not errors, "browser B4: no page errors", errors)
            await ctx.close()
        finally:
            await browser.close()


# ---------------------------------------------------------------- record
def record(base: str) -> None:
    import httpx

    base = base.rstrip("/")
    get = lambda path: httpx.get(base + path, timeout=180).json()  # noqa: E731
    FIXTURES.mkdir(parents=True, exist_ok=True)

    mte = get(f"/v1/live/conformity/mte/{CAR}")
    sinaflor = get(f"/v1/live/conformity/sinaflor/{CAR}")
    for m in sinaflor.get("matches") or []:
        m.pop("enterprise", None)  # may be a natural person in other responses; never stored
    panel = get(f"/v1/live/map-panel/{CAR}")
    keep = ("ok", "car_code", "car_status", "property_type", "condition", "area_ha", "fiscal_modules", "created_at",
            "updated_at", "uf", "compliance_sources", "source_audit", "risk")
    panel = {k: panel.get(k) for k in keep}

    # Outage responses from the REAL source code with the transport down (what the portal returns then).
    sys.path.insert(0, str(ROOT))
    import sinaflor_authorization_hardening_v48 as hardening

    hardening.sf._METADATA_CACHE = None
    hardening.sf.deploy_app._curl = lambda *a, **k: {"ok": False, "timed_out": True, "detail": "process_timeout_after_45s", "bytes": 0}
    sinaflor_failed = hardening.query_sinaflor_authorization(CAR)

    import mte_slave_labor_v48 as mte_mod

    class _Down:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            raise httpx.ConnectError("transport_down_simulated")

        def __exit__(self, *a):
            return False

    mte_mod._CACHE = None
    mte_mod.httpx = type("httpx_down", (), {"Client": _Down})
    mte_failed = mte_mod.query_mte_slave_labor(None, None)

    out = {
        "mte_blocked_real.json": mte,
        "mte_source_failed_real.json": mte_failed,
        "sinaflor_spatial_unconfirmed_real.json": sinaflor,
        "sinaflor_source_failed_real.json": sinaflor_failed,
        "map_panel_real.json": panel,
    }
    for name, data in out.items():
        hits = personal_data_hits(data)
        if hits:
            raise SystemExit(f"refusing to store personal data in {name}: {hits}")
        (FIXTURES / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("recorded", name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", help="git ref to test instead of the working tree (positive control)")
    ap.add_argument("--record", metavar="BASE_URL", help="re-record fixtures from a running portal")
    ap.add_argument("--skip-browser", action="store_true")
    args = ap.parse_args()
    if args.record:
        record(args.record)
        return 0
    print(f"RX_F2_PAINEL_MTE_SINAFLOR_GATE source={'working-tree' if not args.ref else args.ref}")
    src = load_sources(args.ref)
    static_checks(src)
    expected = pure_checks()
    if not args.skip_browser:
        asyncio.run(browser_checks(src, expected))
    verdict = "PASS" if not failures else "FAIL"
    print(f"RX_F2_PAINEL_MTE_SINAFLOR_GATE={verdict} failures={len(failures)}")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

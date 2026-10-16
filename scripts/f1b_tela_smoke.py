"""F1B screen smoke (local, real browser, real SICAR): hover, full reading, calls per source, PDF button.

NOT in CI: it needs SICAR, the conformity sources and the report worker answering, which GitHub
cannot always reach. The rules themselves are proven without network by scripts/f1b_tela_gate.py.

Per width (375x812 mobile, 768x1024, 1440x900):
  1B.1  hovering a property draws nothing over the map (no Leaflet tooltip, no layer with a tooltip);
  1B.2  "VER ANÁLISE COMPLETA" (card) opens the panel and the reading appears INSIDE the panel:
        loading state first, then one row per question answered Sim / Não / Consulta pendente,
        nothing written into #pbody, no internal wording, pt-BR numbers;
  1B.3  one call per source per opened property: map-panel, MTE, SINAFLOR and CAR integrity are
        asked once when they answer (at most twice when the first try did not answer: one automatic
        retry); one /v1/live/quick; none of the hidden legacy calls (premium, WhatsApp, monitoring,
        critical minerals); the panel scrolls on its own (wheel over it never zooms the map); the
        PDF and full-analysis buttons are on screen without scrolling and at least 44 px tall.
Failure flow (1440): MTE, SINAFLOR, integrity and the report worker answer 503 -> each source is
asked at most twice and the panel shows a quiet "Consulta pendente" with a 44 px retry.

Run (portal already up):  python scripts/f1b_tela_smoke.py --base http://127.0.0.1:8311 --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
READY = "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard') && window.rxV46Installed===true && !!window.rxFullReadingF1b"
INTERNAL = ("PREPARADO — OFF", "BACKEND DE ALERTAS INDISPONÍVEL", "RISCO NÃO CLASSIFICADO", "fontes responderam", "SISTEMA LIVE")
LEGACY_HIDDEN = ("/v1/integrations/premium/status", "/v1/whatsapp/status", "/v1/monitoring/status", "/v1/live/critical-minerals/")
SOURCES = {
    "map-panel": re.compile(r"^/v1/live/map-panel/[^/]+$"),
    "mte": re.compile(r"^/v1/live/conformity/mte/"),
    "sinaflor": re.compile(r"^/v1/live/conformity/sinaflor/"),
    "integridade": re.compile(r"^/v1/live/car-integrity/"),
    "quick": re.compile(r"^/v1/live/quick/"),
    "progressive-status": re.compile(r"^/v1/live/progressive/status/"),
}
ANSWER = {"map-panel": lambda d: d.get("ok") is True, "integridade": lambda d: d.get("ok") is True,
          "mte": lambda d: d.get("ok") is True, "sinaflor": lambda d: d.get("ok") is True and d.get("answered") is True}

GEO_JS = """()=>{const vis=sel=>{const e=document.querySelector(sel);if(!e||!e.getClientRects().length)return null;const b=e.getBoundingClientRect(),x=b.left+b.width/2,y=b.top+b.height/2,hit=document.elementFromPoint(x,y);
  return {top:Math.round(b.top),bottom:Math.round(b.bottom),w:Math.round(b.width),h:Math.round(b.height),on_screen:b.top>=0&&b.bottom<=innerHeight&&b.left>=0&&b.right<=innerWidth,topmost:!!hit&&(hit===e||e.contains(hit))}};
  const h=document.querySelector('#rx43SnapshotHost'),hb=h?h.getBoundingClientRect():null;
  return {inner:[innerWidth,innerHeight],hscroll:document.documentElement.scrollWidth>innerWidth+1,doc_scroll:document.documentElement.scrollHeight>innerHeight+1,
   host:h?{top:Math.round(hb.top),bottom:Math.round(hb.bottom),scrollH:h.scrollHeight,clientH:h.clientHeight,overflowY:getComputedStyle(h).overflowY}:null,
   full:vis('#rx45Full'),pdf:vis('#rx45Pdf'),pbody_len:(document.querySelector('#pbody')?.innerHTML||'').trim().length}}"""

SLOT_JS = """(car)=>{const c=document.querySelector(`#rx43SnapshotHost .rx45-panel-card[data-car="${car}"]`);const s=c&&c.querySelector('[data-rx-full-slot]');
  if(!s)return {present:false};const cs=getComputedStyle(s);return {present:true,phase:s.dataset.phase||'',display:cs.display,visibility:cs.visibility,rects:s.getClientRects().length,
  rows:[...s.querySelectorAll('[data-rx-f1b-row]')].map(r=>({id:r.dataset.rxF1bRow,answer:r.querySelector('.rx-f1b-a')?.textContent||'',q:r.querySelector('.rx-f1b-q')?.textContent||'',d:r.querySelector('.rx-f1b-d')?.textContent||''})),
  retry:(()=>{const b=s.querySelector('[data-rx-f1b-retry]');if(!b)return null;const r=b.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height)}})(),text:s.innerText,button:c.querySelector('#rx45Full')?.textContent||''}}"""


def classify(path: str) -> str | None:
    for name, rx in SOURCES.items():
        if rx.search(path):
            return name
    return None


class Traffic:
    def __init__(self, page):
        self.t0 = time.time()
        self.calls: list[dict] = []
        self.legacy: list[str] = []
        page.on("request", self.on_request)
        page.on("response", lambda r: asyncio.ensure_future(self.on_response(r)))

    def on_request(self, req):
        path = urlparse(req.url).path
        if any(x in path for x in LEGACY_HIDDEN):
            self.legacy.append(path)
        name = classify(path)
        if name:
            self.calls.append({"src": name, "t": round(time.time() - self.t0, 2), "url": req.url, "status": None, "answered": None})

    async def on_response(self, resp):
        path = urlparse(resp.url).path
        name = classify(path)
        if not name:
            return
        row = next((c for c in reversed(self.calls) if c["url"] == resp.url and c["status"] is None), None)
        if row is None:
            return
        row["status"] = resp.status
        try:
            row["wait_ms"] = round(resp.request.timing.get("responseStart", -1))
        except Exception:
            row["wait_ms"] = None
        if name in ANSWER:
            try:
                body = await resp.json()
                row["answered"] = bool(resp.ok and isinstance(body, dict) and ANSWER[name](body))
            except Exception:
                row["answered"] = False

    def count(self):
        out = {}
        for c in self.calls:
            out[c["src"]] = out.get(c["src"], 0) + 1
        return out


def check(cond, msg, failures):
    if not cond:
        failures.append(msg)
    return cond


def per_source_contract(traffic: Traffic, label: str, failures: list) -> dict:
    by = {}
    for c in traffic.calls:
        by.setdefault(c["src"], []).append(c)
    report = {}
    for src in ("map-panel", "mte", "sinaflor", "integridade"):
        calls = by.get(src, [])
        first_answered = bool(calls) and calls[0]["answered"] is True
        report[src] = {"calls": len(calls), "first_answered": first_answered, "statuses": [c["status"] for c in calls]}
        check(len(calls) >= 1, f"{label}:{src}:never_asked", failures)
        limit = 1 if first_answered else 2
        check(len(calls) <= limit, f"{label}:{src}:asked_{len(calls)}x_limit_{limit}", failures)
    report["quick"] = {"calls": len(by.get("quick", []))}
    report["progressive-status"] = {"calls": len(by.get("progressive-status", []))}
    # One /v1/live/quick per property. The reading's single automatic retry is legitimate only when the engine
    # did not answer within its wait (DEEP_WAIT_MS=110 s in portal_full_reading_f1b.py): a second call earlier
    # than that, or a third call, is a duplicate. (Measured 14/09: engine slower than 110 s -> retry at +116 s.)
    quick = by.get("quick", [])
    retry_ok = len(quick) == 2 and quick[1]["t"] - quick[0]["t"] >= 110
    check(len(quick) == 1 or retry_ok, f"{label}:quick:asked_{len(quick)}x", failures)
    check(not traffic.legacy, f"{label}:legacy_hidden_calls:{traffic.legacy}", failures)
    return report


async def wait_ready(page):
    await page.wait_for_function(READY, timeout=90000)


async def open_property(page):
    await page.locator("#q").fill(CAR)
    await page.locator("#go").click()
    await page.locator(f'.rx46-card[data-car="{CAR}"]').wait_for(state="visible", timeout=90000)


async def hover_probe(page):
    await page.wait_for_timeout(2500)
    pt = await page.evaluate("""()=>{const c=document.querySelector('.rx46-card')?.getBoundingClientRect();let best=null;
      map.eachLayer(l=>{if(best||!l.feature?.properties?.cod_imovel||!l.getBounds)return;try{const q=map.latLngToContainerPoint(l.getBounds().getCenter()),r=map.getContainer().getBoundingClientRect(),x=q.x+r.left,y=q.y+r.top;
      if(x<40||y<90||x>innerWidth-40||y>innerHeight-40)return;if(c&&x>c.left-20&&x<c.right+20&&y>c.top-20&&y<c.bottom+20)return;const hit=document.elementFromPoint(x,y);if(!hit||!hit.closest('.leaflet-container')||hit.closest('.leaflet-popup'))return;best={x,y}}catch(e){}});return best}""")
    if not pt:
        return {"point": None}
    await page.mouse.move(pt["x"] - 4, pt["y"] - 4)
    await page.mouse.move(pt["x"], pt["y"], steps=5)
    await page.wait_for_timeout(700)
    return {"point": pt, **(await page.evaluate("""()=>{let layers=0,with_tip=0;map.eachLayer(l=>{if(l.feature?.properties?.cod_imovel){layers++;if(l.getTooltip&&l.getTooltip())with_tip++}});
      return {layers,with_tip,visible_tooltips:[...document.querySelectorAll('.leaflet-tooltip')].filter(t=>t.offsetParent!==null).map(t=>t.innerText)}}"""))}


async def run_width(browser, w, h, out: Path, failures: list) -> dict:
    label = f"{w}x{h}"
    kw = dict(viewport={"width": w, "height": h}, locale="pt-BR")
    if w < 768:
        kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
    ctx = await browser.new_context(**kw)
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    res = {"width": w, "height": h}
    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
    await wait_ready(page)
    traffic = Traffic(page)
    await open_property(page)
    if w >= 768:
        hov = await hover_probe(page)
        res["hover"] = hov
        await page.screenshot(path=str(out / f"f1b_after_{label}_hover.png"))
        if check(hov.get("point") is not None, f"{label}:hover:no_parcel_point", failures):
            check(hov["with_tip"] == 0 and not hov["visible_tooltips"], f"{label}:hover:tooltip_over_map:{hov}", failures)
        await page.mouse.move(5, h - 5)
    await page.locator('.rx46-card [data-rx46-action="full"]').click()
    await page.locator(f'.rx45-panel-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
    # loading state is visible inside the panel right after the click
    await page.wait_for_function(f"!!document.querySelector('.rx45-panel-card[data-car=\"{CAR}\"] [data-rx-full-slot]')", timeout=15000)
    res["slot_first"] = {k: v for k, v in (await page.evaluate(SLOT_JS, CAR)).items() if k in ("phase", "display", "button")}
    await page.wait_for_timeout(9000)
    geo = await page.evaluate(GEO_JS)
    res["geometry"] = geo
    await page.screenshot(path=str(out / f"f1b_after_{label}_panel.png"))
    for key in ("full", "pdf"):
        b = geo.get(key)
        if check(b is not None, f"{label}:{key}:missing", failures):
            check(b["on_screen"] and b["topmost"], f"{label}:{key}:not_on_screen:{b}", failures)
            check(b["h"] >= 44 and b["w"] >= 44, f"{label}:{key}:target_below_44px:{b}", failures)
    check(not geo["hscroll"], f"{label}:horizontal_scroll", failures)
    host = geo.get("host") or {}
    check(host.get("bottom", 10**6) <= h + 1, f"{label}:panel_bottom_off_screen:{host}", failures)
    if host.get("scrollH", 0) > host.get("clientH", 0) + 4:
        before = await page.evaluate("()=>({top:document.querySelector('#rx43SnapshotHost').scrollTop,zoom:map.getZoom()})")
        hb = await page.locator("#rx43SnapshotHost").bounding_box()
        if w >= 768:
            await page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + min(hb["height"] / 2, 300))
            await page.mouse.wheel(0, 400)
        else:
            await page.evaluate("()=>document.querySelector('#rx43SnapshotHost').scrollBy(0,400)")
        await page.wait_for_timeout(600)
        after = await page.evaluate("()=>({top:document.querySelector('#rx43SnapshotHost').scrollTop,zoom:map.getZoom()})")
        res["panel_scroll"] = {"before": before, "after": after}
        check(after["top"] > before["top"], f"{label}:panel_does_not_scroll:{res['panel_scroll']}", failures)
        check(after["zoom"] == before["zoom"], f"{label}:wheel_over_panel_zoomed_map:{res['panel_scroll']}", failures)
        geo2 = await page.evaluate(GEO_JS)
        check(bool(geo2["pdf"]) and geo2["pdf"]["on_screen"], f"{label}:pdf_leaves_screen_when_scrolled:{geo2['pdf']}", failures)
    # wait for the reading to finish (worker may be asleep)
    try:
        await page.wait_for_function(f"['ready','failed'].includes(document.querySelector('.rx45-panel-card[data-car=\"{CAR}\"] [data-rx-full-slot]')?.dataset.phase)", timeout=170000)
    except Exception:
        pass
    slot = await page.evaluate(SLOT_JS, CAR)
    res["slot"] = {k: slot.get(k) for k in ("phase", "display", "visibility", "rects", "button", "retry")}
    res["rows"] = slot.get("rows")
    check(slot.get("present") and slot["display"] != "none" and slot["visibility"] != "hidden" and slot["rects"] > 0, f"{label}:reading_hidden:{res['slot']}", failures)
    check(slot.get("phase") in ("ready", "failed"), f"{label}:reading_never_finished:{slot.get('phase')}", failures)
    if slot.get("phase") == "ready":
        check(len(slot["rows"]) >= 5, f"{label}:too_few_rows:{len(slot['rows'])}", failures)
        for r in slot["rows"]:
            check(r["answer"] in ("Sim", "Não", "Consulta pendente"), f"{label}:bad_answer:{r}", failures)
            check(not re.search(r"\d\.\d{3,}\s*ha|\d+\.\d+ ha", r["d"]), f"{label}:non_ptbr_number:{r}", failures)
    else:
        check(bool(slot.get("retry")) and slot["retry"]["h"] >= 44, f"{label}:failed_without_44px_retry:{slot.get('retry')}", failures)
    await page.evaluate(f"()=>document.querySelector('.rx45-panel-card[data-car=\"{CAR}\"] [data-rx-full-slot]').scrollIntoView({{block:'start'}})")
    await page.wait_for_timeout(500)
    await page.screenshot(path=str(out / f"f1b_after_{label}_reading.png"))
    panel_text = await page.locator(f'.rx45-panel-card[data-car="{CAR}"]').inner_text()
    leaked = [x for x in INTERNAL if x.casefold() in panel_text.casefold()]
    check(not leaked, f"{label}:internal_text_in_panel:{leaked}", failures)
    geo_end = await page.evaluate(GEO_JS)
    check(geo_end["pbody_len"] == 0, f"{label}:result_written_into_hidden_pbody:{geo_end['pbody_len']}", failures)
    res["panel_text"] = panel_text[:4000]
    res["calls"] = per_source_contract(traffic, label, failures)
    res["request_log"] = [(c["t"], c["src"], c["status"], c["answered"], c.get("wait_ms")) for c in traffic.calls]
    res["errors"] = errors
    check(not errors, f"{label}:page_errors:{errors}", failures)
    await ctx.close()
    return res


async def failure_flow(browser, out: Path, failures: list) -> dict:
    ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
    page = await ctx.new_page()
    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
    await wait_ready(page)
    for pattern in ("**/v1/live/conformity/mte/**", "**/v1/live/conformity/sinaflor/**", "**/v1/live/car-integrity/**", "**/v1/live/quick/**", "**/v1/live/progressive/status/**"):
        await page.route(pattern, lambda route: route.fulfill(status=503, json={"detail": "fixture_unavailable"}))
    traffic = Traffic(page)
    await open_property(page)
    await page.locator('.rx46-card [data-rx46-action="full"]').click()
    await page.locator(f'.rx45-panel-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
    await page.wait_for_function(f"document.querySelector('.rx45-panel-card[data-car=\"{CAR}\"] [data-rx-full-slot]')?.dataset.phase==='failed'", timeout=60000)
    await page.wait_for_timeout(15000)  # past every automatic retry: nothing else may be asked
    slot = await page.evaluate(SLOT_JS, CAR)
    counts = traffic.count()
    res = {"calls": counts, "slot": {k: slot.get(k) for k in ("phase", "retry", "button")}}
    for src in ("mte", "sinaflor", "integridade", "quick"):
        check(1 <= counts.get(src, 0) <= 2, f"failure:{src}:asked_{counts.get(src, 0)}x", failures)
    check(bool(slot.get("retry")) and slot["retry"]["h"] >= 44, f"failure:retry_target:{slot.get('retry')}", failures)
    text = await page.locator(f'.rx45-panel-card[data-car="{CAR}"]').inner_text()
    check("consulta pendente" in text.casefold(), "failure:no_quiet_pending_text", failures)
    for loud in ("SEM PENDÊNCIAS", "NÃO FOI POSSÍVEL", "FONTE INDISPONÍVEL"):
        check(loud.casefold() not in text.casefold(), f"failure:loud_text:{loud}", failures)
    await page.evaluate(f"()=>document.querySelector('.rx45-panel-card[data-car=\"{CAR}\"] [data-rx-full-slot]').scrollIntoView({{block:'start'}})")
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(out / "f1b_after_1440x900_failure.png"))
    before = counts.get("quick", 0)
    await page.locator('[data-rx-f1b-retry]').click()
    await page.wait_for_timeout(1500)
    res["retry_asks_again"] = traffic.count().get("quick", 0) > before
    check(res["retry_asks_again"], "failure:retry_did_not_ask_again", failures)
    await ctx.close()
    return res


async def main(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for w, h in ((1440, 900), (768, 1024), (375, 812)):
                if args.only and str(w) not in args.only.split(","):
                    continue
                results[f"{w}x{h}"] = await run_width(browser, w, h, out, failures)
                print("F1B_SMOKE_WIDTH", w, json.dumps({k: results[f"{w}x{h}"].get(k) for k in ("calls", "slot", "geometry", "hover")}, ensure_ascii=False), flush=True)
            if not args.skip_failure:
                results["failure"] = await failure_flow(browser, out, failures)
                print("F1B_SMOKE_FAILURE", json.dumps(results["failure"], ensure_ascii=False), flush=True)
        finally:
            await browser.close()
    results["failures"] = failures
    (out / "f1b_tela_smoke.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print("F1B_TELA_SMOKE=" + ("PASS" if not failures else "FAIL " + json.dumps(failures, ensure_ascii=False)), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8311")
    ap.add_argument("--out", default="f1b_smoke_out")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-failure", action="store_true")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    sys.exit(asyncio.run(main(a)))

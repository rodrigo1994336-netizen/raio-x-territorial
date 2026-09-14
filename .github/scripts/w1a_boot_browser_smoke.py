"""W1a browser smoke: a new tab opens the portal with ONE navigation (no boot reload),
the page scripts all ran, assets are same-origin and cached on the second visit.

    BASE=http://127.0.0.1:8000/ python .github/scripts/w1a_boot_browser_smoke.py            # server already up
    python .github/scripts/w1a_boot_browser_smoke.py --spawn 8311                          # also a cold server

Also, with the real server (requests intercepted in the browser only):
* the hashed CSS, the hashed JS, or both fail to load -> the tab reloads once and ends on the
  complete, styled inline page (/?rx-inline=1);
* the "failed" boot page at 375/768/1440: honest copy (no automatic-retry promise, no
  internals), a visible "Tentar de novo" button of at least 44 px that reloads the tab.

Positive control: against the pre-W1a portal every new tab navigates twice
(sessionStorage 'rx-v26-ready-reload' + location.reload) and rxPortalBootReady never appears;
against the pre-review W1a commit the CSS-abort tab stays unstyled on one navigation and the
failed boot page promises an automatic retry with the button hidden.
Writes artifacts/w1a_boot_<vp>.png, w1a_boot_failed_<vp>.png and artifacts/w1a_boot_smoke.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("BASE", "http://127.0.0.1:8000/")
OUT = Path(os.environ.get("OUT", "artifacts"))
VIEWPORTS = {"1440": (1440, 900), "768": (768, 1024), "375": (375, 812)}
FAIL: list[str] = []
REPORT: dict = {"base": BASE, "viewports": {}, "cold_server": None, "asset_failure": {}, "boot_failed": {}}
# Same wait condition as every portal smoke.
READY_JS = "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard')"
PROMISES = re.compile(r"tentaremos|automaticamente|vamos tentar|m[óo]dulo|carregando|inicializando|demorando|pronto", re.I)

STATE = r"""()=>({std:""" + READY_JS + r""", url:location.pathname+location.search,
  top:(()=>{const t=document.querySelector('.top');return t?getComputedStyle(t).position:null})(),
  guard:!!document.querySelector('#rxBootGuard'), boot:document.body?document.body.dataset.rxBoot||null:null,
  ready:window.rxPortalBootReady===true, v46:window.rxV46Installed===true, rxNum:typeof (window.rxNum&&window.rxNum.ha)==='function',
  parts:[...document.scripts].filter(s=>/^rxW1aRun\(\d+\)$/.test(s.text)).length, ran:window.rxW1aRan||0,
  map:(()=>{try{return typeof map!=='undefined'&&!!map&&!!map.getBounds}catch(e){return false}})(),
  tiles:document.querySelectorAll('.leaflet-tile-pane img.leaflet-tile-loaded').length,
  overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth})"""
CACHE = r"""()=>performance.getEntriesByType('resource').filter(e=>e.name.includes('/static/')).map(e=>({n:e.name.replace(location.origin,''),transfer:e.transferSize,decoded:e.decodedBodySize}))"""


def check(ok: bool, msg: str) -> None:
    if not ok:
        FAIL.append(msg)
        print("FAIL", msg, flush=True)


async def open_tab(ctx, url: str, label: str, shot: bool, until=None):
    page = await ctx.new_page()
    navs, errors, statics, cdn = [], [], [], []
    first_doc_boot = []
    page.on("framenavigated", lambda fr: navs.append(round(time.perf_counter() * 1000)) if fr == page.main_frame else None)
    page.on("pageerror", lambda e: errors.append(str(e)[:300]))

    def on_response(resp):
        u = resp.url
        if resp.request.resource_type == "document" and resp.frame == page.main_frame:
            first_doc_boot.append(resp.headers.get("x-raiox-boot"))
        if "/static/" in u:
            statics.append((u, resp.status))
        if "unpkg.com/leaflet" in u:
            cdn.append(u)

    page.on("response", on_response)
    t0 = time.perf_counter()
    await page.goto(url, wait_until="commit", timeout=90000)
    st = {}
    while time.perf_counter() - t0 < 90:
        try:
            st = await page.evaluate(STATE)
        except Exception:
            await asyncio.sleep(0.03)
            continue
        if st["std"] and st["v46"] and st["map"] and (until is None or until(st)):
            break
        await asyncio.sleep(0.03)
    ready_ms = round((time.perf_counter() - t0) * 1000)
    tile_deadline = time.perf_counter() + 12
    while time.perf_counter() < tile_deadline and st.get("tiles", 0) == 0:
        await asyncio.sleep(0.1)
        st = await page.evaluate(STATE)
    await page.wait_for_timeout(400)
    st = await page.evaluate(STATE)
    if shot:
        OUT.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUT / f"w1a_boot_{label}.png"))
    cache = await page.evaluate(CACHE)
    await page.close()
    return {"navigations": len(navs), "documents": first_doc_boot, "ready_ms": ready_ms, "state": st,
            "pageerrors": errors, "static": statics, "cdn_leaflet": cdn, "cache": cache}


async def ready_server(browser):
    for vp, (w, h) in VIEWPORTS.items():
        kw = dict(viewport={"width": w, "height": h}, locale="pt-BR", service_workers="block")
        if vp == "375":
            kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
        ctx = await browser.new_context(**kw)
        first = await open_tab(ctx, BASE, vp, True)
        second = await open_tab(ctx, BASE, vp + "_2nd", False)
        await ctx.close()
        REPORT["viewports"][vp] = {"first": first, "second": second}
        for name, r in (("first", first), ("second", second)):
            st = r["state"]
            check(r["navigations"] == 1, f"{vp} {name} tab navigated {r['navigations']}x (boot reload is back)")
            check(st.get("ready") and st.get("v46") and st.get("map") and st.get("rxNum") and st.get("parts", 0) > 0 and st.get("ran") == st.get("parts"), f"{vp} {name} page scripts did not all run: {st}")
            check(not st.get("guard"), f"{vp} {name} boot overlay still on screen")
            check(not r["pageerrors"], f"{vp} {name} page errors: {r['pageerrors'][:3]}")
            check(all(s == 200 for _, s in r["static"]) and r["static"], f"{vp} {name} static responses: {r['static'][:5]}")
            check(not r["cdn_leaflet"], f"{vp} {name} still loads Leaflet from unpkg")
            check(st.get("overflow", 1) <= 1, f"{vp} {name} horizontal overflow {st.get('overflow')} px")
        hashed = [c for c in second["cache"] if c["n"].startswith("/static/")]
        check(hashed and all(c["transfer"] == 0 and c["decoded"] > 0 for c in hashed), f"{vp} second tab re-downloaded assets: {hashed}")
        print(json.dumps({"vp": vp, "nav": [first["navigations"], second["navigations"]], "ready_ms": [first["ready_ms"], second["ready_ms"]], "tiles": first["state"].get("tiles")}), flush=True)


async def asset_failure(browser, normal_top):
    """Hashed CSS and/or JS aborted in the browser: one reload, then the styled inline page."""
    for kind, pattern in (("css", re.compile(r"/static/rx/[0-9a-f]+\.css$")), ("js", re.compile(r"/static/rx/[0-9a-f]+\.js$")),
                          ("both", re.compile(r"/static/rx/[0-9a-f]+\.(?:css|js)$"))):
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR", service_workers="block")
        aborted: list[str] = []

        def make_abort(sink):  # Playwright passes (route, request) to two-argument handlers
            async def abort(route):
                sink.append(urlparse(route.request.url).path)
                await route.abort("failed")
            return abort

        abort = make_abort(aborted)

        await ctx.route(lambda u, pattern=pattern: bool(pattern.search(urlparse(u).path)), abort)
        # the page's own query must survive the fallback (a shared /?car= link keeps its property)
        r = await open_tab(ctx, BASE + "?rxkeep=1", f"asset_{kind}", True, until=lambda st: "rx-inline=1" in st["url"])
        await ctx.close()
        st = r["state"]
        REPORT["asset_failure"][kind] = {**r, "aborted": aborted}
        check(bool(aborted), f"asset failure {kind}: nothing was aborted (pattern did not match the served assets)")
        check(r["navigations"] == 3, f"asset failure {kind}: {r['navigations']} navigations (expected page, one reload, inline page)")
        check(r["documents"] == ["ready", "ready", "ready-inline"], f"asset failure {kind}: documents {r['documents']}")
        check("rx-inline=1" in st.get("url", ""), f"asset failure {kind}: did not end on the inline page ({st.get('url')})")
        check("rxkeep=1" in st.get("url", ""), f"asset failure {kind}: the inline fallback dropped the page query ({st.get('url')})")
        check(st.get("ready") and st.get("v46") and st.get("map") and not st.get("guard"), f"asset failure {kind}: final page incomplete {st}")
        check(normal_top not in (None, "static") and st.get("top") == normal_top,
              f"asset failure {kind}: final page unstyled (.top position {st.get('top')!r}, normal {normal_top!r})")
        check(not r["pageerrors"], f"asset failure {kind}: page errors {r['pageerrors'][:3]}")
        print(json.dumps({"asset_failure": kind, "nav": r["navigations"], "documents": r["documents"], "top": st.get("top")}), flush=True)


FAILED_STATE = r"""()=>{const b=document.querySelector('#rxBootRetry'),r=b?b.getBoundingClientRect():null,
  bar=document.querySelector('#rxBootGuard .bar'),box=document.querySelector('#rxBootGuard .box');
  const bx=box?box.getBoundingClientRect():null;
  return {boot:document.body.dataset.rxBoot||null,title:(document.querySelector('#rxBootTitle')||document.querySelector('#rxBootGuard h2')||{}).textContent||'',
    text:(document.querySelector('#rxBootText')||{}).textContent||'',
    button:b?{text:b.textContent.trim(),display:getComputedStyle(b).display,w:r.width,h:r.height,top:r.top,bottom:r.bottom,left:r.left,right:r.right}:null,
    bar:bar?getComputedStyle(bar).display:null,vw:innerWidth,vh:innerHeight,
    box:bx?{left:bx.left,right:bx.right,top:bx.top,bottom:bx.bottom}:null,
    overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}}"""


async def boot_failed(browser):
    """The failed boot page (the deferred load raised; the server does not retry) in the three widths."""
    import portal_boot_assets_w1a as w1a

    body = w1a.boot_page("failed").decode("utf-8")
    expected = getattr(w1a, "BOOT_COPY", {}).get("failed")
    for vp, (w, h) in VIEWPORTS.items():
        kw = dict(viewport={"width": w, "height": h}, locale="pt-BR", service_workers="block")
        if vp == "375":
            kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
        ctx = await browser.new_context(**kw)
        await ctx.route(lambda u: urlparse(u).path == "/", lambda route: route.fulfill(
            status=200, headers={"content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-raiox-boot": "failed"}, body=body))
        await ctx.route(lambda u: urlparse(u).path == "/v1/bootstrap/state", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"ready": False, "error": "RuntimeError:smoke"})))
        page = await ctx.new_page()
        navs: list[str] = []
        page.on("framenavigated", lambda fr: navs.append(fr.url) if fr == page.main_frame else None)
        await page.goto(BASE, wait_until="load")
        await page.wait_for_timeout(1500)  # first poll answers "failed"; the copy must stay honest after it
        st = await page.evaluate(FAILED_STATE)
        OUT.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUT / f"w1a_boot_failed_{vp}.png"))
        b = st.get("button") or {}
        words = f"{st['title']} {st['text']}"
        check(st["boot"] == "failed", f"failed page {vp}: body state {st['boot']!r}")
        check(bool(st["text"].strip()) and not PROMISES.search(words), f"failed page {vp}: copy promises a retry or talks internals: {words!r}")
        if expected:
            check(st["title"] == expected["title"] and st["text"] == expected["text"], f"failed page {vp}: copy differs from BOOT_COPY: {words!r}")
        check(b.get("text") == "Tentar de novo" and b.get("display") != "none" and b.get("w", 0) >= 44 and b.get("h", 0) >= 44,
              f"failed page {vp}: no visible 44 px 'Tentar de novo' button: {b}")
        check(bool(b) and b["left"] >= 0 and b["right"] <= st["vw"] and b["top"] >= 0 and b["bottom"] <= st["vh"], f"failed page {vp}: button outside the screen: {b}")
        check(st["bar"] == "none", f"failed page {vp}: progress bar still animating on a failed load ({st['bar']!r})")
        box = st.get("box") or {}
        check(bool(box) and box["left"] >= 0 and box["right"] <= st["vw"] and st["overflow"] <= 1, f"failed page {vp}: box overflows {box} overflow={st['overflow']}")
        before = len(navs)
        reloaded = False
        if b.get("display") not in (None, "none"):
            if vp == "375":
                await page.tap("#rxBootRetry")
            else:
                await page.click("#rxBootRetry")
            for _ in range(100):
                if len(navs) > before:
                    reloaded = True
                    break
                await page.wait_for_timeout(100)
        check(reloaded, f"failed page {vp}: 'Tentar de novo' did not reload the tab")
        REPORT["boot_failed"][vp] = {**st, "reloaded": reloaded}
        await ctx.close()
        print(json.dumps({"boot_failed": vp, "button": [b.get("w"), b.get("h")], "reloaded": reloaded}), flush=True)


async def cold_server(browser, port: int):
    env = dict(os.environ, RX_RELEASE="V8_OPERATIONAL_ZERO_COST", PYTHONUTF8="1")
    env["PYTHONPATH"] = os.pathsep.join(x for x in (os.getcwd(), env.get("PYTHONPATH", "")) if x)
    log = open(OUT / "w1a_cold_server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "portal_api:app", "--host", "127.0.0.1", "--port", str(port)],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR", service_workers="block")
        r = await open_tab(ctx, f"http://127.0.0.1:{port}/", "cold_server", False)
        await ctx.close()
        REPORT["cold_server"] = r
        docs = r["documents"]
        check(r["navigations"] <= 2, f"cold server: {r['navigations']} navigations")
        check(r["navigations"] == 1 or (docs and docs[0] in ("pending", "failed")), f"cold server reloaded a page that was not the boot page: {docs}")
        check(docs and docs[-1] == "ready", f"cold server final document not the ready page: {docs}")
        st = r["state"]
        check(st.get("ready") and st.get("v46") and st.get("map") and not st.get("guard"), f"cold server final page incomplete: {st}")
        print(json.dumps({"cold_server": {"navigations": r["navigations"], "documents": docs, "ready_ms": r["ready_ms"]}}), flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        log.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        if "--only-spawn" not in sys.argv:
            await ready_server(browser)
            normal = REPORT["viewports"].get("1440", {}).get("first", {}).get("state", {}).get("top")
            await asset_failure(browser, normal)
            await boot_failed(browser)
        if "--spawn" in sys.argv or "--only-spawn" in sys.argv:
            flag = "--spawn" if "--spawn" in sys.argv else "--only-spawn"
            await cold_server(browser, int(sys.argv[sys.argv.index(flag) + 1]))
        await browser.close()
    REPORT["failures"] = FAIL
    (OUT / "w1a_boot_smoke.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": not FAIL, "failures": FAIL}, ensure_ascii=False), flush=True)
    sys.exit(1 if FAIL else 0)


asyncio.run(main())

"""W1a browser smoke: CAR parcels fast, progressive, canvas, no blink, honest notice.

Runs against a booted portal (RX_SMOKE_BASE, default http://127.0.0.1:8000/). The SICAR cells
are served by a deterministic in-browser fixture (page.route), so the result never depends on
SICAR answering the CI runner; the server side of the contract is covered by
scripts/w1a_imoveis_rapidos_gate.py.

Checked at 375 (touch), 768 and 1440:
  * one request per grid cell (no legacy bbox call), parcels on one canvas renderer;
  * progressive: parcels on screen while visible cells are still loading;
  * warm server (fixture answers in 20 ms): first parcels <= 1000 ms and 90% <= 1500 ms after a move;
  * hysteresis: a 10% pan requests nothing; no cell is requested twice;
  * no blank frame while panning or zooming with a slow server;
  * "Mostrando N imóveis. Há mais nesta área." only with a capped cell, N = parcels in view;
  * a cell failing twice becomes the quiet pending line and is asked again on the next move;
  * a canvas click opens the card of that CAR; an empty click closes it.
Positive control: on origin/main it fails at the first assertion (legacy bbox request, SVG paths).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

BASE = os.getenv("RX_SMOKE_BASE", "http://127.0.0.1:8000/")
OUT = Path(os.getenv("RX_SMOKE_OUT", "artifacts"))
RESULTS: dict = {"viewports": {}}
NOANIM = {"animate": False}

IN_VIEW_JS = """()=>{const b=map.getBounds();let n=0;map.eachLayer(l=>{const p=l.feature&&l.feature.properties;if(p&&p.cod_imovel&&l.getBounds){try{if(b.intersects(l.getBounds()))n++}catch(e){}}});return n}"""
SVG_PARCELS_JS = """()=>{let n=0;map.eachLayer(l=>{if(l._path&&l.feature?.properties?.cod_imovel)n++});return n}"""
CELL_AT_JS = """a=>{const s=window.rxW1aGridState().step;return [s,Math.floor(a[1]/s+1e-9),Math.floor(a[0]/s+1e-9)]}"""
PARCEL_POINT_JS = """()=>{const r=document.querySelector('#map').getBoundingClientRect(),top=Math.max(r.top,document.querySelector('header.top')?.getBoundingClientRect().bottom||0),c0=map.getSize();let best=null;
  map.eachLayer(l=>{const p=l.feature&&l.feature.properties;if(!p||!p.cod_imovel||!l._containsPoint)return;const c=map.latLngToContainerPoint(l.getBounds().getCenter()),x=r.left+c.x,y=r.top+c.y;
    if(x<r.left+40||x>r.right-70||y<top+80||y>r.bottom-90)return;if(!l._containsPoint(map.containerPointToLayerPoint([c.x,c.y])))return;const el=document.elementFromPoint(x,y);if(!el||el.tagName!=='CANVAS')return;
    const d=Math.hypot(c.x-c0.x/2,c.y-c0.y/2);if(!best||d<best.d)best={x,y,d,car:p.cod_imovel}});return best}"""
EMPTY_POINT_JS = """()=>{const r=document.querySelector('#map').getBoundingClientRect();for(const fy of [.35,.45,.55,.65])for(const fx of [.2,.3,.4,.5,.6,.7]){const x=r.left+r.width*fx,y=r.top+r.height*fy,el=document.elementFromPoint(x,y);
  if(!el||!el.closest('#map')||el.closest('.leaflet-popup,.leaflet-control'))continue;const lp=map.containerPointToLayerPoint([x-r.left,y-r.top]);let hit=false;map.eachLayer(l=>{if(!hit&&l.feature?.properties?.cod_imovel&&l._containsPoint){try{hit=l._containsPoint(lp)}catch(e){}}});if(!hit)return {x,y}}return null}"""


def car_code(key: tuple, i: int) -> str:
    return "MG-3152006-" + hashlib.md5(f"{key}:{i}".encode()).hexdigest().upper()


class CellFixture:
    def __init__(self) -> None:
        self.requests: list[tuple] = []
        self.legacy: list[str] = []
        self.delay_ms = 20
        self.stagger_ms = 0
        self.fail: set[tuple] = set()
        self.dense: set[tuple] = set()

    async def handle(self, route) -> None:
        q = parse_qs(urlparse(route.request.url).query)
        if "cell" not in q:
            self.legacy.append(route.request.url)
            await route.fulfill(status=500, json={"detail": "legacy bbox request"})
            return
        step = float(q["cell"][0])
        ix, iy = round(float(q["west"][0]) / step), round(float(q["south"][0]) / step)
        key = (step, ix, iy)
        order = len(self.requests)
        self.requests.append(key)
        await asyncio.sleep((self.delay_ms + self.stagger_ms * order) / 1000)
        if key in self.fail:
            await route.fulfill(status=502, json={"detail": "SICAR indisponível nesta quadrícula."})
            return
        n = 50 if key in self.dense else 9
        cols = math.ceil(math.sqrt(n))
        size = step / cols
        feats = []
        for i in range(n):
            x = ix * step + (i % cols) * size + size * 0.2
            y = iy * step + (i // cols) * size + size * 0.2
            s = size * 0.6
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]},
                          "properties": {"cod_imovel": car_code(key, i), "area": 12.5, "municipio": "Pompéu", "uf": "MG", "status_imovel": "AT",
                                         "condicao": "Aguardando análise", "tipo_imovel": "IRU", "m_fiscal": 0.31}})
        body = {"type": "FeatureCollection", "features": feats, "uf": "MG", "ufs": ["MG"], "truncated": n >= 50, "partial_failures": 0,
                "grid": {"step": step, "ix": ix, "iy": iy, "cells": 1}, "cached": True}
        await route.fulfill(status=200, json=body, headers={"Cache-Control": "public, max-age=600"})


async def wait_runtime(page) -> None:
    await page.wait_for_function("sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')", timeout=60000)
    await page.wait_for_function("window.rxV46Installed===true && typeof map!=='undefined' && !!map.getBounds", timeout=30000)


async def settle(page, timeout_ms=8000) -> None:
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        st = await page.evaluate("()=>window.rxW1aGridState?window.rxW1aGridState():null")
        assert st is not None, "W1a grid loader missing (window.rxW1aGridState)"
        if st["step"] and not st["active"] and not st["queued"]:
            return
        await page.wait_for_timeout(60)
    raise AssertionError(f"grid did not settle: {st}")


def op(name: str, *args) -> dict:
    return {"name": name, "args": list(args)}


async def measure_move(page, action: dict, max_ms=6000) -> dict:
    return await page.evaluate(
        """async ({action,max})=>{const count=%s;const t0=performance.now();map[action.name](...action.args);let first=null,series=[],last=-1,progressive=false;
          while(performance.now()-t0<max){await new Promise(r=>setTimeout(r,20));const n=count(),st=window.rxW1aGridState?window.rxW1aGridState():{active:0,queued:0};if(n>0&&first===null)first=performance.now()-t0;
            if(n>0&&(st.active||st.queued))progressive=true;if(n!==last){series.push([Math.round(performance.now()-t0),n]);last=n}
            if(first!==null&&!st.active&&!st.queued&&performance.now()-t0-series[series.length-1][0]>400)break}
          const fin=last,t90=(series.find(s=>s[1]>=Math.ceil(fin*.9))||[null])[0];return {first_ms:first===null?null:Math.round(first),t90_ms:t90,final:fin,progressive,series:series.slice(0,30)}}""" % IN_VIEW_JS,
        {"action": action, "max": max_ms},
    )


async def sample_min(page, action: dict, ms=1400) -> int:
    return await page.evaluate(
        """async ({action,ms})=>{const count=%s;map[action.name](...action.args);let min=1e9;const t0=performance.now();while(performance.now()-t0<ms){min=Math.min(min,count());await new Promise(r=>requestAnimationFrame(()=>r()))}return min}""" % IN_VIEW_JS,
        {"action": action, "ms": ms},
    )


async def viewport_flow(browser, width: int, height: int) -> None:
    label = str(width)
    kw = {"viewport": {"width": width, "height": height}, "locale": "pt-BR"}
    if width < 768:
        kw.update(is_mobile=True, has_touch=True)
    ctx = await browser.new_context(**kw)
    page = await ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))
    # The fixture answers 502 on purpose in step 6; the browser logs that as a network line, not a JS error.
    page.on("console", lambda m: errors.append(f"console:{m.text[:200]}") if m.type == "error" and "status of 502" not in m.text else None)
    fx = CellFixture()
    await page.route("**/v1/live/sicar/viewport-v46*", fx.handle)
    for pattern in ("**/v1/live/map-panel/**", "**/v1/live/snapshot/**", "**/v1/live/property-identity/**"):
        await page.route(pattern, lambda route: route.fulfill(json={"ok": False, "detail": "w1a_fixture"}))
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    await wait_runtime(page)
    await page.evaluate("()=>{map.setView([-19.2247,-45.0033],10,{animate:false})}")
    await page.wait_for_timeout(500)
    rec: dict = {}

    # 1) slow first view: progressive, canvas, cell requests only.
    fx.delay_ms, fx.stagger_ms = 120, 45
    first = await measure_move(page, op("setView", [-19.2247, -45.0033], 13, NOANIM), 9000)
    rec["first_view"] = first
    assert not fx.legacy, ("legacy bbox viewport request", fx.legacy[:2])
    assert fx.requests, "no cell request"
    await settle(page, 12000)
    st = await page.evaluate("()=>window.rxW1aGridState()")
    assert st["renderer"] == "canvas" and await page.locator(".leaflet-rxParcel-pane canvas").count() == 1, st
    assert await page.evaluate(SVG_PARCELS_JS) == 0, "parcels still drawn as SVG paths"
    assert first["progressive"], ("parcels only appeared after every cell", first)

    # 2) warm server: fixture answers in 20 ms, fresh area.
    fx.delay_ms, fx.stagger_ms = 20, 0
    warm = await measure_move(page, op("setView", [-19.5, -45.4], 13, NOANIM), 5000)
    await settle(page)
    rec["warm_move"] = warm
    assert warm["first_ms"] is not None and warm["first_ms"] <= 1000, ("first parcels after move > 1000 ms", warm)
    assert warm["t90_ms"] is not None and warm["t90_ms"] <= 1500, ("90% of parcels after move > 1500 ms", warm)

    # 3) hysteresis: small pan asks nothing; no cell is ever asked twice.
    n0 = len(fx.requests)
    min_small = await sample_min(page, op("panBy", [int(width * 0.1), 0], NOANIM), 900)
    rec["small_pan_new_requests"] = len(fx.requests) - n0
    assert len(fx.requests) == n0, ("10% pan requested cells", fx.requests[n0:])
    assert min_small > 0, "blank map during small pan"
    fx.delay_ms = 350
    min_big = await sample_min(page, op("panBy", [int(width * 0.6), 0], NOANIM), 1400)
    await settle(page)
    rec["big_pan_min_in_view"] = min_big
    assert min_big > 0, "blank frame while a 60% pan loads"
    dup = {k for k in fx.requests if fx.requests.count(k) > 1}
    assert not dup, ("cells requested twice", sorted(dup)[:3])

    # 4) zoom with a slow server: previous parcels stay until the new cells arrive.
    fx.delay_ms = 400
    zoom_now = await page.evaluate("()=>map.getZoom()")
    min_zoom = await sample_min(page, op("setZoom", zoom_now + 1, NOANIM), 1200)
    await settle(page)
    rec["zoom_min_in_view"] = min_zoom
    assert min_zoom > 0, "blank frame while zooming"

    # 5) honest notice: no capped cell -> quiet; capped center cell -> count equals parcels in view.
    fx.delay_ms = 20
    state_plain = await page.evaluate("()=>document.querySelector('#rxMapState')?.textContent||''")
    assert "Há mais" not in state_plain, ("truncation notice without a capped cell", state_plain)
    # Same zoom and screen as the warm move, so the grid step is the same: mark the cell under the
    # next center as capped BEFORE the browser ever asks for it.
    await page.evaluate("()=>void map.setView([-19.5,-45.4],13,{animate:false})")
    await page.wait_for_timeout(300)
    await settle(page)
    dense_key = tuple(await page.evaluate(CELL_AT_JS, [-19.9, -45.9]))
    fx.dense.add(dense_key)
    await page.evaluate("()=>void map.setView([-19.9,-45.9],13,{animate:false})")
    await page.wait_for_timeout(300)
    await settle(page)
    await page.wait_for_timeout(200)
    in_view = await page.evaluate(IN_VIEW_JS)
    notice = await page.evaluate("()=>document.querySelector('#rxMapState')?.textContent||''")
    rec["notice"] = notice
    assert notice == f"Mostrando {in_view} imóveis. Há mais nesta área.", (notice, in_view, dense_key)

    # 6) a failing cell: one automatic retry, quiet pending line, asked again on the next move.
    fail_key = tuple(await page.evaluate(CELL_AT_JS, [-20.3, -46.3]))
    fx.fail.add(fail_key)
    await page.evaluate("k=>void map.setView([(k[2]+.5)*k[0],(k[1]+.5)*k[0]],13,{animate:false})", list(fail_key))
    await page.wait_for_timeout(2600)
    await settle(page)
    pending = await page.evaluate("()=>document.querySelector('#rxMapState')?.textContent||''")
    tries = fx.requests.count(fail_key)
    rec["failed_cell"] = {"notice": pending, "tries": tries}
    assert tries == 2, ("failed cell must be retried exactly once automatically", tries)
    assert pending == "Parte dos imóveis desta área ainda não carregou.", pending
    fx.fail.discard(fail_key)
    await page.evaluate("()=>void map.panBy([2,0],{animate:false})")
    await page.wait_for_timeout(600)
    await settle(page)
    assert fx.requests.count(fail_key) == 3, ("failed cell not asked again after a move", fx.requests.count(fail_key))
    after = await page.evaluate("()=>document.querySelector('#rxMapState')?.textContent||''")
    assert "não carregou" not in after, after

    # 7) canvas click opens that CAR; empty click closes the card.
    await page.evaluate("()=>void map.setView([-19.2247,-45.0033],14,{animate:false})")
    await page.wait_for_timeout(400)
    await settle(page)
    pt = await page.evaluate(PARCEL_POINT_JS)
    assert pt, "no clickable parcel point on the canvas"
    await page.mouse.click(pt["x"], pt["y"])
    card = page.locator(".rx46-card")
    await card.wait_for(state="visible", timeout=6000)
    got = await card.get_attribute("data-car")
    assert got == pt["car"], ("canvas click opened another CAR", got, pt["car"])
    assert await page.locator(".rx46-selection").count() >= 1, "selected outline missing"
    await page.wait_for_timeout(500)
    OUT.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT / f"w1a-{label}-card.png"))
    empty = await page.evaluate(EMPTY_POINT_JS)
    assert empty, "no empty map point"
    await page.mouse.click(empty["x"], empty["y"])
    await card.wait_for(state="detached", timeout=2500)

    # 8) below z11 nothing is requested and parcels leave.
    n1 = len(fx.requests)
    await page.evaluate("()=>void map.setZoom(10,{animate:false})")
    await page.wait_for_timeout(700)
    assert len(fx.requests) == n1 and await page.evaluate(IN_VIEW_JS) == 0, "z<11 must stay idle and empty"
    await page.evaluate("()=>void map.setView([-19.2247,-45.0033],13,{animate:false})")
    await page.wait_for_timeout(300)
    await settle(page)
    await page.screenshot(path=str(OUT / f"w1a-{label}-map.png"))

    rec["requests"] = len(fx.requests)
    rec["errors"] = errors
    RESULTS["viewports"][label] = rec
    print("RX_W1A_SMOKE", label, json.dumps({k: v for k, v in rec.items() if k not in ("first_view",)}, ensure_ascii=False)[:900], flush=True)
    assert not errors, errors
    await ctx.close()


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for w, h in ((375, 812), (768, 1024), (1440, 900)):
            await viewport_flow(browser, w, h)
        await browser.close()
    (OUT / "w1a-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RX_W1A_BROWSER_SMOKE=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

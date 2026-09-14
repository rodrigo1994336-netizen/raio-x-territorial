"""W1a browser smoke: CAR parcels fast, progressive, canvas, no blink, honest notice.

Runs against a booted portal (RX_SMOKE_BASE, default http://127.0.0.1:8000/). The SICAR cells
are served by a deterministic in-browser fixture (page.route), so the result never depends on
SICAR answering the CI runner; the server side of the contract is covered by
scripts/w1a_imoveis_rapidos_gate.py.

Checked at 375 (touch), 768 and 1440:
  * one request per grid cell (no legacy bbox call), parcels on one canvas renderer;
  * progressive: parcels on screen while visible cells are still loading;
  * warm server (fixture answers in 20 ms): first parcels <= 1000 ms and 90% <= 1500 ms after a move;
  * hysteresis: a pan that stays inside the planned margin requests nothing, even when that pan
    moves the margin across a cell edge (premise checked, so the check cannot pass vacuously);
    no cell is requested twice;
  * no blank frame while panning or zooming with a slow server;
  * "Mostrando N imóveis. Há mais nesta área." only while a cell ALREADY LOADED and VISIBLE hit the
    cap, N = parcels in view: never carried over to a new area while it loads, never for a capped
    cell that is only in the margin, and gone as soon as no visible cell is capped. Every sample is
    judged against an independent record of the capped cells the browser actually received;
  * a cell failing twice becomes the quiet pending line and is asked again on the next move;
  * a canvas click opens the card of that CAR; an empty click closes it;
  * card data from a map cell read more than 5 min ago: no Status/Condição, and when the live
    answer fails the card says when SICAR was read; fresh cell data keeps its status.

Positive controls: `--mutation-controls` serves the real portal with one rule broken at a time
(canvas renderer removed, hysteresis off, stuck notice restored, notice ignoring the view, stale
status accepted) and requires each run to fail at the assertion tagged for that rule. A control
that passes, or fails at another tag, fails the run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

BASE = os.getenv("RX_SMOKE_BASE", "http://127.0.0.1:8000/")
OUT = Path(os.getenv("RX_SMOKE_OUT", "artifacts"))
RESULTS: dict = {"viewports": {}}
NOANIM = {"animate": False}
READY_JS = "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard')"

IN_VIEW_JS = """()=>{const b=map.getBounds();let n=0;map.eachLayer(l=>{const p=l.feature&&l.feature.properties;if(p&&p.cod_imovel&&l.getBounds){try{if(b.intersects(l.getBounds()))n++}catch(e){}}});return n}"""
SVG_PARCELS_JS = """()=>{let n=0;map.eachLayer(l=>{if(l._path&&l.feature?.properties?.cod_imovel)n++});return n}"""
CELL_AT_JS = """a=>{const s=window.rxW1aGridState().step;return [s,Math.floor(a[1]/s+1e-9),Math.floor(a[0]/s+1e-9)]}"""
BOUNDS_JS = """()=>{const b=map.getBounds();return [b.getWest(),b.getSouth(),b.getEast(),b.getNorth()]}"""
NOTICE_JS = """()=>document.querySelector('#rxMapState')?.textContent||''"""
PARCEL_POINT_JS = """()=>{const r=document.querySelector('#map').getBoundingClientRect(),top=Math.max(r.top,document.querySelector('header.top')?.getBoundingClientRect().bottom||0),c0=map.getSize();let best=null;
  map.eachLayer(l=>{const p=l.feature&&l.feature.properties;if(!p||!p.cod_imovel||!l._containsPoint)return;const c=map.latLngToContainerPoint(l.getBounds().getCenter()),x=r.left+c.x,y=r.top+c.y;
    if(x<r.left+40||x>r.right-70||y<top+80||y>r.bottom-90)return;if(!l._containsPoint(map.containerPointToLayerPoint([c.x,c.y])))return;const el=document.elementFromPoint(x,y);if(!el||el.tagName!=='CANVAS')return;
    const d=Math.hypot(c.x-c0.x/2,c.y-c0.y/2);if(!best||d<best.d)best={x,y,d,car:p.cod_imovel}});return best}"""
EMPTY_POINT_JS = """()=>{const r=document.querySelector('#map').getBoundingClientRect();for(const fy of [.35,.45,.55,.65])for(const fx of [.2,.3,.4,.5,.6,.7]){const x=r.left+r.width*fx,y=r.top+r.height*fy,el=document.elementFromPoint(x,y);
  if(!el||!el.closest('#map')||el.closest('.leaflet-popup,.leaflet-control'))continue;const lp=map.containerPointToLayerPoint([x-r.left,y-r.top]);let hit=false;map.eachLayer(l=>{if(!hit&&l.feature?.properties?.cod_imovel&&l._containsPoint){try{hit=l._containsPoint(lp)}catch(e){}}});if(!hit)return {x,y}}return null}"""

# Independent record of what the browser RECEIVED: every capped cell answer, by its own URL.
# The notice is judged against this, never against the loader's internal state.
TRUNC_RECORDER_JS = """(()=>{window.__rxW1aTrunc=[];const of=window.fetch;window.fetch=async function(input,init){const r=await of.apply(this,arguments);
  try{const u=new URL(String(input&&input.url||input),location.href);if(u.pathname.endsWith('/viewport-v46')&&u.searchParams.has('cell')&&r.ok){const d=await r.clone().json();
    if(d&&d.truncated)window.__rxW1aTrunc.push({w:+u.searchParams.get('west'),s:+u.searchParams.get('south'),e:+u.searchParams.get('east'),n:+u.searchParams.get('north')})}}catch(e){}return r}})()"""
# Samples the notice every 15 ms while a move settles. A sample saying "Há mais" is a violation
# unless a received capped cell intersects the view at that instant.
NOTICE_SAMPLER_JS = """async ({action,max})=>{const t0=performance.now();if(action)map[action.name](...action.args);const bad=[],texts=[];let quiet=null;
  while(performance.now()-t0<max){await new Promise(r=>setTimeout(r,15));const txt=document.querySelector('#rxMapState')?.textContent||'';if(!texts.includes(txt))texts.push(txt);
    if(/Há mais nesta área/.test(txt)){const b=map.getBounds(),w=b.getWest(),s=b.getSouth(),e=b.getEast(),n=b.getNorth();if(!(window.__rxW1aTrunc||[]).some(c=>c.w<e&&c.e>w&&c.s<n&&c.n>s))bad.push([Math.round(performance.now()-t0),txt])}
    const st=window.rxW1aGridState();if(!st.active&&!st.queued){if(quiet===null)quiet=performance.now();else if(performance.now()-quiet>450)break}else quiet=null}
  return {bad:bad.slice(0,4),texts:texts.slice(0,10),ms:Math.round(performance.now()-t0)}}"""
REVEAL_PAN_JS = """k=>{const s=k[0],b=map.getBounds(),sz=map.getSize(),W=b.getEast()-b.getWest(),H=b.getNorth()-b.getSouth();const cw=k[1]*s,ce=(k[1]+1)*s,cs=k[2]*s,cn=(k[2]+1)*s;let dx=0,dy=0;
  if(cw>=b.getEast())dx=(cw-b.getEast())/W*sz.x+24;else if(ce<=b.getWest())dx=-((b.getWest()-ce)/W*sz.x+24);
  if(cs>=b.getNorth())dy=-((cs-b.getNorth())/H*sz.y+24);else if(cn<=b.getSouth())dy=(b.getSouth()-cn)/H*sz.y+24;return [Math.round(dx),Math.round(dy)]}"""
CARD_JS = """()=>{const c=document.querySelector('.rx46-card');if(!c)return null;return {car:c.dataset.car,labels:[...c.querySelectorAll('.rx46-field small')].map(x=>x.textContent.trim()),
  fields:Object.fromEntries([...c.querySelectorAll('.rx46-field')].map(f=>[f.querySelector('small')?.textContent.trim(),f.querySelector('b')?.textContent.trim()])),asof:c.querySelector('.rx46-asof')?.textContent.trim()||null}}"""

# Each control breaks ONE rule in the served portal (HTML or script) and names the tag that must fail.
MUTATIONS: dict[str, dict] = {
    "svg_renderer": {"tag": "W1A_CANVAS", "edits": [("L.geoJSON(f,{renderer,style:rxParcelStyleFor", "L.geoJSON(f,{style:rxParcelStyleFor")]},
    "hysteresis_off": {"tag": "W1A_HYSTERESIS", "edits": [("if(inside&&covered&&!force){", "if(false&&inside&&covered&&!force){")]},
    "stuck_notice": {"tag": "W1A_NOTICE_MOVE", "edits": [
        ("if(trunc){setMapState(", "if(trunc||(pending&&n&&qs('#rxMapState')?.dataset.rxTruncated==='1')){setMapState("),
        ("if(pending){setMapState(n?'':'Carregando imóveis do SICAR nesta área do Brasil…',false);return}", "if(pending){if(!n)setMapState('Carregando imóveis do SICAR nesta área do Brasil…');return}"),
    ]},
    "notice_ignores_view": {"tag": "W1A_NOTICE_MARGIN", "edits": [("c.truncated&&rxW1aHits(c,bw,bs,be,bn)", "c.truncated")]},
    "stale_status_accepted": {"tag": "W1A_CARD_STALE", "edits": [("RX46_CELL_STATUS_MAX_MS=300000", "RX46_CELL_STATUS_MAX_MS=1e15")]},
}


def car_code(key: tuple, i: int) -> str:
    return "MG-3152006-" + hashlib.md5(f"{key}:{i}".encode()).hexdigest().upper()


def cells_for(step: float, w: float, s: float, e: float, n: float) -> set[tuple]:
    x0, x1 = math.floor(w / step + 1e-9), math.floor(e / step - 1e-9)
    y0, y1 = math.floor(s / step + 1e-9), math.floor(n / step - 1e-9)
    return {(step, ix, iy) for iy in range(y0, y1 + 1) for ix in range(x0, x1 + 1)}


class CellFixture:
    def __init__(self) -> None:
        self.requests: list[tuple] = []
        self.legacy: list[str] = []
        self.delay_ms = 20
        self.stagger_ms = 0
        self.stagger_from = 0
        self.fetched_offset_s = 0.0
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
        await asyncio.sleep((self.delay_ms + self.stagger_ms * max(0, order - self.stagger_from)) / 1000)
        if key in self.fail:
            await route.fulfill(status=502, json={"detail": "SICAR indisponível nesta quadrícula."})
            return
        n = 50 if key in self.dense else 9  # denseness is read at answer time, so a test may mark a cell already asked
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
                "grid": {"step": step, "ix": ix, "iy": iy, "cells": 1}, "cached": True, "fetched_at": round(time.time() - self.fetched_offset_s, 3)}
        await route.fulfill(status=200, json=body, headers={"Cache-Control": "public, max-age=600"})


class Mutator:
    """Serves the real portal document/scripts with one rule broken (positive control only)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.edits = MUTATIONS[name]["edits"]
        self.applied = [0] * len(self.edits)

    async def handle(self, route) -> None:
        if route.request.resource_type not in ("document", "script"):
            await route.fallback()
            return
        resp = await route.fetch()
        text = await resp.text()
        changed = False
        for i, (old, new) in enumerate(self.edits):
            if old in text:
                assert text.count(old) == 1, ("mutation anchor not unique", self.name, old)
                text = text.replace(old, new)
                self.applied[i] += 1
                changed = True
        if not changed:
            await route.fulfill(response=resp)
            return
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding", "etag")}
        await route.fulfill(status=resp.status, headers=headers, body=text)


async def wait_runtime(page) -> None:
    await page.wait_for_function(READY_JS, timeout=60000)
    await page.wait_for_function("window.rxV46Installed===true && typeof map!=='undefined' && !!map.getBounds", timeout=30000)


async def settle(page, timeout_ms=8000) -> None:
    end = time.monotonic() + timeout_ms / 1000
    st = None
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


async def hysteresis_plan(page, asked: set) -> dict | None:
    """A pan that keeps the view inside the planned box (so hysteresis must ask nothing) while the
    25% margin of the NEW view reaches a cell never asked (so a loader without hysteresis would ask it)."""
    st = await page.evaluate("()=>{const g=window.rxW1aGridState(),b=map.getBounds(),z=map.getSize();return {step:g.step,box:g.box,b:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()],size:[z.x,z.y]}}")
    step, box, (w, s, e, n), (sx, sy) = st["step"], st["box"], st["b"], st["size"]
    if not step or not box:
        return None
    W, H = e - w, n - s
    sides = (("E", (box[2] - e) / W * sx, (1, 0)), ("W", (w - box[0]) / W * sx, (-1, 0)),
             ("N", (box[3] - n) / H * sy, (0, -1)), ("S", (s - box[1]) / H * sy, (0, 1)))
    for side, room_px, (ux, uy) in sides:
        for px in range(2, int(room_px) - 8, 2):
            dx, dy = ux * px / sx * W, -uy * px / sy * H
            if cells_for(step, w + dx - W * 0.25, s + dy - H * 0.25, e + dx + W * 0.25, n + dy + H * 0.25) - asked:
                move = px + 4
                return {"side": side, "px": [ux * move, uy * move], "room_px": round(room_px), "step": step, "box": box}
    return None


async def viewport_flow(browser, width: int, height: int, mutation: str | None = None) -> None:
    label = str(width) + (f"-mut-{mutation}" if mutation else "")
    kw = {"viewport": {"width": width, "height": height}, "locale": "pt-BR", "service_workers": "block"}
    if width < 768:
        kw.update(is_mobile=True, has_touch=True)
    ctx = await browser.new_context(**kw)
    try:
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))
        # The fixture answers 502 on purpose in step 6; the browser logs that as a network line, not a JS error.
        page.on("console", lambda m: errors.append(f"console:{m.text[:200]}") if m.type == "error" and "status of 502" not in m.text else None)
        mut = Mutator(mutation) if mutation else None
        if mut:  # registered first = consulted last: the fixtures below answer their own URLs
            await page.route("**/*", mut.handle)
        await page.add_init_script(TRUNC_RECORDER_JS)
        fx = CellFixture()
        await page.route("**/v1/live/sicar/viewport-v46*", fx.handle)
        for pattern in ("**/v1/live/map-panel/**", "**/v1/live/snapshot/**", "**/v1/live/property-identity/**"):
            await page.route(pattern, lambda route: route.fulfill(json={"ok": False, "detail": "w1a_fixture"}))
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        if mut:
            assert all(mut.applied), ("W1A_MUTATION_NOT_APPLIED", mutation, mut.applied)
        assert not await page.evaluate("()=>!!window.rxFieldMode"), "field mode is on in the smoke browser: margin/hysteresis premises do not hold"
        await page.evaluate("()=>{map.setView([-19.2247,-45.0033],10,{animate:false})}")
        await page.wait_for_timeout(500)
        rec: dict = {}

        # 1) slow first view: progressive, canvas, cell requests only.
        fx.delay_ms, fx.stagger_ms, fx.stagger_from = 120, 45, len(fx.requests)
        first = await measure_move(page, op("setView", [-19.2247, -45.0033], 13, NOANIM), 9000)
        rec["first_view"] = first
        assert not fx.legacy, ("legacy bbox viewport request", fx.legacy[:2])
        assert fx.requests, "no cell request"
        await settle(page, 12000)
        st = await page.evaluate("()=>window.rxW1aGridState()")
        canvases = await page.locator(".leaflet-rxParcel-pane canvas").count()
        svg = await page.evaluate(SVG_PARCELS_JS)
        assert st["renderer"] == "canvas" and canvases == 1 and svg == 0, ("W1A_CANVAS parcels not on one canvas renderer", st, canvases, svg)
        assert first["progressive"], ("parcels only appeared after every cell", first)

        # 2) warm server: fixture answers in 20 ms, fresh area.
        fx.delay_ms, fx.stagger_ms = 20, 0
        warm = await measure_move(page, op("setView", [-19.5, -45.4], 13, NOANIM), 5000)
        await settle(page)
        rec["warm_move"] = warm
        assert warm["first_ms"] is not None and warm["first_ms"] <= 1000, ("first parcels after move > 1000 ms", warm)
        assert warm["t90_ms"] is not None and warm["t90_ms"] <= 1500, ("90% of parcels after move > 1500 ms", warm)

        # 3) hysteresis: small pan asks nothing; a pan inside the box that moves the margin across a cell
        #    edge asks nothing either; no cell is ever asked twice.
        n0 = len(fx.requests)
        min_small = await sample_min(page, op("panBy", [int(width * 0.1), 0], NOANIM), 900)
        rec["small_pan_new_requests"] = len(fx.requests) - n0
        assert len(fx.requests) == n0, ("W1A_HYSTERESIS 10% pan requested cells", fx.requests[n0:])
        assert min_small > 0, "blank map during small pan"
        plan = None
        for _ in range(6):
            await settle(page)
            plan = await hysteresis_plan(page, set(fx.requests))
            if plan:
                break
            await page.evaluate("()=>{const s=window.rxW1aGridState().step,c=map.getCenter();map.setView([c.lat+s*.37,c.lng+s*.37],map.getZoom(),{animate:false})}")
            await page.wait_for_timeout(300)
        assert plan, "W1A_HYST_PREMISE no pan keeps the view inside the planned box while its margin reaches an unasked cell"
        n1 = len(fx.requests)
        asked = set(fx.requests)
        await page.evaluate("p=>void map.panBy(p,{animate:false})", plan["px"])
        await page.wait_for_timeout(700)
        await settle(page)
        b = await page.evaluate(BOUNDS_JS)
        pw, ph = (b[2] - b[0]) * 0.25, (b[3] - b[1]) * 0.25
        box = plan["box"]
        premise_inside = b[0] >= box[0] and b[1] >= box[1] and b[2] <= box[2] and b[3] <= box[3]
        premise_new = cells_for(plan["step"], b[0] - pw, b[1] - ph, b[2] + pw, b[3] + ph) - asked
        rec["hysteresis_edge_pan"] = {"px": plan["px"], "unasked_cells_in_new_margin": len(premise_new), "requests": len(fx.requests) - n1}
        assert premise_inside and premise_new, ("W1A_HYST_PREMISE pan left the box or its margin reached no unasked cell", plan, b, len(premise_new))
        assert len(fx.requests) == n1, ("W1A_HYSTERESIS pan inside the planned margin requested cells", fx.requests[n1:][:4])
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
        state_plain = await page.evaluate(NOTICE_JS)
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
        notice = await page.evaluate(NOTICE_JS)
        rec["notice"] = notice
        assert notice == f"Mostrando {in_view} imóveis. Há mais nesta área.", (notice, in_view, dense_key)

        # 5b) move from the capped area to a far one while cells arrive one by one: no sample may keep
        #     the previous area's "Há mais" (checked from the first frame after the move).
        step5 = (await page.evaluate("()=>window.rxW1aGridState()"))["step"]
        fx.delay_ms, fx.stagger_ms, fx.stagger_from = 120, 25, len(fx.requests)
        moved = await page.evaluate(NOTICE_SAMPLER_JS, {"action": op("setView", [-15.60, -56.10], 13, NOANIM), "max": 12000})
        await settle(page, 12000)
        rec["notice_far_move"] = moved
        assert not moved["bad"], ("W1A_NOTICE_MOVE 'Há mais' kept or shown in an area whose visible cells are not capped", moved)
        assert "Há mais" not in await page.evaluate(NOTICE_JS), "W1A_NOTICE_MOVE notice left after the new area settled"

        # 5c) a capped cell that is only in the margin says nothing; bringing it into view shows the
        #     notice; taking it out of view clears it.
        fx.stagger_from = len(fx.requests)
        # Put the east edge of the view just short of a cell edge, so the 25% margin owns a column of
        # cells beside the view (with a coarse step the margin can otherwise be empty).
        bb = await page.evaluate(BOUNDS_JS)
        vw = bb[2] - bb[0]
        edge = math.ceil((-49.40 + vw / 2) / step5) * step5
        lng_c = edge - vw / 2 - min(0.1 * vw, 0.2 * step5)
        target = await page.evaluate("c=>{map.setView([-12.20,c],13,{animate:false});const b=map.getBounds();return [b.getWest(),b.getSouth(),b.getEast(),b.getNorth()]}", lng_c)
        w, s, e, n = target
        pw, ph = (e - w) * 0.25, (n - s) * 0.25
        visible = cells_for(step5, w, s, e, n)
        rows = {iy for _, _, iy in visible}
        margin = sorted(cells_for(step5, w - pw, s - ph, e + pw, n + ph) - visible, key=lambda k: (0 if k[2] in rows else 1, -k[1]))
        assert margin, ("W1A_NOTICE_MARGIN premise: the planned view has no margin cell", target, step5)
        margin_key = margin[0]
        fx.dense.add(margin_key)  # read at answer time; the fixture is still delaying this area's answers
        planted = await page.evaluate(NOTICE_SAMPLER_JS, {"action": None, "max": 12000})
        await settle(page, 12000)
        rec["notice_margin_only"] = planted
        got_step = (await page.evaluate("()=>window.rxW1aGridState()"))["step"]
        assert got_step == step5 and margin_key in fx.requests, ("W1A_NOTICE_MARGIN premise: margin cell not planned", got_step, step5, margin_key)
        assert any(c for c in await page.evaluate("()=>window.__rxW1aTrunc") if abs(c["w"] - margin_key[1] * step5) < 1e-6), ("W1A_NOTICE_MARGIN premise: capped margin cell never received", margin_key)
        assert not planted["bad"], ("W1A_NOTICE_MARGIN capped cell only in the margin shows the notice", planted, margin_key)
        await page.wait_for_timeout(300)
        quiet = await page.evaluate(NOTICE_JS)
        assert "Há mais" not in quiet, ("W1A_NOTICE_MARGIN capped cell only in the margin shows the notice", quiet, margin_key)
        reveal = await page.evaluate(REVEAL_PAN_JS, list(margin_key))
        shown = await page.evaluate(NOTICE_SAMPLER_JS, {"action": op("panBy", reveal, NOANIM), "max": 3000})
        await settle(page)
        await page.wait_for_timeout(250)
        in_view = await page.evaluate(IN_VIEW_JS)
        notice = await page.evaluate(NOTICE_JS)
        rec["notice_margin_revealed"] = {"pan": reveal, "notice": notice, "bad": shown["bad"]}
        assert not shown["bad"], ("W1A_NOTICE_MARGIN 'Há mais' without a visible capped cell while revealing", shown)
        assert notice == f"Mostrando {in_view} imóveis. Há mais nesta área.", ("W1A_NOTICE_VISIBLE capped cell in view but no honest notice", notice, in_view)
        hide = await page.evaluate(NOTICE_SAMPLER_JS, {"action": op("panBy", [-reveal[0], -reveal[1]], NOANIM), "max": 3000})
        await settle(page)
        gone = await page.evaluate(NOTICE_JS)
        rec["notice_margin_hidden"] = {"notice": gone, "bad": hide["bad"]}
        assert "Há mais" not in gone and not hide["bad"], ("W1A_NOTICE_MARGIN notice stays after the capped cell left the view", gone, hide)
        fx.stagger_ms = 0

        # 6) a failing cell: one automatic retry, quiet pending line, asked again on the next move.
        fx.delay_ms = 20
        fail_key = tuple(await page.evaluate(CELL_AT_JS, [-20.3, -46.3]))
        fx.fail.add(fail_key)
        await page.evaluate("k=>void map.setView([(k[2]+.5)*k[0],(k[1]+.5)*k[0]],13,{animate:false})", list(fail_key))
        await page.wait_for_timeout(2600)
        await settle(page)
        pending = await page.evaluate(NOTICE_JS)
        tries = fx.requests.count(fail_key)
        rec["failed_cell"] = {"notice": pending, "tries": tries}
        assert tries == 2, ("failed cell must be retried exactly once automatically", tries)
        assert pending == "Parte dos imóveis desta área ainda não carregou.", pending
        fx.fail.discard(fail_key)
        await page.evaluate("()=>void map.panBy([2,0],{animate:false})")
        await page.wait_for_timeout(600)
        await settle(page)
        assert fx.requests.count(fail_key) == 3, ("failed cell not asked again after a move", fx.requests.count(fail_key))
        after = await page.evaluate(NOTICE_JS)
        assert "não carregou" not in after, after

        # 7) canvas click opens that CAR (fresh cell data: status kept, no date line); empty click closes it.
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
        await page.wait_for_timeout(700)
        fresh = await page.evaluate(CARD_JS)
        rec["card_fresh"] = fresh
        assert fresh and fresh["fields"].get("Status") == "Ativo" and fresh["asof"] is None, ("W1A_CARD_FRESH fresh cell data lost its status or got a date line", fresh)
        OUT.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUT / f"w1a-{label}-card.png"))
        empty = await page.evaluate(EMPTY_POINT_JS)
        assert empty, "no empty map point"
        await page.mouse.click(empty["x"], empty["y"])
        await card.wait_for(state="detached", timeout=2500)

        # 7b) cell data read by SICAR 1 h ago (server cache) and the live answer fails: no status/condition,
        #     the remaining fields say when SICAR was read.
        fx.fetched_offset_s = 3600
        stale_t = time.time() - 3600
        await page.evaluate("()=>void map.setView([-19.62,-44.62],14,{animate:false})")
        await page.wait_for_timeout(400)
        await settle(page)
        pt = await page.evaluate(PARCEL_POINT_JS)
        assert pt, "no clickable parcel point on the canvas (stale area)"
        await page.mouse.click(pt["x"], pt["y"])
        await card.wait_for(state="visible", timeout=6000)
        try:
            await page.locator(".rx46-card .rx46-asof").wait_for(state="visible", timeout=4000)
        except Exception:
            pass
        stale = await page.evaluate(CARD_JS)
        want_asof = await page.evaluate("t=>{const d=new Date(t*1000),z=n=>String(n).padStart(2,'0');return [`${z(d.getDate())}/${z(d.getMonth()+1)}/${d.getFullYear()}`,d.getHours(),d.getMinutes()]}", stale_t)
        rec["card_stale"] = stale
        assert stale and stale["car"] == pt["car"], ("stale click opened another CAR", stale, pt)
        assert "Status" not in stale["labels"] and "Condição" not in stale["labels"], ("W1A_CARD_STALE status/condition from a 1 h old cell shown as current", stale)
        asof = stale["asof"] or ""
        assert asof.startswith(f"Dados do SICAR de {want_asof[0]} às ") and "Área" in stale["labels"], ("W1A_CARD_STALE_DATE card without the SICAR read date", stale, want_asof)
        hh, mm = (int(x) for x in asof.rsplit(" às ", 1)[1].split(":"))
        assert abs((hh * 60 + mm) - (want_asof[1] * 60 + want_asof[2])) <= 1, ("W1A_CARD_STALE_DATE wrong read time", asof, want_asof)
        await page.wait_for_timeout(500)  # let the popup fade-in finish, so the evidence shows the real contrast
        await page.screenshot(path=str(OUT / f"w1a-{label}-card-stale.png"))
        await page.evaluate("()=>window.rxV46CloseAnchor&&window.rxV46CloseAnchor()")
        fx.fetched_offset_s = 0

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
        print("RX_W1A_SMOKE", label, json.dumps({k: v for k, v in rec.items() if k not in ("first_view",)}, ensure_ascii=False)[:1400], flush=True)
        assert not errors, errors
    finally:
        await ctx.close()


async def mutation_controls(browser) -> None:
    outcomes, evidence = {}, {}
    for name, spec in MUTATIONS.items():
        try:
            await viewport_flow(browser, 1440, 900, mutation=name)
            outcomes[name] = "PASSED (rule not enforced)"
        except AssertionError as exc:
            text = str(exc)
            evidence[name] = text[:260]
            outcomes[name] = "caught" if spec["tag"] in text else f"red at another assertion: {text[:220]}"
        except Exception as exc:  # a timeout or crash is not evidence that THIS rule is guarded
            outcomes[name] = f"crashed: {type(exc).__name__}: {str(exc)[:220]}"
        print("RX_W1A_MUTATION", name, spec["tag"], outcomes[name], "|", evidence.get(name, "")[:200], flush=True)
    RESULTS["mutations"] = outcomes
    RESULTS["mutation_evidence"] = evidence
    missed = {k: v for k, v in outcomes.items() if v != "caught"}
    assert not missed, ("W1a mutation controls not caught", missed)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    controls = "--mutation-controls" in sys.argv
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        if controls:
            await mutation_controls(browser)
        else:
            for w, h in ((375, 812), (768, 1024), (1440, 900)):
                await viewport_flow(browser, w, h)
        await browser.close()
    name = "w1a-mutations.json" if controls else "w1a-results.json"
    (OUT / name).write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RX_W1A_MUTATION_CONTROLS=PASS" if controls else "RX_W1A_BROWSER_SMOKE=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

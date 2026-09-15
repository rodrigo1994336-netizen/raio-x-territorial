from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

BASE = os.getenv("RX_SMOKE_BASE", "http://127.0.0.1:8000/")
OUT = Path("artifacts")
DENSE = {"lat": -18.4448863, "lon": -44.2181254, "zoom": 13}
results = {"viewports": {}, "movements": [], "console": []}


async def js(page, expr, arg=None):
    if arg is None:
        return await page.evaluate(expr)
    return await page.evaluate(expr, arg)


async def wait_runtime(page):
    # Wait for the complete page (W1a: no reload once ready; a cold server shows the
    # boot page first and reloads once). Do not type into a document about to be replaced.
    await page.wait_for_function("(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard')", timeout=15000)
    await page.wait_for_function("window.rxV46Installed===true", timeout=15000)
    await page.wait_for_function(
        "typeof map!=='undefined' && !!map && !!map.getBounds", timeout=10000
    )


# W1a: parcels may be drawn by a canvas renderer (no <path> per parcel). Every probe below
# reads Leaflet layers carrying a CAR code, so it works for SVG and canvas alike.
PARCELS_IN_VIEW_JS = """()=>{if(typeof map==='undefined'||!map)return 0;const b=map.getBounds();let n=0;map.eachLayer(l=>{const p=l.feature&&l.feature.properties;if(p&&p.cod_imovel&&l.getBounds){try{if(b.intersects(l.getBounds()))n++}catch(e){}}});return n}"""


async def set_dense(page):
    await js(page, "p=>map.setView([p.lat,p.lon],p.zoom,{animate:false})", DENSE)
    await page.wait_for_function(f"({PARCELS_IN_VIEW_JS})()>0", timeout=25000)


async def map_center(page):
    return await js(page, "()=>{const c=map.getCenter();return [c.lat,c.lng,map.getZoom()]}")


async def visible_parcels(page):
    return await js(page, PARCELS_IN_VIEW_JS)


async def proven_empty_map_point(page):
    return await js(
        page,
        """()=>{
          const mapEl=document.querySelector('#map'),r=mapEl?.getBoundingClientRect();
          if(!r)return null;
          // W1a: with every CAR drawn, bare pixels are rarer; scan a finer grid (coarse points first).
          const fx=[.08,.16,.24,.32,.40,.48,.56,.64,.72,.80,.88,.94,.04,.12,.20,.28,.36,.44,.52,.60,.68,.76,.84,.92];
          const fy=[.12,.22,.32,.42,.52,.62,.72,.82,.90,.17,.27,.37,.47,.57,.67,.77,.86];
          for(const yy of fy)for(const xx of fx){
            const x=r.left+r.width*xx,y=r.top+r.height*yy,el=document.elementFromPoint(x,y);
            if(!el||!el.closest('#map'))continue;
            if(el.closest('.leaflet-popup,.leaflet-control')||(el.tagName!=='CANVAS'&&el.closest('.leaflet-interactive')))continue; // a canvas keeps .leaflet-interactive while hovered; parcels are excluded below
            const lp=map.containerPointToLayerPoint([x-r.left,y-r.top]);let onParcel=false;
            const ll=map.containerPointToLatLng([x-r.left,y-r.top]);map.eachLayer(l=>{if(!onParcel&&l.feature?.properties?.cod_imovel&&l._containsPoint&&l.getBounds){try{onParcel=l.getBounds().contains(ll)&&l._containsPoint(lp)}catch(e){}}});
            if(onParcel)continue;
            return {x,y,tag:el.tagName,id:el.id||null,cls:String(el.className||'')};
          }
          return null;
        }""",
    )


# Parcel click (CI flake of 15/09: "no interactive CAR polygon to click" at 1440 px with 17 parcels).
# The old probe only tried the CENTRE of each parcel's bounding box, inside fixed margins, right after
# the FIRST parcel touched the view. When the cells that arrived first hold parcels that merely stick
# into the view (edge or margin cells, SICAR slow for the runner), or the parcel is concave, no centre
# qualifies and the smoke failed although clickable parcels were on screen.
# Now the target is read from the geometry the map really has: a grid over the free map area (same
# margins as before), where each pixel must be covered by nothing but the parcel canvas/path, and the
# parcel under it is the one Leaflet will receive the click (canvas: LAST drawn interactive layer
# containing the point, exactly like L.Canvas._onClick). The 4 neighbours at PAD px must hit the same
# parcel, so rounding cannot move the click to a border or to a neighbour. Pixels with a single
# parcel are preferred. No pixel yet: wait for cells; still none: pan to a loaded parcel's interior.
# The click must open the card of THAT CAR (tags below name each failure).
PARCEL_BOX = {"l": 30, "r": 60, "t": 60, "b": 70, "step": 18, "pad": 5, "want": 4}
PARCEL_TARGET_JS = """(o)=>{
  const mapEl=document.querySelector('#map');if(!mapEl||typeof map==='undefined'||!map)return {cands:[],parcels:0,stats:{no_map:true}};
  const r=mapEl.getBoundingClientRect(),top=Math.max(r.top,document.querySelector('header.top')?.getBoundingClientRect().bottom||0);
  const box={l:r.left+o.l,r:r.right-o.r,t:top+o.t,b:r.bottom-o.b},parcels=[],byPath=new Map(),rends=new Map();
  map.eachLayer(l=>{if(!l.feature?.properties?.cod_imovel||!l._containsPoint||!l.options?.interactive)return;parcels.push(l);
    if(l._path)byPath.set(l._path,l);else if(l._renderer?._container)rends.set(l._renderer._container,l._renderer)});
  const tag=el=>el.tagName.toLowerCase()+(el.id?'#'+el.id:'')+(typeof el.className==='string'&&el.className.trim()?'.'+el.className.trim().split(/\\s+/)[0]:'');
  const probe=(x,y)=>{const el=document.elementFromPoint(x,y);if(!el)return {why:'offscreen'};const lp=map.containerPointToLayerPoint(L.point(x-r.left,y-r.top));
    if(byPath.has(el))return {target:byPath.get(el),alone:parcels.filter(l=>l._containsPoint(lp)).length===1};
    const rend=rends.get(el);if(!rend)return parcels.some(l=>l._containsPoint(lp))?{why:'covered',by:tag(el)}:{why:'empty'};
    let hit=null,n=0;for(let q=rend._drawFirst;q;q=q.next){const l=q.layer;if(l.options.interactive&&l._containsPoint(lp)){hit=l;n++}}
    if(!hit)return {why:'empty'};if(!hit.feature?.properties?.cod_imovel)return {why:'covered',by:'non-parcel layer'};return {target:hit,alone:n===1}};
  const carOf=l=>l.feature.properties.cod_imovel;
  if(o.at){const p=probe(o.at[0],o.at[1]);return {car:p.target?carOf(p.target):null,why:p.why||null,by:p.by||null,parcels:parcels.length}}
  const stats={parcels:parcels.length,points:0,empty:0,edge:0,covered:{}},cx=(box.l+box.r)/2,cy=(box.t+box.b)/2,pts=[];
  for(let y=box.t;y<=box.b;y+=o.step)for(let x=box.l;x<=box.r;x+=o.step)pts.push([x,y]);
  pts.sort((a,b)=>Math.hypot(a[0]-cx,a[1]-cy)-Math.hypot(b[0]-cx,b[1]-cy));
  const alone=[],shared=[],seen=new Set();
  for(const [x,y] of pts){if(alone.length>=o.want)break;stats.points++;const p=probe(x,y);
    if(!p.target){if(p.why==='covered')stats.covered[p.by]=(stats.covered[p.by]||0)+1;else stats.empty++;continue}
    const car=carOf(p.target);if(seen.has(car))continue;let ok=true,solo=p.alone;
    for(const [dx,dy] of [[-o.pad,0],[o.pad,0],[0,-o.pad],[0,o.pad]]){const q=probe(x+dx,y+dy);if(q.target!==p.target){ok=false;break}solo=solo&&q.alone}
    if(!ok){stats.edge++;continue}seen.add(car);(solo?alone:shared).push({x,y,car,alone:solo})}
  return {cands:alone.concat(shared).slice(0,o.want),parcels:parcels.length,stats}}"""
# Pan fallback: the loaded interactive parcel nearest to the free area, moved so that an interior
# pixel (neighbours at 2*PAD inside too) lands in the middle of that area.
PARCEL_PAN_JS = """(o)=>{const mapEl=document.querySelector('#map');if(!mapEl)return null;const r=mapEl.getBoundingClientRect(),top=Math.max(r.top,document.querySelector('header.top')?.getBoundingClientRect().bottom||0);
  const cx=(r.left+o.l+r.right-o.r)/2-r.left,cy=(top+o.t+r.bottom-o.b)/2-r.top,skip=new Set(o.skip||[]),rows=[];
  map.eachLayer(l=>{const car=l.feature?.properties?.cod_imovel,b=l._pxBounds;if(!car||skip.has(car)||!l._containsPoint||!l.options?.interactive||!b)return;
    if(b.max.x-b.min.x<4*o.pad||b.max.y-b.min.y<4*o.pad)return;const c=map.layerPointToContainerPoint(b.getCenter());rows.push({l,car,d:Math.hypot(c.x-cx,c.y-cy)})});
  rows.sort((a,b)=>a.d-b.d);
  for(const {l,car} of rows.slice(0,60)){const b=l._pxBounds,mid=b.getCenter();let best=null;
    for(let i=1;i<12;i++)for(let j=1;j<12;j++){const p=L.point(b.min.x+(b.max.x-b.min.x)*i/12,b.min.y+(b.max.y-b.min.y)*j/12);
      if(!l._containsPoint(p)||![[-1,0],[1,0],[0,-1],[0,1]].every(([dx,dy])=>l._containsPoint(L.point(p.x+dx*2*o.pad,p.y+dy*2*o.pad))))continue;
      const d=p.distanceTo(mid);if(!best||d<best.d)best={p,d}}
    if(best){const c=map.layerPointToContainerPoint(best.p);return {car,dx:Math.round(c.x-cx),dy:Math.round(c.y-cy)}}}
  return null}"""


async def click_first_parcel(page, timeout_ms=30000, pan_after_ms=6000):
    """Clicks a pixel proven to belong to one visible interactive CAR parcel and requires the card of
    that CAR. Returns the click, with the map centre read right before it (card contract)."""
    assert await page.locator(".rx46-card").count() == 0, "click_first_parcel expects no open card"
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    deadline, next_pan = t0 + timeout_ms / 1000, t0 + pan_after_ms / 1000
    pans, races = [], 0
    while True:
        found = await js(page, PARCEL_TARGET_JS, PARCEL_BOX)
        if found["cands"]:
            cand = found["cands"][0]
            center = await map_center(page)
            await page.mouse.click(cand["x"], cand["y"])
            try:
                await page.wait_for_selector(".rx46-card", state="visible", timeout=5000)
            except Exception:
                now = await js(page, PARCEL_TARGET_JS, {**PARCEL_BOX, "at": [cand["x"], cand["y"]]})
                raise AssertionError(("CLICK_NO_CARD: a pixel of a visible interactive CAR parcel opened no card", cand, now)) from None
            got = (await page.locator(".rx46-card").get_attribute("data-car") or "").strip()
            if got == cand["car"]:
                info = {**cand, "center": center, "waited_ms": round((loop.time() - t0) * 1000), "pans": pans, "races": races, "parcels": found["parcels"]}
                print("RX_V46_PARCEL_CLICK", json.dumps(info, ensure_ascii=False))
                return info
            now = await js(page, PARCEL_TARGET_JS, {**PARCEL_BOX, "at": [cand["x"], cand["y"]]})
            # Only accepted mismatch: a cell that arrived between the probe and the click drew the card's
            # parcel on top of that pixel (the parcel set changed and the pixel now belongs to it).
            if now["car"] == got and now["parcels"] != found["parcels"] and races < 2 and loop.time() < deadline:
                races += 1
                await js(page, "()=>window.rxV46CloseAnchor?.()")
                await page.locator(".rx46-card").wait_for(state="detached", timeout=1500)
                continue
            raise AssertionError(("CLICK_OTHER_CAR: the click opened the card of another property", cand, got, now))
        tick = loop.time()
        if tick >= deadline:
            grid = await js(page, "()=>window.rxW1aGridState?window.rxW1aGridState():null")
            raise AssertionError(("NO_CLICKABLE_PARCEL: no pixel of a visible interactive CAR parcel could be clicked",
                                  {"stats": found["stats"], "grid": grid, "pans": pans, "in_view": await visible_parcels(page)}))
        if tick >= next_pan and len(pans) < 3:
            pan = await js(page, PARCEL_PAN_JS, {**PARCEL_BOX, "skip": [p["car"] for p in pans]})
            if pan:
                await js(page, "d=>map.panBy([d.dx,d.dy],{animate:false})", pan)
                pans.append(pan)
            next_pan = tick + 3
        await page.wait_for_timeout(400)


AREA_RE = re.compile(r"(\d{1,3}(\.\d{3})*,\d{2}|< 0,01) ha")
NUMBER_RE = re.compile(r"\d{1,3}(\.\d{3})*,\d{2}|< 0,01")
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
PLACEHOLDERS = ("—", "não informad", "situação informada", "tipo informado", "m²")

# Reads the copy control, its legibility and the 2-column grids without assuming
# which optional fields SICAR returned (the /map-panel enrichment may fail in CI).
CONTROL_JS = """(root)=>{
  const scope=document.querySelector(root);if(!scope)return {missing:true};
  const lum=c=>{const m=String(c).match(/[\\d.]+/g)||[0,0,0];const f=v=>{v=Number(v)/255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(m[0])+.7152*f(m[1])+.0722*f(m[2])};
  const bg=el=>{for(let x=el;x;x=x.parentElement){const c=getComputedStyle(x).backgroundColor,m=String(c).match(/[\\d.]+/g)||[];if(m.length>=3&&(m.length<4||Number(m[3])>.5))return c}return 'rgb(7,21,15)'};
  const ctl=[...scope.querySelectorAll('[data-rx-copy-car]')];
  const c=ctl[0],cs=c&&getComputedStyle(c),r=c&&c.getBoundingClientRect();
  const l1=c?lum(cs.color):0,l2=c?lum(bg(c)):0,contrast=c?(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05):0;
  const grids=[...scope.querySelectorAll('.rx46-grid,.rx45-grid')].map(g=>{const items=[...g.children];const last=items[items.length-1];return {count:items.length,lastSpans:!last||items.length%2===0||Math.abs(last.getBoundingClientRect().width-g.getBoundingClientRect().width)<2}});
  return {missing:false,count:ctl.length,car:c?.dataset.rxCopyCar||'',fontSize:c?parseFloat(cs.fontSize):0,height:r?r.height:0,contrast,grids};
}"""

# Visible title text without the transient copy feedback overlay that sits on the code control.
TITLE_JS = """sel=>{const h=document.querySelector(sel);if(!h)return '';const c=h.cloneNode(true);c.querySelectorAll('[data-rx-copy-feedback]').forEach(x=>x.remove());return String(c.textContent||'').replace(/\\s+/g,' ').trim()}"""


async def assert_copy_control(page, root, car, width):
    info = await page.evaluate(CONTROL_JS, root)
    assert not info.get("missing"), (root, info)
    assert info["count"] == 1 and info["car"] == car, (root, car, info)
    assert info["fontSize"] >= 10, (root, info)
    assert info["contrast"] >= 4.5, (root, info)
    if width <= 720:
        assert info["height"] >= 44, (root, info)
    assert all(g["lastSpans"] for g in info["grids"]), (root, info)
    return info


def assert_title(title, car, named):
    assert title and title.casefold() != "imóvel rural", title
    assert not re.fullmatch(r".+ - [A-Z]{2}", title), title
    if not named:
        assert title == car, (title, car)


async def assert_card_contract(page, center_before, width):
    await page.wait_for_selector(".rx46-card", state="visible", timeout=6000)
    await page.wait_for_timeout(700)
    center_after = await map_center(page)
    assert abs(center_before[0] - center_after[0]) < 1e-9 and abs(
        center_before[1] - center_after[1]
    ) < 1e-9, (center_before, center_after)
    # Everything below reads the card after the /map-panel enrichment settled
    # (success or failure), so a re-render cannot detach nodes mid-measurement.
    await settle_map_panel(page)
    card = page.locator(".rx46-card")
    text = await card.inner_text()
    folded = text.casefold()
    for required in (
        "CAR",
        "Mapa KML",
        "Consultar no SICAR (site oficial)",
        "VER ANÁLISE COMPLETA",
    ):
        assert required.casefold() in folded, (required, text)
    assert "consultar demonstrativo car" not in folded, text
    for forbidden in (
        "RISCO NÃO CLASSIFICADO",
        "FONTES RESPONDERAM",
        "gerar PDF",
    ) + PLACEHOLDERS:
        assert forbidden.casefold() not in folded, (forbidden, text)
    assert await page.locator(".rx46-anchor-popup .leaflet-popup-tip").count() == 1
    box = await page.evaluate("()=>{const r=document.querySelector('.rx46-card')?.getBoundingClientRect();return r?{x:r.x,y:r.y,width:r.width,height:r.height}:null}")
    assert box and 195 <= box["width"] <= 235, box
    assert await page.locator(".rx45-panel-card").count() == 0, "V45 opened before CTA"
    car = (await card.get_attribute("data-car") or "").strip()
    named = (await card.get_attribute("data-rx-named") or "") == "1"
    title = await page.evaluate(TITLE_JS, ".rx46-card .rx46-title")
    assert_title(title, car, named)
    assert text.count(car) == 1, ("the CAR code must appear exactly once", text)
    fields = {}
    for raw in await page.locator(".rx46-field").all_inner_texts():
        parts = [x.strip() for x in raw.split("\n") if x.strip()]
        assert len(parts) >= 2, ("empty field rendered", raw)
        fields[parts[0].casefold()] = parts[-1]
    if "área" in fields:
        assert AREA_RE.fullmatch(fields["área"]), fields
    if "módulos fiscais" in fields:
        assert NUMBER_RE.fullmatch(fields["módulos fiscais"]), fields
    date_fields = [v for k, v in fields.items() if k in ("criação", "atualização")]
    assert len(date_fields) <= 2, fields
    for val in date_fields:
        assert DATE_RE.fullmatch(val), fields
    if await page.locator(".rx46-status").count():
        status = (await page.locator(".rx46-status").inner_text()).strip()
        assert status not in {"AT", "PE", "CA", "SU", "IN"}, status
    type_text = " ".join(await page.locator(".rx46-field").all_inner_texts())
    assert "\nIRU" not in type_text, type_text
    await assert_copy_control(page, ".rx46-card", car, width)
    return {"title": title, "text": text, "box": box}


async def reveal_in_map(page, selector):
    # The anchored card grows upward from the clicked point and may sit under the
    # top bar (placement is C2c). Pan the map, never the page, so the control is
    # reachable; the map-centre contract was already checked right after the click.
    delta = await js(page, """sel=>{const el=document.querySelector(sel),m=document.querySelector('#map');if(!el||!m||!el.closest('.leaflet-popup'))return 0;
      const r=el.getBoundingClientRect(),mr=m.getBoundingClientRect(),top=Math.max(mr.top,document.querySelector('header.top')?.getBoundingClientRect().bottom||0);
      return r.top<top+8?Math.ceil(top+8-r.top):0}""", selector)
    if delta:
        await js(page, "d=>map.panBy([0,-d],{animate:false})", delta)
        await page.wait_for_timeout(150)


MAP_PANEL_TRAFFIC = {}


def track_map_panel(page):
    """Counts /v1/live/map-panel requests so checks can wait for the card enrichment."""
    state = {"started": 0, "done": 0}

    def bump(key, req):
        if "/v1/live/map-panel/" in req.url:
            state[key] += 1

    page.on("request", lambda req: bump("started", req))
    page.on("requestfinished", lambda req: bump("done", req))
    page.on("requestfailed", lambda req: bump("done", req))
    MAP_PANEL_TRAFFIC[id(page)] = state


async def settle_map_panel(page, timeout_ms=15000):
    # The enrichment may legitimately fail in CI (GitHub cannot always reach SICAR):
    # wait for it to finish either way, bounded, then let the re-render run.
    state = MAP_PANEL_TRAFFIC.get(id(page))
    if not state:
        return
    waited = 0
    while state["done"] < state["started"] and waited < timeout_ms:
        await page.wait_for_timeout(100)
        waited += 100
    await page.wait_for_timeout(250)


# Click, feedback and position are read inside ONE evaluate with fresh queries: the V45
# settle timers and the V46 enrichment replace these nodes, and only microtasks (the
# mocked clipboard promise chain) run between the reads, never a timer or a fetch task.
COPY_JS = """async ({root, mode})=>{
  const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve()};
  const scope=()=>document.querySelector(root);
  const ctl=()=>document.querySelector(root+' [data-rx-copy-car]');
  const s=scope(),b=ctl();if(!s||!b)return {missing:true};
  const car=String(s.dataset.car||'').trim();
  const r0=b.getBoundingClientRect(),hit=document.elementFromPoint(r0.left+r0.width/2,r0.top+r0.height/2);
  const reachable=!!hit&&hit.closest('[data-rx-copy-car]')===b;
  window.__rxExecCommand=window.__rxExecCommand||document.execCommand.bind(document);
  window.__rxCopied=[];
  const ok={configurable:true,value:{writeText:(v)=>{window.__rxCopied.push(String(v));return Promise.resolve()}}};
  if(mode==='ok'){
    document.execCommand=window.__rxExecCommand;
    Object.defineProperty(navigator,'clipboard',ok);
    b.click();await flush();
    const c=ctl();
    return {car,reachable,copied:[...window.__rxCopied],text:scope()?.innerText||'',y0:r0.top,y1:c?c.getBoundingClientRect().top:null};
  }
  document.execCommand=()=>false;
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});
  b.click();await flush();
  const f=scope()?.querySelector('[data-rx-copy-fallback]'),fr=f?.getBoundingClientRect(),c=ctl(),cr=c?.getBoundingClientRect();
  const out={car,reachable,body:document.body.innerText,copied:[...window.__rxCopied],
    fallback:f?String(f.innerText||'').replace(/\\s+/g,''):'',
    visible:!!f&&f.getClientRects().length>0&&fr.height>0,
    notCut:!!f&&f.scrollWidth<=f.clientWidth+1&&f.scrollHeight<=f.clientHeight+1,
    covers:!!fr&&!!cr&&Math.abs(fr.top-cr.top)<2&&fr.height>=cr.height-1,
    userSelect:f?(getComputedStyle(f).userSelect||getComputedStyle(f).webkitUserSelect):'',
    isInput:!!f&&f.tagName==='INPUT',y0:r0.top,y1:cr?cr.top:null};
  // A tap on the fallback retries the copy.
  if(f){Object.defineProperty(navigator,'clipboard',ok);document.execCommand=window.__rxExecCommand;f.click();await flush();out.retryCopied=[...window.__rxCopied]}
  document.execCommand=window.__rxExecCommand;
  return out;
}"""


async def assert_car_copy(page, root):
    """One tap on the code copies it; the UI claims 'copiado' only after a real success."""
    await settle_map_panel(page)
    await reveal_in_map(page, f"{root} [data-rx-copy-car]")
    ok = await page.evaluate(COPY_JS, {"root": root, "mode": "ok"})
    assert not ok.get("missing"), (root, ok)
    car = ok["car"]
    assert car and ok["reachable"], (root, ok)
    assert car in ok["copied"], (car, ok)
    assert "copiado" in ok["text"].casefold(), ok
    # Feedback must not move the control away from the finger.
    assert ok["y1"] is not None and abs(ok["y0"] - ok["y1"]) < 2, ok
    failed = await page.evaluate(COPY_JS, {"root": root, "mode": "fail"})
    assert not failed.get("missing") and failed["reachable"], (root, failed)
    assert not failed["copied"], failed
    assert "copiado" not in failed["body"].casefold(), ("copy failed but the UI claims success", failed["body"][:600])
    assert failed["visible"] and failed["fallback"] == car, failed
    assert failed["notCut"] and failed["covers"] and not failed["isInput"], failed
    assert failed["userSelect"] == "all", failed
    assert failed["y1"] is not None and abs(failed["y0"] - failed["y1"]) < 2, failed
    assert car in failed.get("retryCopied", []), failed


async def assert_official_sicar_action(page):
    car = (await page.locator(".rx46-card").get_attribute("data-car") or "").strip()
    assert car, "selected card has no CAR code"
    await settle_map_panel(page)
    await reveal_in_map(page, '[data-rx46-action="demo"]')
    # A failed copy may still open the official site, but it must never claim 'copiado'.
    await page.evaluate(
        """()=>{
          window.__rx46Opened=[];
          window.__rx46Copied=[];
          window.open=(url,target,features)=>{window.__rx46Opened.push({url:String(url),target:String(target||''),features:String(features||'')});return null};
          window.__rxExecCommand=window.__rxExecCommand||document.execCommand.bind(document);
          document.execCommand=()=>false;
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});
        }"""
    )
    await page.locator('[data-rx46-action="demo"]').click()
    await page.wait_for_timeout(250)
    probe = await page.evaluate("()=>({opened:window.__rx46Opened||[],body:document.body.innerText})")
    assert probe["opened"] and probe["opened"][-1]["url"] == "https://consulta.car.gov.br/", probe["opened"]
    assert "copiado" not in probe["body"].casefold(), probe["body"][:600]
    await page.evaluate(
        """()=>{
          document.execCommand=window.__rxExecCommand;
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:(value)=>{window.__rx46Copied.push(String(value));return Promise.resolve()}}});
        }"""
    )
    await page.locator('[data-rx46-action="demo"]').click()
    await page.wait_for_timeout(120)
    probe = await page.evaluate(
        "()=>({opened:window.__rx46Opened||[],copied:window.__rx46Copied||[]})"
    )
    assert probe["opened"] and probe["opened"][-1]["url"] == "https://consulta.car.gov.br/", probe
    assert probe["opened"][-1]["target"] == "_blank", probe
    assert car in probe["copied"], (car, probe)


async def open_full(page, width):
    await page.locator('[data-rx46-action="full"]').click()
    await page.wait_for_selector(".rx45-panel-card", state="visible", timeout=10000)
    # V46 intentionally schedules normalization at 70/220/650/1300 ms so the
    # final pass sees asynchronously rendered V45 content. Validate after that
    # final product pass rather than against an earlier intermediate frame.
    await page.wait_for_timeout(1450)
    assert await page.locator(".rx46-card").count() == 0
    panel = page.locator(".rx45-panel-card")
    text = await panel.inner_text()
    # F1B: no internal wording in the client panel (no risk placeholder, no "N de M fontes responderam").
    for internal in ("RISCO NÃO CLASSIFICADO", "Fonte não consultada não significa ausência de ocorrência", "fontes responderam"):
        assert internal.casefold() not in text.casefold(), (internal, text)
    assert "ver fontes e datas" in text.casefold(), text
    assert "VER ANÁLISE COMPLETA" in text
    assert not re.search(r"20\d{2}-\d{2}-\d{2}T\d{2}:", text), text
    # C2a: clean panel head and KPIs. Compliance rows carry their own labels (C3),
    # so the placeholder scan is limited to the identity head, KPIs and Cadastro rows.
    assert "m²" not in text and "datas não informadas" not in text.casefold(), text
    car = (await panel.get_attribute("data-car") or "").strip()
    named = (await panel.get_attribute("data-rx-named") or "") == "1"
    title = await page.evaluate(TITLE_JS, ".rx45-panel-card .rx45-title h2")
    assert_title(title, car, named)
    top = await page.locator(".rx45-top").inner_text()
    assert top.count(car) == 1, ("the CAR code must appear exactly once in the panel head", top)
    values = await page.locator(".rx45-top, .rx45-kpi b, .rx45-row span").all_inner_texts()
    for value in values:
        for placeholder in PLACEHOLDERS:
            assert placeholder not in value.casefold(), (placeholder, value)
    kpis = {}
    for raw in await page.locator(".rx45-kpi").all_inner_texts():
        parts = [x.strip() for x in raw.split("\n") if x.strip()]
        assert len(parts) >= 2, ("empty KPI rendered", raw)
        kpis[parts[0].casefold()] = parts[-1]
    if "área car" in kpis:
        assert AREA_RE.fullmatch(kpis["área car"]), kpis
    if "módulos fiscais" in kpis:
        assert NUMBER_RE.fullmatch(kpis["módulos fiscais"]), kpis
    assert await page.locator(".rx45-panel-card .rx46-code-mini").count() == 0
    await assert_copy_control(page, ".rx45-panel-card", car, width)
    return text


SNAPSHOT_DELAY_S = 2.6  # between the V45 settle repaints at 1800 and 9500 ms


async def delay_snapshot(page):
    """Answers V43's /v1/live/snapshot late, after the V45 panel is already on screen."""
    late = {"answered": False}

    async def handler(route):
        await asyncio.sleep(SNAPSHOT_DELAY_S)
        car = route.request.url.split("/v1/live/snapshot/", 1)[-1].split("?", 1)[0]
        try:
            await route.fulfill(json={"car_code": car, "public_name_state": "confirmed", "main_signals": []})
        finally:
            late["answered"] = True

    await page.route("**/v1/live/snapshot/**", handler)
    return late


async def assert_late_snapshot_keeps_panel(page, late):
    waited = 0
    while not late["answered"] and waited < 12000:
        await page.wait_for_timeout(100)
        waited += 100
    assert late["answered"], "the delayed V43 snapshot was never requested"
    await page.wait_for_timeout(200)
    state = await js(page, """()=>{const h=document.querySelector('#rx43SnapshotHost');return {panel:!!h?.querySelector('.rx45-panel-card'),snapshot:!!h?.querySelector('#rx43Full')}}""")
    assert state["panel"] and not state["snapshot"], ("a late V43 snapshot replaced the V45 panel", state)
    await page.unroute("**/v1/live/snapshot/**")


PLACEMENT_JS = r"""()=>{const pop=document.querySelector('.rx46-anchor-popup .leaflet-popup-content-wrapper');const mr=document.querySelector('#map')?.getBoundingClientRect();
  const sels=[...document.querySelectorAll('.rx46-selection')].map(e=>e.getBoundingClientRect()).filter(r=>r.width>0&&r.height>0);
  if(!pop||!mr||!sels.length)return null;const w=pop.getBoundingClientRect();
  const u=sels.reduce((a,r)=>({l:Math.min(a.l,r.left),t:Math.min(a.t,r.top),r:Math.max(a.r,r.right),b:Math.max(a.b,r.bottom)}),{l:1e9,t:1e9,r:-1e9,b:-1e9});
  const ix=Math.max(0,Math.min(w.right,u.r)-Math.max(w.left,u.l)),iy=Math.max(0,Math.min(w.bottom,u.b)-Math.max(w.top,u.t));
  return {placement:window.__rx46Placement||null,card:{l:w.left,t:w.top,r:w.right,b:w.bottom},sel:u,overlap_px:Math.round(ix*iy),map:{l:mr.left,t:mr.top,r:mr.right,b:mr.bottom}}}"""

TEXT_FIT_JS = r"""()=>{const q=document.querySelector('#q');if(!q)return null;const cs=getComputedStyle(q);const ctx=document.createElement('canvas').getContext('2d');
  ctx.font=`${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;const w=q.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
  const st=document.querySelector('#rxMapState');
  return {placeholder:q.placeholder,text_px:ctx.measureText(q.placeholder).width,box_px:w,
    state:st?{text:st.innerText,sw:st.scrollWidth,cw:st.clientWidth,visible:getComputedStyle(st).display!=='none'&&!!(st.textContent||'').trim()}:null}}"""


async def assert_card_placement(page, label, inside_map):
    # C2c: the card never covers the clicked property unless no side fits (then it is marked 'overlap').
    probe = await js(page, PLACEMENT_JS)
    assert probe and probe["placement"], (label, "placement_not_run", probe)
    print("RX_C2C_PLACEMENT", label, json.dumps({"side": probe["placement"]["side"], "overlap_px": probe["overlap_px"], "inside_map": inside_map}))
    c, m = probe["card"], probe["map"]
    if inside_map:
        assert c["l"] >= m["l"] - 1 and c["r"] <= m["r"] + 1 and c["t"] >= m["t"] - 1 and c["b"] <= m["b"] + 1, (label, "card_outside_map", probe)
    if probe["placement"]["side"] != "overlap":
        assert probe["overlap_px"] <= 4, (label, "card_covers_property", probe)
    return probe


async def assert_text_fits(page, label):
    # C2c: search hint and map notice are shortened/wrapped, never cut mid-word.
    p = await js(page, TEXT_FIT_JS)
    assert p and p["text_px"] <= p["box_px"] + 1, (label, "search_placeholder_cut", p)
    if p["state"] and p["state"]["visible"]:
        assert p["state"]["sw"] <= p["state"]["cw"] + 1, (label, "map_state_cut", p)
    print("RX_C2C_TEXT_FIT", label, json.dumps({"placeholder": p["placeholder"], "state": (p["state"] or {}).get("text")}, ensure_ascii=False))


async def assert_quiet_pending_panel(browser):
    # C3: separate context (the 502 fixture logs console errors on purpose).
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()
    hits = []

    async def fail_panel(route):
        hits.append(route.request.url)
        await route.fulfill(status=502, json={"detail": "fixture_unavailable"})

    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await page.route("**/v1/live/map-panel/*", fail_panel)
    await set_dense(page)
    await click_first_parcel(page)
    await page.locator('[data-rx46-action="full"]').click()
    await page.wait_for_selector(".rx45-panel-card", state="visible", timeout=10000)
    first = await page.locator(".rx45-panel-card").inner_text()
    assert "NÃO FOI POSSÍVEL" not in first, first
    assert "consultando fontes oficiais" in first.casefold(), first
    # V45 settle retries at 40/500/1800/9500 ms before declaring the consultation pending.
    await page.wait_for_function(
        "()=>!!document.querySelector('.rx45-panel-card [data-rx45-retry]')", timeout=20000
    )
    pending = await page.locator(".rx45-panel-card").inner_text()
    folded = pending.casefold()
    assert "consulta às fontes oficiais pendente" in folded and "consultar de novo" in folded, pending
    for loud in ("NÃO FOI POSSÍVEL", "FONTE INDISPONÍVEL", "sem pendências"):
        assert loud.casefold() not in folded, (loud, pending)
    before = len(hits)
    await page.locator(".rx45-panel-card [data-rx45-retry]").click()
    await page.wait_for_timeout(1200)
    assert len(hits) > before, ("retry did not ask the server again", before, len(hits))
    print("RX_C3_QUIET_PENDING=PASS", json.dumps({"map_panel_requests": len(hits)}))
    await context.close()


async def viewport_flow(browser, width, height, label):
    context = await browser.new_context(
        viewport={"width": width, "height": height}, accept_downloads=True
    )
    page = await context.new_page()
    track_map_panel(page)
    errors = []
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on(
        "console",
        lambda msg: errors.append(f"console:{msg.type}:{msg.text}")
        if msg.type == "error"
        else None,
    )
    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await search_regression(page, label)
    # Isolate the existing polygon-click flow from the name-result fixture's
    # temporary SIGEF/result overlay and its lifetime timers.
    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await set_dense(page)
    await assert_parcel_tooltips(page)
    await assert_text_fits(page, label)
    # The helper may wait for cells or pan to reach a clickable parcel; the "map does not move on
    # click" contract compares with the centre read right before the click.
    clicked = await click_first_parcel(page)
    await page.wait_for_timeout(700)
    await assert_card_placement(page, label, inside_map=True)
    card = await assert_card_contract(page, clicked["center"], width)
    await assert_card_placement(page, label, inside_map=False)
    await assert_car_copy(page, ".rx46-card")
    await assert_official_sicar_action(page)
    await page.screenshot(path=str(OUT / f"{label}-card.png"), full_page=True)

    empty = await proven_empty_map_point(page)
    assert empty, "no provably empty map pixel found"
    print("RX_V46_EMPTY_MAP_PIXEL", label, json.dumps(empty, ensure_ascii=False))
    await page.mouse.click(empty["x"], empty["y"])
    await page.locator(".rx46-card").wait_for(state="detached", timeout=1500)
    assert await page.locator(".rx46-selection").count() >= 1, (
        "selected property disappeared with card close"
    )

    clicked = await click_first_parcel(page)
    await assert_card_contract(page, clicked["center"], width)
    late = await delay_snapshot(page)
    await open_full(page, width)
    await assert_late_snapshot_keeps_panel(page, late)
    await assert_car_copy(page, ".rx45-panel-card")
    await page.screenshot(path=str(OUT / f"{label}-panel.png"), full_page=True)

    tools = page.locator(".rx45-tools .rx45-tool")
    if width <= 390 and await tools.count() >= 2:
        b0 = await tools.nth(0).bounding_box()
        b1 = await tools.nth(1).bounding_box()
        assert b0 and b1 and abs(b0["y"] - b1["y"]) < 5, (b0, b1)
    if width <= 720:
        host = await page.locator("#rx43SnapshotHost").bounding_box()
        sel = await page.locator(".rx46-selection").first.bounding_box()
        assert host and sel, (host, sel)
        free_bottom = host["y"]
        visible_h = max(
            0,
            min(sel["y"] + sel["height"], free_bottom) - max(sel["y"], 0),
        )
        assert visible_h > 2, {
            "host": host,
            "selected": sel,
            "visible_above_panel": visible_h,
        }
    results["viewports"][label] = {
        "card_title": card["title"],
        "errors": errors,
        "empty_map_pixel": empty,
    }
    assert not errors, errors
    await context.close()


async def movement_gate(browser):
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()
    errors = []
    # C1: a property name only appears after a click, so moving the map must
    # never request names nor draw a name label.
    name_requests = []
    page.on(
        "request",
        lambda req: name_requests.append(req.url)
        if "/v1/live/property-names/" in req.url
        else None,
    )
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on(
        "console",
        lambda msg: errors.append(f"console:{msg.type}:{msg.text}")
        if msg.type == "error"
        else None,
    )
    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await set_dense(page)
    assert await visible_parcels(page) > 0
    ops = [
        ("zoom_in", "map.setZoom(Math.min(14,map.getZoom()+1),{animate:false})"),
        ("pan_e", "map.panBy([220,0],{animate:false})"),
        ("pan_s", "map.panBy([0,170],{animate:false})"),
        ("zoom_out", "map.setZoom(Math.max(12,map.getZoom()-1),{animate:false})"),
        ("pan_w", "map.panBy([-260,0],{animate:false})"),
        ("zoom_in_2", "map.setZoom(Math.min(14,map.getZoom()+1),{animate:false})"),
        ("pan_ne", "map.panBy([170,-140],{animate:false})"),
        ("pan_sw", "map.panBy([-190,180],{animate:false})"),
        ("zoom_out_2", "map.setZoom(Math.max(12,map.getZoom()-1),{animate:false})"),
        ("zoom_in_3", "map.setZoom(Math.min(14,map.getZoom()+1),{animate:false})"),
    ]
    for idx, (name, code) in enumerate(ops, 1):
        await js(page, f"()=>void ({code})")
        samples = []
        for _ in range(16):
            samples.append(await visible_parcels(page))
            await page.wait_for_timeout(100)
        await page.screenshot(
            path=str(OUT / f"movement-{idx:02d}-{name}.png"), full_page=True
        )
        rec = {
            "step": idx,
            "operation": name,
            "samples": samples,
            "minimum_visible": min(samples),
        }
        results["movements"].append(rec)
        print("RX_V46_MOVEMENT", json.dumps(rec, ensure_ascii=False))
        assert min(samples) > 0, rec
        await assert_parcel_fill(page)
        labels = await page.locator(".rx-farm-name-label, .rx-farm-name-icon").count()
        assert labels == 0, {"step": idx, "map_name_labels": labels}
    results["console"] = errors
    results["map_name_requests"] = name_requests
    print("RX_C1_MAP_NAME_REQUESTS", len(name_requests))
    assert not name_requests, name_requests
    assert not errors, errors
    await context.close()


async def assert_parcel_fill(page):
    fills = await js(page, """()=>{const rows=[];map.eachLayer(l=>{if(!l.feature?.properties?.cod_imovel||!l.options)return;if(l._path){const s=getComputedStyle(l._path);rows.push({fill:s.fill,opacity:Number(s.fillOpacity)})}else if(l._parts){rows.push({fill:l.options.fill===false?'none':String(l.options.fillColor||l.options.color),opacity:Number(l.options.fillOpacity)})}});return rows}""")
    assert fills and all(x['fill'] != 'none' and .15 <= x['opacity'] <= .25 for x in fills), fills


async def assert_parcel_tooltips(page):
    # F1B: nothing is written over the map on hover (the municipality/area balloon is gone);
    # the click still opens the card.
    tips = await js(page, """()=>{let n=0,withTip=0;map.eachLayer(l=>{if(l.feature?.properties?.cod_imovel){n++;if(l.getTooltip&&l.getTooltip())withTip++}});return {n,withTip,open:document.querySelectorAll('.leaflet-tooltip').length}}""")
    assert tips["n"] > 0, ("no parcels to check", tips)
    assert tips["withTip"] == 0 and tips["open"] == 0, ("tooltip over the map", tips)


async def assert_search_dropdown_escaped(page, car, geometry):
    await page.route('**/v1/live/search/properties?*', lambda route: route.fulfill(json={
        'items': [
            {'type': 'car', 'name': None, 'car_code': car, 'municipality': 'Curvelo', 'uf': 'MG', 'area_ha': 593.5167},
            {'type': 'sigef', 'name': '<b id="rxXssProbe">Area SIGEF</b>', 'municipality': '<i id="rxXssProbe2">Curvelo</i>',
             'uf': 'MG', 'registry': '<u id="rxXssProbe3">1</u>', 'area_ha': 12.5},
        ]
    }))
    await page.locator('#q').fill('Busca de escape')
    await page.locator('#go').click()
    await page.locator('.rx-smart-item').first.wait_for(state='visible', timeout=15000)
    probe = await js(page, """()=>({probes:['#rxXssProbe','#rxXssProbe2','#rxXssProbe3'].filter(s=>document.querySelector(s)).length,
      first:document.querySelector('.rx-smart-item b')?.innerText||'',text:document.querySelector('#rxSmartResults')?.innerText||''})""")
    assert probe['probes'] == 0, ("search dropdown interpolated external HTML", probe)
    assert probe['first'] == car, probe
    assert 'imóvel rural' not in probe['text'].casefold() and '<b id="rxXssProbe">' in probe['text'], probe
    await page.unroute('**/v1/live/search/properties?*')
    await js(page, "()=>document.querySelector('#rxSmartResults')?.remove()")


CARD_FIELDS_JS = """()=>{const c=document.querySelector('.rx46-card');const f={};c?.querySelectorAll('.rx46-field').forEach(x=>{f[(x.querySelector('small')?.textContent||'').trim().toLowerCase()]=(x.querySelector('b')?.innerText||'').trim()});
  const k=document.querySelector('.rx46-card [data-rx-copy-car]'),cr=c?.getBoundingClientRect(),kr=k?.getBoundingClientRect();
  return {fields:f,count:Object.keys(f).length,text:c?.innerText||'',named:c?.dataset.rxNamed||'',enriched:c?.dataset.rx46Enriched||'',
    cardTop:cr?cr.top:null,codeTop:kr?kr.top:null,codes:c?c.querySelectorAll('[data-rx-copy-car]').length:0}}"""


async def assert_card_rules_runtime(page, car, geometry):
    """Runs the shipped JS rules (not a Python twin) on deterministic payloads."""
    # F1B: the card and the panel share ONE /map-panel request per CAR (window.rxMapPanelOnce). The real
    # search just opened `car`, and its real /map-panel request may still be in flight (SICAR slow from
    # GitHub): a fixture for the same code would never be asked. The deterministic payloads use their own code.
    car = car[:-1] + ("0" if car[-1] != "0" else "1")
    fmt = await js(page, """()=>({ha0:rxNum.ha(0),haNull:rxNum.ha(null),haEmpty:rxNum.ha(''),haNaN:rxNum.ha(NaN),haStr0:rxNum.ha('0'),
      tiny:rxNum.num(0.003,2),edge:rxNum.num(0.005,2),big:rxNum.ha(1981.2),date:rxDateBR('2016-04-11T02:05:53.354Z'),dateNull:rxDateBR(null),
      renderWrapped:window.rxV46RenderV45Immediate?.__rxIdentitySanitizedV49===true})""")
    assert fmt == {"ha0": "0,00 ha", "haNull": "", "haEmpty": "", "haNaN": "", "haStr0": "0,00 ha", "tiny": "< 0,01",
                   "edge": "0,01", "big": "1.981,20 ha", "date": "10/04/2016", "dateNull": "",
                   "renderWrapped": True}, fmt

    panel = {}

    async def map_panel(route):
        if panel.get("delay"):
            await asyncio.sleep(panel["delay"])
        await route.fulfill(json=panel.get("body") or {"ok": False})

    await page.route("**/v1/live/map-panel/**", map_panel)
    try:
        # Zero is data, a missing value is hidden (owner rule 3).
        await js(page, """a=>window.rxV46SelectProperty({car_code:a.car,municipality:'Curvelo',uf:'MG',area_ha:0,fiscal_modules:0.003,condition:'',created_at:null,updated_at:null},a.g,null)""", {"car": car, "g": geometry})
        await page.wait_for_timeout(400)
        zero = await js(page, CARD_FIELDS_JS)
        assert zero["fields"].get("área") == "0,00 ha" and zero["fields"].get("módulos fiscais") == "< 0,01", zero
        assert not {"condição", "criação", "atualização"} & set(zero["fields"]), zero

        # A validated name is the title and the code appears exactly once, below it.
        name = "FAZENDA TESTE VALIDADA"
        await js(page, """a=>window.rxV46SelectProperty({car_code:a.car,municipality:'Curvelo',uf:'MG',name:a.name,validated_name:a.name,validation_status:'VALIDATED',name_validation_status:'VALIDATED',panel_name_eligible:true},a.g,null)""", {"car": car, "g": geometry, "name": name})
        await page.wait_for_timeout(400)
        named = await js(page, CARD_FIELDS_JS)
        title = await page.evaluate(TITLE_JS, ".rx46-card .rx46-title")
        assert named["named"] == "1" and title == name, (named, title)
        assert named["text"].count(car) == 1 and named["codes"] == 1, named

        # The enrichment adds rows but must not move the card or the code under the finger.
        panel.update({"delay": 1.2, "body": {"ok": True, "car_code": car, "created_at": "2016-04-11T02:05:53.354Z",
                                             "updated_at": "2025-09-10T23:38:30.026Z"}})
        await js(page, """a=>window.rxV46SelectProperty({car_code:a.car,municipality:'Curvelo',uf:'MG',area_ha:12.5},a.g,null)""", {"car": car, "g": geometry})
        await page.wait_for_timeout(150)
        first = await js(page, CARD_FIELDS_JS)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await page.wait_for_timeout(150)
        enriched = await js(page, CARD_FIELDS_JS)
        assert first["enriched"] == "" and enriched["count"] == first["count"] + 2, (first, enriched)
        assert DATE_RE.fullmatch(enriched["fields"].get("criação", "")), enriched
        assert abs(first["cardTop"] - enriched["cardTop"]) < 2 and abs(first["codeTop"] - enriched["codeTop"]) < 2, (first, enriched)
    finally:
        await page.unroute("**/v1/live/map-panel/**")


async def search_regression(page, label):
    car = 'MG-3120904-F3ED1E9DAC0042B8ADA898DC3EAF5A28'
    await js(page, "()=>map.setView([-14,-52],4,{animate:false})")
    await page.locator('#q').fill(car)
    await page.locator('#go').click()
    await page.locator(f'.rx46-card[data-car="{car}"]').wait_for(state='visible', timeout=60000)
    async def assert_framed():
        state = await js(page, """()=>{const g=window.current.geometry,b=L.geoJSON(g).getBounds();return {zoom:map.getZoom(),contains:map.getBounds().contains(b),geometry:g,property:window.current}}""")
        assert state['zoom'] > 4 and state['contains'], state
        assert await page.locator('.rx45-panel-card').count() == 0
        assert await page.locator('.rx46-selection').count() > 0
        return state
    state = await assert_framed()
    await page.screenshot(path=str(OUT / f'{label}-car-search.png'), full_page=True)
    # C2a / D1a runtime: the V49 sanitize runs on the final V46 entry, and a name
    # from another registry never becomes the card title.
    probe = await js(page, """()=>{const c=window.current;window.rxV46SelectProperty({...c,name:'NOME DE OUTRO CADASTRO',validated_name:'NOME DE OUTRO CADASTRO',validation_status:'UNVALIDATED',name_validation_status:'UNVALIDATED',panel_name_eligible:false},c.geometry,null);
      return {wrapped:window.rxV46SelectProperty.__rxIdentitySanitizedV49===true,name:window.current.name??null,title:document.querySelector('.rx46-title')?.innerText?.trim()||''}}""")
    assert probe['wrapped'] and probe['name'] is None and probe['title'] == car, probe
    await assert_card_rules_runtime(page, car, state['geometry'])
    await js(page, "()=>{window.rxV46CloseAnchor();map.setView([-14,-52],4,{animate:false})}")
    # Exercise the name-result UI using an explicit CAR result fixture carrying
    # the real geometry just resolved above. This does not assert name coverage.
    await page.route('**/v1/live/search/properties?*', lambda route: route.fulfill(json={
        'items': [{'type': 'car', 'name': 'Resultado de busca de teste', 'car_code': car,
                   'municipality': 'Curvelo', 'uf': 'MG', 'geometry': state['geometry']}]
    }))
    await page.locator('#q').fill('Resultado de busca de teste')
    await page.locator('#go').click()
    await page.locator('.rx-smart-item').first.click()
    await page.locator(f'.rx46-card[data-car="{car}"]').wait_for(state='visible', timeout=60000)
    await assert_framed()
    await page.screenshot(path=str(OUT / f'{label}-name-search.png'), full_page=True)
    await page.unroute('**/v1/live/search/properties?*')
    await js(page, "()=>window.rxV46CloseAnchor()")
    await assert_search_dropdown_escaped(page, car, state['geometry'])


# C2b: the SIGEF/INCRA reference inside the anchored card and the V45 panel, driven by
# /map-panel fixtures in a flow of its own (the real-click flows above stay untouched).
SIGEF_CAR = "MG-3152006-BB48D05173F540CD9703B23088C3ABF4"
SIGEF_LABEL = "PROJETO DE ASSENTAMENTO PAULISTA"
SIGEF_ORIGIN = "Acervo Fundiário do INCRA (SIGEF)"
SIGEF_GEOMETRY = {"type": "Polygon", "coordinates": [[[-45.02, -19.22], [-44.98, -19.22], [-44.98, -19.18], [-45.02, -19.18], [-45.02, -19.22]]]}

REF_JS = """(root)=>{
  const s=document.querySelector(root);if(!s)return {missing:true,present:false,all:''};
  const lum=c=>{const m=String(c).match(/[\\d.]+/g)||[0,0,0];const f=v=>{v=Number(v)/255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(m[0])+.7152*f(m[1])+.0722*f(m[2])};
  const bg=el=>{for(let x=el;x;x=x.parentElement){const c=getComputedStyle(x).backgroundColor,m=String(c).match(/[\\d.]+/g)||[];if(m.length>=3&&(m.length<4||Number(m[3])>.5))return c}return 'rgb(7,21,15)'};
  const ratio=el=>{const a=lum(getComputedStyle(el).color),b=lum(bg(el));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
  const sr=s.getBoundingClientRect(),t=s.querySelector('.rx46-title,.rx45-title h2');
  const base={missing:false,all:s.innerText||'',title:t?String(t.innerText||'').replace(/\\s+/g,'').trim():'',cardWidth:sr.width,cardTop:sr.top,enriched:s.dataset.rx46Enriched||''};
  const b=s.querySelector('[data-rx-sigef-ref]');if(!b)return {...base,present:false};
  const texts=[b,...b.querySelectorAll('*')].filter(e=>[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()));
  const br=b.getBoundingClientRect();
  return {...base,present:true,state:b.dataset.rxSigefRef,text:b.innerText,
    minFont:Math.min(...texts.map(e=>parseFloat(getComputedStyle(e).fontSize))),
    minContrast:Math.min(...texts.map(ratio)),
    inside:br.left>=sr.left-1&&br.right<=sr.right+1,noOverflow:b.scrollWidth<=b.clientWidth+1};
}"""


def sigef_body(car, state, reference=None, others=0, osm=()):
    return {"ok": True, "car_code": car, "municipality": "Pompéu", "uf": "MG", "area_ha": 1243.5656,
            "car_status": "AT", "validated_name": None, "validated_name_state": "unresolved",
            "name_validation_status": "UNRESOLVED", "panel_name_eligible": False,
            "geographic_references": list(osm), "geometry": SIGEF_GEOMETRY,
            "sigef_reference": reference, "sigef_reference_state": state, "sigef_reference_others": others}


def assert_ref_readable(info, where):
    assert info["present"] and info["minFont"] >= 9, (where, info)
    assert info["minContrast"] >= 4.5, (where, info)
    assert info["inside"] and info["noOverflow"], (where, info)
    assert "sem referência" not in info["all"].casefold(), (where, info)


async def sigef_reference_flow(browser, width, height, scenarios):
    context = await browser.new_context(viewport={"width": width, "height": height})
    page = await context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
    fixtures = {}
    hits = {}
    retry_hits = {}

    async def map_panel(route):
        url = route.request.url
        car = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        hits[car] = hits.get(car, 0) + 1
        retry = "sigef_retry=1" in url
        if retry:
            retry_hits[car] = retry_hits.get(car, 0) + 1
        fx = fixtures.get(car) or {}
        if fx.get("delay"):
            await asyncio.sleep(fx["delay"])
        body = fx.get("retry_body") if retry and fx.get("retry_body") else fx.get("body")
        await route.fulfill(json=body or {"ok": False})

    async def quiet(route):
        await route.fulfill(json={"ok": False, "detail": "c2b_fixture"})

    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await page.route("**/v1/live/map-panel/**", map_panel)
    # F1B: opening the card's "VER ANÁLISE COMPLETA" starts the full reading (report engine): quiet too, the
    # fixture CAR codes do not exist and the engine is not what this flow checks.
    for pattern in ("**/v1/live/snapshot/**", "**/v1/live/property-identity/**", "**/v1/live/conformity/**", "**/v1/live/car-integrity/**",
                    "**/v1/live/quick/**", "**/v1/live/progressive/**"):
        await page.route(pattern, quiet)
    # Below z11 the viewport loader stays idle, so no external source is involved.
    await js(page, "()=>map.setView([-19.2,-45.0],10,{animate:false})")
    await page.wait_for_timeout(300)

    async def select(car):
        await js(page, "()=>{window.rxV46CloseAnchor?.();window.rx43CloseDossier?.()}")
        await js(page, "a=>window.rxV46SelectProperty({car_code:a.car,municipality:'Pompéu',uf:'MG'},a.g,null)", {"car": car, "g": SIGEF_GEOMETRY})
        await page.wait_for_selector(f'.rx46-card[data-car="{car}"]', state="visible", timeout=5000)

    async def open_panel(car):
        await js(page, "()=>document.querySelector('.rx46-card [data-rx46-action=\"full\"]')?.click()")
        await page.wait_for_selector(f'.rx45-panel-card[data-car="{car}"]', state="visible", timeout=8000)

    helper = await js(page, """()=>{const R=window.rxSigefRefC2;if(!R)return null;const f=(o,st,po)=>R.html({car_code:'X',sigef_reference_state:st||'found',sigef_reference:{label:'L',car_overlap_ratio:o,parcel_overlap_ratio:po}},'card');
      return {p9995:R.pct(0.9995),p99996:R.pct(0.99996),p1:R.pct(1),p05:R.pct(0.5),p5005:R.pct(0.5005),below:f(0.49),none:f(0.99,'none'),nq:f(0.99,'not_queried'),found:f(0.6).includes('60,00%'),
        incomplete:f(0.99,'incomplete')+R.state({car_code:'X',sigef_reference_state:'incomplete'}),
        within:f(1,'found',0.013232).includes('o imóvel ocupa 1,32% desta parcela'),tiny:f(1,'found',0.00004).includes('menos de 0,01%'),
        same:f(0.9995,'found',0.9996).includes('desta parcela')}}""")
    assert helper == {"p9995": "99,95%", "p99996": "99,99%", "p1": "100,00%", "p05": "50,00%", "p5005": "50,05%", "below": "",
                      "none": "", "nq": "", "found": True, "incomplete": "hidden", "within": True, "tiny": True,
                      "same": False}, helper

    if "found" in scenarios:
        car = SIGEF_CAR
        ref = {"label": SIGEF_LABEL, "kind": "SIGEF_CADASTRAL", "origin": SIGEF_ORIGIN, "car_overlap_ratio": 0.999512,
               "parcel_overlap_ratio": 0.99962, "incra_property_code": "4170920078203", "parcel_code": "0d94a58a"}
        fixtures[car] = {"delay": 1.0, "body": sigef_body(car, "found", ref, others=1, osm=("Fazenda Teste OSM",))}
        await select(car)
        loading = await page.evaluate(REF_JS, ".rx46-card")
        assert not loading["present"] and loading["enriched"] == "", ("reference shown while loading", loading)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await page.wait_for_timeout(200)
        info = await page.evaluate(REF_JS, ".rx46-card")
        assert_ref_readable(info, "card-found")
        assert info["state"] == "found", info
        for required in ("Referência INCRA", SIGEF_LABEL, "99,95%", SIGEF_ORIGIN):
            assert required in info["text"], (required, info)
        assert "100,00%" not in info["all"], info
        assert SIGEF_LABEL.replace(" ", "") not in info["title"] and info["title"] == car, info
        assert 195 <= info["cardWidth"] <= 235 and abs(info["cardTop"] - loading["cardTop"]) < 2, (loading, info)
        await page.screenshot(path=str(OUT / f"c2b-{width}-card-found.png"), full_page=True)
        await open_panel(car)
        await page.wait_for_function("document.querySelector('.rx45-panel-card [data-rx-sigef-ref]')", timeout=8000)
        await page.wait_for_timeout(700)
        pinfo = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert_ref_readable(pinfo, "panel-found")
        for required in ("Referência INCRA", SIGEF_LABEL, "99,95%", SIGEF_ORIGIN, "não é o nome do CAR", "+1 outra certificação INCRA cobre metade ou mais"):
            assert required in pinfo["text"], (required, pinfo)
        assert SIGEF_LABEL.replace(" ", "") not in pinfo["title"], pinfo
        assert "OpenStreetMap" in pinfo["all"] and "Fazenda Teste OSM" in pinfo["all"], pinfo
        assert "Fazenda Teste OSM" not in pinfo["text"], ("OSM names belong to their own block", pinfo)
        await page.screenshot(path=str(OUT / f"c2b-{width}-panel-found.png"), full_page=True)

    if "unavailable" in scenarios:
        car = SIGEF_CAR[:-1] + "1"
        fixtures[car] = {"body": sigef_body(car, "unavailable")}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await page.wait_for_timeout(300)
        first = await page.evaluate(REF_JS, ".rx46-card")
        assert not first["present"] and "consulta pendente" not in first["all"], ("hidden before the retry", first)
        assert hits.get(car) == 1, hits
        # One automatic retry (setTimeout, ~8 s) while the same card is open; then a discreet pending line.
        await page.wait_for_function("document.querySelector('.rx46-card [data-rx-sigef-ref]')", timeout=15000)
        pending = await page.evaluate(REF_JS, ".rx46-card")
        assert_ref_readable(pending, "card-pending")
        assert pending["state"] == "pending" and "Referência INCRA: consulta pendente" in pending["text"], pending
        assert hits.get(car) == 2, hits
        await page.wait_for_timeout(9500)
        assert hits.get(car) == 2, ("more than one automatic retry", hits)
        still = await page.evaluate(REF_JS, ".rx46-card")
        assert still["state"] == "pending" and still["title"] == car, still
        await page.screenshot(path=str(OUT / f"c2b-{width}-card-pending.png"), full_page=True)
        await open_panel(car)
        await page.wait_for_timeout(2500)
        pinfo = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert_ref_readable(pinfo, "panel-pending")
        assert pinfo["state"] == "pending" and "consulta pendente" in pinfo["text"], pinfo
        assert retry_hits.get(car) == 1, ("the automatic retry skips the brief server memory", retry_hits)

        # The source recovers: a re-click shows the answer, and the panel's own older copy never hides it.
        await js(page, "()=>window.rx43CloseDossier?.()")
        found_ref = {"label": SIGEF_LABEL, "kind": "SIGEF_CADASTRAL", "origin": SIGEF_ORIGIN, "car_overlap_ratio": 0.999512,
                     "parcel_overlap_ratio": 0.99962, "incra_property_code": "4170920078203", "parcel_code": "0d94a58a"}
        fixtures[car] = {"body": sigef_body(car, "found", found_ref)}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card [data-rx-sigef-ref=\"found\"]')", timeout=10000)
        await open_panel(car)
        await page.wait_for_timeout(2500)
        again = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert again.get("state") == "found" and "99,95%" in again["text"] and "consulta pendente" not in again["all"], ("stale panel copy", again)

        # The panel opened before the card retry fired still gets exactly one retry.
        car = SIGEF_CAR[:-1] + "2"
        fixtures[car] = {"body": sigef_body(car, "unavailable")}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await open_panel(car)
        await page.wait_for_timeout(1500)
        early = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert not early["present"] and "sem referência" not in early["all"].casefold(), early
        loads = hits.get(car, 0)
        await page.wait_for_function("document.querySelector('.rx45-panel-card [data-rx-sigef-ref=\"pending\"]')", timeout=15000)
        await page.wait_for_timeout(9500)
        late = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert late["state"] == "pending", late
        assert hits.get(car, 0) == loads + 1, ("exactly one automatic retry for the open panel", loads, hits)

    if "recovered" in scenarios:
        found_ref = {"label": SIGEF_LABEL, "kind": "SIGEF_CADASTRAL", "origin": SIGEF_ORIGIN, "car_overlap_ratio": 0.999512,
                     "parcel_overlap_ratio": 0.99962, "incra_property_code": "4170920078203", "parcel_code": "0d94a58a"}
        # The card: unanswered first, the single automatic retry answers -> the reference replaces nothing but hidden.
        car = SIGEF_CAR[:-1] + "6"
        fixtures[car] = {"body": sigef_body(car, "unavailable"), "retry_body": sigef_body(car, "found", found_ref)}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await page.wait_for_timeout(300)
        first = await page.evaluate(REF_JS, ".rx46-card")
        assert not first["present"], ("hidden before the retry", first)
        await page.wait_for_function("document.querySelector('.rx46-card [data-rx-sigef-ref]')", timeout=15000)
        await page.wait_for_timeout(200)
        info = await page.evaluate(REF_JS, ".rx46-card")
        assert_ref_readable(info, "card-recovered")
        assert info["state"] == "found" and "99,95%" in info["text"] and "consulta pendente" not in info["all"], info
        assert abs(info["cardTop"] - first["cardTop"]) < 2 and info["title"] == car, (first, info)
        await page.wait_for_timeout(9500)
        assert hits.get(car) == 2 and retry_hits.get(car) == 1, ("exactly one automatic retry", hits, retry_hits)

        # The panel opened before the retry fires is repainted with the answer too.
        car = SIGEF_CAR[:-1] + "7"
        fixtures[car] = {"body": sigef_body(car, "unavailable"), "retry_body": sigef_body(car, "found", found_ref)}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await open_panel(car)
        await page.wait_for_timeout(1500)
        early = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert not early["present"], early
        loads = hits.get(car, 0)
        await page.wait_for_function("document.querySelector('.rx45-panel-card [data-rx-sigef-ref=\"found\"]')", timeout=15000)
        await page.wait_for_timeout(9500)
        late = await page.evaluate(REF_JS, ".rx45-panel-card")
        assert late["state"] == "found" and "99,95%" in late["text"] and "consulta pendente" not in late["all"], late
        assert retry_hits.get(car) == 1 and hits.get(car, 0) == loads + 1, ("exactly one automatic retry for the open panel", loads, hits, retry_hits)
        await page.screenshot(path=str(OUT / f"c2b-{width}-panel-recovered.png"), full_page=True)

    if "none" in scenarios:
        car = SIGEF_CAR[:-1] + "3"
        fixtures[car] = {"body": sigef_body(car, "none")}
        await select(car)
        await page.wait_for_function("document.querySelector('.rx46-card')?.dataset.rx46Enriched==='1'", timeout=10000)
        await page.wait_for_timeout(1200)
        info = await page.evaluate(REF_JS, ".rx46-card")
        assert not info["present"] and "Referência INCRA" not in info["all"] and "sem referência" not in info["all"].casefold(), info
        assert hits.get(car) == 1, hits

    results.setdefault("c2b_sigef_reference", {})[str(width)] = {"scenarios": list(scenarios), "hits": hits, "retry_hits": retry_hits, "errors": errors}
    assert not errors, errors
    await context.close()


# --click-controls: the parcel click helper against parcels that defeated the old probe, served by a
# deterministic in-browser fixture (no SICAR), then one broken rule at a time in the served portal.
# Rings are pixel offsets from the DENSE centre at DENSE zoom (x east, y south).
CLICK_LAYOUTS = {
    # bbox centre of the U falls in its notch, the donut's in its hole: both clickable only by their body.
    "concave": [
        [[(-160, -120), (-90, -120), (-90, 60), (90, 60), (90, -120), (160, -120), (160, 120), (-160, 120)]],
        [[(250, -100), (450, -100), (450, 100), (250, 100)], [(300, -50), (400, -50), (400, 50), (300, 50)]],
    ],
    # only parcels sticking into the view from outside (as when the edge/margin cells answer first):
    # 20 px at the right edge and 16 px under the top bar, both outside the free area -> needs the pan.
    "edge": [
        [[(700, -60), (1000, -60), (1000, 60), (700, 60)]],
        [[(-100, -520), (100, -520), (100, -400), (-100, -400)]],
    ],
}
CLICK_MUTATIONS = {
    "click_ignored": ("CLICK_NO_CARD", "l.on('click',e=>{if(e.originalEvent){try{e.originalEvent.__rx46ParcelClick=true",
                      "l.on('dblclick',e=>{if(e.originalEvent){try{e.originalEvent.__rx46ParcelClick=true"),
    "other_car": ("CLICK_OTHER_CAR", "const live=l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')",
                  "const live=(()=>{let o=null;map.eachLayer(x=>{if(!o&&x.feature?.properties?.cod_imovel&&x.feature!==l.feature)o=x.feature});return o})()||l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')"),
    "not_interactive": ("NO_CLICKABLE_PARCEL", "L.geoJSON(f,{renderer,style:rxParcelStyleFor", "L.geoJSON(f,{renderer,interactive:false,style:rxParcelStyleFor"),
}


def dense_px_to_lonlat(dx: float, dy: float) -> list[float]:
    scale = 256 * 2 ** DENSE["zoom"]
    siny = math.sin(math.radians(DENSE["lat"]))
    x = (DENSE["lon"] + 180) / 360 * scale + dx
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale + dy
    return [x / scale * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))]


def click_layout_features(layout: str) -> list[dict]:
    feats = []
    for i, rings in enumerate(CLICK_LAYOUTS[layout]):
        coords = [[dense_px_to_lonlat(*pt) for pt in ring + [ring[0]]] for ring in rings]
        car = "MG-3120904-" + hashlib.md5(f"click:{layout}:{i}".encode()).hexdigest().upper()
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": coords},
                      "properties": {"cod_imovel": car, "area": 40.5, "municipio": "Curvelo", "uf": "MG", "status_imovel": "AT",
                                     "condicao": "Aguardando análise", "tipo_imovel": "IRU", "m_fiscal": 1.1}})
    return feats


async def click_fixture_run(browser, layout: str, mutation: str | None = None, timeout_ms: int = 30000) -> dict:
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()
    feats = click_layout_features(layout)
    applied = []

    async def cells(route):
        q = parse_qs(urlparse(route.request.url).query)
        w, s, e, n = (float(q[k][0]) for k in ("west", "south", "east", "north"))
        hit = []
        for f in feats:
            xs = [c[0] for c in f["geometry"]["coordinates"][0]]
            ys = [c[1] for c in f["geometry"]["coordinates"][0]]
            if min(xs) < e and max(xs) > w and min(ys) < n and max(ys) > s:
                hit.append(f)
        await route.fulfill(json={"type": "FeatureCollection", "features": hit, "uf": "MG", "ufs": ["MG"], "truncated": False,
                                  "partial_failures": 0, "cached": True, "fetched_at": round(time.time(), 3)})

    async def mutate(route):
        if route.request.resource_type not in ("document", "script"):
            await route.fallback()
            return
        resp = await route.fetch()
        text = await resp.text()
        _, old, new = CLICK_MUTATIONS[mutation]
        if old not in text:
            await route.fulfill(response=resp)
            return
        assert text.count(old) == 1, ("CLICK_MUTATION_ANCHOR_NOT_UNIQUE", mutation)
        applied.append(route.request.url)
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding", "etag")}
        await route.fulfill(status=resp.status, headers=headers, body=text.replace(old, new))

    try:
        if mutation:
            await page.route("**/*", mutate)
        await page.route("**/v1/live/sicar/viewport-v46?*", cells)
        await page.route("**/v1/live/map-panel/*", lambda route: route.fulfill(status=502, json={"detail": "fixture_unavailable"}))
        await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await wait_runtime(page)
        assert not mutation or applied, ("CLICK_MUTATION_NOT_APPLIED", mutation)
        await set_dense(page)
        clicked = await click_first_parcel(page, timeout_ms=timeout_ms)
        assert clicked["car"] in {f["properties"]["cod_imovel"] for f in feats}, clicked
        return clicked
    finally:
        await context.close()


async def click_controls(browser) -> None:
    report = {"layouts": {}, "mutations": {}}
    for layout in CLICK_LAYOUTS:
        clicked = await click_fixture_run(browser, layout)
        assert layout != "edge" or clicked["pans"], ("edge layout must need the pan (premise)", clicked)
        report["layouts"][layout] = clicked
        print("RX_V46_CLICK_LAYOUT", layout, "PASS")
    missed = []
    for name, (tag, _, _) in CLICK_MUTATIONS.items():
        try:
            await click_fixture_run(browser, "concave", mutation=name, timeout_ms=8000)
            outcome = "passed"
        except AssertionError as exc:
            msg = str(exc)
            outcome = "caught" if tag in msg and "CLICK_MUTATION_" not in msg else "other: " + msg[:300]
        report["mutations"][name] = outcome
        print("RX_V46_CLICK_MUTATION", name, tag, outcome)
        if outcome != "caught":
            missed.append(name)
    (OUT / "v46-click-controls.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not missed, ("parcel click mutations not caught at their tag", missed)
    print("RX_V46_CLICK_CONTROLS=PASS")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if "--click-controls" in sys.argv:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                await click_controls(browser)
            finally:
                await browser.close()
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        OUT.joinpath("browser-executed.txt").write_text(
            "chromium launched\n", encoding="utf-8"
        )
        print("RX_V46_BROWSER_EXECUTED=YES")
        await viewport_flow(browser, 375, 812, "375")
        await viewport_flow(browser, 768, 900, "768")
        await viewport_flow(browser, 1440, 900, "1440")
        await movement_gate(browser)
        await assert_quiet_pending_panel(browser)
        await sigef_reference_flow(browser, 1440, 900, ("found", "unavailable", "recovered", "none"))
        await sigef_reference_flow(browser, 375, 812, ("found",))
        await browser.close()
    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RX_V46_BROWSER_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())


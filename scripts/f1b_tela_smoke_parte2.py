"""F1B part 2 screen smoke (local, real browser, real SICAR): card without jump, state border, municipality
search, report engine wake. NOT in CI (needs SICAR); the rules are proven offline by scripts/f1b_tela_gate.py.

Per width (1440x900, 768x1024, 375x812 mobile):
  1B.7  search by CAR -> card; click a parcel -> card; click a parcel whose map cell is older than 5 min
        (simulated age) -> card. Each is sampled every animation frame from first paint until the live
        /map-panel answer: time to first paint, fields at first paint, and the jump (card height, CTA top,
        card top) in px. Never an empty field, never m².
  1B.6  coordinate search on the SP/MG border (-20.1, -47.4) opens the SICAR property that covers it;
        municipality search typed without accents lists the IBGE municipality (a different one per width).
  1B.8  opening several cards in the same tab sends exactly one POST /v1/live/report-engine/wake.
Also: no horizontal scroll, no page error.

Run (portal already up):  python scripts/f1b_tela_smoke_parte2.py --base http://127.0.0.1:8311 --out <dir> [--tag after]
The --tag names the screenshots, so the same script documents a baseline and the change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
BORDER = "-20.1, -47.4"
BORDER_CAR = "SP-3543600-6BC541EC8E57497CAD7AE659E7B1F6C5"
# Typed without accents; a different municipality per width and per tag, so no run reuses a warm server cache.
CITY_CASES = {"before": [("sete lagoas", "Sete Lagoas"), ("paracatu", "Paracatu"), ("unai", "Unaí")],
              "after": [("sao joao del rei", "São João del Rei"), ("tres marias", "Três Marias"), ("pompeu", "Pompéu")]}
READY = "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard') && window.rxV46Installed===true"

REC_JS = """()=>{window.__rxRec={t0:performance.now(),rows:[],done:false};const R=window.__rxRec;
 const snap=()=>{const c=document.querySelector('.rx46-card');if(!c)return null;const b=c.getBoundingClientRect(),cta=c.querySelector('[data-rx46-action="full"]'),cb=cta?cta.getBoundingClientRect():null;
  return {t:Math.round(performance.now()-R.t0),car:c.dataset.car,top:Math.round(b.top),h:Math.round(b.height),cta_top:cb?Math.round(cb.top):null,cta_h:cb?Math.round(cb.height):null,
   labels:[...c.querySelectorAll('.rx46-field small')].map(x=>x.textContent.trim()),values:[...c.querySelectorAll('.rx46-field b')].map(x=>x.textContent.trim()),
   waits:c.querySelectorAll('.rx46-wait').length,enriched:c.dataset.rx46Enriched==='1',text:c.innerText,in_view:b.top>=0&&b.left>=0&&b.bottom<=innerHeight&&b.right<=innerWidth}};
 let last='';const loop=()=>{if(R.done)return;const s=snap();const key=s?JSON.stringify([s.car,s.top,s.h,s.cta_top,s.labels.length,s.waits,s.enriched,s.values.join('|')]):'none';if(key!==last){last=key;if(s)R.rows.push(s)}requestAnimationFrame(loop)};requestAnimationFrame(loop);return true}"""

PICK_JS = """(exclude)=>{const c=document.querySelector('.rx46-card')?.getBoundingClientRect();const r=map.getContainer().getBoundingClientRect();let best=null,bestD=1e9;const cx=r.left+r.width/2,cy=r.top+r.height/2;
 const inside=(pt,ring)=>{let o=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const xi=ring[i][0],yi=ring[i][1],xj=ring[j][0],yj=ring[j][1];if(((yi>pt[1])!==(yj>pt[1]))&&(pt[0]<(xj-xi)*(pt[1]-yi)/(yj-yi)+xi))o=!o}return o};
 map.eachLayer(l=>{const code=l.feature?.properties?.cod_imovel;if(!code||exclude.includes(code)||!l.getBounds)return;try{const b=l.getBounds(),sz=map.latLngToContainerPoint(b.getNorthEast()).x-map.latLngToContainerPoint(b.getSouthWest()).x;if(sz<40)return;
  const q=map.latLngToContainerPoint(b.getCenter()),x=q.x+r.left,y=q.y+r.top;if(x<60||y<r.top+60||x>innerWidth-60||y>innerHeight-60)return;if(c&&x>c.left-30&&x<c.right+30&&y>c.top-30&&y<c.bottom+30)return;
  const ll=b.getCenter(),gj=l.feature.geometry,polys=gj.type==='Polygon'?[gj.coordinates]:gj.type==='MultiPolygon'?gj.coordinates:[];if(!polys.some(p=>inside([ll.lng,ll.lat],p[0])))return;
  const d=Math.hypot(x-cx,y-cy);if(d<bestD){bestD=d;best={x,y,code}}}catch(e){}});return best}"""


def check(cond, msg, failures):
    if not cond:
        failures.append(msg)
    return cond


def summarize(rows, car):
    rows = [r for r in rows if r.get("car") == car]
    if not rows:
        return {"painted": False}
    first, last = rows[0], rows[-1]
    hs, ctas, tops = [r["h"] for r in rows], [r["cta_top"] for r in rows if r["cta_top"] is not None], [r["top"] for r in rows]
    return {"painted": True, "first_paint_ms": first["t"], "first_labels": first["labels"], "first_waits": first["waits"],
            "final_labels": last["labels"], "final_values": last["values"], "enriched_ms": next((r["t"] for r in rows if r["enriched"]), None),
            "height_jump_px": max(hs) - min(hs), "cta_jump_px": (max(ctas) - min(ctas)) if ctas else None, "top_jump_px": max(tops) - min(tops),
            "first_h": first["h"], "final_h": last["h"], "in_view": last["in_view"], "text": last["text"],
            "timeline": [(r["t"], r["h"], r["cta_top"], len(r["labels"]), r["waits"], r["enriched"]) for r in rows][:12]}


async def card_flow(page, act, out_png, wait_ms=25000):
    await page.evaluate(REC_JS)
    car = await act()
    try:
        await page.wait_for_function(f"!!document.querySelector('.rx46-card[data-car=\"{car}\"][data-rx46-enriched=\"1\"]')", timeout=wait_ms)
    except Exception:
        pass
    await page.wait_for_timeout(900)
    await page.screenshot(path=str(out_png))
    rows = await page.evaluate("()=>{window.__rxRec.done=true;return window.__rxRec.rows}")
    s = summarize(rows, car)
    s["car"] = car
    return s


async def run_width(browser, w, h, out: Path, tag: str, failures: list) -> dict:
    label = f"{w}x{h}"
    kw = dict(viewport={"width": w, "height": h}, locale="pt-BR")
    if w < 768:
        kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
    ctx = await browser.new_context(**kw)
    page = await ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    wakes: list[dict] = []
    cities: list[dict] = []
    t0 = time.time()

    def on_req(req):
        p = urlparse(req.url)
        if p.path == "/v1/live/report-engine/wake":
            wakes.append({"t": round(time.time() - t0, 2), "method": req.method})

    async def on_resp(resp):
        p = urlparse(resp.url)
        if p.path == "/v1/live/cities":
            try:
                body = await resp.json()
            except Exception:
                body = {}
            cities.append({"status": resp.status, "q": p.query, "items": [(x.get("name"), x.get("uf")) for x in (body.get("items") or [])][:4],
                           "ms": round(resp.request.timing.get("responseEnd", -1))})

    page.on("request", on_req)
    page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    res: dict = {"width": w, "height": h}
    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_function(READY, timeout=90000)
    tap = (lambda x, y: page.touchscreen.tap(x, y)) if w < 768 else (lambda x, y: page.mouse.click(x, y))

    async def by_search():
        await page.locator("#q").fill(CAR)
        await page.locator("#go").click()
        return CAR
    res["search"] = await card_flow(page, by_search, out / f"f1b2_{tag}_{label}_card_search.png")
    await page.evaluate("()=>window.rxV46CloseAnchor&&window.rxV46CloseAnchor()")
    await page.wait_for_timeout(3000)
    seen = [CAR]
    pt = await page.evaluate(PICK_JS, seen)
    if pt:
        seen.append(pt["code"])

        async def by_click():
            await tap(pt["x"], pt["y"])
            return pt["code"]
        res["parcel"] = await card_flow(page, by_click, out / f"f1b2_{tag}_{label}_card_parcel.png")
        await page.evaluate("()=>window.rxV46CloseAnchor&&window.rxV46CloseAnchor()")
        await page.wait_for_timeout(600)
    pt2 = None
    for _ in range(3):  # small screens: the first view may hold no unseen parcel clear of the controls
        pt2 = await page.evaluate(PICK_JS, seen)
        if pt2:
            break
        await page.evaluate("()=>map.panBy([90,-90],{animate:false})")
        await page.wait_for_timeout(2500)
    if pt2:
        await page.evaluate("()=>{window.__rxAge=window.rxW1aCellFetchedAt;window.rxW1aCellFetchedAt=g=>{const t=window.__rxAge?window.__rxAge(g):null;return Number.isFinite(t)?Date.now()-11*60000:t}}")

        async def by_stale():
            await tap(pt2["x"], pt2["y"])
            return pt2["code"]
        res["stale"] = await card_flow(page, by_stale, out / f"f1b2_{tag}_{label}_card_stale.png")
        await page.evaluate("()=>{if(window.__rxAge)window.rxW1aCellFetchedAt=window.__rxAge}")
        await page.evaluate("()=>window.rxV46CloseAnchor&&window.rxV46CloseAnchor()")
    res["wakes_after_cards"] = list(wakes)

    # 1B.6 border point by coordinate search
    await page.locator("#q").fill(BORDER)
    await page.locator("#go").click()
    try:
        await page.locator(f'.rx46-card[data-car="{BORDER_CAR}"]').wait_for(state="visible", timeout=60000)
        res["border"] = {"opened": True}
    except Exception:
        res["border"] = {"opened": False, "toast": await page.evaluate("()=>document.querySelector('#toast')?.textContent||''"),
                         "card": await page.evaluate("()=>document.querySelector('.rx46-card')?.dataset.car||null")}
    await page.wait_for_timeout(1200)
    await page.screenshot(path=str(out / f"f1b2_{tag}_{label}_border.png"))
    await page.evaluate("()=>window.rxV46CloseAnchor&&window.rxV46CloseAnchor()")

    # 1B.6 municipality search without accents
    cases = CITY_CASES.get(tag) or CITY_CASES["after"]
    typed, expected = cases[[1440, 768, 375].index(w) if w in (1440, 768, 375) else 0]
    await page.locator("#q").fill(typed)
    t_city = time.time()
    await page.locator("#go").click()
    try:
        await page.wait_for_function("exp=>((document.querySelector('#rxSmartResults')||document.querySelector('#rxCityResults')||{}).innerText||'').includes(exp)", arg=expected, timeout=45000)
        res["city"] = {"typed": typed, "listed": True, "ms": round((time.time() - t_city) * 1000)}
    except Exception:
        res["city"] = {"typed": typed, "listed": False, "ms": round((time.time() - t_city) * 1000)}
    res["city"]["panel_text"] = (await page.evaluate("()=>((document.querySelector('#rxSmartResults')||document.querySelector('#rxCityResults')||{}).innerText||'').slice(0,300)"))
    res["city"]["responses"] = cities[-2:]
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(out / f"f1b2_{tag}_{label}_city.png"))
    res["hscroll"] = await page.evaluate("()=>document.documentElement.scrollWidth>innerWidth+1")
    res["errors"] = errors
    res["wakes_total"] = len(wakes)

    # ------------------------------------------------------------------ judgement
    for k in ("search", "parcel", "stale"):
        s = res.get(k)
        if not check(bool(s and s.get("painted")), f"{label}:{k}:card_not_painted", failures):
            continue
        check("" not in s["final_values"], f"{label}:{k}:empty_field", failures)
        check("m²" not in s["text"] and " m2" not in s["text"], f"{label}:{k}:m2_in_card", failures)
        check(s["in_view"], f"{label}:{k}:card_off_screen", failures)
        if k in ("search", "parcel"):
            check("Criação" in s["first_labels"] and "Atualização" in s["first_labels"], f"{label}:{k}:dates_not_at_first_paint:{s['first_labels']}", failures)
            check(s["height_jump_px"] == 0 and (s["cta_jump_px"] or 0) == 0, f"{label}:{k}:card_jumped_{s['height_jump_px']}px", failures)
        else:
            check(s["first_waits"] >= 1 and "Status" not in s["first_labels"], f"{label}:stale:no_placeholder_or_stale_status:{s['first_labels']}", failures)
    check(res["border"].get("opened"), f"{label}:border_property_not_opened:{res['border']}", failures)
    check(res["city"]["listed"], f"{label}:city_without_accent_not_listed:{res['city']}", failures)
    check(len([x for x in wakes if x["method"] == "POST"]) == 1, f"{label}:wake_posts_{len(wakes)}", failures)
    check(not res["hscroll"], f"{label}:horizontal_scroll", failures)
    check(not errors, f"{label}:page_errors:{errors}", failures)
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
                r = await run_width(browser, w, h, out, args.tag, failures)
                results[f"{w}x{h}"] = r
                brief = {k: ({kk: vv for kk, vv in v.items() if kk not in ("timeline", "text")} if isinstance(v, dict) else v) for k, v in r.items()}
                print("F1B2_SMOKE_WIDTH", json.dumps(brief, ensure_ascii=False)[:2500], flush=True)
        finally:
            await browser.close()
    results["failures"] = failures
    (out / f"f1b2_smoke_{args.tag}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print("F1B2_TELA_SMOKE=" + ("PASS" if not failures else "FAIL " + json.dumps(failures, ensure_ascii=False)), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8311")
    ap.add_argument("--out", default="f1b2_smoke_out")
    ap.add_argument("--tag", default="after")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    sys.exit(asyncio.run(main(a)))

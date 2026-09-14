"""W1a browser smoke: /?car=<CODE> opens the right card; the "Compartilhar" menu works inside
the card without making it taller; a real polygon click/tap rewrites ?car=; link notices are
honest and closable; invalid codes are inert. Runs at 1440 (mouse) and 375 (touch).

Runs against a portal already booted (RX_BASE, default http://127.0.0.1:8000). The CAR,
map-panel and viewport routes are mocked in the browser, so the result never depends on
SICAR answering the runner (it does not answer GitHub). Fails on origin/main (no card
opens from the link; no share control).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import urlopen

from playwright.async_api import async_playwright

BASE = os.environ.get("RX_BASE", "http://127.0.0.1:8000").rstrip("/")
OUT = Path(os.environ.get("RX_ARTIFACTS", "artifacts"))
VALID = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OTHER = "MG-3120904-0123456789ABCDEF0123456789ABCDEF"
NOTFOUND = "MG-3120904-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
UNANSWERED = "MG-3120904-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"
MISMATCH = "MG-3120904-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
SLOW = "MG-3120904-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
FIXTURES = {VALID: (-18.8913, -44.1820, 14.795), OTHER: (-18.9420, -44.2400, 22.5), SLOW: (-18.9000, -44.2000, 9.1)}
EXACT = ("wfs1_equal", "wfs1_in", "wfs2_equal", "wfs1_like_exact", "wfs1_ogc_filter")
INVALID = (
    '<img src=x onerror="window.__w1aPwned=1">',
    "MG-3120904-XYZ",
    "javascript:window.__w1aPwned=1",
    VALID + '"><svg onload="window.__w1aPwned=1">',
    "XX-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
)
MSG_BUSY = "Abrindo o imóvel do link…"
MSG_INVALID = "O link não traz um código CAR válido."
MSG_NOTFOUND = "Não encontramos esse imóvel no SICAR. Confira o código do link."
MSG_PENDING = "Consulta pendente: o SICAR não respondeu agora."
# Standard boot wait for every smoke (old and new).
READY = "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard')"
results: dict = {"base": BASE, "viewports": {}}
car_calls: list[str] = []


def square(lat: float, lon: float, d: float = 0.004) -> dict:
    return {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]}


def car_body(code: str, answer_code: str | None = None) -> dict:
    lat, lon, area = FIXTURES[code]
    return {"car": {"ok": True, "source": "SICAR", "properties": {
        "cod_imovel": answer_code or code, "status_imovel": "AT", "area": area, "condicao": "Aguardando análise",
        "uf": "MG", "municipio": "Curvelo", "m_fiscal": 0.37, "tipo_imovel": "IRU"}, "geometry": square(lat, lon)},
        "lookup_mode": "smoke"}


def not_found_body(answered: bool) -> dict:
    attempts = [{"strategy": s, "ok": answered, "bytes": 147 if answered else 0, "detail": None if answered else "curl: (28) timeout"} for s in EXACT]
    return {"detail": {"car": {"ok": False, "source": "SICAR", "not_found": True, "detail": "CAR não localizado", "attempts": attempts}}}


async def route_car(route):
    code = unquote(urlparse(route.request.url).path.split("/v1/live/car/", 1)[1])
    car_calls.append(code)
    try:
        if code == SLOW:
            await asyncio.sleep(3)
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(car_body(code)))
        elif code in FIXTURES:
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(car_body(code)))
        elif code in (NOTFOUND, UNANSWERED):
            await route.fulfill(status=404, content_type="application/json", body=json.dumps(not_found_body(code == NOTFOUND)))
        elif code == MISMATCH:
            # The server answers with ANOTHER property: the card must never open.
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(car_body(VALID)))
        else:
            await route.fulfill(status=502, content_type="application/json", body=json.dumps({"detail": "smoke"}))
    except Exception:
        pass  # the page cancelled the request (closed notice): nothing to answer


async def route_panel(route):
    code = unquote(urlparse(route.request.url).path.split("/v1/live/map-panel/", 1)[1])
    if code not in FIXTURES:
        await route.fulfill(status=502, content_type="application/json", body="{}")
        return
    lat, lon, area = FIXTURES[code]
    await route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "ok": True, "car_code": code, "car_status": "AT", "area_ha": area, "municipality": "Curvelo", "uf": "MG",
        "property_type": "IRU", "condition": "Aguardando análise", "fiscal_modules": 0.37,
        "created_at": "2018-11-28", "updated_at": "2025-03-07", "geometry": square(lat, lon),
        "sigef_reference_state": "incomplete"}))


async def route_viewport(route):
    features = [{"type": "Feature", "id": code, "geometry": square(lat, lon), "properties": {
        "cod_imovel": code, "municipio": "Curvelo", "uf": "MG", "area": area, "status_imovel": "AT",
        "condicao": "Aguardando análise", "tipo_imovel": "IRU", "m_fiscal": 0.37}}
        for code, (lat, lon, area) in FIXTURES.items() if code != SLOW]
    await route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"type": "FeatureCollection", "uf": "MG", "features": features, "truncated": False, "cached": False}))


async def wait_runtime(page):
    await page.wait_for_function(READY, timeout=30000)
    await page.wait_for_function("window.rxV46Installed===true && typeof map!=='undefined' && !!map.getBounds", timeout=15000)


async def url_car(page):
    return await page.evaluate("()=>new URL(location.href).searchParams.getAll('car')")


async def notice(page):
    return await page.evaluate("()=>{const e=document.querySelector('#rxShareStateW1a');return e&&!e.hidden?e.querySelector('.rx-share-state-text').textContent:null}")


async def wait_notice(page, text, timeout):
    await page.wait_for_function(f"document.querySelector('#rxShareStateW1a:not([hidden]) .rx-share-state-text')?.textContent==={json.dumps(text)}", timeout=timeout)


async def reveal(page, selector):
    delta = await page.evaluate("""sel=>{const el=document.querySelector(sel);if(!el)return 0;const r=el.getBoundingClientRect(),
      top=(document.querySelector('header.top')?.getBoundingClientRect().bottom||0)+8,bottom=innerHeight-8;
      return r.top<top?Math.ceil(top-r.top):(r.bottom>bottom?-Math.ceil(r.bottom-bottom):0)}""", selector)
    if delta:
        await page.evaluate("d=>map.panBy([0,-d],{animate:false})", delta)
        await page.wait_for_timeout(200)


TARGET_JS = """sel=>{const el=document.querySelector(sel);if(!el)return null;const r=el.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,
  hit=document.elementFromPoint(cx,cy);return {w:r.width,h:r.height,top:r.top,bottom:r.bottom,left:r.left,right:r.right,
  hit:!!hit&&(hit===el||el.contains(hit)),vw:innerWidth,vh:innerHeight}}"""


async def assert_target(page, selector, label):
    await reveal(page, selector)
    t = await page.evaluate(TARGET_JS, selector)
    assert t, (label, "missing")
    assert t["w"] >= 44 and t["h"] >= 44, (label, "touch target below 44 px", t)
    assert t["left"] >= 0 and t["right"] <= t["vw"] and t["top"] >= 0 and t["bottom"] <= t["vh"], (label, "outside viewport", t)
    assert t["hit"], (label, "covered by another element", t)
    return t


async def open_menu(page, label):
    await assert_target(page, ".rx46-card [data-rx-share-toggle]", f"{label}:share-toggle")
    if await page.evaluate("document.querySelector('.rx46-card [data-rx-share-menu]').hidden"):
        await page.locator(".rx46-card [data-rx-share-toggle]").click()
    await page.wait_for_selector(".rx46-card [data-rx-share-menu]:not([hidden])", timeout=3000)
    assert await page.evaluate("document.querySelector('.rx46-card [data-rx-share-toggle]').getAttribute('aria-expanded')") == "true"


async def run_viewport(browser, width, height):
    label = str(width)
    touch = width < 768
    kw = dict(viewport={"width": width, "height": height}, locale="pt-BR")
    if touch:
        kw.update(is_mobile=True, has_touch=True, device_scale_factor=2,
                  user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
    ctx = await browser.new_context(**kw)
    await ctx.route("**/v1/live/car/*", route_car)
    await ctx.route("**/v1/live/map-panel/*", route_panel)
    await ctx.route("**/v1/live/sicar/viewport-v46*", route_viewport)
    await ctx.route("https://wa.me/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<title>wa</title>"))
    page = await ctx.new_page()
    errors, dialogs = [], []
    page.on("pageerror", lambda e: errors.append(str(e)[:300]))
    page.on("dialog", lambda d: (dialogs.append(d.message), asyncio.ensure_future(d.dismiss())))
    out: dict = {}
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. The link opens the card of exactly that CAR, title = code (no validated name here).
    await page.goto(f"{BASE}/?car={VALID}", wait_until="domcontentloaded", timeout=60000)
    await wait_runtime(page)
    card = page.locator(f'.rx46-card[data-car="{VALID}"]')
    await card.wait_for(state="visible", timeout=20000)
    await page.wait_for_function(f"document.querySelector('.rx46-card[data-car=\"{VALID}\"]')?.dataset.rx46Enriched==='1'", timeout=15000)
    title = (await page.locator(".rx46-card .rx46-title").inner_text()).strip()
    assert title == VALID, ("title must be the CAR code", title)
    text = await card.inner_text()
    assert text.count(VALID) == 1, ("CAR code must appear exactly once in the card", text)
    assert await url_car(page) == [VALID]
    assert await notice(page) is None

    # 2. The share control adds no height: the card is as tall without it.
    geo = await page.evaluate("""()=>{const c=document.querySelector('.rx46-card'),h=c.querySelector('.rx46-head'),s=c.querySelector('[data-rx-share-row]');
      const m=()=>({card:c.getBoundingClientRect().height,head:h.getBoundingClientRect().height});const with_=m(),next=s.nextSibling,parent=s.parentNode;
      s.remove();const without=m();parent.insertBefore(s,next);const r=c.getBoundingClientRect();
      const lines=e=>{const g=document.createRange();g.selectNodeContents(e);return new Set([...g.getClientRects()].map(x=>Math.round(x.top))).size};
      return {with:with_,without,w:r.width,kmlLines:lines(h.querySelector('.rx46-linkbtn')),placement:window.__rx46Placement||null}}""")
    assert geo["with"] == geo["without"], ("share control changed the card height", geo)
    assert geo["kmlLines"] == 1, ("share control squeezed 'Mapa KML' onto two lines", geo)
    assert 195 <= geo["w"] <= 235, geo
    out["card"] = {"title": title, "geometry": geo}

    # 3. Menu opens inside the card with 44 px items that nothing covers.
    await open_menu(page, label)
    menu = await page.evaluate("""()=>{const c=document.querySelector('.rx46-card').getBoundingClientRect(),m=document.querySelector('.rx46-card [data-rx-share-menu]').getBoundingClientRect();
      return {inside:m.left>=c.left-.5&&m.right<=c.right+.5&&m.top>=c.top-.5&&m.bottom<=c.bottom+.5,menu:[m.left,m.top,m.width,m.height],card:[c.left,c.top,c.width,c.height]}}""")
    assert menu["inside"], ("menu leaves the card and covers the map", menu)
    out["menu"] = menu
    out["copy_target"] = await assert_target(page, ".rx46-card [data-rx-share-copy]", f"{label}:copy")
    out["wa_target"] = await assert_target(page, ".rx46-card [data-rx-share-wa]", f"{label}:whatsapp")
    await page.screenshot(path=str(OUT / f"w1a_{label}_menu.png"))

    # 4. WhatsApp: real link, pt-BR text carrying the property link; opener isolated; menu closes.
    wa = await page.evaluate("()=>{const a=document.querySelector('.rx46-card [data-rx-share-wa]');return {href:a.getAttribute('href'),target:a.target,rel:a.rel}}")
    link = f"{BASE}/?car={VALID}"
    assert wa["href"].startswith("https://wa.me/?text="), wa
    wa_text = unquote(wa["href"].split("?text=", 1)[1])
    assert wa_text.endswith(link) and "Raio-X Territorial" in wa_text and "(Curvelo / MG)" in wa_text, wa_text
    assert wa["target"] == "_blank" and "noopener" in wa["rel"], wa
    out["whatsapp_text"] = wa_text
    async with page.expect_popup(timeout=5000) as pop_info:
        await page.locator(".rx46-card [data-rx-share-wa]").click()
    popup = await pop_info.value
    assert popup.url.startswith("https://wa.me/?text="), popup.url
    await popup.close()
    assert await card.count() == 1, "WhatsApp click must not close the card"
    await page.wait_for_selector(".rx46-card [data-rx-share-menu][hidden]", state="attached", timeout=3000)

    # 5. Copy success: "Link copiado" only after the clipboard really took the exact link.
    hist = await page.evaluate("history.length")
    await page.evaluate("()=>{window.__w1aCopied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:v=>{window.__w1aCopied.push(String(v));return Promise.resolve()}}})}")
    await open_menu(page, label)
    await page.locator(".rx46-card [data-rx-share-copy]").click()
    await page.wait_for_function("document.querySelector('.rx46-card [data-rx-share-copy]')?.innerText.includes('Link copiado')", timeout=3000)
    assert await page.evaluate("window.__w1aCopied") == [link]
    assert await card.count() == 1, "copy click must not close the card"
    await page.wait_for_selector(".rx46-card [data-rx-share-menu][hidden]", state="attached", timeout=4000)
    await page.wait_for_function("!document.querySelector('.rx46-card [data-rx-share-toggle]')?.dataset.state", timeout=6000)

    # 6. Copy failure: never "copiado"; the link is shown, selected, in a closable sheet.
    await page.evaluate("()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});document.execCommand=()=>false}")
    await open_menu(page, label)
    await page.locator(".rx46-card [data-rx-share-copy]").click()
    await page.wait_for_selector("#rxShareSheetW1a:not([hidden])", timeout=3000)
    await page.wait_for_timeout(300)
    labels = await page.evaluate("()=>[...document.querySelectorAll('.rx46-card [data-rx-share-row], #rxShareLiveW1a')].map(e=>e.textContent).join(' | ')")
    assert "copiado" not in labels.lower(), ("fake copy success", labels)
    field = await page.evaluate("()=>{const t=document.querySelector('#rxShareSheetW1a textarea');return {value:t.value,sel:t.selectionEnd-t.selectionStart}}")
    assert field["value"] == link and field["sel"] == len(link), field
    await page.screenshot(path=str(OUT / f"w1a_{label}_copy_fallback.png"))
    await assert_target(page, "#rxShareSheetW1a .rx-share-sheet-close", f"{label}:sheet-close")
    await page.locator("#rxShareSheetW1a .rx-share-sheet-close").click()
    assert await page.evaluate("document.querySelector('#rxShareSheetW1a').hidden") is True

    # 7. A real click (mouse) or tap (touch) on another polygon rewrites ?car= without history entries.
    lat, lon, _ = FIXTURES[OTHER]
    await page.evaluate("a=>map.setView([a[0],a[1]],15,{animate:false})", [lat, lon])
    await page.wait_for_function(f"(()=>{{let ok=false;map.eachLayer(l=>{{if(l._path&&l.feature?.properties?.cod_imovel==={json.dumps(OTHER)})ok=true}});return ok}})()", timeout=15000)
    await page.wait_for_timeout(400)
    point = await page.evaluate("""a=>{const [code,lat,lon]=a;let path=null;map.eachLayer(l=>{if(l._path&&l.feature?.properties?.cod_imovel===code)path=l._path});if(!path)return null;
      const mr=map.getContainer().getBoundingClientRect();for(const dy of [0,-.002,.002,-.003,.003])for(const dx of [0,-.002,.002,-.003,.003]){
        const p=map.latLngToContainerPoint([lat+dy,lon+dx]),x=mr.left+p.x,y=mr.top+p.y;if(x<0||y<0||x>innerWidth||y>innerHeight)continue;
        if(document.elementFromPoint(x,y)===path)return {x,y}}return null}""", [OTHER, lat, lon])
    assert point, (label, "no uncovered point on the other polygon")
    if touch:
        await page.touchscreen.tap(point["x"], point["y"])
    else:
        await page.mouse.click(point["x"], point["y"])
    await page.locator(f'.rx46-card[data-car="{OTHER}"]').wait_for(state="visible", timeout=10000)
    assert await url_car(page) == [OTHER]
    assert await page.evaluate("history.length") == hist, "selection must use replaceState"
    out["polygon_click"] = {"input": "tap" if touch else "mouse", "point": point}

    # 8. Closing the card drops the parameter.
    await reveal(page, '.rx46-card [data-rx46-action="close"]')
    await page.locator('.rx46-card [data-rx46-action="close"]').click()
    await page.locator(".rx46-card").wait_for(state="detached", timeout=3000)
    assert await url_car(page) == []

    # 9. Map position is written to and read from #z/lat/lon.
    await page.evaluate("()=>map.setView([-19.7472,-47.9381],13,{animate:false})")
    await page.wait_for_function("location.hash==='#13/-19.74720/-47.93810'", timeout=3000)
    assert await page.evaluate("history.length") == hist
    await page.goto(f"{BASE}/?w1a=pos#12/-19.20000/-45.00000", wait_until="domcontentloaded", timeout=60000)
    await wait_runtime(page)
    await page.wait_for_function("map.getZoom()===12&&Math.abs(map.getCenter().lat+19.2)<1e-3&&Math.abs(map.getCenter().lng+45)<1e-3", timeout=5000)

    # 10. Invalid codes never fetch, never open, never inject; a discreet notice explains.
    calls_before = len(car_calls)
    cases = INVALID if not touch else INVALID[:2]
    for i, raw in enumerate(cases + (f"{VALID}&car={OTHER}",)):
        query = f"car={quote(raw, safe='')}" if "&car=" not in raw else f"car={VALID}&car={OTHER}"
        await page.goto(f"{BASE}/?{query}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await wait_notice(page, MSG_INVALID, 6000)
        await page.wait_for_timeout(400)
        state = await page.evaluate("""()=>({card:document.querySelectorAll('.rx46-card').length,pwned:window.__w1aPwned===1,
          injected:document.querySelectorAll('img[onerror],svg[onload],[onload],[onerror]').length,car:new URL(location.href).searchParams.getAll('car')})""")
        assert state == {"card": 0, "pwned": False, "injected": 0, "car": []}, (label, raw, state)
        if i == 0:
            await page.screenshot(path=str(OUT / f"w1a_{label}_invalid.png"))
            await assert_target(page, "#rxShareStateW1a .rx-share-state-x", f"{label}:notice-close")
    assert len(car_calls) == calls_before, ("invalid code reached the CAR endpoint", car_calls[calls_before:])

    # 11. Busy notice: visible close; closing cancels the lookup and the card never opens.
    await page.goto(f"{BASE}/?car={SLOW}", wait_until="domcontentloaded", timeout=60000)
    await wait_runtime(page)
    await wait_notice(page, MSG_BUSY, 6000)
    await page.screenshot(path=str(OUT / f"w1a_{label}_busy.png"))
    await assert_target(page, "#rxShareStateW1a .rx-share-state-x", f"{label}:busy-close")
    await page.locator("#rxShareStateW1a .rx-share-state-x").click()
    assert await notice(page) is None and await url_car(page) == []
    await page.wait_for_timeout(3500)
    assert await page.locator(".rx46-card").count() == 0, "cancelled link opened the card"

    if not touch:
        # 12. SICAR answered "no such property": honest notice, no card.
        await page.goto(f"{BASE}/?car={NOTFOUND}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await wait_notice(page, MSG_NOTFOUND, 8000)
        assert await page.locator(".rx46-card").count() == 0 and await url_car(page) == []
        # 13. A 404 without any SICAR answer is pending, never "não encontramos".
        await page.goto(f"{BASE}/?car={UNANSWERED}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        seen = set()
        for _ in range(60):
            t = await notice(page)
            if t:
                seen.add(t)
            if t == MSG_PENDING:
                break
            await page.wait_for_timeout(200)
        assert MSG_PENDING in seen and MSG_NOTFOUND not in seen, ("404 without SICAR answer", seen)
        # 14. An answer for another CAR is never shown: one retry, then discreet pending + retry button.
        await page.goto(f"{BASE}/?car={MISMATCH}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await wait_notice(page, MSG_PENDING, 12000)
        assert await page.locator(".rx46-card").count() == 0
        assert car_calls.count(MISMATCH) == 2, car_calls
        await assert_target(page, "#rxShareStateW1a .rx-share-state-retry", f"{label}:retry")

    assert not errors, (label, "page errors", errors)
    assert not dialogs, (label, "dialogs opened", dialogs)
    await ctx.close()
    return out


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with urlopen(f"{BASE}/", timeout=30) as resp:
        assert "RX_SHARE_LINK_W1A" in resp.read().decode("utf-8", "replace"), "RX_SHARE_LINK_W1A marker missing: W1a not loaded"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for width, height in ((1440, 900), (375, 812)):
                results["viewports"][str(width)] = await run_viewport(browser, width, height)
                print(f"W1A_LINK_SMOKE_VIEWPORT={width} PASS", flush=True)
        finally:
            await browser.close()
            (OUT / "w1a_link_smoke.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("W1A_LINK_SMOKE=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

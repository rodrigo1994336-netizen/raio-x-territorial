"""W1a browser smoke: /?car=<CODE> opens the right card; invalid codes are inert; share
buttons work at 375 and 1440.

Runs against a portal already booted (RX_BASE, default http://127.0.0.1:8000). The CAR,
map-panel and viewport routes are mocked in the browser, so the result never depends on
SICAR answering the runner (it does not answer GitHub). Fails on origin/main (no card
opens from the link; no share row).
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
MISMATCH = "MG-3120904-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FIXTURES = {VALID: (-18.8913, -44.1820, 14.795), OTHER: (-18.9420, -44.2400, 22.5)}
INVALID = (
    '<img src=x onerror="window.__w1aPwned=1">',
    "MG-3120904-XYZ",
    "javascript:window.__w1aPwned=1",
    VALID + '"><svg onload="window.__w1aPwned=1">',
    "XX-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
)
MSG_INVALID = "O link não traz um código CAR válido."
MSG_NOTFOUND = "O SICAR não retornou imóvel com o código deste link."
MSG_PENDING = "Não foi possível abrir o imóvel do link agora."
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


async def route_car(route):
    code = unquote(urlparse(route.request.url).path.split("/v1/live/car/", 1)[1])
    car_calls.append(code)
    if code in FIXTURES:
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(car_body(code)))
    elif code == NOTFOUND:
        await route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": {"car": {"ok": False, "not_found": True}}}))
    elif code == MISMATCH:
        # The server answers with ANOTHER property: the card must never open.
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(car_body(VALID)))
    else:
        await route.fulfill(status=502, content_type="application/json", body=json.dumps({"detail": "smoke"}))


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
    await route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"type": "FeatureCollection", "features": [], "truncated": False, "cached": False}))


async def wait_runtime(page):
    await page.wait_for_function("sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')", timeout=30000)
    await page.wait_for_function("window.rxV46Installed===true && typeof map!=='undefined' && !!map.getBounds", timeout=15000)


async def url_car(page):
    return await page.evaluate("()=>new URL(location.href).searchParams.getAll('car')")


async def notice(page):
    return await page.evaluate("()=>{const e=document.querySelector('#rxShareStateW1a');return e&&!e.hidden?e.querySelector('.rx-share-state-text').textContent:null}")


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


async def run_viewport(browser, width, height):
    label = str(width)
    kw = dict(viewport={"width": width, "height": height}, locale="pt-BR")
    if width < 768:
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
    box = await page.evaluate("()=>{const r=document.querySelector('.rx46-card').getBoundingClientRect();return {w:r.width,h:r.height}}")
    assert 195 <= box["w"] <= 235, box
    out["card"] = {"title": title, "box": box}

    # 2. Share targets >= 44 px, inside the screen and not covered.
    out["copy_target"] = await assert_target(page, '.rx46-card [data-rx-share-copy]', f"{label}:copy")
    out["wa_target"] = await assert_target(page, '.rx46-card [data-rx-share-wa]', f"{label}:whatsapp")
    OUT.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT / f"w1a_{label}_card.png"))

    # 3. WhatsApp: real link, pt-BR text carrying the property link; opener isolated.
    wa = await page.evaluate("()=>{const a=document.querySelector('.rx46-card [data-rx-share-wa]');return {href:a.getAttribute('href'),target:a.target,rel:a.rel}}")
    link = f"{BASE}/?car={VALID}"
    assert wa["href"].startswith("https://wa.me/?text="), wa
    wa_text = unquote(wa["href"].split("?text=", 1)[1])
    assert wa_text.endswith(link) and "Raio-X Territorial" in wa_text and "(Curvelo / MG)" in wa_text, wa_text
    assert wa["target"] == "_blank" and "noopener" in wa["rel"], wa
    out["whatsapp_text"] = wa_text
    async with page.expect_popup(timeout=5000) as pop_info:
        await page.locator('.rx46-card [data-rx-share-wa]').click()
    popup = await pop_info.value
    assert popup.url.startswith("https://wa.me/?text="), popup.url
    await popup.close()
    assert await card.count() == 1, "WhatsApp click must not close the card"

    # 4. Copy success: "Link copiado" only after the clipboard really took the exact link.
    hist = await page.evaluate("history.length")
    await page.evaluate("()=>{window.__w1aCopied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:v=>{window.__w1aCopied.push(String(v));return Promise.resolve()}}})}")
    await page.locator('.rx46-card [data-rx-share-copy]').click()
    await page.wait_for_function("document.querySelector('.rx46-card [data-rx-share-copy]')?.innerText.includes('Link copiado')", timeout=3000)
    assert await page.evaluate("window.__w1aCopied") == [link]
    await page.wait_for_timeout(800)
    assert await card.count() == 1, "copy click must not close the card"
    await page.wait_for_function("document.querySelector('.rx46-card [data-rx-share-copy]')?.innerText.trim()==='Copiar link'", timeout=6000)

    # 5. Copy failure: never "copiado"; the link is shown, selected, in a closable sheet.
    await page.evaluate("()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});document.execCommand=()=>false}")
    await page.locator('.rx46-card [data-rx-share-copy]').click()
    await page.wait_for_selector("#rxShareSheetW1a:not([hidden])", timeout=3000)
    await page.wait_for_timeout(300)
    label_now = await page.locator('.rx46-card [data-rx-share-copy]').inner_text()
    assert "copiado" not in label_now.lower(), ("fake copy success", label_now)
    field = await page.evaluate("()=>{const t=document.querySelector('#rxShareSheetW1a textarea');return {value:t.value,sel:t.selectionEnd-t.selectionStart}}")
    assert field["value"] == link and field["sel"] == len(link), field
    await page.screenshot(path=str(OUT / f"w1a_{label}_copy_fallback.png"))
    await assert_target(page, "#rxShareSheetW1a .rx-share-sheet-close", f"{label}:sheet-close")
    await page.locator("#rxShareSheetW1a .rx-share-sheet-close").click()
    assert await page.evaluate("document.querySelector('#rxShareSheetW1a').hidden") is True

    # 6. Selecting another property rewrites ?car= without adding history entries.
    lat, lon, area = FIXTURES[OTHER]
    await page.evaluate("""a=>window.showProperty({car_code:a.code,municipality:'Curvelo',uf:'MG',area_ha:a.area,status:'AT'},a.geom)""",
                        {"code": OTHER, "area": area, "geom": square(lat, lon)})
    await page.locator(f'.rx46-card[data-car="{OTHER}"]').wait_for(state="visible", timeout=10000)
    assert await url_car(page) == [OTHER]
    assert await page.evaluate("history.length") == hist, "selection must use replaceState"

    # 7. Closing the card drops the parameter.
    await reveal(page, '.rx46-card [data-rx46-action="close"]')
    await page.locator('.rx46-card [data-rx46-action="close"]').click()
    await page.locator(".rx46-card").wait_for(state="detached", timeout=3000)
    assert await url_car(page) == []

    # 8. Map position is written to and read from #z/lat/lon.
    await page.evaluate("()=>map.setView([-19.7472,-47.9381],13,{animate:false})")
    await page.wait_for_function("location.hash==='#13/-19.74720/-47.93810'", timeout=3000)
    assert await page.evaluate("history.length") == hist
    await page.goto(f"{BASE}/?w1a=pos#12/-19.20000/-45.00000", wait_until="domcontentloaded", timeout=60000)
    await wait_runtime(page)
    await page.wait_for_function("map.getZoom()===12&&Math.abs(map.getCenter().lat+19.2)<1e-3&&Math.abs(map.getCenter().lng+45)<1e-3", timeout=5000)

    # 9. Invalid codes never fetch, never open, never inject; a discreet notice explains.
    calls_before = len(car_calls)
    cases = INVALID if width >= 768 else INVALID[:2]
    for i, raw in enumerate(cases + (f"{VALID}&car={OTHER}",)):
        query = f"car={quote(raw, safe='')}" if "&car=" not in raw else f"car={VALID}&car={OTHER}"
        await page.goto(f"{BASE}/?{query}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await page.wait_for_function(f"document.querySelector('#rxShareStateW1a:not([hidden]) .rx-share-state-text')?.textContent==={json.dumps(MSG_INVALID)}", timeout=6000)
        await page.wait_for_timeout(400)
        state = await page.evaluate("""()=>({card:document.querySelectorAll('.rx46-card').length,pwned:window.__w1aPwned===1,
          injected:document.querySelectorAll('img[onerror],svg[onload],[onload],[onerror]').length,car:new URL(location.href).searchParams.getAll('car')})""")
        assert state == {"card": 0, "pwned": False, "injected": 0, "car": []}, (label, raw, state)
        if i == 0:
            await page.screenshot(path=str(OUT / f"w1a_{label}_invalid.png"))
            await assert_target(page, "#rxShareStateW1a .rx-share-state-x", f"{label}:notice-close")
    assert len(car_calls) == calls_before, ("invalid code reached the CAR endpoint", car_calls[calls_before:])

    if width >= 768:
        # 10. SICAR said "not found": honest notice, no card.
        await page.goto(f"{BASE}/?car={NOTFOUND}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await page.wait_for_function(f"document.querySelector('#rxShareStateW1a:not([hidden]) .rx-share-state-text')?.textContent==={json.dumps(MSG_NOTFOUND)}", timeout=8000)
        assert await page.locator(".rx46-card").count() == 0 and await url_car(page) == []
        # 11. An answer for another CAR is never shown: one retry, then discreet pending + retry button.
        await page.goto(f"{BASE}/?car={MISMATCH}", wait_until="domcontentloaded", timeout=60000)
        await wait_runtime(page)
        await page.wait_for_function(f"document.querySelector('#rxShareStateW1a:not([hidden]) .rx-share-state-text')?.textContent==={json.dumps(MSG_PENDING)}", timeout=12000)
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

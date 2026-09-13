from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000/"
OUT = Path("artifacts")
DENSE = {"lat": -18.4448863, "lon": -44.2181254, "zoom": 13}
results = {"viewports": {}, "movements": [], "console": []}


async def js(page, expr, arg=None):
    if arg is None:
        return await page.evaluate(expr)
    return await page.evaluate(expr, arg)


async def wait_runtime(page):
    # The boot guard reloads once. Do not type a search into the document that
    # is about to be replaced, or the input vanishes before the button click.
    await page.wait_for_function("sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')", timeout=15000)
    await page.wait_for_function("window.rxV46Installed===true", timeout=15000)
    await page.wait_for_function(
        "typeof map!=='undefined' && !!map && !!map.getBounds", timeout=10000
    )


async def set_dense(page):
    await js(page, "p=>map.setView([p.lat,p.lon],p.zoom,{animate:false})", DENSE)
    await page.wait_for_function(
        "document.querySelectorAll('.leaflet-overlay-pane path.leaflet-interactive').length>0",
        timeout=25000,
    )


async def map_center(page):
    return await js(page, "()=>{const c=map.getCenter();return [c.lat,c.lng,map.getZoom()]}")


async def visible_parcels(page):
    return await js(
        page,
        """()=>{const mr=document.querySelector('#map')?.getBoundingClientRect();if(!mr)return 0;return [...document.querySelectorAll('.leaflet-overlay-pane path.leaflet-interactive')].filter(p=>{const r=p.getBoundingClientRect();return r.width>1&&r.height>1&&r.right>mr.left&&r.left<mr.right&&r.bottom>mr.top&&r.top<mr.bottom}).length}""",
    )


async def proven_empty_map_point(page):
    return await js(
        page,
        """()=>{
          const mapEl=document.querySelector('#map'),r=mapEl?.getBoundingClientRect();
          if(!r)return null;
          const fx=[.08,.16,.24,.32,.40,.48,.56,.64,.72,.80,.88,.94];
          const fy=[.12,.22,.32,.42,.52,.62,.72,.82,.90];
          for(const yy of fy)for(const xx of fx){
            const x=r.left+r.width*xx,y=r.top+r.height*yy,el=document.elementFromPoint(x,y);
            if(!el||!el.closest('#map'))continue;
            if(el.closest('.leaflet-popup,.leaflet-control,.leaflet-interactive'))continue;
            return {x,y,tag:el.tagName,id:el.id||null,cls:String(el.className||'')};
          }
          return null;
        }""",
    )


async def click_first_parcel(page):
    paths = page.locator(".leaflet-overlay-pane path.leaflet-interactive")
    n = await paths.count()
    assert n > 0, "no interactive CAR polygon to click"
    for i in range(min(n, 12)):
        try:
            b = await paths.nth(i).bounding_box()
            if b and b["width"] > 4 and b["height"] > 4:
                await paths.nth(i).click(
                    position={
                        "x": max(2, min(b["width"] / 2, b["width"] - 2)),
                        "y": max(2, min(b["height"] / 2, b["height"] - 2)),
                    },
                    force=True,
                )
                await page.wait_for_selector(".rx46-card", state="visible", timeout=5000)
                return i
        except Exception:
            pass
    raise AssertionError("could not click a visible CAR polygon")


async def assert_card_contract(page, center_before):
    await page.wait_for_selector(".rx46-card", state="visible", timeout=6000)
    await page.wait_for_timeout(700)
    center_after = await map_center(page)
    assert abs(center_before[0] - center_after[0]) < 1e-9 and abs(
        center_before[1] - center_after[1]
    ) < 1e-9, (center_before, center_after)
    card = page.locator(".rx46-card")
    text = await card.inner_text()
    folded = text.casefold()
    for required in (
        "CAR",
        "Mapa KML",
        "Consultar no SICAR (site oficial)",
        "Área",
        "Área (m²)",
        "Status",
        "Tipo",
        "Condição",
        "Módulos fiscais",
        "Criação",
        "Atualização",
        "VER ANÁLISE COMPLETA",
    ):
        assert required.casefold() in folded, (required, text)
    assert "consultar demonstrativo car" not in folded, text
    for forbidden in (
        "RISCO NÃO CLASSIFICADO",
        "FONTES RESPONDERAM",
        "gerar PDF",
    ):
        assert forbidden.casefold() not in folded, (forbidden, text)
    assert await page.locator(".rx46-anchor-popup .leaflet-popup-tip").count() == 1
    box = await card.bounding_box()
    assert box and 195 <= box["width"] <= 235, box
    assert await page.locator(".rx45-panel-card").count() == 0, "V45 opened before CTA"
    title = (await page.locator(".rx46-title").inner_text()).strip()
    assert title and title.casefold() != "imóvel rural", title
    dates = await page.locator(".rx46-field").all_inner_texts()
    date_fields = [
        x
        for x in dates
        if x.casefold().startswith("criação") or x.casefold().startswith("atualização")
    ]
    assert len(date_fields) == 2, date_fields
    for x in date_fields:
        val = x.split("\n")[-1].strip()
        assert val == "—" or re.fullmatch(r"\d{2}/\d{2}/\d{4}", val), x
    status = (await page.locator(".rx46-status").inner_text()).strip()
    assert status not in {"AT", "PE", "CA", "SU", "IN"}, status
    type_text = " ".join(await page.locator(".rx46-field").all_inner_texts())
    assert "\nIRU" not in type_text, type_text
    return {"title": title, "text": text, "box": box}


async def assert_official_sicar_action(page):
    car = (await page.locator(".rx46-card").get_attribute("data-car") or "").strip()
    assert car, "selected card has no CAR code"
    await page.evaluate(
        """()=>{
          window.__rx46Opened=[];
          window.__rx46Copied=[];
          window.open=(url,target,features)=>{window.__rx46Opened.push({url:String(url),target:String(target||''),features:String(features||'')});return null};
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


async def open_full(page):
    await page.locator('[data-rx46-action="full"]').click()
    await page.wait_for_selector(".rx45-panel-card", state="visible", timeout=10000)
    # V46 intentionally schedules normalization at 70/220/650/1300 ms so the
    # final pass sees asynchronously rendered V45 content. Validate after that
    # final product pass rather than against an earlier intermediate frame.
    await page.wait_for_timeout(1450)
    assert await page.locator(".rx46-card").count() == 0
    text = await page.locator(".rx45-panel-card").inner_text()
    assert "RISCO NÃO CLASSIFICADO" in text, text
    assert "Fonte não consultada não significa ausência de ocorrência" in text, text
    assert "fontes responderam" in text.lower(), text
    assert "VER ANÁLISE COMPLETA" in text
    assert not re.search(r"20\d{2}-\d{2}-\d{2}T\d{2}:", text), text
    return text


async def viewport_flow(browser, width, height, label):
    context = await browser.new_context(
        viewport={"width": width, "height": height}, accept_downloads=True
    )
    page = await context.new_page()
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
    before = await map_center(page)
    await click_first_parcel(page)
    card = await assert_card_contract(page, before)
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

    before = await map_center(page)
    await click_first_parcel(page)
    await assert_card_contract(page, before)
    await open_full(page)
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
        await js(page, code)
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
    fills = await js(page, """()=>{const rows=[];map.eachLayer(l=>{if(l._path&&l.feature?.properties?.cod_imovel){const s=getComputedStyle(l._path);rows.push({fill:s.fill,opacity:Number(s.fillOpacity)})}});return rows}""")
    assert fills and all(x['fill'] != 'none' and .15 <= x['opacity'] <= .25 for x in fills), fills


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


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
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
        await browser.close()
    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RX_V46_BROWSER_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())


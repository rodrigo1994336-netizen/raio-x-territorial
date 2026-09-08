from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000/"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OUT = Path("artifacts-v47")
VIEWPORTS = (
    (375, 812, "375"),
    (768, 900, "768"),
    (1440, 900, "1440"),
)

EXPECTED = (
    "Nenhuma inconsistência cadastral: o imóvel não se sobrepõe a outro CAR e está inteiramente dentro de Curvelo/MG.",
    "Sobreposição CAR × CAR",
    "0,0000 ha · 0,00% do imóvel",
    "Município · Curvelo/MG",
    "Estado · Minas Gerais (MG)",
    "Dentro do limite",
    "Snapshot SICAR: 04/08/2026",
    "Vegetação nativa",
    "Reserva legal",
    "Nenhuma feição desta classe no imóvel",
    "APP",
    "1,4547 ha",
    "1,4556 ha · 100,00%",
    "Uso restrito",
    "Área consolidada",
    "12,5701 ha",
    "12,5778 ha · 100,00%",
    "Hidrografia",
    "Campo não publicado",
    "0,4108 ha · 100,00%",
    "Regeneração",
    "Não é campo declarado",
    "1,8155 ha · 12,26%",
)


async def wait_runtime(page):
    await page.wait_for_function(
        "sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')",
        timeout=20000,
    )
    await page.wait_for_function("window.rxV46Installed===true", timeout=20000)
    await page.wait_for_function("window.rxV47IntegrityInstalled===true", timeout=20000)


async def open_curvelo(page):
    await page.locator("#q").fill(CAR)
    await page.locator("#go").click()
    await page.locator(f'.rx46-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
    await page.locator('[data-rx46-action="full"]').click()
    await page.locator(f'.rx45-panel-card[data-car="{CAR}"]').wait_for(state="visible", timeout=15000)

    # V46 performs finite late normalization. Attach V47 only to the current node.
    await page.wait_for_timeout(1550)
    await page.wait_for_function("typeof window.rxV47LoadIntegrity==='function'", timeout=10000)
    table_selector = f'.rx45-panel-card[data-car="{CAR}"] .rx45-integrity-table'
    for _ in range(6):
        if await page.locator(table_selector).count():
            try:
                await page.locator(table_selector).wait_for(state="visible", timeout=1200)
                return
            except Exception:
                pass
        await page.evaluate("window.rxV47LoadIntegrity()")
        await page.wait_for_timeout(1350)
    await page.locator(table_selector).wait_for(state="visible", timeout=10000)


async def current_panel(page):
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    await panel.wait_for(state="visible", timeout=10000)
    return panel


async def current_integrity(page):
    integrity = page.locator(f'.rx45-panel-card[data-car="{CAR}"] .rx45-integrity-slot')
    await integrity.wait_for(state="visible", timeout=10000)
    return integrity


async def assert_mobile_no_truncation(page):
    layout = await page.evaluate(
        """car=>{
          const root=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"] .rx45-integrity-slot`);
          if(!root)return {missing:true};
          const scroll=root.querySelector('.rx45-integrity-scroll');
          const clipped=[...root.querySelectorAll('.rx45-integrity-table tbody th,.rx45-integrity-table tbody td,.rx45-integrity-check b,.rx45-integrity-check span')]
            .filter(el=>el.getClientRects().length && el.scrollWidth>el.clientWidth+1)
            .map(el=>({text:el.textContent,clientWidth:el.clientWidth,scrollWidth:el.scrollWidth}));
          const thead=root.querySelector('.rx45-integrity-table thead');
          return {missing:false,horizontalOverflow:scroll?scroll.scrollWidth-scroll.clientWidth:999,clipped,theadDisplay:thead?getComputedStyle(thead).display:null};
        }""",
        CAR,
    )
    assert not layout.get("missing"), layout
    assert layout["horizontalOverflow"] <= 1, layout
    assert not layout["clipped"], layout
    assert layout["theadDisplay"] == "none", layout


async def position_integrity(page, edge: str):
    state = await page.evaluate(
        """({car,edge})=>{
          const host=document.querySelector('#rx43SnapshotHost');
          const root=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"] .rx45-integrity-slot`);
          if(!host||!root)return {ok:false};
          const hr=host.getBoundingClientRect(),rr=root.getBoundingClientRect();
          if(edge==='top') host.scrollTop += rr.top-hr.top-4;
          else host.scrollTop += rr.bottom-hr.bottom+4;
          return {ok:true,hostScrollTop:host.scrollTop};
        }""",
        {"car": CAR, "edge": edge},
    )
    assert state.get("ok"), state
    await page.wait_for_timeout(350)


async def assert_top_evidence_visible(page):
    state = await page.evaluate(
        """car=>{
          const host=document.querySelector('#rx43SnapshotHost');
          const root=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"] .rx45-integrity-slot`);
          if(!host||!root)return {ok:false};
          const hr=host.getBoundingClientRect();
          const els=[root.querySelector('.rx45-integrity-reading'),...root.querySelectorAll('.rx45-integrity-check')];
          const rects=els.map(el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,text:el.textContent}});
          const visible=rects.every(r=>r.bottom>hr.top+1&&r.top<hr.bottom-1);
          return {ok:true,host:{top:hr.top,bottom:hr.bottom},rects,visible};
        }""",
        CAR,
    )
    assert state.get("ok") and state.get("visible"), state


async def assert_bottom_evidence_visible(page):
    state = await page.evaluate(
        """car=>{
          const host=document.querySelector('#rx43SnapshotHost');
          const root=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"] .rx45-integrity-slot`);
          if(!host||!root)return {ok:false};
          const hr=host.getBoundingClientRect();
          const rows=[...root.querySelectorAll('.rx45-integrity-table tbody tr')];
          const regen=rows.find(r=>/Regeneração/i.test(r.textContent||''));
          const note=root.querySelector('.rx45-integrity-note');
          const visible=el=>{if(!el)return false;const r=el.getBoundingClientRect();return r.bottom>hr.top+1&&r.top<hr.bottom-1};
          return {ok:true,regenVisible:visible(regen),noteVisible:visible(note)};
        }""",
        CAR,
    )
    assert state.get("ok") and state.get("regenVisible") and state.get("noteVisible"), state


async def run_viewport(browser, width: int, height: int, label: str):
    context = await browser.new_context(viewport={"width": width, "height": height})
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)

    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await open_curvelo(page)

    panel = await current_panel(page)
    panel_text = await panel.inner_text()
    assert "Curvelo" in panel_text and CAR in panel_text, (label, panel_text[:1200])

    integrity = await current_integrity(page)
    text = await integrity.inner_text()
    folded = text.casefold()
    for expected in EXPECTED:
        assert expected.casefold() in folded, (label, expected, text)
    assert "fonte indisponível" not in folded, text
    assert "consultando composição" not in folded, text
    assert "indisponível" not in folded, text
    assert "1.4556" not in text and "12.5778" not in text and "100.00%" not in text, text
    assert not errors, errors
    if width <= 390:
        await assert_mobile_no_truncation(page)

    # Real-screen evidence at two actual scroll positions. No CSS/layout is
    # modified for screenshots: top proves reading+three checks, bottom proves
    # final table rows+legal note are reachable/readable at each viewport.
    await position_integrity(page, "top")
    await assert_top_evidence_visible(page)
    top_shot = OUT / f"v47-curvelo-{label}-top.png"
    await page.screenshot(path=str(top_shot), full_page=False)

    await position_integrity(page, "bottom")
    await assert_bottom_evidence_visible(page)
    bottom_shot = OUT / f"v47-curvelo-{label}-bottom.png"
    await page.screenshot(path=str(bottom_shot), full_page=False)

    data = {
        "width": width,
        "height": height,
        "car": CAR,
        "integrity_text": text,
        "errors": errors,
        "top_screenshot": str(top_shot),
        "bottom_screenshot": str(bottom_shot),
    }
    print("RX_V47_CURVELO_VISUAL", json.dumps(data, ensure_ascii=False))
    await context.close()
    return data


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for width, height, label in VIEWPORTS:
                results.append(await run_viewport(browser, width, height, label))
        finally:
            await browser.close()
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "browser-executed.txt").write_text("V47_CURVELO_VISUAL_TOP_BOTTOM\n", encoding="utf-8")
    print("RX_V47_CURVELO_VISUAL_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())

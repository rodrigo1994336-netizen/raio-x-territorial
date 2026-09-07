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

# Exact customer-facing strings in the current candidate. Summary/boundary
# values use pt-BR formatting while table rows still carry dot decimals.
EXPECTED = (
    "Sobreposição com outros CARs",
    "0 CAR(s) · 0,0000 ha · 0,00%",
    "Vegetação nativa",
    "Reserva legal",
    "APP",
    "1.4547 ha",
    "1.4556 ha · 100.00%",
    "Uso restrito",
    "Área consolidada",
    "12.5701 ha",
    "12.5778 ha · 100.00%",
    "Hidrografia",
    "Campo não publicado",
    "0.4108 ha · 100.00%",
    "Regeneração",
    "Não é campo declarado",
    "1.8155 ha · 12.26%",
    "Município: 0,0000 ha fora · 0,00%",
    "UF: 0,0000 ha fora · 0,00%",
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

    # V46 intentionally normalizes the V45 panel through 1300 ms. Attaching the
    # V47 section before that can target a node that is about to be replaced.
    await page.wait_for_timeout(1550)
    await page.wait_for_function("typeof window.rxV47LoadIntegrity==='function'", timeout=10000)

    table_selector = f'.rx45-panel-card[data-car="{CAR}"] .rx45-integrity-table'
    # Finite retry only. Each attempt schedules the V47 extension against the
    # current panel node; no product polling/observer is introduced.
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
    assert not errors, errors

    # Scroll using the current DOM and re-acquire before each capture, because
    # late enrichment is allowed to replace panel nodes.
    await page.evaluate(
        """car=>{
          const sel=`.rx45-panel-card[data-car="${CSS.escape(car)}"] .rx45-integrity-slot`;
          const el=document.querySelector(sel);
          if(!el) throw new Error('V47 integrity slot missing before screenshot');
          el.scrollIntoView({block:'center',inline:'nearest'});
        }""",
        CAR,
    )
    await page.wait_for_timeout(300)
    integrity = await current_integrity(page)
    text_after = await integrity.inner_text()
    assert "0 CAR(s) · 0,0000 ha · 0,00%" in text_after, text_after

    shot = OUT / f"v47-curvelo-{label}.png"
    detail = OUT / f"v47-curvelo-{label}-integrity.png"
    await page.screenshot(path=str(shot), full_page=False)
    integrity = await current_integrity(page)
    await integrity.screenshot(path=str(detail))

    data = {
        "width": width,
        "height": height,
        "car": CAR,
        "integrity_text": text_after,
        "errors": errors,
        "screenshot": str(shot),
        "integrity_screenshot": str(detail),
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
    (OUT / "browser-executed.txt").write_text("V47_CURVELO_VISUAL\n", encoding="utf-8")
    print("RX_V47_CURVELO_VISUAL_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())

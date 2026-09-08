from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000/"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OUT = Path("artifacts-v48-mte")
VIEWPORTS = ((375, 812, "375"), (768, 900, "768"), (1440, 900, "1440"))
ORIGINAL_EIGHT = {
    "embargo": "Embargos",
    "prodes": "PRODES",
    "indigenous_land": "Terra Indígena",
    "legal_reserve": "Reserva Legal",
    "conservation_unit": "Un. Conservação",
    "registry": "Matrícula",
    "public_forest": "Floresta Pública",
    "snci": "SNCI",
}


async def wait_runtime(page):
    await page.wait_for_function(
        "sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')",
        timeout=20000,
    )
    await page.wait_for_function("window.rxV46Installed===true", timeout=20000)
    await page.wait_for_function(
        "document.documentElement.innerHTML.includes('RX_CONFORMITY_MTE_V48')",
        timeout=20000,
    )


async def open_panel(page):
    await page.locator("#q").fill(CAR)
    await page.locator("#go").click()
    await page.locator(f'.rx46-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
    await page.locator('[data-rx46-action="full"]').click()
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    await panel.wait_for(state="visible", timeout=15000)
    await page.locator('.rx45-check[data-source="mte_slave_labor"][data-state="blocked_missing_owner_identity"]').wait_for(state="visible", timeout=30000)
    await page.wait_for_timeout(900)
    return panel


async def assert_contract(page, label):
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    text = await panel.inner_text()

    # Source 1 is a regression gate now: later implemented sources may add rows,
    # but the original eight and exactly one MTE row must remain intact.
    rows = panel.locator('.rx45-check')
    assert await rows.count() >= 9, (label, await rows.count(), text)
    for source_id, expected_label in ORIGINAL_EIGHT.items():
        row = panel.locator(f'.rx45-check[data-source="{source_id}"]')
        assert await row.count() == 1, (label, source_id, text)
        assert expected_label.casefold() in (await row.inner_text()).casefold(), (label, source_id)

    mte = panel.locator('.rx45-check[data-source="mte_slave_labor"]')
    assert await mte.count() == 1, (label, await mte.count(), text)
    mte_text = await mte.inner_text()
    folded = mte_text.casefold()
    assert "mte — trabalho escravo" in folded, (label, mte_text)
    assert "não verificada" in folded, (label, mte_text)
    assert "cpf/cnpj" in folded and "nenhum vínculo" in folded, (label, mte_text)
    assert "fonte: mte" in folded, (label, mte_text)
    assert "04/09/2026" in mte_text, (label, mte_text)
    assert await mte.get_attribute("data-state") == "blocked_missing_owner_identity"
    assert await mte.get_attribute("data-answered") == "0"
    assert await mte.locator('.rx45-dot.blocked_missing_owner_identity').count() == 1
    assert "sem ocorrência" not in folded, mte_text

    audit = panel.locator('.rx45-audit-count')
    audit_text = await audit.inner_text()
    audit_folded = audit_text.casefold()
    assert "fontes responderam" in audit_folded, (label, audit_text)
    assert "2 de 18" not in audit_folded, (label, audit_text)
    assert "car/sicar" in audit_folded and "resolução de identidade" in audit_folded, (label, audit_text)

    await audit.locator('#rx45Audit').click()
    audit_box = panel.locator('.rx48-audit-box.open')
    await audit_box.wait_for(state="visible", timeout=3000)
    audit_detail = await audit_box.inner_text()
    detail_folded = audit_detail.casefold()
    assert detail_folded.count("mte — trabalho escravo") == 1, (label, audit_detail)
    assert "não verificada" in detail_folded, (label, audit_detail)
    for expected in ORIGINAL_EIGHT.values():
        assert expected.casefold() in detail_folded, (label, expected, audit_detail)
    await audit.locator('#rx45Audit').click()

    geometry = await page.evaluate(
        """()=>{
          const s=[...document.querySelectorAll('.rx45-section')].find(x=>/Conformidade/i.test(x.querySelector('h4')?.textContent||''));
          if(!s)return {missing:true};
          const clipped=[...s.querySelectorAll('.rx48-check-label,.rx48-check-status,.rx48-check-reason,.rx48-check-meta,.rx45-audit-count,.rx45-check>span')]
            .filter(x=>x.getClientRects().length && x.scrollWidth>x.clientWidth+1)
            .map(x=>({text:x.textContent,client:x.clientWidth,scroll:x.scrollWidth}));
          return {missing:false,clipped,scrollWidth:s.scrollWidth,clientWidth:s.clientWidth};
        }"""
    )
    assert not geometry.get("missing"), geometry
    assert not geometry.get("clipped"), geometry
    assert geometry["scrollWidth"] <= geometry["clientWidth"] + 1, geometry
    return {"panel_text": text, "mte_text": mte_text, "audit": audit_text, "audit_detail": audit_detail}


async def position_conformity(page, edge):
    state = await page.evaluate(
        """edge=>{
          const host=document.querySelector('#rx43SnapshotHost');
          const sec=[...document.querySelectorAll('.rx45-section')].find(x=>/Conformidade/i.test(x.querySelector('h4')?.textContent||''));
          if(!host||!sec)return {ok:false};
          const hr=host.getBoundingClientRect(),sr=sec.getBoundingClientRect();
          if(edge==='top')host.scrollTop+=sr.top-hr.top-4;
          else host.scrollTop+=sr.bottom-hr.bottom+4;
          return {ok:true};
        }""",
        edge,
    )
    assert state.get("ok"), state
    await page.wait_for_timeout(180)


async def run_viewport(browser, width, height, label):
    context = await browser.new_context(viewport={"width": width, "height": height})
    page = await context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    await open_panel(page)
    evidence = await assert_contract(page, label)
    assert not errors, errors

    await position_conformity(page, "top")
    top = OUT / f"v48-mte-{label}-top.png"
    await page.screenshot(path=str(top), full_page=False)
    await position_conformity(page, "bottom")
    bottom = OUT / f"v48-mte-{label}-bottom.png"
    await page.screenshot(path=str(bottom), full_page=False)

    result = {"width": width, "height": height, "car": CAR, **evidence, "errors": errors,
              "top_screenshot": str(top), "bottom_screenshot": str(bottom)}
    print("RX_V48_MTE_VISUAL", json.dumps(result, ensure_ascii=False))
    await context.close()
    return result


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
    (OUT / "browser-executed.txt").write_text("V48_MTE_ADDITIVE_REGRESSION_375_768_1440\n", encoding="utf-8")
    print("RX_V48_MTE_VISUAL_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())

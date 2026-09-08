from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000/"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OUT = Path("artifacts-v48-sinaflor")
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
HIDDEN_FUTURE = (
    "IBAMA — embargos por área",
    "IBAMA — autos por área",
    "ICMBio — embargos por área",
    "ICMBio — autos por área",
    "INCRA — assentamento rural",
    "INCRA — território quilombola",
)


async def wait_runtime(page):
    await page.wait_for_function(
        "sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')",
        timeout=25000,
    )
    await page.wait_for_function("window.rxV46Installed===true", timeout=25000)
    await page.wait_for_function(
        "document.documentElement.innerHTML.includes('RX_CONFORMITY_MTE_V48') && document.documentElement.innerHTML.includes('RX_CONFORMITY_SINAFLOR_V48')",
        timeout=25000,
    )


async def open_panel(page):
    await page.locator("#q").fill(CAR)
    await page.locator("#go").click()
    await page.locator(f'.rx46-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
    await page.locator('[data-rx46-action="full"]').click()
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    await panel.wait_for(state="visible", timeout=15000)
    await page.locator('.rx45-check[data-source="mte_slave_labor"]').wait_for(state="visible", timeout=15000)
    await page.locator('.rx45-check[data-source="sinaflor"]').wait_for(state="visible", timeout=15000)
    await page.locator('.rx45-check[data-source="mte_slave_labor"][data-state="blocked_missing_owner_identity"]').wait_for(state="visible", timeout=35000)
    await page.locator('.rx45-check[data-source="sinaflor"][data-state="checked_spatial_record_unconfirmed"]').wait_for(state="visible", timeout=45000)
    await page.wait_for_timeout(700)
    return panel


async def assert_contract(page, label):
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    text = await panel.inner_text()
    rows = panel.locator('.rx45-check')
    assert await rows.count() == 10, (label, await rows.count(), text)

    for source_id, expected_label in ORIGINAL_EIGHT.items():
        row = panel.locator(f'.rx45-check[data-source="{source_id}"]')
        assert await row.count() == 1, (label, source_id, text)
        assert expected_label.casefold() in (await row.inner_text()).casefold(), (label, source_id)

    folded_panel = text.casefold()
    for hidden in HIDDEN_FUTURE:
        assert hidden.casefold() not in folded_panel, (label, hidden, text)

    mte = panel.locator('.rx45-check[data-source="mte_slave_labor"]')
    mte_text = await mte.inner_text()
    assert "não verificada" in mte_text.casefold(), (label, mte_text)
    assert "cpf/cnpj" in mte_text.casefold(), (label, mte_text)
    assert await mte.get_attribute("data-answered") == "0", (label, mte_text)

    sina = panel.locator('.rx45-check[data-source="sinaflor"]')
    sina_text = await sina.inner_text()
    folded = sina_text.casefold()
    assert "sinaflor — supressão" in folded, (label, sina_text)
    assert "registro espacial" in folded and "vínculo não confirmado" in folded, (label, sina_text)
    assert "20319201908550" in sina_text, (label, sina_text)
    assert "territorial ampla" in folded and "não identifica este car" in folded, (label, sina_text)
    assert "o imóvel não foi declarado como autorizado" in folded, (label, sina_text)
    assert "fonte: ibama/pamgia" in folded, (label, sina_text)
    assert "07/09/2026" in sina_text, (label, sina_text)
    assert await sina.get_attribute("data-state") == "checked_spatial_record_unconfirmed", (label, sina_text)
    assert await sina.get_attribute("data-answered") == "1", (label, sina_text)
    assert await sina.locator('.rx45-dot.checked_spatial_record_unconfirmed').count() == 1, (label, sina_text)
    assert "autorização sinaflor localizada" not in folded, (label, sina_text)

    audit = panel.locator('.rx45-audit-count')
    audit_text = await audit.inner_text()
    audit_folded = audit_text.casefold()
    assert "3 de 12 fontes responderam" in audit_folded, (label, audit_text)
    assert "2 de 11" not in audit_folded and "2 de 18" not in audit_folded, (label, audit_text)

    await audit.locator('#rx45Audit').click()
    box = panel.locator('.rx48-audit-box.open')
    await box.wait_for(state="visible", timeout=3000)
    detail = await box.inner_text()
    detail_folded = detail.casefold()
    assert detail_folded.count("pendente de implementação") == 6, (label, detail)
    for hidden in HIDDEN_FUTURE:
        assert hidden.casefold() in detail_folded, (label, hidden, detail)
    assert detail_folded.count("sinaflor — supressão") == 1, (label, detail)
    assert "registro espacial · vínculo não confirmado" in detail_folded, (label, detail)
    assert "sinaflor — autorização\npendente de implementação" not in detail_folded, (label, detail)
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
    return {"panel_text": text, "mte_text": mte_text, "sinaflor_text": sina_text, "audit": audit_text, "audit_detail": detail}


async def position_conformity(page, edge):
    state = await page.evaluate(
        """edge=>{
          const host=document.querySelector('#rx43SnapshotHost');
          const sec=[...document.querySelectorAll('.rx45-section')].find(x=>/Conformidade/i.test(x.querySelector('h4')?.textContent||''));
          if(!host||!sec)return {ok:false};
          const hr=host.getBoundingClientRect(),sr=sec.getBoundingClientRect();
          if(edge==='top')host.scrollTop+=sr.top-hr.top-4;
          else host.scrollTop+=sr.bottom-hr.bottom+4;
          const live=[...document.querySelectorAll('.rx45-section')].find(x=>/Conformidade/i.test(x.querySelector('h4')?.textContent||''));
          const lr=live?.getBoundingClientRect();
          return {ok:!!live,hostTop:hr.top,hostBottom:hr.bottom,sectionTop:lr?.top,sectionBottom:lr?.bottom};
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
    top = OUT / f"v48-sinaflor-{label}-top.png"
    await page.screenshot(path=str(top), full_page=False)
    await position_conformity(page, "bottom")
    bottom = OUT / f"v48-sinaflor-{label}-bottom.png"
    await page.screenshot(path=str(bottom), full_page=False)

    result = {"width": width, "height": height, "car": CAR, **evidence, "errors": errors,
              "top_screenshot": str(top), "bottom_screenshot": str(bottom)}
    print("RX_V48_SINAFLOR_VISUAL", json.dumps(result, ensure_ascii=False))
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
    (OUT / "browser-executed.txt").write_text("V48_SINAFLOR_ORIGINAL8_MTE_SINAFLOR_DENOMINATOR12_FUTURE6_AUDIT_ONLY_375_768_1440\n", encoding="utf-8")
    print("RX_V48_SINAFLOR_VISUAL_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())

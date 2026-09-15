from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE = os.environ.get("RX_SMOKE_BASE_URL", "http://127.0.0.1:8000/").rstrip("/") + "/"
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

# F2 regression: the conformity rows must resolve even when /v1/live/map-panel
# answers after every legacy fixed timer (~11 s) has already fired, and a
# re-render of the panel must never re-query a source that already answered.
MAP_PANEL_DELAY_S = 12.0
SETTLE_AFTER_FULL_S = 11.5
MAX_QUERIES_PER_SOURCE = 2  # first attempt + the single automatic retry


async def delay_map_panel(page, seconds):
    async def slow(route):
        await asyncio.sleep(seconds)
        try:
            await route.continue_()
        except Exception:
            pass  # page closed while the response was held

    await page.route("**/v1/live/map-panel/**", slow)


def count_conformity_requests(page):
    counts = {"mte": 0, "sinaflor": 0}

    def on_request(request):
        url = request.url
        if "/v1/live/conformity/mte/" in url:
            counts["mte"] += 1
        elif "/v1/live/conformity/sinaflor/" in url:
            counts["sinaflor"] += 1

    page.on("request", on_request)
    return counts


async def assert_no_requery(page, counts, t_full, label):
    # Let every legacy re-render timer (the last one at ~9.5 s after opening) fire first.
    remaining = SETTLE_AFTER_FULL_S - (asyncio.get_running_loop().time() - t_full)
    if remaining > 0:
        await page.wait_for_timeout(int(remaining * 1000))
    assert counts["mte"] <= MAX_QUERIES_PER_SOURCE, (label, "mte_requeried_on_rerender", counts)
    assert counts["sinaflor"] <= MAX_QUERIES_PER_SOURCE, (label, "sinaflor_requeried_on_rerender", counts)
    return dict(counts)


# F1B: the client line no longer shows "N de M fontes responderam"; the audit registry still counts
# (card.__rxSourceAudit), so the consistency contract is read from the registry, not from the text.
COUNTER_JS = r"""(panelSel)=>{const p=document.querySelector(panelSel);if(!p)return null;
  const t=(p.querySelector('.rx45-audit-count')?.innerText||''),a=p.__rxSourceAudit;
  const answered=new Set(['ANSWERED_CLEAR','ANSWERED_HIT']);
  return {text:t,responded:a&&Array.isArray(a.registry)?a.registry.filter(x=>answered.has(x.state)).length:null,total:a&&Array.isArray(a.registry)?a.registry.length:null,
    answered_rows:p.querySelectorAll('.rx45-check[data-answered="1"]').length,
    answered_ids:[...p.querySelectorAll('.rx45-check[data-answered="1"]')].map(x=>x.dataset.source)}}"""


async def assert_audit_counter(page, panel_selector, label):
    # The counter and the rows are read in one tick: SINAFLOR may answer at any
    # moment, so the contract is consistency (CAR base + answered rows), never a
    # fixed number that pins one side of that race.
    probe = await page.evaluate(COUNTER_JS, panel_selector)
    assert probe and probe["total"] == 11, (label, "audit_counter_contract_missing", probe)
    assert probe["responded"] == 1 + probe["answered_rows"], (label, "audit_counter_inconsistent", probe)
    assert "fontes responderam" not in probe["text"].casefold(), (label, "internal_counter_text_shown", probe)
    return probe


async def wait_runtime(page):
    await page.wait_for_function(
        "(window.rxPortalBootReady===true || sessionStorage.getItem('rx-v26-ready-reload')==='1') && !document.querySelector('#rxBootGuard')",
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
    t_full = asyncio.get_running_loop().time()
    panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
    await panel.wait_for(state="visible", timeout=15000)
    await page.locator('.rx45-check[data-source="mte_slave_labor"][data-state="blocked_missing_owner_identity"]').wait_for(state="visible", timeout=45000)
    await page.wait_for_timeout(900)
    return panel, t_full


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
    counter = await assert_audit_counter(page, f'.rx45-panel-card[data-car="{CAR}"]', label)
    assert "mte_slave_labor" not in counter["answered_ids"], (label, counter)
    assert "resolução de identidade" not in audit_folded, (label, audit_text)

    await audit.locator('#rx45Audit').click()
    audit_box = panel.locator('.rx48-audit-box.open')
    await audit_box.wait_for(state="visible", timeout=3000)
    audit_detail = await audit_box.inner_text()
    detail_folded = audit_detail.casefold()
    assert detail_folded.count("mte — trabalho escravo") == 1, (label, audit_detail)
    assert "não verificada" in detail_folded, (label, audit_detail)
    assert "car / sicar" in detail_folded, (label, audit_detail)
    # F1B: the box lists only what this consultation knows; a source nobody asked is not listed and no
    # development wording reaches the client.
    for absent in ("não consultada", "pendente de implementação", "contador", "reserva legal", "matrícula"):
        assert absent not in detail_folded, (label, absent, audit_detail)
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


async def run_viewport(browser, width, height, label, map_panel_delay_s=0.0):
    context = await browser.new_context(viewport={"width": width, "height": height})
    page = await context.new_page()
    if map_panel_delay_s:
        await delay_map_panel(page, map_panel_delay_s)
    counts = count_conformity_requests(page)
    errors = []
    page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
    await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    await wait_runtime(page)
    _, t_full = await open_panel(page)
    evidence = await assert_contract(page, label)
    evidence["conformity_requests"] = await assert_no_requery(page, counts, t_full, label)
    evidence["map_panel_delay_s"] = map_panel_delay_s
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
            results.append(await run_viewport(browser, 375, 812, "375-slow-map-panel", MAP_PANEL_DELAY_S))
        finally:
            await browser.close()
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "browser-executed.txt").write_text("V48_MTE_ADDITIVE_REGRESSION_375_768_1440\n", encoding="utf-8")
    print("RX_V48_MTE_VISUAL_SMOKE=PASS")


if __name__ == "__main__":
    asyncio.run(main())

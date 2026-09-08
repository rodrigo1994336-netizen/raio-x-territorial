from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000/"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
OUT = Path("artifacts-v48-sinaflor")


async def dom_state(page, stage: str, network: list[dict], errors: list[str]) -> dict:
    state = await page.evaluate(
        """({car,stage})=>{
          const panel=document.querySelector(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);
          const card=document.querySelector(`.rx46-card[data-car="${CSS.escape(car)}"]`);
          const rows=panel?[...panel.querySelectorAll('.rx45-check')]:[];
          const audit=panel?.querySelector('.rx45-audit-count');
          const rect=panel?.getBoundingClientRect();
          return {
            stage,
            href:location.href,
            readyState:document.readyState,
            viewport:{w:innerWidth,h:innerHeight},
            sessionReady:sessionStorage.getItem('rx-v26-ready-reload'),
            bootGuard:!!document.querySelector('#rxBootGuard'),
            rxV46Installed:window.rxV46Installed===true,
            hasMteMarker:document.documentElement.innerHTML.includes('RX_CONFORMITY_MTE_V48'),
            hasSinaflorMarker:document.documentElement.innerHTML.includes('RX_CONFORMITY_SINAFLOR_V48'),
            currentCar:String((window.current||{}).car_code||''),
            resultCardExists:!!card,
            fullButtons:document.querySelectorAll('[data-rx46-action="full"]').length,
            panelExists:!!panel,
            panelRect:rect?{x:rect.x,y:rect.y,w:rect.width,h:rect.height}:null,
            rowCount:rows.length,
            rows:rows.map(r=>({
              source:r.dataset.source||'',
              state:r.dataset.state||'',
              answered:r.dataset.answered||'',
              text:(r.innerText||'').replace(/\s+/g,' ').trim().slice(0,320)
            })),
            audit:audit?(audit.innerText||'').replace(/\s+/g,' ').trim():null
          };
        }""",
        {"car": CAR, "stage": stage},
    )
    state["network"] = network[-40:]
    state["errors"] = errors[-30:]
    return state


async def save(timeline: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "v48-sinaflor-768-diagnostic.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def checkpoint(page, timeline: list[dict], stage: str, network: list[dict], errors: list[str]) -> None:
    timeline.append(await dom_state(page, stage, network, errors))
    await save(timeline)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    timeline: list[dict] = []
    network: list[dict] = []
    errors: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 768, "height": 900})
        page = await context.new_page()
        page.on("pageerror", lambda exc: errors.append("pageerror:" + str(exc)))
        page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
        page.on(
            "response",
            lambda response: network.append({"status": response.status, "url": response.url})
            if "/v1/live/" in response.url
            else None,
        )
        try:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
            await checkpoint(page, timeline, "goto_done", network, errors)
            await page.wait_for_function(
                "sessionStorage.getItem('rx-v26-ready-reload')==='1' && !document.querySelector('#rxBootGuard')",
                timeout=25000,
            )
            await page.wait_for_function("window.rxV46Installed===true", timeout=25000)
            await page.wait_for_function(
                "document.documentElement.innerHTML.includes('RX_CONFORMITY_MTE_V48') && document.documentElement.innerHTML.includes('RX_CONFORMITY_SINAFLOR_V48')",
                timeout=25000,
            )
            await checkpoint(page, timeline, "runtime_ready", network, errors)
            await page.locator("#q").fill(CAR)
            await page.locator("#go").click()
            await checkpoint(page, timeline, "search_clicked", network, errors)
            await page.locator(f'.rx46-card[data-car="{CAR}"]').wait_for(state="visible", timeout=60000)
            await checkpoint(page, timeline, "result_card_visible", network, errors)
            await page.locator('[data-rx46-action="full"]').click()
            await checkpoint(page, timeline, "full_clicked", network, errors)
            panel = page.locator(f'.rx45-panel-card[data-car="{CAR}"]')
            await panel.wait_for(state="visible", timeout=15000)
            await checkpoint(page, timeline, "panel_visible", network, errors)
            for i in range(1, 13):
                await page.wait_for_timeout(1000)
                await checkpoint(page, timeline, f"panel_plus_{i}s", network, errors)
                state = timeline[-1]
                row_map = {r["source"]: r for r in state.get("rows", [])}
                if (
                    state.get("rowCount") == 10
                    and row_map.get("mte_slave_labor", {}).get("state") == "blocked_missing_owner_identity"
                    and row_map.get("sinaflor", {}).get("state") == "checked_spatial_record_unconfirmed"
                ):
                    break
            await page.screenshot(path=str(OUT / "v48-sinaflor-768-diagnostic.png"), full_page=False)
            await checkpoint(page, timeline, "diagnostic_finished", network, errors)
        except Exception as exc:
            errors.append(f"exception:{type(exc).__name__}:{exc}")
            try:
                await checkpoint(page, timeline, "exception", network, errors)
                await page.screenshot(path=str(OUT / "v48-sinaflor-768-failure.png"), full_page=False)
            except Exception as inner:
                errors.append(f"diagnostic_capture_exception:{type(inner).__name__}:{inner}")
                await save(timeline + [{"stage": "capture_failed", "errors": errors}])
        finally:
            await context.close()
            await browser.close()
    print("RX_V48_SINAFLOR_768_DIAGNOSTIC=RECORDED")


if __name__ == "__main__":
    asyncio.run(main())

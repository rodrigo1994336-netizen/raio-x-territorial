"""W1a gate: CAR polygons fast on the map without the national grid (headless, no network).

Usage:
  PYTHONPATH=. python scripts/w1a_imoveis_rapidos_gate.py

It boots the real portal (same deferred path as production), replaces only the SICAR WFS call
with a deterministic fake, blocks every outgoing httpx request, and checks the contract:

  * UF without an external service: local IBGE mesh, Nominatim never called for Brazil interior;
    a border cell asks every UF it touches; a border point is not guessed;
  * one request per grid cell, answered from a 6 h byte cache with a short public Cache-Control;
  * incomplete answers (a UF/quadrant failed) are never stored and say no-store;
  * truncated only when a SICAR call reached its 50-feature cap;
  * final portal HTML: canvas renderer, per-cell progressive loader with hysteresis, the
    anchor-state click guard still wired, honest "Há mais nesta área".

Positive control: on origin/main (before W1a) this gate fails (no uf_locator_br, no cell mode,
no Cache-Control, SVG loader). The browser half lives in .github/scripts/w1a_imoveis_rapidos_smoke.py.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL_RELEASE = "V8_OPERATIONAL_ZERO_COST"

if os.getenv("RX_RELEASE") != PORTAL_RELEASE:
    env = dict(os.environ, RX_RELEASE=PORTAL_RELEASE, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.getenv("PYTHONPATH")])))
    sys.exit(subprocess.call([sys.executable, str(Path(__file__).resolve())], env=env, cwd=str(ROOT)))

sys.path.insert(0, str(ROOT))
FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        FAILURES.append(msg)


def section(name: str, fn) -> None:
    print(f"== {name}", flush=True)
    try:
        fn()
    except Exception as exc:  # a crash is a failure of that contract, never a skip
        check(False, f"{name} crashed: {type(exc).__name__}: {str(exc)[:300]}")


# ---------------------------------------------------------------- network lockdown
import httpx  # noqa: E402

OUTBOUND: list[str] = []
_real_async_send = httpx.AsyncClient.send


async def _blocked_send(self, request, *args, **kwargs):
    OUTBOUND.append(str(request.url))
    raise httpx.ConnectError("W1a gate: outbound network blocked", request=request)


httpx.AsyncClient.send = _blocked_send  # type: ignore[assignment]


def nominatim() -> list[str]:
    # Other portal modules probe their own public sources during boot; this gate only
    # judges the UF resolver, so it counts Nominatim calls (the blocked send still stops all).
    return [u for u in OUTBOUND if "nominatim" in u.lower()]


def uf_locator_contract() -> None:
    import uf_locator_br as uf

    uf._load()
    samples = {
        "Pompéu/MG": (-19.2247, -45.0033, "MG"),
        "Curvelo/MG": (-18.7565, -44.4306, "MG"),
        "Uberaba/MG": (-19.7472, -47.9381, "MG"),
        "Campinas/SP": (-22.9056, -47.0608, "SP"),
        "Brasília/DF": (-15.7939, -47.8828, "DF"),
        "Goiânia/GO": (-16.6869, -49.2648, "GO"),
        "Manaus/AM": (-3.1190, -60.0217, "AM"),
        "Porto Alegre/RS": (-30.0346, -51.2177, "RS"),
        "Belém/PA": (-1.4558, -48.4902, "PA"),
        "Cuiabá/MT": (-15.6010, -56.0974, "MT"),
    }
    for label, (lat, lon, want) in samples.items():
        check(uf.uf_for_point(lat, lon) == want, f"UF local {label} = {want}")
    check(uf.uf_for_point(-20.0, -35.0) is None, "UF local: ocean point is not guessed")
    check(uf.uf_for_point(-25.3, -57.6) is None, "UF local: Paraguay point is not guessed")
    # Rio Grande (MG/SP border near Igarapava): the cell must ask both SICAR layers.
    border = uf.ufs_for_bbox(-47.76, -20.08, -47.68, -20.0)
    check(set(border) == {"MG", "SP"}, f"UF local: border cell asks MG and SP ({border})")
    states = {code: geom for code, geom, _prepared, _bounds in uf._load()}
    shared = states["MG"].boundary.intersection(states["SP"].buffer(0.002))
    on_border = shared.interpolate(0.5, normalized=True)
    near = (on_border.y + 0.01, on_border.x)
    check(uf.uf_for_point(on_border.y, on_border.x) is None and uf.uf_for_point(*near) is None,
          f"UF local: points on/near the MG-SP border are not guessed ({on_border.y:.4f},{on_border.x:.4f})")
    check(uf.ufs_for_bbox(-45.04, -19.28, -44.96, -19.2) == ["MG"], "UF local: interior cell asks one UF")
    check(uf.AMBIGUITY_DEG >= 0.03, "UF local: ambiguity band covers 2x the measured 1.46 km border error")


def boot_portal():
    import sitecustomize  # noqa: F401  (starts the deferred portal boot)
    import portal_api  # noqa: F401
    import portal_boot_guard_v26

    deadline = time.time() + 90
    while time.time() < deadline and not portal_boot_guard_v26.STATE.get("ready") and not portal_boot_guard_v26.STATE.get("error"):
        time.sleep(0.1)
    if not portal_boot_guard_v26.STATE.get("ready"):
        raise RuntimeError(f"portal did not boot: {portal_boot_guard_v26.STATE}")


def reverse_uf_contract() -> None:
    import portal_api

    OUTBOUND.clear()
    got = asyncio.run(portal_api._reverse_uf(-19.2247, -45.0033))
    check(got == "MG" and not nominatim(), f"_reverse_uf interior answers locally without Nominatim ({got}, nominatim={len(nominatim())})")


FAKE_COUNTS: dict[str, int] = {}
FAKE_FAIL: set[str] = set()
FAKE_CALLS: list[tuple[str, float, float]] = []


def install_fake_sicar() -> None:
    import portal_sicar_resilient
    from fastapi import HTTPException

    async def fake(request, west, south, east, north, uf=None, limit=50):
        FAKE_CALLS.append((str(uf), round(west, 6), round(south, 6)))
        if uf in FAKE_FAIL:
            raise HTTPException(status_code=502, detail="fake SICAR down")
        n = FAKE_COUNTS.get(str(uf), 12)
        feats = []
        for i in range(n):
            x, y = west + 0.001 * (i + 1), south + 0.001 * (i + 1)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 0.0006, y], [x + 0.0006, y + 0.0006], [x, y + 0.0006], [x, y]]]},
                "properties": {"cod_imovel": f"{uf}-{west:.2f}-{south:.2f}-{i}", "area": 1.5, "municipio": "Teste", "uf": uf,
                               "status_imovel": "AT", "condicao": "Aguardando análise", "tipo_imovel": "IRU", "m_fiscal": 0.1},
            })
        return {"type": "FeatureCollection", "features": feats, "uf": uf, "truncated": len(feats) >= min(limit, 50), "source_bytes": 100 * n}

    portal_sicar_resilient.live_sicar_viewport_resilient = fake


def endpoint_contract() -> None:
    import portal_map_v46 as v46
    import portal_v8
    from fastapi.testclient import TestClient

    install_fake_sicar()
    client = TestClient(portal_v8.app)
    ttl = getattr(v46, "_V46_VIEWPORT_TTL", 0)
    check(6 * 3600 <= ttl <= 24 * 3600, f"server cache TTL is hours, bounded ({ttl}s)")

    def cell(w, s, step, **extra):
        params = {"west": f"{w:.6f}", "south": f"{s:.6f}", "east": f"{w + step:.6f}", "north": f"{s + step:.6f}", "cell": str(step)}
        params.update(extra)
        return client.get("/v1/live/sicar/viewport-v46", params=params)

    # Complete interior cell: cacheable, second hit served from bytes without calling SICAR.
    OUTBOUND.clear()
    FAKE_CALLS.clear()
    FAKE_COUNTS["MG"] = 12
    r1 = cell(-45.04, -19.28, 0.08)
    body1 = r1.json() if r1.status_code == 200 else {}
    cc = r1.headers.get("cache-control", "")
    check(r1.status_code == 200, f"cell request 200 ({r1.status_code})")
    check(cc.startswith("public") and "max-age=" in cc and 0 < int(cc.split("max-age=")[1].split(",")[0]) <= 900,
          f"complete cell has short public Cache-Control ({cc!r})")
    check(body1.get("cached") is False and body1.get("ufs") == ["MG"] and len(body1.get("features") or []) == 12, "complete cell: 12 features from MG, cached=false")
    check(body1.get("truncated") is False, "12 of a 50 cap is not truncated")
    calls_after_first = len(FAKE_CALLS)
    r2 = cell(-45.04, -19.28, 0.08)
    check(r2.status_code == 200 and r2.json().get("cached") is True and len(FAKE_CALLS) == calls_after_first,
          "second identical cell served from server cache without a SICAR call")
    check(r2.headers.get("cache-control", "").startswith("public"), "cache hit keeps public Cache-Control")
    check(not nominatim(), f"no Nominatim request for an interior cell ({nominatim()[:2]})")

    # Truncation is exactly the SICAR cap.
    FAKE_COUNTS["MG"] = 50
    r3 = cell(-45.12, -19.28, 0.08)
    check(r3.status_code == 200 and r3.json().get("truncated") is True, "a SICAR call at its 50-feature cap marks truncated")
    FAKE_COUNTS["MG"] = 49
    r4 = cell(-45.20, -19.28, 0.08)
    check(r4.status_code == 200 and r4.json().get("truncated") is False, "49 features is not truncated")

    # Border cell with one UF failing: shown, never stored, no-store.
    FAKE_COUNTS["MG"] = 10
    FAKE_COUNTS["SP"] = 7
    FAKE_FAIL.add("SP")
    FAKE_CALLS.clear()
    r5 = cell(-47.76, -20.08, 0.08)
    b5 = r5.json() if r5.status_code == 200 else {}
    check(r5.status_code == 200 and b5.get("partial_failures") == 1 and len(b5.get("features") or []) == 10,
          f"border cell with SP down still shows MG and reports partial_failures ({r5.status_code}, {b5.get('partial_failures')})")
    check(r5.headers.get("cache-control") == "no-store", f"incomplete cell is no-store ({r5.headers.get('cache-control')!r})")
    before = len(FAKE_CALLS)
    r6 = cell(-47.76, -20.08, 0.08)
    check(r6.status_code == 200 and r6.json().get("cached") is False and len(FAKE_CALLS) > before, "incomplete cell is never served from cache")
    FAKE_FAIL.discard("SP")
    r7 = cell(-47.76, -20.08, 0.08)
    b7 = r7.json() if r7.status_code == 200 else {}
    check(r7.status_code == 200 and set(b7.get("ufs") or []) == {"MG", "SP"} and len(b7.get("features") or []) == 17,
          "border cell asks both SICAR layers once SP answers (10 + 7 features)")

    # Every UF down: 502, nothing stored.
    FAKE_FAIL.update({"MG"})
    r8 = cell(-45.28, -19.28, 0.08)
    check(r8.status_code == 502, f"cell with SICAR down is 502, not an empty map ({r8.status_code})")
    FAKE_FAIL.clear()
    r9 = cell(-45.28, -19.28, 0.08)
    check(r9.status_code == 200 and r9.json().get("cached") is False, "a failure is never cached")

    # Invalid cells.
    bad = client.get("/v1/live/sicar/viewport-v46", params={"west": "-45.03", "south": "-19.28", "east": "-44.95", "north": "-19.2", "cell": "0.08"})
    check(bad.status_code == 422, f"misaligned cell rejected ({bad.status_code})")
    bad2 = cell(-45.05, -19.25, 0.05)
    check(bad2.status_code == 422, f"cell step outside the grid rejected ({bad2.status_code})")

    # Legacy bbox mode keeps working, UF local, Cache-Control on complete answers.
    OUTBOUND.clear()
    FAKE_COUNTS["MG"] = 5
    legacy = client.get("/v1/live/sicar/viewport-v46", params={"west": -45.1, "south": -19.3, "east": -44.9, "north": -19.15, "limit": 200, "zoom": 13})
    check(legacy.status_code == 200 and legacy.json().get("uf") == "MG" and not nominatim(), "legacy bbox mode: UF local, no Nominatim request")
    check(legacy.headers.get("cache-control", "").startswith("public"), "legacy bbox complete answer has public Cache-Control")


def html_contract() -> None:
    import portal_v8

    html = portal_v8.PORTAL_HTML
    check(html.count("async function loadVisibleParcels(force){") == 1, "exactly one effective CAR loader")
    for token, why in (
        ("L.canvas({pane:'rxParcelPane'", "parcels drawn by a canvas renderer in their own pane"),
        ("u.searchParams.set('cell',String(s))", "one request per grid cell"),
        ("window.rx46ViewportRequest(u,{signal:ctrl.signal})", "cell requests are abortable"),
        ("rxW1aDraw(m,cell,d.features)", "each cell is drawn when it arrives"),
        ("inside&&covered&&!force", "hysteresis: no request while the view stays inside the planned margin"),
        ("pw=(east-west)*.25,ph=(north-south)*.25", "margin of 25% per side (~50% folga)"),
        ("Mostrando ${n} imóveis. Há mais nesta área.", "truncation notice uses the drawn count"),
        ("Parte dos imóveis desta área ainda não carregou.", "a failed cell becomes a quiet pending line"),
        ("e.originalEvent.__rx46ParcelClick=true", "anchor-state click guard still wraps the parcel click"),
        ("window.rxV46SelectProperty(p,live.geometry,e.latlng)", "parcel click opens the V46 card"),
        ("window.rxFieldMode?850:120", "debounce: field mode unchanged, normal 120 ms"),
        ("window.rxW1aGridState=", "grid state exposed for the browser smoke"),
    ):
        check(token in html, why)
    start = html.find("window.rx46ViewportRequest=async function(")
    request_js = html[start:html.find("};", start)] if start >= 0 else ""
    check(bool(request_js) and "no-store" not in request_js, "viewport request honours HTTP cache (no background no-store refetch)")
    check("/v1/live/property-names/viewport" not in html, "C1 still holds: no name request from the map")


section("UF local mesh", uf_locator_contract)
booted = len(FAILURES)
section("portal boot", boot_portal)
if len(FAILURES) == booted:  # every contract below needs the real booted portal
    section("_reverse_uf local-first", reverse_uf_contract)
    section("viewport-v46 cell endpoint", endpoint_contract)
    section("final portal HTML", html_contract)

httpx.AsyncClient.send = _real_async_send  # type: ignore[assignment]
if FAILURES:
    print(f"W1A_GATE=FAIL ({len(FAILURES)})", flush=True)
    for f in FAILURES:
        print("  - " + f, flush=True)
    os._exit(1)
print("W1A_GATE=PASS", flush=True)
os._exit(0)

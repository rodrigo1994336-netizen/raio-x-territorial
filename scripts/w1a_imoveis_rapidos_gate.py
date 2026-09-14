"""W1a gate: CAR polygons fast on the map without the national grid (headless, no network).

Usage:
  PYTHONPATH=. python scripts/w1a_imoveis_rapidos_gate.py

It boots the real portal (same deferred path as production), replaces only the SICAR WFS call
with a deterministic fake, blocks every outgoing httpx request, and checks the contract:

  * UF without an external service: local IBGE mesh, Nominatim never called for Brazil interior;
    a border cell asks every UF it touches; a border point is not guessed;
  * one request per grid cell, answered from a 6 h byte cache with a short public Cache-Control;
  * incomplete answers (a UF/quadrant failed) are never stored and say no-store;
  * truncated only when a SICAR call reached its 50-feature cap; every cell carries fetched_at;
  * SICAR calls: identical concurrent requests share ONE call (single-flight), at most
    _V46_SICAR_CONCURRENCY at once, the first caller leaving never cancels another caller's
    answer, and a queued call whose callers all left is never made;
  * the loader region V46 replaces still carries the five upstream anchors (boot drift checks)
    and its text matches the pinned fingerprint for this release chain;
  * final portal HTML: canvas renderer, per-cell loader, the anchor-state click guard still wired.

The HTML section only proves the pieces are wired. The BEHAVIOUR in the browser (canvas, hysteresis,
honest "Há mais nesta área" after moves, stale status hidden on the card) is enforced by
.github/scripts/w1a_imoveis_rapidos_smoke.py, which carries its own mutation controls.

Positive controls (mutations, run on every green execution after the real checks): single-flight
off, concurrency limit off, one caller leaving cancelling the shared call, partial answers
classified as complete, and an upstream edit inside the loader region. Each must turn a NAMED
check red; a control that passes, or turns red somewhere else, fails the gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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


CAPTURING = False


def check(cond: bool, msg: str) -> None:
    # Inside a mutation control a red check is the expected outcome: logged as "red ", never "FAIL".
    print(("ok   " if cond else ("red  " if CAPTURING else "FAIL ")) + msg, flush=True)
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
FAKE_FAIL_AT: set[tuple[str, float]] = set()  # (uf, west rounded to 2 decimals)
FAKE_CALLS: list[tuple[str, float, float]] = []


def install_fake_sicar() -> None:
    import portal_sicar_resilient
    from fastapi import HTTPException

    async def fake(request, west, south, east, north, uf=None, limit=50):
        FAKE_CALLS.append((str(uf), round(west, 6), round(south, 6)))
        if uf in FAKE_FAIL or (str(uf), round(west, 2)) in FAKE_FAIL_AT:
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
    v46._V46_VIEWPORT_CACHE.clear()  # controls re-run this contract; start from a cold server
    v46._V46_VIEWPORT_CACHE_BYTES = 0
    flight_calls0 = v46._V46_SICAR_STATS["calls"]
    t_before = time.time()
    r1 = cell(-45.04, -19.28, 0.08)
    body1 = r1.json() if r1.status_code == 200 else {}
    cc = r1.headers.get("cache-control", "")
    check(r1.status_code == 200, f"cell request 200 ({r1.status_code})")
    check(cc.startswith("public") and "max-age=" in cc and 0 < int(cc.split("max-age=")[1].split(",")[0]) <= 900,
          f"complete cell has short public Cache-Control ({cc!r})")
    check(body1.get("cached") is False and body1.get("ufs") == ["MG"] and len(body1.get("features") or []) == 12, "complete cell: 12 features from MG, cached=false")
    check(body1.get("truncated") is False, "12 of a 50 cap is not truncated")
    check(v46._V46_SICAR_STATS["calls"] - flight_calls0 == len(FAKE_CALLS) == 1,
          f"cell endpoint reaches SICAR only through the single-flight limiter ({v46._V46_SICAR_STATS['calls'] - flight_calls0} vs {len(FAKE_CALLS)})")
    fetched = body1.get("fetched_at")
    check(isinstance(fetched, (int, float)) and t_before - 1 <= fetched <= time.time() + 1, f"cell payload says when SICAR was read ({fetched})")
    calls_after_first = len(FAKE_CALLS)
    r2 = cell(-45.04, -19.28, 0.08)
    check(r2.status_code == 200 and r2.json().get("cached") is True and len(FAKE_CALLS) == calls_after_first,
          "second identical cell served from server cache without a SICAR call")
    check(r2.headers.get("cache-control", "").startswith("public"), "cache hit keeps public Cache-Control")
    check(r2.json().get("fetched_at") == fetched, "cache hit keeps the original SICAR read time (never re-stamped as fresh)")
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

    # Legacy bbox mode, one of four quadrants down: shown, never stored, no-store.
    FAKE_FAIL_AT.add(("MG", -45.1))
    lp = {"west": -45.1, "south": -19.5, "east": -44.9, "north": -19.35, "limit": 200, "zoom": 13}
    before = len(FAKE_CALLS)
    l1 = client.get("/v1/live/sicar/viewport-v46", params=lp)
    b_l1 = l1.json() if l1.status_code == 200 else {}
    check(l1.status_code == 200 and b_l1.get("partial_failures", 0) >= 1 and len(FAKE_CALLS) > before,
          f"legacy partial: a failed quadrant is reported ({l1.status_code}, {b_l1.get('partial_failures')})")
    check(l1.headers.get("cache-control") == "no-store", f"legacy partial is no-store ({l1.headers.get('cache-control')!r})")
    before = len(FAKE_CALLS)
    l2 = client.get("/v1/live/sicar/viewport-v46", params=lp)
    check(l2.status_code == 200 and l2.json().get("cached") is False and len(FAKE_CALLS) > before,
          "legacy partial is never served from cache")
    FAKE_FAIL_AT.clear()


class FakeRequest:
    def __init__(self, gone: bool = False) -> None:
        self.gone = gone

    async def is_disconnected(self) -> bool:
        return self.gone


FLIGHT = {"calls": 0, "active": 0, "peak": 0}


def flight_reset() -> None:
    import portal_map_v46 as v46

    v46._V46_SICAR_INFLIGHT.clear()
    v46._V46_SICAR_LIMITER.clear()
    FLIGHT.update(calls=0, active=0, peak=0)


def sicar_flight_contract() -> None:
    import portal_map_v46 as v46
    import portal_sicar_resilient
    from fastapi import HTTPException

    async def slow_fake(request, west, south, east, north, uf=None, limit=50):
        # Same lifecycle as the real transport: it polls the request and aborts on disconnect.
        FLIGHT["calls"] += 1
        FLIGHT["active"] += 1
        FLIGHT["peak"] = max(FLIGHT["peak"], FLIGHT["active"])
        try:
            for _ in range(6):
                if await request.is_disconnected():
                    raise HTTPException(status_code=499, detail="client_disconnected")
                await asyncio.sleep(0.04)
            return {"type": "FeatureCollection", "features": [], "uf": uf, "truncated": False, "source_bytes": 1}
        finally:
            FLIGHT["active"] -= 1

    saved = portal_sicar_resilient.live_sicar_viewport_resilient
    portal_sicar_resilient.live_sicar_viewport_resilient = slow_fake
    box = (-45.04, -19.28, -44.96, -19.2)
    limit = v46._V46_SICAR_CONCURRENCY
    try:
        async def identical():
            return await asyncio.gather(*(v46._sicar_viewport_call(FakeRequest(), *box, "MG", 50) for _ in range(8)), return_exceptions=True)

        flight_reset()
        res = asyncio.run(identical())
        check(FLIGHT["calls"] == 1 and all(isinstance(r, dict) for r in res),
              f"single-flight: 8 identical concurrent requests make ONE SICAR call ({FLIGHT['calls']} calls)")

        async def distinct():
            return await asyncio.gather(*(v46._sicar_viewport_call(FakeRequest(), box[0] - i * 0.08, box[1], box[2] - i * 0.08, box[3], "MG", 50) for i in range(20)), return_exceptions=True)

        flight_reset()
        res = asyncio.run(distinct())
        check(1 <= limit <= 8 and FLIGHT["calls"] == 20 and FLIGHT["peak"] == limit and all(isinstance(r, dict) for r in res),
              f"concurrency limit: 20 distinct calls all answered, at most {limit} at once (peak {FLIGHT['peak']})")

        async def leader_leaves():
            leader, follower = FakeRequest(), FakeRequest()
            t1 = asyncio.ensure_future(v46._sicar_viewport_call(leader, *box, "MG", 50))
            await asyncio.sleep(0.01)
            t2 = asyncio.ensure_future(v46._sicar_viewport_call(follower, *box, "MG", 50))
            await asyncio.sleep(0.03)
            leader.gone = True
            return await asyncio.gather(t1, t2, return_exceptions=True)

        flight_reset()
        res = asyncio.run(leader_leaves())
        check(isinstance(res[1], dict) and FLIGHT["calls"] == 1,
              f"single-flight: the first caller leaving does not cancel the answer a second caller waits for ({type(res[1]).__name__}, {FLIGHT['calls']} calls)")

        async def queued_and_gone():
            busy = [asyncio.ensure_future(v46._sicar_viewport_call(FakeRequest(), box[0] - i * 0.08, box[1], box[2] - i * 0.08, box[3], "SP", 50)) for i in range(max(1, min(limit, 8)))]
            await asyncio.sleep(0.01)
            gone = asyncio.ensure_future(v46._sicar_viewport_call(FakeRequest(gone=True), box[0] + 1, box[1], box[2] + 1, box[3], "SP", 50))
            return await asyncio.gather(*busy, gone, return_exceptions=True)

        flight_reset()
        skipped0 = v46._V46_SICAR_STATS["skipped_disconnected"]
        res = asyncio.run(queued_and_gone())
        live = max(1, min(limit, 8))
        check(FLIGHT["calls"] == live and isinstance(res[-1], HTTPException) and res[-1].status_code == 499
              and v46._V46_SICAR_STATS["skipped_disconnected"] == skipped0 + 1,
              f"a queued SICAR call whose callers all left is never made ({FLIGHT['calls']} calls for {live} live callers)")
    finally:
        portal_sicar_resilient.live_sicar_viewport_resilient = saved
        flight_reset()


# Fingerprint of the upstream loader text V46 replaces, for RX_RELEASE=V8_OPERATIONAL_ZERO_COST.
# If it changes, an upstream module edited the region: port that change into _W1A_LOADER (or
# confirm it is obsolete) and update this pin in the same commit.
W1A_REGION_SHA256 = "edccd3db74ed34b7c6eff0b6621aeb67847879e7859a49c40482f3eba4aae6b3"


def region_drift_contract() -> None:
    import portal_map_v46 as v46

    got = getattr(v46, "_W1A_REGION_SHA256", "")
    check(got == W1A_REGION_SHA256, f"loader region text matches the pinned upstream fingerprint (got {got}, pinned {W1A_REGION_SHA256})")
    # The boot drift detector itself: an anchor that vanished or doubled must raise.
    saved = (v46.html, v46._region_start, v46._region_end)
    anchor = "const u=new URL('/v1/live/sicar/viewport',location.origin);"
    outcomes = {}
    try:
        for label, text in (("once", "A" + anchor + "B"), ("missing", "AB"), ("twice", anchor + anchor)):
            v46.html, v46._region_start, v46._region_end = text, 0, len(text)
            try:
                v46.expect_once_in_region(anchor, "drift")
                outcomes[label] = "ok"
            except RuntimeError:
                outcomes[label] = "raised"
    finally:
        v46.html, v46._region_start, v46._region_end = saved
    check(outcomes == {"once": "ok", "missing": "raised", "twice": "raised"}, f"boot drift check raises when an upstream anchor vanishes or doubles ({outcomes})")


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
    check("pending&&n&&confirmed" not in html, "the stuck-notice branch (previous area's truncation kept while loading) is gone")
    check("window.rxW1aCellFetchedAt=" in html and "RX46_CELL_STATUS_MAX_MS=300000" in html, "card reads the cell data age (status older than 5 min is not shown)")
    consumer = Path(ROOT, "portal_pmtiles_consumer_v48.py").read_text(encoding="utf-8")
    check("legacyViewportRequest(u,init)" in consumer and "legacyViewportRequest(u)" not in consumer,
          "PMTiles preview wrapper forwards the abort init of rx46ViewportRequest")


ABORT_SIM = r"""
const vm=require('vm'),fs=require('fs');
const src=fs.readFileSync(process.argv[2],'utf8');
(async()=>{
  const shown=[],reports=[];
  const el={id:'',className:'',_t:'',set textContent(v){this._t=v;shown.push(v)},get textContent(){return this._t}};
  const document={readyState:'complete',querySelector:s=>(s==='#rxUiStatus'&&el.id==='rxUiStatus')?el:null,createElement:()=>el,body:{appendChild(){}},addEventListener(){}};
  const window={addEventListener(){},fetch:async(input,init)=>{const u=String(input);
    if(u.includes('/v1/ui/client-error')){reports.push(JSON.parse(init.body).kind);return {ok:true,status:200}}
    if(u.includes('/v1/ui/readiness'))return {ok:true,status:200,json:async()=>({ok:true})};
    if(u.includes('abort')){const e=new Error('signal is aborted without reason');e.name='AbortError';throw e}
    if(u.includes('net'))throw new TypeError('Failed to fetch');
    return {ok:true,status:200}}};
  // In a page window is the global object: a bare fetch() inside the script is the current window.fetch.
  const ctx={window,document,location:{pathname:'/'},fetch:(i,init)=>window.fetch(i,init),setTimeout:()=>0,clearTimeout(){},String,JSON,Date,console};
  vm.createContext(ctx);vm.runInContext(src,ctx);
  const out={};
  await window.fetch('/v1/live/sicar/viewport-v46?cell=0.08&abort=1',{signal:{aborted:true}}).catch(e=>{out.abort_rethrown=e.name});
  await new Promise(r=>setImmediate(r));
  out.abort_shown=[...shown];out.abort_reports=[...reports];
  await window.fetch('/v1/live/car/X?net=1',{}).catch(e=>{out.net_rethrown=e.name});
  await new Promise(r=>setImmediate(r));
  out.net_shown=shown.slice(out.abort_shown.length);out.net_reports=reports.slice(out.abort_reports.length);
  process.stdout.write(JSON.stringify(out));
})().catch(e=>{process.stdout.write(JSON.stringify({crash:String(e&&e.stack||e)}))});
"""


def abort_toast_script(html: str) -> str:
    start = html.find("if(window.__rxActionV25)return;")
    begin = html.rfind("<script>", 0, start)
    end = html.find("</script>", start)
    return html[begin + len("<script>"):end] if start >= 0 and begin >= 0 and end > start else ""


ABORT_SCRIPT_OVERRIDE: list[str] = []


def abort_toast_contract() -> None:
    """Rule: an aborted fetch (W1a cell eviction, a closed link lookup) never shows 'Falha de conexão';
    a real network failure still does. Runs the V25 wrapper exactly as it sits in the final portal HTML."""
    import shutil
    import tempfile

    import portal_v8

    node = shutil.which("node")
    check(bool(node), "node found (the abort/connection-toast rule runs the served JS)")
    if not node:
        return
    js = ABORT_SCRIPT_OVERRIDE[-1] if ABORT_SCRIPT_OVERRIDE else abort_toast_script(portal_v8.PORTAL_HTML)
    check(bool(js), "V25 action runtime script found in the final portal HTML")
    with tempfile.TemporaryDirectory() as tmp:
        src, sim = Path(tmp, "v25.js"), Path(tmp, "sim.js")
        src.write_text(js, encoding="utf-8")
        sim.write_text(ABORT_SIM, encoding="utf-8")
        run = subprocess.run([node, str(sim), str(src)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    try:
        r = json.loads(run.stdout or "{}")
    except ValueError:
        r = {"crash": (run.stdout + run.stderr)[:300]}
    check("crash" not in r, f"abort/connection-toast simulation ran ({r.get('crash', '')[:160]})")
    check(r.get("abort_rethrown") == "AbortError" and r.get("net_rethrown") == "TypeError", "the wrapper still rethrows both errors to the caller")
    check(not any("Falha de conex" in t for t in r.get("abort_shown", [])) and "network-error" not in r.get("abort_reports", []),
          f"aborted request shows no 'Falha de conexão' and logs no network-error (shown {r.get('abort_shown')}, reports {r.get('abort_reports')})")
    check(any("Falha de conex" in t for t in r.get("net_shown", [])) and "network-error" in r.get("net_reports", []),
          f"real network failure still shows 'Falha de conexão' and is logged (shown {r.get('net_shown')})")


MUTATIONS: list[tuple[str, str]] = []


def captured(fn) -> list[str]:
    global FAILURES, CAPTURING
    saved, FAILURES, CAPTURING = FAILURES, [], True
    try:
        fn()
    except Exception as exc:
        FAILURES.append(f"crashed: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        got, FAILURES, CAPTURING = FAILURES, saved, False
    return got


def mutation_controls() -> None:
    import portal_map_v46 as v46

    class NoStore(dict):
        def __setitem__(self, key, value):  # single-flight off: nobody ever finds a flight to join
            return None

    def control(name: str, apply, restore, fn, expect: str) -> None:
        apply()
        try:
            failed = captured(fn)
        finally:
            restore()
        hit = [f for f in failed if f.startswith(expect)]
        MUTATIONS.append((name, "caught" if hit else "MISSED"))
        check(bool(hit), f"mutation control '{name}' turns red at: {expect} (red: {[f[:70] for f in failed][:3]})")

    saved_inflight = v46._V46_SICAR_INFLIGHT
    control("single-flight off",
            lambda: setattr(v46, "_V46_SICAR_INFLIGHT", NoStore()),
            lambda: setattr(v46, "_V46_SICAR_INFLIGHT", saved_inflight),
            sicar_flight_contract, "single-flight: 8 identical concurrent requests make ONE SICAR call")
    saved_limiter = v46._sicar_limiter
    control("concurrency limit off",
            lambda: setattr(v46, "_sicar_limiter", lambda: asyncio.Semaphore(1000)),
            lambda: setattr(v46, "_sicar_limiter", saved_limiter),
            sicar_flight_contract, "concurrency limit: 20 distinct calls")
    saved_disc = v46._SicarFlightRequest.is_disconnected

    async def any_gone(self):  # the first caller leaving cancels everyone
        for req in self.requests:
            if await req.is_disconnected():
                return True
        return False

    control("one caller leaving cancels the shared call",
            lambda: setattr(v46._SicarFlightRequest, "is_disconnected", any_gone),
            lambda: setattr(v46._SicarFlightRequest, "is_disconnected", saved_disc),
            sicar_flight_contract, "single-flight: the first caller leaving")
    saved_merge = v46._merge_results

    def merge_hides_failures(results):
        features, _partial, truncated, source_bytes = saved_merge([r for r in results if not isinstance(r, BaseException)])
        return features, 0, truncated, source_bytes

    control("partial answer classified as complete",
            lambda: setattr(v46, "_merge_results", merge_hides_failures),
            lambda: setattr(v46, "_merge_results", saved_merge),
            endpoint_contract, "legacy partial is never served from cache")
    import portal_v8

    fixed = "catch(e){if(e?.name==='AbortError'||init?.signal?.aborted)throw e;report('network-error'"
    control("aborted request shown as connection failure",
            lambda: ABORT_SCRIPT_OVERRIDE.append(abort_toast_script(portal_v8.PORTAL_HTML).replace(fixed, "catch(e){report('network-error'")),
            lambda: ABORT_SCRIPT_OVERRIDE.clear(),
            abort_toast_contract, "aborted request shows no 'Falha de conexão'")
    saved_sha = v46._W1A_REGION_SHA256
    control("upstream edit inside the loader region",
            lambda: setattr(v46, "_W1A_REGION_SHA256", hashlib.sha256(b"upstream module changed the loader").hexdigest()),
            lambda: setattr(v46, "_W1A_REGION_SHA256", saved_sha),
            region_drift_contract, "loader region text matches the pinned upstream fingerprint")


section("UF local mesh", uf_locator_contract)
booted = len(FAILURES)
section("portal boot", boot_portal)
if len(FAILURES) == booted:  # every contract below needs the real booted portal
    section("_reverse_uf local-first", reverse_uf_contract)
    section("viewport-v46 cell endpoint", endpoint_contract)
    section("SICAR single-flight and concurrency limit", sicar_flight_contract)
    section("loader region drift", region_drift_contract)
    section("final portal HTML", html_contract)
    section("aborted request is not a connection failure (V25 toast)", abort_toast_contract)
    if not FAILURES:  # controls only mean something on a green tree
        section("mutation controls (each must turn a named check red)", mutation_controls)
        print("W1A_MUTATIONS=" + ";".join(f"{n}:{r}" for n, r in MUTATIONS), flush=True)

httpx.AsyncClient.send = _real_async_send  # type: ignore[assignment]
if FAILURES:
    print(f"W1A_GATE=FAIL ({len(FAILURES)})", flush=True)
    for f in FAILURES:
        print("  - " + f, flush=True)
    os._exit(1)
print("W1A_GATE=PASS", flush=True)
os._exit(0)

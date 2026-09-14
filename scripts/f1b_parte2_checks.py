"""F1B part 2 checks, run by scripts/f1b_tela_gate.py inside the booted portal (no external network).

1B.6  UF by the IBGE borders and municipality search without Nominatim:
      * /v1/live/resolve on a state border asks the SICAR layer of EVERY UF in the band, together, and finds a
        property registered in the other UF; absence only when every UF answered (a UF that failed = 502
        pending, never 404); no Nominatim/httpx call; the SICAR helper is called with its request argument;
      * /v1/live/cities and the advanced search municipality box answer from the embedded IBGE list, with and
        without accents, with no network call.
1B.7  the card paints at once with what the feature carries, dates included:
      * the viewport features keep dat_criacao/data_atualizacao; the map, search, link and point mappings
        carry created_at/updated_at; the cache key space changed with the payload;
      * served card script (node): fresh cell -> 7 fields and no placeholder; cell older than 5 min -> status,
        condition and update date hidden and kept as unlabeled placeholders while the live answer comes;
        answered or failed -> no placeholder, absent field not shown, never a label with an empty value.
1B.8  the report engine wake:
      * server lock: 25 simultaneous calls -> 1 ping to {WORKER}/health; another only after 10 min; daily cap;
        the answer never waits for the ping; a failing ping raises nothing; no URL -> no network at all;
      * served script (node): 1 POST per tab per 10 min whatever the number of cards, after the card is
        painted, per tab (sessionStorage) and still limited when storage is blocked; nothing without a URL.

Each rule has positive controls: the defect is reintroduced (source or served-script mutation, or a module
attribute swap) and the rule must FAIL for its own reason.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "f1b_parte2_harness.js"
BORDER = (-20.1, -47.4)  # SP side of the SP/MG border (Rifaina): inside the measured ambiguity band
INTERIOR = (-18.82, -44.45)  # Curvelo/MG, far from any border
OCEAN = (-20.0, -30.0)
CAR_MG = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
CAR_SP = "SP-3543600-6BC541EC8E57497CAD7AE659E7B1F6C5"


# ------------------------------------------------------------------ network spy (any httpx use is a failure)
class NetSpy:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __enter__(self):
        import httpx

        spy = self

        class NoNetwork:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, *a, **k):
                spy.urls.append(str(url))
                raise httpx.ConnectError("gate: no network")

            async def request(self, method, url, *a, **k):
                spy.urls.append(str(url))
                raise httpx.ConnectError("gate: no network")

            def stream(self, method, url, *a, **k):
                spy.urls.append(str(url))
                raise httpx.ConnectError("gate: no network")

        self._saved = httpx.AsyncClient
        httpx.AsyncClient = NoNetwork
        return self

    def __exit__(self, *a):
        import httpx

        httpx.AsyncClient = self._saved
        return False


def square(lat: float, lon: float, code: str, d: float = 0.004) -> dict:
    ring = [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"cod_imovel": code, "municipio": "Teste", "uf": code[:2], "area": 12.5, "status_imovel": "AT",
                           "condicao": "Aguardando análise", "tipo_imovel": "IRU", "m_fiscal": 0.31,
                           "dat_criacao": "2018-11-28T13:10:00.000Z", "data_atualizacao": "2025-03-07T16:10:06.509Z"}}


def mutate_function(fn: Callable, old: str, new: str) -> Callable:
    """Source mutation of a module-level function: same globals, one exact replacement."""
    src = inspect.getsource(fn)
    assert src.count(old) == 1, f"positive control anchor missing in {fn.__name__}: {old[:60]}"
    ns: dict[str, Any] = {}
    exec(compile(src.replace(old, new), f"<mutant {fn.__name__}>", "exec"), fn.__globals__, ns)
    return ns[fn.__name__]


def status_of(coro) -> tuple[int, Any]:
    from fastapi import HTTPException

    try:
        out = asyncio.run(coro)
        if hasattr(out, "body"):
            return int(out.status_code), json.loads(out.body)
        return 200, out
    except HTTPException as exc:
        return int(exc.status_code), exc.detail


# ------------------------------------------------------------------ 1B.6 resolve
def resolve_observations(resolve: Callable | None = None, local_ufs: Callable | None = None) -> dict:
    import portal_mobile_v19 as v19
    import portal_sicar_resilient as sicar

    resolve = resolve or v19.resolve_v19
    saved_fetch, saved_local = sicar._fetch_sicar_bbox, v19._local_ufs
    obs: dict[str, Any] = {}

    def run_case(name, point, layers, failing=()):
        asked: list[str] = []

        async def fake_fetch(request, west, south, east, north, uf, limit):  # the real signature: request first
            asked.append(uf)
            if uf in failing:
                from fastapi import HTTPException

                raise HTTPException(status_code=502, detail="fixture_unavailable")
            return {"data": {"type": "FeatureCollection", "features": layers.get(uf, [])}, "bytes": 0, "cap": limit}

        sicar._fetch_sicar_bbox = fake_fetch
        try:
            with NetSpy() as spy:
                code, body = status_of(resolve(point[0], point[1]))
        finally:
            sicar._fetch_sicar_bbox = saved_fetch
        prop = body.get("property") if isinstance(body, dict) else None
        obs[name] = {"status": code, "asked": sorted(set(asked)), "car": (prop or {}).get("car_code"),
                     "created_at": (prop or {}).get("created_at"), "net": spy.urls[:3]}

    if local_ufs is not None:
        v19._local_ufs = local_ufs
    try:
        # the property that covers the point is registered in the OTHER state's layer
        run_case("border_other_uf", BORDER, {"MG": [square(*BORDER, CAR_MG)]})
        run_case("border_found_one_failed", BORDER, {"SP": [square(*BORDER, CAR_SP)]}, failing=("MG",))
        run_case("border_none_one_failed", BORDER, {}, failing=("MG",))
        run_case("border_none_all_answered", BORDER, {})
        run_case("interior", INTERIOR, {"MG": [square(*INTERIOR, CAR_MG)]})
        run_case("ocean", OCEAN, {})
    finally:
        v19._local_ufs = saved_local
    return obs


def judge_resolve(o: dict) -> list[str]:
    p = []
    b = o["border_other_uf"]
    if b["status"] != 200 or b["car"] != CAR_MG:
        p.append(f"border property in the other UF not found: {b}")
    if b["asked"] != ["MG", "SP"]:
        p.append(f"border point did not ask every UF in the band together: {b['asked']}")
    if o["border_found_one_failed"]["status"] != 200 or o["border_found_one_failed"]["car"] != CAR_SP:
        p.append(f"answer lost because another UF failed: {o['border_found_one_failed']}")
    if o["border_none_one_failed"]["status"] != 502:
        p.append(f"absence claimed while a UF did not answer: {o['border_none_one_failed']['status']}")
    if o["border_none_all_answered"]["status"] != 404:
        p.append(f"all UFs answered empty but not 404: {o['border_none_all_answered']['status']}")
    if o["interior"]["asked"] != ["MG"] or o["interior"]["status"] != 200:
        p.append(f"interior point asked other UFs: {o['interior']}")
    if o["interior"]["created_at"] != "2018-11-28T13:10:00.000Z":
        p.append("point answer without the SICAR dates")
    if o["ocean"]["status"] != 404 or o["ocean"]["asked"]:
        p.append(f"point outside Brazil asked SICAR: {o['ocean']}")
    nets = [x for v in o.values() for x in v["net"]]
    if nets:
        p.append(f"Nominatim/external network asked: {nets[:2]}")
    return p


# ------------------------------------------------------------------ 1B.6 municipality search
async def legacy_city_search(q: str):
    """The pre-F1B route (Nominatim), kept only as the positive control of the local-search rule."""
    import httpx
    from fastapi import HTTPException

    params = {"q": f"{q}, Brasil", "format": "jsonv2", "countrycodes": "br", "addressdetails": "1", "limit": "6"}
    try:
        async with httpx.AsyncClient(timeout=18) as client:
            r = await client.get("https://nominatim.openstreetmap.org/search", params=params)
            data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Busca de município indisponível: {type(exc).__name__}")
    return {"ok": True, "items": data}


def city_observations(endpoint: Callable | None = None) -> dict:
    import portal_advanced_search_v39 as adv
    import portal_v8

    route = [r for r in portal_v8.app.router.routes if getattr(r, "path", None) == "/v1/live/cities"]
    endpoint = endpoint or (route[0].endpoint if len(route) == 1 else None)
    obs: dict[str, Any] = {"routes": len(route)}
    with NetSpy() as spy:
        for q in ("sao joao del rei", "São João del-Rei", "BRASILIA", "Brasília", "Curvelo - MG", "curvelo", "formosa go", "zzzz"):
            code, body = status_of(endpoint(q))
            items = body.get("items") if isinstance(body, dict) else None
            obs[q] = {"status": code, "items": [(x.get("name"), x.get("uf")) for x in (items or [])][:6],
                      "geo_ok": all(isinstance(x.get("lat"), float) and float(x["boundingbox"][0]) <= x["lat"] <= float(x["boundingbox"][1])
                                    and float(x["boundingbox"][2]) <= x["lon"] <= float(x["boundingbox"][3]) for x in (items or []))}
        code, box = status_of(adv._city_bbox("SAO JOAO DEL REI", "mg"))
        obs["adv_box"] = {"status": code, "name": (box or {}).get("name") if isinstance(box, dict) else None,
                          "inside": isinstance(box, dict) and box.get("south", 1) <= box.get("lat", 0) <= box.get("north", -1)}
        obs["adv_wrong_uf"] = status_of(adv._city_bbox("Curvelo", "SP"))[0]
    obs["net"] = spy.urls[:3]
    import municipios_ibge_br as mun

    data = mun._load()
    obs["data"] = {"rows": len(data["items"]), "ufs": len({x["uf"] for x in data["items"]}),
                   "bbox_ok": all(x["south"] <= x["lat"] <= x["north"] and x["west"] <= x["lon"] <= x["east"] for x in data["items"])}
    return obs


def judge_cities(o: dict) -> list[str]:
    p = []
    if o.get("routes") != 1:
        p.append("municipality route not registered exactly once")
    for q in ("sao joao del rei", "São João del-Rei"):
        if (o.get(q) or {}).get("items", [None])[:1] != [("São João del Rei", "MG")]:
            p.append(f"accent-insensitive search failed for {q!r}: {(o.get(q) or {}).get('items')}")
    for q in ("BRASILIA", "Brasília"):
        if (o.get(q) or {}).get("items", [None])[:1] != [("Brasília", "DF")]:
            p.append(f"search failed for {q!r}: {(o.get(q) or {}).get('items')}")
    cm = (o.get("Curvelo - MG") or {}).get("items") or []
    if cm[:1] != [("Curvelo", "MG")] or any(uf != "MG" for _, uf in cm):
        p.append(f"trailing UF not honoured: {cm}")
    if (o.get("formosa go") or {}).get("items") != [("Formosa", "GO")]:
        p.append(f"'formosa go' wrong: {(o.get('formosa go') or {}).get('items')}")
    if (o.get("zzzz") or {}).get("status") != 200 or (o.get("zzzz") or {}).get("items"):
        p.append("unknown municipality did not answer an empty list")
    if not all((o.get(q) or {}).get("geo_ok") for q in ("curvelo", "Brasília")):
        p.append("municipality position outside its IBGE box")
    if o["adv_box"]["status"] != 200 or o["adv_box"]["name"] != "São João del Rei" or not o["adv_box"]["inside"] or o["adv_wrong_uf"] != 404:
        p.append(f"advanced search municipality box not local: {o['adv_box']} wrong_uf={o['adv_wrong_uf']}")
    if o["net"]:
        p.append(f"municipality search asked the network (Nominatim): {o['net']}")
    d = o["data"]
    if d["rows"] < 5500 or d["ufs"] != 27 or not d["bbox_ok"]:
        p.append(f"embedded IBGE list incomplete: {d}")
    return p


# ------------------------------------------------------------------ 1B.7 viewport dates + static mappings
def viewport_observations() -> dict:
    import portal_sicar_resilient as sicar

    saved = sicar._fetch_sicar_bbox

    async def fake(request, west, south, east, north, uf, limit):
        return {"data": {"features": [square(*INTERIOR, CAR_MG)]}, "bytes": 10, "cap": limit}

    sicar._fetch_sicar_bbox = fake
    try:
        code, body = status_of(sicar.live_sicar_viewport_resilient(None, -44.46, -18.83, -44.44, -18.81, uf="MG", limit=50))
    finally:
        sicar._fetch_sicar_bbox = saved
    props = ((body or {}).get("features") or [{}])[0].get("properties") or {}
    return {"status": code, "props": sorted(props)}


def judge_viewport(o: dict) -> list[str]:
    missing = [k for k in ("dat_criacao", "data_atualizacao", "status_imovel", "condicao", "tipo_imovel", "m_fiscal", "area") if k not in o["props"]]
    return [f"drawn feature lost {missing}"] if missing or o["status"] != 200 else []


def rule_mappings(html: str) -> list[str]:
    import portal_map_v46 as v46

    p = []
    if "created_at:p.dat_criacao||p.created_at||'',updated_at:p.data_atualizacao||p.updated_at||''}}" not in html:
        p.append("map feature mapping drops the SICAR dates")
    if "fiscal_modules:p.m_fiscal,created_at:p.dat_criacao,updated_at:p.data_atualizacao},c.geometry)" not in html:
        p.append("search (loadCar) mapping drops the SICAR dates")
    if "{car_code:code,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal,created_at:p.dat_criacao,updated_at:p.data_atualizacao}" not in html:
        p.append("link mapping drops the SICAR dates")
    if getattr(v46, "_V46_VIEWPORT_CACHE_SCHEMA", "W1A1") == "W1A1":
        p.append("viewport cache schema not bumped with the payload")
    if not re.search(r"\.rx46-grid>\.rx46-wait:last-child:nth-child\(odd\)\{grid-column:1/-1\}", html):
        p.append("placeholder does not keep the grid position of the field")
    for sel, body in re.findall(r"([^{}]*rx46-wait[^{}]*)\{([^{}]*)\}", html):
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden|height\s*:\s*0", body):
            p.append(f"placeholder collapsed by CSS: {sel.strip()[:60]}")
    return p


# ------------------------------------------------------------------ node harness (card + wake script)
def served_script(html: str, sid: str) -> str:
    i = html.index(f'<script id="{sid}">')
    return html[i:html.index("</script>", i)]


def run_harness(payload: dict) -> dict:
    node = shutil.which("node")
    assert node, "node not found on PATH"
    proc = subprocess.run([node, str(HARNESS)], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"card": {"fatal": (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]}, "wake": {"fatal": "harness crashed"}}


def judge_card(c: dict) -> list[str]:
    p = []
    if c.get("fatal"):
        return [f"card harness: {c['fatal'][:200]}"]
    if c.get("errors"):
        p.append(f"served card script failed to load: {c['errors']}")
    all7 = ["Área", "Status", "Tipo", "Condição", "Módulos fiscais", "Criação", "Atualização"]
    f = c["fresh"]
    if f["labels"] != all7 or f["wait_nodes"]:
        p.append(f"fresh cell card not complete at once: {f['labels']} waits={f['wait_nodes']}")
    if "14,80 ha" not in f["values"] or "28/11/2018" not in f["values"] or "07/03/2025" not in f["values"]:
        p.append(f"pt-BR values/dates wrong: {f['values']}")
    sv = c["stale_values"]
    if sv["updated_at"] or sv["status"] or sv["condition"] or not sv["created_at"]:
        p.append(f"stale cell shows the update date/status/condition: {sv}")
    s = c["stale"]
    if s["waiting"] != ["Status", "Condição", "Atualização"] or s["wait_nodes"] != 3:
        p.append(f"stale card has no placeholders where the live fields will land (jump): {s['waiting']}")
    if "Atualização" in s["labels"]:
        p.append("stale cell shows the update date")
    if c["stale_failed"]["wait_nodes"] or c["stale_enriched"]["wait_nodes"] or c["answered_missing"]["wait_nodes"] or c["no_code"]["wait_nodes"]:
        p.append("placeholders stay after the live answer arrived or failed")
    if c["stale_enriched"]["labels"] != all7:
        p.append(f"enriched stale card incomplete: {c['stale_enriched']['labels']}")
    if "Módulos fiscais" in c["answered_missing"]["labels"]:
        p.append("empty field shown: Módulos fiscais without value")
    lm = c.get("live_missing") or {}
    if lm.get("wait_nodes") or "Atualização" in (lm.get("labels") or []):
        p.append(f"placeholder for a field SICAR did not give (the card would shrink): {lm.get('waiting')}")
    for k in ("fresh", "stale", "stale_failed", "stale_enriched", "answered_missing", "no_code", "search", "live_missing"):
        v = c[k]
        if v["empty_labels"]:
            p.append(f"empty field shown ({k})")
        if v["labelled_waits"]:
            p.append(f"placeholder shows a field label ({k})")
        if re.search(r"m²|m2\b|\d{4}-\d{2}-\d{2}T", v["html"]):
            p.append(f"m² or raw ISO date in the card ({k})")
        if k != "no_code" and (f'data-car="{CAR_MG}"' not in v["html"] or "rx46-title rx46-title-code" not in v["html"]):
            p.append(f"card title is not the CAR code ({k})")
    if c["search"]["labels"] != all7:
        p.append("search answer card not complete at once")
    return p


def judge_wake_script(w: dict) -> list[str]:
    p = []
    if w.get("fatal") or w.get("load_error"):
        return [f"wake harness: {w.get('fatal') or w.get('load_error')}"]
    if not w.get("wrapped"):
        p.append("card selection wrapper lost the flags of the previous wrappers")
    if w.get("sync_fetches") != 0 or w.get("returns") != "painted":
        p.append(f"wake sent before the card is painted: sync={w.get('sync_fetches')}")
    b = w.get("burst") or {}
    if b.get("fetches") != 1 or b.get("select_calls") != 5:
        p.append(f"tab limit broken: {b.get('fetches')} wakes for 5 cards")
    first = b.get("first") or {}
    if first.get("url") != "/v1/live/report-engine/wake" or first.get("method") != "POST" or not first.get("keepalive"):
        p.append(f"wake request wrong: {first}")
    if w.get("after_9min") != 1 or w.get("after_10min") != 2:
        p.append(f"tab limit is not 10 min: 9min={w.get('after_9min')} 10min={w.get('after_10min')}")
    if w.get("reload_same_tab") != 0 or w.get("other_tab") != 1:
        p.append(f"per-tab memory wrong: reload={w.get('reload_same_tab')} other_tab={w.get('other_tab')}")
    if w.get("storage_blocked") != 1:
        p.append(f"tab limit broken when storage is blocked: {w.get('storage_blocked')}")
    if w.get("no_car") != 0:
        p.append("wake without a property")
    o = w.get("offline") or {}
    if o.get("threw") or o.get("select_calls") != 1:
        p.append(f"offline wake broke the card: {o}")
    d = w.get("disabled") or {}
    if d.get("fetches") != 0 or d.get("select_calls") != 4 or d.get("enabled") is not False:
        p.append(f"wake sent without a service URL: {d}")
    return p


# ------------------------------------------------------------------ 1B.8 server lock
def wake_server_observations() -> dict:
    import httpx
    import portal_pdf_v21
    import portal_report_wake_f1b as W

    saved_client, saved_worker = httpx.AsyncClient, portal_pdf_v21.WORKER
    gets: list[str] = []
    mode = {"delay": 0.25, "raise": False}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **k):
            gets.append(str(url))
            await asyncio.sleep(mode["delay"])
            if mode["raise"]:
                raise httpx.ConnectTimeout("fixture")

            class R:
                status_code = 200

            return R()

    def reset():
        W.STATE.update({"last": None, "day": "", "count": 0, "task": None, "sent": 0, "refused": 0, "last_result": None})

    async def settle():
        t = W.STATE.get("task")
        if t is not None:
            try:
                await asyncio.wait_for(t, 5)
            except Exception:
                pass

    obs: dict[str, Any] = {}
    httpx.AsyncClient = FakeClient
    portal_pdf_v21.WORKER = "https://worker.fixture.invalid"
    try:
        async def burst():
            reset()
            res = await asyncio.gather(*(W.report_engine_wake() for _ in range(25)))
            await settle()
            sent = [json.loads(r.body)["sent"] for r in res]
            again = json.loads((await W.report_engine_wake()).body)
            W.STATE["last"] -= W.WAKE_INTERVAL_S + 1
            later = json.loads((await W.report_engine_wake()).body)
            await settle()
            W.STATE["last"] = None
            W.STATE["count"] = W.WAKE_DAILY_CAP
            capped = json.loads((await W.report_engine_wake()).body)
            return {"sent": sum(sent), "gets_after_burst": 0, "again": again, "later": later, "capped": capped}

        gets.clear()
        obs["burst"] = asyncio.run(burst())
        obs["burst"]["gets"] = list(gets)

        async def slow():
            reset()
            mode["delay"] = 2.0
            t0 = time.perf_counter()
            r = await W.report_engine_wake()
            ms = round((time.perf_counter() - t0) * 1000)
            t = W.STATE.get("task")
            if t is not None:
                t.cancel()
                try:
                    await t
                except BaseException:
                    pass
            mode["delay"] = 0.25
            return {"ms": ms, "status": r.status_code}

        obs["non_blocking"] = asyncio.run(slow())

        async def failing():
            reset()
            mode["raise"] = True
            r = await W.report_engine_wake()
            await settle()
            mode["raise"] = False
            return {"status": r.status_code, "last_result": W.STATE.get("last_result")}

        obs["failing"] = asyncio.run(failing())

        async def unconfigured():
            reset()
            portal_pdf_v21.WORKER = ""
            before = len(gets)
            r = await W.report_engine_wake()
            await settle()
            return {"body": json.loads(r.body), "gets": len(gets) - before, "ui_disabled": "const ENABLED=false," in W.ui_html()}

        obs["unconfigured"] = asyncio.run(unconfigured())
    finally:
        httpx.AsyncClient = saved_client
        portal_pdf_v21.WORKER = saved_worker
        reset()
    return obs


def judge_wake_server(o: dict) -> list[str]:
    p = []
    b = o["burst"]
    if b["sent"] != 1 or len([g for g in b["gets"][:1] if g.endswith("/health")]) != 1:
        p.append(f"server lock broken: {b['sent']} sent for 25 simultaneous calls, gets={b['gets'][:3]}")
    if b["again"].get("sent") or not b["later"].get("sent"):
        p.append(f"server interval wrong: again={b['again']} later={b['later']}")
    if b["capped"].get("sent") or b["capped"].get("reason") != "daily_cap":
        p.append(f"daily cap not enforced: {b['capped']}")
    if len(b["gets"]) != 2:
        p.append(f"server lock broken: worker pinged {len(b['gets'])}x (expected 2)")
    nb = o["non_blocking"]
    if nb["ms"] > 500 or nb["status"] != 202:
        p.append(f"wake answer blocks on the worker: {nb}")
    f = o["failing"]
    if f["status"] != 202 or f["last_result"] != "ConnectTimeout":
        p.append(f"failing ping not contained: {f}")
    u = o["unconfigured"]
    if u["body"].get("configured") is not False or u["body"].get("sent") or u["gets"] or not u["ui_disabled"]:
        p.append(f"wake without a configured service URL: {u}")
    return p


def rule_city_search_first(html: str) -> list[str]:
    """The search box lists municipalities at once; the slow property-name search never holds them back."""
    p = []
    s = html[html.index("async function citySearch(term)"):] if "async function citySearch(term)" in html else ""
    if not s or "const pending=propertySearch(raw),cities=await citySearch(raw);" not in s:
        p.append("municipality list waits behind the property-name search")
    if "if(cities.length)card(cities,raw,'MUNICÍPIOS');const props=await pending;" not in s:
        p.append("municipality list waits behind the property-name search (not painted before properties)")
    if "props===null?'Busca pendente" not in s:
        p.append("failed property search shown as 'no result'")
    return p


def rule_wake_static(html: str) -> list[str]:
    import portal_v8

    p = []
    if html.count('<script id="rxReportWakeScriptF1b">') != 1 or html.count("<!-- RX_REPORT_WAKE_F1B -->") != 1:
        return ["wake script not served exactly once"]
    if html.index('<script id="rxReportWakeScriptF1b">') < html.index("<!-- RX_SHARE_LINK_W1A -->"):
        p.append("wake hook loads before the last selection wrapper")
    routes = [r for r in portal_v8.app.router.routes if getattr(r, "path", None) == "/v1/live/report-engine/wake"]
    if len(routes) != 1 or sorted(getattr(routes[0], "methods", [])) != ["POST"]:
        p.append("wake route is not one POST route")
    import portal_report_wake_f1b as W

    if W.WAKE_INTERVAL_S < 600 or W.CLIENT_GAP_MS < 600000 or W.WAKE_DAILY_CAP > 96:
        p.append(f"wake limits loosened: server={W.WAKE_INTERVAL_S}s tab={W.CLIENT_GAP_MS}ms cap={W.WAKE_DAILY_CAP}")
    enabled = "const ENABLED=true," in served_script(html, "rxReportWakeScriptF1b")
    if enabled != bool(W.worker_url()):
        p.append("served wake flag differs from the configured service URL")
    return p


# ------------------------------------------------------------------ entry point used by the gate
def run_all(html: str, check: Callable[[bool, str], bool]) -> None:
    import portal_mobile_v19 as v19
    import portal_report_wake_f1b as W
    import portal_sicar_resilient as sicar

    def guarded(label, fn):
        try:
            return fn()
        except Exception as exc:
            return [f"rule could not run: {type(exc).__name__}: {exc}"]

    # ---------- the rules on the real code
    res = resolve_observations()
    probs = judge_resolve(res)
    check(not probs, f"1B.6 point on a state border resolved by the IBGE borders {probs if probs else ''}".rstrip())
    print("F1B2_RESOLVE_EVIDENCE=" + json.dumps(res, ensure_ascii=False)[:900], flush=True)
    cities = city_observations()
    probs = judge_cities(cities)
    check(not probs, f"1B.6 municipality search local, with and without accents {probs if probs else ''}".rstrip())
    print("F1B2_CITIES_EVIDENCE=" + json.dumps({k: v for k, v in cities.items() if k in ("sao joao del rei", "BRASILIA", "Curvelo - MG", "adv_box", "net", "data")}, ensure_ascii=False)[:900], flush=True)
    probs = guarded("ui", lambda: rule_city_search_first(html))
    check(not probs, f"1B.6 search box lists municipalities without waiting for the property search {probs if probs else ''}".rstrip())
    probs = guarded("vp", lambda: judge_viewport(viewport_observations())) + guarded("map", lambda: rule_mappings(html))
    check(not probs, f"1B.7 dates travel with the drawn feature and every card mapping {probs if probs else ''}".rstrip())

    payload = {"format": served_script(html, "rxNumberFormatC2"), "v46": served_script(html, "rxMapV46Script"),
               "wake": served_script(html, "rxReportWakeScriptF1b")}
    saved_worker = __import__("portal_pdf_v21").WORKER
    __import__("portal_pdf_v21").WORKER = ""
    try:
        payload["wake_disabled"] = W.ui_html().split("\n", 1)[1].split("</script>")[0]
    finally:
        __import__("portal_pdf_v21").WORKER = saved_worker
    hr = run_harness(payload)
    probs = judge_card(hr.get("card") or {})
    check(not probs, f"1B.7 card paints at once and keeps its height, no empty field {probs if probs else ''}".rstrip())
    probs = judge_wake_script(hr.get("wake") or {})
    check(not probs, f"1B.8 wake script: 1 per tab per 10 min, after the paint, nothing without URL {probs if probs else ''}".rstrip())
    print("F1B2_WAKE_SCRIPT_EVIDENCE=" + json.dumps(hr.get("wake"), ensure_ascii=False)[:700], flush=True)
    ws = wake_server_observations()
    probs = judge_wake_server(ws) + guarded("static", lambda: rule_wake_static(html))
    check(not probs, f"1B.8 server lock, non-blocking answer, no URL no network {probs if probs else ''}".rstrip())
    print("F1B2_WAKE_SERVER_EVIDENCE=" + json.dumps({k: v for k, v in ws.items()}, ensure_ascii=False)[:700], flush=True)

    # ---------- positive controls: each defect back, each rule must fail for its reason
    def control(label, got, reason):
        check(any(reason in x for x in got), f"positive control catches: {label} (for its reason: {reason!r}; got {got[:2]})")

    orig_local = v19._local_ufs
    control("1B.6 only the first IBGE UF", judge_resolve(resolve_observations(local_ufs=lambda *a: (orig_local(*a) or [])[:1])), "border property in the other UF not found")
    control("1B.6 back to the Nominatim resolver", judge_resolve(resolve_observations(local_ufs=lambda *a: None)), "Nominatim/external network asked")
    control("1B.6 absence claimed with a UF down", judge_resolve(resolve_observations(
        resolve=mutate_function(v19.resolve_v19, "if errors:raise HTTPException(status_code=502", "if errors and len(errors)>=len(ufs):raise HTTPException(status_code=502"))), "absence claimed while a UF did not answer")
    control("1B.6 SICAR helper without its request", judge_resolve(resolve_observations(
        resolve=mutate_function(v19.resolve_v19, "sicar._fetch_sicar_bbox(request,lon-eps", "sicar._fetch_sicar_bbox(lon-eps"))), "border property in the other UF not found")
    control("1B.6 municipality route on Nominatim", judge_cities(city_observations(endpoint=legacy_city_search)), "asked the network (Nominatim)")
    control("1B.6 municipalities behind the property search", rule_city_search_first(html.replace(
        "const pending=propertySearch(raw),cities=await citySearch(raw);", "const props0=await propertySearch(raw),pending=Promise.resolve(props0),cities=await citySearch(raw);")), "waits behind the property-name search")
    import municipios_ibge_br as mun

    saved_norm, saved_data = mun.norm, mun._DATA
    mun.norm, mun._DATA = (lambda v: re.sub(r"\s+", " ", str(v or "").lower()).strip()), None
    try:
        control("1B.6 accent-sensitive matching", judge_cities(city_observations()), "accent-insensitive search failed")
    finally:
        mun.norm, mun._DATA = saved_norm, saved_data

    saved_keys = sicar.VIEWPORT_PROPERTY_KEYS
    sicar.VIEWPORT_PROPERTY_KEYS = tuple(k for k in saved_keys if not k.startswith("dat"))
    try:
        control("1B.7 dates dropped from the drawn feature", judge_viewport(viewport_observations()), "drawn feature lost")
    finally:
        sicar.VIEWPORT_PROPERTY_KEYS = saved_keys
    control("1B.7 search mapping without dates", rule_mappings(html.replace(",created_at:p.dat_criacao,updated_at:p.data_atualizacao},c.geometry)", "},c.geometry)")), "search (loadCar) mapping drops")

    def card_mutant(old, new):
        pl = dict(payload)
        assert pl["v46"].count(old) == 1, f"positive control anchor missing: {old[:60]}"
        pl["v46"] = pl["v46"].replace(old, new)
        return judge_card(run_harness(pl).get("card") or {})

    control("1B.7 stale update date shown", card_mutant("condition:'',updated_at:'',__rxCellFetchedAt:t", "condition:'',__rxCellFetchedAt:t"), "stale cell shows the update date")
    control("1B.7 no placeholder (card jumps)", card_mutant("waiting=!!p&&", "waiting=false&&!!p&&"), "stale card has no placeholders")
    control("1B.7 placeholder with a field label", card_mutant('aria-hidden="true"><small>&nbsp;</small>', 'aria-hidden="true"><small>${esc(l)}</small>'), "placeholder shows a field label")
    control("1B.7 empty field shown", card_mutant(".map(r=>r[1]?r:(waiting&&late[r[0]]?[r[0],null]:null)).filter(Boolean)", ".map(r=>r[1]?r:(waiting&&late[r[0]]?[r[0],null]:r))"), "empty field shown")
    control("1B.7 placeholder for a field the live record lacks", card_mutant("waiting&&late[r[0]]?[r[0],null]", "(waiting||!p.__rx46Enriched)?[r[0],null]"), "placeholder for a field SICAR did not give")

    def wake_mutant(old, new, key="wake"):
        pl = dict(payload)
        assert pl[key].count(old) == 1, f"positive control anchor missing: {old[:60]}"
        pl[key] = pl[key].replace(old, new)
        return judge_wake_script(run_harness(pl).get("wake") or {})

    control("1B.8 no tab limit", wake_mutant("if(t&&now>=t&&now-t<GAP)return false;", ""), "tab limit broken")
    control("1B.8 wake before the paint", wake_mutant("setTimeout(wake,0)", "wake()"), "wake sent before the card is painted")
    control("1B.8 wake without URL", wake_mutant("if(!ENABLED)return false;", "", key="wake_disabled"), "wake sent without a service URL")
    control("1B.8 wrapper drops earlier flags", wake_mutant("for(const k of Object.keys(sel))wrapped[k]=sel[k];", ""), "lost the flags")

    saved_admit, saved_start, saved_url = W._admit, W._start, W.worker_url
    try:
        W._admit = lambda now: None
        control("1B.8 no server lock", judge_wake_server(wake_server_observations()), "server lock broken")
    finally:
        W._admit = saved_admit
    try:
        W._start = W._ping
        control("1B.8 answer waits for the worker", judge_wake_server(wake_server_observations()), "blocks on the worker")
    finally:
        W._start = saved_start
    try:
        W.worker_url = lambda: "https://worker.fixture.invalid"
        control("1B.8 pings without a configured URL", judge_wake_server(wake_server_observations()), "without a configured service URL")
    finally:
        W.worker_url = saved_url

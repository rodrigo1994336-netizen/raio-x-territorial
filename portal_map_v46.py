from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response

import portal_v8
import portal_sicar_resilient

app = portal_v8.app

# V46 viewport cache. The cache key is a snapped geographic envelope (legacy bbox mode) or a
# fixed grid cell (W1a cell mode), so panning back and forth reuses the same CAR payload
# instead of creating a new WFS request for every pixel movement.
#
# W1a: entries are the final JSON bytes (no re-encoding on a hit) and live 6 h instead of 5 min.
# Why 6 h is safe: the map only DRAWS and IDENTIFIES outlines; the SICAR public base changes by
# publication cycles (the panel declares a monthly snapshot), and the card replaces area,
# status, condition and dates with the live /v1/live/map-panel answer on click. 6 h bounds the
# outline staleness to one work shift. Invalidation stays safe because: (1) only COMPLETE
# answers are stored — any failed quadrant/UF is never cached and is answered with no-store;
# (2) the schema tag below changes the key space on every contract change; (3) the cache is
# per process memory, so deploy/restart/sleep clears it; (4) a byte budget with LRU eviction
# keeps memory bounded.
_V46_VIEWPORT_CACHE: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
_V46_VIEWPORT_TTL = 6 * 3600
_V46_VIEWPORT_MAX = 900
_V46_VIEWPORT_BUDGET_BYTES = 32 * 1024 * 1024  # ~500 dense cells; keeps the 220 MB portal RSS budget
_V46_VIEWPORT_CACHE_BYTES = 0
# Cache schema bump for W1a (bytes + cell mode). Old entries must never collide with new ones.
_V46_VIEWPORT_CACHE_SCHEMA = "W1A1"
# W1a cell grid (degrees). The browser picks one step per zoom/screen and asks one cell per request.
_V46_CELL_STEPS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
# Browser/CDN reuse for a complete answer: short, so a correction reaches users within minutes
# of the server entry expiring. Incomplete answers and errors are never stored anywhere.
_V46_PUBLIC_CACHE = "public, max-age=600"
_V46_NO_STORE = "no-store"
_CACHED_FALSE_TAIL = b',"cached":false}'
_CACHED_TRUE_TAIL = b',"cached":true}'

try:  # warm the embedded UF mesh in the deferred boot thread, never inside a request
    import uf_locator_br

    uf_locator_br._load()
except Exception as exc:  # the legacy resolver remains available
    uf_locator_br = None  # type: ignore[assignment]
    print(f"RX_W1A_UF_LOCAL=unavailable:{type(exc).__name__}", flush=True)


def _snap_bounds(west: float, south: float, east: float, north: float) -> tuple[float, float, float, float, float]:
    span = max(east - west, north - south)
    if span <= 0.08:
        step = 0.02
    elif span <= 0.25:
        step = 0.05
    elif span <= 0.60:
        step = 0.10
    else:
        step = 0.20
    w = math.floor(west / step) * step
    s = math.floor(south / step) * step
    e = math.ceil(east / step) * step
    n = math.ceil(north / step) * step
    return round(w, 6), round(s, 6), round(e, 6), round(n, 6), step


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != "cached"}
    body["cached"] = False
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _cache_drop(key: str) -> None:
    global _V46_VIEWPORT_CACHE_BYTES
    old = _V46_VIEWPORT_CACHE.pop(key, None)
    if old is not None:
        _V46_VIEWPORT_CACHE_BYTES -= len(old[1])


def _cache_get(key: str) -> bytes | None:
    cached = _V46_VIEWPORT_CACHE.get(key)
    if not cached:
        return None
    if time.monotonic() - cached[0] >= _V46_VIEWPORT_TTL:
        _cache_drop(key)
        return None
    _V46_VIEWPORT_CACHE.move_to_end(key)
    return cached[1][: -len(_CACHED_FALSE_TAIL)] + _CACHED_TRUE_TAIL


def _cache_put(key: str, body: bytes) -> None:
    global _V46_VIEWPORT_CACHE_BYTES
    if not body.endswith(_CACHED_FALSE_TAIL):
        return
    _cache_drop(key)
    _V46_VIEWPORT_CACHE[key] = (time.monotonic(), body)
    _V46_VIEWPORT_CACHE_BYTES += len(body)
    while _V46_VIEWPORT_CACHE and (
        _V46_VIEWPORT_CACHE_BYTES > _V46_VIEWPORT_BUDGET_BYTES or len(_V46_VIEWPORT_CACHE) > _V46_VIEWPORT_MAX
    ):
        _, (_, old_body) = _V46_VIEWPORT_CACHE.popitem(last=False)
        _V46_VIEWPORT_CACHE_BYTES -= len(old_body)


def _json_response(body: bytes, *, complete: bool) -> Response:
    return Response(
        content=body,
        media_type="application/json",
        headers={"Cache-Control": _V46_PUBLIC_CACHE if complete else _V46_NO_STORE},
    )


def _merge_results(results: list[Any]) -> tuple[list[dict[str, Any]], int, bool, int]:
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    partial_failures = 0
    truncated = False
    source_bytes = 0
    for result in results:
        if isinstance(result, BaseException):
            partial_failures += 1
            continue
        truncated = truncated or bool(result.get("truncated"))
        source_bytes += int(result.get("source_bytes") or 0)
        for feature in result.get("features") or []:
            props = feature.get("properties") or {}
            code = str(props.get("cod_imovel") or "").strip().upper()
            dedupe = code or repr(feature.get("geometry"))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            features.append(feature)
    return features, partial_failures, truncated, source_bytes


# W1a: SICAR calls made by the map share one flight per identical request and a small process-wide
# concurrency limit. No timeout changes here: each call keeps the transport limits of
# portal_sicar_resilient (connect 12 s, max 40 s, hard 45 s). The limit only queues calls.
# 6 = the in-flight cap of one browser (rxW1aPump), so a single user's cold view is not slowed,
# while several users (or tabs) can no longer multiply live curl processes without bound.
_V46_SICAR_CONCURRENCY = 6
_V46_SICAR_LIMITER: list[Any] = []  # [loop, Semaphore] of the serving loop
_V46_SICAR_INFLIGHT: dict[tuple, "_SicarFlight"] = {}
_V46_SICAR_STATS = {"calls": 0, "shared": 0, "active": 0, "peak": 0, "skipped_disconnected": 0}


class _SicarFlightRequest:
    """Request-like view for one shared SICAR call: disconnected only when EVERY waiter is.

    portal_sicar_resilient cancels the curl process when `request.is_disconnected()` turns true.
    With single-flight, the first caller closing its tab must not cancel the answer another
    caller is still waiting for.
    """

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def is_disconnected(self) -> bool:
        if not self.requests:
            return False
        for req in list(self.requests):
            probe = getattr(req, "is_disconnected", None)
            if probe is None:
                return False
            try:
                if not await probe():
                    return False
            except Exception:
                return False
        return True


class _SicarFlight:
    def __init__(self) -> None:
        self.view = _SicarFlightRequest()
        self.task: asyncio.Future | None = None


def _sicar_limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if not _V46_SICAR_LIMITER or _V46_SICAR_LIMITER[0] is not loop:
        # One serving loop in production; a test harness may run several loops one after another.
        _V46_SICAR_LIMITER[:] = [loop, asyncio.Semaphore(_V46_SICAR_CONCURRENCY)]
    return _V46_SICAR_LIMITER[1]


async def _sicar_flight_run(key: tuple, flight: _SicarFlight, west: float, south: float, east: float, north: float, uf: str, limit: int):
    try:
        async with _sicar_limiter():
            if await flight.view.is_disconnected():
                # Every waiter left while queued (cell evicted, tab closed): do not spend a SICAR call.
                _V46_SICAR_STATS["skipped_disconnected"] += 1
                raise HTTPException(status_code=499, detail="client_disconnected")
            _V46_SICAR_STATS["calls"] += 1
            _V46_SICAR_STATS["active"] += 1
            _V46_SICAR_STATS["peak"] = max(_V46_SICAR_STATS["peak"], _V46_SICAR_STATS["active"])
            try:
                return await portal_sicar_resilient.live_sicar_viewport_resilient(
                    flight.view, west, south, east, north, uf=uf, limit=limit
                )
            finally:
                _V46_SICAR_STATS["active"] -= 1
    finally:
        if _V46_SICAR_INFLIGHT.get(key) is flight:
            _V46_SICAR_INFLIGHT.pop(key, None)


async def _sicar_viewport_call(request: Any, west: float, south: float, east: float, north: float, uf: str, limit: int = 50):
    uf = str(uf).upper()
    key = (uf, round(west, 6), round(south, 6), round(east, 6), round(north, 6), int(limit))
    loop = asyncio.get_running_loop()
    for attempt in range(2):
        flight = _V46_SICAR_INFLIGHT.get(key)
        if flight is not None and (flight.task is None or flight.task.done() or flight.task.get_loop() is not loop):
            flight = None
        if flight is None:
            flight = _SicarFlight()
            flight.view.requests.append(request)
            _V46_SICAR_INFLIGHT[key] = flight
            flight.task = asyncio.ensure_future(_sicar_flight_run(key, flight, west, south, east, north, uf, limit))
            flight.task.add_done_callback(lambda t: t.cancelled() or t.exception())  # no "never retrieved" noise
        else:
            flight.view.requests.append(request)
            _V46_SICAR_STATS["shared"] += 1
        try:
            # shield: one waiter being cancelled must not cancel the call the others share.
            return await asyncio.shield(flight.task)
        except HTTPException as exc:
            # The shared call was dropped because its waiters had left; this caller is still here.
            if exc.status_code == 499 and attempt == 0:
                probe = getattr(request, "is_disconnected", None)
                if probe is None or not await probe():
                    continue
            raise
    raise HTTPException(status_code=499, detail="client_disconnected")


def _cell_ufs(west: float, south: float, east: float, north: float) -> list[str] | None:
    if uf_locator_br is None:
        return None
    try:
        return uf_locator_br.ufs_for_bbox(west, south, east, north)
    except Exception:
        return None


async def _viewport_cell(
    request: Request, west: float, south: float, east: float, north: float, cell: float, uf: str | None
) -> Response:
    steps = [x for x in _V46_CELL_STEPS if abs(x - float(cell)) < 1e-9]
    if not steps:
        raise HTTPException(status_code=422, detail="Célula do mapa inválida.")
    step = steps[0]
    ix = math.floor(west / step + 0.5)
    iy = math.floor(south / step + 0.5)
    tol = 1e-6
    if (
        abs(ix * step - west) > tol
        or abs(iy * step - south) > tol
        or abs((ix + 1) * step - east) > tol
        or abs((iy + 1) * step - north) > tol
    ):
        raise HTTPException(status_code=422, detail="Célula do mapa inválida.")
    cw, cs, ce, cn = (round(ix * step, 6), round(iy * step, 6), round((ix + 1) * step, 6), round((iy + 1) * step, 6))
    forced = str(uf or "").strip().upper()
    if forced and (len(forced) != 2 or not forced.isalpha()):
        raise HTTPException(status_code=422, detail="UF inválida para consulta SICAR.")
    key = f"{_V46_VIEWPORT_CACHE_SCHEMA}:CELL:{step:g}:{ix}:{iy}:{forced or 'AUTO'}"
    hit = _cache_get(key)
    if hit is not None:
        return _json_response(hit, complete=True)
    if forced:
        ufs = [forced]
    else:
        local = _cell_ufs(cw, cs, ce, cn)
        ufs = local if local is not None else [str(await portal_v8.base._reverse_uf((cs + cn) / 2, (cw + ce) / 2)).upper()]

    results: list[Any] = []
    if ufs:
        results = await asyncio.gather(
            *(
                _sicar_viewport_call(request, cw, cs, ce, cn, uf=u, limit=50)
                for u in ufs
            ),
            return_exceptions=True,
        )
    features, partial_failures, truncated, source_bytes = _merge_results(results)
    if results and partial_failures == len(results):
        raise HTTPException(status_code=502, detail="SICAR indisponível nesta quadrícula.")
    body = _payload_bytes(
        {
            "type": "FeatureCollection",
            "features": features,
            "uf": ufs[0] if ufs else None,
            "ufs": ufs,
            "source": "SICAR/WFS público · célula W1A com cache",
            "truncated": truncated,
            "source_bytes": source_bytes,
            "grid": {"west": cw, "south": cs, "east": ce, "north": cn, "step": step, "cells": 1, "ix": ix, "iy": iy},
            "partial_failures": partial_failures,
            "zoom": None,
            "fetched_at": round(time.time(), 3),
        }
    )
    complete = partial_failures == 0
    if complete:
        _cache_put(key, body)
    return _json_response(body, complete=complete)


@app.get("/v1/live/sicar/viewport-v46")
async def live_sicar_viewport_v46(
    request: Request,
    west: float,
    south: float,
    east: float,
    north: float,
    uf: str | None = None,
    limit: int = 200,
    zoom: int | None = None,
    cell: float | None = None,
):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail="Área do mapa inválida.")
    span = max(east - west, north - south)
    if span > 1.5:
        raise HTTPException(status_code=422, detail="Área visível ampla demais para carregar limites CAR.")
    if cell is not None:
        return await _viewport_cell(request, west, south, east, north, cell, uf)

    w, s, e, n, step = _snap_bounds(west, south, east, north)
    cap = max(1, min(int(limit or 200), 240))
    zoom_key = str(int(zoom)) if zoom is not None else "NA"
    key_suffix = f":limit={cap}:zoom={zoom_key}"
    auto_key = f"{_V46_VIEWPORT_CACHE_SCHEMA}:AUTO:{w:.6f}:{s:.6f}:{e:.6f}:{n:.6f}:{step:.4f}{key_suffix}"
    if not uf:
        hit = _cache_get(auto_key)
        if hit is not None:
            return _json_response(hit, complete=True)
        center_lat = (s + n) / 2
        center_lon = (w + e) / 2
        uf = await portal_v8.base._reverse_uf(center_lat, center_lon)
    uf = str(uf).upper()
    key = f"{_V46_VIEWPORT_CACHE_SCHEMA}:{uf}:{w:.6f}:{s:.6f}:{e:.6f}:{n:.6f}:{step:.4f}{key_suffix}"
    hit = _cache_get(key)
    if hit is not None:
        return _json_response(hit, complete=True)

    width = e - w
    height = n - s
    # One query is enough at very close zoom. Otherwise split into four fixed
    # quadrants: each WFS call keeps the upstream 50-feature safety cap while the
    # visible map can receive up to ~200 distinct properties without one giant call.
    parts = 1 if max(width, height) <= 0.06 else 2
    cells: list[tuple[float, float, float, float]] = []
    for iy in range(parts):
        for ix in range(parts):
            cw = w + width * ix / parts
            ce = w + width * (ix + 1) / parts
            cs = s + height * iy / parts
            cn = s + height * (iy + 1) / parts
            cells.append((cw, cs, ce, cn))

    async def fetch_cell(cell_box: tuple[float, float, float, float]):
        cw, cs, ce, cn = cell_box
        return await _sicar_viewport_call(request, cw, cs, ce, cn, uf=uf, limit=50)

    results = await asyncio.gather(*(fetch_cell(c) for c in cells), return_exceptions=True)
    features, partial_failures, truncated, source_bytes = _merge_results(list(results))
    if len(features) > cap:
        truncated = True
        features = features[:cap]
    if not features and partial_failures == len(results):
        raise HTTPException(status_code=502, detail="SICAR indisponível nesta quadrícula.")

    body = _payload_bytes(
        {
            "type": "FeatureCollection",
            "features": features,
            "uf": uf,
            "source": "SICAR/WFS público · quadrícula V46 com cache",
            "truncated": truncated,
            "source_bytes": source_bytes,
            "grid": {"west": w, "south": s, "east": e, "north": n, "step": step, "cells": len(cells)},
            "partial_failures": partial_failures,
            "zoom": zoom,
            "fetched_at": round(time.time(), 3),
        }
    )
    # A partial answer (some quadrant failed) is shown but never stored, so it cannot be
    # replayed for hours as if it were the complete area.
    complete = partial_failures == 0
    if complete:
        _cache_put(key, body)
        _cache_put(auto_key, body)
    return _json_response(body, complete=complete)


html = portal_v8.PORTAL_HTML


def once(old: str, new: str, error: str) -> None:
    global html
    if html.count(old) != 1:
        raise RuntimeError(error)
    html = html.replace(old, new, 1)


# Opening and low-zoom states must be quiet. A confirmed truncation notice stays
# visible while the next viewport loads; only the next settled result clears it.
# Errors remain visible.
once(
    "function setMapState(t){let el=qs('#rxMapState');if(!el){el=document.createElement('div');el.id='rxMapState';el.className='rx-map-state';qs('.main')?.appendChild(el)}if(el)el.textContent=t||''}",
    "function setMapState(t,truncatedState){const msg=String(t||'');let el=qs('#rxMapState');const legacy=!!el&&/^Mostrando \\d+ imóveis\\. Há mais nesta área\\.$/.test(el.textContent||'');const confirmed=!!el&&(el.dataset.rxTruncated==='1'||legacy);const keep=confirmed&&truncatedState===undefined&&/^Carregando imóveis rurais\\b/i.test(msg);if(keep)return;const quiet=!msg||/^Aproxime\\b/i.test(msg)||/^Busque um município/i.test(msg)||/imóvel\\(is\\) CAR carregado/i.test(msg)||/imóveis rurais nesta área/i.test(msg)||/aproxime o mapa para ver os imóveis/i.test(msg);if(!el&&!quiet){el=document.createElement('div');el.id='rxMapState';el.className='rx-map-state';qs('.main')?.appendChild(el)}if(!el)return;if(truncatedState===true)el.dataset.rxTruncated='1';else if(truncatedState===false||!msg)delete el.dataset.rxTruncated;if(quiet){el.textContent='';return}el.textContent=msg}",
    "v46_map_state_patch_missing",
)

# W1a: the effective CAR viewport loader is written HERE (it replaces the V21/V43 loader and
# scheduleParcels in one piece). Instead of one 4-quadrant request per settled view it keeps a
# fixed grid of cells:
#   * one request per cell (/viewport-v46?...&cell=STEP), each drawn as soon as it arrives;
#   * visible cells first (center outwards), then a ~50% margin (25% per side);
#   * hysteresis: nothing is requested while the view stays inside the planned margin;
#   * parcels are drawn by one L.canvas renderer (padding .5) in a pane under the SVG overlays,
#     so the selected outline stays on top and a drag does not expose blank edges;
#   * cells of the previous zoom stay on the map until the new visible cells settle (no blink);
#   * "Há mais nesta área" only while a cell already LOADED and INTERSECTING the view reached the
#     SICAR 50-feature cap, with the count of parcels drawn in view. It is re-judged on every
#     moveend/zoomend at once (not after the debounce), so a previous area's notice is never
#     carried into a new area while its cells load; a cell that failed twice becomes a quiet
#     pending line and is asked again on the next move.
# portal_map_v46_anchor_state.py anchors on the parcel click below (kept verbatim).
_W1A_REGION_START = "async function loadVisibleParcels(force){"
_W1A_REGION_END = "  function locateUser(){"
_old_click = "l.bindTooltip('',{sticky:true});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof showProperty==='function')showProperty(p,live.geometry)})"
_new_click = "l.on('mouseover',()=>{try{l.setStyle(window.rxParcelHoverStyle?.()||{weight:2,fillOpacity:.23})}catch(e){}});l.on('mouseout',()=>{try{l.setStyle(rxParcelStyleFor(l.feature||ff))}catch(e){}});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')window.rxV46SelectProperty(p,live.geometry,e.latlng);else if(typeof showProperty==='function')showProperty(p,live.geometry)})"
_W1A_LOADER = r"""const RX_W1A_STEPS=[0.01,0.02,0.04,0.08,0.16,0.32];
  const rxW1a={step:0,box:null,cells:new Map(),parcelCells:new Map(),queue:[],active:0,epoch:0,canvas:null,drawMs:0,draws:0,noticeMs:0};
  const rxW1aAge=new WeakMap();window.rxW1aCellFetchedAt=g=>{const t=g&&typeof g==='object'?rxW1aAge.get(g):undefined;return Number.isFinite(t)?t:null};
  window.rxW1aGridState=()=>{const by={};for(const c of rxW1a.cells.values())by[c.state]=(by[c.state]||0)+1;return {step:rxW1a.step,box:rxW1a.box,cells:rxW1a.cells.size,by,active:rxW1a.active,queued:rxW1a.queue.length,parcels:rxParcelIndex.size,renderer:rxW1a.canvas?'canvas':null,draws:rxW1a.draws,drawMs:Math.round(rxW1a.drawMs),noticeMs:Math.round(rxW1a.noticeMs)}};
  function rxW1aKey(step,ix,iy){return step+':'+ix+':'+iy}
  function rxW1aCells(step,w,s,e,n){const out=[],x0=Math.floor(w/step+1e-9),x1=Math.floor(e/step-1e-9),y0=Math.floor(s/step+1e-9),y1=Math.floor(n/step-1e-9);if((x1-x0+1)*(y1-y0+1)>400)return out;for(let iy=y0;iy<=y1;iy++)for(let ix=x0;ix<=x1;ix++)out.push(rxW1aKey(step,ix,iy));return out}
  function rxW1aStep(w,s,e,n){const target=Math.sqrt(Math.max(1e-9,(e-w)*(n-s))/(window.rxFieldMode?4:6)),cur=rxW1a.step;if(cur&&Math.abs(Math.log(cur/target))<Math.log(2)*.75)return cur;let best=RX_W1A_STEPS[0];for(const x of RX_W1A_STEPS)if(Math.abs(Math.log(x/target))<Math.abs(Math.log(best/target)))best=x;return best}
  function rxW1aRenderer(m){if(!m.getPane('rxParcelPane')){const pane=m.createPane('rxParcelPane');pane.style.zIndex='390'}if(!rxW1a.canvas)rxW1a.canvas=L.canvas({pane:'rxParcelPane',padding:.5,tolerance:matchMedia('(pointer:coarse)').matches?4:1});return rxW1a.canvas}
  function rxW1aReset(){rxW1a.epoch+=1;for(const c of rxW1a.cells.values()){if(c.ctrl){try{c.ctrl.abort()}catch(e){}}}rxW1a.cells.clear();rxW1a.parcelCells.clear();rxW1a.queue=[];rxW1a.box=null;rxW1a.step=0}
  function rxW1aDrop(k){const c=rxW1a.cells.get(k);if(!c)return;rxW1a.cells.delete(k);if(c.ctrl){try{c.ctrl.abort()}catch(e){}}for(const key of c.keys){const set=rxW1a.parcelCells.get(key);if(!set)continue;set.delete(k);if(!set.size){rxW1a.parcelCells.delete(key);const g=rxParcelIndex.get(key);if(g){try{rxParcelLayer&&rxParcelLayer.removeLayer(g)}catch(e){}rxParcelIndex.delete(key)}}}}
  function rxW1aDraw(m,cell,features){if(!rxParcelLayer)rxParcelLayer=L.layerGroup().addTo(m);const renderer=rxW1aRenderer(m);const rxParcelStyleFor=f=>(window.rxParcelStyle?window.rxParcelStyle(f):{color:'#48d995',weight:1.4,fillColor:'#48d995',fillOpacity:.075});const rxParcelKey=(f,i)=>{const p=propertyFromFeature(f);return String(p.car_code||f?.id||('anon:'+cell.key+':'+i+':'+(p.municipality||'')+':'+(p.area_ha??'')))};const rxGeomSig=f=>{try{return JSON.stringify(f?.geometry?.coordinates||[]).length}catch(e){return 0}};const rxBuildParcel=(f,key)=>{const group=L.geoJSON(f,{renderer,style:rxParcelStyleFor,onEachFeature:(ff,l)=>{__RX_W1A_CLICK__}});group.__rxGeomSig=rxGeomSig(f);group.eachLayer(l=>{l.feature=f});group.addTo(rxParcelLayer);rxParcelIndex.set(key,group);return group};(features||[]).forEach((f,i)=>{if(f&&f.geometry&&typeof f.geometry==='object'&&Number.isFinite(cell.fetchedAt))rxW1aAge.set(f.geometry,cell.fetchedAt);const key=rxParcelKey(f,i);cell.keys.add(key);let set=rxW1a.parcelCells.get(key);if(!set)rxW1a.parcelCells.set(key,set=new Set());set.add(cell.key);const group=rxParcelIndex.get(key),sig=rxGeomSig(f);if(!group){rxBuildParcel(f,key);return}if(group.__rxGeomSig!==sig){rxBuildParcel(f,key);try{rxParcelLayer.removeLayer(group)}catch(e){}return}group.eachLayer(l=>{l.feature=f;})})}
  function rxW1aHits(c,w,s,e,n){return c.ix*c.step<e&&(c.ix+1)*c.step>w&&c.iy*c.step<n&&(c.iy+1)*c.step>s}
  function rxW1aNotice(){const m=rxMap();if(!m||!rxW1a.step||m.getZoom()<11)return;const b=m.getBounds(),bw=b.getWest(),bs=b.getSouth(),be=b.getEast(),bn=b.getNorth(),vis=rxW1aCells(rxW1a.step,bw,bs,be,bn).map(k=>rxW1a.cells.get(k));let n=0;for(const g of rxParcelIndex.values()){try{if(b.intersects(g.getBounds()))n++}catch(x){}}window.rxVisibleCarCountV43=n;let trunc=false;for(const c of rxW1a.cells.values()){if(c.state==='ok'&&c.truncated&&rxW1aHits(c,bw,bs,be,bn)){trunc=true;break}}const pending=vis.some(c=>!c||c.state==='queued'||c.state==='loading'),failed=vis.some(c=>c&&(c.state==='fail'||(c.state==='ok'&&c.partial)));if(trunc){setMapState(`Mostrando ${n} imóveis. Há mais nesta área.`,true);return}if(pending){setMapState(n?'':'Carregando imóveis do SICAR nesta área do Brasil…',false);return}if(failed){setMapState('Parte dos imóveis desta área ainda não carregou.',false);return}setMapState('',false)}
  function rxW1aEvict(){const m=rxMap();if(!m||!rxW1a.step)return;const b=m.getBounds(),w=b.getWest(),s=b.getSouth(),e=b.getEast(),n=b.getNorth(),dw=e-w,dh=n-s,R=[w-dw,s-dh,e+dw,n+dh],step=rxW1a.step;const settled=rxW1aCells(step,w,s,e,n).every(k=>{const c=rxW1a.cells.get(k);return !!c&&(c.state==='ok'||c.state==='fail')});for(const [k,c] of [...rxW1a.cells]){const x0=c.ix*c.step,y0=c.iy*c.step,far=x0+c.step<R[0]||x0>R[2]||y0+c.step<R[1]||y0>R[3];if(far||(c.step!==step&&(settled||c.state!=='ok')))rxW1aDrop(k)}}
  function rxW1aRetryLater(cell){setTimeout(()=>{if(rxW1a.cells.get(cell.key)!==cell||(cell.state==='ok'&&!cell.partial)||cell.state==='loading'||cell.state==='queued')return;cell.state='queued';rxW1a.queue.unshift(cell.key);rxW1aPump()},1500)}
  async function rxW1aFetch(cell){const m=rxMap();if(!m)return;cell.state='loading';cell.tries+=1;rxW1a.active+=1;const ctrl=new AbortController(),epoch=rxW1a.epoch;cell.ctrl=ctrl;let ok=false,d=null;try{const s=cell.step,f=v=>v.toFixed(6),u=new URL('/v1/live/sicar/viewport-v46',location.origin);u.searchParams.set('west',f(cell.ix*s));u.searchParams.set('south',f(cell.iy*s));u.searchParams.set('east',f((cell.ix+1)*s));u.searchParams.set('north',f((cell.iy+1)*s));u.searchParams.set('cell',String(s));const pack=window.rx46ViewportRequest?await window.rx46ViewportRequest(u,{signal:ctrl.signal}):null;const r=pack?pack.response:await fetch(u,{signal:ctrl.signal});d=pack?pack.data:await r.json();ok=!!r.ok&&!!d&&Array.isArray(d.features)}catch(e){ok=false}finally{rxW1a.active=Math.max(0,rxW1a.active-1);cell.ctrl=null}rxW1aPump();if(epoch!==rxW1a.epoch||rxW1a.cells.get(cell.key)!==cell)return;if(ok){cell.state='ok';cell.truncated=!!d.truncated;cell.partial=Number(d.partial_failures||0)>0;cell.fetchedAt=Number(d.fetched_at)>0?Number(d.fetched_at)*1000:NaN;rxLastUf=d.uf||rxLastUf;const t0=performance.now();try{rxW1aDraw(m,cell,d.features)}catch(e){cell.state='fail'}rxW1a.drawMs+=performance.now()-t0;rxW1a.draws+=1;if(cell.partial&&cell.tries<2)rxW1aRetryLater(cell)}else{cell.state='fail';if(cell.tries<2)rxW1aRetryLater(cell)}const t1=performance.now();rxW1aEvict();rxW1aNotice();rxW1a.noticeMs+=performance.now()-t1}
  function rxW1aPump(){const max=window.rxFieldMode?2:6;while(rxW1a.active<max&&rxW1a.queue.length){const k=rxW1a.queue.shift(),c=rxW1a.cells.get(k);if(c&&c.state==='queued')rxW1aFetch(c)}}
  function rxW1aPlan(keys){const want=new Set(keys);for(const [k,c] of [...rxW1a.cells]){if(c.state==='queued'&&!want.has(k))rxW1a.cells.delete(k)}rxW1a.queue=[];for(const k of keys){const c=rxW1a.cells.get(k);if(c&&c.state!=='fail'){if(c.state==='queued')rxW1a.queue.push(k);continue}const [s,ix,iy]=k.split(':').map(Number);rxW1a.cells.set(k,{key:k,step:s,ix,iy,state:'queued',tries:0,keys:c?c.keys:new Set(),truncated:false,partial:false,ctrl:null});rxW1a.queue.push(k)}rxW1aPump()}
  async function loadVisibleParcels(force){const m=rxMap();if(!m||document.body.classList.contains('rx43-dossier-open'))return;const z=m.getZoom();if(z<11){rxW1aReset();if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}rxParcelIndex.clear();setMapState('');return}const b=m.getBounds(),west=b.getWest(),south=b.getSouth(),east=b.getEast(),north=b.getNorth();if(!(Number.isFinite(west)&&Number.isFinite(south)&&Number.isFinite(east)&&Number.isFinite(north)&&east>west&&north>south)){setMapState('Mapa ajustando…');return}const span=Math.max(east-west,north-south);if(span>1.2&&!force){setMapState('Aproxime um pouco mais para carregar os imóveis rurais.');return}const step=rxW1aStep(west,south,east,north),visible=rxW1aCells(step,west,south,east,north),box=rxW1a.box,inside=!!box&&rxW1a.step===step&&west>=box[0]&&south>=box[1]&&east<=box[2]&&north<=box[3],covered=visible.every(k=>{const c=rxW1a.cells.get(k);return !!c&&c.state!=='fail'});if(inside&&covered&&!force){rxW1aEvict();rxW1aNotice();return}rxW1a.step=step;const pw=(east-west)*.25,ph=(north-south)*.25,pad=[west-pw,south-ph,east+pw,north+ph];rxW1a.box=pad;const cx=(west+east)/2,cy=(south+north)/2,dist=k=>{const p=k.split(':').map(Number);return Math.hypot((p[1]+.5)*step-cx,(p[2]+.5)*step-cy)};const seen=new Set(visible);let margin=window.rxFieldMode?[]:rxW1aCells(step,...pad).filter(k=>!seen.has(k));if(visible.length+margin.length>64)margin=[];rxW1aPlan([...visible.sort((a,c)=>dist(a)-dist(c)),...margin.sort((a,c)=>dist(a)-dist(c))]);rxW1aEvict();rxW1aNotice()}
  function scheduleParcels(){rxLastUf=null;clearTimeout(rxTimer);if(!document.body.classList.contains('rx43-dossier-open')){try{rxW1aEvict();rxW1aNotice()}catch(e){}}rxTimer=setTimeout(()=>loadVisibleParcels(false),window.rxFieldMode?850:120)}
""".replace("__RX_W1A_CLICK__", _new_click)

_region_start = html.find(_W1A_REGION_START)
_region_end = html.find(_W1A_REGION_END, _region_start + 1) if _region_start >= 0 else -1
if (
    _region_start < 0
    or _region_end < 0
    or html.find(_W1A_REGION_START, _region_start + 1) >= 0
    or _old_click not in html[_region_start:_region_end]
    or "function scheduleParcels(){" not in html[_region_start:_region_end]
):
    raise RuntimeError("w1a_viewport_loader_region_missing")


def expect_once_in_region(anchor: str, error: str) -> None:
    """The once() checks V46 had on this region, kept as boot-time drift detectors.

    The loader region is replaced as a whole, so the old rewrites are no longer applied; but if an
    upstream module (V21/V43/V31) stops producing the exact text those patches expected, V46 must
    fail at boot exactly as before instead of silently discarding the upstream change.
    """
    if html.count(anchor) != 1 or not (_region_start <= html.find(anchor) < _region_end):
        raise RuntimeError(error)


expect_once_in_region(
    "if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}rxParcelIndex.clear();setMapState('Aproxime o mapa para visualizar os limites dos imóveis rurais do CAR.');return}",
    "v46_low_zoom_branch_missing",
)
expect_once_in_region("const u=new URL('/v1/live/sicar/viewport',location.origin);", "v46_viewport_url_missing")
expect_once_in_region(
    "u.searchParams.set('limit',window.rxFieldMode?'35':'80');const r=await (window.rxFieldFetch?window.rxFieldFetch(u,window.rxFieldMode?6500:10000):fetch(u));const d=await r.json();",
    "v46_viewport_fetch_patch_missing",
)
expect_once_in_region(
    "setMapState(`${d.features?.length||0} imóvel(is) CAR carregado(s) nesta área${d.truncated?' · aproxime para ver mais':''}. Clique em um polígono.`)",
    "v48_t_truncation_notice_patch_missing",
)
expect_once_in_region(_old_click, "v46_polygon_click_patch_missing")

# Fingerprint of the exact upstream text this loader replaces. The W1a gate pins it for the CI
# release chain, so ANY upstream edit inside the region (not only the five anchors above) turns
# CI red until someone ports it into _W1A_LOADER and updates the pin in the same commit. It is
# not enforced at boot, because another release chain may legitimately produce other text.
_W1A_REGION_SHA256 = hashlib.sha256(html[_region_start:_region_end].encode("utf-8")).hexdigest()
html = html[:_region_start] + _W1A_LOADER + html[_region_end:]

# An empty-map click closes the anchor instead of launching a second point resolver.
once(
    "map.on('click',e=>resolvePoint(e.latlng.lat,e.latlng.lng).catch(x=>toast(x.message)));",
    "map.on('click',()=>window.rxV46CloseAnchor?.());",
    "v46_map_click_patch_missing",
)

V46_UI = r'''
<style id="rxMapV46">
.rx-map-state:empty{display:none!important}.rx-map-state{top:12px!important;left:12px!important;right:auto!important;max-width:260px!important;padding:6px 9px!important;font-size:9px!important;border-radius:9px!important;background:rgba(6,20,14,.88)!important;box-shadow:0 5px 18px #0005!important}.rx-map-state[data-rx-truncated="1"]{display:flex!important;align-items:center;gap:8px;max-width:min(330px,calc(100vw - 24px))!important;padding:9px 12px!important;background:#10251c!important;color:#f4fff8!important;border:1px solid #63e6a5!important;box-shadow:0 7px 22px rgba(0,0,0,.52)!important;font-size:12px!important;font-weight:800!important;line-height:1.35!important}.rx-map-state[data-rx-truncated="1"]::before{content:'i';display:inline-grid;place-items:center;flex:0 0 18px;width:18px;height:18px;border-radius:50%;background:#63e6a5;color:#062018;font:900 11px/1 sans-serif}
.rx46-anchor-popup .leaflet-popup-content-wrapper{background:rgba(7,21,15,.985);color:#eef8f2;border:1px solid #2a493b;border-radius:15px;box-shadow:0 18px 52px #0009;padding:0;backdrop-filter:blur(14px)}
.rx46-anchor-popup .leaflet-popup-content{width:218px!important;margin:0!important}.rx46-anchor-popup .leaflet-popup-tip{background:#0b2118;border:1px solid #2a493b;box-shadow:none}.rx46-card{padding:10px;display:grid;gap:8px}.rx46-card *{box-sizing:border-box}.rx46-head{display:flex;align-items:center;gap:8px}.rx46-car-badge{font-size:9px;line-height:1;padding:4px 6px;border-radius:7px;background:#173a2b;color:#8df0bd;font-weight:950;letter-spacing:.5px}.rx46-spacer{flex:1}.rx46-linkbtn,.rx46-x{border:0;background:transparent;color:#dceae2;font-size:10px;font-weight:850;padding:3px 4px;cursor:pointer}.rx46-x{font-size:18px;line-height:1;color:#c5d5cd}.rx46-title{font-size:14px;line-height:1.18;margin:0;overflow-wrap:anywhere}.rx46-title-code{overflow-wrap:normal}.rx46-place{margin-top:-3px;font-size:10px;line-height:1.3;color:#b9ccc2}.rx46-demo{border:0;background:transparent;color:#72e9ad;font-size:10px;text-align:left;padding:0;text-decoration:underline;text-underline-offset:2px;cursor:pointer;justify-self:start}.rx46-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 9px}.rx46-grid>.rx46-field:last-child:nth-child(odd){grid-column:1/-1}.rx46-field{min-width:0}.rx46-field small{display:block;color:#a9bfb4;font-size:9px;text-transform:uppercase;letter-spacing:.2px;margin-bottom:2px}.rx46-field b{display:block;font-size:11px;line-height:1.28;overflow-wrap:anywhere}.rx46-status{display:inline-flex!important;width:max-content;max-width:100%;align-items:center;padding:3px 6px;border-radius:999px;font-size:10px!important;background:#26372f;color:#c5d5cd}.rx46-status.active{background:#123c29;color:#7ff0b6}.rx46-status.pending{background:#4b3917;color:#ffd77d}.rx46-status.bad{background:#4d211c;color:#ffad9d}.rx46-asof{font-size:9px;line-height:1.3;color:#a9bfb4;margin-top:-2px}.rx46-cta{width:100%;min-height:38px;border:1px solid #63e6a5;border-radius:9px;background:#63e6a5;color:#052116;font-size:10px;font-weight:950;cursor:pointer}.rx46-selection path{vector-effect:non-scaling-stroke}
.rx46-status-seal{display:inline-flex;align-items:center;padding:4px 7px;border-radius:999px;background:#26372f;color:#d5e4dc;font-size:9px;font-weight:900}.rx46-status-seal.active{background:#123c29;color:#7ff0b6}.rx46-status-seal.pending{background:#4b3917;color:#ffd77d}.rx46-status-seal.bad{background:#4d211c;color:#ffad9d}
@media(max-width:720px),(pointer:coarse){.rx46-head{margin:-10px -8px -6px 0}.rx46-linkbtn,.rx46-x{min-width:44px;min-height:44px}.rx46-demo{min-height:44px;display:flex;align-items:center;width:max-content;max-width:100%}.rx46-cta{min-height:44px}}
@media(max-width:720px){.rx-map-state{top:8px!important;left:8px!important;right:auto!important}.rx45-tools{flex-direction:row!important;flex-wrap:nowrap!important;gap:3px!important}.rx45-tool{height:28px!important;padding:0 5px!important;min-width:0!important}.rx45-panel-card{transition:transform .18s ease,opacity .18s ease}}
@media(max-width:390px){.rx45-tools{flex-direction:row!important}.rx45-tool{height:27px!important;padding:0 4px!important}}
.rx46-anchor-popup.rx46-tipless .leaflet-popup-tip-container{visibility:hidden}.rx-map-state{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;text-wrap:balance}@media(max-width:720px){.rx-map-state,.rx-map-state[data-rx-truncated="1"]{max-width:calc(100vw - 16px)!important}}
</style>
<script id="rxMapV46Script">
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const mapRef=()=>{try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}};
 const num=(v,d=2)=>window.rxNum?window.rxNum.num(v,d):'';
 const ha=v=>window.rxNum?window.rxNum.ha(v):'';
 const date=v=>window.rxDateBR?window.rxDateBR(v):'';
 // C2b: the SIGEF/INCRA reference block (one owner: window.rxSigefRefC2); never the title.
 const sigefRef=p=>window.rxSigefRefC2?window.rxSigefRefC2.html(p,'card'):'';
 const identity=p=>{if(window.rxCardIdentityC2)return window.rxCardIdentityC2(p);const car=String(p?.car_code||'').trim().toUpperCase(),city=String(p?.municipality||'').trim(),uf=String(p?.uf||'').trim().toUpperCase();return {named:false,title:car,code:car,place:city?(uf?`${city} / ${uf}`:city):''}};
 const STATUS={AT:'Ativo',PE:'Pendente',CA:'Cancelado',SU:'Suspenso',IN:'Inativo'};
 const TYPES={IRU:'Imóvel Rural',AST:'Assentamento',PCT:'Povos e Comunidades Tradicionais'};
 const statusInfo=v=>{const raw=String(v||'').trim(),label=raw?(STATUS[raw.toUpperCase()]||raw):'',low=label.toLowerCase();return {raw,label,cls:/ativ/.test(low)?'active':/pendent/.test(low)?'pending':/cancel|suspens|inativ/.test(low)?'bad':''}};
 const typeLabel=v=>{const raw=String(v||'').trim();return raw?(TYPES[raw.toUpperCase()]||raw):''};
 const viewportMemory=new Map();
 function snappedUrl(input){const u=new URL(String(input),location.origin);if(u.searchParams.has('cell'))return u;const w=Number(u.searchParams.get('west')),s=Number(u.searchParams.get('south')),e=Number(u.searchParams.get('east')),n=Number(u.searchParams.get('north')),span=Math.max(e-w,n-s);let step=span<=.08?.02:span<=.25?.05:span<=.60?.10:.20;if([w,s,e,n].every(Number.isFinite)){u.searchParams.set('west',String(Math.floor(w/step)*step));u.searchParams.set('south',String(Math.floor(s/step)*step));u.searchParams.set('east',String(Math.ceil(e/step)*step));u.searchParams.set('north',String(Math.ceil(n/step)*step))}return u}
 // W1a: complete answers are reused for 10 min (same as the server Cache-Control); the browser HTTP cache
 // does the rest. No background no-store refetch, and an incomplete answer is never remembered.
 window.rx46ViewportRequest=async function(input,init){const u=snappedUrl(input),key=u.toString(),hit=viewportMemory.get(key);if(hit&&Date.now()-hit.t<600000){viewportMemory.delete(key);viewportMemory.set(key,hit);return {response:{ok:true,status:200},data:hit.d}}if(hit)viewportMemory.delete(key);const r=await fetch(u,init||{}),d=await r.json();if(r.ok&&d&&Number(d.partial_failures||0)===0){viewportMemory.set(key,{t:Date.now(),d});if(viewportMemory.size>160)viewportMemory.delete(viewportMemory.keys().next().value)}return {response:r,data:d}};
 window.rxParcelStyle=function(){return {color:'#80c9aa',weight:1.25,opacity:.96,fill:true,fillColor:'#55b889',fillOpacity:.20}};
 window.rxParcelHoverStyle=function(){return {color:'#a0e8ca',weight:2.1,opacity:1,fillColor:'#63e6a5',fillOpacity:.24}};
 let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0;
 // Search owns navigation; polygon clicks call rxV46SelectProperty directly.
 function fitSearch(g){const m=mapRef();if(!m||!g)return;try{const b=L.geoJSON(g).getBounds();if(!b.isValid())return;m.invalidateSize({animate:false});const size=m.getSize();m.fitBounds(b,{paddingTopLeft:[24,Math.min(310,size.y*.42)],paddingBottomRight:[24,28],maxZoom:16,animate:false})}catch(e){}}
 // W1a: area/status/condition on the pre-click card come from the map cell, which the server may reuse
 // for hours. Status and condition change without the outline changing, so a cell read more than
 // 5 min ago never shows them (the live /map-panel answer fills them in); if that answer fails, the
 // remaining cell fields carry the date SICAR was read. Search results and live answers are untouched.
 const RX46_CELL_STATUS_MAX_MS=300000;
 function rx46CellAge(s,g){const t=(g&&typeof window.rxW1aCellFetchedAt==='function')?window.rxW1aCellFetchedAt(g):null;if(!Number.isFinite(t))return s;if(Date.now()-t<=RX46_CELL_STATUS_MAX_MS)return {...s,__rxCellFetchedAt:t};return {...s,status:'',car_status:'',condition:'',__rxCellFetchedAt:t,__rxCellStale:true}}
 function rx46AsOf(t){const d=new Date(t),z=n=>String(n).padStart(2,'0');return `Dados do SICAR de ${z(d.getDate())}/${z(d.getMonth()+1)}/${d.getFullYear()} às ${z(d.getHours())}:${z(d.getMinutes())}`}
 function cardHtml(p){const id=identity(p),car=id.code,C=window.rxCopyCarC2,code=C?C.button(car):esc(car),st=statusInfo(p?.car_status||p?.status),rows=[['Área',esc(ha(p?.area_ha))],['Status',st.label?`<span class="rx46-status ${st.cls}">${esc(st.label)}</span>`:''],['Tipo',esc(typeLabel(p?.property_type||p?.type))],['Condição',esc(String(p?.condition??'').trim())],['Módulos fiscais',esc(num(p?.fiscal_modules,2))],['Criação',esc(date(p?.created_at))],['Atualização',esc(date(p?.updated_at))]].filter(r=>r[1]);const head=id.named?`<h3 class="rx46-title">${esc(id.title)}</h3>${car?`<div class="rx46-code-row">${code}</div>`:''}`:(car?`<h3 class="rx46-title rx46-title-code">${code}</h3>`:'');return `<div class="rx46-card" data-car="${esc(car)}" data-rx-copy-scope${id.named?' data-rx-named="1"':''}${p?.__rx46Enriched?' data-rx46-enriched="1"':''}><div class="rx46-head"><span class="rx46-car-badge">CAR</span><span class="rx46-spacer"></span>${window.rxShareW1a?window.rxShareW1a.html(car,id.place):''}<button type="button" class="rx46-linkbtn" data-rx46-action="kml">Mapa KML</button><button type="button" class="rx46-x" aria-label="Fechar" data-rx46-action="close">×</button></div>${head}${id.place?`<div class="rx46-place">${esc(id.place)}</div>`:''}<button type="button" class="rx46-demo" data-rx46-action="demo" title="Abre a Consulta Pública oficial do SICAR; informe o código CAR se o site solicitar.">Consultar no SICAR (site oficial)</button>${rows.length?`<div class="rx46-grid">${rows.map(([l,v])=>`<div class="rx46-field"><small>${l}</small><b>${v}</b></div>`).join('')}</div>`:''}${rows.length&&p?.__rxCellStale&&p?.__rx46EnrichFailed&&!p?.__rx46Enriched?`<div class="rx46-asof">${esc(rx46AsOf(p.__rxCellFetchedAt))}</div>`:''}${sigefRef(p)}<button type="button" class="rx46-cta" data-rx46-action="full">VER ANÁLISE COMPLETA</button></div>`}
 function anchorFor(g,latlng){if(latlng&&Number.isFinite(latlng.lat)&&Number.isFinite(latlng.lng))return latlng;try{const b=L.geoJSON(g).getBounds();if(b.isValid())return b.getCenter()}catch(e){}const m=mapRef();return m?m.getCenter():null}
 function replaceSelectedLayer(g){const m=mapRef();if(!m||!g)return;let fresh=null;try{fresh=L.geoJSON(g,{className:'rx46-selection',style:{color:'#63e6a5',weight:3.1,opacity:1,fillColor:'#63e6a5',fillOpacity:.22,interactive:false}}).addTo(m);fresh.bringToFront?.()}catch(e){return}const old=selectedLayer;selectedLayer=fresh;if(old){try{m.removeLayer(old)}catch(e){}}}
 let rx46AnchorSeq=-1;
 function rx46PlaceC2c(m,ll){try{const el=popup&&popup.getElement();if(!m||!el||!selectedLayer||!ll)return;if(!m.__rx46PlaceHook){m.__rx46PlaceHook=1;m.on('zoomend',()=>{try{if(popup&&m.hasLayer(popup))rx46PlaceC2c(m,popup.getLatLng())}catch(e){}})}let b=null;try{b=selectedLayer.getBounds()}catch(e){return}if(!b||!b.isValid())return;const size=m.getSize(),W=el.offsetWidth,H=el.offsetHeight,G=12,TIP=20,R=selected&&selected.__rx46Enriched?0:150,HR=H+R;const a=m.latLngToContainerPoint(ll),p1=m.latLngToContainerPoint(b.getNorthWest()),p2=m.latLngToContainerPoint(b.getSouthEast());const s={l:Math.min(p1.x,p2.x),t:Math.min(p1.y,p2.y),r:Math.max(p1.x,p2.x),b:Math.max(p1.y,p2.y)};const mr=m.getContainer().getBoundingClientRect();let top=8;const st=document.querySelector('#rxMapState');if(st&&(st.textContent||'').trim()&&getComputedStyle(st).display!=='none'){const r=st.getBoundingClientRect();if(r.height)top=Math.max(top,Math.round(r.bottom-mr.top)+8)}const bottom=size.y-(size.x<=720?76:12),left=8,right=size.x-8;const cx=x=>Math.min(Math.max(x,left+W/2),right-W/2),cy=y=>Math.max(top,Math.min(y,bottom-HR));const X=cx(Math.min(Math.max(a.x,s.l),s.r)),midT=cy((s.t+s.b)/2-HR/2);const C=[{side:'above',x:X,t:s.t-G-TIP-HR},{side:'below',x:X,t:s.b+G},{side:'right',x:s.r+G+W/2,t:midT},{side:'left',x:s.l-G-W/2,t:midT}];const ov=c=>Math.max(0,Math.min(c.x+W/2,s.r)-Math.max(c.x-W/2,s.l))*Math.max(0,Math.min(c.t+HR,s.b)-Math.max(c.t,s.t));const inside=c=>c.x-W/2>=left-.5&&c.x+W/2<=right+.5&&c.t>=top-.5&&c.t+HR<=bottom+.5;let pick=C.find(c=>inside(c)&&ov(c)===0),side=pick?pick.side:'overlap';if(!pick)pick=C.map(c=>({...c,x:cx(c.x),t:cy(c.t)})).sort((u,v)=>ov(u)-ov(v))[0];popup.options.offset=[Math.round(pick.x-a.x),Math.round(pick.t+H+TIP-a.y)];popup.update();el.classList.toggle('rx46-tipless',side!=='above');window.__rx46Placement={side,overlap_px:Math.round(ov(pick)),reserve:R,card:{w:W,h:H},selection:s}}catch(e){}}function renderAnchor(latlng){const m=mapRef();if(!m||!selected||!latlng)return;if(!popup)popup=L.popup({className:'rx46-anchor-popup',closeButton:false,autoPan:false,closeOnClick:false,maxWidth:230,minWidth:210,offset:[0,-8]});const open=m.hasLayer(popup),el=open?popup.getElement():null,same=!!el&&rx46AnchorSeq===seq,h0=same?el.offsetHeight:0;const fa=same&&el.contains(document.activeElement)?document.activeElement:null,fsel=!fa?'':fa.matches('[data-rx-copy-fallback]')?'[data-rx-copy-fallback]':fa.matches('[data-rx-copy-car]')?'[data-rx-copy-car]':fa.dataset?.rx46Action?`[data-rx46-action="${fa.dataset.rx46Action}"]`:'';if(!same)popup.options.offset=[0,-8];popup.setLatLng(latlng).setContent(cardHtml(selected));if(!open)popup.openOn(m);if(!same)rx46PlaceC2c(m,latlng);rx46AnchorSeq=seq;if(same){const d=el.offsetHeight-h0;if(d){const o=popup.options.offset||[0,-8];popup.options.offset=[(Array.isArray(o)?o[0]:o.x)||0,(Array.isArray(o)?o[1]:o.y)+d];popup.update()}if(fsel){try{el.querySelector(fsel)?.focus({preventScroll:true})}catch(e){}}}}
 async function enrich(localSeq,latlng){const car=String(selected?.car_code||'').trim().toUpperCase();if(!car)return;const failed=()=>{if(localSeq!==seq||!selected||!selected.__rxCellStale||selected.__rx46Enriched)return;selected={...selected,__rx46EnrichFailed:true};window.current={...selected};renderAnchor(latlng)};try{const {ok:rOk,d}=await (window.rxMapPanelOnce?window.rxMapPanelOnce(car):fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`).then(async r=>({ok:r.ok,d:await r.json()})));if(localSeq!==seq)return;if(!rOk||!d?.ok){failed();return}selected={...selected,...d,status:d.car_status||selected.status,type:d.property_type||selected.type,geometry:d.geometry||selected.geometry,__rx46Enriched:true};window.current={...selected};replaceSelectedLayer(selected.geometry);renderAnchor(latlng);sigefFollowUp(localSeq,latlng,car)}catch(e){failed()}}
 // C2b: a reference query that did not answer gets ONE automatic retry while this same card is open.
 // The late repaint never moves the CTA under a mouse already on the card: it waits for the mouse to leave.
 function sigefFollowUp(localSeq,latlng,car){const R=window.rxSigefRefC2;if(!R||!R.needsRetry(selected))return;R.scheduleRetry(car,()=>localSeq===seq&&!!popup&&!!mapRef()?.hasLayer(popup),d=>{if(localSeq!==seq||!selected)return;if(d)selected={...selected,...d,status:d.car_status||selected.status,type:d.property_type||selected.type,geometry:d.geometry||selected.geometry,__rx46Enriched:true};window.current={...selected};const el=popup?.getElement?.(),paint=()=>{if(localSeq===seq&&selected)renderAnchor(latlng)};if(el&&window.matchMedia?.('(hover:hover) and (pointer:fine)').matches&&el.matches(':hover')){el.addEventListener('mouseleave',paint,{once:true});return}paint()})}
 function closeAnchor(){const m=mapRef();if(m&&popup&&m.hasLayer(popup)){try{m.removeLayer(popup)}catch(e){}}}
 window.rxV46CloseAnchor=closeAnchor;
 window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;selected=rx46CellAge({...(p||{}),geometry:g||(p||{}).geometry||null},g);window.current={...selected};replaceSelectedLayer(selected.geometry);const anchor=anchorFor(selected.geometry,latlng);renderAnchor(anchor);enrich(seq,anchor)};
 function normalizeV45(){document.querySelectorAll('.rx45-kpi').forEach(box=>{const label=box.querySelector('small')?.textContent?.trim(),b=box.querySelector('b');if(!b)return;if(label==='Datas'){b.textContent=b.textContent.replace(/\d{4}-\d{2}-\d{2}(?:T[^ ·]+)?/g,x=>date(x))}if(label==='Situação CAR'&&!b.dataset.rx46){const st=statusInfo(b.textContent.trim());b.dataset.rx46='1';b.innerHTML=st.label?`<span class="rx46-status-seal ${st.cls}">${esc(st.label)}</span>`:''}});document.querySelectorAll('.rx45-row').forEach(row=>{if(row.querySelector('b')?.textContent?.trim()==='Tipo do imóvel'){const span=row.querySelector('span');if(span&&!span.dataset.rx46){span.dataset.rx46='1';span.textContent=typeLabel(span.textContent.trim())}}})}
 function fitMobile(){if(!matchMedia('(max-width:720px)').matches||!selected?.geometry)return;const m=mapRef(),host=q('#rx43SnapshotHost');if(!m||!host)return;try{m.invalidateSize({animate:false});const b=L.geoJSON(selected.geometry).getBounds();if(!b.isValid())return;const h=Math.min(host.getBoundingClientRect().height,m.getSize().y*.78);m.fitBounds(b,{paddingTopLeft:[18,18],paddingBottomRight:[18,Math.max(36,h+18)],maxZoom:16,animate:false})}catch(e){}}
 function postOpen(){[70,220,650,1300].forEach(ms=>setTimeout(()=>{normalizeV45();fitMobile()},ms))}
 function openFull(){if(!selected||typeof legacyOpen!=='function')return;closeAnchor();const payload={...selected,status:selected.status||selected.car_status,type:selected.type||selected.property_type,geometry:selected.geometry};window.current={...payload};legacyOpen(payload,payload.geometry);postOpen()}
 function openOfficialSicar(){const car=String(selected?.car_code||'').trim().toUpperCase();if(!car)return;let pending=Promise.resolve(false);try{if(window.rxCopyCarC2)pending=window.rxCopyCarC2.copy(car)}catch(e){}window.open('https://consulta.car.gov.br/','_blank','noopener,noreferrer');pending.then(ok=>{if(ok===true)window.toast?.('Código CAR copiado. Cole no campo de consulta do SICAR.')}).catch(()=>{})}
 function action(e){const btn=e.target.closest?.('[data-rx46-action]');if(!btn)return;const act=btn.dataset.rx46Action;e.preventDefault();e.stopPropagation();if(act==='close')closeAnchor();else if(act==='full')openFull();else if(act==='kml'&&selected?.car_code)window.open(`/v1/exports/property/${encodeURIComponent(selected.car_code)}/kml`,'_blank');else if(act==='demo'&&selected?.car_code)openOfficialSicar()}
 function install(){const m=mapRef();if(!m||typeof window.showProperty!=='function'){if((window.__rx46InstallAttempts=(window.__rx46InstallAttempts||0)+1)<=4)setTimeout(install,120);return}legacyOpen=window.showProperty;window.__rx46LegacyOpen=legacyOpen;window.showProperty=showProperty=function(p,g){fitSearch(g||p?.geometry);window.rxV46SelectProperty(p,g,null)};document.addEventListener('click',action,true);try{const layers=window.rxBasemapLayersV43;if(layers?.roads)layers.roads.setZIndex(625);if(layers?.places)layers.places.setZIndex(630)}catch(e){}window.rxV46Installed=true}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
</script>
<!-- RX_MAP_V46_TWO_LEVEL -->
'''

html = html.replace("</body>", V46_UI + "</body>")
portal_v8.PORTAL_HTML = html
portal_v8.APP_PORTAL_VERSION = "0.47.0-v47-search-name-truth-fill"

print("RX_MAP_V46=two_level_anchor_cached_grid_labels_truthful_panel_cta_only w1a_cells:canvas_progressive_hysteresis uf:local cache:6h_bytes", flush=True)

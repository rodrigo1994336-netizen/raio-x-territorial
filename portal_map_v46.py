from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_sicar_resilient

app = portal_v8.app

# V46 viewport cache. The cache key is a snapped geographic envelope so a small
# back-and-forth pan reuses the same CAR payload instead of creating a new WFS
# request for every pixel movement.
_V46_VIEWPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_V46_VIEWPORT_TTL = 300
_V46_VIEWPORT_MAX = 180
# Cache schema bump for V48-T. Old V46 entries without limit/zoom must never
# collide with post-fix entries, even during overlapping process lifetimes.
_V46_VIEWPORT_CACHE_SCHEMA = "V48T1"


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


def _cache_get(key: str) -> dict[str, Any] | None:
    cached = _V46_VIEWPORT_CACHE.get(key)
    if not cached:
        return None
    if time.monotonic() - cached[0] >= _V46_VIEWPORT_TTL:
        _V46_VIEWPORT_CACHE.pop(key, None)
        return None
    out = dict(cached[1])
    out["cached"] = True
    return out


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _V46_VIEWPORT_CACHE[key] = (time.monotonic(), value)
    if len(_V46_VIEWPORT_CACHE) > _V46_VIEWPORT_MAX:
        for old_key, _ in sorted(_V46_VIEWPORT_CACHE.items(), key=lambda kv: kv[1][0])[:30]:
            _V46_VIEWPORT_CACHE.pop(old_key, None)


@app.get("/v1/live/sicar/viewport-v46")
async def live_sicar_viewport_v46(
    west: float,
    south: float,
    east: float,
    north: float,
    uf: str | None = None,
    limit: int = 200,
    zoom: int | None = None,
):
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=422, detail="Área do mapa inválida.")
    span = max(east - west, north - south)
    if span > 1.5:
        raise HTTPException(status_code=422, detail="Área visível ampla demais para carregar limites CAR.")

    w, s, e, n, step = _snap_bounds(west, south, east, north)
    cap = max(1, min(int(limit or 200), 240))
    zoom_key = str(int(zoom)) if zoom is not None else "NA"
    key_suffix = f":limit={cap}:zoom={zoom_key}"
    auto_key = f"{_V46_VIEWPORT_CACHE_SCHEMA}:AUTO:{w:.6f}:{s:.6f}:{e:.6f}:{n:.6f}:{step:.4f}{key_suffix}"
    if not uf:
        hit = _cache_get(auto_key)
        if hit is not None:
            return hit
        center_lat = (s + n) / 2
        center_lon = (w + e) / 2
        uf = await portal_v8.base._reverse_uf(center_lat, center_lon)
    uf = str(uf).upper()
    key = f"{_V46_VIEWPORT_CACHE_SCHEMA}:{uf}:{w:.6f}:{s:.6f}:{e:.6f}:{n:.6f}:{step:.4f}{key_suffix}"
    hit = _cache_get(key)
    if hit is not None:
        if auto_key != key:
            _cache_put(auto_key, hit)
        return hit

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

    async def fetch_cell(cell: tuple[float, float, float, float]):
        cw, cs, ce, cn = cell
        return await portal_sicar_resilient.live_sicar_viewport_resilient(
            cw, cs, ce, cn, uf=uf, limit=50
        )

    results = await asyncio.gather(*(fetch_cell(cell) for cell in cells), return_exceptions=True)
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    partial_failures = 0
    truncated = False
    source_bytes = 0
    for result in results:
        if isinstance(result, Exception):
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

    if len(features) > cap:
        truncated = True
        features = features[:cap]
    if not features and partial_failures == len(results):
        raise HTTPException(status_code=502, detail="SICAR indisponível nesta quadrícula.")

    out = {
        "type": "FeatureCollection",
        "features": features,
        "uf": uf,
        "source": "SICAR/WFS público · quadrícula V46 com cache",
        "truncated": truncated,
        "source_bytes": source_bytes,
        "grid": {"west": w, "south": s, "east": e, "north": n, "step": step, "cells": len(cells)},
        "partial_failures": partial_failures,
        "cached": False,
        "zoom": zoom,
    }
    _cache_put(key, out)
    _cache_put(auto_key, out)
    return out


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
    "function setMapState(t){const msg=String(t||'');let el=qs('#rxMapState');const keep=!!el&&/^Mostrando \\d+ imóveis\\. Há mais nesta área\\.$/.test(el.textContent||'')&&/^Carregando imóveis rurais\\b/i.test(msg);if(keep)return;const quiet=!msg||/^Aproxime\\b/i.test(msg)||/^Busque um município/i.test(msg)||/imóvel\\(is\\) CAR carregado/i.test(msg)||/imóveis rurais nesta área/i.test(msg)||/aproxime o mapa para ver os imóveis/i.test(msg);if(!el&&!quiet){el=document.createElement('div');el.id='rxMapState';el.className='rx-map-state';qs('.main')?.appendChild(el)}if(!el)return;if(quiet){el.textContent='';return}el.textContent=msg}",
    "v46_map_state_patch_missing",
)

# Below the normal CAR-density zoom the ordinary parcels may leave the map, but
# V46 keeps the selected property in its own layer. Do not display a limitation banner.
once(
    "if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}rxParcelIndex.clear();setMapState('Aproxime o mapa para visualizar os limites dos imóveis rurais do CAR.');return}",
    "if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}rxParcelIndex.clear();setMapState('');return}",
    "v46_low_zoom_branch_missing",
)

# Use the snapped/cached V46 viewport endpoint and a browser memory cache. The
# existing V43 reconciler already adds the next set before removing stale parcels.
once(
    "const u=new URL('/v1/live/sicar/viewport',location.origin);",
    "const u=new URL('/v1/live/sicar/viewport-v46',location.origin);",
    "v46_viewport_url_missing",
)
once(
    "u.searchParams.set('limit',window.rxFieldMode?'35':'80');const r=await (window.rxFieldFetch?window.rxFieldFetch(u,window.rxFieldMode?6500:10000):fetch(u));const d=await r.json();",
    "u.searchParams.set('limit',window.rxFieldMode?'120':'200');u.searchParams.set('zoom',String(z));const pack=window.rx46ViewportRequest?await window.rx46ViewportRequest(u):null;const r=pack?pack.response:await (window.rxFieldFetch?window.rxFieldFetch(u,window.rxFieldMode?6500:10000):fetch(u));const d=pack?pack.data:await r.json();",
    "v46_viewport_fetch_patch_missing",
)

# A partial viewport must never look complete. Use the actual delivered feature
# count, never the requested URL limit and never an estimated total.
once(
    "setMapState(`${d.features?.length||0} imóvel(is) CAR carregado(s) nesta área${d.truncated?' · aproxime para ver mais':''}. Clique em um polígono.`)",
    "setMapState(d.truncated?`Mostrando ${d.features?.length||0} imóveis. Há mais nesta área.`:'')",
    "v48_t_truncation_notice_patch_missing",
)

# Polygon clicks no longer call V45/V43 directly. They select the V46 anchor card.
_old_click = "l.bindTooltip('',{sticky:true});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof showProperty==='function')showProperty(p,live.geometry)})"
_new_click = "l.bindTooltip('',{sticky:true});l.on('mouseover',()=>{try{l.setStyle(window.rxParcelHoverStyle?.()||{weight:2,fillOpacity:.23})}catch(e){}});l.on('mouseout',()=>{try{l.setStyle(rxParcelStyleFor(l.feature||ff))}catch(e){}});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);const live=l.feature||ff,p=propertyFromFeature(live);if(typeof window.rxV46SelectProperty==='function')window.rxV46SelectProperty(p,live.geometry,e.latlng);else if(typeof showProperty==='function')showProperty(p,live.geometry)})"
once(_old_click, _new_click, "v46_polygon_click_patch_missing")

# An empty-map click closes the anchor instead of launching a second point resolver.
once(
    "map.on('click',e=>resolvePoint(e.latlng.lat,e.latlng.lng).catch(x=>toast(x.message)));",
    "map.on('click',()=>window.rxV46CloseAnchor?.());",
    "v46_map_click_patch_missing",
)

V46_UI = r'''
<style id="rxMapV46">
.rx-map-state:empty{display:none!important}.rx-map-state{top:12px!important;left:12px!important;right:auto!important;max-width:260px!important;padding:6px 9px!important;font-size:9px!important;border-radius:9px!important;background:rgba(6,20,14,.88)!important;box-shadow:0 5px 18px #0005!important}
.rx46-anchor-popup .leaflet-popup-content-wrapper{background:rgba(7,21,15,.985);color:#eef8f2;border:1px solid #2a493b;border-radius:15px;box-shadow:0 18px 52px #0009;padding:0;backdrop-filter:blur(14px)}
.rx46-anchor-popup .leaflet-popup-content{width:218px!important;margin:0!important}.rx46-anchor-popup .leaflet-popup-tip{background:#0b2118;border:1px solid #2a493b;box-shadow:none}.rx46-card{padding:10px;display:grid;gap:8px}.rx46-card *{box-sizing:border-box}.rx46-head{display:flex;align-items:center;gap:6px}.rx46-car-badge{font-size:7px;line-height:1;padding:5px 6px;border-radius:7px;background:#173a2b;color:#8df0bd;font-weight:950;letter-spacing:.6px}.rx46-spacer{flex:1}.rx46-linkbtn,.rx46-x{border:0;background:transparent;color:#dceae2;font-size:7px;font-weight:850;padding:3px;cursor:pointer}.rx46-x{font-size:16px;line-height:1;color:#9fb5aa}.rx46-title{font-size:14px;line-height:1.18;margin:0;overflow-wrap:anywhere}.rx46-code{font:700 7px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;color:#79968a;overflow-wrap:anywhere}.rx46-demo{border:0;background:transparent;color:#72e9ad;font-size:8px;text-align:left;padding:0;text-decoration:underline;text-underline-offset:2px;cursor:pointer}.rx46-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 9px}.rx46-field{min-width:0}.rx46-field small{display:block;color:#91a99d;font-size:6.5px;text-transform:uppercase;letter-spacing:.42px;margin-bottom:2px}.rx46-field b{display:block;font-size:9px;line-height:1.28;overflow-wrap:anywhere}.rx46-status{display:inline-flex!important;width:max-content;max-width:100%;align-items:center;padding:3px 6px;border-radius:999px;font-size:8px!important;background:#26372f;color:#c5d5cd}.rx46-status.active{background:#123c29;color:#7ff0b6}.rx46-status.pending{background:#4b3917;color:#ffd77d}.rx46-status.bad{background:#4d211c;color:#ffad9d}.rx46-cta{width:100%;min-height:36px;border:1px solid #63e6a5;border-radius:9px;background:#63e6a5;color:#052116;font-size:8px;font-weight:950;cursor:pointer}.rx46-selection path{vector-effect:non-scaling-stroke}
.rx46-status-seal{display:inline-flex;align-items:center;padding:4px 7px;border-radius:999px;background:#26372f;color:#d5e4dc;font-size:9px;font-weight:900}.rx46-status-seal.active{background:#123c29;color:#7ff0b6}.rx46-status-seal.pending{background:#4b3917;color:#ffd77d}.rx46-status-seal.bad{background:#4d211c;color:#ffad9d}.rx46-code-mini{display:inline-block;margin-left:5px;color:#789789;font:700 7px ui-monospace,SFMono-Regular,Menlo,monospace}
@media(max-width:720px){.rx-map-state{top:8px!important;left:8px!important;right:auto!important}.rx45-tools{flex-direction:row!important;flex-wrap:nowrap!important;gap:3px!important}.rx45-tool{height:28px!important;padding:0 5px!important;min-width:0!important}.rx45-panel-card{transition:transform .18s ease,opacity .18s ease}}
@media(max-width:390px){.rx45-tools{flex-direction:row!important}.rx45-tool{height:27px!important;padding:0 4px!important}}
</style>
<script id="rxMapV46Script">
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const mapRef=()=>{try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}};
 const fmt=(v,d=2)=>{const n=Number(String(v??'').replace(',','.'));return Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d}):'—'};
 const txt=v=>(v===null||v===undefined||v==='')?'—':String(v);
 const date=v=>{const m=String(v||'').match(/(\d{4})-(\d{2})-(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:(v?String(v):'—')};
 const STATUS={AT:'Ativo',PE:'Pendente',CA:'Cancelado',SU:'Suspenso',IN:'Inativo'};
 const TYPES={IRU:'Imóvel Rural',AST:'Assentamento',PCT:'Povos e Comunidades Tradicionais'};
 const statusInfo=v=>{const raw=String(v||'').trim(),label=STATUS[raw.toUpperCase()]||(raw.length>3?raw:'Situação informada'),low=label.toLowerCase();return {raw,label,cls:/ativ/.test(low)?'active':/pendent/.test(low)?'pending':/cancel|suspens|inativ/.test(low)?'bad':''}};
 const typeLabel=v=>{const raw=String(v||'').trim();return TYPES[raw.toUpperCase()]||(raw.length>3?raw:'Tipo informado')};
 const viewportMemory=new Map();
 function snappedUrl(input){const u=new URL(String(input),location.origin),w=Number(u.searchParams.get('west')),s=Number(u.searchParams.get('south')),e=Number(u.searchParams.get('east')),n=Number(u.searchParams.get('north')),span=Math.max(e-w,n-s);let step=span<=.08?.02:span<=.25?.05:span<=.60?.10:.20;if([w,s,e,n].every(Number.isFinite)){u.searchParams.set('west',String(Math.floor(w/step)*step));u.searchParams.set('south',String(Math.floor(s/step)*step));u.searchParams.set('east',String(Math.ceil(e/step)*step));u.searchParams.set('north',String(Math.ceil(n/step)*step))}return u}
 window.rx46ViewportRequest=async function(input){const u=snappedUrl(input),key=u.toString(),cached=viewportMemory.get(key);if(cached){fetch(u,{cache:'no-store'}).then(async r=>{if(r.ok){const d=await r.json();viewportMemory.set(key,d)}}).catch(()=>{});return {response:{ok:true,status:200},data:cached}}const r=await fetch(u,{cache:'no-store'}),d=await r.json();if(r.ok){viewportMemory.set(key,d);if(viewportMemory.size>36)viewportMemory.delete(viewportMemory.keys().next().value)}return {response:r,data:d}};
 window.rxParcelStyle=function(){return {color:'#80c9aa',weight:1.25,opacity:.96,fill:true,fillColor:'#55b889',fillOpacity:.20}};
 window.rxParcelHoverStyle=function(){return {color:'#a0e8ca',weight:2.1,opacity:1,fillColor:'#63e6a5',fillOpacity:.24}};
 let legacyOpen=null,popup=null,selectedLayer=null,selected=null,seq=0;
 // Search owns navigation; polygon clicks call rxV46SelectProperty directly.
 function fitSearch(g){const m=mapRef();if(!m||!g)return;try{const b=L.geoJSON(g).getBounds();if(!b.isValid())return;m.invalidateSize({animate:false});const size=m.getSize();m.fitBounds(b,{paddingTopLeft:[24,Math.min(310,size.y*.42)],paddingBottomRight:[24,28],maxZoom:16,animate:false})}catch(e){}}
 function titleFor(p){return p?.validated_name||`${txt(p?.municipality)}${p?.uf?' - '+p.uf:''}`}
 function cardHtml(p){const st=statusInfo(p?.car_status||p?.status),areaHa=Number(p?.area_ha),areaM2=Number.isFinite(Number(p?.area_m2))?Number(p.area_m2):(Number.isFinite(areaHa)?areaHa*10000:null),type=typeLabel(p?.property_type||p?.type);return `<div class="rx46-card" data-car="${esc(p?.car_code||'')}"><div class="rx46-head"><span class="rx46-car-badge">CAR</span><span class="rx46-spacer"></span><button type="button" class="rx46-linkbtn" data-rx46-action="kml">Mapa KML</button><button type="button" class="rx46-x" aria-label="Fechar" data-rx46-action="close">×</button></div><h3 class="rx46-title">${esc(titleFor(p))}</h3><div class="rx46-code">${esc(p?.car_code||'—')}</div><button type="button" class="rx46-demo" data-rx46-action="demo" title="Abre a Consulta Pública oficial do SICAR; informe o código CAR se o site solicitar.">Consultar no SICAR (site oficial)</button><div class="rx46-grid"><div class="rx46-field"><small>Área</small><b>${fmt(areaHa,2)} ha</b></div><div class="rx46-field"><small>Área (m²)</small><b>${fmt(areaM2,0)}</b></div><div class="rx46-field"><small>Status</small><b><span class="rx46-status ${st.cls}">${esc(st.label)}</span></b></div><div class="rx46-field"><small>Tipo</small><b>${esc(type)}</b></div><div class="rx46-field"><small>Condição</small><b>${esc(txt(p?.condition))}</b></div><div class="rx46-field"><small>Módulos fiscais</small><b>${fmt(p?.fiscal_modules,2)}</b></div><div class="rx46-field"><small>Criação</small><b>${esc(date(p?.created_at))}</b></div><div class="rx46-field"><small>Atualização</small><b>${esc(date(p?.updated_at))}</b></div></div><button type="button" class="rx46-cta" data-rx46-action="full">VER ANÁLISE COMPLETA</button></div>`}
 function anchorFor(g,latlng){if(latlng&&Number.isFinite(latlng.lat)&&Number.isFinite(latlng.lng))return latlng;try{const b=L.geoJSON(g).getBounds();if(b.isValid())return b.getCenter()}catch(e){}const m=mapRef();return m?m.getCenter():null}
 function replaceSelectedLayer(g){const m=mapRef();if(!m||!g)return;let fresh=null;try{fresh=L.geoJSON(g,{className:'rx46-selection',style:{color:'#63e6a5',weight:3.1,opacity:1,fillColor:'#63e6a5',fillOpacity:.22,interactive:false}}).addTo(m);fresh.bringToFront?.()}catch(e){return}const old=selectedLayer;selectedLayer=fresh;if(old){try{m.removeLayer(old)}catch(e){}}}
 function renderAnchor(latlng){const m=mapRef();if(!m||!selected||!latlng)return;if(!popup)popup=L.popup({className:'rx46-anchor-popup',closeButton:false,autoPan:false,closeOnClick:false,maxWidth:230,minWidth:210,offset:[0,-8]});popup.setLatLng(latlng).setContent(cardHtml(selected));if(!m.hasLayer(popup))popup.openOn(m)}
 async function enrich(localSeq,latlng){const car=String(selected?.car_code||'').trim().toUpperCase();if(!car)return;try{const r=await fetch(`/v1/live/map-panel/${encodeURIComponent(car)}`),d=await r.json();if(localSeq!==seq||!r.ok||!d?.ok)return;selected={...selected,...d,status:d.car_status||selected.status,type:d.property_type||selected.type,geometry:d.geometry||selected.geometry};window.current={...selected};replaceSelectedLayer(selected.geometry);renderAnchor(latlng)}catch(e){}}
 function closeAnchor(){const m=mapRef();if(m&&popup&&m.hasLayer(popup)){try{m.removeLayer(popup)}catch(e){}}}
 window.rxV46CloseAnchor=closeAnchor;
 window.rxV46SelectProperty=function(p,g,latlng){const m=mapRef();if(!m||!p)return;seq+=1;selected={...(p||{}),geometry:g||(p||{}).geometry||null};window.current={...selected};replaceSelectedLayer(selected.geometry);const anchor=anchorFor(selected.geometry,latlng);renderAnchor(anchor);enrich(seq,anchor)};
 function normalizeV45(){document.querySelectorAll('.rx45-kpi').forEach(box=>{const label=box.querySelector('small')?.textContent?.trim(),b=box.querySelector('b');if(!b)return;if(label==='Datas'){b.textContent=b.textContent.replace(/\d{4}-\d{2}-\d{2}(?:T[^ ·]+)?/g,x=>date(x))}if(label==='Situação CAR'&&!b.dataset.rx46){const st=statusInfo(b.textContent.trim());b.dataset.rx46='1';b.innerHTML=`<span class="rx46-status-seal ${st.cls}">${esc(st.label)}</span>${st.raw&&st.raw!==st.label?`<span class="rx46-code-mini">${esc(st.raw)}</span>`:''}`}});document.querySelectorAll('.rx45-row').forEach(row=>{if(row.querySelector('b')?.textContent?.trim()==='Tipo do imóvel'){const span=row.querySelector('span');if(span&&!span.dataset.rx46){span.dataset.rx46='1';span.textContent=typeLabel(span.textContent.trim())}}})}
 function fitMobile(){if(!matchMedia('(max-width:720px)').matches||!selected?.geometry)return;const m=mapRef(),host=q('#rx43SnapshotHost');if(!m||!host)return;try{m.invalidateSize({animate:false});const b=L.geoJSON(selected.geometry).getBounds();if(!b.isValid())return;const h=Math.min(host.getBoundingClientRect().height,m.getSize().y*.78);m.fitBounds(b,{paddingTopLeft:[18,18],paddingBottomRight:[18,Math.max(36,h+18)],maxZoom:16,animate:false})}catch(e){}}
 function postOpen(){[70,220,650,1300].forEach(ms=>setTimeout(()=>{normalizeV45();fitMobile()},ms))}
 function openFull(){if(!selected||typeof legacyOpen!=='function')return;closeAnchor();const payload={...selected,status:selected.status||selected.car_status,type:selected.type||selected.property_type,geometry:selected.geometry};window.current={...payload};legacyOpen(payload,payload.geometry);postOpen()}
 function openOfficialSicar(){const car=String(selected?.car_code||'').trim().toUpperCase();if(!car)return;try{navigator.clipboard?.writeText(car).then(()=>window.toast?.('Código CAR copiado. Cole no campo de consulta do SICAR.')).catch(()=>{})}catch(e){}window.open('https://consulta.car.gov.br/','_blank','noopener,noreferrer')}
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

print("RX_MAP_V46=two_level_anchor_cached_grid_labels_truthful_panel_cta_only", flush=True)

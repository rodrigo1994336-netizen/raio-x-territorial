from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

import portal_v8

app = portal_v8.app
CANONICAL_FP = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
NATIVE_MIN_ZOOM = 10
NATIVE_MAX_ZOOM = 16
FALLBACK_ABOVE_ZOOM = 16
EXPECTED_UFS = {"AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"}


def _truthy(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _preview_enabled() -> bool:
    return _truthy("RX_V48_PMTILES_PREVIEW_ENABLED")


def _active_url() -> str:
    return str(os.getenv("RX_V48_ACTIVE_JSON_URL", "")).strip()


@app.get("/v1/live/pmtiles-consumer-v48/status")
async def pmtiles_consumer_status_v48() -> dict[str, Any]:
    enabled = _preview_enabled() and bool(_active_url())
    return {
        "ok": True,
        "consumer_deployed": True,
        "public_default": "viewport-v46",
        "preview_enabled": enabled,
        "active_json_configured": bool(_active_url()),
        "active_json_cache": "no-store",
        "native_min_zoom": NATIVE_MIN_ZOOM,
        "native_max_zoom": NATIVE_MAX_ZOOM,
        "fallback_v46_above_zoom": FALLBACK_ABOVE_ZOOM,
        "session_source_pinned": True,
        "global_active_json_mutated": False,
        "canonical_manifest_fingerprint": CANONICAL_FP,
    }


@app.get("/v1/live/pmtiles-consumer-v48/resolve-uf")
async def pmtiles_consumer_resolve_uf_v48(lat: float, lon: float) -> dict[str, str]:
    if not _preview_enabled():
        raise HTTPException(status_code=404, detail="pmtiles_preview_disabled")
    uf = str(await portal_v8.base._reverse_uf(lat, lon) or "").upper().strip()
    if uf not in EXPECTED_UFS:
        raise HTTPException(status_code=404, detail="uf_not_resolved")
    return {"uf": uf}


def _boot_config() -> dict[str, Any]:
    enabled = _preview_enabled() and bool(_active_url())
    return {
        "enabled": enabled,
        "activeUrl": _active_url() if enabled else None,
        "canonicalFingerprint": CANONICAL_FP,
        "nativeMinZoom": NATIVE_MIN_ZOOM,
        "nativeMaxZoom": NATIVE_MAX_ZOOM,
        "fallbackAboveZoom": FALLBACK_ABOVE_ZOOM,
        "publicDefault": "viewport-v46",
    }


def _inject_preview(html: str) -> str:
    if html.count("</body>") != 1:
        raise RuntimeError("pmtiles_preview_body_injection_point_invalid")
    boot = json.dumps(_boot_config(), ensure_ascii=False, separators=(",", ":"))
    script = _PREVIEW_SCRIPT.replace("__RX_BOOT__", boot)
    return html.replace("</body>", script + "</body>", 1)


@app.get("/v48/pmtiles-preview", response_class=HTMLResponse)
async def pmtiles_preview_v48() -> HTMLResponse:
    # Parallel page only. Importing this module never mutates portal_v8.PORTAL_HTML.
    return HTMLResponse(_inject_preview(portal_v8.PORTAL_HTML), headers={"Cache-Control": "no-store"})


_PREVIEW_SCRIPT = r'''
<script id="rxV48PmtilesConsumerBoot">
window.__RX_V48_PMTILES_BOOT__=__RX_BOOT__;
(function(){
 const boot=window.__RX_V48_PMTILES_BOOT__;
 window.__RX_V48_PMTILES_STATE__={mode:'v46',reason:boot.enabled?'not-started':'preview-disabled',uf:null};
 if(!boot.enabled)return;
 const mapRef=()=>{try{return(typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}};
 const loadScript=src=>new Promise((ok,bad)=>{const s=document.createElement('script');s.src=src;s.crossOrigin='anonymous';s.onload=ok;s.onerror=()=>bad(new Error('script_load_failed:'+src));document.head.appendChild(s)});
 const loadCss=href=>{const l=document.createElement('link');l.rel='stylesheet';l.href=href;l.crossOrigin='anonymous';document.head.appendChild(l)};
 const activeFetch=()=>fetch(boot.activeUrl,{cache:'no-store',headers:{'Cache-Control':'no-cache'}}).then(r=>{if(!r.ok)throw new Error('active_json_http_'+r.status);return r.json()});
 function validateActive(a){if(!a||a.mode!=='pmtiles-v48')throw new Error('active_json_mode_invalid');if(a.canonical_manifest_fingerprint!==boot.canonicalFingerprint)throw new Error('active_json_fingerprint_mismatch');const u=a.ufs||{};if(Object.keys(u).length!==27)throw new Error('active_json_ufs_not_27');for(const [uf,v] of Object.entries(u)){if(!/^[A-Z]{2}$/.test(uf)||!v||typeof v.pmtiles_url!=='string'||!/^https:\/\//.test(v.pmtiles_url))throw new Error('active_json_uf_entry_invalid:'+uf)}return a}
 async function resolveUf(m){const c=m.getCenter(),r=await fetch(`/v1/live/pmtiles-consumer-v48/resolve-uf?lat=${encodeURIComponent(c.lat)}&lon=${encodeURIComponent(c.lng)}`,{cache:'no-store'});if(!r.ok)throw new Error('uf_resolve_http_'+r.status);return(await r.json()).uf}
 let active=null,glLayer=null,currentUf=null,legacyViewportRequest=null,installed=false,sessionMode=null,tip=null;
 function useV46(reason){sessionMode='v46';window.__RX_V48_PMTILES_STATE__={mode:'v46',reason,uf:currentUf};const m=mapRef();if(m&&glLayer){try{m.removeLayer(glLayer)}catch(e){}glLayer=null}if(m&&tip){try{m.closeTooltip(tip)}catch(e){}tip=null}return'v46'}
 function syntheticEmpty(){return{response:{ok:true,status:200},data:{type:'FeatureCollection',features:[],source:'PMTiles V48',truncated:false,partial_failures:0,cached:true}}}
 async function installLibraries(){loadCss('https://unpkg.com/maplibre-gl@5.13.0/dist/maplibre-gl.css');await loadScript('https://unpkg.com/maplibre-gl@5.13.0/dist/maplibre-gl.js');await loadScript('https://unpkg.com/pmtiles@4.5.0/dist/pmtiles.js');await loadScript('https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.3/leaflet-maplibre-gl.js');if(!window.maplibregl||!window.pmtiles||!L.maplibreGL)throw new Error('pmtiles_library_contract_missing');const p=new pmtiles.Protocol({metadata:true});maplibregl.addProtocol('pmtiles',p.tile)}
 function styleFor(url){return{version:8,sources:{car:{type:'vector',url:'pmtiles://'+url}},layers:[{id:'rx-v48-car-fill',type:'fill',source:'car','source-layer':'car',minzoom:11,maxzoom:17,paint:{'fill-color':'#55b889','fill-opacity':.20}},{id:'rx-v48-car-line',type:'line',source:'car','source-layer':'car',minzoom:11,maxzoom:17,paint:{'line-color':'#80c9aa','line-width':1.25,'line-opacity':.96}},{id:'rx-v48-car-hover',type:'line',source:'car','source-layer':'car',minzoom:11,maxzoom:17,filter:['==',['get','car_code'],''],paint:{'line-color':'#a0e8ca','line-width':2.1,'line-opacity':1}}]}}
 function addLayer(m,uf,url){if(glLayer){try{m.removeLayer(glLayer)}catch(e){}glLayer=null}glLayer=L.maplibreGL({style:styleFor(url),interactive:false,pane:'overlayPane'}).addTo(m);currentUf=uf;sessionMode='pmtiles';window.__RX_V48_PMTILES_STATE__={mode:'pmtiles',reason:'native-z11-z16',uf}}
 async function reconcile(){const m=mapRef();if(!m)return;const z=m.getZoom();if(z>boot.fallbackAboveZoom){useV46('mandatory-fallback-z>16');return}if(z<11){useV46('preserve-v46-z<11');return}const uf=await resolveUf(m),e=active.ufs?.[uf];if(!e?.pmtiles_url)throw new Error('active_json_pmtiles_missing:'+uf);if(sessionMode==='pmtiles'&&currentUf===uf&&glLayer)return;addLayer(m,uf,e.pmtiles_url)}
 function hitAt(e){if(sessionMode!=='pmtiles'||!glLayer)return null;const gl=glLayer.getMaplibreMap?.();if(!gl)return null;try{return gl.queryRenderedFeatures(gl.project([e.latlng.lng,e.latlng.lat]),{layers:['rx-v48-car-fill']})?.[0]||null}catch(_){return null}}
 function hover(e){const h=hitAt(e),gl=glLayer?.getMaplibreMap?.();if(!gl)return;const code=String(h?.properties?.car_code||'');try{gl.setFilter('rx-v48-car-hover',['==',['get','car_code'],code])}catch(_){}const m=mapRef();if(!m)return;if(!h){if(tip){try{m.closeTooltip(tip)}catch(_){}tip=null}return}const p=h.properties||{},area=Number(p.area_ha);const txt=`<b>${code||'Imóvel rural'}</b><br>${Number.isFinite(area)?area.toLocaleString('pt-BR',{maximumFractionDigits:2})+' ha':'—'}`;if(!tip)tip=L.tooltip({sticky:true,opacity:.94});tip.setLatLng(e.latlng).setContent(txt).openOn(m)}
 function click(e){const h=hitAt(e);if(!h)return;try{const p=h.properties||{},g=h.toJSON?h.toJSON().geometry:h.geometry,prop={car_code:p.car_code,id_municipio:p.id_municipio,status:p.status,area_ha:p.area_ha,uf:currentUf};if(typeof window.rxV46SelectProperty==='function')window.rxV46SelectProperty(prop,g,e.latlng)}catch(err){console.warn('RX_V48_PMTILES_CLICK_FAIL',err)}}
 function markStart(label){performance.clearMarks('rx-v48-car-start');performance.clearMarks('rx-v48-car-painted');performance.mark('rx-v48-car-start');window.__RX_V48_PAINT_LABEL__=label}
 function markDone(label){requestAnimationFrame(()=>requestAnimationFrame(()=>{performance.mark('rx-v48-car-painted');const x=performance.measure('rx-v48-car-paint','rx-v48-car-start','rx-v48-car-painted');window.__RX_V48_LAST_PAINT__={mode:label,duration_ms:x.duration,at:new Date().toISOString(),zoom:mapRef()?.getZoom(),uf:currentUf};console.info('RX_V48_TPAINT',window.__RX_V48_LAST_PAINT__)}))}
 async function bootConsumer(){const m=mapRef();if(!m||typeof window.rx46ViewportRequest!=='function'){setTimeout(bootConsumer,120);return}if(installed)return;installed=true;legacyViewportRequest=window.rx46ViewportRequest;try{active=validateActive(await activeFetch());await installLibraries()}catch(err){sessionMode='v46';window.__RX_V48_PMTILES_STATE__={mode:'v46',reason:'preview-init-failed:'+String(err),uf:null};console.warn('RX_V48_PMTILES_FAIL_CLOSED',err);return}window.rx46ViewportRequest=async function(u){const z=mapRef()?.getZoom()??0;if(sessionMode==='pmtiles'&&z>=11&&z<=boot.fallbackAboveZoom)return syntheticEmpty();return legacyViewportRequest(u)};const originalAdd=addLayer;addLayer=function(mm,uf,url){markStart('pmtiles');originalAdd(mm,uf,url);const gl=glLayer?.getMaplibreMap?.();if(gl)gl.once('idle',()=>markDone('pmtiles'))};m.on('mousemove',hover);m.on('click',click);m.on('zoomend moveend',()=>reconcile().catch(err=>{console.warn('RX_V48_PMTILES_RECONCILE_FAIL',err);useV46('reconcile-failed')}));await reconcile()}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bootConsumer);else bootConsumer();
})();
</script>
<!-- RX_V48_PMTILES_CONSUMER_OFF_BY_DEFAULT -->
'''

print("RX_V48_PMTILES_CONSUMER=DEPLOYED_OFF_DEFAULT_V46 fallback_z_gt_16", flush=True)

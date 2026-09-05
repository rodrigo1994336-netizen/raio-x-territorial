from __future__ import annotations

from fastapi.responses import Response

import portal_v8

app = portal_v8.app

SW_JS = r'''
const RX_VERSION='rx-field-v43';
const SHELL_CACHE=RX_VERSION+'-shell';
const DATA_CACHE=RX_VERSION+'-data';
const TILE_CACHE=RX_VERSION+'-tiles';
const ASSET_CACHE=RX_VERSION+'-assets';

self.addEventListener('install',event=>{
  self.skipWaiting();
  event.waitUntil(caches.open(SHELL_CACHE).then(async cache=>{
    try{const r=await fetch('/',{cache:'no-store'});if(r&&r.ok)await cache.put('/',r.clone())}catch(e){}
  }));
});

self.addEventListener('activate',event=>{
  event.waitUntil((async()=>{
    const keep=new Set([SHELL_CACHE,DATA_CACHE,TILE_CACHE,ASSET_CACHE]);
    for(const key of await caches.keys())if(key.startsWith('rx-field-')&&!keep.has(key))await caches.delete(key);
    await self.clients.claim();
  })());
});

async function trimCache(cacheName,maxEntries){
  const cache=await caches.open(cacheName),keys=await cache.keys();
  for(let i=0;i<Math.max(0,keys.length-maxEntries);i++)await cache.delete(keys[i]);
}

async function networkFirst(req,cacheName,timeoutMs,maxEntries=180){
  const cache=await caches.open(cacheName);
  let timer;
  try{
    const timed=new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('timeout')),timeoutMs)});
    const res=await Promise.race([fetch(req),timed]);
    clearTimeout(timer);
    if(res&&(res.ok||res.type==='opaque')){await cache.put(req,res.clone()).catch(()=>{});await trimCache(cacheName,maxEntries).catch(()=>{})}
    return res;
  }catch(e){
    clearTimeout(timer);
    const hit=await cache.match(req);
    if(hit)return hit;
    throw e;
  }
}

async function staleWhileRevalidate(event,req,cacheName,maxEntries=180){
  const cache=await caches.open(cacheName),hit=await cache.match(req);
  const update=fetch(req).then(async res=>{
    if(res&&(res.ok||res.type==='opaque')){await cache.put(req,res.clone()).catch(()=>{});await trimCache(cacheName,maxEntries).catch(()=>{})}
    return res;
  });
  if(hit){event.waitUntil(update.catch(()=>{}));return hit}
  return await update;
}

async function cacheFirst(req,cacheName){
  const cache=await caches.open(cacheName),hit=await cache.match(req);
  if(hit)return hit;
  const res=await fetch(req);
  if(res&&(res.ok||res.type==='opaque'))await cache.put(req,res.clone()).catch(()=>{});
  return res;
}

self.addEventListener('fetch',event=>{
  const req=event.request;
  if(req.method!=='GET')return;
  const u=new URL(req.url);

  if(u.origin===location.origin&&u.pathname==='/'){
    event.respondWith(networkFirst(req,SHELL_CACHE,2400,4));
    return;
  }

  if(u.hostname==='unpkg.com'&&u.pathname.includes('/leaflet@1.9.4/')){
    event.respondWith(cacheFirst(req,ASSET_CACHE));
    return;
  }

  if(u.hostname.endsWith('.tile.openstreetmap.org')){
    event.respondWith(networkFirst(req,TILE_CACHE,2800,240));
    return;
  }

  if(u.origin===location.origin&&(
      u.pathname==='/v1/live/sicar/viewport' ||
      u.pathname==='/v1/live/property-names/viewport' ||
      u.pathname==='/v1/live/cities' ||
      u.pathname==='/v1/live/resolve' ||
      u.pathname.startsWith('/v1/live/snapshot/') ||
      u.pathname.startsWith('/v1/live/property-identity/') ||
      u.pathname.startsWith('/v1/live/property/')
  )){
    event.respondWith(staleWhileRevalidate(event,req,DATA_CACHE,180));
    return;
  }
});
'''


@app.get('/sw.js')
def rx_field_service_worker():
    return Response(
        SW_JS,
        media_type='application/javascript',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Service-Worker-Allowed': '/',
            'X-RaioX-Field-Mode': 'v43',
        },
    )


FIELD_UI = r'''
<style>
.rx-net-state{position:absolute;z-index:792;right:14px;bottom:14px;display:none;align-items:center;gap:6px;padding:6px 9px;border:1px solid #355044;border-radius:9px;background:rgba(6,22,15,.9);color:#b5c9bf;font-size:8px;box-shadow:0 6px 18px #0006;pointer-events:none}.rx-net-state.show{display:flex}.rx-net-dot{width:6px;height:6px;border-radius:50%;background:#ffc866}.rx-net-state.offline .rx-net-dot{background:#ff756f}.rx-net-state.online .rx-net-dot{background:#63e6a5}
@media(max-width:720px){.rx-net-state{right:9px;bottom:38px;font-size:7px}}
</style>
<script>
(function(){
  const conn=navigator.connection||navigator.mozConnection||navigator.webkitConnection||null;
  function mobile(){return window.matchMedia&&window.matchMedia('(max-width: 820px)').matches}
  function weak(){
    if(!navigator.onLine)return true;
    if(conn){
      const t=String(conn.effectiveType||'').toLowerCase();
      if(conn.saveData||t==='slow-2g'||t==='2g'||t==='3g')return true;
      if(Number(conn.downlink)>0&&Number(conn.downlink)<1.6)return true;
    }
    return !conn&&mobile();
  }
  window.rxFieldMode=weak();
  window.rxNetworkProfile=()=>({online:navigator.onLine,field:window.rxFieldMode,effectiveType:conn?.effectiveType||null,downlink:conn?.downlink||null,saveData:!!conn?.saveData});

  window.rxFieldFetch=async function(input,timeoutMs=7000,init={}){
    if(navigator.serviceWorker&&navigator.serviceWorker.controller)return fetch(input,init);
    const ctrl=new AbortController();
    const timer=setTimeout(()=>ctrl.abort(),timeoutMs);
    try{return await fetch(input,{...init,signal:ctrl.signal})}finally{clearTimeout(timer)}
  };

  function statusEl(){
    let el=document.querySelector('#rxNetState');
    if(!el){el=document.createElement('div');el.id='rxNetState';el.className='rx-net-state';el.innerHTML='<span class="rx-net-dot"></span><span id="rxNetText"></span>';document.querySelector('.main')?.appendChild(el)}
    return el;
  }
  function renderStatus(){
    window.rxFieldMode=weak();
    const el=statusEl(),txt=el?.querySelector('#rxNetText');if(!el||!txt)return;
    if(!navigator.onLine){el.className='rx-net-state show offline';txt.textContent='Sem internet · usando dados já carregados';return}
    if(window.rxFieldMode){el.className='rx-net-state show';txt.textContent='Modo campo · conexão econômica';return}
    el.className='rx-net-state online';txt.textContent='Conexão normal';
  }

  function installMapMemory(){
    let m=null;try{m=(typeof map!=='undefined'&&map&&map.getCenter)?map:null}catch(e){}
    if(!m){setTimeout(installMapMemory,180);return}
    try{
      const raw=localStorage.getItem('rx:last-map:v31');
      if(raw){const s=JSON.parse(raw),age=Date.now()-Number(s.t||0);if(age<1000*60*60*24*30&&Number.isFinite(s.lat)&&Number.isFinite(s.lon)&&Number.isFinite(s.z))m.setView([s.lat,s.lon],Math.max(5,Math.min(18,s.z)),{animate:false})}
    }catch(e){}
    let timer=null;
    const save=()=>{clearTimeout(timer);timer=setTimeout(()=>{try{const c=m.getCenter();localStorage.setItem('rx:last-map:v31',JSON.stringify({lat:c.lat,lon:c.lng,z:m.getZoom(),t:Date.now()}))}catch(e){}},500)};
    m.on('moveend',save);m.on('zoomend',save);
  }

  function registerSW(){
    if(!('serviceWorker' in navigator)||location.protocol!=='https:')return;
    const run=()=>navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});
    if('requestIdleCallback' in window)requestIdleCallback(run,{timeout:2200});else setTimeout(run,1500);
  }

  window.addEventListener('online',renderStatus);
  window.addEventListener('offline',renderStatus);
  if(conn&&conn.addEventListener)conn.addEventListener('change',renderStatus);
  renderStatus();
  installMapMemory();
  registerSW();
})();
</script>
<!-- RX_FIELD_MODE_V43 -->
'''

html = portal_v8.PORTAL_HTML

html = html.replace(
    '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">',
    '<link rel="preconnect" href="https://unpkg.com" crossorigin><link rel="dns-prefetch" href="//unpkg.com"><link rel="preconnect" href="https://tile.openstreetmap.org" crossorigin><link rel="dns-prefetch" href="//tile.openstreetmap.org"><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">',
)

# These two replacements target the current nationwide parcel loader. Names are no
# longer patched here: portal_property_names_v30 reads rxFieldMode directly in V43.
html = html.replace(
    "function scheduleParcels(){rxLastUf=null;clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(false),220)}",
    "function scheduleParcels(){rxLastUf=null;clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(false),window.rxFieldMode?850:260)}",
)
html = html.replace(
    "u.searchParams.set('limit','80');const r=await fetch(u);",
    "u.searchParams.set('limit',window.rxFieldMode?'35':'80');const r=await (window.rxFieldFetch?window.rxFieldFetch(u,window.rxFieldMode?6500:10000):fetch(u));",
)
html = html.replace(
    "const r=await fetch(`/v1/live/cities?q=${encodeURIComponent(q)}`);",
    "const r=await (window.rxFieldFetch?window.rxFieldFetch(`/v1/live/cities?q=${encodeURIComponent(q)}`,6500):fetch(`/v1/live/cities?q=${encodeURIComponent(q)}`));",
)

portal_v8.PORTAL_HTML = html.replace('</body>', FIELD_UI + '</body>')

print('RX_FIELD_MODE_V43=weak_network_swr_snapshot_identity_map_cache', flush=True)

from __future__ import annotations

import json
import os

import httpx
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from shapely.geometry import mapping, shape

import portal_api as base
from critical_minerals import query_critical_minerals
from premium_integrations import status as premium_status
from report_api import _analyze_with_live_addons
from whatsapp_gateway import register_routes as register_whatsapp_routes
from monitoring_routes import register_monitoring_routes
import monitoring_store

app = base.app
APP_PORTAL_VERSION = '0.19.0-v8-map-live'

# Replace portal root only; keep every production report/live/export route already registered.
app.router.routes = [r for r in app.router.routes if getattr(r, 'path', None) != '/']

EXTRA_JS = r'''
<style>
.rx-city-results{position:fixed;z-index:2200;top:61px;left:252px;width:min(650px,calc(100vw - 520px));background:#091912;border:1px solid #244136;border-radius:12px;box-shadow:0 18px 50px #0009;overflow:hidden}
.rx-city-results button{display:block;width:100%;padding:11px 13px;border:0;border-bottom:1px solid #244136;background:#091912;color:#eef8f2;text-align:left;cursor:pointer}.rx-city-results button:hover{background:#10251b}.rx-city-results small{display:block;color:#9bb1a6;margin-top:2px}
.rx-locate{position:absolute;z-index:850;right:14px;top:14px;border:1px solid #244136;background:rgba(7,20,14,.96);color:#eef8f2;border-radius:11px;padding:10px 12px;font-weight:800;cursor:pointer;box-shadow:0 10px 30px #0007}.rx-map-state{position:absolute;z-index:700;left:14px;top:58px;background:rgba(7,20,14,.91);border:1px solid #244136;color:#9bb1a6;border-radius:10px;padding:7px 10px;font-size:10px;max-width:360px}
@media(max-width:720px){.rx-city-results{left:9px;right:9px;top:58px;width:auto}.rx-locate{top:54px}.rx-map-state{top:96px;right:14px;max-width:none}}
</style>
<script>
(function(){
  const originalRender = window.renderAnalysis || renderAnalysis;
  let rxParcelLayer=null, rxLocationMarker=null, rxLastUf=null, rxTimer=null, rxLoading=false;
  const qs=(s)=>document.querySelector(s);
  function rxMap(){try{return (typeof map!=='undefined'&&map&&map.getBounds)?map:null}catch(e){return null}}
  function setMapState(t){let el=qs('#rxMapState');if(!el){el=document.createElement('div');el.id='rxMapState';el.className='rx-map-state';qs('.main')?.appendChild(el)}if(el)el.textContent=t||''}
  function propertyFromFeature(f){const p=f?.properties||{};return {car_code:p.cod_imovel||p.car_code||'',municipality:p.municipio||p.municipality||'',uf:p.uf||rxLastUf||'',area_ha:p.area??p.area_ha,status:p.status_imovel||p.status||'',condition:p.condicao||p.condition||'',type:p.tipo_imovel||p.type||'',fiscal_modules:p.m_fiscal||p.fiscal_modules}}
  function clearCityResults(){qs('#rxCityResults')?.remove()}
  function cityResults(items){clearCityResults();const host=document.createElement('div');host.id='rxCityResults';host.className='rx-city-results';(items||[]).forEach(x=>{const b=document.createElement('button');b.type='button';b.innerHTML=`<b>${x.name||x.display_name||'Município'}</b><small>${x.state||''}${x.uf?' · '+x.uf:''}</small>`;b.onclick=()=>{clearCityResults();rxLastUf=x.uf||null;const m=rxMap();if(m){m.setView([Number(x.lat),Number(x.lon)],13);setTimeout(()=>loadVisibleParcels(true),350)}qs('#q').value=''};host.appendChild(b)});if(host.childNodes.length)document.body.appendChild(host)}
  async function searchCity(q){setMapState('Localizando município…');try{const r=await fetch(`/v1/live/cities?q=${encodeURIComponent(q)}`);const d=await r.json();if(!r.ok)throw new Error(d.detail||'Município não localizado');if(!d.items?.length)throw new Error('Município não localizado');cityResults(d.items);if(d.items.length===1){const x=d.items[0];rxLastUf=x.uf||null;const m=rxMap();if(m){m.setView([Number(x.lat),Number(x.lon)],13);clearCityResults();setTimeout(()=>loadVisibleParcels(true),350)}}setMapState('Escolha o município ou aproxime o mapa para ver os imóveis do CAR.')}catch(e){setMapState(e.message);if(typeof toast==='function')toast(e.message)}}
  async function loadVisibleParcels(force){const m=rxMap();if(!m||rxLoading)return;const z=m.getZoom();if(z<11){if(rxParcelLayer){m.removeLayer(rxParcelLayer);rxParcelLayer=null}setMapState('Aproxime o mapa para visualizar os limites dos imóveis rurais do CAR.');return}const b=m.getBounds();const span=Math.max(b.getEast()-b.getWest(),b.getNorth()-b.getSouth());if(span>1.2&&!force){setMapState('Aproxime um pouco mais para carregar os imóveis rurais.');return}rxLoading=true;setMapState('Carregando imóveis rurais do SICAR na área visível…');try{const u=new URL('/v1/live/sicar/viewport',location.origin);u.searchParams.set('west',b.getWest());u.searchParams.set('south',b.getSouth());u.searchParams.set('east',b.getEast());u.searchParams.set('north',b.getNorth());if(rxLastUf)u.searchParams.set('uf',rxLastUf);u.searchParams.set('limit','80');const r=await fetch(u);const d=await r.json();if(!r.ok)throw new Error(d.detail||'SICAR indisponível');rxLastUf=d.uf||rxLastUf;if(rxParcelLayer)m.removeLayer(rxParcelLayer);rxParcelLayer=L.geoJSON(d,{style:{color:'#48d995',weight:1.4,fillColor:'#48d995',fillOpacity:.075},onEachFeature:(f,l)=>{const p=propertyFromFeature(f);l.bindTooltip(`<b>${p.municipality||'Imóvel rural'}</b><br>${p.area_ha??'—'} ha`,{sticky:true});l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);if(typeof showProperty==='function')showProperty(p,f.geometry)})}}).addTo(m);setMapState(`${d.features?.length||0} imóvel(is) CAR carregado(s) nesta área${d.truncated?' · aproxime para ver mais':''}. Clique em um polígono.`)}catch(e){setMapState(`CAR: ${e.message||'fonte temporariamente indisponível'}`)}finally{rxLoading=false}}
  function scheduleParcels(){clearTimeout(rxTimer);rxTimer=setTimeout(()=>loadVisibleParcels(false),500)}
  function locateUser(){const m=rxMap();if(!m)return;if(!navigator.geolocation){setMapState('Seu navegador não disponibilizou localização. Digite uma cidade na busca.');return}setMapState('Obtendo sua localização…');navigator.geolocation.getCurrentPosition(pos=>{const lat=pos.coords.latitude,lon=pos.coords.longitude;m.setView([lat,lon],14);if(rxLocationMarker)m.removeLayer(rxLocationMarker);rxLocationMarker=L.circleMarker([lat,lon],{radius:7,color:'#fff',weight:2,fillColor:'#2f8cff',fillOpacity:1}).addTo(m).bindTooltip('Sua localização');rxLastUf=null;setTimeout(()=>loadVisibleParcels(true),300)},()=>setMapState('Localização não autorizada. Digite uma cidade ou mova o mapa.'),{enableHighAccuracy:true,timeout:9000,maximumAge:180000})}
  function installMapUX(){const m=rxMap();if(!m){setTimeout(installMapUX,250);return}const input=qs('#q');const go=qs('#go');if(input)input.placeholder='Digite uma cidade, CAR ou clique em uma propriedade';if(go){go.onclick=()=>{const raw=(input?.value||'').trim();if(!raw)return;if(typeof carPattern==='function'&&carPattern(raw)){clearCityResults();loadCar(raw.toUpperCase()).catch(x=>toast(x.message));return}searchCity(raw)}}const main=qs('.main');if(main&&!qs('#rxLocateBtn')){const btn=document.createElement('button');btn.id='rxLocateBtn';btn.className='rx-locate';btn.type='button';btn.textContent='◎ Minha localização';btn.onclick=locateUser;main.appendChild(btn)}m.on('moveend',scheduleParcels);m.on('zoomend',scheduleParcels);setTimeout(locateUser,350)}
  async function criticalSection(){if(!window.current&&!((typeof current!=='undefined')&&current))return;const cur=(typeof current!=='undefined')?current:window.current;if(!cur?.car_code)return;const host=qs('#pbody');if(!host)return;let box=qs('#criticalMineralsSection');if(!box){box=document.createElement('div');box.id='criticalMineralsSection';box.className='section';box.innerHTML='<h4>Terras raras e minerais críticos</h4><div class="row"><span>Consultando ANM + Serviço Geológico do Brasil…</span></div>';host.appendChild(box)}try{const r=await fetch(`/v1/live/critical-minerals/${encodeURIComponent(cur.car_code)}`),d=await r.json();if(!r.ok)throw new Error(d.detail||'consulta indisponível');const a=d.anm||{},s=d.sgb||{},codes=d.mineral_codes||[],rare=d.rare_earth_signal===true;box.innerHTML=`<h4>Terras raras e minerais críticos</h4><div class="row"><b class="${rare?'warn':'ok'}">${rare?'SINAL DE INTERESSE EM TERRAS RARAS':'TRIAGEM DE MINERAIS CRÍTICOS'}</b><br><span>${codes.length?codes.join(', ').replaceAll('_',' '):'Nenhum sinal específico identificado nas fontes consultadas.'}</span></div><div class="row"><b>ANM</b><br><span>${a.critical_process_count||0} processo(s) intersectante(s) classificado(s) como mineral crítico.</span></div><div class="row"><b>SGB / GeoSGB</b><br><span>${(s.hit_layers||[]).length} camada(s) com sinal. Potencial geológico não comprova jazida, recurso ou reserva economicamente explotável.</span></div>`}catch(e){box.innerHTML='<h4>Terras raras e minerais críticos</h4><div class="row"><b class="warn">FONTE PARCIAL</b><br><span>'+e.message+'. Indisponibilidade não é tratada como resultado negativo.</span></div>'}}
  async function premiumSection(){const host=qs('#pbody');if(!host)return;let box=qs('#premiumSection');if(!box){box=document.createElement('div');box.id='premiumSection';box.className='section';host.appendChild(box)}try{const r=await fetch('/v1/integrations/premium/status'),d=await r.json();const rows=(d.integrations||[]).map(x=>`<div class="source"><small>${x.label}</small><b class="${x.ready?'ok':'warn'}">${x.state}</b></div>`).join('');box.innerHTML=`<h4>Integrações premium</h4><div class="row"><span>Arquitetura pronta e custo zero enquanto estiver OFF. Ativação somente quando houver credenciais/clientes.</span></div><div class="sources">${rows}</div>`}catch(e){}}
  async function whatsappSection(){const host=qs('#pbody');if(!host)return;let box=qs('#whatsappSection');if(!box){box=document.createElement('div');box.id='whatsappSection';box.className='section';host.appendChild(box)}try{const r=await fetch('/v1/whatsapp/status'),d=await r.json();box.innerHTML=`<h4>WhatsApp</h4><div class="row"><b class="${d.enabled&&d.configured?'ok':'warn'}">${d.enabled&&d.configured?'ATIVO':'PREPARADO — OFF'}</b><br><span>Gateway oficial Meta Cloud API para consulta por CAR e entrega de relatório.</span></div>`}catch(e){}}
  async function monitoringSection(){const host=qs('#pbody');if(!host)return;let box=qs('#monitoringSection');if(!box){box=document.createElement('div');box.id='monitoringSection';box.className='section';host.appendChild(box)}try{const r=await fetch('/v1/monitoring/status'),d=await r.json();const active=d.persistence==='durable',cur=(typeof current!=='undefined')?current:window.current,code=cur?.car_code||'',action=active&&code?`<div class="row"><button id="rxMonitorBtn" type="button" style="width:100%;padding:12px 14px;border:0;border-radius:10px;background:#0E603B;color:#fff;font-weight:800;cursor:pointer">MONITORAR ESTA PROPRIEDADE</button><small id="rxMonitorMsg" style="display:block;margin-top:8px"></small></div>`:'';box.innerHTML=`<h4>Monitoramento contínuo</h4><div class="row"><b class="${active?'ok':'warn'}">${active?'PERSISTENTE — PRONTO':'AGUARDANDO VÍNCULO DO BANCO'}</b><br><span>Snapshots, detecção de mudanças, alertas e histórico. Agendamento preparado para 15 minutos.</span></div>${action}`;const btn=qs('#rxMonitorBtn');if(btn)btn.onclick=async()=>{const msg=qs('#rxMonitorMsg');btn.disabled=true;btn.textContent='ATIVANDO MONITORAMENTO…';try{const rr=await fetch(`/v1/monitoring/properties/${encodeURIComponent(code)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:'in_app'})}),dd=await rr.json();if(!rr.ok)throw new Error(dd.detail||'falha ao ativar');btn.textContent='MONITORAMENTO ATIVO';if(msg)msg.textContent='Baseline salvo. As próximas varreduras detectarão mudanças reais.'}catch(e){btn.disabled=false;btn.textContent='TENTAR NOVAMENTE';if(msg)msg.textContent=e.message}}}catch(e){}}
  window.renderAnalysis=renderAnalysis=function(d){originalRender(d);criticalSection();premiumSection();whatsappSection();monitoringSection()};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',installMapUX);else installMapUX();
})();
</script>
'''

PORTAL_HTML = base.PORTAL_HTML.replace('</body>', EXTRA_JS + '</body>')


def _postgres_driver_available():
    return monitoring_store.readiness().get('driver')


def _db_binding_names():
    keys=('DATABASE_URL','POSTGRES_URL','RENDER_POSTGRES_URL')
    return [k for k in keys if bool(os.getenv(k))]


def _uf_from_address(address: dict) -> str | None:
    iso=address.get('ISO3166-2-lvl4') or address.get('ISO3166-2-lvl6') or ''
    if isinstance(iso,str) and iso.upper().startswith('BR-'):
        return iso[-2:].upper()
    return base.STATE_TO_UF.get(base._norm(address.get('state')))


print('RX_PERSISTENCE_BINDING=' + ('yes' if _db_binding_names() else 'no'), flush=True)
print('RX_POSTGRES_DRIVER=' + str(_postgres_driver_available() or 'none'), flush=True)


@app.get('/', response_class=HTMLResponse)
def portal_root_v8():
    return HTMLResponse(PORTAL_HTML, headers={'Cache-Control':'no-store, no-cache, must-revalidate','Pragma':'no-cache','X-RaioX-Portal-Version':APP_PORTAL_VERSION})


@app.head('/')
def portal_head_v8():
    return Response(status_code=200, headers={'X-RaioX-Portal-Version':APP_PORTAL_VERSION})


@app.get('/v1/live/cities')
async def live_city_search(q: str):
    text=(q or '').strip()
    if len(text)<2:
        raise HTTPException(status_code=422,detail='Digite pelo menos 2 caracteres do município.')
    params={'q':f'{text}, Brasil','format':'jsonv2','countrycodes':'br','addressdetails':'1','limit':'6','accept-language':'pt-BR'}
    headers={'User-Agent':'Raio-X-Territorial/0.19 (city-search)'}
    try:
        async with httpx.AsyncClient(timeout=18,follow_redirects=True,headers=headers) as client:
            r=await client.get('https://nominatim.openstreetmap.org/search',params=params)
            r.raise_for_status(); data=r.json()
    except Exception as exc:
        raise HTTPException(status_code=502,detail=f'Busca de município indisponível: {type(exc).__name__}')
    items=[];seen=set()
    for x in data:
        a=x.get('address') or {}; name=a.get('city') or a.get('town') or a.get('municipality') or a.get('village') or x.get('name') or x.get('display_name','').split(',')[0]
        uf=_uf_from_address(a); key=(name,uf)
        if not name or key in seen: continue
        seen.add(key); items.append({'name':name,'display_name':x.get('display_name'),'state':a.get('state'),'uf':uf,'lat':float(x['lat']),'lon':float(x['lon']),'boundingbox':x.get('boundingbox')})
    return {'ok':True,'items':items[:6]}


@app.get('/v1/live/sicar/viewport')
async def live_sicar_viewport(west: float, south: float, east: float, north: float, uf: str | None=None, limit: int=80):
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    if max(east-west,north-south)>1.5:
        raise HTTPException(status_code=422,detail='Aproxime o mapa para carregar os imóveis rurais.')
    uf=(uf or '').upper().strip() or await base._reverse_uf((south+north)/2,(west+east)/2)
    if uf not in base.STATE_TO_UF.values():
        raise HTTPException(status_code=422,detail='UF não identificada.')
    type_name=f"sicar:sicar_imoveis_{'DF' if uf=='DF' else uf.lower()}"
    params={'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':type_name,'outputFormat':'application/json','srsName':'EPSG:4674','bbox':f'{west},{south},{east},{north},EPSG:4674','maxFeatures':str(max(1,min(limit,100)))}
    headers={'User-Agent':'Raio-X-Territorial/0.19 (viewport)'}
    raw=bytearray(); cap=6_000_000
    try:
        async with httpx.AsyncClient(timeout=30,follow_redirects=True,headers=headers) as client:
            async with client.stream('GET',base.SICAR,params=params) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>cap:
                        raise HTTPException(status_code=413,detail='Muitos imóveis nesta área. Aproxime o mapa.')
        data=json.loads(raw)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502,detail=f'SICAR indisponível: {type(exc).__name__}')
    out=[]; tolerance=max(0.00001,min(0.00012,max(east-west,north-south)/2500))
    for f in (data.get('features') or [])[:100]:
        try:
            g=shape(f.get('geometry')).simplify(tolerance,preserve_topology=True)
            props=f.get('properties') or {}
            out.append({'type':'Feature','geometry':mapping(g),'properties':{k:props.get(k) for k in ('cod_imovel','area','municipio','uf','status_imovel','condicao','tipo_imovel','m_fiscal')}})
        except Exception:
            continue
    return {'type':'FeatureCollection','features':out,'uf':uf,'source':'SICAR/WFS público','truncated':len(data.get('features') or [])>=max(1,min(limit,100))}


@app.get('/v1/live/critical-minerals/{car_code}')
async def live_critical_minerals(car_code: str):
    result=await _analyze_with_live_addons(car_code.upper())
    car=result.get('car') or {}
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502,detail='CAR não localizado ou fonte indisponível')
    return await query_critical_minerals(car.get('geometry'),result.get('anm'))


@app.get('/v1/integrations/premium/status')
def premium_integrations_status():
    return premium_status()


@app.get('/v1/internal/persistence/status')
def persistence_status():
    present=_db_binding_names()
    return {'durable_database_binding':bool(present),'detected_variable_names':present,'driver_available':_postgres_driver_available(),'policy':'No connection string or credential is returned by this endpoint.'}


@app.get('/v1/portal/v8/status')
def v8_status():
    ready=monitoring_store.readiness()['ready']
    return {'ok':True,'portal_version':APP_PORTAL_VERSION,'live_property_resolution':True,'city_search':True,'device_geolocation':True,'live_sicar_viewport':True,'real_pdf':True,'kml':True,'geojson':True,'critical_minerals':True,'premium_integrations_prepared':True,'whatsapp_gateway_prepared':True,'monitoring_persistence':'durable' if ready else 'database-link-required','monitoring_scheduler':'github-actions-15min'}


register_whatsapp_routes(app, _analyze_with_live_addons)
register_monitoring_routes(app, _analyze_with_live_addons)

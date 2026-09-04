from __future__ import annotations

import asyncio
import html
import json
import os
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse, RedirectResponse
from shapely.geometry import Point, shape

from report_api import app
from deploy_app import SICAR, fetch_car_live

APP_PORTAL_VERSION = '0.18.0-operational-portal'

# report_api already owns the FastAPI app. Replace only its JSON root with the user portal.
app.router.routes = [r for r in app.router.routes if getattr(r, 'path', None) != '/']

STATE_TO_UF = {
    'acre':'AC','alagoas':'AL','amapa':'AP','amazonas':'AM','bahia':'BA','ceara':'CE',
    'distrito federal':'DF','espirito santo':'ES','goias':'GO','maranhao':'MA','mato grosso':'MT',
    'mato grosso do sul':'MS','minas gerais':'MG','para':'PA','paraiba':'PB','parana':'PR',
    'pernambuco':'PE','piaui':'PI','rio de janeiro':'RJ','rio grande do norte':'RN',
    'rio grande do sul':'RS','rondonia':'RO','roraima':'RR','santa catarina':'SC','sao paulo':'SP',
    'sergipe':'SE','tocantins':'TO'
}


def _norm(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c)).lower().strip()


def _coords_iter(g):
    if not isinstance(g, dict):
        return
    coords = g.get('coordinates') or []
    def walk(v):
        if isinstance(v, (list, tuple)) and len(v) >= 2 and isinstance(v[0], (int,float)) and isinstance(v[1], (int,float)):
            yield float(v[0]), float(v[1])
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)
    yield from walk(coords)


def _kml_geometry(geometry: dict) -> str:
    gt = (geometry or {}).get('type')
    c = (geometry or {}).get('coordinates') or []
    def ring(points):
        return ' '.join(f'{float(x):.8f},{float(y):.8f},0' for x,y,*_ in points)
    def polygon(poly):
        if not poly: return ''
        outer = ring(poly[0])
        inners = ''.join(f'<innerBoundaryIs><LinearRing><coordinates>{ring(r)}</coordinates></LinearRing></innerBoundaryIs>' for r in poly[1:])
        return f'<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates></LinearRing></outerBoundaryIs>{inners}</Polygon>'
    if gt == 'Polygon':
        return polygon(c)
    if gt == 'MultiPolygon':
        return '<MultiGeometry>' + ''.join(polygon(p) for p in c) + '</MultiGeometry>'
    return ''


async def _reverse_uf(lat: float, lon: float) -> str:
    url = os.getenv('NOMINATIM_REVERSE_URL', 'https://nominatim.openstreetmap.org/reverse')
    params = {'format':'jsonv2','lat':str(lat),'lon':str(lon),'zoom':'8','addressdetails':'1'}
    headers = {'User-Agent':'Raio-X-Territorial/0.18 (territorial-analysis)'}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            r = await client.get(url, params=params)
            data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Não foi possível identificar a UF do ponto: {type(e).__name__}')
    addr = data.get('address') or {}
    iso = addr.get('ISO3166-2-lvl4') or addr.get('ISO3166-2-lvl6') or ''
    if isinstance(iso, str) and iso.upper().startswith('BR-') and len(iso) >= 5:
        return iso[-2:].upper()
    uf = STATE_TO_UF.get(_norm(addr.get('state')))
    if uf: return uf
    raise HTTPException(status_code=422, detail='UF não identificada para o ponto selecionado.')


async def _sicar_at_point(lat: float, lon: float, uf: str):
    type_name = f"sicar:sicar_imoveis_{'DF' if uf == 'DF' else uf.lower()}"
    eps = 0.00008
    params = {
        'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':type_name,
        'outputFormat':'application/json','srsName':'EPSG:4674',
        'bbox':f'{lon-eps},{lat-eps},{lon+eps},{lat+eps},EPSG:4674','maxFeatures':'50'
    }
    headers = {'User-Agent':'Raio-X-Territorial/0.18'}
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=True, headers=headers) as client:
            r = await client.get(SICAR, params=params)
            data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'SICAR indisponível no momento: {type(e).__name__}')
    features = data.get('features') or []
    p = Point(float(lon), float(lat))
    exact = []
    for f in features:
        try:
            g = shape(f.get('geometry'))
            if g.contains(p) or g.touches(p):
                exact.append(f)
        except Exception:
            continue
    chosen = exact[0] if exact else (features[0] if features else None)
    if not chosen:
        raise HTTPException(status_code=404, detail='Nenhum imóvel do SICAR foi localizado neste ponto.')
    props = chosen.get('properties') or {}
    return {
        'ok': True, 'source':'SICAR/WFS público','uf':uf,
        'property': {
            'car_code': props.get('cod_imovel'), 'municipality': props.get('municipio'), 'uf': props.get('uf') or uf,
            'area_ha': props.get('area'), 'status': props.get('status_imovel'), 'condition': props.get('condicao'),
            'type': props.get('tipo_imovel'), 'fiscal_modules': props.get('m_fiscal')
        },
        'geometry': chosen.get('geometry'), 'candidate_count':len(features), 'exact_count':len(exact)
    }


@app.get('/v1/live/resolve')
async def resolve_point(lat: float, lon: float):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail='Coordenadas inválidas.')
    uf = await _reverse_uf(lat, lon)
    return await _sicar_at_point(lat, lon, uf)


@app.get('/v1/exports/property/{car_code}/geojson')
async def export_geojson(car_code: str):
    car = await asyncio.to_thread(fetch_car_live, car_code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail='CAR não localizado ou SICAR indisponível.')
    feature = {'type':'Feature','geometry':car.get('geometry'),'properties':car.get('properties') or {}}
    fc = {'type':'FeatureCollection','features':[feature]}
    return Response(content=json.dumps(fc, ensure_ascii=False), media_type='application/geo+json', headers={'Content-Disposition':f'attachment; filename="raio_x_{car_code.upper()}.geojson"'})


@app.get('/v1/exports/property/{car_code}/kml')
async def export_kml(car_code: str):
    car = await asyncio.to_thread(fetch_car_live, car_code.upper())
    if not car.get('ok'):
        raise HTTPException(status_code=404 if car.get('not_found') else 502, detail='CAR não localizado ou SICAR indisponível.')
    props = car.get('properties') or {}
    geom = _kml_geometry(car.get('geometry') or {})
    name = html.escape(f"Imóvel rural - {props.get('municipio') or ''}/{props.get('uf') or ''}")
    code = html.escape(str(props.get('cod_imovel') or car_code.upper()))
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Raio-X Territorial</name><Style id="rx"><LineStyle><color>ff3bcf79</color><width>3</width></LineStyle><PolyStyle><color>403bcf79</color></PolyStyle></Style><Placemark><name>{name}</name><description>CAR: {code}</description><styleUrl>#rx</styleUrl>{geom}</Placemark></Document></kml>'''
    return Response(content=xml, media_type='application/vnd.google-earth.kml+xml', headers={'Content-Disposition':f'attachment; filename="raio_x_{car_code.upper()}.kml"'})


PORTAL_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Raio-X Territorial</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{--bg:#06110d;--panel:#0b1b14;--panel2:#10251b;--line:#244136;--text:#eef8f2;--muted:#9bb1a6;--green:#63e6a5;--amber:#ffc866;--red:#ff756f}*{box-sizing:border-box}html,body{margin:0;height:100%;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text)}button,input{font:inherit}.top{height:68px;position:fixed;z-index:1000;left:0;right:0;top:0;background:rgba(6,17,13,.96);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;padding:0 18px}.logo{display:flex;align-items:center;gap:10px;min-width:220px}.mark{width:39px;height:39px;border-radius:12px;background:var(--green);color:#052116;font-weight:950;display:grid;place-items:center}.logo b{font-size:15px}.logo small{display:block;color:var(--muted);font-size:9px;letter-spacing:1.2px;text-transform:uppercase;margin-top:3px}.search{flex:1;max-width:760px;height:43px;background:var(--panel);border:1px solid var(--line);border-radius:13px;display:flex;overflow:hidden}.search input{flex:1;border:0;outline:0;background:transparent;color:var(--text);padding:0 14px}.search button,.btn{border:0;border-radius:11px;background:var(--green);color:#052116;padding:10px 15px;font-weight:900;cursor:pointer}.search button{margin:5px}.status{margin-left:auto;color:var(--green);font-size:10px;font-weight:800}.main{position:fixed;top:68px;bottom:0;left:0;right:0}.map{height:100%;background:#0d1a14}.hint{position:absolute;z-index:700;top:14px;left:14px;background:rgba(7,20,14,.94);border:1px solid var(--line);padding:10px 13px;border-radius:12px;color:var(--muted);font-size:11px;box-shadow:0 12px 35px #0007}.card{position:absolute;z-index:800;left:16px;bottom:16px;width:min(390px,calc(100% - 32px));background:rgba(7,20,14,.97);border:1px solid var(--line);border-radius:19px;padding:18px;box-shadow:0 25px 70px #0009}.hidden{display:none!important}.eyebrow{font-size:9px;color:var(--green);font-weight:900;letter-spacing:1.5px}.card h2{font-size:18px;margin:5px 0}.meta{color:var(--muted);font-size:11px;margin-bottom:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:11px 0 14px}.stat{background:var(--panel2);border:1px solid var(--line);border-radius:11px;padding:9px}.stat small{display:block;color:var(--muted);font-size:8px}.stat b{font-size:10px}.actions{display:grid;grid-template-columns:1fr auto auto;gap:7px}.ghost{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:11px;padding:10px 12px;font-weight:800;cursor:pointer}.panel{position:absolute;z-index:900;top:0;right:0;bottom:0;width:min(610px,52vw);background:#07150f;border-left:1px solid var(--line);overflow:auto;box-shadow:-25px 0 70px #0007}.phead{position:sticky;top:0;background:rgba(7,21,15,.97);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:15px 17px;display:flex;gap:11px;align-items:center;z-index:3}.back{width:34px;height:34px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);cursor:pointer}.pbody{padding:16px}.hero{border:1px solid var(--line);border-radius:16px;background:linear-gradient(145deg,#0d2319,#091711);padding:15px}.hero h3{margin:4px 0;font-size:19px}.hero p{color:var(--muted);font-size:11px;line-height:1.5}.sources{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin-top:13px}.source{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:10px;min-width:0}.source small{display:block;color:var(--muted);font-size:8px}.source b{display:block;font-size:10px;margin-top:4px}.ok{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}.section{margin-top:18px}.section h4{margin:0 0 8px;font-size:13px}.row{border:1px solid var(--line);background:var(--panel);padding:10px;border-radius:11px;margin-bottom:6px;font-size:10px;line-height:1.5}.row span{color:var(--muted)}.downloads{display:grid;grid-template-columns:2fr 1fr 1fr;gap:7px;margin-top:14px}.loader{padding:50px 20px;text-align:center}.spin{width:52px;height:52px;border:3px solid #173a2b;border-top-color:var(--green);border-radius:50%;animation:s 1s linear infinite;margin:0 auto 15px}@keyframes s{to{transform:rotate(360deg)}}.loader p{color:var(--muted);font-size:11px}.toast{position:fixed;z-index:3000;bottom:22px;left:50%;transform:translateX(-50%);background:#f1fff7;color:#062015;padding:10px 14px;border-radius:11px;font-size:11px;font-weight:800;box-shadow:0 15px 45px #0008}
@media(max-width:720px){.top{height:62px;padding:0 9px;gap:7px}.logo{min-width:auto}.logo>div:last-child{display:none}.mark{width:36px;height:36px}.status{display:none}.search{height:40px}.main{top:62px}.panel{width:100%}.sources{grid-template-columns:1fr 1fr}.card{bottom:10px;left:10px;width:calc(100% - 20px)}.hint{left:10px;right:10px;text-align:center}.downloads{grid-template-columns:1fr 1fr}.downloads .btn{grid-column:1/3}}
</style></head><body>
<header class="top"><div class="logo"><div class="mark">RX</div><div><b>Raio-X Territorial</b><small>Inteligência rural</small></div></div><div class="search"><input id="q" placeholder="Cole o código CAR ou clique na propriedade"><button id="go">Buscar</button></div><div class="status">● SISTEMA LIVE</div></header>
<main class="main"><div id="map" class="map"></div><div class="hint">Clique dentro de uma propriedade rural ou cole o código do CAR acima.</div>
<article id="card" class="card hidden"><span class="eyebrow">IMÓVEL REAL • SICAR</span><h2 id="name">Imóvel rural</h2><div id="meta" class="meta"></div><div class="grid3"><div class="stat"><small>ÁREA</small><b id="area">-</b></div><div class="stat"><small>CAR</small><b id="status">-</b></div><div class="stat"><small>CONDIÇÃO</small><b id="condition">-</b></div></div><div class="actions"><button class="btn" id="analyze">FAZER RAIO-X</button><button class="ghost" id="kml">KML</button><button class="ghost" id="geo">GeoJSON</button></div></article>
<section id="panel" class="panel hidden"><div class="phead"><button id="back" class="back">←</button><div><span class="eyebrow">ANÁLISE TERRITORIAL REAL</span><b id="ptitle" style="display:block;margin-top:3px"></b></div></div><div id="pbody" class="pbody"></div></section></main><div id="toast" class="toast hidden"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const $=q=>document.querySelector(q);let current=null,layer=null;const map=L.map('map',{zoomControl:false}).setView([-16.5,-46],5);L.control.zoom({position:'bottomright'}).addTo(map);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
function toast(t){$('#toast').textContent=t;$('#toast').classList.remove('hidden');setTimeout(()=>$('#toast').classList.add('hidden'),2400)}
function fmt(v){let n=Number(String(v??'').replace(',','.'));return Number.isFinite(n)?n.toLocaleString('pt-BR',{maximumFractionDigits:3})+' ha':'-'}
function carPattern(s){return /^[A-Z]{2}-\d{7}-[A-F0-9]{32}$/i.test(s.trim())}
function showProperty(p,g){current={...p,geometry:g};$('#name').textContent=`Imóvel rural • ${p.municipality||'-'}/${p.uf||'-'}`;$('#meta').textContent=p.car_code||'-';$('#area').textContent=fmt(p.area_ha);$('#status').textContent=p.status||'-';$('#condition').textContent=p.condition||'-';$('#card').classList.remove('hidden');if(layer)map.removeLayer(layer);if(g){layer=L.geoJSON(g,{style:{color:'#63e6a5',weight:3,fillColor:'#63e6a5',fillOpacity:.12}}).addTo(map);map.fitBounds(layer.getBounds(),{padding:[35,35]})}}
async function resolvePoint(lat,lon){toast('Localizando imóvel no SICAR…');let r=await fetch(`/v1/live/resolve?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);let d=await r.json();if(!r.ok)throw new Error(d.detail||'Imóvel não localizado');showProperty(d.property,d.geometry)}
async function loadCar(code){toast('Consultando CAR real…');let r=await fetch(`/v1/live/car/${encodeURIComponent(code)}`);let d=await r.json();if(!r.ok)throw new Error((d.detail&&JSON.stringify(d.detail))||'CAR não localizado');let c=d.car||{},p=c.properties||{};showProperty({car_code:p.cod_imovel,municipality:p.municipio,uf:p.uf,area_ha:p.area,status:p.status_imovel,condition:p.condicao,type:p.tipo_imovel,fiscal_modules:p.m_fiscal},c.geometry)}
map.on('click',e=>resolvePoint(e.latlng.lat,e.latlng.lng).catch(x=>toast(x.message)));$('#go').onclick=()=>{let q=$('#q').value.trim().toUpperCase();if(!carPattern(q)){toast('Cole um código CAR válido ou clique no mapa.');return}loadCar(q).catch(x=>toast(x.message))};$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')$('#go').click()});
function sourceCard(name,obj){let ok=obj&&obj.ok===true,unknown=!obj||obj.ok==null;let cls=ok?'ok':unknown?'warn':'bad';let st=ok?'CONSULTADA':unknown?'NÃO CONSULTADA':'INDISPONÍVEL';let count=obj&&(obj.exact?.occurrence_count??obj.occurrence_count??obj.feature_count_bbox??obj.intersection_count);return `<div class="source"><small>${name}</small><b class="${cls}">${st}${count!=null?' • '+count:''}</b></div>`}
function renderAnalysis(d){let a=d.analysis||{},car=a.car||{},ide=a.ide_layers||{},cons=a.territorial_constraints||{};let sourceHtml=[sourceCard('SICAR',car),sourceCard('SIGEF',a.sigef),sourceCard('IBAMA Embargos',a.embargos_ibama),sourceCard('IBAMA Autos',a.autos_ibama),sourceCard('ANM / Mineração',a.anm),sourceCard('INPE PRODES',a.prodes),sourceCard('INPE Fogo',a.fire_live),sourceCard('Outorgas',a.water_mg),sourceCard('Pivôs ANA',a.pivots_ana),sourceCard('NASA Clima',a.climate_nasa)].join('');Object.entries(ide).forEach(([k,v])=>sourceHtml+=sourceCard('IDE '+k,v));let cservices=(cons.services||{});Object.entries(cservices).forEach(([k,v])=>sourceHtml+=sourceCard(v.label||k,v));let findings=[];let add=(title,text,level='')=>findings.push(`<div class="row"><b class="${level}">${title}</b><br><span>${text}</span></div>`);let emb=a.embargos_ibama?.exact;if(emb?.occurrence_count)add('Embargo IBAMA',`${emb.occurrence_count} ocorrência(s) • ${emb.area_unique_ha||0} ha de interseção`,'bad');let anm=a.anm?.exact;if(anm?.occurrence_count)add('Processo minerário',`${anm.occurrence_count} processo(s) • ${anm.area_unique_ha||0} ha de interseção`,'warn');let pro=a.prodes?.exact;if(pro?.occurrence_count)add('PRODES',`${pro.occurrence_count} ocorrência(s) • ${pro.area_unique_ha||0} ha. PRODES não prova, isoladamente, infração.`,'warn');let fire=a.fire_live;if(fire?.inside_count||fire?.near_count)add('Fogo recente',`${fire.inside_count||0} foco(s) dentro • ${fire.near_count||0} próximo(s)`,'bad');if(!findings.length)add('Leitura inicial','Nenhuma ocorrência crítica foi exibida nas fontes que retornaram dados. Fontes indisponíveis continuam como ponto cego; isso não equivale a regularidade.','ok');$('#pbody').innerHTML=`<div class="hero"><span class="eyebrow">CENTRO DE DECISÃO</span><h3>${current?.municipality||''}/${current?.uf||''}</h3><p>Consulta online a fontes oficiais. Resultado territorial de triagem; não substitui matrícula, cadeia dominial, laudo técnico ou parecer jurídico.</p><div class="downloads"><button class="btn" onclick="downloadPDF()">BAIXAR PDF REAL</button><button class="ghost" onclick="downloadKML()">KML</button><button class="ghost" onclick="downloadGeo()">GeoJSON</button></div></div><div class="section"><h4>Fontes consultadas</h4><div class="sources">${sourceHtml}</div></div><div class="section"><h4>Pontos que exigem atenção</h4>${findings.join('')}</div>`}
async function analyze(){if(!current?.car_code)return;$('#panel').classList.remove('hidden');$('#ptitle').textContent=current.car_code;$('#pbody').innerHTML='<div class="loader"><div class="spin"></div><b>Cruzando fontes oficiais…</b><p>CAR, SIGEF, IBAMA, ANM, PRODES, fogo, água, clima, solo, aptidão e restrições territoriais.</p></div>';try{let r=await fetch(`/v1/reports/property/${encodeURIComponent(current.car_code)}/meta`);let d=await r.json();if(!r.ok)throw new Error(d.detail||'Falha na análise');renderAnalysis(d)}catch(e){$('#pbody').innerHTML=`<div class="row"><b class="bad">Falha parcial</b><br><span>${e.message}</span></div>`}}
function go(u){window.open(u,'_blank')}function downloadPDF(){go(`/v1/reports/property/${encodeURIComponent(current.car_code)}`)}function downloadKML(){go(`/v1/exports/property/${encodeURIComponent(current.car_code)}/kml`)}function downloadGeo(){go(`/v1/exports/property/${encodeURIComponent(current.car_code)}/geojson`)}
$('#analyze').onclick=analyze;$('#back').onclick=()=>$('#panel').classList.add('hidden');$('#kml').onclick=downloadKML;$('#geo').onclick=downloadGeo;window.downloadPDF=downloadPDF;window.downloadKML=downloadKML;window.downloadGeo=downloadGeo;
</script></body></html>'''


@app.get('/', response_class=HTMLResponse)
def portal_root():
    return HTMLResponse(PORTAL_HTML, headers={'Cache-Control':'no-store'})


@app.get('/v1/portal/status')
def portal_status():
    return {'ok':True,'portal_version':APP_PORTAL_VERSION,'mode':'live','cost_mode':'zero-cost-public-sources'}

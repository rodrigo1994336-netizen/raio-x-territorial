from __future__ import annotations

import asyncio

from fastapi import HTTPException

import portal_v8
import portal_search_resilient_v43 as resilient
import cafir_name_search_v44 as cafir

app=portal_v8.app


@app.get('/v1/live/search/cafir-name')
async def cafir_name_search(q:str,uf:str='MG',municipality:str|None=None,limit:int=30):
    return await asyncio.to_thread(cafir.search_sync,q,uf,municipality,limit)


@app.get('/v1/live/search/cafir-name/locate')
async def cafir_name_locate(incra_code:str):
    out=await asyncio.to_thread(cafir.locate_incra_sync,incra_code)
    if not out.get('ok') and str(out.get('detail') or '').startswith('invalid'):
        raise HTTPException(status_code=422,detail=out)
    return out


async def advanced_search_v44(
    uf:str,
    municipality:str,
    q:str|None=None,
    min_area_ha:float|None=None,
    max_area_ha:float|None=None,
    limit:int=40,
):
    term=(q or '').strip()
    code=(uf or '').strip().upper()
    if term and code=='MG':
        out=await asyncio.to_thread(cafir.search_sync,term,code,municipality,min(limit,60))
        items=[]
        for x in out.get('items') or []:
            area=x.get('area_ha')
            if min_area_ha is not None and (area is None or float(area)<float(min_area_ha)):continue
            if max_area_ha is not None and (area is None or float(area)>float(max_area_ha)):continue
            items.append(x)
        return {
          **out,'items':items,'count':len(items),'mode':'cafir_name','municipality':municipality,'uf':'MG',
          'note':'Busca invertida direta no CAFIR. O resultado é um registro cadastral real da Receita Federal; nenhum CAR herda o nome sem vínculo validado.'
        }
    return await resilient.advanced_property_search_v43(uf,municipality,q,min_area_ha,max_area_ha,limit)


# V44 must be the final owner of the advanced route.
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None)!='/v1/live/search/advanced']
app.get('/v1/live/search/advanced')(advanced_search_v44)

V39_MAP="function mapOpen(x){close();try{if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(typeof showProperty==='function')showProperty({car_code:x.car_code,name:x.name,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry)}catch(e){if(typeof toast==='function')toast('Não foi possível abrir este imóvel agora.')}}"
V40_SAFE_MAP="async function mapOpen(x){close();try{if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(x.car_code&&x.name_validation_status==='VALIDATED'&&x.panel_name_eligible===true&&typeof showProperty==='function'){showProperty({car_code:x.car_code,name:x.name,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry);return}if(typeof toast==='function')toast('Referência SIGEF localizada. Nenhuma denominação foi aplicada a um CAR sem vínculo validado.')}catch(e){if(typeof toast==='function')toast('Não foi possível abrir esta referência agora.')}}"
NEW_MAP=r'''async function mapOpen(x){close();try{if(x?.type==='cafir'){if(!x.incra_code){if(typeof toast==='function')toast(`${x.name}: registro CAFIR localizado, mas sem código INCRA utilizável para posicionamento.`);return}const r=await fetch(`/v1/live/search/cafir-name/locate?incra_code=${encodeURIComponent(x.incra_code)}`,{cache:'no-store'}),d=await r.json();if(!r.ok||!d.ok||!(d.items||[]).length){if(typeof toast==='function')toast(`${x.name}: registro CAFIR localizado; parcela SIGEF não localizada agora. Nenhum CAR foi presumido.`);return}const feats=(d.items||[]).filter(y=>y.geometry).map(y=>({type:'Feature',geometry:y.geometry,properties:{}}));if(typeof map!=='undefined'&&feats.length){const l=L.geoJSON({type:'FeatureCollection',features:feats},{style:{color:'#ffc866',weight:3,dashArray:'7 5',fillColor:'#ffc866',fillOpacity:.07}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},6500)}if(typeof toast==='function')toast(`${x.name}: CAFIR localizado · ${feats.length} parcela(s) SIGEF exibida(s) · CAR não vinculado automaticamente.`);return}if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(x.car_code&&typeof showProperty==='function'){const validated=x.name_validation_status==='VALIDATED'&&x.panel_name_eligible===true;showProperty({car_code:x.car_code,name:validated?x.name:undefined,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry)}}catch(e){if(typeof toast==='function')toast('Não foi possível abrir este resultado agora.')}}'''
map_replaced=False
for old_map in (V40_SAFE_MAP,V39_MAP):
    if old_map in portal_v8.PORTAL_HTML:
        portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(old_map,NEW_MAP,1)
        map_replaced=True
        break
if not map_replaced:
    raise RuntimeError('v44_inverse_mapOpen_target_not_found')

OLD_CAR_LINE="<small>CAR ${esc(x.car_code||'—')}</small>"
NEW_CAR_LINE="${x.type==='cafir'?`<small>CAFIR · INCRA ${esc(x.incra_code||'não informado')} · NIRF ${esc(x.nirf||'—')}</small>`:`<small>CAR ${esc(x.car_code||'—')}</small>`}"
if OLD_CAR_LINE not in portal_v8.PORTAL_HTML:
    raise RuntimeError('v44_inverse_result_card_target_not_found')
portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(OLD_CAR_LINE,NEW_CAR_LINE,1)

OLD_FLOW="lastItems=d.items||[];if(!selected){status(`${lastItems.length} imóvel(is) localizado(s) em ${d.municipality}/${d.uf}${d.truncated?' · refine os filtros para reduzir a lista':''}.`);render(lastItems);return}"
NEW_FLOW="lastItems=d.items||[];if(d.mode==='cafir_name'){status(`${lastItems.length} registro(s) CAFIR localizado(s) pelo nome em ${d.municipality}/${d.uf}${d.truncated?' · refine o nome para reduzir a lista':''}. O nome vem direto do CAFIR; um CAR só será associado por vínculo validado.`);render(lastItems);return}if(!selected){status(`${lastItems.length} imóvel(is) localizado(s) em ${d.municipality}/${d.uf}${d.truncated?' · refine os filtros para reduzir a lista':''}.`);render(lastItems);return}"
if OLD_FLOW not in portal_v8.PORTAL_HTML:
    raise RuntimeError('v44_inverse_search_flow_target_not_found')
portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(OLD_FLOW,NEW_FLOW,1)

portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace(
    'Fontes: SICAR/WFS para imóveis e MapBiomas Coleção 11 para perfil de uso/cobertura.',
    'Fontes: CAFIR/Receita Federal para busca por denominação; SICAR/WFS para CAR; MapBiomas Coleção 11 para perfil de uso/cobertura.'
)

if 'RX_CAFIR_INVERSE_V44' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>','<!-- RX_CAFIR_INVERSE_V44 --></body>')

print('RX_CAFIR_INVERSE_V44=direct_cafir_name_primary_truth_safe_map_locator',flush=True)

from __future__ import annotations

from shapely.geometry import shape

import portal_v8
import portal_advanced_search_v39 as adv
import portal_sicar_resilient as sicar

app = portal_v8.app


def _area(v):
    try:
        return float(str(v).replace(',', '.'))
    except Exception:
        return None


async def advanced_property_search_v43(
    uf: str,
    municipality: str,
    q: str | None = None,
    min_area_ha: float | None = None,
    max_area_ha: float | None = None,
    limit: int = 40,
):
    uf = (uf or '').upper().strip()
    municipality = (municipality or '').strip()
    term = adv._norm(q)
    if uf not in adv.UFS:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='Selecione uma UF válida.')
    if len(municipality) < 2:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='Informe o município.')
    if min_area_ha is not None and min_area_ha < 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='Área mínima inválida.')
    if max_area_ha is not None and max_area_ha < 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='Área máxima inválida.')
    if min_area_ha is not None and max_area_ha is not None and min_area_ha > max_area_ha:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='A área mínima não pode ser maior que a máxima.')

    cap = max(1, min(int(limit), 60))
    bbox = await adv._city_bbox(municipality, uf)
    # Same resilient SICAR transport already proven by the main map.
    fetched = await sicar._fetch_sicar_bbox(
        bbox['west'], bbox['south'], bbox['east'], bbox['north'], uf, min(50, max(cap, 40))
    )
    features = fetched['data'].get('features') or []
    wanted_city = adv._norm(municipality)
    items = []
    for f in features:
        p = f.get('properties') or {}
        city = p.get('municipio') or ''
        if wanted_city and wanted_city not in adv._norm(city) and adv._norm(city) not in wanted_city:
            continue
        area = _area(p.get('area'))
        if min_area_ha is not None and (area is None or area < min_area_ha):
            continue
        if max_area_ha is not None and (area is None or area > max_area_ha):
            continue
        if term:
            hay = ' | '.join(str(p.get(k) or '') for k in (
                'nome_imovel', 'denominacao', 'cod_imovel', 'municipio', 'uf',
                'status_imovel', 'condicao', 'tipo_imovel'
            ))
            if term not in adv._norm(hay):
                continue
        geom = f.get('geometry')
        center = None
        try:
            c = shape(geom).centroid
            center = {'lat': float(c.y), 'lon': float(c.x)}
        except Exception:
            pass
        code = p.get('cod_imovel')
        if not code:
            continue
        public_name = p.get('nome_imovel') or p.get('denominacao')
        items.append({
            'type': 'car',
            'name': public_name or f"Imóvel rural · {city}/{uf}",
            'public_name': public_name,
            'car_code': code,
            'municipality': city,
            'uf': p.get('uf') or uf,
            'area_ha': area,
            'status': p.get('status_imovel'),
            'condition': p.get('condicao'),
            'property_type': p.get('tipo_imovel'),
            'fiscal_modules': p.get('m_fiscal'),
            'center': center,
            'geometry': geom,
            'source': 'SICAR/WFS público · transporte resiliente',
        })
        if len(items) >= cap:
            break
    return {
        'ok': True,
        'items': items,
        'count': len(items),
        'uf': uf,
        'municipality': bbox.get('name') or municipality,
        'city_center': {'lat': bbox['lat'], 'lon': bbox['lon']},
        'source': 'SICAR/WFS público · transporte resiliente',
        'truncated': len(features) >= fetched['cap'] or len(items) >= cap,
        'note': 'Estado + município já são suficientes para listar imóveis rurais disponíveis.',
    }


# Replace only the fragile advanced search transport. Keep the existing UI/profile route.
app.router.routes = [
    r for r in app.router.routes
    if getattr(r, 'path', None) != '/v1/live/search/advanced'
]
app.get('/v1/live/search/advanced')(advanced_property_search_v43)

AUTO_UI = r'''
<script id="rxSearchAutoV43">
(function(){
  let timer=0,lastKey='';
  function eligible(){
    const uf=document.querySelector('#rxAdvUf');
    const city=document.querySelector('#rxAdvCity');
    return !!(uf&&city&&uf.value&&city.value.trim().length>=2);
  }
  function run(){
    if(!eligible())return;
    const uf=document.querySelector('#rxAdvUf'),city=document.querySelector('#rxAdvCity');
    const key=uf.value+'|'+city.value.trim().toLowerCase();
    if(key===lastKey)return;
    lastKey=key;
    document.querySelector('#rxAdvSearch')?.click();
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(run,550)}
  document.addEventListener('change',e=>{if(e.target?.id==='rxAdvUf'){lastKey='';schedule()}});
  document.addEventListener('input',e=>{if(e.target?.id==='rxAdvCity'){lastKey='';schedule()}});
  document.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.target?.id==='rxAdvCity'||e.target?.id==='rxAdvUf')){clearTimeout(timer);lastKey='';run()}});
})();
</script>
<!-- RX_SEARCH_AUTO_V43_7 -->
'''
if 'RX_SEARCH_AUTO_V43_7' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace('</body>', AUTO_UI + '</body>')

portal_v8.APP_PORTAL_VERSION = '0.43.7-v43-snapshot-first'
print('RX_SEARCH_V43=resilient_city_auto_list', flush=True)
print('RX_SEARCH_CITY_TRIGGER_V43=uf_plus_municipality_auto', flush=True)

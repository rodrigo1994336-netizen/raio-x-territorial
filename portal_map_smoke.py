from __future__ import annotations

import asyncio

import portal_v8


async def _run_map_smoke() -> None:
    await asyncio.sleep(1.0)
    try:
        city = await portal_v8.live_city_search('Curvelo')
        items = city.get('items') or []
        mg = next((x for x in items if x.get('uf') == 'MG'), items[0] if items else None)
        if not mg:
            raise RuntimeError('city_search_returned_no_items')
        print(
            'RX_MAP_CITY_OK=' + str({
                'name': mg.get('name'),
                'uf': mg.get('uf'),
                'lat': mg.get('lat'),
                'lon': mg.get('lon'),
            }),
            flush=True,
        )
    except Exception as exc:
        print(f'RX_MAP_CITY_FAIL={type(exc).__name__}:{str(exc)[:300]}', flush=True)

    try:
        # Known real SICAR area in Curvelo/MG previously validated by the report smoke.
        fc = await portal_v8.live_sicar_viewport(
            west=-44.1900,
            south=-18.9000,
            east=-44.1730,
            north=-18.8820,
            uf='MG',
            limit=20,
        )
        features = fc.get('features') or []
        if not features:
            raise RuntimeError('sicar_viewport_returned_no_features')
        first = (features[0].get('properties') or {})
        print(
            'RX_MAP_SICAR_OK=' + str({
                'count': len(features),
                'uf': fc.get('uf'),
                'first_car': first.get('cod_imovel'),
                'municipio': first.get('municipio'),
            }),
            flush=True,
        )
        print('RX_MAP_LIVE_OK', flush=True)
    except Exception as exc:
        print(f'RX_MAP_SICAR_FAIL={type(exc).__name__}:{str(exc)[:300]}', flush=True)


@portal_v8.app.on_event('startup')
async def _start_map_smoke() -> None:
    asyncio.create_task(_run_map_smoke())

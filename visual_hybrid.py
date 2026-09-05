from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from highres_reference import build_highres_reference_image
from sentinel_cog import build_sentinel_cog_property_image
from property_visual_plate_v25 import build_property_visual_plate


async def build_hybrid_property_imagery(car_geometry:dict[str,Any],out_path:str|Path,property_meta:dict[str,Any]|None=None):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    high_path=out_path.with_name('property_highres_reference.jpg')
    sentinel_path=out_path.with_name('property_sentinel2_10m.jpg')
    plate_path=out_path.with_name('property_visual_plate_v28.jpg')
    high,sentinel=await asyncio.gather(
        build_highres_reference_image(car_geometry,high_path),
        build_sentinel_cog_property_image(car_geometry,sentinel_path),
        return_exceptions=True
    )
    if isinstance(high,Exception):high={'ok':False,'source':'Esri World Imagery','detail':f'{type(high).__name__}:{str(high)[:220]}'}
    if isinstance(sentinel,Exception):sentinel={'ok':False,'source':'Sentinel-2','detail':f'{type(sentinel).__name__}:{str(sentinel)[:220]}'}

    try:
        plate=build_property_visual_plate(
            plate_path,
            car_geometry,
            high.get('path') if high.get('ok') else None,
            sentinel.get('path') if sentinel.get('ok') else None,
            sentinel.get('ndvi_image_path') if sentinel.get('ok') else None,
            sentinel,
            property_meta or {},
        )
    except Exception as e:
        plate={'ok':False,'detail':f'{type(e).__name__}:{str(e)[:220]}'}

    fallback=high.get('path') if high.get('ok') else (sentinel.get('path') if sentinel.get('ok') else None)
    primary=plate.get('path') if plate.get('ok') else fallback
    meta={
        'ok':bool(primary),'path':primary,
        'visual_plate':plate,
        'visual_plate_path':plate.get('path') if plate.get('ok') else None,
        'visual_reference':high,
        'sentinel':sentinel,
        'visual_reference_path':high.get('path') if high.get('ok') else None,
        'sentinel_image_path':sentinel.get('path') if sentinel.get('ok') else None,
        'ndvi_image_path':sentinel.get('ndvi_image_path') if sentinel.get('ok') else None,
        'source':'Prancha V28: Esri World Imagery + Copernicus Sentinel-2 + NDVI' if plate.get('ok') else ('Esri World Imagery + Copernicus Sentinel-2' if high.get('ok') and sentinel.get('ok') else (high.get('source') if high.get('ok') else sentinel.get('source'))),
        'note':'A capa identifica a fazenda e compara o mesmo CAR em alta resolução, Sentinel-2 datado e NDVI. Cada lente tem função própria e origem explicitada.'
    }
    for k in ('scene_id','date','cloud_cover_pct','resolution_m','ndvi_mean','ndvi_median','ndvi_p10','ndvi_p90','ndvi_low_share_pct','ndvi_medium_share_pct','ndvi_high_share_pct','ndvi_pixel_count'):
        if k in sentinel:meta[k]=sentinel.get(k)
    if not primary:meta['detail']=f"highres={high.get('detail')}; sentinel={sentinel.get('detail')}; plate={plate.get('detail')}"
    return meta


print('RX_VISUAL_HYBRID_V28=property_identity_highres_sentinel_ndvi',flush=True)

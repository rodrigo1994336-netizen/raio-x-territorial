from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from highres_reference import build_highres_reference_image
from sentinel_cog import build_sentinel_cog_property_image


async def build_hybrid_property_imagery(car_geometry:dict[str,Any],out_path:str|Path):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    high_path=out_path.with_name('property_highres_reference.jpg')
    sentinel_path=out_path.with_name('property_sentinel2_10m.jpg')
    high,sentinel=await asyncio.gather(
        build_highres_reference_image(car_geometry,high_path),
        build_sentinel_cog_property_image(car_geometry,sentinel_path),
        return_exceptions=True
    )
    if isinstance(high,Exception):high={'ok':False,'source':'Esri World Imagery','detail':f'{type(high).__name__}:{str(high)[:220]}'}
    if isinstance(sentinel,Exception):sentinel={'ok':False,'source':'Sentinel-2','detail':f'{type(sentinel).__name__}:{str(sentinel)[:220]}'}
    primary=high.get('path') if high.get('ok') else (sentinel.get('path') if sentinel.get('ok') else None)
    meta={
        'ok':bool(primary),'path':primary,
        'visual_reference':high,
        'sentinel':sentinel,
        'visual_reference_path':high.get('path') if high.get('ok') else None,
        'sentinel_image_path':sentinel.get('path') if sentinel.get('ok') else None,
        'ndvi_image_path':sentinel.get('ndvi_image_path') if sentinel.get('ok') else None,
        'source':'Esri World Imagery + Copernicus Sentinel-2' if high.get('ok') and sentinel.get('ok') else (high.get('source') if high.get('ok') else sentinel.get('source')),
        'note':'A imagem de alta resolução é referência visual. A cena Sentinel-2 datada é a evidência científica usada para NDVI e métricas espectrais.'
    }
    # Promote scientific Sentinel metadata without changing the visual-reference identity.
    for k in ('scene_id','date','cloud_cover_pct','resolution_m','ndvi_mean','ndvi_median','ndvi_p10','ndvi_p90','ndvi_low_share_pct','ndvi_medium_share_pct','ndvi_high_share_pct','ndvi_pixel_count'):
        if k in sentinel:meta[k]=sentinel.get(k)
    if not primary:
        meta['detail']=f"highres={high.get('detail')}; sentinel={sentinel.get('detail')}"
    return meta


print('RX_VISUAL_HYBRID=highres_reference_plus_sentinel_science',flush=True)

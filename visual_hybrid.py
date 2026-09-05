from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any

from highres_reference import build_highres_reference_image
from sentinel_cog import build_sentinel_cog_property_image
from property_visual_plate_v25 import build_property_visual_plate


async def _timed(label:str,coro):
    t0=time.monotonic()
    try:
        value=await coro
        print(f'RX_VISUAL_STAGE={label}:{round((time.monotonic()-t0)*1000)}ms:ok={bool((value or {}).get("ok"))}',flush=True)
        return value
    except Exception as e:
        print(f'RX_VISUAL_STAGE={label}:{round((time.monotonic()-t0)*1000)}ms:error={type(e).__name__}',flush=True)
        return {'ok':False,'source':label,'detail':f'{type(e).__name__}:{str(e)[:220]}'}


async def build_hybrid_property_imagery(car_geometry:dict[str,Any],out_path:str|Path,property_meta:dict[str,Any]|None=None):
    total=time.monotonic();out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    high_path=out_path.with_name('property_highres_reference.jpg')
    sentinel_path=out_path.with_name('property_sentinel2_10m.jpg')
    plate_path=out_path.with_name('property_visual_plate_v28.jpg')
    high,sentinel=await asyncio.gather(
        _timed('highres_reference',build_highres_reference_image(car_geometry,high_path)),
        _timed('sentinel_cog_ndvi',build_sentinel_cog_property_image(car_geometry,sentinel_path)),
    )

    t0=time.monotonic()
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
    print(f'RX_VISUAL_STAGE=visual_plate:{round((time.monotonic()-t0)*1000)}ms:ok={bool(plate.get("ok"))}',flush=True)

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
    print(f'RX_VISUAL_TOTAL={round((time.monotonic()-total)*1000)}ms:ok={bool(meta.get("ok"))}',flush=True)
    return meta


print('RX_VISUAL_HYBRID_V30=timed_property_identity_highres_sentinel_ndvi',flush=True)

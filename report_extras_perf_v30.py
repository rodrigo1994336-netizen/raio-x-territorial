from __future__ import annotations

import asyncio
from pathlib import Path
import time

import live_report_adapter_v13 as v13
import live_report_adapter_v17 as v17
import report_visual_identity_v28 as identity_v28
from visual_hybrid import build_hybrid_property_imagery
from sicar_detail_sources_v2 import query_sicar_details_v2


async def _timed(label:str,coro):
    t0=time.monotonic()
    try:
        value=await coro
        ok=isinstance(value,dict) and bool(value.get('ok'))
        print(f'RX_EXTRA_STAGE={label}:{round((time.monotonic()-t0)*1000)}ms:ok={ok}',flush=True)
        return value
    except Exception as e:
        print(f'RX_EXTRA_STAGE={label}:{round((time.monotonic()-t0)*1000)}ms:error={type(e).__name__}',flush=True)
        return {'ok':False,'source':label,'detail':f'{type(e).__name__}:{str(e)[:220]}'}


async def _extras_v30(result:dict,car_code:str,out_dir:Path):
    car=result.get('car') or {};geom=car.get('geometry');bbox=car.get('bbox');props=car.get('properties') or {}
    identity={
        'name':identity_v28._name(result,props),
        'car_code':props.get('cod_imovel') or car_code,
        'area_ha':props.get('area'),
        'municipality':props.get('municipio'),
        'uf':props.get('uf'),
    }
    return await asyncio.gather(
        _timed('visual_hybrid',build_hybrid_property_imagery(geom,out_dir/'property_visual.jpg',identity)),
        _timed('groundwater',v13.query_groundwater(geom,20.0)),
        _timed('safras',v13.query_safras(car_code)),
        _timed('sicar_internal',asyncio.to_thread(query_sicar_details_v2,geom,bbox,10,car_code)),
        _timed('aerodromes',asyncio.to_thread(v13.query_aerodromes_anac,geom,50.0,12)),
        _timed('soilgrids',asyncio.to_thread(v13.query_soilgrids_wcs,geom)),
        _timed('climatology',asyncio.to_thread(v13.query_climatology_nasa,geom)),
        _timed('sif_chain',v13.query_sif_establishments(props.get('municipio'),props.get('uf'),30)),
    )


v13._extras=_extras_v30
v17._extras_v17=_extras_v30

_orig_terrain=v17.query_terrain_srtm
def _timed_terrain(geom):
    t0=time.monotonic()
    try:
        value=_orig_terrain(geom)
        print(f'RX_EXTRA_STAGE=terrain_srtm:{round((time.monotonic()-t0)*1000)}ms:ok={bool((value or {}).get("ok"))}',flush=True)
        return value
    except Exception as e:
        print(f'RX_EXTRA_STAGE=terrain_srtm:{round((time.monotonic()-t0)*1000)}ms:error={type(e).__name__}',flush=True)
        raise
v17.query_terrain_srtm=_timed_terrain

print('RX_REPORT_EXTRAS_V30=per_source_timing_enabled',flush=True)

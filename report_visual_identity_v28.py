from __future__ import annotations

import asyncio
from pathlib import Path

import live_report_adapter_v13 as v13
import live_report_adapter_v17 as v17
from visual_hybrid import build_hybrid_property_imagery
from sicar_detail_sources_v2 import query_sicar_details_v2


def _name(result:dict,props:dict)->str:
    # Same rule as v13._best_property_name: only a SICAR denomination is a name.
    return v13._best_property_name(result)[0]


async def _extras_v28(result:dict,car_code:str,out_dir:Path):
    car=result.get('car') or {};geom=car.get('geometry');bbox=car.get('bbox');props=car.get('properties') or {}
    identity={
        'name':_name(result,props),
        'car_code':props.get('cod_imovel') or car_code,
        'area_ha':props.get('area'),
        'municipality':props.get('municipio'),
        'uf':props.get('uf'),
    }
    return await asyncio.gather(
        build_hybrid_property_imagery(geom,out_dir/'property_visual.jpg',identity),
        v13.query_groundwater(geom,20.0),
        v13.query_safras(car_code),
        asyncio.to_thread(query_sicar_details_v2,geom,bbox,10,car_code),
        asyncio.to_thread(v13.query_aerodromes_anac,geom,50.0,12),
        asyncio.to_thread(v13.query_soil_texture,geom),
        asyncio.to_thread(v13.query_climatology_nasa,geom),
        v13.query_sif_establishments(props.get('municipio'),props.get('uf'),30),
        return_exceptions=True
    )


# V13 resolves the extras function dynamically when the report is generated.
v13._extras=_extras_v28
v17._extras_v17=_extras_v28

print('RX_REPORT_VISUAL_IDENTITY_V28=farm_name_area_municipality_car',flush=True)

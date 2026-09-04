from __future__ import annotations

import live_report_adapter_v13 as v13
from sentinel_cog import build_sentinel_cog_property_image


# V13's extras pipeline resolves this global at call time. Replace the thumbnail
# renderer with direct 10 m COG processing without duplicating the report pipeline.
v13.build_satellite_property_image=build_sentinel_cog_property_image

_orig_satellite_patch=v13.v12._patch_satellite


def _patch_satellite_v14(payload:dict,meta:dict,technical_map:str):
    payload=_orig_satellite_patch(payload,meta,technical_map)
    if meta.get('ok') and meta.get('ndvi_mean') is not None:
        agro=payload.setdefault('agropecuaria',{})
        agro['satellite_ndvi']={k:meta.get(k) for k in ('date','resolution_m','ndvi_mean','ndvi_median','ndvi_p10','ndvi_p90','ndvi_low_share_pct','ndvi_medium_share_pct','ndvi_high_share_pct','ndvi_pixel_count','ndvi_image_path')}
        checks=agro.setdefault('property_screening',{}).setdefault('checks',[])
        checks.append({'factor':'NDVI Sentinel-2','scope':'pixels dentro do CAR','status':'consultada','value':{'data':meta.get('date'),'resolução_m':meta.get('resolution_m'),'média':meta.get('ndvi_mean'),'mediana':meta.get('ndvi_median'),'baixo_pct':meta.get('ndvi_low_share_pct'),'médio_pct':meta.get('ndvi_medium_share_pct'),'alto_pct':meta.get('ndvi_high_share_pct')}})
        payload.setdefault('sources',[]).append({'name':'Copernicus Sentinel-2 — NDVI real dentro do CAR','description':f"NDVI calculado diretamente das bandas NIR e vermelho da cena {meta.get('scene_id') or 'Sentinel-2'} ({meta.get('date') or 'data não informada'}), resolução {meta.get('resolution_m') or 10} m. Média {meta.get('ndvi_mean')}; mediana {meta.get('ndvi_median')}. Não equivale à classificação MapBiomas de pastagem.",'status':'CONSULTADA','level':'ok'})
        payload.setdefault('interpretation_rules',[]).append('NDVI Sentinel-2 é um indicador espectral de vigor/cobertura verde no momento da cena. Não identifica sozinho cultura, produtividade, qualidade de pastagem ou capacidade de suporte.')
    return payload


v13.v12._patch_satellite=_patch_satellite_v14
generate_live_report=v13.generate_live_report

print('RX_LIVE_REPORT_ADAPTER=V14_SENTINEL_COG_RGB_NDVI',flush=True)

from __future__ import annotations

import live_report_adapter_v14  # installs Sentinel COG/NDVI hooks
import live_report_adapter_v13 as v13
from mapbiomas_coverage import query_mapbiomas_coverage


_orig_repair=v13._repair_agro_keys


def _repair_v15(payload:dict,result:dict):
    payload=_orig_repair(payload,result)
    agro=payload.setdefault('agropecuaria',{});screen=agro.setdefault('property_screening',{});checks=screen.setdefault('checks',[])
    sat=payload.get('satellite_imagery') or {}
    if sat.get('ok') and sat.get('ndvi_mean') is not None:
        agro['satellite_ndvi']={k:sat.get(k) for k in ('date','resolution_m','ndvi_mean','ndvi_median','ndvi_p10','ndvi_p90','ndvi_low_share_pct','ndvi_medium_share_pct','ndvi_high_share_pct','ndvi_pixel_count','ndvi_image_path')}
        checks.append({'factor':'Vigor espectral atual — NDVI Sentinel-2','scope':'pixels dentro do CAR','status':'consultada','value':{'data':sat.get('date'),'resolução_m':sat.get('resolution_m'),'média':sat.get('ndvi_mean'),'mediana':sat.get('ndvi_median'),'baixo_pct':sat.get('ndvi_low_share_pct'),'médio_pct':sat.get('ndvi_medium_share_pct'),'alto_pct':sat.get('ndvi_high_share_pct')}})
        payload.setdefault('sources',[]).append({'name':'Copernicus Sentinel-2 — NDVI real dentro do CAR','description':f"NDVI calculado diretamente das bandas NIR e vermelho da cena {sat.get('scene_id') or 'Sentinel-2'} ({sat.get('date') or 'data não informada'}), resolução {sat.get('resolution_m') or 10} m. Média {sat.get('ndvi_mean')}; mediana {sat.get('ndvi_median')}.",'status':'CONSULTADA','level':'ok'})
    mb=result.get('mapbiomas_coverage') or {}
    # Remove the old placeholder so a real MapBiomas result cannot coexist with an OFF row.
    payload['sources']=[x for x in payload.get('sources') or [] if str(x.get('name') or '').strip().lower()!='mapbiomas — pastagem / vigor']
    if mb.get('ok'):
        pasture={
            'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11',
            'state':'ready','year':mb.get('year'),'pasture_area_ha':mb.get('pasture_area_ha'),'pasture_share_pct':mb.get('pasture_share_pct'),
            'native_vegetation_share_pct':mb.get('native_vegetation_share_pct'),'agriculture_and_pasture_share_pct':mb.get('agriculture_and_pasture_share_pct'),'water_share_pct':mb.get('water_share_pct'),
            'ndvi_current':agro.get('satellite_ndvi'),
            'note':'Área de pastagem vem do MapBiomas Coleção 11 recortado ao CAR. O vigor espectral atual vem do NDVI Sentinel-2. A classificação histórica oficial de Condição de Vigor MapBiomas é mantida como integração GEE separada.'
        }
        agro['pasture']=pasture
        checks.append({'factor':'Pastagem MapBiomas','scope':'pixels MapBiomas dentro do CAR','status':'consultada','value':{'ano':mb.get('year'),'área_ha':mb.get('pasture_area_ha'),'percentual':mb.get('pasture_share_pct')}})
        prod=payload.setdefault('productive',{})
        mbrows=[]
        for x in (mb.get('classes') or [])[:15]:mbrows.append([f"MapBiomas — {x.get('class_name')}",f"{x.get('area_ha')} ha • {x.get('share_pct')}% do imóvel"])
        if mbrows:prod['landcover_rows']=mbrows+(prod.get('landcover_rows') or [])
        payload.setdefault('sources',[]).append({'name':'MapBiomas Brasil — Cobertura/Pastagem Coleção 11','description':f"GeoTIFF público 2025 recortado ao CAR: pastagem {mb.get('pasture_area_ha')} ha ({mb.get('pasture_share_pct')}%); vegetação nativa {mb.get('native_vegetation_share_pct')}%; agropecuária {mb.get('agriculture_and_pasture_share_pct')}%.",'status':'CONSULTADA','level':'ok'})
        payload.setdefault('sources',[]).append({'name':'MapBiomas — Condição de Vigor da Pastagem','description':'O produto oficial histórico (baixo/médio/alto, 2000–2025) é público, mas o acesso automatizado oficial exposto pelo MapBiomas é via asset Google Earth Engine. O Raio-X já calcula NDVI Sentinel-2 real para vigor atual; o conector GEE será ativado quando houver credencial de serviço autorizada.','status':'INTEGRAÇÃO PREPARADA — CREDENCIAL GEE','level':'neutral'})
    else:
        agro['pasture']={'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11','state':'unavailable','detail':mb.get('detail'),'ndvi_current':agro.get('satellite_ndvi'),'note':'Falha de leitura do GeoTIFF público nesta emissão; não interpretada como ausência de pastagem.'}
        payload.setdefault('sources',[]).append({'name':'MapBiomas Brasil — Cobertura/Pastagem Coleção 11','description':f"GeoTIFF público não respondeu nesta emissão: {mb.get('detail') or 'falha externa'}.",'status':'INDISPONÍVEL','level':'attention'})
    return payload


v13._repair_agro_keys=_repair_v15


def generate_live_report(result:dict,car_code:str):
    working=dict(result)
    try:working['mapbiomas_coverage']=query_mapbiomas_coverage((result.get('car') or {}).get('geometry'),2025)
    except Exception as e:working['mapbiomas_coverage']={'ok':False,'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11','detail':f'{type(e).__name__}:{str(e)[:220]}'}
    return v13.generate_live_report(working,car_code)


print('RX_LIVE_REPORT_ADAPTER=V15_MAPBIOMAS_PASTURE_SENTINEL_NDVI',flush=True)

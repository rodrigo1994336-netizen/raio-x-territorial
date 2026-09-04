from __future__ import annotations

import asyncio
from pathlib import Path

import live_report_adapter_v16 as v16
import live_report_adapter_v13 as v13
from visual_hybrid import build_hybrid_property_imagery
from sicar_detail_sources_v2 import query_sicar_details_v2
from terrain_srtm import query_terrain_srtm
from report_engine_v7 import build_premium_property_report_v7


# --- extras pipeline -------------------------------------------------------
async def _extras_v17(result:dict,car_code:str,out_dir:Path):
    car=result.get('car') or {};geom=car.get('geometry');bbox=car.get('bbox');props=car.get('properties') or {}
    return await asyncio.gather(
        build_hybrid_property_imagery(geom,out_dir/'property_visual.jpg'),
        v13.query_groundwater(geom,20.0),
        v13.query_safras(car_code),
        asyncio.to_thread(query_sicar_details_v2,geom,bbox,10,car_code),
        asyncio.to_thread(v13.query_aerodromes_anac,geom,50.0,12),
        asyncio.to_thread(v13.query_soilgrids_wcs,geom),
        asyncio.to_thread(v13.query_climatology_nasa,geom),
        v13.query_sif_establishments(props.get('municipio'),props.get('uf'),30),
        return_exceptions=True
    )

v13._extras=_extras_v17


# --- image truth ----------------------------------------------------------
def _patch_satellite_v17(payload:dict,meta:dict,technical_map:str):
    payload['technical_map_image_path']=technical_map
    payload['satellite_imagery']=meta
    high=meta.get('visual_reference') or {};sent=meta.get('sentinel') or {}
    primary=meta.get('path')
    if primary:
        payload['satellite_image_path']=primary
        payload['map_image_path']=primary
        payload['car_map_image_path']=primary
    payload['visual_reference_image_path']=high.get('path') if high.get('ok') else None
    payload['sentinel_image_path']=sent.get('path') if sent.get('ok') else None
    payload['ndvi_image_path']=sent.get('ndvi_image_path') if sent.get('ok') else None

    # Remove any older mixed visual/scientific source labels.
    payload['sources']=[s for s in payload.get('sources') or [] if not (
        'copernicus sentinel-2 — imagem orbital real' in str(s.get('name') or '').lower()
        or 'esri world imagery' in str(s.get('name') or '').lower()
    )]
    if high.get('ok'):
        payload.setdefault('sources',[]).append({
            'name':'Esri World Imagery — referência visual de alta resolução',
            'description':'Mosaico visual de alta resolução com o limite do CAR sobreposto. Pode combinar imagens de satélite e aerofotogrametria de diferentes provedores e datas; usado para reconhecimento visual, não para análise temporal.',
            'status':'CONSULTADA','level':'ok'
        })
    else:
        payload.setdefault('sources',[]).append({'name':'Esri World Imagery — referência visual de alta resolução','description':f"Não respondeu nesta emissão: {high.get('detail') or 'fonte indisponível'}.",'status':'INDISPONÍVEL','level':'attention'})
    if sent.get('ok'):
        payload.setdefault('sources',[]).append({
            'name':'Copernicus Sentinel-2 — cena técnica datada / NDVI',
            'description':f"Cena {sent.get('scene_id') or 'Sentinel-2'} de {sent.get('date') or 'data não informada'}, resolução {sent.get('resolution_m') or 10} m, nuvens {sent.get('cloud_cover_pct') if sent.get('cloud_cover_pct') is not None else '—'}%. RGB/NIR usados para o NDVI dentro do CAR.",
            'status':'CONSULTADA','level':'ok'
        })
    else:
        payload.setdefault('sources',[]).append({'name':'Copernicus Sentinel-2 — cena técnica datada / NDVI','description':f"Não respondeu nesta emissão: {sent.get('detail') or 'fonte indisponível'}.",'status':'INDISPONÍVEL','level':'attention'})
    return payload

v13.v12._patch_satellite=_patch_satellite_v17


# High-res builder already carries a truthful title/attribution. Do not stamp it
# again as "orbital Sentinel" in the legacy V13 finalizer.
_orig_stamp=v13._stamp_image
def _stamp_v17(path,name,kind):
    if kind=='satellite' and path and 'highres_reference' in str(path):return
    return _orig_stamp(path,name,kind)
v13._stamp_image=_stamp_v17


# --- CAR internal composition truth --------------------------------------
def _patch_car_details_v17(payload:dict,details:dict):
    car=payload.setdefault('car',{});env=payload.setdefault('environment',{})
    labels=('APP','Reserva Legal','Área consolidada','Vegetação nativa/remanescente','Uso restrito','Nascentes','Hidrografia')
    rows=[];consulted=[]
    for label in labels:
        x=(details.get('summary') or {}).get(label) or {}
        if not x.get('consulted'):continue
        area=float(x.get('area_unique_ha') or 0);n=int(x.get('occurrence_count') or 0)
        rows.append([label,f'{area:.4f} ha • {n} interseção(ões)'])
        consulted.append(label)
        env.setdefault('layer_rows',[]).append([f'CAR — {label}',f'{area:.4f} ha • {n} ocorrência(s)','SICAR/WFS validado'])
    if rows:
        fields=[x for x in list(car.get('fields') or []) if not str(x[0]).startswith('Composição CAR —')]
        fields += [[f'Composição CAR — {a}',b] for a,b in rows];car['fields']=fields
    successful=int(details.get('successful_layers') or 0);requested=int(details.get('requested_layers') or 0)
    status='CONSULTADA' if details.get('ok') and not details.get('partial') else ('PARCIAL' if details.get('ok') else 'INDISPONÍVEL')
    missing_area=' Área consolidada não foi inferida: só entra quando o WFS ao vivo expõe camada identificável.' if 'Área consolidada' not in consulted else ''
    payload.setdefault('sources',[]).append({
        'name':'SICAR — composição ambiental interna',
        'description':f"Camadas validadas explícitas no WFS raiz: {successful}/{requested} responderam. Categorias consultadas: {', '.join(consulted) or 'nenhuma'}.{missing_area}",
        'status':status,'level':'ok' if status=='CONSULTADA' else 'attention'
    })
    return payload

v13._patch_car_details=_patch_car_details_v17


# --- terrain --------------------------------------------------------------
_prev_repair=v13._repair_agro_keys
def _repair_with_terrain(payload:dict,result:dict):
    payload=_prev_repair(payload,result)
    t=result.get('terrain_srtm') or {}
    if not t.get('ok'):return payload
    prod=payload.setdefault('productive',{})
    apt=next((x for x in prod.get('terrain_kpis') or [] if str(x.get('label') or '').lower()=='aptidão'),None)
    under8=0.0
    for r in t.get('slope_classes') or []:
        if r.get('class') in {'0–3°','3–8°'}:under8+=float(r.get('share_pct') or 0)
    kpis=[
        {'label':'Altitude','value':f"{t.get('elevation_median_m')} m",'note':f"faixa {t.get('elevation_min_m')}–{t.get('elevation_max_m')} m • SRTM ~30 m",'status':'CONSULTADA','level':'ok'},
        {'label':'Declividade','value':f"{t.get('slope_median_deg')}° mediana",'note':f"média {t.get('slope_mean_deg')}° • P90 {t.get('slope_p90_deg')}°",'status':'CONSULTADA','level':'ok'},
        {'label':'Declive ≤ 8°','value':f"{round(under8,1)}%",'note':'participação raster do CAR; indicador topográfico, não laudo de mecanização','status':'CONSULTADA','level':'info'},
    ]
    if apt:kpis.append(apt)
    else:kpis.append({'label':'Aptidão','value':'VER SEÇÃO','note':'camada de aptidão agrícola consultada separadamente','status':'INFO','level':'info'})
    prod['terrain_kpis']=kpis[:4]
    prod['terrain_srtm']=t
    checks=payload.setdefault('agropecuaria',{}).setdefault('property_screening',{}).setdefault('checks',[])
    checks=[x for x in checks if str(x.get('factor') or '')!='Declividade']
    checks.append({'factor':'Declividade SRTM','scope':'raster ~30 m dentro do CAR','status':'consultada','value':{'mediana_graus':t.get('slope_median_deg'),'p90_graus':t.get('slope_p90_deg'),'declive_ate_8_pct':round(under8,1)}})
    payload['agropecuaria']['property_screening']['checks']=checks
    payload['sources']=[x for x in payload.get('sources') or [] if 'ide-sisema / declividade' not in str(x.get('name') or '').lower()]
    payload.setdefault('sources',[]).append({'name':'SRTM 1 arc-second — altitude e declividade','description':f"DEM público ~30 m recortado ao CAR: altitude mediana {t.get('elevation_median_m')} m; declividade mediana {t.get('slope_median_deg')}°, P90 {t.get('slope_p90_deg')}°. Não substitui levantamento topográfico de campo.",'status':'CONSULTADA','level':'ok'})
    return payload

v13._repair_agro_keys=_repair_with_terrain

# Make V13 use V7 truthful-caption wrapper.
v13.build_premium_property_report_v6=build_premium_property_report_v7


def generate_live_report(result:dict,car_code:str):
    working=dict(result)
    try:working['terrain_srtm']=query_terrain_srtm((result.get('car') or {}).get('geometry'))
    except Exception as e:working['terrain_srtm']={'ok':False,'source':'SRTM 1 arc-second','detail':f'{type(e).__name__}:{str(e)[:220]}'}
    meta=v16.generate_live_report(working,car_code)
    meta['report_version']='V17'
    return meta


print('RX_LIVE_REPORT_ADAPTER=V17_HIGHRES_SRTM_SICARV2',flush=True)

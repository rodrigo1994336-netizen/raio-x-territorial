from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json

import report_book_style  # mutates shared report styles before V6 renderer imports them
import live_report_adapter_v11 as v11
from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from report_narrative import build_narrative
from report_engine_v6 import build_premium_property_report_v6
from satellite_real import build_satellite_property_image
from groundwater_siagas import query_groundwater


def _patch_satellite(payload:dict,meta:dict,technical_map:str):
    payload['technical_map_image_path']=technical_map
    payload['satellite_imagery']=meta
    if meta.get('ok'):
        payload['satellite_image_path']=meta.get('path')
        payload['map_image_path']=meta.get('path')
        payload['car_map_image_path']=meta.get('path')
        payload.setdefault('sources',[]).append({
          'name':'Copernicus Sentinel-2 — imagem orbital real',
          'description':f"Cena {meta.get('scene_id') or 'Sentinel-2'} de {meta.get('date') or 'data informada na imagem'}, nuvens {meta.get('cloud_cover_pct') if meta.get('cloud_cover_pct') is not None else '—'}%. Limite CAR sobreposto pelo Raio-X.",
          'status':'CONSULTADA','level':'ok'
        })
    else:
        payload.setdefault('sources',[]).append({'name':'Copernicus Sentinel-2 — imagem orbital real','description':f"Imagem orbital não foi obtida nesta emissão: {meta.get('detail') or 'fonte externa indisponível'}. O mapa técnico permanece disponível.",'status':'INDISPONÍVEL','level':'attention'})
    return payload


def _patch_groundwater(payload:dict,gw:dict):
    water=payload.setdefault('water',{})
    water['groundwater']=gw
    if gw.get('ok'):
        water.setdefault('rain_rows',[]).extend([
          ['Poços SIAGAS próximos',f"{gw.get('well_count') or 0} em até {gw.get('search_radius_km') or '—'} km"],
          ['Profundidade mediana dos poços',f"{gw.get('well_depth_median_m') if gw.get('well_depth_median_m') is not None else '—'} m · n={gw.get('well_depth_sample_n') or 0}"],
          ['Nível estático mediano',f"{gw.get('static_water_level_median_m') if gw.get('static_water_level_median_m') is not None else '—'} m · n={gw.get('static_water_level_sample_n') or 0}"],
          ['Evidência hidrogeológica regional',f"{gw.get('groundwater_evidence') or '—'} · confiança {gw.get('confidence') or '—'}"],
        ])
        payload.setdefault('sources',[]).append({'name':'SGB / SIAGAS — Água subterrânea','description':f"{gw.get('well_count') or 0} poço(s) cadastrados na vizinhança; profundidade mediana {gw.get('well_depth_median_m') if gw.get('well_depth_median_m') is not None else '—'} m e nível estático mediano {gw.get('static_water_level_median_m') if gw.get('static_water_level_median_m') is not None else '—'} m.",'status':'CONSULTADA','level':'ok'})
        payload.setdefault('interpretation_rules',[]).append('Profundidade e nível d’água de poços vizinhos são evidência hidrogeológica regional e não garantem água na mesma profundidade dentro do imóvel; ponto de perfuração exige avaliação local.')
    else:
        payload.setdefault('sources',[]).append({'name':'SGB / SIAGAS — Água subterrânea','description':f"Fonte não respondeu nesta emissão: {gw.get('detail') or 'falha externa'}.",'status':'INDISPONÍVEL','level':'attention'})
    return payload


async def _visual_and_groundwater(result:dict,out_dir):
    geom=(result.get('car') or {}).get('geometry')
    return await asyncio.gather(
      build_satellite_property_image(geom,out_dir/'satellite_property.jpg'),
      query_groundwater(geom,20.0),
      return_exceptions=True
    )


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ');safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_');report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id;out_dir.mkdir(parents=True,exist_ok=True)
    technical_map=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    try:
        sat,gw=asyncio.run(_visual_and_groundwater(result,out_dir))
        if isinstance(sat,Exception):sat={'ok':False,'source':'Sentinel-2','detail':f'{type(sat).__name__}:{str(sat)[:240]}'}
        if isinstance(gw,Exception):gw={'ok':False,'source':'SGB/SIAGAS','detail':f'{type(gw).__name__}:{str(gw)[:240]}'}
    except Exception as e:
        sat={'ok':False,'source':'Sentinel-2','detail':f'{type(e).__name__}:{str(e)[:240]}'};gw={'ok':False,'source':'SGB/SIAGAS','detail':'parallel_extra_failed'}
    primary=sat.get('path') if sat.get('ok') and sat.get('path') else technical_map
    payload=build_live_payload(result,report_id,now.isoformat(),primary)
    payload=_patch_satellite(payload,sat,technical_map)
    payload=v11._patch_autos(payload,result);payload=v11._patch_fire(payload,result);payload=v11._patch_car_limit(payload);payload=v11._patch_constraints(payload,result);payload=v11._patch_extra_territorial(payload,result);payload=v11._patch_water(payload,result);payload=_patch_groundwater(payload,gw);payload=v11._patch_pivots(payload,result);payload=v11._patch_climate(payload,result);payload=v11._patch_minerals(payload,result);payload=v11._patch_ide(payload,result);payload=v11._patch_prodes_lens(payload,result);payload=v11._patch_restricted_parity(payload);payload=v11._patch_agro(payload,result,car_code)
    payload=v11._dedupe_sources(payload);payload=v11._final_truth_guard(payload,result)
    payload['narrative']=build_narrative(payload);payload['quick_read']=payload['narrative']['one_sentence']
    payload['source_version']='Raio-X Territorial V12 • diagramação editorial tipo livro + texto justificado + imagem Sentinel-2 real + SIAGAS + paridade FarmScan + agropecuária + minerais críticos/terras raras.'
    payload_path=out_dir/'payload.json';payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf';digest=build_premium_property_report_v6(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(primary),'technical_map_path':str(technical_map),'satellite_image_path':sat.get('path') if sat.get('ok') else None,'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

print('RX_LIVE_REPORT_ADAPTER=V12_BOOK_SATELLITE_GROUNDWATER',flush=True)

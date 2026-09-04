from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos
from live_report_adapter_v4 import _patch_fire, _patch_car_limit
from live_report_adapter_v5 import _patch_constraints
from live_report_adapter_v6 import _patch_water
from report_engine_v3 import build_premium_property_report_v3


def _patch_pivots(payload:dict,result:dict):
    piv=result.get('pivots_ana') or {}
    section=payload.setdefault('water',{})
    if piv.get('ok'):
        count=int(piv.get('intersection_count') or 0)
        near=int(piv.get('near_count') or 0)
        area=float(piv.get('intersection_area_unique_ha') or 0)
        section['pivot_count']=count
        payload['sources'].append({
            'name':'ANA / SNIRH - Pivôs Centrais 2022',
            'description':f'Mapeamento nacional de pivôs centrais, ano de referência {piv.get("reference_year",2022)}, com interseção geométrica exata ao CAR.',
            'status':'CONSULTADA','level':'ok'
        })
        payload['compliance'].append({
            'label':'Pivôs centrais de irrigação',
            'text':f'{count} pivô(s) intersectante(s), {round(area,6)} ha de interseção única; {near} no raio de {piv.get("radius_km",5)} km.',
            'badge':'IDENTIFICADO' if count else 'SEM INTERSEÇÃO','level':'info' if count else 'ok'
        })
        if count:
            payload['conclusion'].setdefault('positives',[]).append(f'Infraestrutura irrigada mapeada: {count} pivô(s) central(is) intersectam o imóvel na base ANA/SNIRH 2022.')
            payload['conclusion'].setdefault('opportunities',[]).append('Cruzar a infraestrutura de irrigação mapeada com disponibilidade hídrica, outorgas vigentes e aptidão produtiva.')
            grant_count=section.get('grant_count')
            try: grant_count_int=int(grant_count)
            except Exception: grant_count_int=None
            if grant_count_int == 0:
                payload['attention_points'].append('Há pivô central mapeado no imóvel, mas nenhuma outorga intersectante foi localizada nas fontes IGAM/ANA consultadas. Isso não prova irregularidade; exige conferência do ponto de captação, titularidade, vigência e eventual outorga próxima.')
                payload['conclusion'].setdefault('diligence',[]).append('Compatibilizar os pivôs mapeados com as outorgas estaduais/federais, incluindo pontos de captação eventualmente localizados fora do limite do CAR.')
        # Preserve a compact trace in rain rows area only if no climate data is present.
        if not section.get('rain_rows'):
            section['rain_rows']=[
                ['Pivôs intersectantes',count],
                ['Área única de interseção',f'{round(area,6)} ha'],
                ['Pivôs em até 5 km',near],
                ['Referência do mapeamento',str(piv.get('reference_year',2022))],
            ]
    else:
        section['pivot_count']='NÃO CONSULTADO'
        payload['sources'].append({'name':'ANA / SNIRH - Pivôs Centrais 2022','description':'A camada oficial não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_'); report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id; out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result)
    payload=_patch_fire(payload,result)
    payload=_patch_car_limit(payload)
    payload=_patch_constraints(payload,result)
    payload=_patch_water(payload,result)
    payload=_patch_pivots(payload,result)
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v3(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos
from live_report_adapter_v4 import _patch_fire, _patch_car_limit
from live_report_adapter_v5 import _patch_constraints
from report_engine_v3 import build_premium_property_report_v3


def _safe(v, default='-'):
    return default if v is None or v == '' else str(v)


def _fmt_grant(item: dict):
    p=item.get('properties') or {}
    proc=p.get('numpa_4') or p.get('objectid') or '-'
    port=p.get('numport_4') or '-'
    status=p.get('statuspa_4') or '-'
    use=p.get('usoinsig_4') or p.get('tipouso_4') or '-'
    dist=item.get('distance_m')
    loc='DENTRO' if item.get('inside') else (f'{round(float(dist)/1000,2)} km' if dist is not None else '-')
    return [str(proc), str(port), str(status), str(use), loc]


def _patch_water(payload: dict, result: dict):
    water=result.get('water_mg') or {}
    section=payload.setdefault('water',{})
    if water.get('ok'):
        inside=int(water.get('inside_count') or 0)
        near=int(water.get('near_count') or 0)
        section['grant_count']=inside
        section['grants']=[_fmt_grant(x) for x in (water.get('inside') or [])[:8]]
        nearest=water.get('nearest') or {}
        if inside:
            section['meaning']=f'Foram localizados {inside} ponto(s) de outorga intersectando o polígono do imóvel. A vigência e os efeitos de cada ato devem ser conferidos no processo/portaria correspondente.'
        elif near:
            d=nearest.get('distance_m')
            section['meaning']=f'Nenhuma outorga intersecta o imóvel. Foram localizadas {near} outorga(s) em até {water.get("radius_km",5)} km; a mais próxima está a {round(float(d)/1000,2) if d is not None else "-"} km.'
        else:
            section['meaning']=f'Nenhuma outorga foi localizada dentro do imóvel ou no raio de {water.get("radius_km",5)} km na consulta atual.'
        section['rain_rows'] = section.get('rain_rows') or []
        payload['sources'].append({'name':'IGAM / IDE-Sisema - Outorgas','description':f'Consulta WFS oficial por polígono e proximidade. Camada: {water.get("layer") or "-"}.','status':'CONSULTADA','level':'ok'})
        payload['compliance'].append({'label':'Outorgas de uso de água','text':f'{inside} dentro do imóvel; {near} em até {water.get("radius_km",5)} km.','badge':'ATENÇÃO' if inside else 'SEM INTERSEÇÃO','level':'attention' if inside else 'ok'})
        if inside:
            payload['attention_points'].append(f'Outorgas: {inside} ponto(s) de direito de uso de recursos hídricos intersectam o imóvel.')
            payload['conclusion'].setdefault('diligence',[]).append('Conferir processo, portaria, vigência, finalidade e condições das outorgas que intersectam o imóvel.')
            payload['conclusion'].setdefault('risks',[]).append('Há outorga(s) de uso de recursos hídricos vinculada(s) espacialmente ao imóvel; verificar titularidade do ato e obrigações associadas.')
            for row in payload['conclusion'].get('categories') or []:
                if row.get('label')=='Hídrico':
                    row['risk']='ATENÇÃO'; row['level']='attention'; row['text']=f'{inside} outorga(s) intersectante(s) localizada(s) na IDE-Sisema/IGAM.'
    else:
        section['grant_count']='NÃO CONSULTADO'
        section['grants']=[]
        section['meaning']='A fonte oficial de outorgas não respondeu nesta emissão; não interpretar ausência de dados como ausência de outorga.'
        payload['sources'].append({'name':'IGAM / IDE-Sisema - Outorgas','description':'A fonte oficial não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
        payload['compliance'].append({'label':'Outorgas de uso de água','text':'Fonte indisponível nesta emissão.','badge':'NÃO CONSULTADO','level':'neutral'})
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
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v3(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos
from report_engine_v2 import build_premium_property_report_v2


def _patch_fire(payload: dict, result: dict):
    fire=result.get('fire_live') or {}
    mon=payload.setdefault('monitoring',{})
    if fire.get('ok'):
        inside=int(fire.get('inside_count') or 0); near=int(fire.get('near_count') or 0)
        window=fire.get('window_note') or 'Janela recente do feed de 10 minutos.'
        latest=fire.get('latest_file') or '-'
        nearest=fire.get('nearest') or {}
        near_note=f"mais próximo: {nearest.get('distance_m')} m" if nearest else f'arquivo: {latest}'
        mon.update({
            'fire_inside_label':'Focos dentro - feed recente',
            'fire_inside':inside,
            'fire_inside_note':window,
            'fire_near_label':f"Focos até {fire.get('radius_km',5)} km - feed recente",
            'fire_near':near,
            'fire_near_note':near_note,
        })
        payload['sources'].append({'name':'INPE / Programa Queimadas','description':f"Focos de calor em tempo quase real; {window} Último arquivo processado: {latest}.",'status':'CONSULTADA','level':'ok'})
        payload['compliance'].append({'label':'Focos de calor - janela recente','text':f'{inside} dentro do imóvel e {near} até {fire.get("radius_km",5)} km no conjunto recente processado.','badge':'ATENÇÃO' if near else 'SEM FOCO RECENTE','level':'attention' if near else 'ok'})
        if near:
            payload['attention_points'].insert(0,f'Focos de calor recentes: {inside} dentro do imóvel e {near} até {fire.get("radius_km",5)} km; confirmar situação em campo.')
            payload['conclusion'].setdefault('risks',[]).append(f'{near} foco(s) de calor no raio monitorado na janela recente do INPE; foco orbital não confirma sozinho incêndio em solo.')
        payload['interpretation_rules'].append('Foco de calor por satélite é um alerta operacional e não prova sozinho a existência, extensão ou causa de um incêndio em solo.')
    else:
        mon.update({'fire_inside_label':'Focos dentro - feed recente','fire_inside':'NÃO CONSULTADO','fire_inside_note':'feed INPE indisponível','fire_near_label':'Focos próximos - feed recente','fire_near':'NÃO CONSULTADO','fire_near_note':'feed INPE indisponível'})
        payload['sources'].append({'name':'INPE / Programa Queimadas','description':'O feed de focos em tempo quase real não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def _patch_car_limit(payload: dict):
    # The public SICAR WFS used here exposes one property layer per UF, not APP/RL sublayers.
    # Never infer missing environmental composition from that service.
    payload['car']['areas']=[
        ('APP / Reserva Legal / vegetação / consolidada','NÃO CONSULTADO NESTA FONTE'),
    ]
    payload['sources'].append({'name':'SICAR - composição ambiental interna','description':'O WFS público consultado expõe o limite e atributos gerais do imóvel, mas não disponibilizou camadas separadas de APP, Reserva Legal, vegetação e área consolidada. Esses itens permanecem NÃO CONSULTADOS nesta emissão.','status':'NÃO CONSULTADA','level':'neutral'})
    return payload


def generate_live_report(result: dict, car_code: str):
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_')
    report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id; out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result)
    payload=_patch_fire(payload,result)
    payload=_patch_car_limit(payload)
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v2(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

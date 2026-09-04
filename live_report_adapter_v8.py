from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos
from live_report_adapter_v4 import _patch_fire, _patch_car_limit
from live_report_adapter_v5 import _patch_constraints
from live_report_adapter_v6 import _patch_water
from live_report_adapter_v7 import _patch_pivots
from report_engine_v3 import build_premium_property_report_v3


def _fmt(v,suffix='',digits=2):
    if v is None: return 'NÃO DISPONÍVEL'
    try: return f'{round(float(v),digits)}{suffix}'
    except Exception: return str(v)


def _patch_climate(payload:dict,result:dict):
    cl=result.get('climate_nasa') or {}
    section=payload.setdefault('water',{})
    if cl.get('ok'):
        section['rain_30d']=_fmt(cl.get('rain_sum_mm'),' mm',2)
        section['rain_period']=f"{cl.get('period_start','-')} a {cl.get('period_end','-')} • {cl.get('available_days','-')} dias válidos"
        section['rain_rows']=[
            ['Precipitação acumulada - janela recente',_fmt(cl.get('rain_sum_mm'),' mm',2)],
            ['Precipitação média diária',_fmt(cl.get('rain_daily_avg_mm'),' mm/dia',2)],
            ['Temperatura média',_fmt(cl.get('temp_avg_c'),' °C',2)],
            ['Máxima diária média',_fmt(cl.get('temp_max_avg_c'),' °C',2)],
            ['Mínima diária média',_fmt(cl.get('temp_min_avg_c'),' °C',2)],
            ['Umidade relativa média',_fmt(cl.get('rh_avg_pct'),' %',1)],
            ['Radiação solar média',_fmt(cl.get('solar_avg_kwh_m2_day'),' kWh/m²/dia',2)],
        ]
        payload['sources'].append({
            'name':'NASA POWER - Agroclimatologia',
            'description':f'Dados diários no centróide do imóvel; janela {cl.get("period_start")} a {cl.get("period_end")}. Produto em grade, próximo de tempo real.',
            'status':'CONSULTADA','level':'ok'
        })
        payload['interpretation_rules'].append('Dados NASA POWER representam estimativas em grade no centróide do imóvel; não equivalem a medição de pluviômetro ou estação meteorológica instalada na propriedade.')
    else:
        section['rain_30d']='NÃO CONSULTADO'
        section['rain_period']='Fonte climática indisponível nesta emissão.'
        section['rain_rows']=[]
        payload['sources'].append({'name':'NASA POWER - Agroclimatologia','description':'A fonte não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def _patch_minerals(payload:dict,result:dict):
    cm=result.get('critical_minerals') or {}
    mining=payload.setdefault('mining',{})
    anm=cm.get('anm') or {}; sgb=cm.get('sgb') or {}
    counts=anm.get('counts') or {}
    mineral_codes=cm.get('mineral_codes') or []
    rare_count=int(counts.get('terras_raras') or 0)
    rare_signal=bool(cm.get('rare_earth_signal'))
    mining['critical_process_count']=int(anm.get('critical_process_count') or 0)
    mining['critical_minerals']=mineral_codes
    mining['rare_earth_count']=rare_count
    mining['rare_earth_signal']='SIM' if rare_signal else 'NÃO IDENTIFICADO'
    mining['rare_earth_source']='ANM/SIGMINE + Serviço Geológico do Brasil (GeoSGB/WMS)'
    mining['critical_rows']=[
        ['Processos ANM classificados como minerais críticos',mining['critical_process_count']],
        ['Terras raras - processos ANM',rare_count],
        ['Sinal geológico / camada SGB para terras raras','SIM' if rare_signal else 'NÃO IDENTIFICADO'],
        ['Minerais críticos identificados',', '.join(mineral_codes) if mineral_codes else 'Nenhum sinal classificado na consulta'],
        ['Camadas SGB candidatas',sgb.get('candidate_layer_count') if sgb.get('candidate_layer_count') is not None else 'NÃO DISPONÍVEL'],
        ['Camadas SGB consultadas',sgb.get('queried_layer_count') if sgb.get('queried_layer_count') is not None else 'NÃO DISPONÍVEL'],
    ]
    if rare_signal:
        mining['summary']=(mining.get('summary') or '')+' Há sinal de interesse para terras raras em processo ANM e/ou camada geológica pública do SGB. Isso exige investigação geológica; não comprova jazida, recurso ou reserva economicamente explotável.'
    elif cm.get('ok'):
        mining['summary']=(mining.get('summary') or '')+' A triagem ANM/SGB não identificou sinal classificado de terras raras nesta consulta.'
    else:
        mining['summary']=(mining.get('summary') or '')+' A consulta ao Serviço Geológico do Brasil ficou indisponível ou incompleta nesta emissão.'
    payload['sources'].append({
        'name':'ANM/SIGMINE + SGB/GeoSGB - Minerais críticos e terras raras',
        'description':'Triagem de processos minerários e camadas públicas de interesse geológico/mineral. Não equivale a pesquisa mineral de campo, recurso ou reserva.',
        'status':'CONSULTADA' if cm.get('ok') else 'PARCIAL',
        'level':'attention' if rare_signal or not cm.get('ok') else 'ok'
    })
    payload['interpretation_rules'].append('Sinal de terras raras ou favorabilidade geológica é somente triagem de interesse mineral; não comprova ocorrência economicamente explotável, teor, recurso, reserva ou titularidade de direito minerário.')
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
    payload=_patch_climate(payload,result)
    payload=_patch_minerals(payload,result)
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v3(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}
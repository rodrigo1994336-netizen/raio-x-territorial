from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from report_engine import build_premium_property_report
from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos


def _pct(area, total):
    try:
        return round(float(area or 0)/float(total or 0)*100,2) if total else 0.0
    except Exception:
        return 0.0


def _patch_sicar_details(payload: dict, result: dict):
    details=result.get('sicar_details') or {}
    total=float((result.get('car') or {}).get('properties',{}).get('area') or 0)
    if details.get('ok'):
        summary=details.get('summary') or {}
        rows=[]
        layer_rows=[]
        for cat in ('Reserva Legal','APP','Vegetação nativa/remanescente','Área consolidada','Uso restrito','Nascentes','Hidrografia'):
            v=summary.get(cat)
            if not v: continue
            area=round(float(v.get('area_unique_ha') or 0),6)
            rows.append((cat,f'{area} ha ({_pct(area,total)}%)'))
            layer_rows.append([f'SICAR - {cat}',f'{area} ha • {int(v.get("occurrence_count") or 0)} geometria(s)','SICAR/WFS'])
            payload['compliance'].append({'label':f'CAR - {cat}','text':f'{area} ha ({_pct(area,total)}% do imóvel)','badge':'CONSULTADO','level':'ok'})
        payload['car']['areas']=rows
        existing=payload['environment'].get('layer_rows') or []
        payload['environment']['layer_rows']=layer_rows+existing
        payload['sources'].append({'name':'SICAR - camadas ambientais detalhadas','description':f'Descoberta dinâmica de {details.get("discovery_total",0)} camadas candidatas; APP, Reserva Legal, vegetação, área consolidada e demais categorias são cruzadas geometricamente quando disponíveis.','status':'CONSULTADA','level':'ok'})
        payload['land']['summary'] += ' As camadas ambientais internas do SICAR também foram consultadas e medidas por interseção exata.'
    else:
        payload['car']['areas']=[('APP / Reserva Legal / vegetação','NÃO CONSULTADO')]
        payload['sources'].append({'name':'SICAR - camadas ambientais detalhadas','description':'A descoberta ou consulta das camadas internas do SICAR falhou nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def generate_live_report(result: dict, car_code: str):
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_')
    report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id; out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result)
    payload=_patch_sicar_details(payload,result)
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

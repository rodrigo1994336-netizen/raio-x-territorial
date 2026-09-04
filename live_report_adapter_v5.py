from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from live_report_adapter_v2 import _patch_autos
from live_report_adapter_v4 import _patch_fire, _patch_car_limit
from report_engine_v2 import build_premium_property_report_v2


def _pct(area,total):
    try: return round(float(area or 0)/float(total or 0)*100,2) if total else 0.0
    except Exception: return 0.0


def _patch_constraints(payload:dict,result:dict):
    c=result.get('territorial_constraints') or {}; services=c.get('services') or {}
    total=float((result.get('car') or {}).get('properties',{}).get('area') or 0)
    # Remove placeholders that are now actually queried.
    existing=[]
    for row in payload['environment'].get('layer_rows') or []:
        label=str(row[0]).lower()
        if any(x in label for x in ('terra indígena','unidade de conservação','quilombola','assentamento')): continue
        existing.append(row)
    layer_rows=[]
    for key in ('terra_indigena','unidade_conservacao','quilombola','assentamento'):
        r=services.get(key) or {}; label=r.get('label') or key
        if r.get('ok'):
            n=int(r.get('occurrence_count') or 0); area=float(r.get('area_unique_ha') or 0)
            layer_rows.append([label,f'{n} ocorrência(s) • {round(area,6)} ha ({_pct(area,total)}%)',r.get('source') or '-'])
            payload['compliance'].append({'label':label,'text':f'{n} interseção(ões) exata(s), área única {round(area,6)} ha.','badge':'ATENÇÃO' if n else 'SEM SOBREPOSIÇÃO','level':'attention' if n else 'ok'})
            payload['sources'].append({'name':label,'description':f'Consulta territorial por polígono e interseção exata. Fonte: {r.get("source")}.','status':'CONSULTADA','level':'ok'})
            if n:
                payload['attention_points'].append(f'{label}: {n} sobreposição(ões), cobrindo {round(area,6)} ha do imóvel.')
                payload['conclusion'].setdefault('risks',[]).append(f'{label}: sobreposição de {round(area,6)} ha; verificar regime jurídico e efeitos aplicáveis ao imóvel.')
                payload['conclusion'].setdefault('diligence',[]).append(f'Conferir situação, ato constitutivo e efeitos da sobreposição com {label}.')
        else:
            layer_rows.append([label,'NÃO CONSULTADO / FONTE INDISPONÍVEL',r.get('source') or '-'])
            payload['sources'].append({'name':label,'description':'A fonte não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    payload['environment']['layer_rows']=layer_rows+existing

    icmbio=services.get('embargo_icmbio') or {}
    if icmbio.get('ok'):
        n=int(icmbio.get('occurrence_count') or 0); area=float(icmbio.get('area_unique_ha') or 0)
        payload['compliance'].append({'label':'Embargos ICMBio','text':f'{n} interseção(ões) • {round(area,6)} ha','badge':'ALTO' if n else 'SEM EMBARGO','level':'critical' if n else 'ok'})
        payload['sources'].append({'name':'ICMBio - embargos','description':'Embargos ICMBio consultados por interseção espacial exata.','status':'CONSULTADA','level':'ok'})
        if n:
            payload['enforcement']['embargo_count']=int(payload['enforcement'].get('embargo_count') or 0)+n
            payload['enforcement']['embargo_summary']=f'Foram localizados embargo(s) ambiental(is): ICMBio {n}; consultar também o detalhamento IBAMA.'
            for row in payload['conclusion'].get('categories') or []:
                if row.get('label')=='Fiscalização':
                    row['risk']='ALTO'; row['level']='critical'; row['text']=f'Embargo ICMBio: {n} ocorrência(s), {round(area,6)} ha, além das verificações IBAMA.'
            payload['conclusion']['overall_risk']='ALTO'
            payload['conclusion']['overall_reason']='Há embargo ambiental intersectando o imóvel em fonte oficial consultada; exige diligência imediata antes de decisão patrimonial.'
    else:
        payload['compliance'].append({'label':'Embargos ICMBio','text':'Fonte indisponível nesta emissão.','badge':'NÃO CONSULTADO','level':'neutral'})

    union_area=float(c.get('area_unique_all_constraints_ha') or 0)
    if union_area:
        payload['environment']['unique_problem_area_ha']=round(union_area,6)
        payload['environment']['unique_problem_area_pct']=_pct(union_area,total)
    return payload


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_'); report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id; out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result); payload=_patch_fire(payload,result); payload=_patch_car_limit(payload); payload=_patch_constraints(payload,result)
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v2(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

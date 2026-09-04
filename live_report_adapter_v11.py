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
from live_report_adapter_v8 import _patch_climate, _patch_minerals
from live_report_adapter_v9 import _patch_ide, _patch_prodes_lens, _dedupe_sources, _final_truth_guard
from report_narrative import build_narrative
from report_engine_v5 import build_premium_property_report_v5
from premium_integrations import status as premium_status


def _pct(area,total):
    try:return round(float(area or 0)/float(total or 0)*100,2) if total else 0.0
    except Exception:return 0.0


def _patch_extra_territorial(payload:dict,result:dict):
    cons=result.get('territorial_constraints') or {};services=cons.get('services') or {};env=payload.setdefault('environment',{})
    total=float((result.get('car') or {}).get('properties',{}).get('area') or 0)
    labels={'floresta_publica':'Floresta Pública — SFB','sitio_arqueologico':'Sítio Arqueológico — IPHAN'}
    for key,label in labels.items():
        r=services.get(key) or {};source=r.get('source') or ('SFB' if key=='floresta_publica' else 'IPHAN')
        if r.get('ok'):
            n=int(r.get('occurrence_count') or 0);area=float(r.get('area_unique_ha') or 0)
            env.setdefault('layer_rows',[]).append([label,f'{n} ocorrência(s) • {round(area,6)} ha ({_pct(area,total)}%)',source])
            payload.setdefault('sources',[]).append({'name':label,'description':f'Consulta pública com interseção geométrica exata ao CAR. {n} ocorrência(s); área única {round(area,6)} ha.','status':'CONSULTADA','level':'attention' if n else 'ok'})
            payload.setdefault('compliance',[]).append({'label':label,'text':f'{n} interseção(ões) exata(s) • {round(area,6)} ha','badge':'ATENÇÃO' if n else 'SEM SOBREPOSIÇÃO','level':'attention' if n else 'ok'})
            if n:
                if key=='floresta_publica':
                    payload.setdefault('attention_points',[]).append(f'Floresta Pública: {n} sobreposição(ões) cartográfica(s), cobrindo {round(area,6)} ha. Verifique categoria, destinação e regime aplicável.')
                    payload.setdefault('conclusion',{}).setdefault('diligence',[]).append('Conferir no Serviço Florestal Brasileiro a categoria, destinação e situação das áreas de Floresta Pública que intersectam o imóvel.')
                else:
                    payload.setdefault('attention_points',[]).append(f'Sítio arqueológico: {n} ocorrência(s) cartográfica(s) do IPHAN intersectam a área; isso exige verificação do cadastro, precisão posicional e efeitos aplicáveis.')
                    payload.setdefault('conclusion',{}).setdefault('diligence',[]).append('Conferir no IPHAN o cadastro, a precisão espacial e as obrigações relacionadas a sítio(s) arqueológico(s) intersectante(s).')
        else:
            env.setdefault('layer_rows',[]).append([label,'FONTE INDISPONÍVEL NESTA EMISSÃO',source])
            payload.setdefault('sources',[]).append({'name':label,'description':f'A fonte pública não respondeu nesta emissão. Motivo: {r.get("detail") or r.get("error") or "falha externa"}.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def _patch_restricted_parity(payload:dict):
    pstat=premium_status();by_code={x['code']:x for x in pstat.get('integrations') or []}
    mapping=[
        ('sncr_ccir','SNCR / CCIR','CCIR e dados cadastrais do imóvel rural mediante habilitação oficial.'),
        ('onr_matricula','ONR / RI Digital — Matrícula','Matrícula atualizada e serviços registrais.'),
        ('onr_onus','ONR / RI Digital — Ônus','Certidão de ônus e atos registrais.'),
        ('onr_pesquisa_bens','ONR — Pesquisa Nacional de Bens','Pesquisa patrimonial autorizada conforme regras do serviço.'),
        ('holder_search','Busca por titular / CPF / CNPJ','Consulta por titular em provedor/base legalmente habilitada.'),
    ]
    for code,label,desc in mapping:
        row=by_code.get(code) or {};ready=bool(row.get('ready'))
        payload.setdefault('sources',[]).append({'name':label,'description':desc+(' Integração ativa nesta emissão.' if ready else ' Integração já preparada na arquitetura; ativação pendente de credencial/habilitação.'),'status':'CONSULTADA' if ready else 'INTEGRAÇÃO PREPARADA — OFF','level':'ok' if ready else 'neutral'})
    return payload


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ');safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_');report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id;out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result);payload=_patch_fire(payload,result);payload=_patch_car_limit(payload);payload=_patch_constraints(payload,result);payload=_patch_extra_territorial(payload,result);payload=_patch_water(payload,result);payload=_patch_pivots(payload,result);payload=_patch_climate(payload,result);payload=_patch_minerals(payload,result);payload=_patch_ide(payload,result);payload=_patch_prodes_lens(payload,result);payload=_patch_restricted_parity(payload)
    payload=_dedupe_sources(payload);payload=_final_truth_guard(payload,result)
    payload['narrative']=build_narrative(payload);payload['quick_read']=payload['narrative']['one_sentence']
    payload['source_version']='Raio-X Territorial V11.1 • benchmark mesmo CAR • SFB + IPHAN + PRODES histórico/triagem crédito + aptidão ha/% + integrações restritas explícitas.'
    payload_path=out_dir/'payload.json';payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf';digest=build_premium_property_report_v5(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

print('RX_LIVE_REPORT_ADAPTER=V11_1_SAME_CAR_PARITY',flush=True)

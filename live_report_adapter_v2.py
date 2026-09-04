from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json

from report_engine import build_premium_property_report
from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload


def _money(v):
    try:
        n=float(v)
        raw=f'{n:,.2f}'.replace(',','§').replace('.',',').replace('§','.')
        return f'R$ {raw}'
    except Exception:
        return '-'


def _patch_autos(payload: dict, result: dict):
    autos = result.get('autos_ibama') or {}
    ok = autos.get('ok')
    if ok is True:
        count = int(autos.get('occurrence_count') or 0)
        total = float(autos.get('fine_total') or 0)
        rows=[]
        for a in (autos.get('autos') or [])[:8]:
            rows.append([
                a.get('auto_number') or '-',
                a.get('date') or '-',
                _money(a.get('fine_value')) if a.get('fine_value') is not None else '-',
                a.get('status') or 'Sem situação publicada',
            ])
        payload['enforcement']['auto_count']=count
        payload['enforcement']['fine_total_text']=_money(total) if count else 'R$ 0,00'
        payload['enforcement']['autos']=rows
        text = f'{count} auto(s) de infração ambiental intersectam espacialmente o imóvel.' if count else 'Nenhum auto de infração ambiental intersectante foi localizado na consulta atual.'
        level='critical' if count else 'ok'
        badge='ALTO' if count else 'BAIXO'
        payload['compliance'].append({'label':'Autos / multas IBAMA','text':text,'badge':badge,'level':level})
        payload['sources'].append({'name':'IBAMA / PAMGIA - autos de infração','description':'Autos ambientais por coordenada, com deduplicação para reduzir contagem superestimada. Dados pessoais não são exibidos no relatório.','status':'CONSULTADA','level':'ok'})
        cats=payload.get('conclusion',{}).get('categories') or []
        for row in cats:
            if row.get('label')=='Fiscalização':
                emb_count=int((result.get('embargos_ibama') or {}).get('exact',{}).get('occurrence_count') or 0)
                row['text']=f'Embargos IBAMA: {emb_count}. Autos de infração ambientais: {count}. Valor nominal somado dos autos localizados: {_money(total)}.'
                if count or emb_count:
                    row['risk']='ALTO'; row['level']='critical'
        if count:
            con=payload.get('conclusion') or {}
            if con.get('overall_risk') not in ('ALTO','CRÍTICO'):
                con['overall_risk']='ALTO'
                con['overall_reason']='Há auto(s) de infração ou embargo(s) ambiental(is) intersectando o imóvel nas fontes consultadas; exige diligência imediata e conferência do processo administrativo.'
            con.setdefault('risks',[]).insert(0,f'{count} auto(s) de infração ambiental localizado(s), com valor nominal total de {_money(total)}.')
            con.setdefault('diligence',[]).insert(0,'Conferir número, processo, situação, ciência, prazo e documentos de cada auto de infração antes de qualquer decisão.')
    else:
        payload['enforcement']['auto_count']='NÃO CONSULTADO'
        payload['enforcement']['fine_total_text']='fonte indisponível nesta emissão'
        payload['compliance'].append({'label':'Autos / multas IBAMA','text':'A fonte de autos não respondeu nesta emissão.','badge':'NÃO CONSULTADO','level':'neutral'})
        payload['sources'].append({'name':'IBAMA / PAMGIA - autos de infração','description':'A consulta falhou ou não respondeu nesta emissão.','status':'INDISPONÍVEL','level':'attention'})
    return payload


def generate_live_report(result: dict, car_code: str):
    now=datetime.now(timezone.utc)
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_')
    report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id
    out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result)
    payload_path=out_dir/'payload.json'
    payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'
    digest=build_premium_property_report(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

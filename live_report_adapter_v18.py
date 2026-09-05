from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path

import live_report_adapter_v17 as v17
from street_level_access import download_street_level_access
from report_engine_v8 import build_premium_property_report_v8


def generate_live_report(result:dict,car_code:str):
    # Build the complete V17 dossier first, then enrich the final payload with
    # optional street-level evidence and rebuild with the V8 renderer. This keeps
    # the stable V17 analysis pipeline untouched while wiring the new evidence in.
    meta=v17.generate_live_report(result,car_code)
    payload_path=Path(meta['payload_path']);pdf_path=Path(meta['pdf_path']);out_dir=pdf_path.parent
    geom=(result.get('car') or {}).get('geometry')
    try:
        access=asyncio.run(download_street_level_access(geom,out_dir/'street_level_access.jpg')) if geom else {'ok':False,'source':'KartaView','detail':'car_geometry_missing'}
    except Exception as e:
        access={'ok':False,'source':'KartaView','detail':f'{type(e).__name__}:{str(e)[:220]}'}
    payload=json.loads(payload_path.read_text(encoding='utf-8'))
    payload['street_level_access']=access
    # One source row only, with truthful status.
    payload['sources']=[x for x in payload.get('sources') or [] if 'kartaview' not in str(x.get('name') or '').lower()]
    if access.get('ok'):
        payload.setdefault('sources',[]).append({'name':'KartaView — imagem georreferenciada de via pública','description':f"{access.get('label') or 'Imagem próxima ao CAR'}; distância aproximada ao limite {access.get('distance_to_car_m') if access.get('distance_to_car_m') is not None else '—'} m. Licença CC BY-SA 4.0. Não comprova o portão oficial sem validação de campo.",'status':'CONSULTADA','level':'ok'})
    else:
        payload.setdefault('sources',[]).append({'name':'KartaView — imagem georreferenciada de via pública','description':f"Sem imagem aberta utilizável nesta emissão: {access.get('detail') or 'sem cobertura próxima'}. Link de Street View ao vivo é mantido quando disponível.",'status':'INDISPONÍVEL','level':'attention'})
    payload['source_version']='Raio-X Territorial V18 • V17 completo + evidência de acesso/via pública KartaView + link Street View ao vivo.'
    payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    digest=build_premium_property_report_v8(pdf_path,payload)
    meta.update({'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest(),'street_level_access':access,'report_version':'V18'})
    return meta


print('RX_LIVE_REPORT_ADAPTER=V18_STREET_ACCESS_CONNECTED',flush=True)

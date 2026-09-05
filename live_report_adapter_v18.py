from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path

import live_report_adapter_v17 as v17
from street_level_access import download_street_level_access
from report_engine_v8 import build_premium_property_report_v8


def _deferred_renderer(path,payload):
    """Internal placeholder used only while V17 assembles the payload.

    V18 used to render the full V17 PDF and then render it all over again after
    adding street-level evidence. The placeholder satisfies V13's internal file
    bookkeeping; the file is immediately overwritten by the single final V8 render.
    """
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    raw=b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'
    p.write_bytes(raw)
    return sha256(raw).hexdigest()


def generate_live_report(result:dict,car_code:str):
    # Assemble all V17 data/images but defer its expensive ReportLab pass.
    previous_renderer=v17.v13.build_premium_property_report_v6
    v17.v13.build_premium_property_report_v6=_deferred_renderer
    try:
        meta=v17.generate_live_report(result,car_code)
    finally:
        v17.v13.build_premium_property_report_v6=previous_renderer

    payload_path=Path(meta['payload_path']);pdf_path=Path(meta['pdf_path']);out_dir=pdf_path.parent
    geom=(result.get('car') or {}).get('geometry')
    try:
        access=asyncio.run(download_street_level_access(geom,out_dir/'street_level_access.jpg')) if geom else {'ok':False,'source':'KartaView','detail':'car_geometry_missing'}
    except Exception as e:
        access={'ok':False,'source':'KartaView','detail':f'{type(e).__name__}:{str(e)[:220]}'}

    payload=json.loads(payload_path.read_text(encoding='utf-8'))
    payload['street_level_access']=access
    payload['sources']=[x for x in payload.get('sources') or [] if 'kartaview' not in str(x.get('name') or '').lower()]
    if access.get('ok'):
        payload.setdefault('sources',[]).append({'name':'KartaView — imagem georreferenciada de via pública','description':f"{access.get('label') or 'Imagem próxima ao CAR'}; distância aproximada ao limite {access.get('distance_to_car_m') if access.get('distance_to_car_m') is not None else '—'} m. Licença CC BY-SA 4.0. Não comprova o portão oficial sem validação de campo.",'status':'CONSULTADA','level':'ok'})
    else:
        payload.setdefault('sources',[]).append({'name':'KartaView — imagem georreferenciada de via pública','description':f"Sem imagem aberta utilizável nesta emissão: {access.get('detail') or 'sem cobertura próxima'}. Link de Street View ao vivo é mantido quando disponível.",'status':'INDISPONÍVEL','level':'attention'})
    payload['source_version']='Raio-X Territorial V18.1 • V17 completo + evidência de acesso/via pública + renderização única otimizada.'
    payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

    # One and only one full ReportLab pass.
    digest=build_premium_property_report_v8(pdf_path,payload)
    meta.update({'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest(),'street_level_access':access,'report_version':'V18.1'})
    return meta


print('RX_LIVE_REPORT_ADAPTER=V18_1_SINGLE_RENDER_STREET_ACCESS',flush=True)

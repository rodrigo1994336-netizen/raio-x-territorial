from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

import report_book_style
import live_report_adapter_v11 as v11
import live_report_adapter_v12 as v12
from live_report_adapter import REPORT_DIR, build_technical_map, build_live_payload
from report_narrative import build_narrative
from report_engine_v6 import build_premium_property_report_v6
from satellite_real import build_satellite_property_image
from groundwater_siagas import query_groundwater
from climate_nasa import query_climatology_nasa, build_drought_screening
from safras_ibge import query_safras
from sicar_detail_sources import query_sicar_details
from aerodromes_anac import query_aerodromes_anac
from agropecuaria import query_sif_establishments
from soilgrids_wcs import query_soilgrids_wcs


def _s(v,default=''):
    return default if v is None or str(v).strip()=='' else str(v).strip()


def _font(size:int,bold=False):
    path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(path,size)
    except Exception:return None


def _best_property_name(result:dict[str,Any]) -> tuple[str,str]:
    requested=_s(result.get('_requested_property_name'))
    if requested and requested.lower() not in {'imóvel rural','imovel rural'} and not requested.lower().startswith('imóvel rural —'):
        return requested[:120],'nome informado na busca'
    props=(result.get('car') or {}).get('properties') or {}
    for key in ('nome_imovel','denominacao','denom_imovel','nome_area','nom_imovel','nome'):
        value=_s(props.get(key))
        if value and value not in {'-','—'}:return value[:120],f'SICAR:{key}'
    return f"Imóvel rural — {_s(props.get('municipio'),'município não informado')}/{_s(props.get('uf'))}",'SICAR sem denominação pública'


def _stamp_image(path:str|Path|None,name:str,kind:str):
    p=Path(str(path or ''))
    if not p.exists():return
    try:
        img=Image.open(p).convert('RGB')
        if kind=='satellite':
            img=ImageOps.autocontrast(img,cutoff=.5)
            img=ImageEnhance.Contrast(img).enhance(1.18)
            img=ImageEnhance.Color(img).enhance(1.28)
            img=ImageEnhance.Sharpness(img).enhance(1.18)
        draw=ImageDraw.Draw(img,'RGBA');w,h=img.size
        panel_h=max(72,int(h*.09))
        draw.rounded_rectangle((24,h-panel_h-20,w-24,h-18),radius=14,fill=(4,17,12,220),outline=(255,255,255,55),width=1)
        label='IMAGEM ORBITAL REAL — LIMITE DO CAR' if kind=='satellite' else 'MAPA TÉCNICO — LIMITE E INTERSEÇÕES'
        draw.text((42,h-panel_h-8),label,fill='white',font=_font(max(17,int(h*.022)),True))
        draw.text((42,h-panel_h+24),name[:100],fill=(208,238,220),font=_font(max(15,int(h*.019)),True))
        img.save(p,quality=94,optimize=True)
    except Exception:pass


def _append_source(payload:dict,name:str,description:str,status:str,level:str):
    payload.setdefault('sources',[]).append({'name':name,'description':description,'status':status,'level':level})


def _patch_identity(payload:dict,result:dict,name:str,name_source:str):
    p=payload.setdefault('property',{});p['name']=name;p['name_source']=name_source
    car=payload.setdefault('car',{});fields=list(car.get('fields') or [])
    fields=[x for x in fields if str(x[0]).lower() not in {'denominação do imóvel','denominacao do imovel','nome do imóvel','nome do imovel'}]
    fields.insert(0,['Denominação do imóvel',name])
    car['fields']=fields
    car['summary']=f"{name} • CAR {_s(p.get('car_code'),'—')} • situação {_s(car.get('status'),'—')} • {_s(car.get('analysis_status'),'—')}."
    _append_source(payload,'Identidade/denominação do imóvel',f'{name}. Origem usada nesta emissão: {name_source}.','CONSULTADA' if name_source!='SICAR sem denominação pública' else 'PARCIAL','ok' if name_source!='SICAR sem denominação pública' else 'attention')
    return payload


def _patch_car_details(payload:dict,details:dict):
    car=payload.setdefault('car',{});env=payload.setdefault('environment',{})
    summary=details.get('summary') or {}
    labels=('APP','Reserva Legal','Área consolidada','Vegetação nativa/remanescente','Uso restrito','Nascentes','Hidrografia')
    area_rows=[]
    for label in labels:
        x=summary.get(label)
        if not x:continue
        area=float(x.get('area_unique_ha') or 0);n=int(x.get('occurrence_count') or 0)
        area_rows.append([label,f'{area:.4f} ha • {n} interseção(ões)'])
        env.setdefault('layer_rows',[]).append([f'CAR — {label}',f'{area:.4f} ha • {n} ocorrência(s)','SICAR/WFS'])
    if area_rows:
        fields=list(car.get('fields') or [])
        fields += [[f'Composição CAR — {a}',b] for a,b in area_rows]
        car['fields']=fields
    status='CONSULTADA' if details.get('ok') else 'INDISPONÍVEL'
    _append_source(payload,'SICAR — composição interna do imóvel',f"APP, Reserva Legal, área consolidada, vegetação/remanescentes e demais camadas ambientais. {len(details.get('selected_layers') or [])} camada(s) selecionada(s); {len(area_rows)} categoria(s) com resultado espacial nesta emissão.",status,'ok' if details.get('ok') else 'attention')
    return payload


def _patch_soilgrids(payload:dict,soil:dict):
    prod=payload.setdefault('productive',{});existing=list(prod.get('soil_rows') or [])
    rows=[]
    for x in soil.get('rows') or []:
        if x.get('ok'):
            unit=(' '+x.get('unit')) if x.get('unit') else ''
            rows.append([f"{x.get('label')} — 0–5 cm",f"{x.get('value')}{unit}"])
    if rows:
        prod['soil_rows']=rows+[['Leitura',soil.get('note')]]+existing
    status='CONSULTADA' if soil.get('ok') and not soil.get('partial') else ('PARCIAL' if soil.get('successful_properties') else 'INDISPONÍVEL')
    _append_source(payload,'ISRIC SoilGrids — composição físico-química',f"{soil.get('successful_properties',0)}/{soil.get('requested_properties',7)} propriedades responderam: argila, areia, silte, pH, carbono orgânico, CTC e nitrogênio no horizonte 0–5 cm. Produto em grade 250 m; não é análise laboratorial.",status,'ok' if status=='CONSULTADA' else 'attention')
    return payload


def _patch_safras(payload:dict,safras:dict):
    agro=payload.setdefault('agropecuaria',{});screen=agro.setdefault('property_screening',{});checks=screen.setdefault('checks',[])
    products=safras.get('products') or []
    compact=[]
    for p in products[:8]:
        metrics=[]
        for m in (p.get('metrics') or [])[:3]:
            unit=f" {m.get('unit')}" if m.get('unit') else ''
            metrics.append(f"{_s(m.get('measure'),'indicador')}: {m.get('value')}{unit}")
        compact.append(f"{p.get('product')} ({'; '.join(metrics)})")
    checks.append({'factor':'Safras e culturas do município','scope':'município — contexto regional','status':'consultada' if safras.get('ok') else 'indisponível','value':compact[:6] or 'sem série utilizável'})
    _append_source(payload,'IBGE/SIDRA — Produção Agrícola Municipal (PAM)',safras.get('note') or 'Contexto municipal de culturas, área, produção e rendimento.','CONSULTADA' if safras.get('ok') else 'INDISPONÍVEL','ok' if safras.get('ok') else 'attention')
    return payload


def _patch_aerodromes(payload:dict,aero:dict):
    agro=payload.setdefault('agropecuaria',{});checks=agro.setdefault('property_screening',{}).setdefault('checks',[])
    near=aero.get('nearest') or []
    text=[f"{x.get('name')} • {x.get('distance_km')} km • {x.get('municipality')}/{x.get('uf')}" for x in near[:5]]
    checks.append({'factor':'Aeródromos e logística aérea','scope':f"raio {aero.get('radius_km',50)} km",'status':'consultada' if aero.get('ok') else 'indisponível','value':text or f"{aero.get('count_within_radius',0)} aeródromo(s)"})
    _append_source(payload,'ANAC / SIROS — Aeródromos',f"{aero.get('count_within_radius') if aero.get('count_within_radius') is not None else '—'} aeródromo(s) em até {aero.get('radius_km',50)} km do centróide do imóvel.",'CONSULTADA' if aero.get('ok') else 'INDISPONÍVEL','ok' if aero.get('ok') else 'attention')
    return payload


def _patch_sif(payload:dict,sif:dict):
    agro=payload.setdefault('agropecuaria',{});agro['sif_chain']=sif;checks=agro.setdefault('property_screening',{}).setdefault('checks',[])
    checks.append({'factor':'Cadeia agroindustrial SIF','scope':'município/UF','status':'consultada' if sif.get('ok') else 'indisponível','value':f"{sif.get('count',0)} estabelecimento(s) SIF localizado(s) pelo filtro municipal/UF"})
    _append_source(payload,'MAPA / SIGSIF — Estabelecimentos registrados no SIF',f"{sif.get('count',0)} estabelecimento(s) retornado(s) para o município/UF. O filtro é logístico/regional e não comprova vínculo com a fazenda.",'CONSULTADA' if sif.get('ok') else 'INDISPONÍVEL','ok' if sif.get('ok') else 'attention')
    return payload


def _patch_climate_full(payload:dict,result:dict,clim:dict):
    water=payload.setdefault('water',{});recent=result.get('climate_nasa') or {};rows=water.setdefault('rain_rows',[])
    if recent.get('ok'):
        rows.extend([
            ['Dias secos (< 1 mm)',recent.get('dry_days_lt_1mm')],
            ['Dias com chuva forte (≥ 20 mm)',recent.get('heavy_rain_days_ge_20mm')],
            ['Último dado climático disponível',recent.get('latest_data_date') or '—'],
        ])
    for m in (clim.get('months') or [])[:12]:
        rows.append([f"Climatologia {m.get('month')}",f"chuva {m.get('rain_mm') if m.get('rain_mm') is not None else '—'} mm • média {m.get('t_avg_c') if m.get('t_avg_c') is not None else '—'} °C • máx {m.get('t_max_c') if m.get('t_max_c') is not None else '—'} °C • mín {m.get('t_min_c') if m.get('t_min_c') is not None else '—'} °C"])
    drought=build_drought_screening(recent,clim);water['drought_screening']=drought
    if drought.get('ok'):rows.append(['Triagem de seca recente',f"{drought.get('state')} • dias secos {drought.get('dry_day_share_pct')}%"])
    _append_source(payload,'NASA POWER — climatologia mensal',clim.get('note') or 'Climatologia mensal no centróide da propriedade.','CONSULTADA' if clim.get('ok') else 'INDISPONÍVEL','ok' if clim.get('ok') else 'attention')
    return payload


def _repair_agro_keys(payload:dict,result:dict):
    # Previous profile expected Portuguese keys while the IDE engine returns English keys.
    ide=result.get('ide_layers') or {};checks=(payload.setdefault('agropecuaria',{}).setdefault('property_screening',{}).setdefault('checks',[]))
    checks=[x for x in checks if x.get('factor') not in {'Solo','Aptidão agrícola','Declividade'}]
    def val(key):
        d=ide.get(key) or {};return {'interseções':d.get('exact_count'),'área_somada_ha':d.get('intersection_area_sum_ha'),'camada':d.get('layer')}
    checks += [
        {'factor':'Solo','scope':'interseção cartográfica','status':'consultada' if (ide.get('soil') or {}).get('ok') else 'indisponível','value':val('soil')},
        {'factor':'Aptidão agrícola','scope':'interseção cartográfica','status':'consultada' if (ide.get('aptitude') or {}).get('ok') else 'indisponível','value':val('aptitude')},
        {'factor':'Declividade','scope':'interseção cartográfica','status':'consultada' if (ide.get('slope') or {}).get('ok') else 'indisponível','value':val('slope')},
    ]
    payload['agropecuaria']['property_screening']['checks']=checks
    return payload


async def _extras(result:dict,car_code:str,out_dir:Path):
    car=result.get('car') or {};geom=car.get('geometry');bbox=car.get('bbox');props=car.get('properties') or {}
    return await asyncio.gather(
        build_satellite_property_image(geom,out_dir/'satellite_property.jpg'),
        query_groundwater(geom,20.0),
        query_safras(car_code),
        asyncio.to_thread(query_sicar_details,geom,bbox,8),
        asyncio.to_thread(query_aerodromes_anac,geom,50.0,12),
        asyncio.to_thread(query_soilgrids_wcs,geom),
        asyncio.to_thread(query_climatology_nasa,geom),
        query_sif_establishments(props.get('municipio'),props.get('uf'),30),
        return_exceptions=True
    )


def _safe_extra(value,source):
    if isinstance(value,Exception):return {'ok':False,'source':source,'detail':f'{type(value).__name__}:{str(value)[:220]}'}
    return value if isinstance(value,dict) else {'ok':False,'source':source,'detail':'invalid_result'}


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ');safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_');report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id;out_dir.mkdir(parents=True,exist_ok=True)
    name,name_source=_best_property_name(result)
    technical_map=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    try:extras=asyncio.run(_extras(result,car_code,out_dir))
    except Exception as e:extras=[{'ok':False,'detail':f'parallel:{type(e).__name__}:{str(e)[:180]}'}]*8
    sat,gw,safras,car_details,aero,soil,clim,sif=[_safe_extra(x,s) for x,s in zip(extras,['Sentinel-2','SGB/SIAGAS','IBGE/PAM','SICAR detalhado','ANAC','SoilGrids','NASA climatology','MAPA/SIGSIF'])]
    _stamp_image(technical_map,name,'technical')
    if sat.get('ok') and sat.get('path'):_stamp_image(sat.get('path'),name,'satellite')
    primary=sat.get('path') if sat.get('ok') and sat.get('path') else technical_map
    payload=build_live_payload(result,report_id,now.isoformat(),primary)
    payload=_patch_identity(payload,result,name,name_source)
    payload=v12._patch_satellite(payload,sat,technical_map)
    payload=v11._patch_autos(payload,result);payload=v11._patch_fire(payload,result);payload=v11._patch_car_limit(payload);payload=v11._patch_constraints(payload,result);payload=v11._patch_extra_territorial(payload,result);payload=v11._patch_water(payload,result);payload=v12._patch_groundwater(payload,gw);payload=v11._patch_pivots(payload,result);payload=v11._patch_climate(payload,result);payload=v11._patch_minerals(payload,result);payload=v11._patch_ide(payload,result);payload=v11._patch_prodes_lens(payload,result);payload=v11._patch_restricted_parity(payload);payload=v11._patch_agro(payload,result,car_code)
    payload=_repair_agro_keys(payload,result)
    payload=_patch_car_details(payload,car_details);payload=_patch_soilgrids(payload,soil);payload=_patch_safras(payload,safras);payload=_patch_aerodromes(payload,aero);payload=_patch_sif(payload,sif);payload=_patch_climate_full(payload,result,clim)
    payload=v11._dedupe_sources(payload);payload=v11._final_truth_guard(payload,result)
    payload['narrative']=build_narrative(payload)
    one=payload['narrative'].get('one_sentence') or ''
    payload['narrative']['one_sentence']=f'{name} — {one}' if name and name not in one else one
    payload['quick_read']=payload['narrative']['one_sentence']
    payload['source_version']='Raio-X Territorial V13 • identidade da fazenda + duas imagens contornadas + CAR interno + solo físico-químico SoilGrids + climatologia + safras + aeródromos + SIF + V12 completo.'
    payload_path=out_dir/'payload.json';payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf';digest=build_premium_property_report_v6(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(primary),'technical_map_path':str(technical_map),'satellite_image_path':sat.get('path') if sat.get('ok') else None,'property_name':name,'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}


print('RX_LIVE_REPORT_ADAPTER=V13_COMPLETE_PUBLIC_EXPANSION',flush=True)

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
from report_engine_v4 import build_premium_property_report_v4
from report_narrative import build_narrative


def _s(v, default='-'):
    return default if v is None or v == '' else str(v)


def _uniq(seq):
    out=[]; seen=set()
    for x in seq:
        t=str(x).strip()
        if not t or t in seen: continue
        seen.add(t); out.append(t)
    return out


def _sample_props(layer):
    return [(x.get('properties') or {}) for x in (layer.get('samples') or []) if isinstance(x,dict)]


def _layer_status(layer):
    if layer.get('ok') is True:
        return 'CONSULTADA','ok'
    detail=str(layer.get('detail') or '')
    if 'payload_guard' in detail or layer.get('capped') or layer.get('truncated_possible'):
        return 'PARCIAL','attention'
    if layer:
        return 'INDISPONÍVEL','attention'
    return 'NÃO EXECUTADA','neutral'


def _patch_ide(payload:dict,result:dict):
    ide=result.get('ide_layers') or {}
    prod=payload.setdefault('productive',{})
    env=payload.setdefault('environment',{})

    soil=ide.get('soil') or {}; st,level=_layer_status(soil); props=_sample_props(soil)
    labels=_uniq([p.get('legenda') or p.get('classe') or p.get('nome') for p in props if p])
    if soil.get('ok'):
        prod['soil_rows']=[['Classes pedológicas identificadas','; '.join(labels) if labels else f"{soil.get('exact_count',0)} feição(ões) intersectante(s)"],['Interseções exatas',soil.get('exact_count',0)],['Camada',soil.get('layer') or '-']]
    else:
        prod['soil_rows']=[['Solo',st],['Motivo',soil.get('detail') or 'A fonte não retornou dados nesta emissão.']]
    payload['sources'].append({'name':'IDE-Sisema / Mapa de Solos','description':f"Camada {soil.get('layer') or '-'}; {soil.get('exact_count',0)} interseção(ões) exata(s). {soil.get('detail') or ''}".strip(),'status':st,'level':level})

    apt=ide.get('aptitude') or {}; st,level=_layer_status(apt); props=_sample_props(apt); arows=[]
    for p in props[:8]:
        desc=p.get('legenda_ap') or p.get('legenda') or p.get('apt_comp1') or p.get('simb_apt')
        if desc: arows.append([desc, p.get('area_ha') or f"interseção {apt.get('exact_count',0)}"])
    prod['aptitude_rows']=arows or [[st,apt.get('detail') or 'Sem classe retornada']]
    payload['sources'].append({'name':'IDE-Sisema / Aptidão Agrícola','description':f"Camada {apt.get('layer') or '-'}; {apt.get('exact_count',0)} interseção(ões) exata(s). {apt.get('detail') or ''}".strip(),'status':st,'level':level})

    slope=ide.get('slope') or {}; st,level=_layer_status(slope); props=_sample_props(slope); slope_values=[]
    for p in props[:6]:
        for key in ('classe','decliv','intervalo','legenda','gridcode'):
            if p.get(key) not in (None,''): slope_values.append(f"{key}: {p.get(key)}")
    if slope.get('ok'):
        sval='; '.join(_uniq(slope_values)) or f"{slope.get('exact_count',0)} feição(ões) intersectante(s)"; snote='IDE-Sisema'
    elif 'payload_guard' in str(slope.get('detail') or ''):
        sval='PARCIAL'; snote='A camada respondeu acima do limite seguro de payload; o resultado não foi interpretado como ausência de declividade.'
    else:
        sval=st; snote=slope.get('detail') or 'Fonte indisponível.'
    terrain=prod.get('terrain_kpis') or []
    terrain=[x for x in terrain if 'decliv' not in str(x.get('label') or '').lower()]
    terrain.insert(0,{'label':'Declividade','value':sval,'note':snote,'status':st,'level':level})
    prod['terrain_kpis']=terrain[:4]
    payload['sources'].append({'name':'IDE-Sisema / Declividade','description':f"Camada {slope.get('layer') or '-'}; {slope.get('detail') or 'consulta espacial executada.'}",'status':st,'level':level})

    ero=ide.get('erosion') or {}; st,level=_layer_status(ero); props=_sample_props(ero)
    classes=_uniq([p.get('indicador') or p.get('classe') or p.get('legenda') for p in props if p])
    prod['erosion_rows']=[[x,f"interseção exata; {ero.get('exact_count',0)} feição(ões) no total"] for x in classes] if classes else [[st,ero.get('detail') or '-']]
    payload['sources'].append({'name':'IDE-Sisema / Risco Potencial de Erosão','description':f"Camada {ero.get('layer') or '-'}; classes: {', '.join(classes) if classes else '-'}. {ero.get('detail') or ''}".strip(),'status':st,'level':level})

    cover_key=next((k for k in ide if str(k).startswith('landcover')),None); cover=ide.get(cover_key) or {}; st,level=_layer_status(cover); props=_sample_props(cover)
    classes=_uniq([p.get('classe_') or p.get('classe') or p.get('legenda') for p in props if p])
    prod['landcover_rows']=[[x,f"camada {cover.get('layer') or '-'}"] for x in classes] if classes else [[st,cover.get('detail') or '-']]
    payload['sources'].append({'name':'IDE-Sisema / Uso e Cobertura','description':f"Camada {cover.get('layer') or '-'}; {cover.get('exact_count',0)} interseção(ões) exata(s). {cover.get('detail') or ''}".strip(),'status':st,'level':level})

    rl=ide.get('rl_recomposition') or {}; st,level=_layer_status(rl); props=_sample_props(rl); rl_rows=[]
    for p in props[:8]:
        code=p.get('cod_imovel') or '-'; area=p.get('rec_rl_ha')
        rl_rows.append(['Recomposição de Reserva Legal declarada',f"{_s(area,'-')} ha • CAR {code}",rl.get('layer') or 'IDE-Sisema'])
    if rl.get('ok'): env.setdefault('layer_rows',[]).extend(rl_rows[:4])
    payload['sources'].append({'name':'IDE-Sisema / Recomposição de Reserva Legal declarada','description':f"Camada {rl.get('layer') or '-'}; {rl.get('exact_count',0)} interseção(ões) exata(s). Confirmar o código CAR de cada feição antes de atribuir ao imóvel selecionado.",'status':st,'level':level})

    cleaned=[]
    for x in payload.get('attention_points') or []:
        low=str(x).lower()
        if 'solo, aptidão' in low and ('precis' in low or 'ainda' in low): continue
        cleaned.append(x)
    payload['attention_points']=cleaned
    for row in payload.get('compliance') or []:
        if str(row.get('label') or '').lower()=='solo / aptidão':
            row['text']=f"Solo: {_layer_status(soil)[0]} • Aptidão: {_layer_status(apt)[0]} • Declividade: {_layer_status(slope)[0]}"
            row['badge']='CONSULTADO' if soil.get('ok') and apt.get('ok') else 'PARCIAL'; row['level']='ok' if row['badge']=='CONSULTADO' else 'attention'
    for row in (payload.get('conclusion') or {}).get('categories') or []:
        if row.get('label')=='Produtivo':
            row['text']=f"Solo {_layer_status(soil)[0].lower()}, aptidão {_layer_status(apt)[0].lower()}, erosão {_layer_status(ero)[0].lower()} e declividade {_layer_status(slope)[0].lower()}."
            row['risk']='ATENÇÃO' if not (soil.get('ok') and apt.get('ok')) else 'TRIAGEM DISPONÍVEL'; row['level']='attention' if row['risk']=='ATENÇÃO' else 'info'
    return payload


def _dedupe_sources(payload:dict):
    ordered=[]; pos={}
    for src in payload.get('sources') or []:
        key=str(src.get('name') or '').strip().lower()
        if not key: continue
        if key in pos: ordered[pos[key]]=src
        else: pos[key]=len(ordered); ordered.append(src)
    payload['sources']=ordered
    return payload


def _final_truth_guard(payload:dict,result:dict):
    replacements={'outorgas':result.get('water_mg'),'pivôs':result.get('pivots_ana'),'clima':result.get('climate_nasa')}
    for src in payload.get('sources') or []:
        n=str(src.get('name') or '').lower()
        for term,obj in replacements.items():
            if term in n and isinstance(obj,dict) and obj.get('ok') is True and str(src.get('status') or '').upper() in {'NÃO CONSULTADA','NÃO CONSULTADO','NAO CONSULTADA','NAO CONSULTADO'}:
                src['status']='CONSULTADA'; src['level']='ok'
    payload.setdefault('interpretation_rules',[]).append('O status de cada fonte corresponde a esta emissão: CONSULTADA, PARCIAL, INDISPONÍVEL, RESTRITA ou NÃO EXECUTADA. Nenhum desses estados é convertido automaticamente em ausência de ocorrência.')
    return payload


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_'); report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id; out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result); payload=_patch_fire(payload,result); payload=_patch_car_limit(payload); payload=_patch_constraints(payload,result); payload=_patch_water(payload,result); payload=_patch_pivots(payload,result); payload=_patch_climate(payload,result); payload=_patch_minerals(payload,result); payload=_patch_ide(payload,result)
    payload=_dedupe_sources(payload); payload=_final_truth_guard(payload,result)
    payload['narrative']=build_narrative(payload)
    payload['quick_read']=payload['narrative']['one_sentence']
    payload['source_version']='Raio-X Territorial V9 • relatório fluido, acessível e rastreável • matriz de cobertura por fonte • layout automático sem sobreposição.'
    payload_path=out_dir/'payload.json'; payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf'; digest=build_premium_property_report_v4(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

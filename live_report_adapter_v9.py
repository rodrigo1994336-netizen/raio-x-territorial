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
from prodes_lens import derive_prodes_lens


def _s(v, default='-'):
    return default if v is None or v == '' else str(v)


def _uniq(seq):
    out=[];seen=set()
    for x in seq:
        t=str(x).strip()
        if not t or t in seen:continue
        seen.add(t);out.append(t)
    return out


def _samples(layer):
    return [x for x in (layer.get('samples') or []) if isinstance(x,dict)]


def _sample_props(layer):
    return [(x.get('properties') or {}) for x in _samples(layer)]


def _layer_status(layer):
    if layer.get('ok') is True:return 'CONSULTADA','ok'
    detail=str(layer.get('detail') or '')
    if 'payload_guard' in detail or layer.get('capped') or layer.get('truncated_possible'):return 'PARCIAL','attention'
    if layer:return 'INDISPONÍVEL','attention'
    return 'NÃO EXECUTADA','neutral'


def _fmt_area_pct(sample):
    ha=sample.get('intersection_area_ha');pct=sample.get('intersection_pct_car')
    if ha is not None and pct is not None:
        try:return f"{float(ha):.2f} ha • {float(pct):.1f}% do imóvel"
        except Exception:pass
    if ha is not None:
        try:return f"{float(ha):.2f} ha"
        except Exception:pass
    return 'interseção identificada'


def _patch_prodes_lens(payload:dict,result:dict):
    env=payload.setdefault('environment',{});pd=env.setdefault('prodes',{})
    props=(result.get('car') or {}).get('properties') or {}
    lens=derive_prodes_lens(result.get('prodes') or {},props.get('m_fiscal'))
    pd['lens']=lens
    hist=lens.get('historical') or {};post=lens.get('post_2019_07_31') or {};credit=lens.get('credit_screening') or {}
    original=list(pd.get('rows') or [])
    lens_rows=[
        ['Histórico PRODES completo',f"{hist.get('occurrence_count',0)} ocorrência(s) • {hist.get('area_unique_ha',0):.6f} ha únicos • anos: {', '.join(str(x) for x in hist.get('years') or []) or '-'}"],
        ['Recorte pós-31/07/2019',f"{post.get('occurrence_count',0)} ocorrência(s) • soma aprox. {post.get('area_sum_ha',0):.6f} ha • anos: {', '.join(str(x) for x in post.get('years') or []) or '-'}"],
        ['Triagem para crédito rural',credit.get('reading') or '-'],
        ['Base regulatória',credit.get('regulatory_basis') or '-'],
    ]
    pd['rows']=lens_rows+original
    pd['meaning']=lens.get('explanation')+' '+(pd.get('meaning') or '')
    payload['credit_screening']=credit
    payload.setdefault('interpretation_rules',[]).append('PRODES é apresentado em duas lentes: histórico completo e recorte posterior a 31/07/2019 para triagem de crédito rural. Uma detecção não é convertida automaticamente em impedimento.')
    return payload


def _patch_ide(payload:dict,result:dict):
    ide=result.get('ide_layers') or {};prod=payload.setdefault('productive',{});env=payload.setdefault('environment',{})

    soil=ide.get('soil') or {};st,level=_layer_status(soil);props=_sample_props(soil)
    labels=_uniq([p.get('legenda') or p.get('classe') or p.get('nome') for p in props if p])
    if soil.get('ok'):
        prod['soil_rows']=[['Classes pedológicas identificadas','; '.join(labels) if labels else f"{soil.get('exact_count',0)} feição(ões) intersectante(s)"],['Interseções exatas',soil.get('exact_count',0)],['Área intersectada somada',f"{float(soil.get('intersection_area_sum_ha') or 0):.2f} ha"],['Camada',soil.get('layer') or '-']]
    else:prod['soil_rows']=[['Solo',st],['Motivo',soil.get('detail') or 'A fonte não retornou dados nesta emissão.']]
    payload['sources'].append({'name':'IDE-Sisema / Mapa de Solos','description':f"Camada {soil.get('layer') or '-'}; {soil.get('exact_count',0)} interseção(ões) exata(s). {soil.get('detail') or ''}".strip(),'status':st,'level':level})

    apt=ide.get('aptitude') or {};st,level=_layer_status(apt);arows=[]
    for sample in _samples(apt)[:8]:
        p=sample.get('properties') or {};desc=p.get('legenda_ap') or p.get('legenda') or p.get('apt_comp1') or p.get('simb_apt') or p.get('classe')
        if desc:arows.append([desc,_fmt_area_pct(sample)])
    prod['aptitude_rows']=arows or [[st,apt.get('detail') or 'Sem classe retornada']]
    if apt.get('ok') and arows:
        terrain=prod.get('terrain_kpis') or []
        terrain=[x for x in terrain if str(x.get('label') or '').lower()!='aptidão']
        terrain.append({'label':'Aptidão','value':f"{len(arows)} classe(s)",'note':'área e % calculados sobre o CAR','status':'CONSULTADA','level':'ok'})
        prod['terrain_kpis']=terrain[:4]
    payload['sources'].append({'name':'IDE-Sisema / Aptidão Agrícola','description':f"Camada {apt.get('layer') or '-'}; {apt.get('exact_count',0)} interseção(ões) exata(s), com área e percentual calculados localmente. {apt.get('detail') or ''}".strip(),'status':st,'level':level})

    slope=ide.get('slope') or {};st,level=_layer_status(slope);props=_sample_props(slope);slope_values=[]
    for p in props[:6]:
        for key in ('classe','decliv','intervalo','legenda','gridcode'):
            if p.get(key) not in (None,''):slope_values.append(f"{key}: {p.get(key)}")
    if slope.get('ok'):
        sval='; '.join(_uniq(slope_values)) or f"{slope.get('exact_count',0)} feição(ões) intersectante(s)";snote='IDE-Sisema'
    elif 'payload_guard' in str(slope.get('detail') or ''):
        sval='PARCIAL';snote='A camada respondeu acima do limite seguro de payload; o resultado não foi interpretado como ausência de declividade.'
    else:sval=st;snote=slope.get('detail') or 'Fonte indisponível.'
    terrain=prod.get('terrain_kpis') or [];terrain=[x for x in terrain if 'decliv' not in str(x.get('label') or '').lower()]
    terrain.insert(0,{'label':'Declividade','value':sval,'note':snote,'status':st,'level':level});prod['terrain_kpis']=terrain[:4]
    payload['sources'].append({'name':'IDE-Sisema / Declividade','description':f"Camada {slope.get('layer') or '-'}; {slope.get('detail') or 'consulta espacial executada.'}",'status':st,'level':level})

    ero=ide.get('erosion') or {};st,level=_layer_status(ero);props=_sample_props(ero);classes=_uniq([p.get('indicador') or p.get('classe') or p.get('legenda') for p in props if p])
    prod['erosion_rows']=[[x,f"interseção exata; {ero.get('exact_count',0)} feição(ões) no total"] for x in classes] if classes else [[st,ero.get('detail') or '-']]
    payload['sources'].append({'name':'IDE-Sisema / Risco Potencial de Erosão','description':f"Camada {ero.get('layer') or '-'}; classes: {', '.join(classes) if classes else '-'}. {ero.get('detail') or ''}".strip(),'status':st,'level':level})

    cover_key=next((k for k in ide if str(k).startswith('landcover')),None);cover=ide.get(cover_key) or {};st,level=_layer_status(cover);props=_sample_props(cover);classes=_uniq([p.get('classe_') or p.get('classe') or p.get('legenda') for p in props if p])
    prod['landcover_rows']=[[x,f"camada {cover.get('layer') or '-'}"] for x in classes] if classes else [[st,cover.get('detail') or '-']]
    payload['sources'].append({'name':'IDE-Sisema / Uso e Cobertura','description':f"Camada {cover.get('layer') or '-'}; {cover.get('exact_count',0)} interseção(ões) exata(s). {cover.get('detail') or ''}".strip(),'status':st,'level':level})

    rl=ide.get('rl_recomposition') or {};st,level=_layer_status(rl);rl_rows=[]
    for sample in _samples(rl)[:8]:
        p=sample.get('properties') or {};code=p.get('cod_imovel') or '-';area=p.get('rec_rl_ha')
        rl_rows.append(['Recomposição de Reserva Legal declarada',f"{_s(area,'-')} ha • CAR {code}",rl.get('layer') or 'IDE-Sisema'])
    if rl.get('ok'):env.setdefault('layer_rows',[]).extend(rl_rows[:4])
    payload['sources'].append({'name':'IDE-Sisema / Recomposição de Reserva Legal declarada','description':f"Camada {rl.get('layer') or '-'}; {rl.get('exact_count',0)} interseção(ões) exata(s). Confirmar o código CAR de cada feição antes de atribuir ao imóvel selecionado.",'status':st,'level':level})

    cleaned=[]
    for x in payload.get('attention_points') or []:
        low=str(x).lower()
        if 'solo, aptidão' in low and ('precis' in low or 'ainda' in low):continue
        cleaned.append(x)
    payload['attention_points']=cleaned
    for row in payload.get('compliance') or []:
        if str(row.get('label') or '').lower()=='solo / aptidão':
            row['text']=f"Solo: {_layer_status(soil)[0]} • Aptidão: {_layer_status(apt)[0]} • Declividade: {_layer_status(slope)[0]}";row['badge']='CONSULTADO' if soil.get('ok') and apt.get('ok') else 'PARCIAL';row['level']='ok' if row['badge']=='CONSULTADO' else 'attention'
    for row in (payload.get('conclusion') or {}).get('categories') or []:
        if row.get('label')=='Produtivo':
            row['text']=f"Solo {_layer_status(soil)[0].lower()}, aptidão {_layer_status(apt)[0].lower()}, erosão {_layer_status(ero)[0].lower()} e declividade {_layer_status(slope)[0].lower()}.";row['risk']='ATENÇÃO' if not (soil.get('ok') and apt.get('ok')) else 'TRIAGEM DISPONÍVEL';row['level']='attention' if row['risk']=='ATENÇÃO' else 'info'
    return payload


def _dedupe_sources(payload:dict):
    ordered=[];pos={}
    for src in payload.get('sources') or []:
        key=str(src.get('name') or '').strip().lower()
        if not key:continue
        if key in pos:ordered[pos[key]]=src
        else:pos[key]=len(ordered);ordered.append(src)
    payload['sources']=ordered;return payload


def _final_truth_guard(payload:dict,result:dict):
    replacements={'outorgas':result.get('water_mg'),'pivôs':result.get('pivots_ana'),'clima':result.get('climate_nasa')}
    for src in payload.get('sources') or []:
        n=str(src.get('name') or '').lower()
        for term,obj in replacements.items():
            if term in n and isinstance(obj,dict) and obj.get('ok') is True and str(src.get('status') or '').upper() in {'NÃO CONSULTADA','NÃO CONSULTADO','NAO CONSULTADA','NAO CONSULTADO'}:
                src['status']='CONSULTADA';src['level']='ok'
    payload.setdefault('interpretation_rules',[]).append('O status de cada fonte corresponde a esta emissão: CONSULTADA, PARCIAL, INDISPONÍVEL, RESTRITA ou NÃO EXECUTADA. Nenhum desses estados é convertido automaticamente em ausência de ocorrência.')
    return payload


def generate_live_report(result:dict,car_code:str):
    now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ');safe=''.join(ch for ch in car_code.upper() if ch.isalnum() or ch in '-_');report_id=f'RX-{stamp}-{safe[-8:]}'
    out_dir=REPORT_DIR/report_id;out_dir.mkdir(parents=True,exist_ok=True)
    map_path=build_technical_map(result,out_dir/'map_environment.png',include_prodes=True)
    payload=build_live_payload(result,report_id,now.isoformat(),map_path)
    payload=_patch_autos(payload,result);payload=_patch_fire(payload,result);payload=_patch_car_limit(payload);payload=_patch_constraints(payload,result);payload=_patch_water(payload,result);payload=_patch_pivots(payload,result);payload=_patch_climate(payload,result);payload=_patch_minerals(payload,result);payload=_patch_ide(payload,result);payload=_patch_prodes_lens(payload,result)
    payload=_dedupe_sources(payload);payload=_final_truth_guard(payload,result)
    payload['narrative']=build_narrative(payload);payload['quick_read']=payload['narrative']['one_sentence']
    payload['source_version']='Raio-X Territorial V11 • benchmark same-CAR • PRODES histórico + triagem pós-31/07/2019 • aptidão em ha/% • narrativa dinâmica por fonte.'
    payload_path=out_dir/'payload.json';payload_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    pdf_path=out_dir/'raio_x_territorial.pdf';digest=build_premium_property_report_v4(pdf_path,payload)
    return {'report_id':report_id,'pdf_path':str(pdf_path),'payload_path':str(payload_path),'map_path':str(map_path),'sha256':digest,'bytes':pdf_path.stat().st_size,'payload_sha256':sha256(payload_path.read_bytes()).hexdigest()}

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable

from report_engine_v5 import (
    A4, LINE, S, _s, P, _section, _bullets, _info, _kpis, _callout, _image,
    _source_stats, _sources_table, _decision_columns, _header_footer
)


def _agro_rows(agro: dict[str, Any]) -> list[list[str]]:
    rows=[]
    ppm=agro.get('livestock_municipal') or {}
    for x in (ppm.get('series') or [])[:16]:
        delta=x.get('delta_pct')
        trend='' if delta is None else f" · variação {float(delta):+.2f}%"
        rows.append([
            _s(x.get('herd'),'Rebanho'),
            f"{_s(x.get('value'),'—')} cabeças · {_s(x.get('period'),'ano recente')}{trend}",
            'MUNICÍPIO · IBGE/PPM'
        ])
    dairy=agro.get('dairy_cows_municipal') or {}
    if dairy.get('ok'):
        delta=dairy.get('delta_pct')
        trend='' if delta is None else f" · variação {float(delta):+.2f}%"
        rows.append(['Vacas ordenhadas',f"{_s(dairy.get('value'),'—')} cabeças · {_s(dairy.get('period'),'ano recente')}{trend}",'MUNICÍPIO · IBGE/PPM'])
    products=agro.get('animal_products_municipal') or {}
    for x in (products.get('products') or [])[:12]:
        unit=f" {_s(x.get('unit'),'')}" if x.get('unit') else ''
        rows.append([_s(x.get('product'),'Produção animal'),f"{_s(x.get('value'),'—')}{unit} · {_s(x.get('period'),'ano recente')}",'MUNICÍPIO · IBGE/PPM'])
    return rows


def _agro_property_rows(agro: dict[str, Any]) -> list[list[str]]:
    rows=[]
    screening=agro.get('property_screening') or {}
    for x in (screening.get('checks') or [])[:18]:
        value=x.get('value')
        if isinstance(value,dict):
            text=' · '.join(f'{k}: {v}' for k,v in value.items() if v is not None)
        elif isinstance(value,list):
            text=' | '.join(str(v) for v in value[:4])
        else:
            text=_s(value,'—')
        rows.append([_s(x.get('factor'),'Indicador'),text[:520],f"{_s(x.get('scope'),'escopo informado')} · {_s(x.get('status'),'status não informado')}"])
    return rows


def _groundwater_rows(gw:dict[str,Any]) -> list[list[str]]:
    rows=[]
    for x in (gw.get('dominant_aquifers') or [])[:8]:
        rows.append([_s(x.get('name'),'Aquífero'),f"{_s(x.get('count'),0)} registro(s)",'SGB/SIAGAS'])
    return rows


def build_premium_property_report_v6(path:str|Path,payload:dict[str,Any])->str:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=16*mm,rightMargin=16*mm,topMargin=22*mm,bottomMargin=18*mm,title='Raio-X Territorial',author='Raio-X Territorial')
    doc.rx_property=payload.get('property') or {}
    p=payload.get('property') or {}; car=payload.get('car') or {}; land=payload.get('land') or {}; env=payload.get('environment') or {}; enf=payload.get('enforcement') or {}; mining=payload.get('mining') or {}; prod=payload.get('productive') or {}; water=payload.get('water') or {}; mon=payload.get('monitoring') or {}; con=payload.get('conclusion') or {}; nar=payload.get('narrative') or {}; sources=payload.get('sources') or []; agro=payload.get('agropecuaria') or {}; sat=payload.get('satellite_imagery') or {}; gw=water.get('groundwater') or {}
    story=[]

    # 1 — capa e leitura rápida
    story += [Spacer(1,2*mm),Paragraph('RAIO-X TERRITORIAL',S['cover']),P('Dossiê territorial, ambiental, produtivo, pecuário e mineral — feito para ser entendido, não apenas arquivado.','small'),Spacer(1,4*mm)]
    cover_image=payload.get('satellite_image_path') or payload.get('map_image_path') or payload.get('car_map_image_path')
    cover_caption=(f"Imagem orbital real Sentinel-2 da propriedade · cena {_s(sat.get('date'),'data não informada')} · nuvens {_s(sat.get('cloud_cover_pct'),'—')}%. O limite amarelo corresponde ao CAR analisado." if sat.get('ok') else 'Mapa técnico do limite analisado. A imagem orbital não respondeu nesta emissão.')
    story += _image(cover_image,cover_caption)
    story += [Spacer(1,4*mm),_kpis([
        ('Área',f"{_s(p.get('area_ha'))} ha",f"{_s(p.get('municipality'))}/{_s(p.get('uf'))}",'CONSULTADA'),
        ('CAR',_s(car.get('status'),'N/D'),_s(car.get('analysis_status'),''),_s(car.get('status'),'CONSULTADA')),
        ('Risco geral',_s(con.get('overall_risk'),'NÃO CLASSIFICADO'),'triagem consolidada',_s(con.get('overall_risk'),'ATENÇÃO')),
        ('Fontes',str(_source_stats(sources)['ok']),'consultadas nesta emissão','CONSULTADA')
    ]),Spacer(1,5*mm),_callout('EM UMA FRASE',nar.get('one_sentence') or payload.get('quick_read') or con.get('overall_reason') or 'Análise em processamento.','info'),Spacer(1,5*mm),Paragraph('O que encontramos',S['h2'])]
    story += _bullets(nar.get('what_we_found') or [],8)
    story += [Paragraph('Por que isso importa',S['h2'])]+_bullets(nar.get('why_it_matters') or [],6)
    story.append(PageBreak())

    # 2 — decisão
    story += _section('A leitura que interessa para a decisão','Primeiro vem o que muda a decisão; depois, a evidência técnica que sustenta cada conclusão.')
    story.append(_decision_columns(nar))
    story += [Spacer(1,6*mm),Paragraph('O que fazer agora',S['h2'])]+_bullets(nar.get('next_steps') or con.get('diligence') or [],12)
    story += [Spacer(1,4*mm),_callout('REGRA DO RAIO-X','Fonte indisponível vira ponto cego. O sistema nunca transforma “não consegui consultar” em “não existe problema”.','attention'),PageBreak()]

    # 3 — cobertura
    stats=_source_stats(sources)
    story += _section('Cobertura das fontes','Uma fonte só conta como consultada quando efetivamente respondeu nesta emissão.')
    story.append(_kpis([('Consultadas',stats['ok'],'responderam','CONSULTADA'),('Parciais',stats['partial'],'retorno incompleto','PARCIAL'),('Indisponíveis',stats['unavailable'],'falha temporária','INDISPONÍVEL'),('Outras',stats['other'],'restritas/preparadas','ATENÇÃO')]))
    story += [Spacer(1,5*mm),_sources_table(sources),PageBreak()]

    # 4 — CAR e fundiário
    story += _section('CAR, cadastro e situação fundiária','Cadastro ambiental, georreferenciamento e propriedade registral respondem perguntas diferentes.')
    story.append(_info(car.get('fields') or [],[60*mm,105*mm],['Campo','Resultado']))
    story += [Spacer(1,5*mm),Paragraph('SIGEF, SNCI, CCIR e matrícula',S['h2']),_info(land.get('certifications') or [],[28*mm,35*mm,24*mm,78*mm],['Base','Situação','Registros','Leitura'])]
    if land.get('matrix'): story += [Spacer(1,4*mm),_info(land.get('matrix'),[35*mm,40*mm,28*mm,62*mm],['Base/campo','Resultado','Área','O que significa'])]
    ev=land.get('evidence') or {}; story += [Spacer(1,4*mm),_callout('EVIDÊNCIA DOMINIAL',f"{_s(ev.get('score'),'NÃO CLASSIFICADA')} — {_s(ev.get('text'),'')}",'attention'),PageBreak()]

    # 5 — ambiental
    pd=env.get('prodes') or {}
    story += _section('Ambiental e fiscalização','Desmatamento, embargos, autos e restrições são apresentados com área, período e fonte sempre que o dado permitir.')
    story.append(_kpis([('PRODES',_s(pd.get('count'),0),f"{_s(pd.get('area_ha'),0)} ha",_s(pd.get('status'),'CONSULTADA')),('Embargos',_s(enf.get('embargo_count'),0),'IBAMA + ICMBio',_s(enf.get('embargo_status'),'CONSULTADA')),('Autos IBAMA',_s(enf.get('auto_count'),'N/D'),_s(enf.get('fine_total_text'),''),'ATENÇÃO' if _s(enf.get('auto_count')) not in ('0','N/D','NÃO CONSULTADO') else 'CONSULTADA'),('Restrições',f"{_s(env.get('unique_problem_area_ha'),0)} ha",f"{_s(env.get('unique_problem_area_pct'),0)}% do CAR",'ATENÇÃO' if env.get('unique_problem_area_ha') else 'CONSULTADA')]))
    if payload.get('technical_map_image_path'):
        story += [Spacer(1,5*mm)]+_image(payload.get('technical_map_image_path'),'Mapa técnico do mesmo imóvel: limite CAR e interseções cartográficas usadas na análise. Não é ilustração genérica.')
    story += [Spacer(1,5*mm),Paragraph('Camadas ambientais e territoriais',S['h2']),_info(env.get('layer_rows') or [],[48*mm,70*mm,47*mm],['Camada','Resultado','Fonte'])]
    if pd.get('rows'): story += [Spacer(1,5*mm),Paragraph('PRODES — histórico e lente regulatória',S['h2']),_info(pd.get('rows'),[62*mm,103*mm],['Indicador','Resultado'])]
    story += [Spacer(1,4*mm),P(pd.get('meaning') or ''),PageBreak()]

    # 6 — mineral
    story += _section('Mineração, minerais críticos e terras raras','O Raio-X diferencia processo minerário, sinal geológico e jazida comprovada — são coisas diferentes.')
    story.append(_kpis([('Processos ANM',_s(mining.get('process_count'),0),f"{_s(mining.get('overlap_area_ha'),0)} ha",'ATENÇÃO' if mining.get('process_count') else 'CONSULTADA'),('Minerais críticos',_s(mining.get('critical_process_count'),0),', '.join(mining.get('critical_minerals') or []) or 'nenhum classificado','ATENÇÃO' if mining.get('critical_process_count') else 'CONSULTADA'),('Terras raras',_s(mining.get('rare_earth_signal'),'NÃO IDENTIFICADO'),f"processos: {_s(mining.get('rare_earth_count'),0)}",'ATENÇÃO' if _s(mining.get('rare_earth_signal')).upper()=='SIM' else 'CONSULTADA'),('SGB',_s(mining.get('rare_earth_source'),'ANM + SGB'),'camadas públicas','CONSULTADA')]))
    story += [Spacer(1,5*mm),P(mining.get('summary') or '')]
    if mining.get('critical_rows'): story += [Spacer(1,4*mm),_info(mining.get('critical_rows'),[80*mm,85*mm],['Indicador','Resultado'])]
    if mining.get('processes'): story += [Spacer(1,4*mm),Paragraph('Processos identificados',S['h2']),_info(mining.get('processes'),[27*mm,42*mm,35*mm,38*mm,23*mm],['Processo','Titular','Substância','Fase','% imóvel'])]
    story.append(PageBreak())

    # 7 — solo/produtivo
    story += _section('Solo, aptidão, relevo e uso da terra','A triagem produtiva usa a geometria do imóvel quando a fonte permite interseção espacial.')
    terrain=prod.get('terrain_kpis') or []; k=[]
    for x in terrain[:4]: k.append((_s(x.get('label')),_s(x.get('value')),_s(x.get('note'),''),_s(x.get('status') or x.get('level') or 'INFO')))
    while len(k)<4:k.append(('Dado','NÃO DISPONÍVEL','fonte ainda não respondeu','PARCIAL'))
    story.append(_kpis(k[:4]))
    if prod.get('soil_rows'): story += [Spacer(1,5*mm),Paragraph('Solo',S['h2']),_info(prod.get('soil_rows'),[75*mm,90*mm],['Indicador','Resultado'])]
    if prod.get('aptitude_rows'): story += [Spacer(1,5*mm),Paragraph('Aptidão agrícola',S['h2']),_info(prod.get('aptitude_rows'),[118*mm,47*mm],['Classe / evidência','Resultado'])]
    if prod.get('erosion_rows'): story += [Spacer(1,5*mm),Paragraph('Risco potencial de erosão',S['h2']),_info(prod.get('erosion_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    if prod.get('landcover_rows'): story += [Spacer(1,5*mm),Paragraph('Uso e cobertura',S['h2']),_info(prod.get('landcover_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    story.append(PageBreak())

    # 8 — agropecuária
    story += _section('Raio-X Agropecuário e Pecuário','Contexto regional e triagem do imóvel aparecem separados para evitar a falsa impressão de que um dado municipal pertence à fazenda.')
    municipal=_agro_rows(agro)
    if municipal:
        story += [Paragraph('Contexto pecuário do município',S['h2']),_info(municipal,[48*mm,72*mm,45*mm],['Indicador','Resultado','Escopo'])]
    else:
        story += [_callout('PECUÁRIA MUNICIPAL','IBGE/PPM não retornou série utilizável nesta emissão. Isso não significa ausência de atividade pecuária.','attention')]
    property_rows=_agro_property_rows(agro)
    story += [Spacer(1,5*mm),Paragraph('O que sabemos sobre o imóvel',S['h2']),_info(property_rows,[45*mm,76*mm,44*mm],['Fator','Resultado','Escopo / status'])]
    pasture=agro.get('pasture') or {}
    pasture_state=_s(pasture.get('state'),'não executado')
    story += [Spacer(1,5*mm),_callout('PASTAGEM / VIGOR',f"{pasture_state}. {_s(pasture.get('note'),'Métricas só são exibidas após processamento real do polígono.')}",'info' if 'ready' not in pasture_state.lower() else 'good')]
    capacity=(agro.get('property_screening') or {}).get('carrying_capacity_note')
    story += [Spacer(1,4*mm),_callout('LOTAÇÃO / CAPACIDADE DE SUPORTE',capacity or 'Não calculada sem dados de forragem, manejo e validação zootécnica.','attention'),PageBreak()]

    # 9 — água/clima
    story += _section('Água, aquíferos, irrigação e clima','Água subterrânea, outorga, chuva e irrigação são evidências diferentes e aparecem separadas.')
    story.append(_kpis([('Outorgas',_s(water.get('grant_count'),'N/D'),'interseção no imóvel','CONSULTADA' if water.get('grant_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),('Pivôs',_s(water.get('pivot_count'),'N/D'),'ANA / SNIRH','CONSULTADA' if water.get('pivot_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),('Chuva recente',_s(water.get('rain_30d'),'N/D'),_s(water.get('rain_period'),''),'CONSULTADA' if water.get('rain_30d') not in (None,'NÃO CONSULTADO') else 'INDISPONÍVEL'),('Poços SIAGAS',_s(gw.get('well_count'),'N/D'),f"raio {_s(gw.get('search_radius_km'),'—')} km",'CONSULTADA' if gw.get('ok') else 'INDISPONÍVEL')]))
    if gw.get('ok'):
        story += [Spacer(1,5*mm),Paragraph('Água subterrânea — evidência hidrogeológica regional',S['h2']),_kpis([
            ('Profundidade mediana',f"{_s(gw.get('well_depth_median_m'),'—')} m",f"n={_s(gw.get('well_depth_sample_n'),0)} poços",'CONSULTADA'),
            ('Nível estático mediano',f"{_s(gw.get('static_water_level_median_m'),'—')} m",f"n={_s(gw.get('static_water_level_sample_n'),0)} registros",'CONSULTADA'),
            ('Nível dinâmico mediano',f"{_s(gw.get('dynamic_water_level_median_m'),'—')} m",f"n={_s(gw.get('dynamic_water_level_sample_n'),0)} registros",'CONSULTADA'),
            ('Evidência',_s(gw.get('groundwater_evidence'),'—'),f"confiança {_s(gw.get('confidence'),'—')}",'INFO')
        ])]
        aquifers=_groundwater_rows(gw)
        if aquifers: story += [Spacer(1,4*mm),_info(aquifers,[65*mm,55*mm,45*mm],['Aquífero','Registros','Fonte'])]
        story += [Spacer(1,4*mm),_callout('COMO LER ESTE DADO',_s(gw.get('interpretation'),'Poços vizinhos são evidência regional, não garantia de água na mesma profundidade.'),'info'),Spacer(1,3*mm),_callout('ANTES DE PERFURAR',_s(gw.get('drilling_note'),'Recomenda-se avaliação hidrogeológica local e observância das regras estaduais.'),'attention')]
    else:
        story += [Spacer(1,5*mm),_callout('ÁGUA SUBTERRÂNEA','O SIAGAS não respondeu nesta emissão. Isso não significa ausência de água subterrânea.','attention')]
    if water.get('grants'): story += [Spacer(1,5*mm),Paragraph('Outorgas intersectantes',S['h2']),_info(water.get('grants'),[31*mm,27*mm,29*mm,54*mm,24*mm],['Processo','Portaria','Situação','Uso / autoridade','Localização'])]
    if water.get('rain_rows'): story += [Spacer(1,5*mm),Paragraph('Clima e precipitação',S['h2']),_info(water.get('rain_rows'),[86*mm,79*mm],['Indicador','Resultado'])]
    story.append(PageBreak())

    # 10 — conclusão e alertas
    story += _section('Conclusão, próximos passos e monitoramento','A conclusão organiza a diligência; não substitui documentação, vistoria ou análise profissional quando exigida.')
    story += [_callout('VEREDITO DE TRIAGEM',con.get('verdict') or con.get('overall_reason') or 'Dados insuficientes para conclusão definitiva.','attention'),Spacer(1,5*mm),Paragraph('Diligências recomendadas',S['h2'])]+_bullets(con.get('diligence') or nar.get('next_steps') or [],14)
    if mon.get('alerts'): story += [Spacer(1,5*mm),Paragraph('Monitoramento e alertas',S['h2']),_info(mon.get('alerts'),[55*mm,70*mm,40*mm],['Alerta','Gatilho','Canal'])]
    story += [Spacer(1,5*mm),P(con.get('limit') or ''),PageBreak()]

    # 11 — rastreabilidade
    story += _section('Rastreabilidade e limitações','Fonte, status e evidência tornam o relatório auditável e evitam conclusões silenciosas.')
    story.append(_sources_table(sources))
    story += [Spacer(1,5*mm),Paragraph('Regras de interpretação',S['h2'])]+_bullets(payload.get('interpretation_rules') or [],24)
    story += [Spacer(1,5*mm),HRFlowable(width='100%',thickness=.5,color=LINE),Spacer(1,3*mm),P(f"ID do relatório: {_s(payload.get('report_id'))}",'small'),P(f"Gerado em: {_s(payload.get('generated_at'))}",'small'),P(_s(payload.get('source_version'),''),'small')]

    doc.build(story,onFirstPage=_header_footer,onLaterPages=_header_footer)
    return sha256(path.read_bytes()).hexdigest()


print('RX_REPORT_ENGINE=V6_BOOK_SATELLITE_GROUNDWATER_AGRO',flush=True)

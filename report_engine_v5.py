from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    LongTable, Image, HRFlowable
)

PAGE_W,PAGE_H=A4
GREEN=HexColor('#0E603B'); GREEN2=HexColor('#147A4C'); GREEN_SOFT=HexColor('#EAF6EF')
INK=HexColor('#172028'); TEXT=HexColor('#344054'); MUTED=HexColor('#667085')
LINE=HexColor('#D9E0E6'); SOFT=HexColor('#F6F8F7'); WHITE=colors.white
AMBER=HexColor('#A96C13'); AMBER_SOFT=HexColor('#FFF4DF')
RED=HexColor('#B42318'); RED_SOFT=HexColor('#FDECEC')
BLUE=HexColor('#175CD3'); BLUE_SOFT=HexColor('#EEF4FF')

styles=getSampleStyleSheet()
S={
 'cover':ParagraphStyle('cover',parent=styles['Heading1'],fontName='Helvetica-Bold',fontSize=24,leading=28,textColor=INK,spaceAfter=4),
 'tag':ParagraphStyle('tag',parent=styles['BodyText'],fontName='Helvetica-Bold',fontSize=6.5,leading=8,textColor=GREEN,spaceAfter=3),
 'h1':ParagraphStyle('h1',parent=styles['Heading1'],fontName='Helvetica-Bold',fontSize=15,leading=18,textColor=INK,spaceAfter=7),
 'h2':ParagraphStyle('h2',parent=styles['Heading2'],fontName='Helvetica-Bold',fontSize=10,leading=13,textColor=INK,spaceBefore=7,spaceAfter=4),
 'body':ParagraphStyle('body',parent=styles['BodyText'],fontName='Helvetica',fontSize=7.6,leading=10.6,textColor=TEXT,spaceAfter=4),
 'small':ParagraphStyle('small',parent=styles['BodyText'],fontName='Helvetica',fontSize=6.1,leading=8.1,textColor=MUTED),
 'cell':ParagraphStyle('cell',parent=styles['BodyText'],fontName='Helvetica',fontSize=6.35,leading=8.2,textColor=TEXT),
 'cellb':ParagraphStyle('cellb',parent=styles['BodyText'],fontName='Helvetica-Bold',fontSize=6.35,leading=8.2,textColor=INK),
 'th':ParagraphStyle('th',parent=styles['BodyText'],fontName='Helvetica-Bold',fontSize=6.2,leading=7.7,textColor=WHITE),
 'bullet':ParagraphStyle('bullet',parent=styles['BodyText'],fontName='Helvetica',fontSize=7.2,leading=9.8,textColor=TEXT,leftIndent=10,firstLineIndent=-7,spaceAfter=3),
 'quote':ParagraphStyle('quote',parent=styles['BodyText'],fontName='Helvetica-Bold',fontSize=10.2,leading=14,textColor=INK),
}

def _s(v,default='-'): return default if v is None or v=='' else str(v)
def _esc(v):
    from html import escape
    return escape(_s(v,''),quote=False)
def P(v,style='body'): return Paragraph(_esc(v),S[style])

def _palette(status):
    x=_s(status,'').upper()
    if any(k in x for k in ('ALTO','CRÍT','CRIT','EMBARGO','BLOQUE')): return RED_SOFT,RED
    if any(k in x for k in ('ATEN','PARCIAL','INDISP','NÃO','NAO','RESTRITA','PENDENTE')): return AMBER_SOFT,AMBER
    if any(k in x for k in ('CONSULT','SEM ','BAIXO','OK','ATIVO','PRONTO')): return GREEN_SOFT,GREEN
    return BLUE_SOFT,BLUE

def _header_footer(c,doc):
    c.saveState()
    prop=getattr(doc,'rx_property',{}) or {}
    c.setFillColor(WHITE); c.rect(0,PAGE_H-18*mm,PAGE_W,18*mm,fill=1,stroke=0)
    c.setStrokeColor(LINE); c.setLineWidth(.5); c.line(16*mm,PAGE_H-18*mm,PAGE_W-16*mm,PAGE_H-18*mm)
    c.setFillColor(GREEN); c.setFont('Helvetica-Bold',8.3); c.drawString(16*mm,PAGE_H-10.3*mm,'RAIO-X TERRITORIAL')
    c.setFillColor(INK); c.setFont('Helvetica-Bold',7)
    c.drawRightString(PAGE_W-16*mm,PAGE_H-9.4*mm,f"{_s(prop.get('municipality'),'Imóvel rural')}/{_s(prop.get('uf'),'')}")
    c.setFillColor(MUTED); c.setFont('Helvetica',5.7)
    c.drawRightString(PAGE_W-16*mm,PAGE_H-13.4*mm,f"CAR {_s(prop.get('car_code'),'—')}")
    c.setStrokeColor(LINE); c.line(16*mm,13*mm,PAGE_W-16*mm,13*mm)
    c.setFillColor(MUTED); c.setFont('Helvetica',5.4)
    c.drawString(16*mm,8.7*mm,'Dados reais das fontes consultadas. Fonte indisponível nunca é tratada como ausência de ocorrência.')
    c.drawRightString(PAGE_W-16*mm,8.7*mm,f'Página {doc.page}')
    c.restoreState()

def _section(title,subtitle=None):
    out=[Paragraph(_esc(title),S['h1'])]
    if subtitle: out.append(Paragraph(_esc(subtitle),S['small']))
    out.append(Spacer(1,3*mm)); return out

def _bullets(items,limit=None):
    seq=(items or [])[:limit] if limit else (items or [])
    return [Paragraph('• '+_esc(x),S['bullet']) for x in seq] or [P('Nenhum item listado.','small')]

def _info(rows,widths=None,headers=None):
    data=[]
    if headers:data.append([P(x,'th') for x in headers])
    for row in rows or []: data.append([P(v,'cellb' if i==0 else 'cell') for i,v in enumerate(row)])
    if not data:data=[[P('Sem dados para esta seção.','small')]]; widths=widths or [165*mm]
    t=LongTable(data,colWidths=widths,repeatRows=1 if headers else 0,splitByRow=1,hAlign='LEFT')
    st=[('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,LINE),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
    start=0
    if headers: st += [('BACKGROUND',(0,0),(-1,0),INK)]; start=1
    for i in range(start,len(data)):
        if (i-start)%2: st.append(('BACKGROUND',(0,i),(-1,i),SOFT))
    t.setStyle(TableStyle(st)); return t

def _kpis(items):
    cells=[]
    for label,value,note,status in items:
        bg,fg=_palette(status)
        cells.append(Table([
            [P(label.upper(),'small')],[Paragraph(_esc(value),ParagraphStyle('kv',parent=S['quote'],fontSize=11.5,leading=13,textColor=INK))],[P(note,'small')]
        ],colWidths=[39*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),.6,fg),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)])))
    while len(cells)<4: cells.append(Spacer(39*mm,1))
    return Table([cells[:4]],colWidths=[41.25*mm]*4,style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]))

def _callout(title,text,tone='info'):
    bg,fg={'info':(BLUE_SOFT,BLUE),'attention':(AMBER_SOFT,AMBER),'risk':(RED_SOFT,RED),'good':(GREEN_SOFT,GREEN)}.get(tone,(SOFT,TEXT))
    return Table([[P(title,'cellb')],[Paragraph(_esc(text),S['quote'])]],colWidths=[165*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),.6,fg),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))

def _image(path,caption):
    p=Path(str(path or ''))
    if not p.exists(): return [P('Mapa técnico indisponível nesta emissão.','small')]
    try:
        im=Image(str(p),width=165*mm,height=94.8*mm); im.hAlign='CENTER'
        return [im,Spacer(1,1.4*mm),P(caption,'small')]
    except Exception: return [P('Mapa técnico não pôde ser renderizado.','small')]

def _source_stats(sources):
    out={'ok':0,'partial':0,'unavailable':0,'other':0}
    for x in sources or []:
        st=_s(x.get('status'),'').upper()
        if 'CONSULTAD' in st and 'NÃO' not in st and 'NAO' not in st:out['ok']+=1
        elif 'PARCIAL' in st:out['partial']+=1
        elif 'INDISP' in st:out['unavailable']+=1
        else:out['other']+=1
    return out

def _sources_table(sources):
    rows=[]
    for x in sources or []:
        st=_s(x.get('status'),'NÃO INFORMADO'); bg,fg=_palette(st)
        rows.append([P(x.get('name'),'cellb'),Paragraph(_esc(st),ParagraphStyle('sst',parent=S['cellb'],textColor=fg)),P(x.get('description'),'cell')])
    data=[[P('Fonte','th'),P('Status','th'),P('O que ela trouxe nesta emissão','th')]]+rows
    t=LongTable(data,colWidths=[47*mm,28*mm,90*mm],repeatRows=1,splitByRow=1)
    style=[('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,LINE),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
    for i,x in enumerate(sources or [],1):
        bg,_=_palette(_s(x.get('status'),'')); style.append(('BACKGROUND',(1,i),(1,i),bg))
        if i%2==0: style += [('BACKGROUND',(0,i),(0,i),SOFT),('BACKGROUND',(2,i),(2,i),SOFT)]
    t.setStyle(TableStyle(style)); return t

def _decision_columns(nar):
    specs=[('O QUE ESTÁ BOM',nar.get('good_points') or [],GREEN_SOFT),('O QUE PEDE ATENÇÃO',nar.get('attention') or [],AMBER_SOFT),('O QUE PODE CUSTAR DINHEIRO',nar.get('things_that_may_cost_money') or [],RED_SOFT)]
    tables=[]
    for title,items,bg in specs:
        flow=[[P(title,'cellb')]]+[[P('• '+_s(x),'cell')] for x in items[:5]]
        tables.append(Table(flow,colWidths=[51*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])))
    return Table([tables],colWidths=[55*mm]*3,style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,LINE),('LEFTPADDING',(0,0),(-1,-1),2),('RIGHTPADDING',(0,0),(-1,-1),2),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)]))

def build_premium_property_report_v5(path:str|Path,payload:dict[str,Any])->str:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=16*mm,rightMargin=16*mm,topMargin=22*mm,bottomMargin=18*mm,title='Raio-X Territorial',author='Raio-X Territorial')
    doc.rx_property=payload.get('property') or {}
    p=payload.get('property') or {}; car=payload.get('car') or {}; land=payload.get('land') or {}; env=payload.get('environment') or {}; enf=payload.get('enforcement') or {}; mining=payload.get('mining') or {}; prod=payload.get('productive') or {}; water=payload.get('water') or {}; mon=payload.get('monitoring') or {}; con=payload.get('conclusion') or {}; nar=payload.get('narrative') or {}; sources=payload.get('sources') or []
    story=[]

    # 1 — CAPA + leitura rápida
    story += [Spacer(1,2*mm),Paragraph('RAIO-X TERRITORIAL',S['cover']),P('Dossiê territorial, ambiental, produtivo e mineral — feito para ser entendido, não apenas arquivado.','small'),Spacer(1,4*mm)]
    story += _image(payload.get('map_image_path') or payload.get('car_map_image_path'),'Mapa técnico do limite analisado. Camadas adicionais aparecem no detalhamento.')
    story += [Spacer(1,4*mm),_kpis([('Área',f"{_s(p.get('area_ha'))} ha",f"{_s(p.get('municipality'))}/{_s(p.get('uf'))}",'CONSULTADA'),('CAR',_s(car.get('status'),'N/D'),_s(car.get('analysis_status'),''),_s(car.get('status'),'CONSULTADA')),('Risco geral',_s(con.get('overall_risk'),'NÃO CLASSIFICADO'),'triagem consolidada',_s(con.get('overall_risk'),'ATENÇÃO')),('Fontes',str(_source_stats(sources)['ok']),'consultadas nesta emissão','CONSULTADA')]),Spacer(1,5*mm),_callout('EM UMA FRASE',nar.get('one_sentence') or payload.get('quick_read') or con.get('overall_reason') or 'Análise em processamento.','info'),Spacer(1,5*mm),Paragraph('O que encontramos',S['h2'])]
    story += _bullets(nar.get('what_we_found') or [],8)
    story += [Paragraph('Por que isso importa',S['h2'])]+_bullets(nar.get('why_it_matters') or [],6)
    story.append(PageBreak())

    # 2 — decisão prática
    story += _section('A leitura que interessa para a decisão','Antes das tabelas, responda três perguntas: o que está bom, o que merece atenção e o que pode virar custo.')
    story.append(_decision_columns(nar)); story += [Spacer(1,6*mm),Paragraph('O que fazer agora',S['h2'])]+_bullets(nar.get('next_steps') or con.get('diligence') or [],12)
    story += [Spacer(1,4*mm),_callout('REGRA DO RAIO-X','Se uma fonte falha, ela aparece como ponto cego. O sistema nunca transforma “não consegui consultar” em “não existe problema”.','attention')]
    story.append(PageBreak())

    # 3 — cobertura real de fontes
    stats=_source_stats(sources)
    story += _section('Cobertura das fontes','Veja exatamente o que respondeu, o que ficou parcial e o que ainda depende de integração restrita ou premium.')
    story.append(_kpis([('Consultadas',stats['ok'],'responderam nesta emissão','CONSULTADA'),('Parciais',stats['partial'],'retorno incompleto','PARCIAL'),('Indisponíveis',stats['unavailable'],'falha temporária','INDISPONÍVEL'),('Outras',stats['other'],'restritas ou ainda não executadas','ATENÇÃO')]))
    story += [Spacer(1,5*mm),_sources_table(sources),PageBreak()]

    # 4 — CAR e fundiário
    story += _section('CAR, cadastro e situação fundiária','CAR, SIGEF e matrícula respondem perguntas diferentes. O relatório não mistura cadastro ambiental com propriedade registral.')
    story.append(_info(car.get('fields') or [],[60*mm,105*mm],['Campo','Resultado']))
    story += [Spacer(1,5*mm),Paragraph('SIGEF, SNCI, CCIR e matrícula',S['h2']),_info(land.get('certifications') or [],[28*mm,35*mm,24*mm,78*mm],['Base','Situação','Registros','Leitura'])]
    if land.get('matrix'): story += [Spacer(1,4*mm),_info(land.get('matrix'),[35*mm,40*mm,28*mm,62*mm],['Base/campo','Resultado','Área','O que isso significa'])]
    ev=land.get('evidence') or {}; story += [Spacer(1,4*mm),_callout('EVIDÊNCIA DOMINIAL',f"{_s(ev.get('score'),'NÃO CLASSIFICADA')} — {_s(ev.get('text'),'')}",'attention'),PageBreak()]

    # 5 — ambiental
    pd=env.get('prodes') or {}
    story += _section('Ambiental e fiscalização','Aqui entram desmatamento mapeado, embargos, autos e sobreposições territoriais. O sistema diferencia ocorrência cartográfica de infração comprovada.')
    story.append(_kpis([('PRODES',_s(pd.get('count'),0),f"{_s(pd.get('area_ha'),0)} ha",_s(pd.get('status'),'CONSULTADA')),('Embargos',_s(enf.get('embargo_count'),0),'IBAMA + ICMBio quando disponíveis',_s(enf.get('embargo_status'),'CONSULTADA')),('Autos IBAMA',_s(enf.get('auto_count'),'N/D'),_s(enf.get('fine_total_text'),''),'ATENÇÃO' if _s(enf.get('auto_count')) not in ('0','N/D','NÃO CONSULTADO') else 'CONSULTADA'),('Restrições',f"{_s(env.get('unique_problem_area_ha'),0)} ha",f"{_s(env.get('unique_problem_area_pct'),0)}% do CAR",'ATENÇÃO' if env.get('unique_problem_area_ha') else 'CONSULTADA')]))
    story += [Spacer(1,5*mm),Paragraph('Camadas ambientais e territoriais',S['h2']),_info(env.get('layer_rows') or [],[48*mm,70*mm,47*mm],['Camada','Resultado','Fonte'])]
    if pd.get('rows'): story += [Spacer(1,5*mm),Paragraph('PRODES em detalhe',S['h2']),_info(pd.get('rows'),[62*mm,103*mm],['Indicador','Resultado'])]
    story += [Spacer(1,4*mm),P(pd.get('meaning') or ''),PageBreak()]

    # 6 — mineral
    story += _section('Mineração, minerais críticos e terras raras','Um processo minerário ou sinal geológico pode ser relevante para a negociação, mas não prova jazida, recurso ou reserva.')
    story.append(_kpis([('Processos ANM',_s(mining.get('process_count'),0),f"{_s(mining.get('overlap_area_ha'),0)} ha",'ATENÇÃO' if mining.get('process_count') else 'CONSULTADA'),('Minerais críticos',_s(mining.get('critical_process_count'),0),', '.join(mining.get('critical_minerals') or []) or 'nenhum classificado','ATENÇÃO' if mining.get('critical_process_count') else 'CONSULTADA'),('Terras raras',_s(mining.get('rare_earth_signal'),'NÃO IDENTIFICADO'),f"processos: {_s(mining.get('rare_earth_count'),0)}",'ATENÇÃO' if _s(mining.get('rare_earth_signal')).upper()=='SIM' else 'CONSULTADA'),('SGB',_s(mining.get('rare_earth_source'),'ANM + SGB'),'favorabilidade e ocorrências públicas','CONSULTADA')]))
    story += [Spacer(1,5*mm),P(mining.get('summary') or '')]
    if mining.get('critical_rows'): story += [Spacer(1,4*mm),_info(mining.get('critical_rows'),[80*mm,85*mm],['Indicador','Resultado'])]
    if mining.get('processes'): story += [Spacer(1,4*mm),Paragraph('Processos identificados',S['h2']),_info(mining.get('processes'),[27*mm,42*mm,35*mm,38*mm,23*mm],['Processo','Titular','Substância','Fase','% imóvel'])]
    story.append(PageBreak())

    # 7 — produtivo
    story += _section('Solo, aptidão, relevo e uso da terra','A parte produtiva traduz as camadas técnicas sem esconder quando a resposta foi parcial.')
    terrain=prod.get('terrain_kpis') or []; k=[]
    for x in terrain[:4]: k.append((_s(x.get('label')),_s(x.get('value')),_s(x.get('note'),''),_s(x.get('status') or x.get('level') or 'INFO')))
    while len(k)<4:k.append(('Dado','NÃO DISPONÍVEL','fonte ainda não respondeu','PARCIAL'))
    story.append(_kpis(k[:4]))
    if prod.get('soil_rows'): story += [Spacer(1,5*mm),Paragraph('Solo',S['h2']),_info(prod.get('soil_rows'),[75*mm,90*mm],['Indicador','Resultado'])]
    if prod.get('aptitude_rows'): story += [Spacer(1,5*mm),Paragraph('Aptidão agrícola',S['h2']),_info(prod.get('aptitude_rows'),[118*mm,47*mm],['Classe / evidência','Resultado'])]
    if prod.get('erosion_rows'): story += [Spacer(1,5*mm),Paragraph('Risco potencial de erosão',S['h2']),_info(prod.get('erosion_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    if prod.get('landcover_rows'): story += [Spacer(1,5*mm),Paragraph('Uso e cobertura',S['h2']),_info(prod.get('landcover_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    story.append(PageBreak())

    # 8 — água/clima
    story += _section('Água, irrigação e clima','Outorgas, pivôs e clima entram juntos porque ajudam a entender disponibilidade operacional, sem confundir proximidade com direito de uso.')
    story.append(_kpis([('Outorgas',_s(water.get('grant_count'),'N/D'),'interseção no imóvel','CONSULTADA' if water.get('grant_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),('Pivôs',_s(water.get('pivot_count'),'N/D'),'ANA / SNIRH','CONSULTADA' if water.get('pivot_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),('Chuva recente',_s(water.get('rain_30d'),'N/D'),_s(water.get('rain_period'),''),'CONSULTADA' if water.get('rain_30d') not in (None,'NÃO CONSULTADO') else 'INDISPONÍVEL'),('Leitura hídrica','TRIAGEM',_s(water.get('meaning'),''),'INFO')]))
    if water.get('grants'): story += [Spacer(1,5*mm),Paragraph('Outorgas intersectantes',S['h2']),_info(water.get('grants'),[31*mm,27*mm,29*mm,54*mm,24*mm],['Processo','Portaria','Situação','Uso / autoridade','Localização'])]
    if water.get('rain_rows'): story += [Spacer(1,5*mm),Paragraph('Agroclimatologia',S['h2']),_info(water.get('rain_rows'),[86*mm,79*mm],['Indicador','Resultado'])]
    story.append(PageBreak())

    # 9 — decisão e monitoramento
    story += _section('Conclusão e próximos passos','A conclusão não fecha a questão: ela organiza a decisão e deixa claro o que ainda pode mudá-la.')
    story += [_callout('VEREDITO DE TRIAGEM',con.get('verdict') or con.get('overall_reason') or 'Dados insuficientes para conclusão definitiva.','attention'),Spacer(1,5*mm),Paragraph('Diligências recomendadas',S['h2'])]+_bullets(con.get('diligence') or nar.get('next_steps') or [],12)
    if mon.get('alerts'): story += [Spacer(1,5*mm),Paragraph('Monitoramento e alertas',S['h2']),_info(mon.get('alerts'),[55*mm,70*mm,40*mm],['Alerta','Gatilho','Canal'])]
    story += [Spacer(1,5*mm),P(con.get('limit') or ''),PageBreak()]

    # 10 — rastreabilidade
    story += _section('Rastreabilidade e limitações','Esta é a parte que permite auditar o relatório: fonte, status e o que cada base efetivamente entregou.')
    story.append(_sources_table(sources)); story += [Spacer(1,5*mm),Paragraph('Regras de interpretação',S['h2'])]+_bullets(payload.get('interpretation_rules') or [],20)
    story += [Spacer(1,5*mm),HRFlowable(width='100%',thickness=.5,color=LINE),Spacer(1,3*mm),P(f"ID do relatório: {_s(payload.get('report_id'))}",'small'),P(f"Gerado em: {_s(payload.get('generated_at'))}",'small'),P(_s(payload.get('source_version'),''),'small')]

    doc.build(story,onFirstPage=_header_footer,onLaterPages=_header_footer)
    return sha256(path.read_bytes()).hexdigest()

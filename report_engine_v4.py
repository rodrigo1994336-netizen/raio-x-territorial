from __future__ import annotations

from html import escape
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Image, KeepTogether, LongTable, HRFlowable
)

PAGE_W, PAGE_H = A4
GREEN = HexColor('#0E603B')
GREEN_2 = HexColor('#147A4C')
GREEN_SOFT = HexColor('#EAF6EF')
INK = HexColor('#172028')
TEXT = HexColor('#344054')
MUTED = HexColor('#667085')
LINE = HexColor('#D9E0E6')
SOFT = HexColor('#F6F8F7')
AMBER = HexColor('#A96C13')
AMBER_SOFT = HexColor('#FFF4DF')
RED = HexColor('#B42318')
RED_SOFT = HexColor('#FDECEC')
BLUE = HexColor('#175CD3')
BLUE_SOFT = HexColor('#EEF4FF')
WHITE = colors.white


def _s(v: Any, default='-') -> str:
    return default if v is None or v == '' else str(v)


def _e(v: Any, default='-') -> str:
    return escape(_s(v, default), quote=False)


def _status_palette(status: str):
    x=(status or '').upper()
    if any(k in x for k in ('ALTO','CRÍT','CRIT','BLOQUE','EMBARGO')):
        return RED_SOFT, RED
    if any(k in x for k in ('ATEN','PARCIAL','INDISP','NÃO','NAO','PENDENTE','RESTRITA')):
        return AMBER_SOFT, AMBER
    if any(k in x for k in ('CONSULTADA','CONSULTADO','OK','BAIXO','SEM ','PRONTO','ATIVO')):
        return GREEN_SOFT, GREEN
    return BLUE_SOFT, BLUE


styles=getSampleStyleSheet()
S={
    'cover_title': ParagraphStyle('cover_title', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=23, leading=27, textColor=INK, spaceAfter=5),
    'cover_sub': ParagraphStyle('cover_sub', parent=styles['BodyText'], fontName='Helvetica', fontSize=8.5, leading=12, textColor=MUTED),
    'h1': ParagraphStyle('h1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=INK, spaceBefore=2, spaceAfter=8),
    'h2': ParagraphStyle('h2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=INK, spaceBefore=10, spaceAfter=5),
    'body': ParagraphStyle('body', parent=styles['BodyText'], fontName='Helvetica', fontSize=7.4, leading=10.2, textColor=TEXT, spaceAfter=4),
    'small': ParagraphStyle('small', parent=styles['BodyText'], fontName='Helvetica', fontSize=6.2, leading=8.3, textColor=MUTED),
    'cell': ParagraphStyle('cell', parent=styles['BodyText'], fontName='Helvetica', fontSize=6.4, leading=8.2, textColor=TEXT),
    'cell_b': ParagraphStyle('cell_b', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=6.4, leading=8.2, textColor=INK),
    'th': ParagraphStyle('th', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=6.2, leading=7.7, textColor=WHITE),
    'kpi_label': ParagraphStyle('kpi_label', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=5.8, leading=7, textColor=MUTED),
    'kpi_value': ParagraphStyle('kpi_value', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=INK),
    'note': ParagraphStyle('note', parent=styles['BodyText'], fontName='Helvetica', fontSize=6.2, leading=8.2, textColor=MUTED),
    'bullet': ParagraphStyle('bullet', parent=styles['BodyText'], fontName='Helvetica', fontSize=7, leading=9.5, textColor=TEXT, leftIndent=10, firstLineIndent=-7, bulletIndent=2, spaceAfter=3),
}


def P(v, style='body'):
    return Paragraph(_e(v,''), S[style])


def status_paragraph(v):
    bg,fg=_status_palette(_s(v,''))
    # Background is applied by the table cell; return text only.
    st=ParagraphStyle('status_dynamic', parent=S['cell_b'], textColor=fg, alignment=TA_CENTER)
    return Paragraph(_e(v,''), st), bg


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(.5)
    canvas.line(18*mm, PAGE_H-16*mm, PAGE_W-18*mm, PAGE_H-16*mm)
    canvas.setFillColor(GREEN)
    canvas.setFont('Helvetica-Bold',8)
    canvas.drawString(18*mm,PAGE_H-11.8*mm,'RAIO-X TERRITORIAL')
    canvas.setFillColor(MUTED)
    canvas.setFont('Helvetica',6)
    canvas.drawRightString(PAGE_W-18*mm,PAGE_H-11.8*mm,'Inteligência territorial rural')
    canvas.line(18*mm,14*mm,PAGE_W-18*mm,14*mm)
    canvas.setFont('Helvetica',5.6)
    canvas.drawString(18*mm,9.5*mm,'Fontes públicas, evidências rastreáveis e cálculo geoespacial. Indisponibilidade nunca é convertida em ausência de risco.')
    canvas.drawRightString(PAGE_W-18*mm,9.5*mm,f'Página {doc.page}')
    canvas.restoreState()


def section_title(title, subtitle=None):
    out=[Paragraph(_e(title,''),S['h1'])]
    if subtitle:
        out.append(Paragraph(_e(subtitle,''),S['cover_sub']))
    out.append(Spacer(1,3*mm))
    return out


def info_table(rows: Iterable, widths=None, header=None):
    data=[]
    if header:
        data.append([Paragraph(_e(x,''),S['th']) for x in header])
    for row in rows or []:
        data.append([Paragraph(_e(x,''),S['cell_b'] if i==0 else S['cell']) for i,x in enumerate(row)])
    if not data:
        data=[[P('Sem registros para esta seção.','small')]]
        widths=widths or [165*mm]
    t=LongTable(data,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT',splitByRow=1)
    cmds=[
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('GRID',(0,0),(-1,-1),.35,LINE),
    ]
    if header:
        cmds += [('BACKGROUND',(0,0),(-1,0),INK),('TEXTCOLOR',(0,0),(-1,0),WHITE)]
        start=1
    else:
        start=0
    for i in range(start,len(data)):
        if (i-start)%2:
            cmds.append(('BACKGROUND',(0,i),(-1,i),SOFT))
    t.setStyle(TableStyle(cmds))
    return t


def source_table(sources):
    rows=[]
    for src in sources or []:
        st=_s(src.get('status'),'NÃO INFORMADO')
        status_p,bg=status_paragraph(st)
        rows.append([P(src.get('name'),'cell_b'),status_p,P(src.get('description'),'cell')])
    data=[[P('Fonte','th'),P('Status','th'),P('Evidência / observação','th')]]+rows
    t=LongTable(data,colWidths=[47*mm,28*mm,90*mm],repeatRows=1,splitByRow=1)
    cmds=[('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,LINE),
          ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
    for i,src in enumerate(sources or [],1):
        bg,_=_status_palette(_s(src.get('status'),''))
        cmds.append(('BACKGROUND',(1,i),(1,i),bg))
        if i%2==0:
            cmds.append(('BACKGROUND',(0,i),(0,i),SOFT)); cmds.append(('BACKGROUND',(2,i),(2,i),SOFT))
    t.setStyle(TableStyle(cmds))
    return t


def kpi_table(items):
    cells=[]
    for label,value,note,status in items:
        bg,fg=_status_palette(status)
        cells.append(Table([[Paragraph(_e(label,''),S['kpi_label'])],[Paragraph(_e(value,''),S['kpi_value'])],[Paragraph(_e(note,''),S['note'])]],
                           colWidths=[39*mm],rowHeights=[7*mm,10*mm,None],
                           style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),.5,fg),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])))
    while len(cells)<4:
        cells.append(Spacer(39*mm,1))
    return Table([cells[:4]],colWidths=[41.25*mm]*4,style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]))


def bullets(items, limit=None):
    out=[]
    seq=(items or [])[:limit] if limit else (items or [])
    for item in seq:
        out.append(Paragraph('• '+_e(item,''),S['bullet']))
    if not out:
        out.append(P('Nenhum item listado.','small'))
    return out


def image_block(path, caption='Mapa técnico'):
    p=Path(str(path or ''))
    if not p.exists():
        return [P('Mapa técnico indisponível nesta emissão.','small')]
    try:
        im=Image(str(p),width=165*mm,height=94.9*mm)
        im.hAlign='CENTER'
        return [im,Spacer(1,1.5*mm),Paragraph(_e(caption,''),S['small'])]
    except Exception:
        return [P('Mapa técnico não pôde ser renderizado.','small')]


def _compact_source_stats(sources):
    c={'CONSULTADA':0,'PARCIAL':0,'INDISPONÍVEL':0,'OUTROS':0}
    for x in sources or []:
        st=_s(x.get('status'),'').upper()
        if 'CONSULTAD' in st and 'NÃO' not in st and 'NAO' not in st: c['CONSULTADA']+=1
        elif 'PARCIAL' in st: c['PARCIAL']+=1
        elif 'INDISP' in st: c['INDISPONÍVEL']+=1
        else:c['OUTROS']+=1
    return c


def build_premium_property_report_v4(path: str|Path, payload: dict[str,Any]) -> str:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=22*mm,bottomMargin=19*mm,
                          title='Raio-X Territorial',author='Raio-X Territorial')
    story=[]
    prop=payload.get('property') or {}; car=payload.get('car') or {}; land=payload.get('land') or {}; env=payload.get('environment') or {}; enf=payload.get('enforcement') or {}; mining=payload.get('mining') or {}; prod=payload.get('productive') or {}; water=payload.get('water') or {}; mon=payload.get('monitoring') or {}; con=payload.get('conclusion') or {}; sources=payload.get('sources') or []

    # CAPA / RESUMO
    story += [Spacer(1,4*mm),Paragraph('RAIO-X TERRITORIAL',S['cover_title']),Paragraph('Dossiê territorial, ambiental, produtivo e mineral',S['cover_sub']),Spacer(1,5*mm)]
    story += image_block(payload.get('map_image_path') or payload.get('car_map_image_path'),'Limite do imóvel e ocorrências cartográficas exibidas no mapa técnico.')
    story += [Spacer(1,5*mm)]
    story.append(kpi_table([
        ('ÁREA CAR',f"{_s(prop.get('area_ha'))} ha",f"{_s(prop.get('municipality'))}/{_s(prop.get('uf'))}",'CONSULTADA'),
        ('CAR',_s(car.get('status'),'N/D'),_s(car.get('analysis_status'),''),_s(car.get('status'),'CONSULTADA')),
        ('RISCO GERAL',_s(con.get('overall_risk'),'NÃO CLASSIFICADO'),'triagem consolidada',_s(con.get('overall_risk'),'ATENÇÃO')),
        ('RELATÓRIO',_s(payload.get('report_id'),'—'),_s(payload.get('generated_at'),''),'CONSULTADA'),
    ]))
    story += [Spacer(1,5*mm),Paragraph('Leitura executiva',S['h2']),P(payload.get('quick_read') or con.get('overall_reason') or 'Análise em processamento.')]
    story += bullets(payload.get('attention_points') or [],5)
    story.append(PageBreak())

    # FONTES PRIMEIRO — transparência da cobertura
    story += section_title('Cobertura e confiabilidade das fontes','Antes da interpretação, veja exatamente quais bases responderam, quais ficaram parciais e quais dependem de integração restrita.')
    stat=_compact_source_stats(sources)
    story.append(kpi_table([
        ('CONSULTADAS',stat['CONSULTADA'],'responderam nesta emissão','CONSULTADA'),
        ('PARCIAIS',stat['PARCIAL'],'houve limite/retorno incompleto','PARCIAL'),
        ('INDISPONÍVEIS',stat['INDISPONÍVEL'],'falha temporária da fonte','INDISPONÍVEL'),
        ('OUTRAS',stat['OUTROS'],'restritas, premium ou não executadas','ATENÇÃO'),
    ]))
    story += [Spacer(1,5*mm),source_table(sources)]
    story.append(PageBreak())

    # IDENTIFICAÇÃO E FUNDIÁRIO
    story += section_title('Identificação, CAR e fundiário','Cadastro ambiental, certificação fundiária e registro imobiliário são tratados como evidências distintas.')
    story.append(info_table(car.get('fields') or [],[62*mm,103*mm],['Campo','Resultado']))
    story += [Spacer(1,5*mm),Paragraph('Certificações e vínculo',S['h2'])]
    story.append(info_table(land.get('certifications') or [],[28*mm,35*mm,24*mm,78*mm],['Base','Situação','Registros','Observação']))
    story += [Spacer(1,4*mm)]
    if land.get('matrix'):
        story.append(info_table(land.get('matrix'),[35*mm,41*mm,28*mm,61*mm],['Base/campo','Resultado','Área','Leitura']))
    ev=land.get('evidence') or {}
    story += [Spacer(1,4*mm),P(f"Grau de evidência dominial: {_s(ev.get('score'),'NÃO CLASSIFICADO')} — {_s(ev.get('text'),'')}")]
    story.append(PageBreak())

    # AMBIENTAL / FISCALIZAÇÃO
    story += section_title('Ambiental e fiscalização','Interseção cartográfica é evidência para diligência; não é automaticamente infração ou irregularidade.')
    pd=env.get('prodes') or {}
    story.append(kpi_table([
        ('PRODES',_s(pd.get('count'),0),f"{_s(pd.get('area_ha'),0)} ha",_s(pd.get('status'),'CONSULTADA')),
        ('EMBARGOS',_s(enf.get('embargo_count'),0),'IBAMA + ICMBio quando disponíveis',_s(enf.get('embargo_status'),'CONSULTADA')),
        ('AUTOS IBAMA',_s(enf.get('auto_count'),'N/D'),_s(enf.get('fine_total_text'),''),'ATENÇÃO' if _s(enf.get('auto_count')) not in ('0','N/D','NÃO CONSULTADO') else 'CONSULTADA'),
        ('ÁREA DE RESTRIÇÕES',f"{_s(env.get('unique_problem_area_ha'),0)} ha",f"{_s(env.get('unique_problem_area_pct'),0)}% do CAR",'ATENÇÃO' if env.get('unique_problem_area_ha') else 'CONSULTADA'),
    ]))
    story += [Spacer(1,5*mm),Paragraph('Camadas territoriais e ambientais',S['h2'])]
    story.append(info_table(env.get('layer_rows') or [],[48*mm,70*mm,47*mm],['Camada','Resultado','Fonte']))
    if pd.get('rows'):
        story += [Spacer(1,5*mm),Paragraph('Detalhe PRODES',S['h2']),info_table(pd.get('rows'),[60*mm,105*mm],['Indicador','Resultado'])]
    story += [Spacer(1,4*mm),P(pd.get('meaning') or '')]
    if enf.get('autos'):
        story += [Spacer(1,5*mm),Paragraph('Autos de infração',S['h2']),info_table(enf.get('autos'),None)]
    story.append(PageBreak())

    # MINERAL
    story += section_title('Inteligência mineral e terras raras','Processos ANM e sinais geológicos do SGB são triagem de interesse mineral; não equivalem a jazida, recurso ou reserva.')
    story.append(kpi_table([
        ('PROCESSOS ANM',_s(mining.get('process_count'),0),f"{_s(mining.get('overlap_area_ha'),0)} ha",'ATENÇÃO' if mining.get('process_count') else 'CONSULTADA'),
        ('MINERAIS CRÍTICOS',_s(mining.get('critical_process_count'),0),', '.join(mining.get('critical_minerals') or []) or 'nenhum classificado','ATENÇÃO' if mining.get('critical_process_count') else 'CONSULTADA'),
        ('TERRAS RARAS',_s(mining.get('rare_earth_signal'),'NÃO IDENTIFICADO'),f"processos: {_s(mining.get('rare_earth_count'),0)}",'ATENÇÃO' if _s(mining.get('rare_earth_signal')).upper()=='SIM' else 'CONSULTADA'),
        ('SGB',_s(mining.get('rare_earth_source'),'ANM + SGB'),'favorabilidade / ocorrências públicas','CONSULTADA'),
    ]))
    story += [Spacer(1,5*mm),P(mining.get('summary') or '')]
    if mining.get('critical_rows'):
        story += [Spacer(1,4*mm),info_table(mining.get('critical_rows'),[80*mm,85*mm],['Indicador','Resultado'])]
    if mining.get('processes'):
        story += [Spacer(1,5*mm),Paragraph('Processos identificados',S['h2']),info_table(mining.get('processes'),[27*mm,42*mm,35*mm,38*mm,23*mm],['Processo','Titular','Substância','Fase','% imóvel'])]
    story.append(PageBreak())

    # PRODUTIVO
    story += section_title('Solo, aptidão, relevo e uso da terra','Camadas produtivas devem informar a fonte e a qualidade da resposta. Retorno parcial não é tratado como ausência.')
    terrain=prod.get('terrain_kpis') or []
    k=[]
    for x in terrain[:4]:
        k.append((_s(x.get('label')),_s(x.get('value')),_s(x.get('note'),''),_s(x.get('status') or x.get('level') or 'INFO')))
    while len(k)<4:k.append(('DADO','NÃO DISPONÍVEL','fonte não retornou','PARCIAL'))
    story.append(kpi_table(k[:4]))
    if prod.get('soil_rows'):
        story += [Spacer(1,5*mm),Paragraph('Solo',S['h2']),info_table(prod.get('soil_rows'),[75*mm,90*mm],['Indicador','Resultado'])]
    if prod.get('aptitude_rows'):
        story += [Spacer(1,5*mm),Paragraph('Aptidão agrícola',S['h2']),info_table(prod.get('aptitude_rows'),[120*mm,45*mm],['Classe / evidência','Resultado'])]
    if prod.get('erosion_rows'):
        story += [Spacer(1,5*mm),Paragraph('Risco potencial de erosão',S['h2']),info_table(prod.get('erosion_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    if prod.get('landcover_rows'):
        story += [Spacer(1,5*mm),Paragraph('Uso e cobertura',S['h2']),info_table(prod.get('landcover_rows'),[100*mm,65*mm],['Classe','Evidência'])]
    story.append(PageBreak())

    # ÁGUA E CLIMA
    story += section_title('Água, irrigação e clima','A existência ou ausência de outorga deve ser confirmada nas fontes competentes; proximidade não significa vínculo jurídico.')
    story.append(kpi_table([
        ('OUTORGAS',_s(water.get('grant_count'),'N/D'),'interseção no imóvel','CONSULTADA' if water.get('grant_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),
        ('PIVÔS',_s(water.get('pivot_count'),'N/D'),'ANA / SNIRH','CONSULTADA' if water.get('pivot_count')!='NÃO CONSULTADO' else 'INDISPONÍVEL'),
        ('CHUVA RECENTE',_s(water.get('rain_30d'),'N/D'),_s(water.get('rain_period'),''),'CONSULTADA' if water.get('rain_30d') not in (None,'NÃO CONSULTADO') else 'INDISPONÍVEL'),
        ('LEITURA HÍDRICA','TRIAGEM',_s(water.get('meaning'),''),'INFO'),
    ]))
    if water.get('grants'):
        story += [Spacer(1,5*mm),Paragraph('Outorgas intersectantes',S['h2']),info_table(water.get('grants'),[31*mm,27*mm,29*mm,54*mm,24*mm],['Processo','Portaria','Situação','Uso / autoridade','Localização'])]
    if water.get('rain_rows'):
        story += [Spacer(1,5*mm),Paragraph('Agroclimatologia',S['h2']),info_table(water.get('rain_rows'),[86*mm,79*mm],['Indicador','Resultado'])]
    story.append(PageBreak())

    # MONITORAMENTO + DECISÃO
    story += section_title('Decisão e próximos passos','O Raio-X organiza evidências e o que deve ser verificado antes de comprar, financiar, arrendar ou assumir obrigação.')
    story.append(kpi_table([
        ('RISCO GERAL',_s(con.get('overall_risk'),'NÃO CLASSIFICADO'),_s(con.get('overall_reason'),''),_s(con.get('overall_risk'),'ATENÇÃO')),
        ('MONITORAMENTO',_s(mon.get('status'),'PREPARADO'),_s(mon.get('cadence_note'),'alertas e snapshots'),'INFO'),
        ('FOGO',_s(mon.get('fire_inside') or mon.get('fire_inside_365d'),'N/D'),'detecções orbitais','ATENÇÃO'),
        ('NDVI',_s(mon.get('ndvi'),'N/D'),_s(mon.get('ndvi_date_source'),''),'INFO'),
    ]))
    story += [Spacer(1,5*mm),Paragraph('Principal ponto de atenção',S['h2']),P(con.get('main_attention') or ''),Paragraph('Veredito de triagem',S['h2']),P(con.get('verdict') or '')]
    cols=[['Pontos positivos']+[_s(x) for x in (con.get('positives') or [])[:6]],['Riscos / atenção']+[_s(x) for x in (con.get('risks') or [])[:6]],['Oportunidades']+[_s(x) for x in (con.get('opportunities') or [])[:6]]]
    maxlen=max(len(x) for x in cols)
    for x in cols:
        while len(x)<maxlen:x.append('')
    data=[]
    data.append([P(x[0],'cell_b') for x in cols])
    for i in range(1,maxlen):data.append([P('• '+x[i] if x[i] else '','cell') for x in cols])
    t=Table(data,colWidths=[55*mm]*3,repeatRows=1,splitByRow=1)
    t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,LINE),('BACKGROUND',(0,0),(0,0),GREEN_SOFT),('BACKGROUND',(1,0),(1,0),RED_SOFT),('BACKGROUND',(2,0),(2,0),BLUE_SOFT),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    story += [Spacer(1,5*mm),t,Spacer(1,5*mm),Paragraph('Diligências recomendadas',S['h2'])] + bullets(con.get('diligence') or [],12)
    story += [Spacer(1,4*mm),P(con.get('limit') or '')]
    story.append(PageBreak())

    # RASTREABILIDADE
    story += section_title('Rastreabilidade, metodologia e limitações','Este anexo registra a origem dos dados e impede que indisponibilidade de fonte seja confundida com inexistência de ocorrência.')
    story.append(source_table(sources))
    story += [Spacer(1,5*mm),Paragraph('Regras de interpretação',S['h2'])] + bullets(payload.get('interpretation_rules') or [],20)
    story += [Spacer(1,5*mm),HRFlowable(width='100%',thickness=.5,color=LINE),Spacer(1,3*mm),P(f"ID do relatório: {_s(payload.get('report_id'))}",'small'),P(f"Gerado em: {_s(payload.get('generated_at'))}",'small'),P(_s(payload.get('source_version'),''),'small')]

    doc.build(story,onFirstPage=_header_footer,onLaterPages=_header_footer)
    return sha256(path.read_bytes()).hexdigest()

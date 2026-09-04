from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

PAGE_W, PAGE_H = A4
M = 42
CONTENT_W = PAGE_W - 2 * M
INK = HexColor('#172028')
TEXT = HexColor('#344054')
MUTED = HexColor('#667085')
LINE = HexColor('#D9E0E6')
SOFT = HexColor('#F7F9FA')
WHITE = HexColor('#FFFFFF')
GREEN = HexColor('#147A4C')
GREEN_DARK = HexColor('#0E603B')
GREEN_SOFT = HexColor('#ECF7F0')
RED = HexColor('#C53A3A')
RED_SOFT = HexColor('#FCEEEE')
AMBER = HexColor('#A96C13')
AMBER_SOFT = HexColor('#FFF4DF')
BLUE = HexColor('#245FA9')
BLUE_SOFT = HexColor('#EDF4FC')
DARK = HexColor('#1B242C')

try:
    pdfmetrics.registerFont(TTFont('Inter', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('Inter-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    FONT='Inter'; BOLD='Inter-Bold'
except Exception:
    FONT='Helvetica'; BOLD='Helvetica-Bold'


def s(v: Any, default='-') -> str:
    return default if v is None or v == '' else str(v)


def fit(c, text, x, y, max_w, font=FONT, size=8, min_size=5.5, color=TEXT):
    text=s(text); sz=size
    while sz > min_size and pdfmetrics.stringWidth(text,font,sz) > max_w:
        sz -= .25
    c.setFont(font,sz); c.setFillColor(color); c.drawString(x,y,text)


def wrap(c, text, x, y, max_w, font=FONT, size=7, leading=9, color=TEXT, max_lines=4):
    words=s(text,'').split(); lines=[]; cur=''
    for w in words:
        trial=w if not cur else cur+' '+w
        if pdfmetrics.stringWidth(trial,font,size) <= max_w: cur=trial
        else:
            if cur: lines.append(cur)
            cur=w
    if cur: lines.append(cur)
    if len(lines) > max_lines:
        lines=lines[:max_lines]
        while lines[-1] and pdfmetrics.stringWidth(lines[-1]+'...',font,size)>max_w:
            lines[-1]=lines[-1][:-1]
        lines[-1]=lines[-1].rstrip()+'...'
    c.setFont(font,size); c.setFillColor(color)
    for line in lines:
        c.drawString(x,y,line); y-=leading
    return y


def header(c,page,section,payload):
    c.setFillColor(WHITE); c.rect(0,PAGE_H-48,PAGE_W,48,fill=1,stroke=0)
    c.setStrokeColor(GREEN); c.setLineWidth(2.4)
    c.line(M,PAGE_H-27,M+10,PAGE_H-35); c.line(M+10,PAGE_H-35,M+20,PAGE_H-21)
    c.setFillColor(INK); c.setFont(BOLD,8.5); c.drawString(M+28,PAGE_H-29,'RAIO-X TERRITORIAL')
    c.setFillColor(MUTED); c.setFont(FONT,6.2); c.drawString(M+28,PAGE_H-40,section.upper())
    prop=payload.get('property') or {}
    fit(c,prop.get('name',''),PAGE_W-225,PAGE_H-29,145,FONT,6.5,5.4,TEXT)
    c.setFont(BOLD,6.5); c.setFillColor(MUTED); c.drawRightString(PAGE_W-M,PAGE_H-29,f'{page:02d}')
    c.setStrokeColor(LINE); c.setLineWidth(.5); c.line(M,PAGE_H-48,PAGE_W-M,PAGE_H-48)


def footer(c,payload):
    c.setStrokeColor(LINE); c.setLineWidth(.5); c.line(M,34,PAGE_W-M,34)
    c.setFont(FONT,5.5); c.setFillColor(MUTED); c.drawString(M,22,'Fontes públicas e cálculos geoespaciais. Consulte limitações e rastreabilidade no encerramento.')
    rid=s(payload.get('report_id'),'')
    if rid: c.drawRightString(PAGE_W-M,22,'ID '+rid)


def title(c,n,title,subtitle=''):
    y=PAGE_H-78
    c.setFont(BOLD,7); c.setFillColor(GREEN); c.drawString(M,y,n)
    c.setFont(BOLD,16); c.setFillColor(INK); c.drawString(M+25,y-1,title)
    if subtitle: wrap(c,subtitle,M+25,y-18,CONTENT_W-25,FONT,7,9,MUTED,2)


def colors(level):
    return {'ok':(GREEN_SOFT,GREEN_DARK),'attention':(AMBER_SOFT,AMBER),'critical':(RED_SOFT,RED),'info':(BLUE_SOFT,BLUE)}.get(level,(SOFT,MUTED))


def badge(c,x,y,text,level='neutral',right=False):
    bg,fg=colors(level); c.setFont(BOLD,6)
    w=max(48,pdfmetrics.stringWidth(s(text),BOLD,6)+14); x0=x-w if right else x
    c.setFillColor(bg); c.roundRect(x0,y,w,16,4,fill=1,stroke=0)
    c.setFillColor(fg); c.drawCentredString(x0+w/2,y+4.7,s(text)); return w


def kpi(c,x,y,w,label,value,note='',level='neutral'):
    _,fg=colors(level); c.setFillColor(WHITE); c.setStrokeColor(LINE); c.roundRect(x,y,w,60,5,fill=1,stroke=1)
    c.setFillColor(fg); c.rect(x,y+57,w,3,fill=1,stroke=0)
    c.setFont(BOLD,5.9); c.setFillColor(MUTED); c.drawString(x+12,y+43,s(label).upper())
    fit(c,value,x+12,y+22,w-24,BOLD,12,7,INK)
    if note: fit(c,note,x+12,y+9,w-24,FONT,5.4,4.9,MUTED)


def summary_row(c,x,y,w,label,text,badge_text,level='neutral',h=42):
    c.setFillColor(WHITE); c.setStrokeColor(LINE); c.roundRect(x,y,w,h,5,fill=1,stroke=1)
    c.setFont(BOLD,6); c.setFillColor(MUTED); c.drawString(x+13,y+h-16,s(label).upper())
    bw=badge(c,x+w-12,y+h-24,badge_text,level,True)
    fit(c,text,x+13,y+11,w-26-bw-8,BOLD,7,5.5,TEXT)


def kv(c,x,y,rows,w=CONTENT_W,row_h=22,label_w=180):
    for i,(label,value) in enumerate(rows):
        c.setFillColor(WHITE if i%2==0 else SOFT); c.rect(x,y-row_h,w,row_h,fill=1,stroke=0)
        c.setStrokeColor(LINE); c.setLineWidth(.3); c.line(x,y-row_h,x+w,y-row_h)
        c.setFont(BOLD,6.3); c.setFillColor(MUTED); c.drawString(x+9,y-row_h+7.5,s(label))
        fit(c,value,x+label_w,y-row_h+7.5,w-label_w-9,FONT,6.8,5.5,TEXT)
        y-=row_h
    return y


def table(c,x,y,widths,headers,rows,row_h=27,font_size=6.1):
    total=sum(widths); c.setFillColor(DARK); c.rect(x,y-row_h,total,row_h,fill=1,stroke=0); cx=x
    for h,w in zip(headers,widths): fit(c,h,cx+9,y-row_h+9,w-18,BOLD,font_size,5.2,WHITE); cx+=w
    y-=row_h
    for i,row in enumerate(rows):
        c.setFillColor(WHITE if i%2==0 else SOFT); c.rect(x,y-row_h,total,row_h,fill=1,stroke=0)
        c.setStrokeColor(LINE); c.setLineWidth(.3); c.line(x,y-row_h,x+total,y-row_h); cx=x
        for v,w in zip(row,widths): fit(c,v,cx+9,y-row_h+9,w-18,FONT,font_size,5.2,TEXT); cx+=w
        y-=row_h
    return y


def map_image(c,path_value,x,y,w,h,title_text):
    c.setFont(BOLD,8.5); c.setFillColor(INK); c.drawString(x,y+h+12,title_text)
    c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(x,y,w,h,5,fill=1,stroke=1)
    if not path_value: return False
    p=Path(str(path_value))
    if not p.exists(): return False
    try:
        img=ImageReader(str(p)); iw,ih=img.getSize(); scale=min((w-8)/iw,(h-8)/ih); dw,dh=iw*scale,ih*scale
        c.drawImage(img,x+(w-dw)/2,y+(h-dh)/2,dw,dh,preserveAspectRatio=True,mask='auto'); return True
    except Exception: return False


class PremiumPropertyReport:
    def __init__(self,payload):
        self.p=payload; self.prop=payload.get('property') or {}; self.car=payload.get('car') or {}; self.land=payload.get('land') or {}; self.env=payload.get('environment') or {}; self.fisc=payload.get('enforcement') or {}; self.mining=payload.get('mining') or {}; self.prod=payload.get('productive') or {}; self.water=payload.get('water') or {}; self.infra=payload.get('infrastructure') or {}; self.mon=payload.get('monitoring') or {}; self.con=payload.get('conclusion') or {}

    def build(self,path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); c=canvas.Canvas(str(path),pagesize=A4)
        pages=[self.page1,self.page2,self.page3,self.page4,self.page5,self.page6,self.page7,self.page8,self.page9,self.page10,self.page11,self.page12,self.page13,self.page14,self.page15]
        for fn in pages: fn(c)
        c.save(); return sha256(path.read_bytes()).hexdigest()

    def page1(self,c):
        header(c,1,'Identificação territorial',self.p)
        c.setFont(BOLD,20); c.setFillColor(INK); c.drawString(M,PAGE_H-88,s(self.prop.get('name'),'Imóvel rural'))
        c.setFont(FONT,7); c.setFillColor(MUTED); c.drawString(M,PAGE_H-108,f"CAR: {s(self.prop.get('car_code'))}   {s(self.prop.get('municipality'))}/{s(self.prop.get('uf'))}")
        map_image(c,self.p.get('map_image_path') or self.p.get('car_map_image_path'),M,415,CONTENT_W,265,'Mapa da propriedade e limite analisado')
        gap=8; w=(CONTENT_W-3*gap)/4; y=330
        kpi(c,M,y,w,'Área',f"{s(self.prop.get('area_ha'))} ha",'área do CAR','info')
        kpi(c,M+w+gap,y,w,'CAR',s(self.car.get('status'),'Não consultado'),'situação cadastral','ok')
        pd=self.env.get('prodes') or {}; kpi(c,M+2*(w+gap),y,w,'PRODES',s(pd.get('count'),0),f"{s(pd.get('area_ha'),0)} ha",'attention' if pd.get('count') else 'ok')
        kpi(c,M+3*(w+gap),y,w,'Processos ANM',s(self.mining.get('process_count'),0),'sobre o imóvel','attention' if self.mining.get('process_count') else 'ok')
        c.setFont(BOLD,8.8); c.setFillColor(INK); c.drawString(M,290,'LEITURA EM 15 SEGUNDOS')
        wrap(c,self.p.get('quick_read','Dados consolidados das fontes consultadas.'),M,270,CONTENT_W,BOLD,9,11,TEXT,4)
        c.setFont(BOLD,8.5); c.drawString(M,205,'PRINCIPAIS PONTOS DE ATENÇÃO'); yy=184
        for item in (self.p.get('attention_points') or [])[:4]:
            c.setFillColor(AMBER); c.circle(M+4,yy+2,2.3,fill=1,stroke=0); wrap(c,item,M+14,yy,CONTENT_W-14,FONT,7,9,TEXT,2); yy-=30
        footer(c,self.p); c.showPage()

    def page2(self,c):
        header(c,2,'Sumário executivo',self.p); title(c,'01','Sumário executivo','Resultado consolidado antes do detalhamento técnico.')
        rows=self.p.get('executive_summary_rows') or []; y=640
        for row in rows[:8]: summary_row(c,M,y,CONTENT_W,s(row[0]),s(row[1]),s(row[2]),s(row[3],'neutral')); y-=52
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,y-6,'Prioridades imediatas'); yy=y-32
        for i,item in enumerate((self.p.get('priorities') or [])[:5],1):
            c.setFillColor(GREEN); c.roundRect(M,yy-4,17,17,4,fill=1,stroke=0); c.setFillColor(WHITE); c.setFont(BOLD,6.5); c.drawCentredString(M+8.5,yy+1,str(i)); wrap(c,item,M+27,yy,CONTENT_W-27,FONT,7,9,TEXT,2); yy-=34
        footer(c,self.p); c.showPage()

    def page3(self,c):
        header(c,3,'Cadastro Ambiental Rural',self.p); title(c,'02','CAR completo','Dados cadastrais, composição ambiental e situação declarada no CAR.')
        map_image(c,self.p.get('car_map_image_path'),M,460,CONTENT_W,190,'Limite CAR')
        rows=self.car.get('fields') or [('CAR',self.prop.get('car_code')),('Município',self.prop.get('municipality')),('Estado',self.prop.get('uf')),('Área',f"{s(self.prop.get('area_ha'))} ha"),('Status',self.car.get('status')),('Tipo',self.car.get('type')),('Módulos fiscais',self.car.get('fiscal_modules')),('Registro',self.car.get('registration_date')),('Retificação',self.car.get('rectification_date')),('Análise',self.car.get('analysis_status'))]
        y=kv(c,M,442,rows[:10])
        if self.car.get('areas'):
            c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,y-20,'Áreas do imóvel, Reserva Legal, vegetação e APP'); kv(c,M,y-32,self.car.get('areas')[:14])
        footer(c,self.p); c.showPage()

    def page4(self,c):
        header(c,4,'Fundiário e cadastral',self.p); title(c,'03','SIGEF, SNCI e vínculo fundiário','Cadastro ambiental, certificação fundiária e registro são apresentados separadamente.')
        certs=self.land.get('certifications') or [['SIGEF','Não consultado','-','Fonte não consultada'],['SNCI','Não consultado','-','Fonte não consultada']]
        table(c,M,650,[95,125,70,221],['Base','Situação','Parcelas','Observação'],certs,30,6)
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,545,'Matriz de vínculo e registro'); table(c,M,530,[110,120,90,191],['Base/campo','Resultado','Área','Leitura'],self.land.get('matrix') or [],28,6)
        ev=self.land.get('evidence') or {}; c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(M,105,CONTENT_W,105,5,fill=1,stroke=1); c.setFont(BOLD,6.4); c.setFillColor(MUTED); c.drawString(M+14,185,'GRAU DE EVIDÊNCIA DOMINIAL'); c.setFont(BOLD,17); c.setFillColor(INK); c.drawString(M+14,156,s(ev.get('score'),'NÃO CONSULTADO')); wrap(c,ev.get('text','A evidência dominial depende das fontes efetivamente consultadas.'),M+14,136,CONTENT_W-28,FONT,7,9,TEXT,5)
        footer(c,self.p); c.showPage()

    def page5(self,c):
        header(c,5,'Sumário de conformidade',self.p); title(c,'04','Sumário de conformidade','Todas as verificações principais em uma única página.'); y=630
        for row in (self.p.get('compliance') or [])[:11]: summary_row(c,M,y,CONTENT_W,s(row.get('label')),s(row.get('text')),s(row.get('badge')),s(row.get('level'),'neutral'),38); y-=46
        footer(c,self.p); c.showPage()

    def page6(self,c):
        header(c,6,'Desmatamento e evidência',self.p); title(c,'05','PRODES e alertas de desmatamento','Ocorrências são mostradas com metadados e área de interseção.')
        map_image(c,self.p.get('problem_map_image_path') or self.p.get('environment_map_image_path'),M,420,CONTENT_W,230,'PRODES e limite do imóvel')
        pd=self.env.get('prodes') or {}; gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,330,w,'Ocorrências PRODES',pd.get('count',0),f"{s(pd.get('area_ha'),0)} ha",'attention' if pd.get('count') else 'ok')
        kpi(c,M+w+gap,330,w,'Área ambiental única',s(self.env.get('unique_problem_area_ha'),0),f"{s(self.env.get('unique_problem_area_pct'),0)}% do imóvel",'attention' if self.env.get('unique_problem_area_ha') else 'ok')
        kpi(c,M+2*(w+gap),330,w,'Alertas adicionais',s(pd.get('additional_alerts'),0),'monitoramento','neutral')
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,300,'Detalhamento da ocorrência'); kv(c,M,285,(pd.get('rows') or [])[:8])
        c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(M,74,CONTENT_W,72,5,fill=1,stroke=1); c.setFont(BOLD,6.2); c.setFillColor(MUTED); c.drawString(M+14,126,'O QUE ISSO SIGNIFICA'); wrap(c,pd.get('meaning','A ocorrência deve ser interpretada conforme a data, classe e fonte consultada.'),M+14,106,CONTENT_W-28,FONT,7,9,TEXT,5)
        footer(c,self.p); c.showPage()

    def page7(self,c):
        header(c,7,'Ambiental e fiscalização',self.p); title(c,'06','Sobreposições, embargos, autos e multas','Problemas ambientais são quantificados por área; sanções aparecem separadamente.')
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,630,w,'Área problemática',f"{s(self.env.get('unique_problem_area_ha'),0)} ha",f"{s(self.env.get('unique_problem_area_pct'),0)}% do imóvel",'attention' if self.env.get('unique_problem_area_ha') else 'ok')
        kpi(c,M+w+gap,630,w,'Embargos',self.fisc.get('embargo_count','-'),'intersectando o imóvel','critical' if self.fisc.get('embargo_count') else 'ok')
        kpi(c,M+2*(w+gap),630,w,'Autos / multas',self.fisc.get('auto_count','-'),s(self.fisc.get('fine_total_text'),'valor nominal'),'critical' if self.fisc.get('auto_count') else 'ok')
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,600,'Camadas socioambientais'); table(c,M,585,[170,245,96],['Camada','Resultado','Fonte'],self.env.get('layer_rows') or [],29,6.2)
        if self.fisc.get('autos'):
            c.setFont(BOLD,9); c.drawString(M,350,'Autos de infração ambiental'); table(c,M,335,[100,100,145,166],['Auto','Data','Valor','Situação'],self.fisc.get('autos')[:7],28,6.1)
        footer(c,self.p); c.showPage()

    def page8(self,c):
        header(c,8,'Inteligência mineral',self.p); title(c,'07','Direitos minerários e minerais estratégicos','Processos ANM são cruzados com o imóvel sem confundir processo com reserva mineral.')
        map_image(c,self.p.get('mineral_map_image_path'),M,445,CONTENT_W,205,'Processos minerários sobre o imóvel')
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,350,w,'Processos ANM',self.mining.get('process_count','-'),f"{s(self.mining.get('overlap_area_ha'),0)} ha",'attention' if self.mining.get('process_count') else 'ok')
        kpi(c,M+w+gap,350,w,'Terras raras',self.mining.get('rare_earth_count','-'),'processos relacionados','critical' if self.mining.get('rare_earth_count') else 'neutral')
        kpi(c,M+2*(w+gap),350,w,'Maior maturidade',s(self.mining.get('max_maturity'),'-'),'escala 1 a 5','info')
        if self.mining.get('processes'):
            c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,320,'Processos identificados'); table(c,M,305,[90,135,95,120,71],['Processo','Titular','Substância','Fase','% imóvel'],self.mining.get('processes')[:8],28,5.8)
        wrap(c,'Processo minerário não comprova ocorrência geológica, jazida ou reserva economicamente aproveitável.',M,85,CONTENT_W,FONT,6.3,8,MUTED,2); footer(c,self.p); c.showPage()

    def page9(self,c):
        header(c,9,'Aptidão e solo',self.p); title(c,'08','Aptidão agrícola e composição do solo','Classes de aptidão e estimativas da camada superficial apresentadas separadamente.')
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,650,'Aptidão agrícola'); table(c,M,635,[365,146],['Classe / leitura','% do imóvel'],self.prod.get('aptitude_rows') or [],31,6.3)
        c.setFont(BOLD,9); c.drawString(M,470,'Composição do solo - camada superficial'); table(c,M,455,[290,221],['Propriedade','Valor'],self.prod.get('soil_rows') or [],27,6.2)
        c.setFont(BOLD,9); c.drawString(M,255,'Relevo e mecanização'); gap=10; w=(CONTENT_W-3*gap)/4
        for i,item in enumerate((self.prod.get('terrain_kpis') or [])[:4]): kpi(c,M+i*(w+gap),160,w,item.get('label'),item.get('value'),item.get('note',''),item.get('level','neutral'))
        footer(c,self.p); c.showPage()

    def page10(self,c):
        header(c,10,'Água e clima',self.p); title(c,'09','Outorgas, irrigação e precipitação','Recursos hídricos e clima são apresentados com período e fonte.')
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,630,w,'Outorgas',self.water.get('grant_count','-'),'dentro do imóvel','info'); kpi(c,M+w+gap,630,w,'Pivôs',self.water.get('pivot_count','-'),'irrigação central','info'); kpi(c,M+2*(w+gap),630,w,'Chuva 30 dias',s(self.water.get('rain_30d'),'-'),s(self.water.get('rain_period'),''),'info')
        if self.water.get('grants'):
            c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,590,'Outorgas - uso de água'); table(c,M,575,[125,145,110,131],['Tipo','Finalidade','Domínio','Vazão média'],self.water.get('grants'),29,6)
        c.setFont(BOLD,9); c.drawString(M,400,'Precipitação'); kv(c,M,385,(self.water.get('rain_rows') or [])[:7])
        c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(M,86,CONTENT_W,78,5,fill=1,stroke=1); c.setFont(BOLD,6.2); c.setFillColor(MUTED); c.drawString(M+14,143,'LEITURA HÍDRICA'); wrap(c,self.water.get('meaning','Disponibilidade hídrica deve ser confirmada na fonte e nos atos vigentes.'),M+14,123,CONTENT_W-28,FONT,7,9,TEXT,5)
        footer(c,self.p); c.showPage()

    def page11(self,c):
        header(c,11,'Infraestrutura e contexto',self.p); title(c,'10','Infraestrutura, patrimônio e produção regional','Distâncias e referências úteis para logística, operação e diligência.'); y=645
        sections=[('Aeródromos',['Aeródromo','Tipo','ICAO/IATA','Distância'],[180,120,100,111],self.infra.get('airports') or []),('CONAB - Armazéns',['Armazém','Município/UF','Capacidade','Distância'],[150,145,110,106],self.infra.get('warehouses') or []),('IPHAN - Patrimônio cultural',['Sítio','Natureza','Distância'],[245,170,96],self.infra.get('iphan') or [])]
        for t,h,w,rows in sections:
            c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,y,t); y-=15
            if rows: y=table(c,M,y,w,h,rows[:4],28,6)-28
            else: c.setFont(FONT,7); c.setFillColor(MUTED); c.drawString(M,y-12,'Não consultado ou nenhuma ocorrência localizada.'); y-=50
        footer(c,self.p); c.showPage()

    def page12(self,c):
        header(c,12,'Monitoramento',self.p); title(c,'11','Alertas e acompanhamento contínuo','O relatório mostra o estado atual; o monitoramento acompanha mudanças depois da emissão.')
        table(c,M,650,[190,210,111],['Alerta','Gatilho','Canal'],self.mon.get('alerts') or [],30,6)
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,410,'Relatórios automáticos'); table(c,M,395,[160,145],['Cadência','Janela'],self.mon.get('cadences') or [],27,6.2)
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,150,w,'NDVI recente',s(self.mon.get('ndvi'),'-'),s(self.mon.get('ndvi_date_source'),''),'info'); kpi(c,M+w+gap,150,w,'Focos dentro - 365d',s(self.mon.get('fire_inside_365d'),'-'),'detecções orbitais','attention'); kpi(c,M+2*(w+gap),150,w,'Focos até 5 km - 365d',s(self.mon.get('fire_5km_365d'),'-'),s(self.mon.get('last_fire'),''),'attention')
        footer(c,self.p); c.showPage()

    def page13(self,c):
        header(c,13,'Conclusão',self.p); title(c,'12','Conclusão do Raio-X Territorial','Classificação consolidada, com o motivo de cada nível apresentado.')
        c.setFillColor(DARK); c.roundRect(M,620,CONTENT_W,75,6,fill=1,stroke=0); c.setFont(BOLD,6.2); c.setFillColor(HexColor('#A9D7BF')); c.drawString(M+14,673,'RISCO GERAL'); c.setFont(BOLD,18); c.setFillColor(WHITE); c.drawString(M+14,646,s(self.con.get('overall_risk'),'NÃO CLASSIFICADO')); wrap(c,self.con.get('overall_reason','Classificação depende das fontes efetivamente consultadas.'),M+190,667,CONTENT_W-205,FONT,7,9,WHITE,4)
        y=565
        for row in (self.con.get('categories') or [])[:7]: summary_row(c,M,y,CONTENT_W,s(row.get('label')),s(row.get('text')),s(row.get('risk')),s(row.get('level'),'neutral'),43); y-=51
        c.setFont(BOLD,8.5); c.setFillColor(INK); c.drawString(M,y-2,'PRINCIPAL PONTO DE ATENÇÃO'); wrap(c,self.con.get('main_attention','Nenhum veredito automático substitui diligência documental.'),M,y-22,CONTENT_W,BOLD,7.3,9.5,TEXT,3)
        y-=70; c.setFont(BOLD,8.5); c.drawString(M,y,'VEREDITO'); wrap(c,self.con.get('verdict','Dados insuficientes para conclusão definitiva.'),M,y-20,CONTENT_W,BOLD,8,10,TEXT,4)
        footer(c,self.p); c.showPage()

    def page14(self,c):
        header(c,14,'Decisão e diligência',self.p); title(c,'13','Pontos positivos, riscos e próximos passos','O relatório organiza o que merece ação.'); cols=[('PONTOS POSITIVOS',self.con.get('positives') or [],GREEN_SOFT,GREEN_DARK),('RISCOS',self.con.get('risks') or [],RED_SOFT,RED),('OPORTUNIDADES',self.con.get('opportunities') or [],BLUE_SOFT,BLUE)]; gap=10; w=(CONTENT_W-2*gap)/3
        for i,(t,items,bg,fg) in enumerate(cols):
            x=M+i*(w+gap); c.setFillColor(bg); c.setStrokeColor(LINE); c.roundRect(x,430,w,220,5,fill=1,stroke=1); c.setFont(BOLD,7); c.setFillColor(fg); c.drawString(x+13,628,t); yy=600
            for item in items[:6]: c.setFillColor(fg); c.circle(x+15,yy+2,2,fill=1,stroke=0); wrap(c,item,x+23,yy,w-36,FONT,6.4,8.2,TEXT,3); yy-=34
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,395,'Verificações recomendadas antes de comprar, financiar ou assumir obrigação'); yy=370
        for i,item in enumerate((self.con.get('diligence') or [])[:7],1): c.setFillColor(GREEN); c.roundRect(M,yy-4,17,17,4,fill=1,stroke=0); c.setFillColor(WHITE); c.setFont(BOLD,6.5); c.drawCentredString(M+8.5,yy+1,str(i)); wrap(c,item,M+27,yy,CONTENT_W-27,FONT,6.7,8.5,TEXT,2); yy-=32
        c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(M,76,CONTENT_W,74,5,fill=1,stroke=1); c.setFont(BOLD,6.2); c.setFillColor(MUTED); c.drawString(M+14,129,'LIMITE DA CONCLUSÃO'); wrap(c,self.con.get('limit','O Raio-X Territorial consolida evidências públicas e cálculos geoespaciais. Não substitui certidão registral, vistoria, laudo técnico ou parecer jurídico quando exigidos.'),M+14,109,CONTENT_W-28,FONT,6.4,8.2,TEXT,5)
        footer(c,self.p); c.showPage()

    def page15(self,c):
        header(c,15,'Fontes e rastreabilidade',self.p); title(c,'14','Fontes, metodologia e rastreabilidade','Cada conclusão deve ser rastreável até a fonte, data e regra espacial usada.'); y=650
        for src in (self.p.get('sources') or [])[:10]: summary_row(c,M,y,CONTENT_W,s(src.get('name')),s(src.get('description')),s(src.get('status'),'NÃO CONSULTADA'),s(src.get('level'),'neutral'),39); y-=46
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,y-5,'Regras de interpretação'); yy=y-30
        for t in self.p.get('interpretation_rules') or []: c.setFillColor(MUTED); c.circle(M+3,yy+2,1.8,fill=1,stroke=0); wrap(c,t,M+13,yy,CONTENT_W-13,FONT,6.4,8.2,TEXT,2); yy-=27
        c.setFillColor(DARK); c.roundRect(M,70,CONTENT_W,65,5,fill=1,stroke=0); c.setFont(BOLD,6.2); c.setFillColor(HexColor('#A9D7BF')); c.drawString(M+14,113,'INTEGRIDADE DO ARQUIVO'); c.setFont(BOLD,8); c.setFillColor(WHITE); c.drawString(M+14,94,f"Relatório {s(self.p.get('report_id'))} - gerado {s(self.p.get('generated_at'))}"); c.setFont(FONT,5.8); c.drawString(M+14,79,s(self.p.get('source_version'),'Versões, fontes, datas e critérios registrados nesta página.'))
        footer(c,self.p); c.showPage()


def build_premium_property_report(path: str | Path, payload: dict[str, Any]) -> str:
    return PremiumPropertyReport(payload).build(path)

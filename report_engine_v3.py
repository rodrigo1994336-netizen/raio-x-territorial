from __future__ import annotations

from pathlib import Path
from typing import Any

from report_engine_v2 import PremiumPropertyReportV2
from report_engine import CONTENT_W, M, INK, SOFT, LINE, MUTED, TEXT, FONT, BOLD, header, title, table, kpi, kv, wrap, footer, s


class PremiumPropertyReportV3(PremiumPropertyReportV2):
    def page10(self,c):
        header(c,10,'Água e clima',self.p)
        title(c,'09','Outorgas, irrigação e precipitação','Recursos hídricos e clima são apresentados com período e fonte; ausência de consulta nunca é tratada como ausência de direito de uso.')
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,630,w,'Outorgas no imóvel',self.water.get('grant_count','-'),'interseção espacial','info')
        kpi(c,M+w+gap,630,w,'Pivôs',self.water.get('pivot_count','NÃO CONSULTADO'),'irrigação central','info')
        kpi(c,M+2*(w+gap),630,w,'Chuva 30 dias',s(self.water.get('rain_30d'),'NÃO CONSULTADO'),s(self.water.get('rain_period'),''),'info')
        rows=self.water.get('grants') or []
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,590,'Outorgas intersectantes - IDE-Sisema / IGAM')
        if rows:
            table(c,M,575,[105,92,100,143,71],['Processo','Portaria','Situação','Uso / tipo','Distância'],rows[:8],29,5.7)
        else:
            c.setFont(FONT,7); c.setFillColor(MUTED); c.drawString(M,558,'Nenhuma outorga intersectante listada ou fonte indisponível; veja a leitura hídrica e a rastreabilidade.')
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,400,'Precipitação')
        rain=self.water.get('rain_rows') or []
        if rain:
            kv(c,M,385,rain[:7])
        else:
            c.setFont(FONT,7); c.setFillColor(MUTED); c.drawString(M,365,'Precipitação ainda não consultada nesta emissão.')
        c.setFillColor(SOFT); c.setStrokeColor(LINE); c.roundRect(M,86,CONTENT_W,78,5,fill=1,stroke=1)
        c.setFont(BOLD,6.2); c.setFillColor(MUTED); c.drawString(M+14,143,'LEITURA HÍDRICA')
        wrap(c,self.water.get('meaning','Disponibilidade hídrica deve ser confirmada na fonte e nos atos vigentes.'),M+14,123,CONTENT_W-28,FONT,7,9,TEXT,5)
        footer(c,self.p); c.showPage()


def build_premium_property_report_v3(path: str | Path, payload: dict[str,Any]) -> str:
    return PremiumPropertyReportV3(payload).build(path)

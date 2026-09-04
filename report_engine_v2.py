from __future__ import annotations

from pathlib import Path
from typing import Any

from report_engine import (
    PremiumPropertyReport, CONTENT_W, M, INK, FONT, BOLD,
    header, title, table, kpi, footer, s
)


class PremiumPropertyReportV2(PremiumPropertyReport):
    def page12(self,c):
        header(c,12,'Monitoramento',self.p)
        title(c,'11','Alertas e acompanhamento contínuo','O relatório mostra o estado atual; o monitoramento acompanha mudanças depois da emissão.')
        table(c,M,650,[190,210,111],['Alerta','Gatilho','Canal'],self.mon.get('alerts') or [],30,6)
        c.setFont(BOLD,9); c.setFillColor(INK); c.drawString(M,410,'Relatórios automáticos')
        table(c,M,395,[160,145],['Cadência','Janela'],self.mon.get('cadences') or [],27,6.2)
        gap=10; w=(CONTENT_W-2*gap)/3
        kpi(c,M,150,w,self.mon.get('ndvi_label','NDVI recente'),s(self.mon.get('ndvi'),'-'),s(self.mon.get('ndvi_date_source'),''),'info')
        kpi(c,M+w+gap,150,w,self.mon.get('fire_inside_label','Focos dentro - janela recente'),s(self.mon.get('fire_inside'),'-'),s(self.mon.get('fire_inside_note'),'detecções orbitais'),'attention')
        kpi(c,M+2*(w+gap),150,w,self.mon.get('fire_near_label','Focos próximos - janela recente'),s(self.mon.get('fire_near'),'-'),s(self.mon.get('fire_near_note'),''),'attention')
        footer(c,self.p); c.showPage()


def build_premium_property_report_v2(path: str | Path, payload: dict[str,Any]) -> str:
    return PremiumPropertyReportV2(payload).build(path)

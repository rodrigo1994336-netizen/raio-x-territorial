from __future__ import annotations

import report_engine_v6 as v6
import report_engine_v7 as v7
from reportlab.lib.units import mm
from reportlab.platypus import Spacer, PageBreak, Paragraph


def build_premium_property_report_v8(path,payload):
    """V7 renderer plus a dedicated street-level/access evidence page.

    KartaView imagery is inserted only when a public georeferenced photo was
    actually found. Google Street View remains a live link; its imagery is not
    downloaded or embedded into the PDF.
    """
    street=payload.get('street_level_access') or {}
    original_section=v6._section

    def section(title,subtitle):
        if title!='Ambiental e fiscalização':
            return original_section(title,subtitle)
        blocks=original_section('Acesso e imagem de via pública','Evidência visual independente do satélite. A proximidade ao limite do CAR não prova que o ponto seja o portão oficial da fazenda.')
        if street.get('ok') and street.get('path'):
            label=street.get('label') or 'Imagem de via pública próxima ao imóvel'
            dist=street.get('distance_to_car_m')
            caption=(f"{label}. Distância aproximada ao limite do CAR: {dist if dist is not None else '—'} m. "
                     f"Data da imagem: {street.get('shot_date') or 'não informada'}. "
                     f"Fonte/licença: {street.get('attribution') or '© Grab and KartaView Contributors — CC BY-SA 4.0'}.")
            blocks += [Spacer(1,3*mm)] + v6._image(street.get('path'),caption)
            blocks += [Spacer(1,3*mm),v6._callout('LEITURA CORRETA',street.get('note') or 'Imagem georreferenciada de via pública próxima ao CAR; não confirma a entrada oficial sem validação de campo.','info')]
        else:
            blocks += [v6._callout('IMAGEM DE VIA PÚBLICA','Nenhuma imagem aberta KartaView foi localizada no entorno consultado. Isso é ausência de cobertura da fonte, não ausência de acesso viário.','attention')]
        if street.get('google_maps_streetview_url'):
            url=street.get('google_maps_streetview_url')
            blocks += [Spacer(1,3*mm),Paragraph(f'<b>Street View ao vivo:</b> <link href="{url}" color="#0E603B">abrir o ponto no Google Maps</link>',v6.S['body'])]
        blocks += [PageBreak()]
        blocks += original_section(title,subtitle)
        return blocks

    v6._section=section
    try:
        return v7.build_premium_property_report_v7(path,payload)
    finally:
        v6._section=original_section


print('RX_REPORT_ENGINE=V8_STREET_LEVEL_ACCESS',flush=True)

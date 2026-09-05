from __future__ import annotations

from pathlib import Path

import report_engine_v6 as v6


def build_premium_property_report_v7(path:str|Path,payload:dict)->str:
    """V6 renderer with truthful captions for the premium aerial hero."""
    original=v6._image
    highres=payload.get('visual_reference_image_path');sat=payload.get('satellite_imagery') or {};plate=sat.get('visual_plate_path') or payload.get('visual_plate_image_path');prop=payload.get('property') or {}
    def _image(path_value,caption,*args,**kwargs):
        p=str(path_value or '');c=str(caption or '')
        if plate and p==str(plate):
            hero=(sat.get('visual_plate') or {}).get('hero_source')
            if hero=='highres':
                c=(f"Imagem aérea de alta resolução de {prop.get('name') or 'imóvel rural'}, com enquadramento fechado e limite do CAR destacado em verde. "
                   f"Sentinel-2 {sat.get('date') or 'data não informada'} e NDVI permanecem como evidências temporais/espectrais separadas no dossiê.")
            else:
                c=(f"Imagem Sentinel-2 de {prop.get('name') or 'imóvel rural'} usada como fallback porque a referência aérea de alta resolução não respondeu nesta emissão. "
                   "A indisponibilidade visual não altera a geometria do CAR analisado.")
        elif highres and p==str(highres):
            c=(f"Imagem aérea de alta resolução de {prop.get('name') or 'imóvel rural'}, com o limite do CAR contornado. Fonte visual: Esri World Imagery. "
               f"A evidência datada permanece separada: Sentinel-2 {sat.get('date') or 'data não informada'}.")
        return original(path_value,c,*args,**kwargs)
    v6._image=_image
    try:return v6.build_premium_property_report_v6(path,payload)
    finally:v6._image=original


print('RX_REPORT_ENGINE=V42_PREMIUM_HERO_CAPTION',flush=True)

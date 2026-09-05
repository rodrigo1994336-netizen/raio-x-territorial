from __future__ import annotations

from pathlib import Path

import report_engine_v6 as v6


def build_premium_property_report_v7(path:str|Path,payload:dict)->str:
    """V6 renderer with truthful captions for V25 visual plate and high-res imagery."""
    original=v6._image
    highres=payload.get('visual_reference_image_path')
    sentinel=payload.get('sentinel_image_path')
    sat=payload.get('satellite_imagery') or {}
    plate=sat.get('visual_plate_path') or payload.get('visual_plate_image_path')
    prop=payload.get('property') or {}

    def _image(path_value,caption,*args,**kwargs):
        p=str(path_value or '')
        c=str(caption or '')
        if plate and p==str(plate):
            c=(
                f"Prancha visual de {prop.get('name') or 'imóvel rural'}: imagem de referência em alta resolução com o CAR em destaque, "
                f"cena Sentinel-2 datada de {sat.get('date') or 'data não informada'} e NDVI calculado no mesmo perímetro. "
                "A alta resolução é usada para reconhecimento visual; Sentinel-2/NDVI sustentam a leitura temporal e espectral."
            )
        elif highres and p==str(highres) and 'Imagem orbital real Sentinel-2' in c:
            c=(
                f"Imagem de referência em alta resolução de {prop.get('name') or 'imóvel rural'}, com o limite do CAR contornado. "
                "Fonte visual: Esri World Imagery. "
                f"A evidência científica datada permanece separada: Sentinel-2 {sat.get('date') or 'data não informada'}, usada no NDVI."
            )
        return original(path_value,c,*args,**kwargs)

    v6._image=_image
    try:return v6.build_premium_property_report_v6(path,payload)
    finally:v6._image=original


print('RX_REPORT_ENGINE=V7_V25_VISUAL_PLATE_CAPTION',flush=True)

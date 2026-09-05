from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from shapely.geometry import shape


def _font(size:int,bold:bool=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(p,size)
    except Exception:return None


def _fit(im:Image.Image,size:tuple[int,int])->Image.Image:
    return ImageOps.fit(im.convert('RGB'),size,method=Image.Resampling.LANCZOS,centering=(0.5,0.5))


def _open(path:str|None):
    if not path:return None
    p=Path(path)
    if not p.exists():return None
    try:return Image.open(p).convert('RGB')
    except Exception:return None


def _panel(draw:ImageDraw.ImageDraw,box,fill=(8,24,18,238),outline=(61,99,80,255),radius=22):
    draw.rounded_rectangle(box,radius=radius,fill=fill,outline=outline,width=2)


def build_property_visual_plate(
    out_path:str|Path,
    car_geometry:dict[str,Any],
    highres_path:str|None,
    sentinel_path:str|None,
    ndvi_path:str|None,
    sentinel_meta:dict[str,Any]|None=None,
)->dict[str,Any]:
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    sentinel_meta=sentinel_meta or {}
    hi=_open(highres_path);sent=_open(sentinel_path);ndvi=_open(ndvi_path)
    if hi is None and sent is None:return {'ok':False,'detail':'no_visual_source'}

    W,H=1900,1240
    bg=Image.new('RGB',(W,H),(6,17,13));draw=ImageDraw.Draw(bg,'RGBA')
    # Header
    draw.text((62,42),'PRANCHA VISUAL DA PROPRIEDADE',fill=(245,253,248),font=_font(42,True))
    draw.text((62,96),'O mesmo CAR visto em três lentes: reconhecimento visual, imagem orbital datada e vigor vegetativo.',fill=(166,196,179),font=_font(20))
    try:
        g=shape(car_geometry);c=g.centroid
        center=f'{abs(c.y):.5f}° {"S" if c.y<0 else "N"}  •  {abs(c.x):.5f}° {"W" if c.x<0 else "E"}'
    except Exception:center='centroide não calculado'
    draw.text((W-590,50),center,fill=(125,158,141),font=_font(16))

    main=(48,155,1260,1110);side1=(1290,155,1852,595);side2=(1290,625,1852,1065)
    _panel(draw,main);_panel(draw,side1);_panel(draw,side2)

    src=hi or sent
    main_im=_fit(src,(main[2]-main[0]-16,main[3]-main[1]-16))
    bg.paste(main_im,(main[0]+8,main[1]+8))
    # dark translucent caption band
    draw.rounded_rectangle((main[0]+28,main[1]+28,main[0]+690,main[1]+112),radius=16,fill=(4,17,12,215))
    draw.text((main[0]+48,main[1]+45),'1 · VISÃO DE RECONHECIMENTO',fill='white',font=_font(27,True))
    draw.text((main[0]+48,main[1]+78),'Alta resolução com o perímetro do CAR em destaque',fill=(198,229,211),font=_font(17))

    if sent:
        x=_fit(sent,(side1[2]-side1[0]-16,side1[3]-side1[1]-16));bg.paste(x,(side1[0]+8,side1[1]+8))
        draw.rounded_rectangle((side1[0]+22,side1[1]+22,side1[0]+450,side1[1]+84),radius=14,fill=(4,17,12,215))
        draw.text((side1[0]+38,side1[1]+36),'2 · SENTINEL-2 DATADO',fill='white',font=_font(22,True))
    else:
        draw.text((side1[0]+34,side1[1]+48),'Sentinel-2 indisponível nesta emissão',fill=(255,200,102),font=_font(21,True))

    if ndvi:
        x=_fit(ndvi,(side2[2]-side2[0]-16,side2[3]-side2[1]-16));bg.paste(x,(side2[0]+8,side2[1]+8))
        draw.rounded_rectangle((side2[0]+22,side2[1]+22,side2[0]+400,side2[1]+84),radius=14,fill=(4,17,12,215))
        draw.text((side2[0]+38,side2[1]+36),'3 · NDVI DO CAR',fill='white',font=_font(22,True))
    else:
        draw.text((side2[0]+34,side2[1]+48),'NDVI indisponível nesta emissão',fill=(255,200,102),font=_font(21,True))

    # Bottom evidence strip
    draw.rounded_rectangle((48,1132,1852,1210),radius=18,fill=(10,31,22,255),outline=(43,76,60,255),width=2)
    dt=sentinel_meta.get('date') or '—';cloud=sentinel_meta.get('cloud_cover_pct');res=sentinel_meta.get('resolution_m') or 10
    nd=sentinel_meta.get('ndvi_mean')
    txt=f'Sentinel-2: {dt}  •  resolução {res} m  •  nuvens {round(float(cloud),1) if cloud is not None else "—"}%  •  NDVI médio {nd if nd is not None else "—"}'
    draw.text((72,1153),txt,fill=(219,239,227),font=_font(18,True))
    draw.text((72,1182),'A prancha é evidência visual e analítica do imóvel selecionado; não é ilustração genérica.',fill=(151,183,165),font=_font(15))

    bg.save(out_path,'JPEG',quality=94,optimize=True)
    return {'ok':True,'path':str(out_path),'width':W,'height':H,'source':'Esri World Imagery + Copernicus Sentinel-2 + NDVI','note':'Prancha comparativa do mesmo CAR, composta apenas quando ao menos uma fonte visual real respondeu.'}


print('RX_PROPERTY_VISUAL_PLATE_V25=premium_three_lens_cover',flush=True)

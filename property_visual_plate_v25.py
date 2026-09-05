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


def _clean(v,default='—'):
    s=' '.join(str(v or '').strip().split())
    return s if s else default


def _short(v,n=54):
    s=_clean(v)
    return s if len(s)<=n else s[:n-1]+'…'


def build_property_visual_plate(
    out_path:str|Path,
    car_geometry:dict[str,Any],
    highres_path:str|None,
    sentinel_path:str|None,
    ndvi_path:str|None,
    sentinel_meta:dict[str,Any]|None=None,
    property_meta:dict[str,Any]|None=None,
)->dict[str,Any]:
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    sentinel_meta=sentinel_meta or {};property_meta=property_meta or {}
    hi=_open(highres_path);sent=_open(sentinel_path);ndvi=_open(ndvi_path)
    if hi is None and sent is None:return {'ok':False,'detail':'no_visual_source'}

    W,H=1900,1240
    bg=Image.new('RGB',(W,H),(6,17,13));draw=ImageDraw.Draw(bg,'RGBA')

    name=_clean(property_meta.get('name'),'Imóvel rural')
    municipality=_clean(property_meta.get('municipality'),'—')
    uf=_clean(property_meta.get('uf'),'—')
    car_code=_clean(property_meta.get('car_code'),'—')
    area=property_meta.get('area_ha')
    try:area_txt=f'{float(area):,.3f}'.replace(',','X').replace('.',',').replace('X','.')+' ha'
    except Exception:area_txt='—'

    # Header: property identity first, product title second.
    draw.text((62,34),'RAIO-X VISUAL DA PROPRIEDADE',fill=(99,230,165),font=_font(22,True))
    draw.text((62,69),_short(name,58),fill=(245,253,248),font=_font(39,True))
    draw.text((62,119),f'{municipality}/{uf}  •  {area_txt}  •  CAR {car_code}',fill=(166,196,179),font=_font(17))
    try:
        g=shape(car_geometry);c=g.centroid
        center=f'{abs(c.y):.5f}° {"S" if c.y<0 else "N"}  •  {abs(c.x):.5f}° {"W" if c.x<0 else "E"}'
    except Exception:center='centróide não calculado'
    draw.text((W-555,52),center,fill=(125,158,141),font=_font(15))
    draw.text((W-555,86),'Mesmo perímetro • três leituras independentes',fill=(166,196,179),font=_font(15,True))

    main=(48,164,1260,1110);side1=(1290,164,1852,595);side2=(1290,625,1852,1065)
    _panel(draw,main);_panel(draw,side1);_panel(draw,side2)

    src=hi or sent
    main_im=_fit(src,(main[2]-main[0]-16,main[3]-main[1]-16))
    bg.paste(main_im,(main[0]+8,main[1]+8))
    # compact labels; avoid hiding the property itself.
    draw.rounded_rectangle((main[0]+26,main[1]+26,main[0]+605,main[1]+102),radius=16,fill=(4,17,12,218))
    draw.text((main[0]+44,main[1]+40),'1 · RECONHECIMENTO DO IMÓVEL',fill='white',font=_font(25,True))
    draw.text((main[0]+44,main[1]+72),'Alta resolução • limite do CAR destacado',fill=(198,229,211),font=_font(16))

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

    # Evidence strip: explain in one line what each lens contributes.
    draw.rounded_rectangle((48,1132,1852,1212),radius=18,fill=(10,31,22,255),outline=(43,76,60,255),width=2)
    dt=sentinel_meta.get('date') or '—';cloud=sentinel_meta.get('cloud_cover_pct');res=sentinel_meta.get('resolution_m') or 10
    nd=sentinel_meta.get('ndvi_mean')
    txt=f'Sentinel-2 {dt}  •  {res} m  •  nuvens {round(float(cloud),1) if cloud is not None else "—"}%  •  NDVI médio {nd if nd is not None else "—"}'
    draw.text((72,1149),txt,fill=(219,239,227),font=_font(17,True))
    draw.text((72,1180),'Alta resolução = reconhecimento visual  •  Sentinel = evidência temporal  •  NDVI = vigor/cobertura verde no momento da cena',fill=(151,183,165),font=_font(14))

    bg.save(out_path,'JPEG',quality=95,optimize=True)
    return {
        'ok':True,'path':str(out_path),'width':W,'height':H,
        'source':'Esri World Imagery + Copernicus Sentinel-2 + NDVI',
        'property_name':name,'car_code':car_code,'area_ha':area,
        'note':'Prancha comparativa do mesmo CAR, composta apenas quando ao menos uma fonte visual real respondeu.'
    }


print('RX_PROPERTY_VISUAL_PLATE_V28=property_identity_three_lens_cover',flush=True)

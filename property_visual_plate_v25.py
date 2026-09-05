from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image,ImageDraw,ImageFont,ImageOps
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


def _clean(v,default='—'):
    s=' '.join(str(v or '').strip().split())
    return s if s else default


def _short(v,n=58):
    s=_clean(v)
    return s if len(s)<=n else s[:n-1]+'…'


def build_property_visual_plate(out_path:str|Path,car_geometry:dict[str,Any],highres_path:str|None,sentinel_path:str|None,ndvi_path:str|None,sentinel_meta:dict[str,Any]|None=None,property_meta:dict[str,Any]|None=None)->dict[str,Any]:
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    sentinel_meta=sentinel_meta or {};property_meta=property_meta or {}
    hi=_open(highres_path);sent=_open(sentinel_path)
    if hi is None and sent is None:return {'ok':False,'detail':'no_visual_source'}
    W,H=1900,1110;bg=Image.new('RGB',(W,H),(6,17,13));draw=ImageDraw.Draw(bg,'RGBA')
    name=_clean(property_meta.get('name'),'Imóvel rural');municipality=_clean(property_meta.get('municipality'),'—');uf=_clean(property_meta.get('uf'),'—');car_code=_clean(property_meta.get('car_code'),'—');area=property_meta.get('area_ha')
    try:area_txt=f'{float(area):,.3f}'.replace(',','X').replace('.',',').replace('X','.')+' ha'
    except Exception:area_txt='—'
    draw.text((58,28),'RAIO-X VISUAL DA PROPRIEDADE',fill=(99,230,165),font=_font(21,True));draw.text((58,62),_short(name),fill=(246,253,249),font=_font(38,True));draw.text((58,111),f'{municipality}/{uf}  •  {area_txt}  •  CAR {car_code}',fill=(170,201,184),font=_font(16))
    try:g=shape(car_geometry);c=g.centroid;center=f'{abs(c.y):.5f}° {"S" if c.y<0 else "N"}  •  {abs(c.x):.5f}° {"W" if c.x<0 else "E"}'
    except Exception:center='centróide não calculado'
    draw.text((W-520,54),center,fill=(132,164,147),font=_font(14));draw.text((W-520,84),'IMÓVEL PROTAGONISTA • ENQUADRAMENTO FECHADO',fill=(174,204,187),font=_font(13,True))
    frame=(46,150,1854,985);draw.rounded_rectangle(frame,radius=24,fill=(8,24,18),outline=(58,102,81),width=2)
    src=hi or sent;hero=_fit(src,(frame[2]-frame[0]-14,frame[3]-frame[1]-14));bg.paste(hero,(frame[0]+7,frame[1]+7))
    tag='IMAGEM AÉREA EM ALTA RESOLUÇÃO' if hi is not None else 'SENTINEL-2 — FALLBACK VISUAL'
    draw.rounded_rectangle((frame[0]+24,frame[1]+24,frame[0]+650,frame[1]+94),radius=15,fill=(4,17,12,205),outline=(255,255,255,28),width=1);draw.text((frame[0]+42,frame[1]+39),tag,fill='white',font=_font(23,True));draw.text((frame[0]+42,frame[1]+69),'Limite do CAR destacado • sem poluição cartográfica',fill=(199,231,213),font=_font(15))
    draw.rounded_rectangle((46,1004,1854,1082),radius=17,fill=(10,31,22),outline=(43,76,60),width=2)
    dt=sentinel_meta.get('date') or '—';cloud=sentinel_meta.get('cloud_cover_pct');nd=sentinel_meta.get('ndvi_mean');res=sentinel_meta.get('resolution_m') or 10
    line=f'Sentinel-2 {dt} • {res} m • nuvens {round(float(cloud),1) if cloud is not None else "—"}% • NDVI médio {nd if nd is not None else "—"}'
    draw.text((70,1021),line,fill=(219,239,227),font=_font(16,True));draw.text((70,1052),'A capa prioriza reconhecimento visual. Sentinel-2 e NDVI permanecem como evidência temporal/espectral no dossiê.',fill=(151,183,165),font=_font(13))
    bg.save(out_path,'JPEG',quality=95,optimize=True)
    return {'ok':True,'path':str(out_path),'width':W,'height':H,'source':'Esri World Imagery + Copernicus Sentinel-2/NDVI','property_name':name,'car_code':car_code,'area_ha':area,'hero_source':'highres' if hi is not None else 'sentinel_fallback','note':'Capa V42 com imagem aérea protagonista; Sentinel-2 e NDVI permanecem como evidência técnica sem competir com a leitura da foto principal.'}


print('RX_PROPERTY_VISUAL_PLATE_V42=premium_clean_hero_cover',flush=True)

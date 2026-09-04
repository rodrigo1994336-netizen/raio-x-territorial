from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from PIL import Image,ImageDraw,ImageFont
from shapely.geometry import shape

BASE='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
ATTRIBUTION='Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community'


def _font(size:int,bold=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(p,size)
    except Exception:return None


def _rings(g):
    if g.geom_type=='Polygon':return [g.exterior]
    return [x.exterior for x in getattr(g,'geoms',[]) if x.geom_type=='Polygon']


async def build_highres_reference_image(car_geometry:dict[str,Any],out_path:str|Path):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    try:g=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'Esri World Imagery','detail':f'geometry:{e}'}
    minx,miny,maxx,maxy=g.bounds;dx=max(maxx-minx,.0004);dy=max(maxy-miny,.0004)
    # Enough context to visually recognise access roads, fields and neighbouring features.
    pad=max(dx,dy)*.55
    left,bottom,right,top=minx-pad,miny-pad,maxx+pad,maxy+pad
    aspect=max((right-left)/(top-bottom),.35)
    width=1600;height=max(900,min(1500,int(width/aspect)))
    params={
        'bbox':f'{left},{bottom},{right},{top}','bboxSR':'4326','imageSR':'4326',
        'size':f'{width},{height}','format':'jpg','transparent':'false','f':'image'
    }
    url=BASE+'?'+urlencode(params)
    try:
        async with httpx.AsyncClient(timeout=60,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/highres-reference'}) as c:
            r=await c.get(url);r.raise_for_status();raw=r.content
        if len(raw)<20_000:return {'ok':False,'source':'Esri World Imagery','detail':f'payload_too_small:{len(raw)}','content_type':r.headers.get('content-type')}
        tmp=out_path.with_suffix('.source.jpg');tmp.write_bytes(raw)
        img=Image.open(tmp).convert('RGB');w,h=img.size;draw=ImageDraw.Draw(img,'RGBA')
        def px(lon,lat):
            return ((lon-left)/(right-left)*w,(top-lat)/(top-bottom)*h)
        for ring in _rings(g):
            pts=[px(float(x),float(y)) for x,y in ring.coords]
            if len(pts)>=3:
                draw.line(pts,fill=(255,238,70,255),width=max(5,int(w/250)),joint='curve')
                draw.line(pts,fill=(5,25,14,255),width=max(2,int(w/650)),joint='curve')
        panel_h=112
        draw.rounded_rectangle((26,24,min(w-26,990),24+panel_h),radius=16,fill=(4,17,12,220),outline=(255,255,255,55),width=1)
        draw.text((46,41),'IMAGEM DE REFERÊNCIA EM ALTA RESOLUÇÃO',fill='white',font=_font(28,True))
        draw.text((46,80),'Limite do CAR contornado em amarelo',fill=(211,239,219),font=_font(19))
        draw.rectangle((30,h-72,55,h-47),fill=(255,238,70,255));draw.text((66,h-76),'Limite do CAR',fill='white',font=_font(18))
        tw=draw.textbbox((0,0),ATTRIBUTION,font=_font(13))[2]
        draw.rounded_rectangle((max(22,w-tw-56),h-72,w-22,h-34),radius=10,fill=(0,0,0,155))
        draw.text((max(34,w-tw-44),h-65),ATTRIBUTION,fill=(235,235,235),font=_font(13))
        img.save(out_path,'JPEG',quality=94,optimize=True)
        try:tmp.unlink()
        except Exception:pass
        return {
            'ok':True,'path':str(out_path),'source':'Esri World Imagery','attribution':ATTRIBUTION,
            'width':w,'height':h,'bbox':[left,bottom,right,top],
            'note':'Mosaico de referência visual de alta resolução. Pode combinar imagens de satélite e aerofotogrametria de diferentes provedores e datas; não é usado como fonte temporal de NDVI/desmatamento.'
        }
    except Exception as e:
        return {'ok':False,'source':'Esri World Imagery','detail':f'{type(e).__name__}:{str(e)[:260]}'}


print('RX_HIGHRES_REFERENCE=world_imagery_outlined',flush=True)

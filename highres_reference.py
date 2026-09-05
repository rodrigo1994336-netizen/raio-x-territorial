from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from PIL import Image,ImageDraw,ImageFont
from pyproj import Geod
from shapely.geometry import shape

BASE='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
ATTRIBUTION='Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
GEOD=Geod(ellps='WGS84')


def _font(size:int,bold=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(p,size)
    except Exception:return None


def _rings(g):
    if g.geom_type=='Polygon':return [g.exterior]
    return [x.exterior for x in getattr(g,'geoms',[]) if x.geom_type=='Polygon']


def _nice_scale(meters:float)->float:
    choices=(50,100,200,500,1000,2000,5000,10000,20000)
    target=max(20,meters*.22)
    return max([x for x in choices if x<=target] or [50])


async def build_highres_reference_image(car_geometry:dict[str,Any],out_path:str|Path):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    try:g=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'Esri World Imagery','detail':f'geometry:{e}'}
    minx,miny,maxx,maxy=g.bounds;dx=max(maxx-minx,.00035);dy=max(maxy-miny,.00035);pad=max(dx,dy)*.23
    left,bottom,right,top=minx-pad,miny-pad,maxx+pad,maxy+pad;aspect=max((right-left)/(top-bottom),.42)
    width=1800;height=max(980,min(1500,int(width/aspect)))
    params={'bbox':f'{left},{bottom},{right},{top}','bboxSR':'4326','imageSR':'4326','size':f'{width},{height}','format':'jpg','transparent':'false','f':'image'}
    url=BASE+'?'+urlencode(params)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=5),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/highres-reference-v41'}) as c:
            r=await c.get(url);r.raise_for_status();raw=r.content
        if len(raw)<20_000:return {'ok':False,'source':'Esri World Imagery','detail':f'payload_too_small:{len(raw)}','content_type':r.headers.get('content-type')}
        tmp=out_path.with_suffix('.source.jpg');tmp.write_bytes(raw);img=Image.open(tmp).convert('RGB');w,h=img.size;draw=ImageDraw.Draw(img,'RGBA')
        def px(lon,lat):return ((lon-left)/(right-left)*w,(top-lat)/(top-bottom)*h)
        for ring in _rings(g):
            pts=[px(float(x),float(y)) for x,y in ring.coords]
            if len(pts)>=3:
                draw.polygon(pts,fill=(255,235,55,34));draw.line(pts,fill=(0,0,0,230),width=max(9,int(w/175)),joint='curve');draw.line(pts,fill=(255,238,70,255),width=max(5,int(w/300)),joint='curve')
        draw.rounded_rectangle((28,26,640,112),radius=18,fill=(4,17,12,220),outline=(255,255,255,45),width=1);draw.text((48,43),'PROPRIEDADE EM ALTA RESOLUÇÃO',fill='white',font=_font(26,True));draw.text((48,78),'CAR destacado em amarelo',fill=(205,233,216),font=_font(17))
        nx,ny=w-92,54;draw.rounded_rectangle((w-140,24,w-34,150),radius=18,fill=(4,17,12,205));draw.polygon([(nx,42),(nx-18,94),(nx,82),(nx+18,94)],fill=(255,255,255,245));draw.text((nx-12,105),'N',fill='white',font=_font(24,True))
        midlat=(bottom+top)/2;width_m=abs(GEOD.inv(left,midlat,right,midlat)[2]);scale_m=_nice_scale(width_m);bar=max(60,int(scale_m/max(width_m,1)*w));x0,y0=40,h-68
        draw.rounded_rectangle((24,h-92,24+bar+190,h-26),radius=14,fill=(0,0,0,170));draw.line((x0,y0,x0+bar,y0),fill='white',width=6);draw.line((x0,y0-8,x0,y0+8),fill='white',width=4);draw.line((x0+bar,y0-8,x0+bar,y0+8),fill='white',width=4)
        label=f'{int(scale_m/1000)} km' if scale_m>=1000 else f'{int(scale_m)} m';draw.text((x0+bar+18,y0-14),label,fill='white',font=_font(18,True));tw=draw.textbbox((0,0),ATTRIBUTION,font=_font(12))[2];draw.rounded_rectangle((max(24,w-tw-54),h-78,w-24,h-34),radius=10,fill=(0,0,0,150));draw.text((max(36,w-tw-42),h-68),ATTRIBUTION,fill=(230,230,230),font=_font(12))
        img.save(out_path,'JPEG',quality=95,optimize=True)
        try:tmp.unlink()
        except Exception:pass
        return {'ok':True,'path':str(out_path),'source':'Esri World Imagery','attribution':ATTRIBUTION,'width':w,'height':h,'bbox':[left,bottom,right,top],'note':'Mosaico de alta resolução focado no CAR, com escala, norte e perímetro realçado. Referência visual; não substitui cena temporal datada.'}
    except Exception as e:return {'ok':False,'source':'Esri World Imagery','detail':f'{type(e).__name__}:{str(e)[:260]}'}


print('RX_HIGHRES_REFERENCE_V41=focused_property_bounded_fetch',flush=True)

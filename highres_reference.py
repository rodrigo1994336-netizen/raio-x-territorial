from __future__ import annotations

import asyncio
import io
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from PIL import Image,ImageDraw,ImageEnhance,ImageFont,ImageOps
from pyproj import Geod
from shapely.geometry import shape

TILE='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
EXPORT='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
ATTRIBUTION='Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
GEOD=Geod(ellps='WGS84')
TILE_SIZE=256


def _font(size:int,bold=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(p,size)
    except Exception:return None


def _rings(g):
    if g.geom_type=='Polygon':return [g.exterior]
    return [x.exterior for x in getattr(g,'geoms',[]) if x.geom_type=='Polygon']


def _nice_scale(meters:float)->float:
    choices=(20,50,100,200,500,1000,2000,5000,10000,20000)
    target=max(20,meters*.20)
    return max([x for x in choices if x<=target] or [20])


def _world_px(lon:float,lat:float,z:int):
    lat=max(-85.05112878,min(85.05112878,float(lat)));n=(2**z)*TILE_SIZE
    x=(float(lon)+180.0)/360.0*n
    r=math.radians(lat);y=(1.0-math.asinh(math.tan(r))/math.pi)/2.0*n
    return x,y


def _choose_zoom(left:float,bottom:float,right:float,top:float):
    for z in range(19,11,-1):
        x0,y1=_world_px(left,bottom,z);x1,y0=_world_px(right,top,z)
        w=max(1,x1-x0);h=max(1,y1-y0)
        tx0,tx1=int(x0//256),int(x1//256);ty0,ty1=int(y0//256),int(y1//256)
        count=(tx1-tx0+1)*(ty1-ty0+1)
        if max(w,h)<=2300 and count<=30:return z
    return 12


async def _tile(client:httpx.AsyncClient,z:int,x:int,y:int):
    url=TILE.format(z=z,x=x,y=y);last=''
    for _ in range(2):
        try:
            r=await client.get(url);r.raise_for_status()
            if len(r.content)<2500:raise RuntimeError(f'tile_too_small:{len(r.content)}')
            return x,y,Image.open(io.BytesIO(r.content)).convert('RGB'),None
        except Exception as e:last=f'{type(e).__name__}:{str(e)[:120]}'
    return x,y,None,last


async def _tile_mosaic(left:float,bottom:float,right:float,top:float):
    z=_choose_zoom(left,bottom,right,top);x0w,y1w=_world_px(left,bottom,z);x1w,y0w=_world_px(right,top,z)
    tx0,tx1=int(x0w//256),int(x1w//256);ty0,ty1=int(y0w//256),int(y1w//256)
    coords=[(x,y) for y in range(ty0,ty1+1) for x in range(tx0,tx1+1)]
    async with httpx.AsyncClient(timeout=httpx.Timeout(6,connect=3),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/highres-tiles-v42'}) as c:
        rows=await asyncio.gather(*[_tile(c,z,x,y) for x,y in coords])
    failed=[e for _,_,im,e in rows if im is None]
    if failed:return {'ok':False,'detail':f'tile_fail:{len(failed)}/{len(coords)}:{failed[0]}'}
    canvas=Image.new('RGB',((tx1-tx0+1)*256,(ty1-ty0+1)*256))
    for x,y,im,_ in rows:canvas.paste(im,((x-tx0)*256,(y-ty0)*256))
    crop=(int(round(x0w-tx0*256)),int(round(y0w-ty0*256)),int(round(x1w-tx0*256)),int(round(y1w-ty0*256)))
    crop=(max(0,crop[0]),max(0,crop[1]),min(canvas.width,crop[2]),min(canvas.height,crop[3]))
    if crop[2]-crop[0]<120 or crop[3]-crop[1]<120:return {'ok':False,'detail':'tile_crop_too_small'}
    return {'ok':True,'image':canvas.crop(crop),'z':z,'world_bbox':(x0w,y0w,x1w,y1w),'tiles':len(coords)}


async def _export_fallback(left,bottom,right,top):
    aspect=max((right-left)/(top-bottom),.42);width=1600;height=max(900,min(1450,int(width/aspect)))
    params={'bbox':f'{left},{bottom},{right},{top}','bboxSR':'4326','imageSR':'4326','size':f'{width},{height}','format':'jpg','transparent':'false','f':'image'}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(9,connect=4),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/highres-export-v42'}) as c:
            r=await c.get(EXPORT+'?'+urlencode(params));r.raise_for_status();raw=r.content
        if len(raw)<20_000:return {'ok':False,'detail':f'export_too_small:{len(raw)}'}
        return {'ok':True,'image':Image.open(io.BytesIO(raw)).convert('RGB'),'z':None,'tiles':0}
    except Exception as e:return {'ok':False,'detail':f'{type(e).__name__}:{str(e)[:180]}'}


def _enhance(img:Image.Image):
    img=ImageOps.autocontrast(img.convert('RGB'),cutoff=.35)
    img=ImageEnhance.Brightness(img).enhance(1.025)
    img=ImageEnhance.Contrast(img).enhance(1.075)
    img=ImageEnhance.Color(img).enhance(1.055)
    return ImageEnhance.Sharpness(img).enhance(1.10)


async def build_highres_reference_image(car_geometry:dict[str,Any],out_path:str|Path):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    try:g=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'Esri World Imagery','detail':f'geometry:{e}'}
    minx,miny,maxx,maxy=g.bounds;dx=max(maxx-minx,.00028);dy=max(maxy-miny,.00028);pad=max(dx,dy)*.145
    left,bottom,right,top=minx-pad,miny-pad,maxx+pad,maxy+pad
    src=await _tile_mosaic(left,bottom,right,top)
    method='tiles'
    if not src.get('ok'):
        first_detail=src.get('detail');src=await _export_fallback(left,bottom,right,top);method='export_fallback'
        if not src.get('ok'):return {'ok':False,'source':'Esri World Imagery','detail':f'tiles={first_detail}; export={src.get("detail")}' }
    img=_enhance(src['image']);scale=min(1.0,1800/max(img.size)) if max(img.size)>1800 else 1.0
    if scale<1:img=img.resize((max(1,int(img.width*scale)),max(1,int(img.height*scale))),Image.Resampling.LANCZOS)
    w,h=img.size;draw=ImageDraw.Draw(img,'RGBA')
    def px(lon,lat):
        if method=='tiles' and src.get('z') is not None:
            wx,wy=_world_px(lon,lat,int(src['z']));a,b,c,d=src['world_bbox'];return ((wx-a)/(c-a)*w,(wy-b)/(d-b)*h)
        return ((lon-left)/(right-left)*w,(top-lat)/(top-bottom)*h)
    for ring in _rings(g):
        pts=[px(float(x),float(y)) for x,y in ring.coords]
        if len(pts)>=3:
            draw.polygon(pts,fill=(47,224,143,42));draw.line(pts,fill=(2,22,14,235),width=max(8,int(w/190)),joint='curve');draw.line(pts,fill=(77,238,165,255),width=max(5,int(w/310)),joint='curve')
    label_w=min(660,w-44);draw.rounded_rectangle((24,22,label_w,104),radius=17,fill=(4,17,12,210),outline=(255,255,255,36),width=1)
    draw.text((42,38),'IMAGEM AÉREA DA PROPRIEDADE',fill='white',font=_font(max(20,int(w/68)),True));draw.text((42,72),'CAR destacado em verde',fill=(198,232,214),font=_font(max(14,int(w/105))))
    nx=w-78;draw.rounded_rectangle((w-124,22,w-28,132),radius=16,fill=(4,17,12,190));draw.polygon([(nx,37),(nx-16,82),(nx,72),(nx+16,82)],fill=(255,255,255,245));draw.text((nx-10,92),'N',fill='white',font=_font(max(18,int(w/80)),True))
    midlat=(bottom+top)/2;width_m=abs(GEOD.inv(left,midlat,right,midlat)[2]);scale_m=_nice_scale(width_m);bar=max(55,int(scale_m/max(width_m,1)*w));x0,y0=32,h-55
    draw.rounded_rectangle((20,h-79,min(w-20,20+bar+165),h-18),radius=12,fill=(0,0,0,155));draw.line((x0,y0,x0+bar,y0),fill='white',width=5);draw.line((x0,y0-7,x0,y0+7),fill='white',width=3);draw.line((x0+bar,y0-7,x0+bar,y0+7),fill='white',width=3)
    st=f'{int(scale_m/1000)} km' if scale_m>=1000 else f'{int(scale_m)} m';draw.text((x0+bar+13,y0-12),st,fill='white',font=_font(max(14,int(w/110)),True))
    fnt=_font(max(9,int(w/150)));tw=draw.textbbox((0,0),ATTRIBUTION,font=fnt)[2];bx=max(20,w-tw-40);draw.rounded_rectangle((bx,h-47,w-18,h-17),radius=8,fill=(0,0,0,145));draw.text((bx+10,h-41),ATTRIBUTION,fill=(232,236,233),font=fnt)
    img.save(out_path,'JPEG',quality=94,optimize=True)
    return {'ok':True,'path':str(out_path),'source':'Esri World Imagery','attribution':ATTRIBUTION,'width':w,'height':h,'bbox':[left,bottom,right,top],'fetch_method':method,'zoom':src.get('z'),'tile_count':src.get('tiles'),'note':'Imagem aérea focada no CAR, com enquadramento fechado, perímetro verde, escala e norte. Referência visual; a evidência temporal datada permanece no Sentinel-2.'}


print('RX_HIGHRES_REFERENCE_V42=premium_tile_mosaic_tight_crop',flush=True)

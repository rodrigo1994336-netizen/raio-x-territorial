from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import io

import httpx
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

EARTH_SEARCH='https://earth-search.aws.element84.com/v1/search'


def _iter_coords(g):
    if g is None or g.is_empty:return
    gt=g.geom_type
    if gt=='Polygon':
        for x,y in g.exterior.coords:yield x,y
    elif gt=='MultiPolygon':
        for p in g.geoms:
            for x,y in p.exterior.coords:yield x,y


def _font(size:int,bold=False):
    path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(path,size)
    except Exception:return None


async def _search(geometry:dict[str,Any],days:int=180):
    end=datetime.now(timezone.utc);start=end-timedelta(days=days)
    body={
      'collections':['sentinel-2-l2a'],'intersects':geometry,
      'datetime':f'{start.isoformat().replace("+00:00","Z")}/{end.isoformat().replace("+00:00","Z")}',
      'limit':20,'query':{'eo:cloud_cover':{'lt':55}},
      'sortby':[{'field':'properties.datetime','direction':'desc'}]
    }
    async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.24-satellite'}) as c:
        r=await c.post(EARTH_SEARCH,json=body);r.raise_for_status();data=r.json()
    fs=data.get('features') or []
    fs=sorted(fs,key=lambda f:(float((f.get('properties') or {}).get('eo:cloud_cover') or 999),str((f.get('properties') or {}).get('datetime') or '')),reverse=False)
    # Favor lower cloud first; ties naturally retain a recent item from the search result.
    return fs


def _thumbnail_url(item:dict[str,Any]):
    for link in item.get('links') or []:
        if link.get('rel')=='thumbnail' and link.get('href'):return link['href']
    for _,a in (item.get('assets') or {}).items():
        roles=a.get('roles') or []
        if 'thumbnail' in roles and a.get('href'):return a['href']
    return None


def _crop_and_overlay(raw:bytes,item:dict[str,Any],car_geom,out_path:Path):
    img=Image.open(io.BytesIO(raw)).convert('RGB');w,h=img.size
    ib=item.get('bbox') or []
    if len(ib)!=4:return None
    ix0,iy0,ix1,iy1=map(float,ib);cx0,cy0,cx1,cy1=car_geom.bounds
    padx=max((cx1-cx0)*.30,(ix1-ix0)*.01);pady=max((cy1-cy0)*.30,(iy1-iy0)*.01)
    bx0=max(ix0,cx0-padx);bx1=min(ix1,cx1+padx);by0=max(iy0,cy0-pady);by1=min(iy1,cy1+pady)
    if bx1<=bx0 or by1<=by0:bx0,by0,bx1,by1=ix0,iy0,ix1,iy1
    def px(lon):return (lon-ix0)/(ix1-ix0)*w
    def py(lat):return (iy1-lat)/(iy1-iy0)*h
    left=max(0,int(px(bx0)));right=min(w,int(px(bx1)));top=max(0,int(py(by1)));bottom=min(h,int(py(by0)))
    if right-left<100 or bottom-top<100:left,top,right,bottom=0,0,w,h;bx0,by0,bx1,by1=ix0,iy0,ix1,iy1
    crop=img.crop((left,top,right,bottom));cw,ch=crop.size
    target_w=1500;target_h=900
    crop.thumbnail((target_w,target_h),Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(target_w,target_h),'#111820')
    ox=(target_w-crop.width)//2;oy=(target_h-crop.height)//2;canvas.paste(crop,(ox,oy))
    draw=ImageDraw.Draw(canvas,'RGBA')
    def cxy(lon,lat):
        x=ox+(lon-bx0)/(bx1-bx0)*crop.width
        y=oy+(by1-lat)/(by1-by0)*crop.height
        return x,y
    for g in ([car_geom] if car_geom.geom_type=='Polygon' else list(car_geom.geoms)):
        pts=[cxy(x,y) for x,y in g.exterior.coords]
        if len(pts)>=3:
            draw.line(pts,fill=(255,238,70,255),width=6,joint='curve')
            draw.line(pts,fill=(20,32,25,255),width=2,joint='curve')
    props=item.get('properties') or {};dt=str(props.get('datetime') or props.get('start_datetime') or '')[:10];cloud=props.get('eo:cloud_cover')
    panel=(18,18,670,104);draw.rounded_rectangle(panel,radius=14,fill=(4,17,12,205),outline=(255,255,255,80),width=1)
    draw.text((35,31),'IMAGEM REAL DA PROPRIEDADE',fill='white',font=_font(23,True))
    draw.text((35,64),f'Sentinel-2 L2A • {dt or "data não informada"} • nuvens {round(float(cloud),1) if cloud is not None else "—"}%',fill=(210,230,218),font=_font(17))
    draw.rectangle((25,target_h-62,48,target_h-39),fill=(255,238,70,255));draw.text((58,target_h-66),'Limite do CAR sobre imagem orbital real',fill='white',font=_font(17))
    draw.text((target_w-520,target_h-62),'Copernicus Sentinel-2 • Earth Search/AWS Open Data',fill=(218,225,221),font=_font(14))
    out_path.parent.mkdir(parents=True,exist_ok=True);canvas.save(out_path,'JPEG',quality=90,optimize=True)
    return {'path':str(out_path),'scene_id':item.get('id'),'date':dt,'cloud_cover_pct':cloud,'source':'Copernicus Sentinel-2 L2A via Element 84 Earth Search / AWS Open Data','note':'Imagem orbital real da cena selecionada. O contorno do CAR é sobreposto pelo Raio-X Territorial.'}


async def build_satellite_property_image(car_geometry:dict[str,Any],out_path:str|Path):
    try:car_geom=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'Sentinel-2','detail':f'geometry:{e}'}
    try:items=await _search(car_geometry)
    except Exception as e:return {'ok':False,'source':'Sentinel-2 / Earth Search','detail':f'{type(e).__name__}:{str(e)[:260]}'}
    if not items:return {'ok':False,'source':'Sentinel-2 / Earth Search','detail':'no_scene_found'}
    for item in items[:8]:
        url=_thumbnail_url(item)
        if not url:continue
        try:
            async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.24-satellite'}) as c:
                r=await c.get(url);r.raise_for_status();raw=r.content
            meta=_crop_and_overlay(raw,item,car_geom,Path(out_path))
            if meta:return {'ok':True,**meta}
        except Exception:continue
    return {'ok':False,'source':'Sentinel-2 / Earth Search','detail':'thumbnail_unavailable_for_candidate_scenes'}

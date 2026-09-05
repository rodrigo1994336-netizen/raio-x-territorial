from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.windows import from_bounds
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

EARTH_SEARCH='https://earth-search.aws.element84.com/v1/search'


def _font(size:int,bold=False):
    path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:return ImageFont.truetype(path,size)
    except Exception:return None


async def _search(geometry:dict[str,Any],days:int=365):
    end=datetime.now(timezone.utc);start=end-timedelta(days=days)
    body={'collections':['sentinel-2-l2a'],'intersects':geometry,'datetime':f'{start.isoformat().replace("+00:00","Z")}/{end.isoformat().replace("+00:00","Z")}',
          'limit':18,'query':{'eo:cloud_cover':{'lt':45}},'sortby':[{'field':'properties.datetime','direction':'desc'}]}
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.41-sentinel-cog'}) as c:
        r=await c.post(EARTH_SEARCH,json=body);r.raise_for_status();data=r.json()
    fs=data.get('features') or []
    fs.sort(key=lambda f:(float((f.get('properties') or {}).get('eo:cloud_cover') or 999),str((f.get('properties') or {}).get('datetime') or '')))
    return fs


def _asset(item:dict,*names):
    assets=item.get('assets') or {}
    for n in names:
        a=assets.get(n)
        if isinstance(a,dict) and a.get('href'):return a.get('href')
    for k,a in assets.items():
        txt=' '.join([str(k),str((a or {}).get('title') or ''),str((a or {}).get('description') or '')]).lower()
        if any(n.lower() in txt for n in names) and (a or {}).get('href'):return a.get('href')
    return None


def _read(url:str,bounds,out_h:int,out_w:int):
    env={
        'GDAL_HTTP_MULTIRANGE':'YES','GDAL_HTTP_MERGE_CONSECUTIVE_RANGES':'YES',
        'CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif,.TIF','GDAL_HTTP_CONNECTTIMEOUT':'5',
        'GDAL_HTTP_TIMEOUT':'9','GDAL_HTTP_MAX_RETRY':'0','VSI_CACHE':'TRUE','VSI_CACHE_SIZE':'5000000'
    }
    with rasterio.Env(**env):
        with rasterio.open(url) as src:
            win=from_bounds(*bounds,transform=src.transform).round_offsets().round_lengths()
            arr=src.read(1,window=win,out_shape=(out_h,out_w),resampling=Resampling.bilinear,boundless=True,fill_value=0)
            tr=src.window_transform(win)*Affine.scale(win.width/out_w,win.height/out_h)
            return arr.astype('float32'),tr,src.crs


async def _read4(red,green,blue,nir,bounds,out_h,out_w):
    return await asyncio.wait_for(asyncio.gather(
        asyncio.to_thread(_read,red,bounds,out_h,out_w),
        asyncio.to_thread(_read,green,bounds,out_h,out_w),
        asyncio.to_thread(_read,blue,bounds,out_h,out_w),
        asyncio.to_thread(_read,nir,bounds,out_h,out_w),
    ),timeout=12)


def _scale_rgb(arr:np.ndarray,valid:np.ndarray):
    vals=arr[valid & np.isfinite(arr) & (arr>0)]
    if vals.size<10:return np.zeros_like(arr,dtype='uint8')
    lo=float(np.percentile(vals,2));hi=float(np.percentile(vals,98))
    if hi<=lo:hi=lo+1
    x=np.clip((arr-lo)/(hi-lo),0,1);x=np.power(x,0.85)
    return (x*255).astype('uint8')


def _draw_boundary(img:Image.Image,gproj,out_transform):
    draw=ImageDraw.Draw(img,'RGBA');inv=~out_transform
    geoms=[gproj] if gproj.geom_type=='Polygon' else list(getattr(gproj,'geoms',[]))
    for g in geoms:
        try:pts=[inv*(float(x),float(y)) for x,y in g.exterior.coords]
        except Exception:continue
        if len(pts)>=3:
            draw.line(pts,fill=(255,238,70,255),width=6,joint='curve');draw.line(pts,fill=(18,35,24,255),width=2,joint='curve')


def _ndvi_image(ndvi:np.ndarray,inside:np.ndarray,out_path:Path,gproj,out_transform):
    h,w=ndvi.shape;rgb=np.zeros((h,w,3),dtype='uint8');rgb[:]=[35,38,42]
    x=np.clip((ndvi+0.15)/0.95,0,1);rgb[...,0]=(180*(1-x)+45*x).astype('uint8');rgb[...,1]=(95*(1-x)+175*x).astype('uint8');rgb[...,2]=(55*(1-x)+75*x).astype('uint8');rgb[~inside]=[24,29,31]
    im=Image.fromarray(rgb,'RGB');_draw_boundary(im,gproj,out_transform);draw=ImageDraw.Draw(im,'RGBA');draw.rounded_rectangle((20,18,620,82),radius=12,fill=(4,17,12,210));draw.text((34,30),'NDVI REAL — SENTINEL-2',fill='white',font=_font(22,True));draw.text((34,57),'Calculado dentro do limite do CAR',fill=(210,235,218),font=_font(15));im.save(out_path,'PNG',optimize=True)


async def build_sentinel_cog_property_image(car_geometry:dict[str,Any],out_path:str|Path):
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    try:car=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'Sentinel-2 COG','detail':f'geometry:{e}'}
    try:items=await _search(car_geometry)
    except Exception as e:return {'ok':False,'source':'Sentinel-2 / Earth Search','detail':f'search:{type(e).__name__}:{str(e)[:220]}'}
    if not items:return {'ok':False,'source':'Sentinel-2 / Earth Search','detail':'no_scene_found'}
    last=None
    # Two high-quality candidates are enough. A slow/broken COG must not hold the whole PDF hostage.
    for item in items[:2]:
        red=_asset(item,'red','B04');green=_asset(item,'green','B03');blue=_asset(item,'blue','B02');nir=_asset(item,'nir','nir08','B08')
        if not all((red,green,blue,nir)):continue
        try:
            with rasterio.Env(GDAL_HTTP_CONNECTTIMEOUT='5',GDAL_HTTP_TIMEOUT='8',GDAL_HTTP_MAX_RETRY='0',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.TIF'):
                with rasterio.open(red) as ref:crs=ref.crs
            tf=Transformer.from_crs('EPSG:4326',crs,always_xy=True);gproj=shp_transform(tf.transform,car)
            minx,miny,maxx,maxy=gproj.bounds;dx=max(maxx-minx,30);dy=max(maxy-miny,30);pad=max(dx,dy)*.38
            bounds=(minx-pad,miny-pad,maxx+pad,maxy+pad);aspect=max((bounds[2]-bounds[0])/(bounds[3]-bounds[1]),.25)
            out_w=1100;out_h=max(650,min(1100,int(out_w/aspect)))
            (r,tr,_),(g,_,_),(b,_,_),(n,_,_)=await _read4(red,green,blue,nir,bounds,out_h,out_w)
            inside=geometry_mask([mapping(gproj)],out_shape=(out_h,out_w),transform=tr,invert=True);valid=(r>0)&(g>0)&(b>0)
            R=_scale_rgb(r,valid);G=_scale_rgb(g,valid);B=_scale_rgb(b,valid);rgb=np.dstack([R,G,B]);im=Image.fromarray(rgb,'RGB');_draw_boundary(im,gproj,tr)
            draw=ImageDraw.Draw(im,'RGBA');props=item.get('properties') or {};dt=str(props.get('datetime') or props.get('start_datetime') or '')[:10];cloud=props.get('eo:cloud_cover')
            draw.rounded_rectangle((20,18,760,96),radius=13,fill=(4,17,12,215));draw.text((36,30),'IMAGEM ORBITAL REAL DA PROPRIEDADE',fill='white',font=_font(23,True));draw.text((36,64),f'Sentinel-2 L2A 10 m • {dt or "data não informada"} • nuvens {round(float(cloud),1) if cloud is not None else "—"}%',fill=(210,235,218),font=_font(16));draw.rectangle((28,out_h-62,49,out_h-41),fill=(255,238,70,255));draw.text((58,out_h-66),'Limite do CAR',fill='white',font=_font(16));draw.text((out_w-500,out_h-62),'Copernicus Sentinel-2 • AWS Open Data / Element 84',fill=(220,225,222),font=_font(13));im.save(out_path,'JPEG',quality=94,optimize=True)
            den=(n+r);ndvi=np.where(den>0,(n-r)/den,np.nan);vals=ndvi[inside & np.isfinite(ndvi) & (r>0) & (n>0)];ndvi_meta={};ndvi_path=out_path.with_name('ndvi_property.png')
            if vals.size:
                ndvi_meta={'ndvi_mean':round(float(np.mean(vals)),3),'ndvi_median':round(float(np.median(vals)),3),'ndvi_p10':round(float(np.percentile(vals,10)),3),'ndvi_p90':round(float(np.percentile(vals,90)),3),'ndvi_low_share_pct':round(float(np.mean(vals<.3)*100),1),'ndvi_medium_share_pct':round(float(np.mean((vals>=.3)&(vals<.55))*100),1),'ndvi_high_share_pct':round(float(np.mean(vals>=.55)*100),1),'ndvi_pixel_count':int(vals.size)};_ndvi_image(ndvi,inside,ndvi_path,gproj,tr)
            return {'ok':True,'path':str(out_path),'ndvi_image_path':str(ndvi_path) if ndvi_path.exists() else None,'scene_id':item.get('id'),'date':dt,'cloud_cover_pct':cloud,'source':'Copernicus Sentinel-2 L2A COG via Element 84 Earth Search / AWS Open Data','resolution_m':10,'note':'RGB e NDVI calculados diretamente das bandas Sentinel-2 COG no recorte da propriedade; não é thumbnail genérico.',**ndvi_meta}
        except Exception as e:last=f'{type(e).__name__}:{str(e)[:260]}';continue
    return {'ok':False,'source':'Sentinel-2 COG','detail':last or 'candidate_assets_unavailable'}


print('RX_SENTINEL_COG=V41_parallel_bands_bounded_candidates',flush=True)

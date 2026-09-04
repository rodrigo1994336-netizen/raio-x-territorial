from __future__ import annotations

import gzip
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import mapping, shape

BASE='https://s3.amazonaws.com/elevation-tiles-prod/skadi'


def _tile_id(lat:int,lon:int)->str:
    return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"


def _tiles(bounds):
    minx,miny,maxx,maxy=bounds
    # Right/top exact integers belong to the previous tile for bbox coverage.
    e=1e-10
    lons=range(math.floor(minx),math.floor(maxx-e)+1)
    lats=range(math.floor(miny),math.floor(maxy-e)+1)
    return [(lat,lon) for lat in lats for lon in lons]


def _download(lat:int,lon:int,cache:Path)->Path:
    tid=_tile_id(lat,lon);folder=tid[:3]
    raw=cache/f'{tid}.hgt';gz=cache/f'{tid}.hgt.gz'
    if raw.exists() and raw.stat().st_size>20_000_000:return raw
    cache.mkdir(parents=True,exist_ok=True)
    url=f'{BASE}/{folder}/{tid}.hgt.gz'
    p=subprocess.run(['curl','-sS','--fail','--retry','2','--retry-delay','1','--connect-timeout','10','--max-time','70','-A','Raio-X-Territorial/terrain-srtm',url,'-o',str(gz)],capture_output=True,timeout=80)
    if p.returncode:raise RuntimeError(f'download {tid}: '+p.stderr.decode('utf-8','ignore')[:180])
    with gzip.open(gz,'rb') as src,open(raw,'wb') as dst:
        while True:
            chunk=src.read(1024*1024)
            if not chunk:break
            dst.write(chunk)
    try:gz.unlink()
    except Exception:pass
    return raw


def _slope_stats(elev:np.ndarray,mask:np.ndarray,lat:float,res_deg:float):
    # SRTM grid is geographic. Convert angular cell spacing to metres locally.
    dy=max(abs(res_deg)*111_132.0,1.0)
    dx=max(abs(res_deg)*111_320.0*math.cos(math.radians(lat)),1.0)
    work=elev.astype('float32')
    valid=mask & np.isfinite(work) & (work>-32000)
    if int(valid.sum())<9:return None
    # Fill outside/nodata with local median only for derivative continuity; statistics remain masked.
    med=float(np.median(work[valid]));work[~valid]=med
    gy,gx=np.gradient(work,dy,dx)
    slope=np.degrees(np.arctan(np.hypot(gx,gy)))
    vals=slope[valid]
    classes=[
        ('0–3°',0,3),('3–8°',3,8),('8–20°',8,20),('20–45°',20,45),('>45°',45,1e9)
    ]
    rows=[]
    for label,a,b in classes:
        pct=float(np.mean((vals>=a)&(vals<b))*100.0)
        rows.append({'class':label,'share_pct':round(pct,2)})
    return {
        'slope_mean_deg':round(float(np.mean(vals)),2),
        'slope_median_deg':round(float(np.median(vals)),2),
        'slope_p90_deg':round(float(np.percentile(vals,90)),2),
        'slope_max_deg':round(float(np.percentile(vals,99.5)),2),
        'slope_classes':rows,
        'slope_sample_pixels':int(vals.size),
    }


def query_terrain_srtm(car_geometry:dict[str,Any]):
    try:car=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'SRTM 1 arc-second / Terrain Tiles AWS Open Data','detail':f'geometry:{e}'}
    bounds=car.bounds;tiles=_tiles(bounds)
    if len(tiles)>4:return {'ok':False,'source':'SRTM 1 arc-second / Terrain Tiles AWS Open Data','detail':f'tile_guard:{len(tiles)}'}
    cache=Path(os.getenv('RX_TERRAIN_CACHE_DIR') or (Path(tempfile.gettempdir())/'raiox_srtm'))
    elevation_values=[];slope_parts=[];used=[];errors=[]
    for lat,lon in tiles:
        tid=_tile_id(lat,lon)
        try:
            fp=_download(lat,lon,cache)
            with rasterio.open(fp) as src:
                minx,miny,maxx,maxy=bounds
                # Clip requested bbox to this 1-degree tile.
                left=max(minx,lon);right=min(maxx,lon+1);bottom=max(miny,lat);top=min(maxy,lat+1)
                if right<=left or top<=bottom:continue
                win=from_bounds(left,bottom,right,top,src.transform).round_offsets().round_lengths()
                # pad by one pixel for gradients
                win=win.round_offsets().round_lengths()
                arr=src.read(1,window=win,boundless=True,fill_value=-32768).astype('float32')
                tr=src.window_transform(win)
                inside=geometry_mask([mapping(car)],out_shape=arr.shape,transform=tr,invert=True,all_touched=False)
                valid=inside & np.isfinite(arr) & (arr>-32000)
                vals=arr[valid]
                if vals.size:
                    elevation_values.append(vals)
                    st=_slope_stats(arr,inside,float(car.centroid.y),abs(float(src.res[0])))
                    if st:slope_parts.append(st)
                    used.append(tid)
        except Exception as e:errors.append(f'{tid}:{type(e).__name__}:{str(e)[:150]}')
    if not elevation_values:
        return {'ok':False,'source':'SRTM 1 arc-second / Terrain Tiles AWS Open Data','tiles':used,'errors':errors,'detail':'no_valid_dem_pixels'}
    vals=np.concatenate(elevation_values)
    # weighted summary of slope parts by sample count
    slope={}
    if slope_parts:
        total=sum(x['slope_sample_pixels'] for x in slope_parts)
        for k in ('slope_mean_deg','slope_median_deg','slope_p90_deg','slope_max_deg'):
            slope[k]=round(sum(x[k]*x['slope_sample_pixels'] for x in slope_parts)/max(total,1),2)
        classes={r['class']:0.0 for x in slope_parts for r in x['slope_classes']}
        for x in slope_parts:
            w=x['slope_sample_pixels']/max(total,1)
            for r in x['slope_classes']:classes[r['class']]+=r['share_pct']*w
        slope['slope_classes']=[{'class':k,'share_pct':round(v,2)} for k,v in classes.items()]
        slope['slope_sample_pixels']=total
    return {
        'ok':True,'source':'SRTM 1 arc-second (~30 m) via Mapzen/Tilezen Terrain Tiles — AWS Open Data',
        'tiles':used,'errors':errors,
        'elevation_min_m':round(float(np.min(vals)),1),'elevation_mean_m':round(float(np.mean(vals)),1),
        'elevation_median_m':round(float(np.median(vals)),1),'elevation_max_m':round(float(np.max(vals)),1),
        'elevation_sample_pixels':int(vals.size),**slope,
        'note':'Modelo digital de elevação SRTM (~30 m). Altitude e declividade são triagem topográfica raster; não substituem levantamento topográfico/geodésico de campo.'
    }


print('RX_TERRAIN_SRTM=elevation_slope_30m',flush=True)

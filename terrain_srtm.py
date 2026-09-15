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
from rasterio.windows import Window, from_bounds
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


# T1 (terra-verdade): as classes de relevo da Embrapa (SiBCS) são em PORCENTAGEM de inclinação,
# não em graus. 3° = 5,2 %; 8° = 14 %. Graus só aparecem no marco legal da Lei 12.651/2012
# (uso restrito de 25° a 45°, art. 11). Não existe "máximo": o antigo slope_max_deg era o percentil 99,5.
RELIEF_CLASSES_PCT=(
    ('plano','0–3%',0.0,3.0),('suave ondulado','3–8%',3.0,8.0),('ondulado','8–20%',8.0,20.0),
    ('forte ondulado','20–45%',20.0,45.0),('montanhoso','45–75%',45.0,75.0),('escarpado','acima de 75%',75.0,float('inf')),
)
LEGAL_RESTRICTED_DEG=25.0
LEGAL_RESTRICTED_PCT=math.tan(math.radians(LEGAL_RESTRICTED_DEG))*100.0  # ≈ 46,63 %
WINDOW_PAD_PX=3


def _slope_stats(elev:np.ndarray,mask:np.ndarray,lat:float,res_deg:float):
    # SRTM grid is geographic. Convert angular cell spacing to metres locally.
    dy=max(abs(res_deg)*111_132.0,1.0)
    dx=max(abs(res_deg)*111_320.0*math.cos(math.radians(lat)),1.0)
    work=elev.astype('float64')
    dem_ok=np.isfinite(work) & (work>-32000)
    valid=mask & dem_ok
    if int(valid.sum())<9:return None
    # Neighbours outside the CAR keep their real elevation (the window is padded); only DEM
    # nodata is filled, so the gradient on the border is terrain and not an artificial step.
    med=float(np.median(work[dem_ok]));work[~dem_ok]=med
    gy,gx=np.gradient(work,dy,dx)
    slope_pct=np.hypot(gx,gy)*100.0
    vals=slope_pct[valid]
    rows=[]
    for label,range_label,a,b in RELIEF_CLASSES_PCT:
        pct=float(np.mean((vals>=a)&(vals<b))*100.0)
        rows.append({'class':label,'range':range_label,'share_pct':round(pct,2)})
    return {
        'slope_unit':'%',
        'slope_mean_pct':round(float(np.mean(vals)),2),
        'slope_median_pct':round(float(np.median(vals)),2),
        'slope_p90_pct':round(float(np.percentile(vals,90)),2),
        'slope_classes':rows,
        'slope_ge_25deg_share_pct':round(float(np.mean(vals>=LEGAL_RESTRICTED_PCT)*100.0),2),
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
                # pad the window so border pixels of the CAR have real neighbours for the gradient
                win=Window(win.col_off-WINDOW_PAD_PX,win.row_off-WINDOW_PAD_PX,win.width+2*WINDOW_PAD_PX,win.height+2*WINDOW_PAD_PX)
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
        # Mean and class shares combine exactly by pixel weight. Median and P90 of a property split
        # across SRTM tiles are pixel-weighted approximations of the per-tile values.
        for k in ('slope_mean_pct','slope_median_pct','slope_p90_pct','slope_ge_25deg_share_pct'):
            slope[k]=round(sum(x[k]*x['slope_sample_pixels'] for x in slope_parts)/max(total,1),2)
        ranges={label:range_label for label,range_label,_a,_b in RELIEF_CLASSES_PCT}
        classes={label:0.0 for label,_r,_a,_b in RELIEF_CLASSES_PCT}
        for x in slope_parts:
            w=x['slope_sample_pixels']/max(total,1)
            for r in x['slope_classes']:classes[r['class']]+=r['share_pct']*w
        slope['slope_classes']=[{'class':k,'range':ranges[k],'share_pct':round(v,2)} for k,v in classes.items()]
        slope['slope_unit']='%'
        slope['slope_sample_pixels']=total
    return {
        'ok':True,'source':'SRTM 1 arc-second (~30 m) via Mapzen/Tilezen Terrain Tiles — AWS Open Data',
        'tiles':used,'errors':errors,
        'elevation_min_m':round(float(np.min(vals)),1),'elevation_mean_m':round(float(np.mean(vals)),1),
        'elevation_median_m':round(float(np.median(vals)),1),'elevation_max_m':round(float(np.max(vals)),1),
        'elevation_sample_pixels':int(vals.size),**slope,
        'note':'Modelo digital de elevação SRTM (~30 m). Inclinação em porcentagem, classes de relevo da Embrapa. Altitude e inclinação são triagem topográfica; não substituem levantamento topográfico de campo.'
    }


print('RX_TERRAIN_SRTM=elevation_slope_pct_embrapa_classes_30m',flush=True)

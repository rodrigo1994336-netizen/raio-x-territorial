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
from affine import Affine
from rasterio.features import geometry_mask
from rasterio.windows import Window
from shapely.geometry import mapping, shape

BASE='https://s3.amazonaws.com/elevation-tiles-prod/skadi'
SOURCE_SHORT='SRTM 1 arc-second / Terrain Tiles AWS Open Data'
SOURCE_LONG='SRTM 1 arc-second (~30 m) via Mapzen/Tilezen Terrain Tiles — AWS Open Data'


def _tile_id(lat:int,lon:int)->str:
    return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"


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
# não em graus. 3° = 5,2 %; 8° = 14 %. Graus só aparecem no marco legal da Lei 12.651/2012:
# de 25° a 45° é uso restrito (art. 11); acima de 45° é área de preservação permanente (art. 4º, V).
# Não existe "máximo": o antigo slope_max_deg era o percentil 99,5.
RELIEF_CLASSES_PCT=(
    ('plano','0–3%',0.0,3.0),('suave ondulado','3–8%',3.0,8.0),('ondulado','8–20%',8.0,20.0),
    ('forte ondulado','20–45%',20.0,45.0),('montanhoso','45–75%',45.0,75.0),('escarpado','acima de 75%',75.0,float('inf')),
)
LEGAL_RESTRICTED_DEG=25.0
LEGAL_RESTRICTED_PCT=math.tan(math.radians(LEGAL_RESTRICTED_DEG))*100.0  # ≈ 46,63 %
LEGAL_APP_DEG=45.0
LEGAL_APP_PCT=100.0  # tan 45° = 1: a lei diz "declividade superior a 45°, equivalente a 100%"
SAMPLES_PER_DEGREE=3600  # SRTM 1": 3601 x 3601 amostras por folha; a primeira e a última linha/coluna são a emenda
PAD_PX=2  # o 3x3 de Horn precisa de 1 vizinho real; 2 dá folga para arredondamento da borda
TILE_GUARD=4
WINDOW_GUARD_PX=9_000_000  # ~800 mil ha de caixa a 30 m; acima disso o método não roda (limite, não pendência)
SLOPE_MIN_COVERAGE=0.99  # pixels do CAR com vizinhança completa; abaixo disso a inclinação fica pendente
SLOPE_METHOD='Horn 3x3 (o do gdaldem e do ArcGIS), em mosaico das folhas SRTM, distâncias no elipsoide WGS84'
_WGS84_A=6_378_137.0
_WGS84_E2=6.69437999014e-3


def _cell_metres(lat_deg:np.ndarray|float,res_deg:float):
    """Largura (leste-oeste) e altura (norte-sul) da célula em metros no elipsoide WGS84."""
    phi=np.radians(np.asarray(lat_deg,dtype='float64'))
    s2=np.sin(phi)**2
    n=_WGS84_A/np.sqrt(1.0-_WGS84_E2*s2)
    m=_WGS84_A*(1.0-_WGS84_E2)/(1.0-_WGS84_E2*s2)**1.5
    r=math.radians(abs(res_deg))
    return np.maximum(n*np.cos(phi)*r,1e-6),np.maximum(m*r,1e-6)


def _slope_stats(elev:np.ndarray,mask:np.ndarray,top_lat:float,res_deg:float,nodata_below:float=-32000.0):
    """Inclinação por Horn 3x3 numa grade geográfica; cada pixel do imóvel entra uma vez.

    ``elev`` é o mosaico (com margem real de vizinhos), ``mask`` marca os pixels dentro do CAR e
    ``top_lat`` é a latitude do centro da primeira linha. Pixel sem os 8 vizinhos válidos não entra
    (nada de preencher com mediana, que inventa degrau).
    """
    z=np.asarray(elev,dtype='float32')
    ok=np.isfinite(z)&(z>nodata_below)
    rows=z.shape[0]
    lats=top_lat-np.arange(rows,dtype='float64')*abs(res_deg)
    ew,ns=_cell_metres(lats,res_deg)
    zf=np.where(ok,z,0.0).astype('float32')
    zp=np.pad(zf,1,mode='edge');okp=np.pad(ok,1,mode='constant',constant_values=False)
    a,b,c=zp[:-2,:-2],zp[:-2,1:-1],zp[:-2,2:]
    d,f=zp[1:-1,:-2],zp[1:-1,2:]
    g,h,i=zp[2:,:-2],zp[2:,1:-1],zp[2:,2:]
    full=(okp[:-2,:-2]&okp[:-2,1:-1]&okp[:-2,2:]&okp[1:-1,:-2]&okp[1:-1,1:-1]&okp[1:-1,2:]
          &okp[2:,:-2]&okp[2:,1:-1]&okp[2:,2:])
    dzdx=((c+2*f+i)-(a+2*d+g))/(8.0*ew[:,None].astype('float32'))
    dzdy=((g+2*h+i)-(a+2*b+c))/(8.0*ns[:,None].astype('float32'))
    slope_pct=np.hypot(dzdx,dzdy)*100.0
    del dzdx,dzdy,zp
    inside=mask&ok
    counted=inside&full
    vals=slope_pct[counted].astype('float64')
    if vals.size<9:return None
    rows_out=[]
    for label,range_label,lo,hi in RELIEF_CLASSES_PCT:
        pct=float(np.mean((vals>=lo)&(vals<hi))*100.0)
        rows_out.append({'class':label,'range':range_label,'share_pct':round(pct,2)})
    return {
        'slope_unit':'%',
        'slope_method':SLOPE_METHOD,
        'slope_mean_pct':round(float(np.mean(vals)),2),
        'slope_median_pct':round(float(np.median(vals)),2),
        'slope_p90_pct':round(float(np.percentile(vals,90)),2),
        'slope_classes':rows_out,
        'slope_ge_25deg_share_pct':round(float(np.mean(vals>=LEGAL_RESTRICTED_PCT)*100.0),2),
        'slope_25_45deg_share_pct':round(float(np.mean((vals>=LEGAL_RESTRICTED_PCT)&(vals<=LEGAL_APP_PCT))*100.0),2),
        'slope_gt_45deg_share_pct':round(float(np.mean(vals>LEGAL_APP_PCT)*100.0),2),
        'slope_sample_pixels':int(vals.size),
        'slope_coverage_pct':round(float(vals.size)/max(int(inside.sum()),1)*100.0,2),
    }


def _pixel_range(bounds,spd:int,pad:int):
    """Índices globais das amostras (n = lon*spd, m = lat*spd) que cobrem a caixa, com margem."""
    minx,miny,maxx,maxy=bounds
    n0=math.floor(minx*spd)-pad;n1=math.ceil(maxx*spd)+pad
    m0=math.floor(miny*spd)-pad;m1=math.ceil(maxy*spd)+pad
    return n0,n1,m0,m1


def _tiles_for_range(lo:int,hi:int,spd:int):
    """Folhas (graus inteiros) que cobrem as amostras lo..hi. A amostra na linha inteira k*spd existe nas
    duas folhas vizinhas (emenda); fica com uma só."""
    t0=math.floor(lo/spd)
    t1=max(t0,math.ceil(hi/spd)-1)
    return list(range(t0,t1+1))


def _fail(state:str,detail:str,**extra):
    return {'ok':False,'state':state,'source':SOURCE_SHORT,'detail':detail,**extra}


def query_terrain_srtm(car_geometry:dict[str,Any]):
    """Altitude e inclinação do CAR num mosaico das folhas SRTM.

    state: ``found``; ``not_found`` = limite do método (imóvel pequeno demais, grande demais, sem pixel
    válido) e a linha some; ``pending`` = download ou leitura falhou e a próxima emissão tenta de novo.
    """
    try:
        car=shape(car_geometry)
        if car.is_empty:raise ValueError('geometria_vazia')
    except Exception as e:return _fail('pending',f'geometry:{e}')
    spd=SAMPLES_PER_DEGREE
    n0,n1,m0,m1=_pixel_range(car.bounds,spd,0)
    required={(la,lo) for la in _tiles_for_range(m0,m1,spd) for lo in _tiles_for_range(n0,n1,spd)}
    if len(required)>TILE_GUARD:return _fail('not_found',f'tile_guard:{len(required)}')
    n0,n1,m0,m1=_pixel_range(car.bounds,spd,PAD_PX)
    width=n1-n0+1;height=m1-m0+1
    if width*height>WINDOW_GUARD_PX:return _fail('not_found',f'window_guard:{width*height}')
    needed=sorted({(la,lo) for la in _tiles_for_range(m0,m1,spd) for lo in _tiles_for_range(n0,n1,spd)})
    cache=Path(os.getenv('RX_TERRAIN_CACHE_DIR') or (Path(tempfile.gettempdir())/'raiox_srtm'))
    mosaic=np.full((height,width),-32768.0,dtype='float32')
    used=[];errors=[]
    for lat,lon in needed:
        tid=_tile_id(lat,lon)
        try:
            fp=_download(lat,lon,cache)
            with rasterio.open(fp) as src:
                if src.width!=spd+1 or src.height!=spd+1:
                    raise ValueError(f'resolucao_inesperada:{src.width}x{src.height}')
                # amostra global n -> coluna da folha c = n - lon*spd; m -> linha r = (lat+1)*spd - m
                cn0=max(n0,lon*spd);cn1=min(n1,(lon+1)*spd)
                rm1=min(m1,(lat+1)*spd);rm0=max(m0,lat*spd)
                if cn1<cn0 or rm1<rm0:continue
                win=Window(cn0-lon*spd,(lat+1)*spd-rm1,cn1-cn0+1,rm1-rm0+1)
                arr=src.read(1,window=win).astype('float32')
                nod=src.nodata
                if nod is not None:arr[arr==nod]=-32768.0
                dst=mosaic[m1-rm1:m1-rm0+1,cn0-n0:cn1-n0+1]
                empty=dst<=-32000
                dst[empty]=arr[empty]  # a emenda (mesma amostra nas duas folhas) entra uma vez
                used.append(tid)
        except Exception as e:
            errors.append(f'{tid}:{type(e).__name__}:{str(e)[:150]}')
            if (lat,lon) in required:
                return _fail('pending','download',tiles=used,errors=errors)
    # centro da amostra n fica em n/spd graus; a borda do pixel, meia amostra antes
    res=1.0/spd
    transform=Affine(res,0.0,(n0-0.5)*res,0.0,-res,(m1+0.5)*res)
    inside=geometry_mask([mapping(car)],out_shape=mosaic.shape,transform=transform,invert=True,all_touched=False)
    ok=mosaic>-32000
    vals=mosaic[inside&ok]
    if not vals.size:
        return _fail('not_found','no_valid_dem_pixels',tiles=used,errors=errors)
    st=_slope_stats(mosaic,inside,m1*res,res)
    if st is None:
        return _fail('not_found','area_abaixo_de_9_pixels',tiles=used,errors=errors)
    if st['slope_coverage_pct']<SLOPE_MIN_COVERAGE*100.0:
        return _fail('pending','vizinhanca_incompleta',tiles=used,errors=errors,slope_coverage_pct=st['slope_coverage_pct'])
    return {
        'ok':True,'state':'found','source':SOURCE_LONG,
        'tiles':used,'errors':errors,
        'elevation_min_m':round(float(np.min(vals)),1),'elevation_mean_m':round(float(np.mean(vals)),1),
        'elevation_median_m':round(float(np.median(vals)),1),'elevation_max_m':round(float(np.max(vals)),1),
        'elevation_sample_pixels':int(vals.size),**st,
        'note':'Modelo digital de elevação SRTM (~30 m). Inclinação em porcentagem pelo método de Horn, classes de relevo da Embrapa. Altitude e inclinação são triagem topográfica; não substituem levantamento topográfico de campo.'
    }


print('RX_TERRAIN_SRTM=elevation_slope_pct_embrapa_classes_30m_horn_mosaic',flush=True)

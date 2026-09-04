from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import math
import subprocess
from typing import Any
from urllib.parse import urlencode

from PIL import Image
from shapely.geometry import shape

BASE='https://maps.isric.org/mapserv'
PROPERTIES={
    'clay':('Argila',10.0,'%'),
    'sand':('Areia',10.0,'%'),
    'silt':('Silte',10.0,'%'),
    'phh2o':('pH em H₂O',10.0,''),
    'soc':('Carbono orgânico',10.0,'g/kg'),
    'cec':('CTC a pH 7',10.0,'cmol(c)/kg'),
    'nitrogen':('Nitrogênio total',100.0,'g/kg'),
}


def _centroid(geometry:dict[str,Any]):
    c=shape(geometry).centroid
    return float(c.x),float(c.y)


def _median(values):
    vals=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not vals:return None
    n=len(vals);m=n//2
    return vals[m] if n%2 else (vals[m-1]+vals[m])/2


def _one(prop:str,lon:float,lat:float):
    # Small WCS window around the property centroid. SoilGrids native output is 250 m;
    # the small window is intentionally sampled rather than pretending to be a soil test.
    delta=.004
    params={
        'map':f'/map/{prop}.map','SERVICE':'WCS','VERSION':'1.0.0','REQUEST':'GetCoverage',
        'COVERAGE':f'{prop}_0-5cm_mean','CRS':'EPSG:4326',
        'BBOX':f'{lon-delta},{lat-delta},{lon+delta},{lat+delta}',
        'RESX':'0.0015','RESY':'0.0015','FORMAT':'GEOTIFF_INT16'
    }
    url=BASE+'?'+urlencode(params)
    try:
        p=subprocess.run(['curl','-sS','--retry','1','--retry-delay','1','--connect-timeout','8','--max-time','35','-A','Raio-X-Territorial/0.25-soilgrids',url],capture_output=True,timeout=42)
    except Exception as e:
        return prop,{'ok':False,'detail':f'{type(e).__name__}:{str(e)[:180]}'}
    if p.returncode:
        return prop,{'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:220]}
    raw=p.stdout
    if raw.lstrip().startswith(b'<'):
        return prop,{'ok':False,'detail':'WCS_service_exception','preview':raw[:220].decode('utf-8','ignore')}
    try:
        img=Image.open(BytesIO(raw));data=[]
        for v in list(img.getdata()):
            if isinstance(v,tuple):v=v[0]
            try:v=float(v)
            except Exception:continue
            if -32000 < v < 32000:data.append(v)
        med=_median(data)
        if med is None:return prop,{'ok':False,'detail':'no_valid_pixels'}
        label,factor,unit=PROPERTIES[prop]
        val=round(med/factor,2)
        return prop,{'ok':True,'label':label,'value':val,'unit':unit,'raw_median':med,'pixel_count':len(data),'coverage':f'{prop}_0-5cm_mean'}
    except Exception as e:
        return prop,{'ok':False,'detail':f'GeoTIFF:{type(e).__name__}:{str(e)[:180]}','bytes':len(raw)}


def query_soilgrids_wcs(car_geometry:dict[str,Any]):
    try:lon,lat=_centroid(car_geometry)
    except Exception as e:return {'ok':False,'source':'ISRIC SoilGrids','detail':f'centroid:{e}'}
    values={}
    with ThreadPoolExecutor(max_workers=4) as ex:
        jobs=[ex.submit(_one,p,lon,lat) for p in PROPERTIES]
        for fut in as_completed(jobs):
            prop,row=fut.result();values[prop]=row
    ok_count=sum(1 for x in values.values() if x.get('ok'))
    rows=[]
    for prop,(label,_,unit) in PROPERTIES.items():
        x=values.get(prop) or {}
        rows.append({'code':prop,'label':label,'value':x.get('value'),'unit':unit,'ok':x.get('ok',False),'detail':x.get('detail'),'pixel_count':x.get('pixel_count')})
    return {
        'ok':ok_count>=4,'partial':0<ok_count<len(PROPERTIES),'source':'ISRIC SoilGrids 2.0 — WCS / mapas 250 m',
        'latitude':round(lat,6),'longitude':round(lon,6),'depth':'0–5 cm','successful_properties':ok_count,'requested_properties':len(PROPERTIES),
        'rows':rows,'raw':values,
        'note':'Predições SoilGrids em grade de 250 m amostradas no entorno do centróide do imóvel. Não substituem análise laboratorial de solo nem amostragem agronômica da propriedade.'
    }


print('RX_SOILGRIDS_WCS=physicochemical_0_5cm',flush=True)

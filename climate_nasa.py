from __future__ import annotations

from datetime import date, timedelta
import json
import subprocess
from typing import Any
from urllib.parse import urlencode

BASE='https://power.larc.nasa.gov/api/temporal/daily/point'
PARAMS=('PRECTOTCORR','T2M','T2M_MAX','T2M_MIN','RH2M','ALLSKY_SFC_SW_DWN')


def _curl_json(url:str,max_time=60):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.16-climate',url],capture_output=True,timeout=max_time+10)
    if p.returncode:
        return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300]}
    raw=p.stdout
    try:
        return {'ok':True,'json':json.loads(raw.decode('utf-8')),'bytes':len(raw)}
    except Exception as e:
        return {'ok':False,'detail':f'JSONDecodeError:{e}','preview':raw[:250].decode('utf-8','ignore')}


def _valid(v):
    try:
        f=float(v)
        return None if f <= -900 else f
    except Exception:
        return None


def _avg(values):
    vals=[v for v in values if v is not None]
    return round(sum(vals)/len(vals),3) if vals else None


def query_climate_nasa(car_geometry:dict[str,Any], days:int=30):
    try:
        coords=car_geometry.get('coordinates')
        # Calculate a robust centroid using shapely when available in service.
        from shapely.geometry import shape
        c=shape(car_geometry).centroid; lon=float(c.x); lat=float(c.y)
    except Exception as e:
        return {'ok':False,'source':'NASA POWER','detail':f'centroid:{e}'}
    today=date.today(); start=today-timedelta(days=max(45,days+15))
    params={
        'parameters':','.join(PARAMS),'community':'AG','longitude':lon,'latitude':lat,
        'start':start.strftime('%Y%m%d'),'end':today.strftime('%Y%m%d'),'format':'JSON','time-standard':'UTC'
    }
    res=_curl_json(BASE+'?'+urlencode(params),65)
    if not res.get('ok'):
        return {'ok':False,'source':'NASA POWER - Daily API','detail':res.get('detail'),'preview':res.get('preview')}
    data=res.get('json') or {}
    if 'messages' in data and not (data.get('properties') or {}).get('parameter'):
        return {'ok':False,'source':'NASA POWER - Daily API','detail':str(data.get('messages'))[:500]}
    ps=((data.get('properties') or {}).get('parameter') or {})
    rain=ps.get('PRECTOTCORR') or {}
    valid_dates=sorted([d for d,v in rain.items() if _valid(v) is not None])
    chosen=valid_dates[-days:]
    if not chosen:
        return {'ok':False,'source':'NASA POWER - Daily API','detail':'no_valid_daily_precipitation'}
    def vals(param):
        series=ps.get(param) or {}
        return [_valid(series.get(d)) for d in chosen]
    rain_vals=vals('PRECTOTCORR')
    out={
        'ok':True,'source':'NASA POWER - Daily API (Agroclimatology)','latitude':round(lat,6),'longitude':round(lon,6),
        'requested_days':days,'available_days':len(chosen),'period_start':chosen[0],'period_end':chosen[-1],
        'rain_sum_mm':round(sum(v for v in rain_vals if v is not None),3),
        'rain_daily_avg_mm':_avg(rain_vals),'temp_avg_c':_avg(vals('T2M')),'temp_max_avg_c':_avg(vals('T2M_MAX')),
        'temp_min_avg_c':_avg(vals('T2M_MIN')),'rh_avg_pct':_avg(vals('RH2M')),'solar_avg_kwh_m2_day':_avg(vals('ALLSKY_SFC_SW_DWN')),
        'latest_data_date':chosen[-1],
        'parameter_units':{k:v for k,v in ((data.get('parameters') or {}).items()) if k in PARAMS},
        'note':'Estimativa por grade NASA POWER no centróide do imóvel; não substitui estação meteorológica local.'
    }
    return out

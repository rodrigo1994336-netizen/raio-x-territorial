from __future__ import annotations

from datetime import date, timedelta
import json
import subprocess
from typing import Any
from urllib.parse import urlencode

from climate_normal_f2 import build_rain_vs_normal, parse_climatology_payload

BASE='https://power.larc.nasa.gov/api/temporal/daily/point'
CLIM_BASE='https://power.larc.nasa.gov/api/temporal/climatology/point'
PARAMS=('PRECTOTCORR','T2M','T2M_MAX','T2M_MIN','RH2M','ALLSKY_SFC_SW_DWN')


def _curl_json(url:str,max_time=60):
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time',str(max_time),'-A','Raio-X-Territorial/0.23-climate',url],capture_output=True,timeout=max_time+10)
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


def _centroid(car_geometry:dict[str,Any]):
    from shapely.geometry import shape
    c=shape(car_geometry).centroid
    return float(c.x),float(c.y)


def query_climate_nasa(car_geometry:dict[str,Any], days:int=30):
    try:
        lon,lat=_centroid(car_geometry)
    except Exception as e:
        return {'ok':False,'source':'NASA POWER','detail':f'centroid:{e}'}
    days=max(7,min(int(days),365))
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
    daily=[]
    for d in chosen:
        daily.append({
            'date':d,
            'rain_mm':_valid((ps.get('PRECTOTCORR') or {}).get(d)),
            't_avg_c':_valid((ps.get('T2M') or {}).get(d)),
            't_max_c':_valid((ps.get('T2M_MAX') or {}).get(d)),
            't_min_c':_valid((ps.get('T2M_MIN') or {}).get(d)),
            'rh_pct':_valid((ps.get('RH2M') or {}).get(d)),
        })
    dry_days=sum(1 for v in rain_vals if v is not None and v < 1.0)
    heavy_days=sum(1 for v in rain_vals if v is not None and v >= 20.0)
    return {
        'ok':True,'source':'NASA POWER - Daily API (Agroclimatology)','latitude':round(lat,6),'longitude':round(lon,6),
        'requested_days':days,'available_days':len(chosen),'period_start':chosen[0],'period_end':chosen[-1],
        'rain_sum_mm':round(sum(v for v in rain_vals if v is not None),3),
        'rain_daily_avg_mm':_avg(rain_vals),'temp_avg_c':_avg(vals('T2M')),'temp_max_avg_c':_avg(vals('T2M_MAX')),
        'temp_min_avg_c':_avg(vals('T2M_MIN')),'rh_avg_pct':_avg(vals('RH2M')),'solar_avg_kwh_m2_day':_avg(vals('ALLSKY_SFC_SW_DWN')),
        'dry_days_lt_1mm':dry_days,'heavy_rain_days_ge_20mm':heavy_days,
        'latest_data_date':chosen[-1],'daily':daily,
        'parameter_units':{k:v for k,v in ((data.get('parameters') or {}).items()) if k in PARAMS},
        'note':'Estimativa por grade NASA POWER no centróide do imóvel; não substitui estação meteorológica local.'
    }


def query_climatology_nasa(car_geometry:dict[str,Any]):
    try: lon,lat=_centroid(car_geometry)
    except Exception as e: return {'ok':False,'source':'NASA POWER Climatology','detail':f'centroid:{e}'}
    params={'parameters':'PRECTOTCORR,T2M,T2M_MAX,T2M_MIN','community':'AG','longitude':lon,'latitude':lat,'format':'JSON'}
    res=_curl_json(CLIM_BASE+'?'+urlencode(params),60)
    if not res.get('ok'): return {'ok':False,'source':'NASA POWER Climatology','detail':res.get('detail')}
    parsed=parse_climatology_payload(res.get('json') or {})
    valid=parsed['months']
    # rain_mm_day/rain_mm_month: PRECTOTCORR comes in mm/day. t_max_record_c/t_min_record_c: T2M_MAX/T2M_MIN of the
    # climatology endpoint are the 2001-2020 record, not the mean of daily maxima (F2, checked against the daily series).
    return {'ok':bool(valid),'source':'NASA POWER - Climatology API','latitude':round(lat,6),'longitude':round(lon,6),'months':valid,'period':parsed['period'],'note':'Climatologia da grade NASA POWER no centróide; é referência regional, não medição da fazenda.'}


def build_drought_screening(recent:dict[str,Any],climatology:dict[str,Any],history:dict[str,Any]|None=None):
    """Recent rain compared with the normal of the same calendar days (F2).

    The old fixed rule (< 50 mm and >= 65 % dry days) measured the dry season, not an anomaly: it flagged
    34 of 35 years in Curvelo. The declared rule lives in climate_normal_f2.build_rain_vs_normal; ``state`` keeps
    being the pt-BR label for existing callers, ``status`` is found / not_found / pending.
    """
    return build_rain_vs_normal(recent,climatology,history)

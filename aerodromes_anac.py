from __future__ import annotations

import csv
import io
import math
import subprocess
import time
from typing import Any
from shapely.geometry import shape

ANAC_CSV='https://siros.anac.gov.br/siros/registros/aerodromo/aerodromos.csv'
_CACHE={'ts':0.0,'rows':None}
CACHE_TTL=6*3600


def _norm(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s or '')) if not unicodedata.combining(c)).lower().strip()


def _pick(row:dict,*needles):
    keys=[(_norm(k),k) for k in row]
    for n in needles:
        nn=_norm(n)
        for nk,k in keys:
            if nk==nn:return row.get(k)
    for n in needles:
        nn=_norm(n)
        for nk,k in keys:
            if nn in nk:return row.get(k)
    return None


def _num(v):
    if v is None:return None
    s=str(v).strip().replace(',','.')
    try:return float(s)
    except Exception:return None


def _dms(v):
    if v is None:return None
    s=str(v).strip().upper().replace('º',' ').replace('°',' ').replace("'",' ').replace('"',' ')
    direct=_num(s)
    if direct is not None:return direct
    import re
    vals=[float(x.replace(',','.')) for x in re.findall(r'\d+(?:[\.,]\d+)?',s)]
    if not vals:return None
    deg=vals[0];minute=vals[1] if len(vals)>1 else 0;sec=vals[2] if len(vals)>2 else 0
    out=deg+minute/60+sec/3600
    if any(x in s for x in ('S','W','O')):out=-out
    return out


def _haversine(lat1,lon1,lat2,lon2):
    r=6371.0088;p1=math.radians(lat1);p2=math.radians(lat2);dp=math.radians(lat2-lat1);dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))


def _fetch_rows():
    now=time.monotonic()
    if _CACHE['rows'] is not None and now-_CACHE['ts']<CACHE_TTL:return _CACHE['rows']
    p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','8','--max-time','35','-A','Raio-X-Territorial/0.20-aerodromes',ANAC_CSV],capture_output=True,timeout=42)
    if p.returncode:raise RuntimeError(p.stderr.decode('utf-8','ignore')[:220])
    raw=p.stdout
    text=None
    for enc in ('utf-8-sig','latin-1'):
        try:text=raw.decode(enc);break
        except Exception:pass
    if text is None:raise RuntimeError('ANAC CSV encoding unsupported')
    sample=text[:4096]
    try:delimiter=csv.Sniffer().sniff(sample,delimiters=';,\t|').delimiter
    except Exception:delimiter=';'
    rows=list(csv.DictReader(io.StringIO(text),delimiter=delimiter))
    _CACHE.update({'ts':now,'rows':rows})
    return rows


def query_aerodromes_anac(car_geometry:dict,radius_km:float=50.0,limit:int=20):
    try:
        car=shape(car_geometry);c=car.centroid;lat0=float(c.y);lon0=float(c.x);rows=_fetch_rows();hits=[]
        for row in rows:
            lat=_dms(_pick(row,'latitude decimal','latitude','latgeopoint','lat'))
            lon=_dms(_pick(row,'longitude decimal','longitude','longeopoint','long','lon'))
            if lat is None or lon is None or not (-90<=lat<=90 and -180<=lon<=180):continue
            d=_haversine(lat0,lon0,lat,lon)
            if d>radius_km:continue
            hits.append({
                'name':str(_pick(row,'nome','denominacao','aerodromo') or '-').strip(),
                'icao':str(_pick(row,'icao','codigo oaci','oaci') or '-').strip(),
                'ciad':str(_pick(row,'ciad','codigo ciad') or '-').strip(),
                'municipality':str(_pick(row,'municipio','municipality') or '-').strip(),
                'uf':str(_pick(row,'uf','estado') or '-').strip(),
                'type':str(_pick(row,'tipo','uso','classificacao','categoria') or '-').strip(),
                'distance_km':round(d,1),'lat':lat,'lon':lon,
            })
        hits.sort(key=lambda x:x['distance_km'])
        return {'ok':True,'count_within_radius':len(hits),'radius_km':radius_km,'nearest':hits[:limit],'source':'ANAC / SIROS — cadastro oficial de aeródromos civis','dataset_url':ANAC_CSV,'rows_scanned':len(rows)}
    except Exception as e:
        return {'ok':False,'count_within_radius':None,'radius_km':radius_km,'nearest':[],'source':'ANAC / SIROS — cadastro oficial de aeródromos civis','detail':f'{type(e).__name__}:{e}'}

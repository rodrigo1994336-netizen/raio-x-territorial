from __future__ import annotations

import asyncio
import csv
import io
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx
from pyproj import CRS, Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

INDEX='https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/10min/'
FILE_RE=re.compile(r'focos_10min_(\d{8})_(\d{4})\.csv')


def _norm(s: Any) -> str:
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+','',s)


def _pick(row: dict[str,Any], *keys):
    lookup={_norm(k):v for k,v in row.items()}
    for key in keys:
        nk=_norm(key)
        if nk in lookup: return lookup[nk]
    for key in keys:
        nk=_norm(key)
        for rk,v in lookup.items():
            if nk and nk in rk: return v
    return None


def _float(v):
    if v is None or v=='': return None
    try: return float(str(v).strip().replace(',','.'))
    except Exception: return None


def _parse_csv(text: str):
    if not text.strip(): return []
    first=text.splitlines()[0] if text.splitlines() else ''
    delim=';' if first.count(';')>first.count(',') else ','
    reader=csv.DictReader(io.StringIO(text),delimiter=delim)
    out=[]
    for row in reader:
        lat=_float(_pick(row,'Latitude','lat')); lon=_float(_pick(row,'Longitude','lon','lng'))
        if lat is None or lon is None: continue
        out.append({
            'latitude':lat,'longitude':lon,
            'datetime':_pick(row,'DataHora','data_hora','datahora','data'),
            'satellite':_pick(row,'Satelite','satellite'),
            'country':_pick(row,'Pais','country'),
            'state':_pick(row,'Estado','uf','state'),
            'municipality':_pick(row,'Municipio','municipality'),
            'biome':_pick(row,'Bioma','biome'),
            'fire_risk':_pick(row,'RiscoFogo','risco_fogo','firerisk'),
            'frp':_float(_pick(row,'FRP','frp')),
        })
    return out


async def fetch_recent_foci(file_count: int=6):
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.15-fire'}) as c:
            idx=await c.get(INDEX)
            names=sorted({m.group(0) for m in FILE_RE.finditer(idx.text)})
            selected=names[-max(1,file_count):]
            async def one(name):
                try:
                    r=await c.get(INDEX+name)
                    return name,r.status_code,_parse_csv(r.text)
                except Exception:
                    return name,0,[]
            batches=await asyncio.gather(*[one(n) for n in selected])
        foci=[]; seen=set()
        files=[]
        for name,status,rows in batches:
            files.append({'name':name,'status':status,'rows':len(rows)})
            for x in rows:
                key=(x.get('datetime'),x.get('satellite'),round(x['latitude'],6),round(x['longitude'],6))
                if key in seen: continue
                seen.add(key); foci.append(x)
        return {'ok':idx.status_code==200,'index_status':idx.status_code,'files':files,'latest_file':selected[-1] if selected else None,'focus_count':len(foci),'foci':foci,'source':'INPE Programa Queimadas - focos CSV 10 min'}
    except Exception as e:
        return {'ok':False,'error':type(e).__name__,'detail':str(e)[:300],'source':'INPE Programa Queimadas - focos CSV 10 min'}


def _local_metric_transformer(car_geom):
    c=car_geom.centroid
    crs=CRS.from_proj4(f'+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs')
    return Transformer.from_crs('EPSG:4674',crs,always_xy=True)


async def analyze_fire_near_property(car_geometry: dict[str,Any], radius_km: float=5.0, file_count: int=6):
    feed=await fetch_recent_foci(file_count)
    if not feed.get('ok'):
        return {**feed,'radius_km':radius_km}
    try:
        car=shape(car_geometry)
        tr=_local_metric_transformer(car)
        car_m=transform(tr.transform,car)
        within=[]; inside=[]
        for focus in feed.get('foci') or []:
            p=Point(focus['longitude'],focus['latitude'])
            pm=transform(tr.transform,p)
            d=float(car_m.distance(pm))
            item={**focus,'distance_m':round(d,1),'inside':bool(car_m.covers(pm))}
            if item['inside']: inside.append(item)
            if d <= radius_km*1000: within.append(item)
        within.sort(key=lambda x:x['distance_m'])
        latest_file=feed.get('latest_file')
        return {
            'ok':True,'source':feed.get('source'),'latest_file':latest_file,'files':feed.get('files'),
            'feed_focus_count':feed.get('focus_count',0),'radius_km':radius_km,
            'inside_count':len(inside),'near_count':len(within),'nearest':within[0] if within else None,
            'inside':inside[:50],'near':within[:100],
            'window_note':f'Últimos {file_count} arquivos de 10 minutos disponíveis no diretório oficial do INPE.'
        }
    except Exception as e:
        return {'ok':False,'error':type(e).__name__,'detail':str(e)[:300],'source':feed.get('source'),'latest_file':feed.get('latest_file')}

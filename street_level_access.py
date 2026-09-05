from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import httpx
from pyproj import Geod
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

API='https://api.openstreetcam.org/2.0/photo/'
GEOD=Geod(ellps='GRS80')
ATTRIBUTION='© Grab and KartaView Contributors — CC BY-SA 4.0'


def _f(v):
    try:return float(v)
    except Exception:return None


def _distance_m(lon1,lat1,lon2,lat2):
    try:return abs(GEOD.inv(float(lon1),float(lat1),float(lon2),float(lat2))[2])
    except Exception:return None


def _bearing(lon1,lat1,lon2,lat2):
    try:return (GEOD.inv(float(lon1),float(lat1),float(lon2),float(lat2))[0]+360)%360
    except Exception:return None


def _angle_diff(a,b):
    if a is None or b is None:return None
    return abs((float(a)-float(b)+180)%360-180)


def _search_points(geom,max_points=5):
    # Query the centroid for small parcels and a few evenly distributed boundary
    # points for larger parcels. This bounds anonymous KartaView API use.
    c=geom.centroid;points=[(float(c.x),float(c.y))]
    boundary=geom.boundary
    try:length=float(boundary.length)
    except Exception:length=0
    if length>0:
        for frac in (.0,.25,.5,.75):
            p=boundary.interpolate(frac*length)
            xy=(float(p.x),float(p.y))
            if all(_distance_m(xy[0],xy[1],x[0],x[1])>80 for x in points):points.append(xy)
            if len(points)>=max_points:break
    return points[:max_points]


async def _near(lat:float,lon:float,radius:int=500):
    params={'lat':lat,'lng':lon,'radius':max(50,min(int(radius),500)),'zoomLevel':18,'join':'sequence','orderBy':'id','orderDirection':'desc'}
    try:
        async with httpx.AsyncClient(timeout=30,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.30-street-access'}) as c:
            r=await c.get(API,params=params);r.raise_for_status();d=r.json()
        rows=((d.get('result') or {}).get('data') or [])
        return rows if isinstance(rows,list) else []
    except Exception:return []


async def _details(photo_id):
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.30-street-access'}) as c:
            r=await c.get(API.rstrip('/')+'/'+str(photo_id));r.raise_for_status();d=r.json()
        row=((d.get('result') or {}).get('data') or {})
        return row if isinstance(row,dict) else {}
    except Exception:return {}


def _photo_url(row:dict):
    for key in ('fileurlProc','fileUrlProc','fileurlLTh','fileUrlLTh','fileurlTh','fileUrlTh','fileurl','fileUrl'):
        u=row.get(key)
        if u:
            u=str(u)
            if '[[sizeprefix]]' in u:u=u.replace('[[sizeprefix]]','proc')
            return u
    return None


async def find_street_level_access(car_geometry:dict[str,Any]):
    try:geom=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'KartaView','detail':f'geometry:{e}'}
    centroid=geom.centroid;queries=_search_points(geom);seen={};
    vals=await asyncio.gather(*[_near(lat,lon,500) for lon,lat in queries],return_exceptions=True)
    for rows in vals:
        if not isinstance(rows,list):continue
        for row in rows[:80]:
            pid=row.get('id') or row.get('photoId')
            if pid is not None:seen[str(pid)]=row
    candidates=[]
    for pid,row in seen.items():
        lat=_f(row.get('lat') or row.get('matchLat'));lon=_f(row.get('lng') or row.get('matchLng'))
        if lat is None or lon is None:continue
        p=Point(lon,lat)
        try:q=nearest_points(p,geom.boundary)[1];dist=_distance_m(lon,lat,q.x,q.y)
        except Exception:dist=None
        heading=_f(row.get('heading'));to_center=_bearing(lon,lat,centroid.x,centroid.y);angle=_angle_diff(heading,to_center)
        # Distance to CAR boundary dominates. Heading toward the parcel is a mild bonus.
        score=(dist if dist is not None else 999999)+(0 if angle is not None and angle<=70 else 45)
        candidates.append({'id':pid,'lat':lat,'lon':lon,'distance_to_car_m':round(dist,1) if dist is not None else None,'heading':heading,'heading_to_property_diff_deg':round(angle,1) if angle is not None else None,'score':score,'row':row})
    candidates.sort(key=lambda x:x['score'])
    if not candidates:
        c=geom.centroid
        return {'ok':False,'source':'KartaView','detail':'no_public_street_imagery_near_property','google_maps_streetview_url':f'https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={c.y},{c.x}','note':'Nenhuma imagem aberta KartaView foi localizada no entorno consultado. O link abre tentativa de Street View do Google ao vivo.'}
    best=candidates[0];details=await _details(best['id']);merged={**best['row'],**details};url=_photo_url(merged)
    if not url:return {'ok':False,'source':'KartaView','detail':'photo_found_without_usable_url','photo_id':best['id']}
    dist=best.get('distance_to_car_m');angle=best.get('heading_to_property_diff_deg')
    probable=bool(dist is not None and dist<=80 and (angle is None or angle<=95))
    label='Provável imagem do acesso/entorno imediato da propriedade' if probable else 'Imagem de via pública mais próxima localizada no entorno do CAR'
    return {'ok':True,'source':'KartaView / OpenStreetCam','license':'CC BY-SA 4.0','attribution':ATTRIBUTION,'photo_id':best['id'],'image_url':url,'shot_date':merged.get('shotDate') or merged.get('dateAdded'),'latitude':best['lat'],'longitude':best['lon'],'heading':best.get('heading'),'distance_to_car_m':dist,'heading_to_property_diff_deg':angle,'probable_access':probable,'label':label,'google_maps_streetview_url':f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={best['lat']},{best['lon']}",'note':'Imagem pública georreferenciada próxima ao limite do CAR. Sem portão/entrada cadastrado ou validação de campo, não é correto afirmar que a foto mostra a entrada oficial da fazenda.'}


async def download_street_level_access(car_geometry:dict[str,Any],out_path:str|Path):
    meta=await find_street_level_access(car_geometry)
    if not meta.get('ok'):return meta
    out=Path(out_path);out.parent.mkdir(parents=True,exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.30-street-access'}) as c:
            r=await c.get(meta['image_url']);r.raise_for_status();raw=r.content
        if len(raw)<5000:raise ValueError('image_too_small')
        out.write_bytes(raw);meta['path']=str(out);meta['bytes']=len(raw);return meta
    except Exception as e:
        return {**meta,'ok':False,'detail':f'image_download:{type(e).__name__}:{str(e)[:200]}'}


print('RX_STREET_LEVEL_ACCESS=KARTAVIEW_CC_BY_SA',flush=True)

from __future__ import annotations

import json
import subprocess
from urllib.parse import urlencode
from typing import Any

from pyproj import Geod
from shapely.geometry import shape
from shapely.ops import unary_union

ANM_QUERY='https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer/0/query'
GEOD=Geod(ellps='GRS80')


def _area_ha(g):
    try:
        return abs(GEOD.geometry_area_perimeter(g)[0])/10000.0 if g is not None and not g.is_empty else 0.0
    except Exception:
        return 0.0


def query_anm_curl_exact(car_geometry:dict[str,Any], bbox:list[float]):
    env=','.join(str(x) for x in bbox)
    params={
        'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4326',
        'spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','outSR':'4326','resultRecordCount':'2000'
    }
    url=ANM_QUERY+'?'+urlencode(params)
    try:
        p=subprocess.run(['curl','-sS','--retry','2','--retry-delay','1','--connect-timeout','15','--max-time','50','-A','Raio-X-Territorial/0.15.3',url],capture_output=True,timeout=60)
        if p.returncode:
            return {'ok':False,'source':'ANM/SIGMINE','error':'curl','detail':p.stderr.decode('utf-8','ignore')[:300]}
        data=json.loads(p.stdout.decode('utf-8'))
        if data.get('error'):
            return {'ok':False,'source':'ANM/SIGMINE','error':'arcgis','detail':str(data.get('error'))[:400]}
        fs=data.get('features') or []
        car=shape(car_geometry); intersections=[]; occ=[]
        for f in fs:
            try:
                g=shape(f.get('geometry'))
                if not car.intersects(g): continue
                inter=car.intersection(g)
                if inter.is_empty: continue
                intersections.append(inter)
                props=f.get('properties') or {}
                safe={}
                for k,v in props.items():
                    lk=str(k).lower()
                    if any(x in lk for x in ('process','numero','número','subst','fase','titular','evento','area','área','ano','uf','municip')) and v not in (None,''):
                        safe[str(k)]=v
                    if len(safe)>=14: break
                occ.append({'area_intersection_ha':round(_area_ha(inter),6),'properties':safe})
            except Exception:
                continue
        union=unary_union(intersections) if intersections else None
        return {
            'ok':True,'status':200,'feature_count_bbox':len(fs),'features':fs,
            'source':'ANM/SIGMINE','exact':{
                'available':True,'occurrence_count':len(intersections),
                'area_unique_ha':round(_area_ha(union),6) if union is not None else 0.0,
                'occurrences':occ[:100]
            },
            'transport':'curl-retry'
        }
    except Exception as e:
        return {'ok':False,'source':'ANM/SIGMINE','error':type(e).__name__,'detail':str(e)[:300]}

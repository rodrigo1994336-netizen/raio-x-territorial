from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from pyproj import Transformer, Geod

URL_TEMPLATE='https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/collection11/lulc/coverage/brazil_coverage/brazil_coverage-col11_{year}.tif'
GEOD=Geod(ellps='GRS80')

CLASS_NAMES={
    1:'Floresta',3:'Formação florestal',4:'Formação savânica',5:'Mangue',6:'Floresta alagável',
    9:'Silvicultura',10:'Vegetação herbácea/arbustiva',11:'Área alagável',12:'Formação campestre',
    14:'Agropecuária',15:'Pastagem',18:'Agricultura',19:'Lavoura temporária',20:'Cana',21:'Mosaico de usos',
    22:'Área não vegetada',23:'Praia/duna/areal',24:'Área urbana',25:'Outras áreas não vegetadas',
    26:'Corpo d’água',29:'Afloramento rochoso',30:'Mineração',31:'Aquicultura',33:'Rio/lago/oceano',
    35:'Dendê',36:'Lavoura perene',39:'Soja',40:'Arroz',41:'Outras lavouras temporárias',46:'Café',47:'Citrus',48:'Outras lavouras perenes',49:'Restinga arborizada',50:'Restinga herbácea',62:'Algodão'
}
NATIVE_CODES={3,4,5,6,10,11,12,49,50}
AGRI_CODES={14,15,18,19,20,21,35,36,39,40,41,46,47,48,62}
WATER_CODES={26,31,33}


def _area_ha(g):
    try:return abs(GEOD.geometry_area_perimeter(g)[0])/10000.0
    except Exception:return None


def query_mapbiomas_coverage(car_geometry:dict[str,Any],year:int=2025):
    year=max(1985,min(int(year),2025));url=URL_TEMPLATE.format(year=year)
    try:car=shape(car_geometry)
    except Exception as e:return {'ok':False,'source':'MapBiomas Coleção 11','detail':f'geometry:{e}'}
    try:
        with rasterio.Env(
            GDAL_HTTP_MULTIRANGE='YES',GDAL_HTTP_MERGE_CONSECUTIVE_RANGES='YES',
            GDAL_HTTP_TIMEOUT='25',GDAL_HTTP_CONNECTTIMEOUT='10',GDAL_HTTP_MAX_RETRY='2',
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.TIF'
        ):
            with rasterio.open(url) as src:
                tf=Transformer.from_crs('EPSG:4326',src.crs,always_xy=True)
                g=shp_transform(tf.transform,car)
                minx,miny,maxx,maxy=g.bounds
                win=from_bounds(minx,miny,maxx,maxy,transform=src.transform).round_offsets().round_lengths()
                # Guard against malformed bounds or accidental large download.
                if win.width<=0 or win.height<=0 or win.width*win.height>2_000_000:
                    return {'ok':False,'source':'MapBiomas Coleção 11','detail':f'window_guard:{win.width}x{win.height}'}
                arr=src.read(1,window=win,boundless=True,fill_value=0)
                tr=src.window_transform(win)
                inside=geometry_mask([mapping(g)],out_shape=arr.shape,transform=tr,invert=True,all_touched=False)
        vals=arr[inside]
        vals=vals[(vals>0)&np.isfinite(vals)]
        if vals.size==0:return {'ok':False,'source':'MapBiomas Coleção 11','detail':'no_pixels_inside_car','url':url}
        counts=Counter(int(x) for x in vals.tolist());total=sum(counts.values());car_area=_area_ha(car)
        rows=[]
        for code,count in counts.most_common():
            pct=count/total*100.0;area=(car_area*pct/100.0) if car_area is not None else None
            rows.append({'code':code,'class_name':CLASS_NAMES.get(code,f'Classe {code}'),'pixel_count':count,'share_pct':round(pct,2),'area_ha':round(area,4) if area is not None else None})
        pasture_count=counts.get(15,0);pasture_pct=pasture_count/total*100.0;pasture_area=(car_area*pasture_pct/100.0) if car_area is not None else None
        native_count=sum(counts.get(c,0) for c in NATIVE_CODES);native_pct=native_count/total*100.0
        agri_count=sum(counts.get(c,0) for c in AGRI_CODES);agri_pct=agri_count/total*100.0
        water_count=sum(counts.get(c,0) for c in WATER_CODES);water_pct=water_count/total*100.0
        return {
            'ok':True,'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11','year':year,'url':url,
            'pixel_count_inside':total,'car_area_ha_geodesic':round(car_area,4) if car_area is not None else None,
            'pasture_area_ha':round(pasture_area,4) if pasture_area is not None else None,'pasture_share_pct':round(pasture_pct,2),
            'native_vegetation_share_pct':round(native_pct,2),'agriculture_and_pasture_share_pct':round(agri_pct,2),'water_share_pct':round(water_pct,2),
            'classes':rows[:30],
            'note':'Classificação MapBiomas 30 m recortada ao CAR. Áreas por classe são estimadas pela participação dos pixels dentro do polígono multiplicada pela área geodésica do imóvel; limites de pixels e resolução geram incerteza cartográfica.'
        }
    except Exception as e:
        return {'ok':False,'source':'MapBiomas Brasil — Cobertura 30 m — Coleção 11','year':year,'url':url,'detail':f'{type(e).__name__}:{str(e)[:280]}'}


print('RX_MAPBIOMAS_COVERAGE=collection11_car_clip',flush=True)

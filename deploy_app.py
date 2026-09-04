from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, asyncio, json, subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

try:
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    from pyproj import Geod
    GEO_AVAILABLE=True
    GEOD=Geod(ellps='GRS80')
except Exception:
    GEO_AVAILABLE=False
    shape=mapping=unary_union=GEOD=None

app = FastAPI(title='Raio-X Territorial API', version='0.14.6-exact-live-analysis')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

TEST_CAR='MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F'
SICAR='https://geoserver.car.gov.br/geoserver/sicar/ows'
IBAMA_EMB='https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer/2/query'
SIGEF_MIRROR='https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10/query'
ANM='https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer/0/query'
PRODES='https://terrabrasilis.dpi.inpe.br/geoserver/ows'
TARGETS={
 'ibama':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/embargos_siscom_brasil/FeatureServer?f=pjson',
 'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
 'prodes':PRODES+'?service=WFS&request=GetCapabilities',
 'sigef_mirror':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10?f=pjson',
 'incra_root':'https://acervofundiario.incra.gov.br/'
}

def _curl(url:str, expect_json=True):
    p=subprocess.run(['curl','-k','-sS','--connect-timeout','12','--max-time','40','-A','Raio-X-Territorial/0.14.6',url],capture_output=True,timeout=45)
    if p.returncode:return {'ok':False,'detail':p.stderr.decode('utf-8','ignore')[:300],'bytes':len(p.stdout)}
    if not expect_json:return {'ok':bool(p.stdout),'bytes':len(p.stdout),'text':p.stdout.decode('utf-8','ignore')}
    try:return {'ok':True,'bytes':len(p.stdout),'json':json.loads(p.stdout.decode('utf-8'))}
    except Exception as e:return {'ok':False,'bytes':len(p.stdout),'detail':f'JSONDecodeError:{e}','preview':p.stdout[:200].decode('utf-8','ignore')}

def _iter_coords(x):
    if isinstance(x,(list,tuple)):
        if len(x)>=2 and isinstance(x[0],(int,float)) and isinstance(x[1],(int,float)):yield float(x[0]),float(x[1])
        else:
            for i in x:yield from _iter_coords(i)

def _bbox(geom):
    pts=list(_iter_coords((geom or {}).get('coordinates',[])))
    if not pts:return None
    xs=[x for x,_ in pts];ys=[y for _,y in pts]
    return [min(xs),min(ys),max(xs),max(ys)]

def _area_ha(g):
    if not GEO_AVAILABLE or g is None or g.is_empty:return None
    try:return abs(GEOD.geometry_area_perimeter(g)[0])/10000.0
    except Exception:return None

def _exact_geojson_intersections(car_geometry,features):
    if not GEO_AVAILABLE:return {'available':False,'reason':'shapely/pyproj_not_installed'}
    car=shape(car_geometry)
    items=[]; intersections=[]
    for f in features or []:
        geom=f.get('geometry')
        if not isinstance(geom,dict):continue
        try:
            src=shape(geom)
            if not car.intersects(src):continue
            inter=car.intersection(src)
            if inter.is_empty:continue
            ha=_area_ha(inter)
            if ha is None or ha<=0:continue
            intersections.append(inter)
            items.append({'id':f.get('id'),'area_intersection_ha':round(ha,6),'properties':f.get('properties') or {}})
        except Exception:continue
    union=unary_union(intersections) if intersections else None
    total=_area_ha(union) if union is not None else 0.0
    return {'available':True,'occurrence_count':len(items),'area_unique_ha':round(total or 0.0,6),'occurrences':items}

def fetch_car_live(car_code:str):
    uf=car_code[:2];tn=f"sicar:sicar_imoveis_{'DF' if uf=='DF' else uf.lower()}"
    q={'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':tn,'outputFormat':'application/json','CQL_FILTER':f"cod_imovel IN ('{car_code}')"}
    r=_curl(SICAR+'?'+urlencode(q),True)
    if not r.get('ok'):return {'ok':False,'source':'SICAR',**{k:r.get(k) for k in ('detail','preview','bytes')}}
    fs=r['json'].get('features') or []
    if not fs:return {'ok':False,'source':'SICAR','not_found':True,'feature_count':0}
    f=fs[0]
    return {'ok':True,'source':'SICAR','feature_count':len(fs),'properties':f.get('properties') or {},'geometry':f.get('geometry'),'bbox':_bbox(f.get('geometry')),'bytes':r.get('bytes',0)}

async def arcgis_bbox(url,bbox,out_fields='*',in_sr='4674',out_sr='4674',f='geojson'):
    env=','.join(str(x) for x in bbox)
    p={'f':f,'where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':in_sr,'spatialRel':'esriSpatialRelIntersects','outFields':out_fields,'returnGeometry':'true','outSR':out_sr,'resultRecordCount':'2000'}
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True) as c:rr=await c.get(url,params=p)
        data=rr.json();fs=data.get('features') or []
        return {'ok':rr.status_code==200 and 'error' not in data,'status':rr.status_code,'feature_count':len(fs),'features':fs,'error':data.get('error')}
    except Exception as e:return {'ok':False,'error':type(e).__name__,'detail':str(e)[:250]}

async def query_sigef(bbox):
    fields='parcela_co,situacao_i,codigo_imo,data_submi,data_aprov,status,nome_area,registro_m,registro_d,municipio_,uf_id'
    r=await arcgis_bbox(SIGEF_MIRROR,bbox,fields,'4674','4674','json');r['source']='IBAMA/PAMGIA espelho público SIGEF-INCRA';return r
async def query_embargos(bbox):
    r=await arcgis_bbox(IBAMA_EMB,bbox,'*','4674','4674','geojson');r['source']='IBAMA/PAMGIA embargos SISCOM';return r
async def query_anm(bbox):
    r=await arcgis_bbox(ANM,bbox,'*','4326','4326','geojson');r['source']='ANM/SIGMINE';return r

def _local(tag):return tag.rsplit('}',1)[-1]
def _layer_score(name,title=''):
    s=(name+' '+title).lower()
    if 'prodes' not in s:return -10000
    sc=100
    for t,p in [('yearly_deforestation',100),('increment',80),('deforestation',50),('desmat',40),('cerrado',30)]:
        if t in s:sc+=p
    for t,p in [('mosaic',-150),('temporal',-100),('hydro',-100),('rates',-100),('uf_mun',-100),('residue',-80),('residual',-80)]:
        if t in s:sc+=p
    return sc

async def query_prodes(bbox):
    try:
        async with httpx.AsyncClient(timeout=40,follow_redirects=True) as c:
            cap=await c.get(PRODES,params={'service':'WFS','version':'2.0.0','request':'GetCapabilities'});root=ET.fromstring(cap.text);layers=[]
            for ft in root.iter():
                if _local(ft.tag)!='FeatureType':continue
                name=title=None
                for ch in ft:
                    if _local(ch.tag)=='Name' and ch.text:name=ch.text.strip()
                    if _local(ch.tag)=='Title' and ch.text:title=ch.text.strip()
                if name and _layer_score(name,title or '')>0:layers.append((_layer_score(name,title or ''),name,title))
            layers=sorted(layers,reverse=True)[:8];results=[];xmin,ymin,xmax,ymax=bbox
            for score,name,title in layers:
                try:
                    rr=await c.get(PRODES,params={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':name,'srsName':'EPSG:4674','bbox':f'{xmin},{ymin},{xmax},{ymax},EPSG:4674','count':'2000','outputFormat':'application/json'})
                    data=rr.json();fs=data.get('features') or []
                    if fs:results.append({'layer':name,'title':title,'score':score,'count':len(fs),'features':fs})
                except Exception as e:results.append({'layer':name,'score':score,'error':type(e).__name__})
        return {'ok':True,'candidate_layers':[x[1] for x in layers],'hits':results,'feature_count':sum(x.get('count',0) for x in results),'source':'INPE/TerraBrasilis WFS'}
    except Exception as e:return {'ok':False,'error':type(e).__name__,'detail':str(e)[:250]}

async def probe_sources():
    async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=12),follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.14.6'}) as c:
        async def one(k,u):
            try:r=await c.get(u);return k,{'ok':200<=r.status_code<400,'status':r.status_code,'bytes':len(r.content)}
            except Exception as e:return k,{'ok':False,'error':type(e).__name__}
        out=dict(await asyncio.gather(*[one(k,u) for k,u in TARGETS.items()]))
    cap=await asyncio.to_thread(_curl,SICAR+'?service=WFS&version=1.0.0&request=GetCapabilities',False);out['sicar_curl']={'ok':cap.get('ok'),'bytes':cap.get('bytes',0)}
    out['exact_geometry_engine']={'ok':GEO_AVAILABLE}
    return out

async def analyze_car(car_code:str):
    car=await asyncio.to_thread(fetch_car_live,car_code.upper())
    if not car.get('ok'):return {'car':car}
    bbox=car['bbox'];sigef,emb,anm,prodes=await asyncio.gather(query_sigef(bbox),query_embargos(bbox),query_anm(bbox),query_prodes(bbox))
    if GEO_AVAILABLE:
        emb['exact']=_exact_geojson_intersections(car['geometry'],emb.get('features') or [])
        anm['exact']=_exact_geojson_intersections(car['geometry'],anm.get('features') or [])
        pfs=[]
        for h in prodes.get('hits') or []:pfs.extend(h.get('features') or [])
        prodes['exact']=_exact_geojson_intersections(car['geometry'],pfs)
    return {'car':car,'sigef':sigef,'embargos_ibama':emb,'anm':anm,'prodes':prodes}

def _exact_summary(r):
    ex=(r or {}).get('exact') or {}
    return {'available':ex.get('available'),'occurrence_count':ex.get('occurrence_count'),'area_unique_ha':ex.get('area_unique_ha')}
def _safe_summary(result):
    car=result.get('car') or {};props=car.get('properties') or {};summary={'geo_engine':GEO_AVAILABLE,'car':{'ok':car.get('ok'),'bbox':car.get('bbox'),'properties':{k:props.get(k) for k in ('cod_imovel','area','municipio','uf','m_fiscal','status_imovel','tipo_imovel','condicao') if k in props}}}
    for key in ('sigef','embargos_ibama','anm'):
        r=result.get(key) or {};item={'ok':r.get('ok'),'feature_count_bbox':r.get('feature_count'),'source':r.get('source')}
        if key!='sigef':item['exact']=_exact_summary(r)
        if r.get('features'):
            f=r['features'][0];item['sample_properties']=f.get('properties') or f.get('attributes') or {}
        summary[key]=item
    p=result.get('prodes') or {};summary['prodes']={'ok':p.get('ok'),'feature_count_bbox':p.get('feature_count'),'exact':_exact_summary(p),'candidate_layers':p.get('candidate_layers'),'hit_layers':[{'layer':h.get('layer'),'count':h.get('count')} for h in p.get('hits',[]) if h.get('count')]}
    ex=(p.get('exact') or {}).get('occurrences') or []
    if ex:
        summary['prodes']['exact_occurrences']=[{'area_intersection_ha':x.get('area_intersection_ha'),'year':(x.get('properties') or {}).get('year'),'class_name':(x.get('properties') or {}).get('class_name'),'image_date':(x.get('properties') or {}).get('image_date'),'satellite':(x.get('properties') or {}).get('satellite'),'sensor':(x.get('properties') or {}).get('sensor')} for x in ex]
    return summary

@app.on_event('startup')
async def startup():
    print('RX_STARTUP_BEGIN',flush=True)
    try:
        print('RX_SOURCE_PROBE='+json.dumps(await probe_sources(),ensure_ascii=False,default=str),flush=True)
        print('RX_REAL_ANALYSIS='+json.dumps(_safe_summary(await analyze_car(TEST_CAR)),ensure_ascii=False,default=str),flush=True)
    except Exception as e:print(f'RX_STARTUP_FATAL={type(e).__name__}:{str(e)[:300]}',flush=True)

@app.get('/')
def root():return {'app':'Raio-X Territorial','status':'online','version':'0.14.6-exact-live-analysis','geo_engine':GEO_AVAILABLE}
@app.get('/health')
def health():return {'ok':True,'env':os.getenv('APP_ENV','unknown'),'geo_engine':GEO_AVAILABLE}
@app.get('/v1/live/probe')
async def live_probe():return {'sources':await probe_sources()}
@app.get('/v1/live/car/{car_code}')
async def live_car(car_code:str):
    r=await analyze_car(car_code)
    if not (r.get('car') or {}).get('ok'):raise HTTPException(status_code=404 if (r.get('car') or {}).get('not_found') else 502,detail=_safe_summary(r))
    return r
@app.get('/v1/live/summary/{car_code}')
async def live_summary(car_code:str):return _safe_summary(await analyze_car(car_code))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os, httpx, asyncio, json, subprocess
from external_process_lifecycle import ManagedProcessCancelled, install_shutdown_cleanup, run_managed_process
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
import source_layer_guard as layer_guard
import incra_acervo_f2
import br_bridge

try:
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    from pyproj import Geod
    GEO_AVAILABLE=True
    GEOD=Geod(ellps='GRS80')
except Exception:
    GEO_AVAILABLE=False
    shape=mapping=unary_union=GEOD=make_valid=None

app = FastAPI(title='Raio-X Territorial API', version='0.14.6-exact-live-analysis')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])
install_shutdown_cleanup(app)

TEST_CAR='MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F'
SICAR='https://geoserver.car.gov.br/geoserver/sicar/ows'
# H1 (13/09/2026): embargos_siscom_brasil/FeatureServer/2 has 0 records nationwide; the
# official IBAMA embargo list is adm_embargos_ibama_a (91.197 embargos, last 12/09/2026).
IBAMA_EMB_LAYER=layer_guard.LAYERS['ibama_embargos']['url']
IBAMA_EMB=IBAMA_EMB_LAYER+'/query'
# Explicit fields only: never the name, CPF/CNPJ, property name or free-text location of the embargoed person.
IBAMA_EMB_FIELDS='objectid,num_tad,serie_tad,dat_embargo,sit_desmatamento,tipo_area,qtd_area_embargada,origem_geom,uf,municipio,dat_ult_alteracao'
IBAMA_EMB_SOURCE='IBAMA — áreas embargadas (base oficial adm_embargos_ibama_a)'
SIGEF_MIRROR='https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10/query'
ANM='https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer/0/query'
PRODES='https://terrabrasilis.dpi.inpe.br/geoserver/ows'
TARGETS={
 'ibama':IBAMA_EMB_LAYER+'?f=pjson',
 'anm':'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer?f=pjson',
 'prodes':PRODES+'?service=WFS&request=GetCapabilities',
 'sigef_mirror':'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10?f=pjson',
 'incra_root':'https://acervofundiario.incra.gov.br/'
}

def _curl(url:str, expect_json=True, *, cancel_event=None, connect_timeout=12, max_time=40, hard_timeout=45):
    args=['curl','-k','-sS','--connect-timeout',str(connect_timeout),'--max-time',str(max_time),'-A','Raio-X-Territorial/0.14.6',url]
    try:
        # SICAR/INCRA pela Ponte no Brasil quando configurada; sem ela, a mesma chamada de antes.
        p=br_bridge.run_curl(args,timeout_seconds=hard_timeout,cancel_event=cancel_event,runner=run_managed_process)
    except ManagedProcessCancelled:
        return {'ok':False,'cancelled':True,'detail':'request_cancelled','bytes':0}
    except subprocess.TimeoutExpired:
        return {'ok':False,'timed_out':True,'detail':f'process_timeout_after_{hard_timeout}s','bytes':0}
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

def _valid_shape(geometry):
    """shapely geometry, repaired when invalid (a self-intersecting ring makes GEOS raise)."""
    g=shape(geometry)
    if not g.is_valid:g=make_valid(g)
    return g

def _exact_geojson_intersections(car_geometry,features):
    if not GEO_AVAILABLE:return {'available':False,'reason':'shapely/pyproj_not_installed'}
    car=_valid_shape(car_geometry)
    items=[]; intersections=[]; geometry_errors=0
    for f in features or []:
        geom=f.get('geometry')
        if not isinstance(geom,dict):continue
        try:
            src=_valid_shape(geom)
            if not car.intersects(src):continue
            inter=car.intersection(src)
            if inter.is_empty:continue
            ha=_area_ha(inter)
            if ha is None or ha<=0:continue
            intersections.append(inter)
            items.append({'id':f.get('id'),'area_intersection_ha':round(ha,6),'properties':f.get('properties') or {}})
        except Exception:
            # H1: a feature that could not be measured is not an absence; callers turn it into a pending reading.
            geometry_errors+=1
    union=unary_union(intersections) if intersections else None
    total=_area_ha(union) if union is not None else 0.0
    return {'available':True,'occurrence_count':len(items),'area_unique_ha':round(total or 0.0,6),'occurrences':items,'geometry_errors':geometry_errors}

def finalize_prodes(prodes,car_geometry):
    """Exact PRODES reading for the property, and the final answer/pending decision.

    Runs wherever a PRODES result is produced (analyze_car and the core retries), so a
    retried result never loses ``exact``. A partial catalog (a yearly layer failed or
    truncated) is an answer only while it still holds an occurrence inside the property;
    a feature that could not be measured makes a zero pending. A pending reading keeps
    no count (source_layer_guard.blank_counts)."""
    prodes=prodes if isinstance(prodes,dict) else {'ok':False,'detail':'consulta_pendente:prodes_sem_resultado'}
    if GEO_AVAILABLE and isinstance(car_geometry,dict):
        pfs=[]
        for h in prodes.get('hits') or []:pfs.extend(h.get('features') or [])
        try:prodes['exact']=_exact_geojson_intersections(car_geometry,pfs)
        except Exception as e:prodes['exact']={'available':False,'reason':f'geometry_error:{type(e).__name__}'}
    ex=prodes.get('exact') or {}
    count=ex.get('occurrence_count') if ex.get('available') else None
    if prodes.get('ok') is True:
        reason=None
        if count is None:reason='prodes_exact_unavailable'
        elif not count and ex.get('geometry_errors'):reason='geometry_error'
        elif not count and prodes.get('source_state')=='partial':reason=(prodes.get('layer_guard') or {}).get('reason') or 'prodes_partial'
        if reason:
            layer_guard.apply_verdict(prodes,{'answer':False,'state':'pending','reason':reason})
            prodes['detail']=f'consulta_pendente:{reason}'
    else:
        layer_guard.blank_counts(prodes)
    return prodes

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
        data=rr.json();fs=(data.get('features') or []) if isinstance(data,dict) else []
        problem=layer_guard.arcgis_answer_problem(rr.status_code,data)
        return {'ok':problem is None,'status':rr.status_code,'feature_count':len(fs),'features':fs,'error':data.get('error') if isinstance(data,dict) else None,'answer_problem':problem}
    except Exception as e:return {'ok':False,'error':type(e).__name__,'detail':str(e)[:250]}

async def query_sigef(bbox):
    fields='parcela_co,situacao_i,codigo_imo,data_submi,data_aprov,status,nome_area,registro_m,registro_d,municipio_,uf_id'
    r=await arcgis_bbox(SIGEF_MIRROR,bbox,fields,'4674','4674','json');r['source']='IBAMA/PAMGIA espelho público SIGEF-INCRA'
    # The mirror stopped in 04/2022 and holds only public parcels: its silence is not an answer.
    if r.get('ok') or r.get('answer_problem'):
        layer_guard.apply_verdict(r,await layer_guard.zero_verdict_async('sigef_publico_espelho',zero=not r.get('feature_count'),answer_problem=r.get('answer_problem')))
    return r

def _ms_date_br(v):
    try:return (datetime.fromtimestamp(float(v)/1000.0,tz=timezone.utc)-timedelta(hours=3)).strftime('%d/%m/%Y')
    except Exception:return None

def _embargo_public_row(props,area_in_property_ha):
    origin=str(props.get('origem_geom') or '')
    point=origin.lower().startswith('ponto')
    sit=str(props.get('sit_desmatamento') or '').strip().upper()
    tad=str(props.get('num_tad') or '').strip();serie=str(props.get('serie_tad') or '').strip()
    return {
        'tad':(tad+(' '+serie if serie else '')) if tad else None,
        'date':_ms_date_br(props.get('dat_embargo')),
        'date_ms':props.get('dat_embargo'),
        'type':(str(props.get('tipo_area') or '').strip() or None),
        'deforestation':{'D':True,'N':False}.get(sit),
        'declared_area_ha':props.get('qtd_area_embargada'),
        'area_in_property_ha':None if point else area_in_property_ha,
        'geometry_origin':'ponto' if point else ('polígono' if origin else None),
        'uf':props.get('uf'),'municipio':props.get('municipio'),
    }

def _embargo_exact(car_geometry,features):
    car=_valid_shape(car_geometry);rows=[];polys=[];occ=[];geometry_errors=0
    for f in features or []:
        geom=f.get('geometry');props=f.get('properties') or {}
        if not isinstance(geom,dict):continue
        try:
            src=_valid_shape(geom)
            if not car.intersects(src):continue
            point=str(props.get('origem_geom') or '').lower().startswith('ponto')
            if point:
                # A point embargo is published as a ~1 m circle: it marks a place, never an area.
                area=None
            else:
                inter=car.intersection(src);area=_area_ha(inter)
                # A shared border or a numeric sliver is not an embargo on the property.
                if inter.is_empty or area is None or area<0.0001:continue
                polys.append(inter)
            row=_embargo_public_row(props,round(area,4) if area is not None else None)
            rows.append(row);occ.append({'id':props.get('objectid'),'area_intersection_ha':row['area_in_property_ha'],'properties':row})
        except Exception:
            # An embargo returned for the envelope that could not be measured is never dropped silently.
            geometry_errors+=1
    order=sorted(range(len(rows)),key=lambda i:float(rows[i].get('date_ms') or 0),reverse=True)
    rows=[rows[i] for i in order];occ=[occ[i] for i in order]
    union=unary_union(polys) if polys else None
    return {'available':True,'occurrence_count':len(rows),'area_unique_ha':round(_area_ha(union) or 0.0,4) if union is not None else 0.0,
            'point_occurrence_count':sum(1 for x in rows if x['geometry_origin']=='ponto'),'occurrences':occ,'items':rows,'geometry_errors':geometry_errors}

async def query_embargos(bbox,geometry=None):
    """Official IBAMA embargoes intersecting the property (exact geometry).

    ok=True only for a complete answer from a live layer; a zero from an empty or
    broken layer is a pending consultation, never "nenhum embargo"."""
    base={'source':IBAMA_EMB_SOURCE,'layer':IBAMA_EMB_LAYER}
    if not GEO_AVAILABLE or not isinstance(geometry,dict):
        return {**base,'ok':False,'source_state':'pending','detail':'consulta_pendente:car_geometry_missing'}
    env=','.join(str(x) for x in bbox);features=[];status=None;problem=None
    try:
        async with httpx.AsyncClient(timeout=35,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/h1-embargos'}) as c:
            for page in range(10):
                p={'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4674','spatialRel':'esriSpatialRelIntersects',
                   'outFields':IBAMA_EMB_FIELDS,'returnGeometry':'true','outSR':'4674','orderByFields':'objectid','resultOffset':str(page*2000),'resultRecordCount':'2000'}
                rr=await c.get(IBAMA_EMB,params=p);status=rr.status_code
                try:data=rr.json()
                except Exception:data=None
                problem=layer_guard.arcgis_answer_problem(status,data)
                if problem=='exceeded_transfer_limit':
                    features.extend(data.get('features') or [])
                    problem='exceeded_transfer_limit_after_10_pages' if page==9 else None
                    continue
                if problem is None:features.extend(data.get('features') or [])
                break
    except Exception as e:
        problem=type(e).__name__
    out={**base,'status':status,'feature_count':len(features)}
    if problem:
        return layer_guard.apply_verdict({**out,'ok':False,'detail':f'consulta_pendente:{problem}'},{'answer':False,'state':'pending','reason':problem})
    try:exact=_embargo_exact(geometry,features)
    except Exception as e:
        return layer_guard.apply_verdict({**out,'ok':False,'detail':'consulta_pendente:geometry_error'},{'answer':False,'state':'pending','reason':f'geometry_error:{type(e).__name__}'})
    out.update(ok=True,exact=exact)
    if exact.get('geometry_errors'):
        return layer_guard.apply_verdict({**out,'ok':False,'detail':'consulta_pendente:geometry_error'},{'answer':False,'state':'pending','reason':'geometry_error'})
    verdict=await layer_guard.zero_verdict_async('ibama_embargos',zero=exact['occurrence_count']==0)
    return layer_guard.apply_verdict(out,verdict)
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
    bbox=car['bbox'];sigef,emb,anm,prodes=await asyncio.gather(query_sigef(bbox),query_embargos(bbox,car.get('geometry')),query_anm(bbox),query_prodes(bbox))
    if GEO_AVAILABLE:
        anm['exact']=_exact_geojson_intersections(car['geometry'],anm.get('features') or [])
        if anm.get('ok') is True and anm['exact'].get('geometry_errors'):
            layer_guard.apply_verdict(anm,{'answer':False,'state':'pending','reason':'geometry_error'})
        elif anm.get('ok') is not True:
            layer_guard.blank_counts(anm)
    prodes=finalize_prodes(prodes,car.get('geometry'))
    result={'car':car,'sigef':sigef,'embargos_ibama':emb,'anm':anm,'prodes':prodes}
    # Geometria é CPU: fora do laço de eventos para não travar os outros usuários.
    await asyncio.to_thread(_apply_prodes_reading,result)
    return result

def _apply_prodes_reading(result):
    # F2: uma leitura só do PRODES para portal, relatório e alertas (prodes_reading_f2).
    # 'exact' passa a contar só o que está dentro do imóvel; o bruto fica em 'exact_raw'.
    # Roda depois do finalize_prodes (H1), que decide resposta/pendência pelo catálogo.
    # Se a leitura falhar, a análise segue com o cálculo bruto (nunca some ocorrência).
    try:
        import prodes_reading_f2
        prodes_reading_f2.apply_reading_to_result(result)
    except Exception as e:
        print(f'RX_PRODES_READING_FAIL={type(e).__name__}:{str(e)[:160]}',flush=True)

def _exact_summary(r):
    ex=(r or {}).get('exact') or {}
    return {'available':ex.get('available'),'occurrence_count':ex.get('occurrence_count'),'area_unique_ha':ex.get('area_unique_ha')}
def _safe_summary(result):
    car=result.get('car') or {};props=car.get('properties') or {};summary={'geo_engine':GEO_AVAILABLE,'car':{'ok':car.get('ok'),'bbox':car.get('bbox'),'properties':{k:props.get(k) for k in ('cod_imovel','area','municipio','uf','m_fiscal','status_imovel','tipo_imovel','condicao') if k in props}}}
    for key in ('sigef','embargos_ibama','anm'):
        r=result.get(key) or {};item={'ok':r.get('ok'),'feature_count_bbox':r.get('feature_count'),'source':r.get('source')}
        if key!='sigef':item['exact']=_exact_summary(r)
        else:
            # F2: the mirror envelope count is not the certification; the summary carries the official state.
            summary[key]=incra_acervo_f2.summary_item(result.get('incra_acervo'));continue
        if r.get('features'):
            f=r['features'][0];item['sample_properties']=f.get('properties') or f.get('attributes') or {}
        summary[key]=item
    if isinstance(result.get('prodes'),dict) and 'reading' not in result['prodes']:_apply_prodes_reading(result)
    p=result.get('prodes') or {};summary['prodes']={'reading':p.get('reading'),'ok':p.get('ok'),'feature_count_bbox':p.get('feature_count'),'exact':_exact_summary(p),'candidate_layers':p.get('candidate_layers'),'hit_layers':[{'layer':h.get('layer'),'count':h.get('count')} for h in p.get('hits',[]) if h.get('count')]}
    ex=(p.get('exact') or {}).get('occurrences') or []
    if ex:
        summary['prodes']['exact_occurrences']=[{'area_intersection_ha':x.get('area_intersection_ha'),'year':(x.get('properties') or {}).get('year'),'class_name':(x.get('properties') or {}).get('class_name'),'image_date':(x.get('properties') or {}).get('image_date')} for x in ex]
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

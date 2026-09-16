from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from shapely.geometry import shape

import portal_v8
import deploy_app
import property_identity_runtime
import public_property_name_seed_v43 as seed
from deploy_app import SIGEF_MIRROR, _curl
import external_process_lifecycle as epl
from external_process_lifecycle import RequestDisconnected, wait_for_cancelling_processes
from property_identity_runtime import _clean_name, _osm_named_farms_bbox

app = portal_v8.app
# Teto desta consulta: o espelho SIGEF (uma chamada do curl padrao do deploy_app) e, quando o SIGEF nao
# devolve nome nenhum, o OSM ao vivo (os dois espelhos em sequencia) — os dois derivados dos modulos que
# os implementam —, mais a folga abaixo.
# Folga ARBITRADA (não medida) para a leitura das geometrias, que corre na mesma thread depois que o curl
# volta: até 220 feições do SIGEF pelo shapely, trabalho de CPU sem rede. Medido em 16/09 nesta máquina,
# no bbox de Curvelo: 5,1 s a rota inteira (rede inclusa) contra os 79,8 s do teto. Folga grande só adia
# o 504; folga pequena corta nome que hoje chega.
_GEOMETRY_S = 20.0
_NAMES_S = round(deploy_app.CURL_WORST_CASE_S + property_identity_runtime.OSM_WORST_CASE_S + _GEOMETRY_S, 1)
TTL_SECONDS = 900
_CACHE: dict[tuple[float, float, float, float, int], tuple[float, dict[str, Any]]] = {}


def _cache_key(west: float, south: float, east: float, north: float, limit: int):
    return (round(west, 3), round(south, 3), round(east, 3), round(north, 3), int(limit))


def _query_names_sync(west: float, south: float, east: float, north: float, limit: int = 60) -> dict[str, Any]:
    started = time.monotonic();cap=max(1,min(int(limit),100));key=_cache_key(west,south,east,north,cap);now=time.monotonic()
    cached=_CACHE.get(key)
    if cached and now-cached[0]<TTL_SECONDS:
        out=dict(cached[1]);out['coverage']=dict(out.get('coverage') or {});out['cached']=True;return out
    env=','.join(str(float(x)) for x in (west,south,east,north))
    params={'f':'geojson','where':'1=1','geometry':env,'geometryType':'esriGeometryEnvelope','inSR':'4326','spatialRel':'esriSpatialRelIntersects','outFields':'parcela_co,codigo_imo,nome_area,registro_m,registro_d,municipio_,uf_id,status,situacao_i','returnGeometry':'true','outSR':'4326','resultRecordCount':str(min(220,max(cap*3,cap)))}
    raw=_curl(SIGEF_MIRROR+'?'+urlencode(params),True)
    # Tentativa cancelada não é resposta. Sem esta saída, a thread abandonada seguia sem o SIGEF, montava a
    # resposta e GRAVAVA no cache: o cliente seguinte recebia 'consultei e não há nome nenhum nesta área'
    # por 15 minutos, sobre uma fonte que ninguém perguntou (regra 3 do dono, 'zero não é ausência').
    if raw.get('cancelled'):
        return {'ok':False,'items':[],'count':0,'cancelled':True,'detail':'request_cancelled',
                'source':'SIGEF + OpenStreetMap','coverage':{'elapsed_ms':round((time.monotonic()-started)*1000,1)}}
    sigef_available=bool(raw.get('ok'));data=(raw.get('json') or {}) if sigef_available else {};features=data.get('features') or []
    items=[];seen=set();valid_name_count=0;named_geometry_count=0;duplicate_count=0;sigef_reference_count=0
    for feature in features:
        props=feature.get('properties') or {};name=_clean_name(props.get('nome_area'));geom=feature.get('geometry')
        if name:valid_name_count+=1
        if not name or not geom:continue
        try:
            g=shape(geom)
            if g.is_empty:continue
            c=g.representative_point();center={'lat':float(c.y),'lon':float(c.x)}
        except Exception:continue
        named_geometry_count+=1;parcel=str(props.get('parcela_co') or '').strip();registry=props.get('registro_m') or props.get('registro_d');dedupe=parcel or f"{name.upper()}|{round(center['lat'],5)}|{round(center['lon'],5)}"
        if dedupe in seen:duplicate_count+=1;continue
        seen.add(dedupe)
        items.append({
            'name':name,'municipality':props.get('municipio_'),'uf':props.get('uf_id'),
            'parcel_code':props.get('parcela_co'),'property_code':props.get('codigo_imo'),
            'registry':registry,'status':props.get('status') or props.get('situacao_i'),'center':center,
            'source':'SIGEF/INCRA — espelho público',
            'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,
            'reference_kind':'SIGEF_CADASTRAL','map_anchor':'CADASTRAL_REFERENCE',
            'origin_label':'SIGEF/INCRA — referência cadastral ainda não vinculada ao CAR'
        });sigef_reference_count+=1
        if len(items)>=cap:break

    # This seed contains OSM name points already audited against one exact CAR code.
    # Spatial auditing does not establish a denomination under the frozen Stage 2 protocol.
    seed_added=0
    if len(items)<cap:
        for farm in seed.in_bbox(west,south,east,north,cap-len(items)):
            name=_clean_name(farm.get('name'));car_code=str(farm.get('car_code') or '').strip().upper()
            if not name or not car_code:continue
            dedupe=f"SEED|{car_code}|{name.casefold()}"
            if dedupe in seen:continue
            seen.add(dedupe)
            items.append({
                'name':name,'municipality':'Curvelo','uf':'MG','parcel_code':None,
                'property_code':car_code,'car_code':car_code,'registry':None,'status':None,
                'center':{'lat':float(farm['lat']),'lon':float(farm['lon'])},'osm_node_id':farm.get('osm_id'),
                'source':seed.SOURCE,
                'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,
                'reference_kind':'OSM_AUDITED','map_anchor':'GEOGRAPHIC_POINT',
                'validation_method':'AUDITED_OSM_POINT_TO_EXACT_CAR',
                'origin_label':'OpenStreetMap auditado — referência geográfica não confirmada para o CAR'
            });seed_added+=1
            if len(items)>=cap:break

    # Live OSM is cartographic context only. It is intentionally never CAR-name eligible.
    osm={'ok':False,'items':[],'count':0,'detail':'not_needed'};live_added=0
    if not items and len(items)<cap:
        osm=_osm_named_farms_bbox(west,south,east,north,max(1,cap-len(items)))
        if osm.get('ok'):
            for farm in osm.get('items') or []:
                name=_clean_name(farm.get('name'))
                if not name:continue
                center={'lat':float(farm['lat']),'lon':float(farm['lon'])};dedupe=f"OSM|{farm.get('osm_id')}|{name.casefold()}"
                if dedupe in seen:continue
                seen.add(dedupe)
                items.append({
                    'name':name,'municipality':None,'uf':None,'parcel_code':None,'property_code':None,'car_code':None,
                    'registry':None,'status':None,'center':center,'osm_node_id':farm.get('osm_id'),
                    'source':'OpenStreetMap contributors — denominação geográfica pública (ODbL)',
                    'display_kind':'REFERENCE','validation_status':'UNVALIDATED','panel_name_eligible':False,
                    'reference_kind':'OSM_LIVE','map_anchor':'GEOGRAPHIC_POINT',
                    'origin_label':'OpenStreetMap ao vivo — referência geográfica não confirmada para o CAR'
                });live_added+=1
                if len(items)>=cap:break

    items.sort(key=lambda x:(0 if x.get('display_kind')=='VALIDATED_PROPERTY_NAME' else 1,str(x.get('name') or '').upper(),str(x.get('municipality') or '').upper()))
    validated_count=sum(1 for x in items if x.get('display_kind')=='VALIDATED_PROPERTY_NAME' and x.get('panel_name_eligible') is True)
    reference_count=sum(1 for x in items if x.get('display_kind')=='REFERENCE')
    coverage={
        'sigef_candidates':len(features) if sigef_available else None,
        'sigef_with_valid_name':valid_name_count if sigef_available else None,
        'sigef_named_with_valid_geometry':named_geometry_count if sigef_available else None,
        'sigef_reference_names_returned':sigef_reference_count,
        'audited_seed_names_returned':seed_added,
        'osm_live_candidates':int(osm.get('count') or 0) if osm.get('ok') else None,
        'osm_live_reference_names_returned':live_added,
        'osm_candidates':seed_added+(int(osm.get('count') or 0) if osm.get('ok') else 0),
        'osm_names_returned':seed_added+live_added,
        'validated_property_names_returned':validated_count,
        'reference_names_returned':reference_count,
        'deduplicated':duplicate_count,'names_returned':len(items),'limit':cap,
        'elapsed_ms':round((time.monotonic()-started)*1000,1),
        # source_available dizia sim com o OSM sozinho e mascarava a ausência do SIGEF: ficava 'consultei'
        # sobre quem não foi consultado. Agora a fonte principal responde por si, e quem não respondeu é
        # nomeado em pending_sources.
        'sigef_available':sigef_available,
        'source_available':bool(sigef_available or seed_added or osm.get('ok'))
    }
    pending=[] if sigef_available else ['SIGEF/INCRA — espelho público']
    if not items and not osm.get('ok') and str(osm.get('detail') or '')!='not_needed':
        pending.append('OpenStreetMap ao vivo')
    coverage['pending_sources']=pending
    if not sigef_available and not seed_added and not osm.get('ok'):
        return {'ok':False,'items':[],'count':0,'source':'SIGEF + OpenStreetMap','detail':raw.get('detail') or raw.get('preview') or osm.get('detail') or 'fontes_indisponiveis','coverage':coverage}
    # ok:true significa "as fontes desta área responderam". Com o SIGEF fora, o que chega é o que as outras
    # trouxeram — nunca a área inteira: entrega o que veio, com a pendência declarada, e NUNCA grava no
    # cache (uma pendência congelada por 15 minutos vira 'não há nada aqui' para todo mundo).
    if pending:
        return {'ok':False,'items':items,'count':len(items),'pending_sources':pending,
                'detail':'consulta_pendente','source':'SIGEF/INCRA + OpenStreetMap — referências públicas',
                'cached':False,'coverage':coverage}
    out={
        'ok':True,'items':items,'count':len(items),'validated_count':validated_count,'reference_count':reference_count,
        'candidate_count':len(features),'truncated':len(items)>=cap,
        'source':'SIGEF/INCRA + OpenStreetMap — referências públicas com semântica de validação explícita',
        'cached':False,
        'note':'Somente itens VALIDATED_PROPERTY_NAME podem denominar um CAR. OSM ao vivo e SIGEF não vinculado são referências cartográficas/cadastrais e nunca preenchem o painel do imóvel.',
        'legend':{
            'validated':'Denominação validada — vinculada ao CAR',
            'osm_reference':'Referências do OpenStreetMap — não confirmadas para o imóvel',
            'sigef_reference':'Referências SIGEF/INCRA — não vinculadas ao CAR'
        },
        'coverage':coverage
    }
    # Segunda trava, independente da primeira: o cancelamento pode ter chegado DEPOIS do curl, enquanto as
    # geometrias eram lidas ou o OSM era tentado. A thread abandonada continua viva; o que ela escreve aqui
    # é o que o próximo cliente lê.
    if epl.scope_cancelled():
        out['cancelled']=True;return out
    _CACHE[key]=(now,out)
    if len(_CACHE)>300:
        for k,_ in sorted(_CACHE.items(),key=lambda kv:kv[1][0])[:60]:_CACHE.pop(k,None)
    return out


@app.get('/v1/live/property-names/viewport')
async def property_names_viewport(west:float,south:float,east:float,north:float,request:Request,limit:int=60,car_visible:int|None=None,diagnostic:bool=False):
    if not (-180<=west<east<=180 and -90<=south<north<=90):raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    if max(east-west,north-south)>1.50:raise HTTPException(status_code=422,detail='Aproxime o mapa para visualizar os nomes das fazendas.')
    # Dentro do escopo: o mapa do cliente troca de area a cada arrasto; o curl da area abandonada cai junto.
    try:
        out=await wait_for_cancelling_processes(
            asyncio.to_thread(_query_names_sync,west,south,east,north,limit),_NAMES_S,request=request)
    except asyncio.TimeoutError:raise HTTPException(status_code=504,detail='property_names_timeout')
    except RequestDisconnected:raise HTTPException(status_code=499,detail='client_disconnected')
    out=dict(out);coverage=dict(out.get('coverage') or {});coverage['car_visible']=max(0,min(int(car_visible),5000)) if car_visible is not None else None;out['coverage']=coverage
    if diagnostic:print('RX_PROPERTY_NAMES_COVERAGE='+json.dumps(coverage,ensure_ascii=False,separators=(',',':')),flush=True)
    return out


print('RX_PROPERTY_NAMES_V44=validated_names_separated_from_geographic_references',flush=True)

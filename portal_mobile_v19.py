from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from fastapi import HTTPException, Request
from shapely.geometry import Point, mapping, shape

import portal_v8
import portal_sicar_resilient as sicar

app=portal_v8.app

# Coarse state envelopes are only a fallback candidate selector. They never become
# the territorial answer: the actual property result still comes from official
# SICAR geometries. Multiple candidate UFs are queried near state borders.
UF_BBOX={
 'AC':(-74.1,-11.3,-66.5,-7.0),'AL':(-38.3,-10.6,-35.0,-8.7),'AP':(-54.9,0.7,-49.6,4.5),
 'AM':(-74.0,-10.0,-56.0,2.3),'BA':(-46.8,-18.5,-37.2,-8.4),'CE':(-41.5,-8.0,-37.1,-2.7),
 'DF':(-48.4,-16.2,-47.2,-15.4),'ES':(-42.0,-21.4,-39.6,-17.7),'GO':(-53.4,-19.6,-45.8,-12.3),
 'MA':(-48.9,-10.4,-41.7,-0.9),'MT':(-61.8,-18.1,-50.1,-7.2),'MS':(-58.3,-24.2,-50.8,-17.1),
 'MG':(-51.2,-23.0,-39.7,-14.1),'PA':(-59.0,-9.9,-45.9,2.7),'PB':(-38.9,-8.4,-34.7,-5.9),
 'PR':(-54.8,-26.8,-47.9,-22.4),'PE':(-41.5,-9.6,-34.7,-7.0),'PI':(-46.0,-11.0,-40.3,-2.6),
 'RJ':(-45.0,-23.5,-40.8,-20.6),'RN':(-38.7,-7.0,-34.8,-4.7),'RS':(-57.8,-33.9,-49.6,-27.0),
 'RO':(-66.9,-13.8,-59.6,-7.8),'RR':(-64.9,0.8,-58.7,5.4),'SC':(-54.0,-29.5,-48.2,-25.8),
 'SP':(-53.3,-25.5,-43.9,-19.7),'SE':(-38.4,-11.7,-36.3,-9.4),'TO':(-50.9,-13.6,-45.6,-5.1),
}


def _candidate_ufs(lat:float,lon:float,limit:int=5)->list[str]:
    rows=[]
    for uf,(w,s,e,n) in UF_BBOX.items():
        if w<=lon<=e and s<=lat<=n:
            area=(e-w)*(n-s)
            # Smaller envelope first; this is only an ordering heuristic.
            rows.append((area,uf))
    return [uf for _,uf in sorted(rows)[:limit]]


async def _resolve_ufs(lat:float,lon:float)->list[str]:
    try:
        uf=await portal_v8.base._reverse_uf(lat,lon)
        if uf:return [str(uf).upper()]
    except Exception:
        pass
    candidates=_candidate_ufs(lat,lon)
    if candidates:return candidates
    raise HTTPException(status_code=422,detail='Não foi possível determinar nenhum estado candidato para este ponto no Brasil.')


async def viewport_v19(west:float,south:float,east:float,north:float,uf:str|None=None,limit:int=50,request:Request=None):
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise HTTPException(status_code=422,detail='Área do mapa inválida.')
    span=max(east-west,north-south)
    if span>1.5:raise HTTPException(status_code=422,detail='Aproxime o mapa para carregar os imóveis rurais.')
    center_lat=(south+north)/2;center_lon=(west+east)/2
    local=None if uf else _local_ufs(west,south,east,north)
    ufs=[uf.upper()] if uf else (local if local else await _resolve_ufs(center_lat,center_lon))
    # Near borders query several plausible states. Stop early after useful data,
    # but never depend on reverse geocoding to get to SICAR.
    async def one(code):
        try:return code,await sicar._fetch_sicar_bbox(request,west,south,east,north,code,limit),None
        except Exception as exc:return code,None,exc
    vals=await asyncio.gather(*(one(x) for x in ufs[:5]))
    features=[];used=[];errors=[]
    cap=max(1,min(int(limit or 50),50))
    for code,fetched,err in vals:
        if err:
            errors.append(f'{code}:{type(err).__name__}')
            continue
        used.append(code)
        features.extend((fetched or {}).get('data',{}).get('features') or [])
    if not used and errors:
        raise HTTPException(status_code=502,detail='SICAR indisponível nos estados candidatos: '+', '.join(errors[:5]))
    tolerance=max(0.000002,min(0.00004,span/3500 if span else 0.000002))
    out=[];seen=set()
    for f in features:
        try:
            props=f.get('properties') or {};code=str(props.get('cod_imovel') or '')
            key=code or str(f.get('id') or id(f))
            if key in seen:continue
            seen.add(key)
            geom=f.get('geometry')
            if not geom:continue
            g=shape(geom).simplify(tolerance,preserve_topology=True)
            out.append({'type':'Feature','geometry':mapping(g),'properties':{k:props.get(k) for k in ('cod_imovel','area','municipio','uf','status_imovel','condicao','tipo_imovel','m_fiscal')}})
            if len(out)>=cap:break
        except Exception:continue
    return {'type':'FeatureCollection','features':out,'uf':used[0] if len(used)==1 else None,'ufs_consultadas':used,'source':'SICAR/WFS público · UF resiliente sem dependência obrigatória de geocodificador','truncated':len(features)>len(out),'fallback_uf':not bool(uf)}


def _local_ufs(west:float,south:float,east:float,north:float)->list[str]|None:
    """F1B 1B.6: every UF whose IBGE border is within the measured ambiguity band of the box (uf_locator_br).

    None only when the embedded mesh cannot be read (then the legacy resolver is the fallback). An empty
    list means the box is outside Brazil: there is no SICAR layer to ask.
    """
    try:
        import uf_locator_br
        return uf_locator_br.ufs_for_bbox(west,south,east,north)
    except Exception:
        return None


RESOLVE_EPS=.0018
# F1B: an answer for the same point (to 0,1 m) is reused for 2 min, so a repeated or looping anonymous call
# does not fan out to every UF layer again. Only answers are kept: a pending consultation (502) is never.
RESOLVE_CACHE_TTL_S=120.0
RESOLVE_CACHE_MAX=256
_RESOLVE_CACHE:OrderedDict[tuple[float,float],tuple[float,int,object]]=OrderedDict()


def _resolve_cached(key:tuple[float,float]):
    item=_RESOLVE_CACHE.get(key)
    if item is None:return None
    if time.monotonic()-item[0]>=RESOLVE_CACHE_TTL_S:
        _RESOLVE_CACHE.pop(key,None);return None
    return item


def _resolve_keep(key:tuple[float,float],status:int,body:object)->None:
    _RESOLVE_CACHE[key]=(time.monotonic(),status,body);_RESOLVE_CACHE.move_to_end(key)
    while len(_RESOLVE_CACHE)>RESOLVE_CACHE_MAX:_RESOLVE_CACHE.popitem(last=False)


async def resolve_v19(lat:float,lon:float,request:Request=None):
    """Property at a point. F1B 1B.6: the UF comes from the IBGE borders, not Nominatim.

    A point on a state border asks the SICAR layer of EVERY UF within the band, together, and returns the
    property whose polygon contains the point (the UF that covers the point first). Absence is only
    declared when every candidate UF answered: a UF that did not answer makes it a pending consultation
    (502), never "no property here" (tentar não é responder).
    """
    if not (-90<=lat<=90 and -180<=lon<=180):raise HTTPException(status_code=422,detail='Coordenadas inválidas.')
    eps=RESOLVE_EPS
    key=(round(float(lat),6),round(float(lon),6))
    hit=_resolve_cached(key)
    if hit is not None:
        if hit[1]==404:raise HTTPException(status_code=404,detail=hit[2])
        return hit[2]
    local=_local_ufs(lon-eps,lat-eps,lon+eps,lat+eps)
    ufs=local if local is not None else await _resolve_ufs(lat,lon)
    if not ufs:raise HTTPException(status_code=404,detail='Nenhum imóvel do SICAR foi localizado exatamente neste ponto.')
    point=Point(float(lon),float(lat))
    async def one(code):
        try:return code,await sicar._fetch_sicar_bbox(request,lon-eps,lat-eps,lon+eps,lat+eps,code,30),None
        except HTTPException as exc:
            if exc.status_code==499:raise
            return code,None,exc
        except Exception as exc:return code,None,exc
    vals=await asyncio.gather(*(one(code) for code in ufs[:5]))
    candidates=[];errors=[];asked=[]
    for code,fetched,err in vals:
        if err is not None:
            errors.append(f'{code}:{type(err).__name__}')
            continue
        asked.append(code)
        feats=(fetched or {}).get('data',{}).get('features') or []
        # 30 is not all: a layer that filled its cap may hold the property further down the list.
        if len(feats)>=int((fetched or {}).get('cap') or 30):errors.append(f'{code}:capped')
        for f in feats:
            try:
                g=shape(f.get('geometry'))
                if g.contains(point) or g.touches(point):candidates.append((code,f))
            except Exception:continue
    if not candidates:
        if errors:
            # The failing UFs and exception names go to the log only; the caller learns it is pending.
            print('RX_RESOLVE_V19_PENDING='+','.join(errors),flush=True)
            raise HTTPException(status_code=502,detail='O SICAR não respondeu para este ponto agora. Consulta pendente.')
        _resolve_keep(key,404,'Nenhum imóvel do SICAR foi localizado exatamente neste ponto.')
        raise HTTPException(status_code=404,detail='Nenhum imóvel do SICAR foi localizado exatamente neste ponto.')
    code,chosen=candidates[0];props=chosen.get('properties') or {}
    body={'ok':True,'source':'SICAR/WFS público · UF pelos limites do IBGE','uf':props.get('uf') or code,'ufs_consultadas':asked,'property':{'car_code':props.get('cod_imovel'),'municipality':props.get('municipio'),'uf':props.get('uf') or code,'area_ha':props.get('area'),'status':props.get('status_imovel'),'condition':props.get('condicao'),'type':props.get('tipo_imovel'),'fiscal_modules':props.get('m_fiscal'),'created_at':props.get('dat_criacao'),'updated_at':props.get('data_atualizacao')},'geometry':chosen.get('geometry'),'candidate_count':len(candidates),'exact_count':len(candidates)}
    _resolve_keep(key,200,body)
    return body

# Replace map routes after all previous SICAR compatibility patches.
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in {'/v1/live/sicar/viewport','/v1/live/resolve'}]
app.get('/v1/live/sicar/viewport')(viewport_v19)
app.get('/v1/live/resolve')(resolve_v19)
portal_v8.live_sicar_viewport=viewport_v19
portal_v8.base.resolve_point=resolve_v19

MOBILE_UI=r'''
<style>
@media(max-width:720px){
 html,body{height:100%;height:100dvh;overflow:hidden}
 .top{height:58px!important;padding:0 8px!important;gap:6px!important;background:rgba(5,16,11,.98)!important}
 .mark{width:34px!important;height:34px!important;border-radius:10px!important;font-size:14px!important}
 .search{height:38px!important;min-width:0!important;border-radius:11px!important}
 .search input{min-width:0!important;padding:0 10px!important;font-size:14px!important}
 .search button{margin:4px!important;padding:6px 11px!important;border-radius:9px!important;font-size:0!important}
 .search button:after{content:'⌕';font-size:20px;line-height:1}
 .rx-alert-bell{width:38px!important;height:38px!important;border-radius:11px!important;font-size:17px!important}
 .main{top:58px!important;bottom:0!important}
 .map{height:100%!important}
 .hint{display:none!important}
 .rx-map-state{top:8px!important;left:10px!important;right:10px!important;max-width:none!important;padding:6px 9px!important;border-radius:9px!important;font-size:9px!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;background:rgba(6,20,14,.86)!important;pointer-events:none!important}
 .rx-locate{top:auto!important;right:auto!important;left:10px!important;bottom:max(18px,env(safe-area-inset-bottom))!important;padding:9px 11px!important;border-radius:999px!important;font-size:0!important;width:43px!important;height:43px!important;display:grid!important;place-items:center!important}
 .rx-locate:after{content:'◎';font-size:22px}
 .rx-filter-btn{top:auto!important;right:auto!important;left:61px!important;bottom:max(18px,env(safe-area-inset-bottom))!important;padding:9px 12px!important;border-radius:999px!important;font-size:0!important;width:43px!important;height:43px!important;display:grid!important;place-items:center!important}
 .rx-filter-btn:after{content:'☷';font-size:19px}
 .rx-filter-panel{position:fixed!important;left:8px!important;right:8px!important;top:auto!important;bottom:calc(max(68px,env(safe-area-inset-bottom) + 60px))!important;width:auto!important;max-height:58dvh!important;overflow:auto!important;border-radius:18px!important}
 .leaflet-bottom.leaflet-right{bottom:74px!important}
 .leaflet-control-zoom{margin-right:10px!important;margin-bottom:8px!important}
 .card{left:8px!important;right:8px!important;bottom:calc(max(66px,env(safe-area-inset-bottom) + 56px))!important;width:auto!important;max-height:44dvh!important;overflow:auto!important;padding:14px!important;border-radius:18px!important}
 .card h2{font-size:16px!important}.meta{margin-bottom:9px!important}.grid3{margin:8px 0 10px!important}.actions{grid-template-columns:1fr 1fr!important}.actions .btn{grid-column:1/3!important}
 .panel{position:fixed!important;top:auto!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;height:76dvh!important;border-left:0!important;border-top:1px solid #244136!important;border-radius:22px 22px 0 0!important;box-shadow:0 -24px 70px #000b!important}
 .phead{padding:11px 12px!important;border-radius:22px 22px 0 0!important}.pbody{padding:12px!important}.hero{padding:12px!important}.hero h3{font-size:17px!important}.sources{grid-template-columns:1fr!important}.rx-tabs{padding:8px!important;gap:5px!important;overflow-x:auto!important;white-space:nowrap!important}.rx-tab{flex:0 0 auto!important;padding:8px 10px!important}
 .rx-alert-drawer{width:100%!important}.rx-alert-summary{padding:9px!important}.rx-alert-body{padding:10px 10px 92px!important}
 .rx-smart-results,.rx-city-results{top:55px!important;left:7px!important;right:7px!important;width:auto!important;max-height:55dvh!important;overflow:auto!important}
 .toast{left:9px!important;right:9px!important;bottom:76px!important;transform:none!important;text-align:center!important}
 }
</style>
<script>
(function(){
 const mobile=()=>matchMedia('(max-width:720px)').matches;
 function compactState(){if(!mobile())return;const el=document.querySelector('#rxMapState');if(!el)return;const t=(el.textContent||'').trim();if(/carregado|clique em um polígono|escolha o município/i.test(t)){setTimeout(()=>{if(el&&(el.textContent||'')===t)el.style.display='none'},1800)}else el.style.display='block'}
 function watch(){const main=document.querySelector('.main');if(!main){setTimeout(watch,250);return}const obs=new MutationObserver(compactState);obs.observe(main,{childList:true,subtree:true,characterData:true});compactState();try{if(typeof map!=='undefined'&&map){setTimeout(()=>map.invalidateSize(),120);window.addEventListener('orientationchange',()=>setTimeout(()=>map.invalidateSize(),300));window.addEventListener('resize',()=>setTimeout(()=>map.invalidateSize(),120))}}catch(e){}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',watch);else watch();
})();
</script>
'''

if 'RX_MOBILE_V19' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',MOBILE_UI+'<!-- RX_MOBILE_V19 --></body>')

print('RX_MOBILE_V19=uf_fallback_clean_mobile_map',flush=True)

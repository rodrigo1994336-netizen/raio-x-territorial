from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field
from shapely.geometry import shape

import portal_v8
import portal_api as base

app=portal_v8.app
WORKER=os.getenv('RX_REPORT_WORKER_URL','https://raio-x-territorial-report.onrender.com').rstrip('/')
UFS=set(base.STATE_TO_UF.values())
UF_TO_STATE={v:k.title() for k,v in base.STATE_TO_UF.items()}


def _norm(v:Any)->str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(v or '')) if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',s).strip().lower()


def _area(v):
    try:return float(str(v).replace(',','.'))
    except Exception:return None


async def _city_bbox(municipality:str,uf:str):
    state=UF_TO_STATE.get(uf,uf)
    params={'q':f'{municipality}, {state}, Brasil','format':'jsonv2','countrycodes':'br','addressdetails':'1','limit':'8','accept-language':'pt-BR'}
    headers={'User-Agent':'Raio-X-Territorial/0.39-advanced-search'}
    try:
        async with httpx.AsyncClient(timeout=16,follow_redirects=True,headers=headers) as c:
            r=await c.get('https://nominatim.openstreetmap.org/search',params=params);r.raise_for_status();rows=r.json()
    except Exception as e:
        raise HTTPException(status_code=502,detail=f'Não foi possível localizar o município: {type(e).__name__}')
    wanted=_norm(municipality)
    for x in rows:
        a=x.get('address') or {}
        iso=a.get('ISO3166-2-lvl4') or a.get('ISO3166-2-lvl6') or ''
        found_uf=iso[-2:].upper() if isinstance(iso,str) and iso.upper().startswith('BR-') else base.STATE_TO_UF.get(_norm(a.get('state')))
        name=a.get('city') or a.get('town') or a.get('municipality') or a.get('village') or x.get('name')
        if found_uf==uf and name and (wanted in _norm(name) or _norm(name) in wanted):
            bb=x.get('boundingbox') or []
            if len(bb)==4:
                south,north,west,east=map(float,bb)
                return {'south':south,'north':north,'west':west,'east':east,'name':name,'state':a.get('state'),'lat':float(x['lat']),'lon':float(x['lon'])}
    raise HTTPException(status_code=404,detail='Município não localizado na UF selecionada.')


@app.get('/v1/live/search/advanced')
async def advanced_property_search(uf:str,municipality:str,q:str|None=None,min_area_ha:float|None=None,max_area_ha:float|None=None,limit:int=40):
    uf=(uf or '').upper().strip();municipality=(municipality or '').strip();term=_norm(q)
    if uf not in UFS:raise HTTPException(status_code=422,detail='Selecione uma UF válida.')
    if len(municipality)<2:raise HTTPException(status_code=422,detail='Informe o município.')
    if min_area_ha is not None and min_area_ha<0:raise HTTPException(status_code=422,detail='Área mínima inválida.')
    if max_area_ha is not None and max_area_ha<0:raise HTTPException(status_code=422,detail='Área máxima inválida.')
    if min_area_ha is not None and max_area_ha is not None and min_area_ha>max_area_ha:raise HTTPException(status_code=422,detail='A área mínima não pode ser maior que a máxima.')
    cap=max(1,min(int(limit),60));bbox=await _city_bbox(municipality,uf)
    type_name=f"sicar:sicar_imoveis_{'DF' if uf=='DF' else uf.lower()}"
    params={'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':type_name,'outputFormat':'application/json','srsName':'EPSG:4674','bbox':f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']},EPSG:4674",'maxFeatures':str(max(80,min(180,cap*3)))}
    raw=bytearray();byte_cap=10_000_000
    try:
        async with httpx.AsyncClient(timeout=32,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/0.39-advanced-search'}) as c:
            async with c.stream('GET',base.SICAR,params=params) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>byte_cap:raise HTTPException(status_code=413,detail='Município muito amplo para esta busca. Use área ou termo para refinar.')
        data=json.loads(raw)
    except HTTPException:raise
    except Exception as e:raise HTTPException(status_code=502,detail=f'SICAR temporariamente indisponível: {type(e).__name__}')
    wanted_city=_norm(municipality);items=[];features=data.get('features') or []
    for f in features:
        p=f.get('properties') or {};city=p.get('municipio') or ''
        if wanted_city and wanted_city not in _norm(city) and _norm(city) not in wanted_city:continue
        area=_area(p.get('area'))
        if min_area_ha is not None and (area is None or area<min_area_ha):continue
        if max_area_ha is not None and (area is None or area>max_area_ha):continue
        if term:
            hay=' | '.join(str(p.get(k) or '') for k in ('cod_imovel','municipio','uf','status_imovel','condicao','tipo_imovel'))
            if term not in _norm(hay):continue
        geom=f.get('geometry');center=None
        try:
            c=shape(geom).centroid;center={'lat':float(c.y),'lon':float(c.x)}
        except Exception:pass
        code=p.get('cod_imovel')
        if not code:continue
        items.append({'type':'car','name':p.get('nome_imovel') or p.get('denominacao') or f"Imóvel rural · {city}/{uf}",'car_code':code,'municipality':city,'uf':p.get('uf') or uf,'area_ha':area,'status':p.get('status_imovel'),'condition':p.get('condicao'),'property_type':p.get('tipo_imovel'),'fiscal_modules':p.get('m_fiscal'),'center':center,'geometry':geom,'source':'SICAR/WFS público'})
        if len(items)>=cap:break
    return {'ok':True,'items':items,'count':len(items),'uf':uf,'municipality':bbox.get('name') or municipality,'city_center':{'lat':bbox['lat'],'lon':bbox['lon']},'source':'SICAR/WFS público','truncated':len(features)>=int(params['maxFeatures']) or len(items)>=cap,'note':'Busca geográfica nacional no SICAR. O perfil produtivo é refinado separadamente por uso/cobertura do solo dentro de cada imóvel.'}


class ProfileCandidate(BaseModel):
    car_code:str
    geometry:dict[str,Any]


class ProfileBatch(BaseModel):
    candidates:list[ProfileCandidate]=Field(default_factory=list,max_length=16)


@app.post('/v1/live/search/landuse-profiles')
async def advanced_landuse_profiles(payload:ProfileBatch):
    body={'candidates':[x.model_dump() for x in payload.candidates[:16]]}
    try:
        async with httpx.AsyncClient(timeout=48,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial-Portal/0.39'}) as c:
            r=await c.post(WORKER+'/v1/heavy/landuse-profiles',json=body)
            try:data=r.json()
            except Exception:data={'detail':r.text[:260]}
        if r.status_code>=400:raise HTTPException(status_code=r.status_code,detail=data.get('detail') or 'Triagem produtiva indisponível.')
        return data
    except HTTPException:raise
    except Exception as e:raise HTTPException(status_code=502,detail=f'Triagem produtiva temporariamente indisponível: {type(e).__name__}')


UI=r'''
<style>
.rx-adv-trigger{height:40px;display:flex;align-items:center;gap:7px;border:1px solid #28483a;background:#0a1b14;color:#dff8eb;border-radius:12px;padding:0 12px;font-size:10px;font-weight:850;cursor:pointer;white-space:nowrap}.rx-adv-trigger svg{width:15px;height:15px}.rx-adv-backdrop{position:fixed;z-index:3900;inset:0;background:rgba(0,0,0,.58);backdrop-filter:blur(4px);display:grid;place-items:center;padding:18px}.rx-adv{width:min(900px,96vw);max-height:min(820px,92vh);display:flex;flex-direction:column;background:#07150f;border:1px solid #315343;border-radius:22px;box-shadow:0 35px 110px #000d;overflow:hidden}.rx-adv-head{display:flex;align-items:flex-start;gap:12px;padding:18px 20px;border-bottom:1px solid #203b30}.rx-adv-head h2{font-size:18px;margin:2px 0 4px}.rx-adv-head p{font-size:10px;color:#91aa9e;margin:0;line-height:1.45}.rx-adv-close{margin-left:auto;width:38px;height:38px;border:1px solid #29483b;background:#0d2118;color:#edf9f3;border-radius:11px;font-size:20px;cursor:pointer}.rx-adv-body{overflow:auto;padding:18px 20px 22px}.rx-adv-grid{display:grid;grid-template-columns:1.1fr .8fr 1fr;gap:10px}.rx-adv-field{min-width:0}.rx-adv-field label{display:block;font-size:8px;letter-spacing:.8px;text-transform:uppercase;color:#83a293;font-weight:900;margin:0 0 5px}.rx-adv-field input,.rx-adv-field select{width:100%;height:42px;border:1px solid #29483b;background:#0b1d15;color:#edf9f3;border-radius:11px;padding:0 11px;outline:none}.rx-adv-area{display:grid;grid-template-columns:1fr 1fr;gap:8px}.rx-adv-profile-title{margin:16px 0 8px;font-size:9px;letter-spacing:1px;color:#88aa99;font-weight:900;text-transform:uppercase}.rx-adv-chips{display:flex;flex-wrap:wrap;gap:7px}.rx-adv-chip{border:1px solid #29483b;background:#0a1b14;color:#bdd0c6;border-radius:999px;padding:8px 10px;font-size:9px;font-weight:800;cursor:pointer}.rx-adv-chip[data-active="1"]{background:#62e4a3;color:#052116;border-color:#62e4a3}.rx-adv-hint{margin-top:9px;color:#80998d;font-size:9px;line-height:1.45}.rx-adv-actions{display:flex;gap:8px;margin-top:15px}.rx-adv-search{flex:1;height:44px;border:0;border-radius:12px;background:#62e4a3;color:#052116;font-weight:950;cursor:pointer}.rx-adv-clear{height:44px;border:1px solid #29483b;border-radius:12px;background:#0b1d15;color:#d8ebe1;padding:0 14px;font-weight:850;cursor:pointer}.rx-adv-status{margin-top:16px;border-top:1px solid #203b30;padding-top:14px;color:#9bb1a6;font-size:10px}.rx-adv-progress{height:3px;border-radius:99px;background:#173126;overflow:hidden;margin-top:8px}.rx-adv-progress:after{content:"";display:block;width:38%;height:100%;background:#62e4a3;animation:rxAdv 1.1s ease-in-out infinite}@keyframes rxAdv{0%{transform:translateX(-110%)}100%{transform:translateX(310%)}}.rx-adv-results{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:12px}.rx-adv-card{border:1px solid #29483b;background:#0a1b14;border-radius:14px;padding:12px;text-align:left;color:#edf9f3;cursor:pointer;min-width:0}.rx-adv-card:hover{background:#10251b}.rx-adv-card b{display:block;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rx-adv-card small{display:block;color:#91aa9e;font-size:8px;margin-top:4px;overflow-wrap:anywhere}.rx-adv-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.rx-adv-badge{border-radius:999px;background:#143626;color:#75e8ad;padding:4px 6px;font-size:7px;font-weight:900}.rx-adv-source{margin-top:12px;color:#6f897c;font-size:8px;line-height:1.45}
@media(max-width:900px){.rx-adv-trigger span{display:none}.rx-adv-trigger{width:40px;padding:0;justify-content:center}.rx-adv-grid{grid-template-columns:1fr 1fr}.rx-adv-grid .rx-adv-wide{grid-column:1/-1}}
@media(max-width:720px){.rx-adv-backdrop{padding:0;place-items:end center;background:#0007}.rx-adv{width:100%;max-height:calc(100dvh - 8px);border-radius:22px 22px 0 0}.rx-adv-head{padding:15px 15px 12px}.rx-adv-body{padding:14px 15px 22px}.rx-adv-grid{grid-template-columns:1fr}.rx-adv-grid .rx-adv-wide{grid-column:auto}.rx-adv-results{grid-template-columns:1fr}.rx-adv-chips{flex-wrap:nowrap;overflow-x:auto;padding-bottom:4px}.rx-adv-chip{flex:0 0 auto}.rx-adv-trigger{height:38px;width:38px;border-radius:11px}}
</style>
<script>
(function(){
const UFS=['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'];
const PROFILES=[['','Todos'],['livestock_pasture','Pecuária / gado'],['agriculture','Agricultura'],['soy','Soja'],['sugarcane','Cana'],['coffee','Café'],['citrus','Citros'],['cotton','Algodão'],['rice','Arroz'],['silviculture','Silvicultura'],['aquaculture','Aquicultura'],['mixed_agro','Misto agropecuário'],['native_dominant','Vegetação nativa'],['mining_landcover','Área minerada']];
let selected='',host=null,lastItems=[];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function close(){host?.remove();host=null;document.documentElement.classList.remove('rx-adv-open')}
function trigger(){if(document.querySelector('#rxAdvTrigger'))return;const search=document.querySelector('.search');if(!search)return setTimeout(trigger,250);const b=document.createElement('button');b.id='rxAdvTrigger';b.className='rx-adv-trigger';b.type='button';b.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h10M18 7h2M10 17h10M4 17h2M14 4v6M10 14v6"/></svg><span>Busca avançada</span>';b.onclick=open;search.insertAdjacentElement('afterend',b)}
function open(){if(host)return;host=document.createElement('div');host.className='rx-adv-backdrop';host.innerHTML=`<section class="rx-adv" role="dialog" aria-modal="true" aria-label="Busca avançada de imóveis"><header class="rx-adv-head"><div><div class="eyebrow">BUSCA TERRITORIAL AVANÇADA</div><h2>Encontre o imóvel pelo perfil que interessa</h2><p>Pesquise por estado, município, área e uso produtivo. A classificação produtiva usa evidência espacial do próprio imóvel.</p></div><button class="rx-adv-close" type="button" aria-label="Fechar">×</button></header><div class="rx-adv-body"><div class="rx-adv-grid"><div class="rx-adv-field rx-adv-wide"><label>Nome, CAR ou identificador (opcional)</label><input id="rxAdvQ" placeholder="Ex.: Fazenda Boa Vista ou código CAR"></div><div class="rx-adv-field"><label>Estado</label><select id="rxAdvUf"><option value="">Selecione a UF</option>${UFS.map(x=>`<option>${x}</option>`).join('')}</select></div><div class="rx-adv-field"><label>Município</label><input id="rxAdvCity" placeholder="Ex.: Curvelo"></div><div class="rx-adv-field"><label>Área do imóvel (ha)</label><div class="rx-adv-area"><input id="rxAdvMin" inputmode="decimal" placeholder="Mín."><input id="rxAdvMax" inputmode="decimal" placeholder="Máx."></div></div></div><div class="rx-adv-profile-title">Perfil do imóvel</div><div class="rx-adv-chips">${PROFILES.map(([k,v])=>`<button type="button" class="rx-adv-chip" data-profile="${k}" data-active="${k===''?'1':'0'}">${v}</button>`).join('')}</div><div class="rx-adv-hint">“Pecuária / gado” significa imóvel com sinal espacial de pastagem compatível com pecuária. Não afirmamos existência de rebanho sem fonte específica. O mesmo rigor vale para os demais perfis.</div><div class="rx-adv-actions"><button id="rxAdvSearch" class="rx-adv-search" type="button">BUSCAR IMÓVEIS</button><button id="rxAdvClear" class="rx-adv-clear" type="button">LIMPAR</button></div><div id="rxAdvStatus" class="rx-adv-status">Escolha a UF e o município. O sistema consulta imóveis reais e, se você selecionar um perfil, refina pela cobertura do solo dentro de cada área.</div><div id="rxAdvResults" class="rx-adv-results"></div><div class="rx-adv-source">Fontes: SICAR/WFS para imóveis e MapBiomas Coleção 11 para perfil de uso/cobertura. Dados regionais podem complementar a análise do imóvel depois, mas nunca são usados para inventar atividade na fazenda.</div></div></section>`;document.body.appendChild(host);document.documentElement.classList.add('rx-adv-open');host.querySelector('.rx-adv-close').onclick=close;host.addEventListener('click',e=>{if(e.target===host)close()});host.querySelectorAll('.rx-adv-chip').forEach(b=>b.onclick=()=>{selected=b.dataset.profile||'';host.querySelectorAll('.rx-adv-chip').forEach(x=>x.dataset.active=x===b?'1':'0')});host.querySelector('#rxAdvSearch').onclick=search;host.querySelector('#rxAdvClear').onclick=()=>{selected='';host.querySelectorAll('input').forEach(x=>x.value='');host.querySelector('#rxAdvUf').value='';host.querySelectorAll('.rx-adv-chip').forEach(x=>x.dataset.active=x.dataset.profile===''?'1':'0');host.querySelector('#rxAdvResults').innerHTML='';status('Filtros limpos.')};setTimeout(()=>host.querySelector('#rxAdvUf')?.focus(),80)}
function status(t,loading=false){const el=host?.querySelector('#rxAdvStatus');if(!el)return;el.innerHTML=esc(t)+(loading?'<div class="rx-adv-progress"></div>':'')}
function num(id){const v=(host.querySelector(id)?.value||'').trim().replace(',','.');return v===''?null:Number(v)}
function mapOpen(x){close();try{if(typeof map!=='undefined'&&x.geometry){const l=L.geoJSON({type:'Feature',geometry:x.geometry,properties:{}},{style:{color:'#62e4a3',weight:3,fillColor:'#62e4a3',fillOpacity:.10}}).addTo(map);const b=l.getBounds();if(b.isValid())map.fitBounds(b.pad(.16),{maxZoom:15});setTimeout(()=>{try{map.removeLayer(l)}catch(e){}},3500)}else if(typeof map!=='undefined'&&x.center)map.setView([x.center.lat,x.center.lon],14);if(typeof showProperty==='function')showProperty({car_code:x.car_code,name:x.name,municipality:x.municipality,uf:x.uf,area_ha:x.area_ha,status:x.status,condition:x.condition,type:x.property_type,fiscal_modules:x.fiscal_modules},x.geometry)}catch(e){if(typeof toast==='function')toast('Não foi possível abrir este imóvel agora.')}}
function render(items,profileMap={}){const box=host.querySelector('#rxAdvResults');box.innerHTML='';if(!items.length){box.innerHTML='<div class="rx-adv-card"><b>Nenhum imóvel encontrado</b><small>Altere cidade, área ou perfil produtivo.</small></div>';return}items.forEach(x=>{const p=profileMap[x.car_code]?.profile||null;const badges=p?.profiles?.slice(0,3)||[];const b=document.createElement('button');b.type='button';b.className='rx-adv-card';b.innerHTML=`<b>${esc(x.name||'Imóvel rural')}</b><small>${esc(x.municipality||'')} / ${esc(x.uf||'')} · ${x.area_ha!=null?esc(Number(x.area_ha).toLocaleString('pt-BR',{maximumFractionDigits:2}))+' ha':'área não informada'}</small><small>CAR ${esc(x.car_code||'—')}</small>${badges.length?`<div class="rx-adv-badges">${badges.map(y=>`<span class="rx-adv-badge">${esc(y.label)} · ${esc(y.share_pct)}%</span>`).join('')}</div>`:''}`;b.onclick=()=>mapOpen(x);box.appendChild(b)})}
async function search(){const uf=host.querySelector('#rxAdvUf').value,city=host.querySelector('#rxAdvCity').value.trim(),q=host.querySelector('#rxAdvQ').value.trim(),min=num('#rxAdvMin'),max=num('#rxAdvMax');if(!uf||city.length<2){status('Selecione a UF e informe o município.');return}if((min!=null&&!Number.isFinite(min))||(max!=null&&!Number.isFinite(max))){status('Revise os valores de área.');return}status('Localizando imóveis reais no município…',true);host.querySelector('#rxAdvResults').innerHTML='';const u=new URL('/v1/live/search/advanced',location.origin);u.searchParams.set('uf',uf);u.searchParams.set('municipality',city);u.searchParams.set('limit',selected?'16':'40');if(q)u.searchParams.set('q',q);if(min!=null)u.searchParams.set('min_area_ha',min);if(max!=null)u.searchParams.set('max_area_ha',max);try{const r=await fetch(u,{cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Busca indisponível');lastItems=d.items||[];if(!selected){status(`${lastItems.length} imóvel(is) localizado(s) em ${d.municipality}/${d.uf}${d.truncated?' · refine os filtros para reduzir a lista':''}.`);render(lastItems);return}if(!lastItems.length){status('Nenhum imóvel candidato encontrado com esses filtros.');render([]);return}status(`Triagem produtiva em ${lastItems.length} imóvel(is)… os dados do mapa continuam disponíveis.`,true);const rr=await fetch('/v1/live/search/landuse-profiles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidates:lastItems.map(x=>({car_code:x.car_code,geometry:x.geometry}))})}),pd=await rr.json();if(!rr.ok)throw new Error(pd.detail||'Triagem produtiva indisponível');const by={};(pd.items||[]).forEach(x=>by[x.car_code]=x);const matched=lastItems.filter(x=>(by[x.car_code]?.profile?.profile_codes||[]).includes(selected));status(`${matched.length} imóvel(is) compatível(is) com o perfil selecionado em ${d.municipality}/${d.uf}. Classificação por uso/cobertura do solo; não é declaração de atividade econômica.`);render(matched,by)}catch(e){status(e.message||'Busca temporariamente indisponível.')}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',trigger);else trigger();
})();
</script>
'''

if 'RX_ADVANCED_SEARCH_V39' not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML=portal_v8.PORTAL_HTML.replace('</body>',UI+'<!-- RX_ADVANCED_SEARCH_V39 --></body>')

print('RX_ADVANCED_SEARCH_V39=nationwide_city_area_productive_profile',flush=True)

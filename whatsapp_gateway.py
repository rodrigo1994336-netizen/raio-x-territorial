from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Callable, Awaitable
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse

CAR_RE=re.compile(r'\b([A-Z]{2}-\d{7}-[A-F0-9]{32})\b',re.I)
COORD_RE=re.compile(r'(?<!\d)(-?\d{1,2}(?:[\.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[\.,]\d+)?)(?!\d)')
URL_RE=re.compile(r'https?://\S+',re.I)
_SESSION:dict[str,dict[str,Any]]={}
SESSION_TTL=6*3600

MENU=(
    '🌾 *RAIO-X TERRITORIAL*\n'
    'Seu assistente rural no WhatsApp.\n\n'
    '🗺️ *Dados da fazenda* — envie um código CAR, coordenada ou link do Google Maps\n'
    '📄 *KML* — arquivo para abrir no Google Earth\n'
    '🔍 *Monitorar* — acompanhar embargos, PRODES, fogo e outras mudanças\n'
    '🌧️ *Chuva* — precipitação dos últimos 30 dias\n'
    '🌱 *Solo* — classe pedológica e composição físico-química quando disponível\n'
    '🔥 *Fogo* — focos recentes dentro/próximo do imóvel\n'
    '⛏️ *Mineração* — ANM, minerais críticos e terras raras\n'
    '📑 *Relatório* — Raio-X completo em PDF\n\n'
    'Envie o CAR/localização primeiro. Depois você pode mandar só: KML, chuva, solo, monitorar, fogo, mineração ou relatório.'
)


def _enabled() -> bool:
    return os.getenv('RX_WHATSAPP_ENABLED','off').strip().lower() in {'1','true','yes','on'}


def _config():
    return {
        'verify_token':os.getenv('WHATSAPP_VERIFY_TOKEN',''),
        'access_token':os.getenv('WHATSAPP_ACCESS_TOKEN',''),
        'phone_number_id':os.getenv('WHATSAPP_PHONE_NUMBER_ID',''),
        'api_version':os.getenv('WHATSAPP_GRAPH_VERSION','v24.0'),
        'public_base_url':os.getenv('RX_PUBLIC_BASE_URL','https://raio-x-territorial-app.onrender.com').rstrip('/'),
    }


def _session_get(phone:str):
    now=time.monotonic(); s=_SESSION.get(phone)
    if s and now-s.get('ts',0)<SESSION_TTL:return s
    if phone in _SESSION:_SESSION.pop(phone,None)
    return {}


def _session_set(phone:str,**values):
    s=_session_get(phone).copy();s.update(values);s['ts']=time.monotonic();_SESSION[phone]=s;return s


def _intent(text:str):
    t=(text or '').strip().lower()
    if not t or t in {'menu','ajuda','help','oi','olá','ola','bom dia','boa tarde','boa noite'}:return 'menu'
    if any(x in t for x in ('monitorar','monitoramento','embargo')):return 'monitor'
    if any(x in t for x in ('kml','google earth')):return 'kml'
    if any(x in t for x in ('chuva','precipit','clima')):return 'rain'
    if any(x in t for x in ('solo','argila','areia','silte','ph','ctc','nitrog')):return 'soil'
    if any(x in t for x in ('fogo','incênd','incend','queimada')):return 'fire'
    if any(x in t for x in ('minera','anm','terra rara','terras raras','mineral')):return 'mining'
    if any(x in t for x in ('relatório','relatorio','pdf','raio-x','raio x')):return 'report'
    return 'property'


async def _send_text(to: str, body: str) -> dict[str,Any]:
    cfg=_config()
    if not (_enabled() and cfg['access_token'] and cfg['phone_number_id']):return {'ok':False,'disabled':True}
    url=f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}/messages"
    headers={'Authorization':f"Bearer {cfg['access_token']}",'Content-Type':'application/json'}
    payload={'messaging_product':'whatsapp','recipient_type':'individual','to':to,'type':'text','text':{'preview_url':True,'body':body[:4096]}}
    async with httpx.AsyncClient(timeout=25,follow_redirects=True) as c:r=await c.post(url,headers=headers,json=payload)
    try:data=r.json()
    except Exception:data={'text':r.text[:500]}
    return {'ok':r.is_success,'status':r.status_code,'response':data}


def _extract_messages(payload: dict[str,Any]):
    out=[]
    for entry in payload.get('entry') or []:
        for change in entry.get('changes') or []:
            value=change.get('value') or {}
            for m in value.get('messages') or []:
                text=((m.get('text') or {}).get('body') or '').strip()
                loc=m.get('location') or {}
                if text or (loc.get('latitude') is not None and loc.get('longitude') is not None):
                    out.append({'from':m.get('from'),'id':m.get('id'),'text':text,'location':loc,'timestamp':m.get('timestamp')})
    return out


def _coords_from_text(text:str):
    m=COORD_RE.search((text or '').replace('−','-'))
    if not m:return None
    try:
        lat=float(m.group(1).replace(',','.'));lon=float(m.group(2).replace(',','.'))
        if -90<=lat<=90 and -180<=lon<=180:return lat,lon
    except Exception:pass
    return None


async def _coords_from_maps_url(text:str):
    m=URL_RE.search(text or '')
    if not m:return None
    url=m.group(0).rstrip('.,)\]')
    # Resolve short Google Maps links. We only extract coordinates from the final URL;
    # no page scraping or private data is performed.
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/WhatsApp'}) as c:
            r=await c.get(url)
        final=unquote(str(r.url))
    except Exception:
        final=unquote(url)
    for pattern in (
        re.compile(r'@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)'),
        re.compile(r'[?&](?:q|query|ll)=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)'),
        re.compile(r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)'),
    ):
        mm=pattern.search(final)
        if mm:
            lat=float(mm.group(1));lon=float(mm.group(2))
            if -90<=lat<=90 and -180<=lon<=180:return lat,lon
    return _coords_from_text(final)


def _car_from_result(result):
    c=result.get('car') or {};p=c.get('properties') or {}
    return str(p.get('cod_imovel') or p.get('car_code') or '').upper() or None


def _result_header(result,car):
    c=result.get('car') or {};p=c.get('properties') or {}
    return [
        '🌾 *RAIO-X TERRITORIAL*',
        f"📍 {p.get('municipio') or '-'} / {p.get('uf') or '-'}",
        f"CAR: {car}",
        f"Área: {p.get('area') or '-'} ha",
    ]


def register_routes(app: FastAPI, analyze_fn: Callable[..., Awaitable[dict[str,Any]]], resolve_point_fn: Callable[[float,float],Awaitable[dict[str,Any]]]|None=None):
    @app.get('/webhooks/whatsapp', response_class=PlainTextResponse)
    async def whatsapp_verify(hub_mode: str|None=Query(None,alias='hub.mode'),hub_challenge: str|None=Query(None,alias='hub.challenge'),hub_verify_token: str|None=Query(None,alias='hub.verify_token')):
        cfg=_config()
        if not _enabled():raise HTTPException(status_code=503,detail='WhatsApp integration is OFF')
        if hub_mode=='subscribe' and cfg['verify_token'] and hub_verify_token==cfg['verify_token']:return PlainTextResponse(hub_challenge or '')
        raise HTTPException(status_code=403,detail='Webhook verification failed')

    async def resolve_input(to:str,msg:dict):
        text=msg.get('text') or ''
        m=CAR_RE.search(text)
        if m:
            car=m.group(1).upper();_session_set(to,car=car);return car
        loc=msg.get('location') or {}
        coords=None
        if loc.get('latitude') is not None and loc.get('longitude') is not None:
            coords=(float(loc['latitude']),float(loc['longitude']))
        if coords is None:coords=_coords_from_text(text)
        if coords is None and URL_RE.search(text):coords=await _coords_from_maps_url(text)
        if coords and resolve_point_fn:
            try:
                resolved=await resolve_point_fn(coords[0],coords[1]);car=((resolved.get('property') or {}).get('car_code') or '').upper()
                if car:_session_set(to,car=car,lat=coords[0],lon=coords[1]);return car
            except Exception:return None
        return (_session_get(to).get('car') or '').upper() or None

    async def respond(to:str,msg:dict):
        text=msg.get('text') or ''; intent=_intent(text)
        if intent=='menu':return await _send_text(to,MENU)
        explicit_car=CAR_RE.search(text)
        car=await resolve_input(to,msg)
        if not car:
            return await _send_text(to,'🗺️ Envie o código CAR, compartilhe sua localização do WhatsApp, mande coordenadas ou cole um link do Google Maps. Depois escolha KML, monitorar, chuva, solo, fogo, mineração ou relatório.')

        # Acknowledge new property input immediately. The detailed analysis can take longer.
        if explicit_car or msg.get('location') or URL_RE.search(text) or _coords_from_text(text):
            await _send_text(to,f'✅ Imóvel recebido: *{car}*\nEstou consultando as fontes. Você pode mandar: *KML*, *monitorar*, *chuva*, *solo*, *fogo*, *mineração* ou *relatório*.')
            if intent=='property':intent='summary'

        base=_config()['public_base_url']
        if intent=='kml':
            return await _send_text(to,f'📄 *KML do imóvel*\n{base}/v1/exports/property/{car}/kml\n\nAbra o arquivo no Google Earth. O limite é obtido do CAR consultado.')
        if intent=='report':
            return await _send_text(to,f'📑 *Relatório completo do Raio-X Territorial*\n{base}/v1/reports/property/{car}\n\nO PDF é gerado com as fontes que responderem nesta emissão; fonte indisponível aparece explicitamente.')
        if intent=='monitor':
            try:
                import monitoring_store as store
                if not store.readiness().get('ready'):
                    return await _send_text(to,'🔍 O motor de monitoramento já está implementado, mas a persistência do banco ainda não está vinculada. Assim que o DATABASE_URL estiver ligado, este mesmo comando ativa o acompanhamento contínuo por WhatsApp.')
                mon=await asyncio.to_thread(store.add_monitor,car,'whatsapp',to)
                try:
                    result=await analyze_fn(car)
                    await asyncio.to_thread(store.save_snapshot,mon['id'],store.compact_snapshot(result))
                except Exception:pass
                return await _send_text(to,f'🔍 *Monitoramento ativado* para {car}.\nVou acompanhar mudanças nas fontes configuradas e enviar alerta aqui quando houver alteração relevante.')
            except Exception as e:
                return await _send_text(to,f'🔍 Não consegui ativar o monitoramento agora ({type(e).__name__}). O imóvel continua salvo nesta conversa.')

        try:
            result=await analyze_fn(car)
            _session_set(to,car=car)
            lines=_result_header(result,car)
            if intent in {'summary','property'}:
                emb=((result.get('embargos_ibama') or {}).get('exact') or {}).get('occurrence_count')
                anm=((result.get('anm') or {}).get('exact') or {}).get('occurrence_count')
                pro=((result.get('prodes') or {}).get('exact') or {}).get('occurrence_count')
                fire=result.get('fire_live') or {}
                lines += [
                    f"Embargos IBAMA: {emb if emb is not None else 'fonte indisponível'}",
                    f"PRODES histórico: {pro if pro is not None else 'fonte indisponível'} ocorrência(s)",
                    f"Processos ANM: {anm if anm is not None else 'fonte indisponível'}",
                    f"Fogo recente dentro: {fire.get('inside_count') if fire.get('ok') else 'fonte indisponível'}",
                    '', 'Digite *relatório* para o PDF completo ou *menu* para ver todas as funções.'
                ]
            elif intent=='rain':
                cl=result.get('climate_nasa') or {}
                lines += ['🌧️ *Precipitação e clima*']
                if cl.get('ok'):
                    lines += [f"Chuva acumulada: {cl.get('rain_sum_mm')} mm",f"Período: {cl.get('period_start')} a {cl.get('period_end')}",f"Temperatura média: {cl.get('temp_avg_c')} °C"]
                else:lines += ['Fonte climática indisponível nesta consulta. Isso não é interpretado como chuva zero.']
            elif intent=='soil':
                ide=result.get('ide_layers') or {};soil=ide.get('soil') or {};chem=result.get('soil_composition') or {}
                lines += ['🌱 *Solo*']
                if soil.get('ok'):
                    labels=[]
                    for s in soil.get('samples') or []:
                        p=s.get('properties') or {};v=p.get('legenda') or p.get('classe') or p.get('nome')
                        if v and v not in labels:labels.append(str(v))
                    lines += [f"Classe pedológica: {'; '.join(labels) if labels else str(soil.get('exact_count',0))+' interseção(ões)'}"]
                else:lines += ['Mapa pedológico: fonte indisponível/parcial nesta consulta.']
                if chem.get('ok'):
                    vals=chem.get('values') or {};lines += [f"Argila: {vals.get('clay_pct','-')}% | Areia: {vals.get('sand_pct','-')}% | Silte: {vals.get('silt_pct','-')}%",f"pH: {vals.get('ph_h2o','-')} | CTC: {vals.get('cec_cmolckg','-')} | C orgânico: {vals.get('soc_gkg','-')} g/kg | N: {vals.get('nitrogen_gkg','-')} g/kg"]
                else:lines += ['Composição físico-química estimada ainda não respondeu nesta emissão.']
                lines += ['Estimativas regionais não substituem análise laboratorial do solo.']
            elif intent=='fire':
                fire=result.get('fire_live') or {};lines += ['🔥 *Fogo e queimadas*']
                if fire.get('ok'):lines += [f"Focos dentro: {fire.get('inside_count',0)}",f"Focos próximos: {fire.get('near_count',0)}",f"Janela: {fire.get('window_note') or '-'}"]
                else:lines += ['Programa Queimadas indisponível nesta consulta.']
            elif intent=='mining':
                m=result.get('critical_minerals') or {};a=m.get('anm') or {};s=m.get('sgb') or {};lines += ['⛏️ *Mineração, minerais críticos e terras raras*',f"Processos ANM: {a.get('process_count',0)}",f"Processos classificados como minerais críticos: {a.get('critical_process_count',0)}",f"Sinal de terras raras: {'SIM — TRIAGEM' if m.get('rare_earth_signal') else 'não identificado'}",f"Camadas SGB com sinal: {len(s.get('hit_layers') or [])}",'Triagem geológica/mineral não comprova jazida, recurso ou reserva.']
            await _send_text(to,'\n'.join(lines))
        except Exception:
            await _send_text(to,'Não consegui concluir esta consulta agora. Alguma fonte externa pode estar indisponível. Tente novamente; o sistema não transforma falha de fonte em resultado negativo.')

    @app.post('/webhooks/whatsapp')
    async def whatsapp_inbound(req: Request):
        payload=await req.json()
        if not _enabled():return {'ok':True,'enabled':False,'processed':0}
        msgs=_extract_messages(payload);processed=0
        for msg in msgs:
            to=msg.get('from')
            if not to:continue
            await respond(to,msg);processed+=1
        return {'ok':True,'enabled':True,'processed':processed}

    @app.get('/v1/whatsapp/status')
    def whatsapp_status():
        cfg=_config()
        return {
            'enabled':_enabled(),'configured':bool(cfg['verify_token'] and cfg['access_token'] and cfg['phone_number_id']),
            'mode':'official-meta-cloud-api','assistant_menu':True,'car':True,'maps_link':True,'shared_location':True,
            'kml':True,'monitoring':True,'rain_30d':True,'soil':True,'fire':True,'critical_minerals':True,'pdf_report':True,
            'conversation_last_property_ttl_hours':SESSION_TTL/3600,
            'cost_guardrail':'OFF by default; no outbound messages while disabled',
        }

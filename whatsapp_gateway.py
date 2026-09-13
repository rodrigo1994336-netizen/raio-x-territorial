from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Awaitable
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse

CAR_RE=re.compile(r'\b([A-Z]{2}-\d{7}-[A-F0-9]{32})\b',re.I)
COORD_RE=re.compile(r'(?<!\d)(-?\d{1,2}(?:[\.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[\.,]\d+)?)(?!\d)')
URL_RE=re.compile(r'https?://\S+',re.I)
_SESSION:dict[str,dict[str,Any]]={}
SESSION_TTL=6*3600
# Only Google Maps links are ever fetched: a link typed by anyone must not make the server call arbitrary hosts.
MAPS_HOSTS=frozenset({'maps.app.goo.gl','goo.gl','google.com','www.google.com','maps.google.com','google.com.br','www.google.com.br','maps.google.com.br'})
# Meta retries a webhook that is slow or fails; the same message must not be answered twice.
_SEEN_IDS:dict[str,float]={}
SEEN_TTL=24*3600
_RATE:dict[str,list[float]]={}
RATE_WINDOW_SECONDS=600
RATE_MAX_MESSAGES=20
_TASKS:set[asyncio.Task]=set()

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
        'app_secret':os.getenv('WHATSAPP_APP_SECRET',''),
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


def signature_valid(raw:bytes,header:str|None,secret:str)->bool:
    """Meta signs every webhook with the app secret (X-Hub-Signature-256)."""
    if not secret or not header or not header.startswith('sha256='):return False
    expected=hmac.new(secret.encode('utf-8'),raw or b'',hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,header[7:].strip().lower())


def first_time_message(message_id:str|None)->bool:
    now=time.monotonic()
    for k,ts in list(_SEEN_IDS.items()):
        if now-ts>SEEN_TTL:_SEEN_IDS.pop(k,None)
    if not message_id:return True
    if message_id in _SEEN_IDS:return False
    _SEEN_IDS[message_id]=now;return True


def rate_allowed(phone:str)->bool:
    now=time.monotonic();hits=[t for t in _RATE.get(phone,[]) if now-t<RATE_WINDOW_SECONDS]
    if len(hits)>=RATE_MAX_MESSAGES:
        _RATE[phone]=hits;return False
    hits.append(now);_RATE[phone]=hits;return True


def maps_url_allowed(url:str)->bool:
    try:
        parts=urlsplit(url);port=parts.port
    except Exception:return False
    return (parts.scheme=='https' and (parts.hostname or '').lower() in MAPS_HOSTS
            and not parts.username and not parts.password and port in (None,443))


def _format_ha(value:Any)->str:
    # Half-up on the value as written: 14.795 -> 14,80 (binary float rounding would print 14,79).
    try:text=f"{Decimal(str(float(value))).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP):,.2f}"
    except Exception:return ''
    return text.replace(',','X').replace('.',',').replace('X','.')+' ha'


def _session_set(phone:str,**values):
    if len(_SESSION)>5000:
        now=time.monotonic()
        for k,v in list(_SESSION.items()):
            if now-v.get('ts',0)>=SESSION_TTL:_SESSION.pop(k,None)
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


async def _send_template(to: str, template: str, language: str, params: list[str]) -> dict[str,Any]:
    """Business-initiated message (an alert) outside the 24 h window: Meta only delivers approved templates."""
    cfg=_config()
    if not (_enabled() and cfg['access_token'] and cfg['phone_number_id']):return {'ok':False,'disabled':True}
    url=f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}/messages"
    headers={'Authorization':f"Bearer {cfg['access_token']}",'Content-Type':'application/json'}
    payload={'messaging_product':'whatsapp','recipient_type':'individual','to':to,'type':'template',
             'template':{'name':template,'language':{'code':language},
                         'components':[{'type':'body','parameters':[{'type':'text','text':str(p)[:1000]} for p in params]}]}}
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as c:r=await c.post(url,headers=headers,json=payload)
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
    url=m.group(0).rstrip('.,)]')
    if not maps_url_allowed(url):return None
    # Resolve short Google Maps links hop by hop, never leaving Google Maps hosts.
    # We only extract coordinates from the final URL; no page scraping or private data.
    final=url
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=False,headers={'User-Agent':'Raio-X-Territorial/WhatsApp'}) as c:
            for _hop in range(5):
                r=await c.get(final)
                location=r.headers.get('location')
                if not (r.is_redirect and location):break
                nxt=str(httpx.URL(final).join(location))
                if not maps_url_allowed(nxt):break
                final=nxt
    except Exception:
        pass
    final=unquote(final)
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
        f"Área: {_format_ha(p.get('area')) or 'não informada'}",
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
            return await _send_text(to,f'📑 *Relatório completo do Raio-X Territorial*\n{base}/v1/reports/property/{car}\n\nO PDF é gerado com as fontes oficiais que responderem nesta emissão; a que não responder aparece como consulta pendente.')
        if intent=='monitor':
            try:
                import monitoring_store as store
                if not store.readiness().get('ready'):
                    return await _send_text(to,'🔍 O monitoramento pelo WhatsApp ainda não está disponível neste número. O imóvel continua salvo nesta conversa.')
                mon=await asyncio.to_thread(store.add_monitor,car,'whatsapp',to)
                try:
                    result=await analyze_fn(car)
                    await asyncio.to_thread(store.save_snapshot,mon['id'],store.compact_snapshot(result))
                except Exception:pass
                return await _send_text(to,f'🔍 *Monitoramento ativado* para {car}.\nVou acompanhar mudanças nas fontes configuradas e enviar alerta aqui quando houver alteração relevante.')
            except Exception:
                return await _send_text(to,'🔍 Não consegui ativar o monitoramento agora. Tente de novo em alguns minutos; o imóvel continua salvo nesta conversa.')

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
                    f"Embargos IBAMA: {emb if emb is not None else 'consulta pendente'}",
                    f"PRODES histórico: {pro if pro is not None else 'consulta pendente'} ocorrência(s)",
                    f"Processos ANM: {anm if anm is not None else 'consulta pendente'}",
                    f"Fogo recente dentro: {fire.get('inside_count') if fire.get('ok') else 'consulta pendente'}",
                    '', 'Digite *relatório* para o PDF completo ou *menu* para ver todas as funções.'
                ]
            elif intent=='rain':
                cl=result.get('climate_nasa') or {}
                lines += ['🌧️ *Precipitação e clima*']
                if cl.get('ok'):
                    lines += [f"Chuva acumulada: {cl.get('rain_sum_mm')} mm",f"Período: {cl.get('period_start')} a {cl.get('period_end')}",f"Temperatura média: {cl.get('temp_avg_c')} °C"]
                else:lines += ['Consulta de chuva pendente; tente de novo em alguns minutos. Isso não significa chuva zero.']
            elif intent=='soil':
                ide=result.get('ide_layers') or {};soil=ide.get('soil') or {};chem=result.get('soil_composition') or {}
                lines += ['🌱 *Solo*']
                if soil.get('ok'):
                    labels=[]
                    for s in soil.get('samples') or []:
                        p=s.get('properties') or {};v=p.get('legenda') or p.get('classe') or p.get('nome')
                        if v and v not in labels:labels.append(str(v))
                    lines += [f"Classe pedológica: {'; '.join(labels) if labels else str(soil.get('exact_count',0))+' interseção(ões)'}"]
                else:lines += ['Mapa de solos: consulta pendente.']
                if chem.get('ok'):
                    vals=chem.get('values') or {};lines += [f"Argila: {vals.get('clay_pct','-')}% | Areia: {vals.get('sand_pct','-')}% | Silte: {vals.get('silt_pct','-')}%",f"pH: {vals.get('ph_h2o','-')} | CTC: {vals.get('cec_cmolckg','-')} | C orgânico: {vals.get('soc_gkg','-')} g/kg | N: {vals.get('nitrogen_gkg','-')} g/kg"]
                else:lines += ['Composição físico-química estimada ainda não respondeu nesta emissão.']
                lines += ['Estimativas regionais não substituem análise laboratorial do solo.']
            elif intent=='fire':
                fire=result.get('fire_live') or {};lines += ['🔥 *Fogo e queimadas*']
                if fire.get('ok'):lines += [f"Focos dentro: {fire.get('inside_count',0)}",f"Focos próximos: {fire.get('near_count',0)}",f"Janela: {fire.get('window_note') or '-'}"]
                else:lines += ['Focos de calor: consulta pendente; tente de novo em alguns minutos.']
            elif intent=='mining':
                m=result.get('critical_minerals') or {};a=m.get('anm') or {};s=m.get('sgb') or {};lines += ['⛏️ *Mineração, minerais críticos e terras raras*',f"Processos ANM: {a.get('process_count',0)}",f"Processos classificados como minerais críticos: {a.get('critical_process_count',0)}",f"Sinal de terras raras: {'SIM — TRIAGEM' if m.get('rare_earth_signal') else 'não identificado'}",f"Camadas SGB com sinal: {len(s.get('hit_layers') or [])}",'Triagem geológica/mineral não comprova jazida, recurso ou reserva.']
            await _send_text(to,'\n'.join(lines))
        except Exception:
            await _send_text(to,'As fontes oficiais não responderam agora. Tente de novo em alguns minutos; o sistema não trata consulta sem resposta como resultado negativo.')

    async def respond_safely(to:str,msg:dict):
        try:await respond(to,msg)
        except Exception as exc:print(f'RX_WHATSAPP_RESPOND_FAILED={type(exc).__name__}',flush=True)

    @app.post('/webhooks/whatsapp')
    async def whatsapp_inbound(req: Request):
        if not _enabled():return {'ok':True,'enabled':False,'processed':0}
        cfg=_config()
        # Fail closed: without the app secret no webhook can be authenticated.
        if not cfg['app_secret']:raise HTTPException(status_code=503,detail='whatsapp_signature_not_configured')
        raw=await req.body()
        if not signature_valid(raw,req.headers.get('X-Hub-Signature-256'),cfg['app_secret']):raise HTTPException(status_code=403,detail='invalid_signature')
        try:payload=json.loads(raw or b'{}')
        except Exception:raise HTTPException(status_code=400,detail='invalid_json')
        accepted=0
        for msg in _extract_messages(payload):
            to=msg.get('from')
            if not to or not first_time_message(msg.get('id')) or not rate_allowed(to):continue
            # Answer Meta at once; the consultation runs after the acknowledgement.
            task=asyncio.create_task(respond_safely(to,msg));_TASKS.add(task);task.add_done_callback(_TASKS.discard);accepted+=1
        return {'ok':True,'enabled':True,'accepted':accepted}

    @app.get('/v1/whatsapp/status')
    def whatsapp_status():
        cfg=_config()
        return {
            'enabled':_enabled(),'configured':bool(cfg['verify_token'] and cfg['app_secret'] and cfg['access_token'] and cfg['phone_number_id']),
            'signature_verification':'x-hub-signature-256','deduplication':True,'rate_limit_per_phone':f'{RATE_MAX_MESSAGES}/{RATE_WINDOW_SECONDS}s',
            'mode':'official-meta-cloud-api','assistant_menu':True,'car':True,'maps_link':True,'shared_location':True,
            'kml':True,'monitoring':True,'rain_30d':True,'soil':True,'fire':True,'critical_minerals':True,'pdf_report':True,
            'conversation_last_property_ttl_hours':SESSION_TTL/3600,
            'cost_guardrail':'OFF by default; no outbound messages while disabled',
        }

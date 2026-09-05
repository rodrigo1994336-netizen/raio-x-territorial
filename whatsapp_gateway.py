from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

# Existing gateway is intentionally lightweight while Meta credentials are OFF.
# Coordinates and CAR identifiers can be extracted from inbound text without scraping.
CAR_RE=re.compile(r'\b[A-Z]{2}-\d{7}-[A-F0-9]{32}\b',re.I)
COORD_RE=re.compile(r'(-?\d{1,2}(?:[\.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[\.,]\d+)?)')
URL_RE=re.compile(r'https?://[^\s<>"\']+',re.I)


def _enabled():
    return os.getenv('RX_WHATSAPP_ENABLED','off').strip().lower() in {'1','true','yes','on'}


def _configured():
    return bool(os.getenv('WHATSAPP_ACCESS_TOKEN') and os.getenv('WHATSAPP_PHONE_NUMBER_ID'))


def _extract_cars(text:str):
    out=[];seen=set()
    for x in CAR_RE.findall((text or '').upper()):
        if x not in seen:seen.add(x);out.append(x)
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
    # Resolve short Google Maps links. We only extract coordinates from the final URL;
    # no page scraping or private data is performed.
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={'User-Agent':'Raio-X-Territorial/WhatsApp'}) as c:
            r=await c.get(url)
        final=unquote(str(r.url))
    except Exception:
        final=unquote(url)
    for pattern in (
        re.compile(r'@(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)'),
        re.compile(r'[?&](?:query|q|ll)=(-?\d{1,2}(?:\.\d+)?)[,%2C]+(-?\d{1,3}(?:\.\d+)?)',re.I),
    ):
        mm=pattern.search(final)
        if mm:
            try:
                lat=float(mm.group(1));lon=float(mm.group(2))
                if -90<=lat<=90 and -180<=lon<=180:return lat,lon
            except Exception:pass
    return None


async def _send_text(to:str,text:str):
    token=os.getenv('WHATSAPP_ACCESS_TOKEN');phone=os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    if not token or not phone:return {'ok':False,'detail':'whatsapp_credentials_missing'}
    url=f'https://graph.facebook.com/v23.0/{phone}/messages'
    payload={'messaging_product':'whatsapp','to':to,'type':'text','text':{'body':text[:4000]}}
    async with httpx.AsyncClient(timeout=25) as c:
        r=await c.post(url,headers={'Authorization':f'Bearer {token}'},json=payload)
    return {'ok':r.is_success,'status_code':r.status_code,'detail':None if r.is_success else r.text[:300]}


def register_routes(app):
    router=APIRouter()

    @router.get('/v1/whatsapp/status')
    async def status():
        return {'enabled':_enabled(),'configured':_configured(),'state':'ready' if _enabled() and _configured() else 'prepared_off','provider':'Meta WhatsApp Cloud API'}

    @router.get('/v1/whatsapp/webhook')
    async def verify(request:Request):
        mode=request.query_params.get('hub.mode');token=request.query_params.get('hub.verify_token');challenge=request.query_params.get('hub.challenge','')
        expected=os.getenv('WHATSAPP_VERIFY_TOKEN')
        if mode=='subscribe' and expected and token==expected:return PlainTextResponse(challenge)
        raise HTTPException(status_code=403,detail='verification_failed')

    @router.post('/v1/whatsapp/webhook')
    async def inbound(request:Request):
        body=await request.json()
        if not (_enabled() and _configured()):return {'ok':True,'state':'prepared_off'}
        messages=[]
        try:
            for entry in body.get('entry') or []:
                for change in entry.get('changes') or []:
                    value=(change.get('value') or {})
                    messages.extend(value.get('messages') or [])
        except Exception:messages=[]
        replies=[]
        for msg in messages[:10]:
            sender=str(msg.get('from') or '')
            text=((msg.get('text') or {}).get('body') or '').strip()
            if not sender or not text:continue
            cars=_extract_cars(text);coords=_coords_from_text(text) or await _coords_from_maps_url(text)
            if cars:reply='CAR recebido: '+cars[0]+'. Abra o Raio-X Territorial para executar a análise completa e emitir o relatório.'
            elif coords:reply=f'Coordenadas recebidas: {coords[0]:.6f}, {coords[1]:.6f}. Abra o Raio-X Territorial para localizar o imóvel correspondente.'
            else:reply='Envie um código CAR, coordenadas ou link do mapa para localizar um imóvel rural.'
            replies.append(await _send_text(sender,reply))
        return {'ok':True,'processed':len(replies),'replies':replies}

    app.include_router(router)


print('RX_WHATSAPP_GATEWAY=prepared_meta_cloud_api',flush=True)

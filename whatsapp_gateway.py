from __future__ import annotations

import os
import re
from typing import Any, Callable, Awaitable

import httpx
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse

CAR_RE=re.compile(r'\b([A-Z]{2}-\d{7}-[A-F0-9]{32})\b',re.I)


def _enabled() -> bool:
    return os.getenv('RX_WHATSAPP_ENABLED','off').strip().lower() in {'1','true','yes','on'}


def _config():
    return {
        'verify_token':os.getenv('WHATSAPP_VERIFY_TOKEN',''),
        'access_token':os.getenv('WHATSAPP_ACCESS_TOKEN',''),
        'phone_number_id':os.getenv('WHATSAPP_PHONE_NUMBER_ID',''),
        'api_version':os.getenv('WHATSAPP_GRAPH_VERSION','v24.0'),
        'public_base_url':os.getenv('RX_PUBLIC_BASE_URL','https://raio-x-territorial-v8.onrender.com').rstrip('/'),
    }


async def _send_text(to: str, body: str) -> dict[str,Any]:
    cfg=_config()
    if not (_enabled() and cfg['access_token'] and cfg['phone_number_id']):
        return {'ok':False,'disabled':True}
    url=f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}/messages"
    headers={'Authorization':f"Bearer {cfg['access_token']}",'Content-Type':'application/json'}
    payload={'messaging_product':'whatsapp','recipient_type':'individual','to':to,'type':'text','text':{'preview_url':True,'body':body[:4096]}}
    async with httpx.AsyncClient(timeout=25,follow_redirects=True) as c:
        r=await c.post(url,headers=headers,json=payload)
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
                if text:
                    out.append({'from':m.get('from'),'id':m.get('id'),'text':text,'timestamp':m.get('timestamp')})
    return out


def register_routes(app: FastAPI, analyze_fn: Callable[[str], Awaitable[dict[str,Any]]]):
    @app.get('/webhooks/whatsapp', response_class=PlainTextResponse)
    async def whatsapp_verify(
        hub_mode: str|None=Query(None,alias='hub.mode'),
        hub_challenge: str|None=Query(None,alias='hub.challenge'),
        hub_verify_token: str|None=Query(None,alias='hub.verify_token'),
    ):
        cfg=_config()
        if not _enabled():
            raise HTTPException(status_code=503,detail='WhatsApp integration is OFF')
        if hub_mode=='subscribe' and cfg['verify_token'] and hub_verify_token==cfg['verify_token']:
            return PlainTextResponse(hub_challenge or '')
        raise HTTPException(status_code=403,detail='Webhook verification failed')

    @app.post('/webhooks/whatsapp')
    async def whatsapp_inbound(req: Request):
        payload=await req.json()
        if not _enabled():
            return {'ok':True,'enabled':False,'processed':0}
        msgs=_extract_messages(payload)
        processed=0
        for msg in msgs:
            to=msg.get('from')
            if not to: continue
            m=CAR_RE.search(msg.get('text') or '')
            if not m:
                await _send_text(to,'Envie o código completo do CAR do imóvel para eu fazer o Raio-X Territorial. Exemplo: UF-0000000-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
                processed+=1; continue
            car=m.group(1).upper()
            try:
                result=await analyze_fn(car)
                c=result.get('car') or {}; p=c.get('properties') or {}
                emb=((result.get('embargos_ibama') or {}).get('exact') or {}).get('occurrence_count')
                anm=((result.get('anm') or {}).get('exact') or {}).get('occurrence_count')
                pro=((result.get('prodes') or {}).get('exact') or {}).get('occurrence_count')
                fire=result.get('fire_live') or {}
                base=_config()['public_base_url']
                report=f'{base}/v1/reports/property/{car}'
                lines=[
                    'RAIO-X TERRITORIAL',
                    f"Imóvel: {p.get('municipio') or '-'} / {p.get('uf') or '-'}",
                    f"Área CAR: {p.get('area') or '-'} ha",
                    f"Embargos IBAMA: {emb if emb is not None else 'fonte indisponível'}",
                    f"Processos ANM: {anm if anm is not None else 'fonte indisponível'}",
                    f"Ocorrências PRODES: {pro if pro is not None else 'fonte indisponível'}",
                    f"Fogo recente dentro: {fire.get('inside_count') if fire.get('ok') else 'fonte indisponível'}",
                    '',
                    f'Relatório completo: {report}',
                    '',
                    'Triagem automática baseada nas fontes consultadas; não equivale a certidão de regularidade.'
                ]
                await _send_text(to,'\n'.join(lines))
            except Exception:
                await _send_text(to,'Não consegui concluir o Raio-X deste CAR agora. A fonte oficial pode estar indisponível. Tente novamente ou use o aplicativo.')
            processed+=1
        return {'ok':True,'enabled':True,'processed':processed}

    @app.get('/v1/whatsapp/status')
    def whatsapp_status():
        cfg=_config()
        return {
            'enabled':_enabled(),
            'configured':bool(cfg['verify_token'] and cfg['access_token'] and cfg['phone_number_id']),
            'mode':'official-meta-cloud-api',
            'cost_guardrail':'OFF by default; no outbound messages while disabled',
        }

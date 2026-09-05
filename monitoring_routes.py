from __future__ import annotations

import asyncio
import os
import time
from fastapi import HTTPException, Request

import monitoring_store as store
from whatsapp_gateway import _send_text
from github_oidc_auth import authorization_from_headers

_LAST_RUN_MONOTONIC=0.0
_RUN_LOCK=asyncio.Lock()


def _alert_text(car_code:str,diff:dict)->str:
    keys=', '.join(diff.keys()) if diff else 'alteração detectada'
    base=os.getenv('RX_PUBLIC_BASE_URL','https://raio-x-territorial-app.onrender.com').rstrip('/')
    return ('ALERTA — RAIO-X TERRITORIAL\n'+f'Imóvel CAR: {car_code}\n'+f'Mudanças detectadas: {keys}\n\n'+f'Abra o Raio-X atualizado: {base}/\n\n'+'O alerta indica mudança nas fontes consultadas e não substitui conferência documental ou técnica.')


async def _deliver_if_configured(mon:dict,saved:dict)->dict:
    if not saved.get('changed'):return {'attempted':False}
    channel=(mon.get('channel') or 'in_app').lower();destination=mon.get('destination')
    if channel!='whatsapp' or not destination:return {'attempted':False,'channel':channel}
    result=await _send_text(destination,_alert_text(mon['car_code'],saved.get('diff') or {}));return {'attempted':True,'channel':'whatsapp','result':result}


def _authorize_scheduler(request:Request)->dict:
    configured=os.getenv('RX_MONITOR_TOKEN')
    if configured:
        supplied=request.headers.get('X-RaioX-Monitor-Token') or request.query_params.get('token')
        if supplied!=configured:raise HTTPException(status_code=401,detail='invalid_monitor_token')
        return {'mode':'shared_secret'}
    try:
        claims=authorization_from_headers(request.headers);return {'mode':'github_oidc','repository':claims.get('repository'),'run_id':claims.get('run_id'),'workflow_ref':claims.get('workflow_ref')}
    except Exception as exc:raise HTTPException(status_code=401,detail=f'invalid_github_oidc:{type(exc).__name__}')


def register_monitoring_routes(app,analyze_fn):
    @app.get('/v1/monitoring/status')
    def monitoring_status():
        r=store.readiness();dur=r.get('durability')
        note=('Persistência PostgreSQL ativa.' if dur=='durable' else ('Alertas operacionais no Key Value gratuito do Render. O store é funcional, mas o plano free não persiste dados após recriação/restart do Key Value; migrar para Starter/Postgres quando houver receita.' if dur=='operational_nonpersistent' else 'Nenhum backend de estado está vinculado.'))
        return {'ok':True,'persistence':dur,'backend':r.get('backend'),'database_bound':r.get('database_bound'),'redis_bound':r.get('redis_bound'),'driver':r.get('driver') or r.get('redis_driver'),'scheduler':'github-actions-10min','scheduler_auth':'shared-secret' if os.getenv('RX_MONITOR_TOKEN') else 'github-actions-oidc','fire_target_minutes':10,'whatsapp_alert_delivery':'official-meta-cloud-api-prepared','note':note}

    @app.post('/v1/monitoring/properties/{car_code}')
    async def add_property_monitor(car_code:str,request:Request):
        if not store.readiness()['ready']:raise HTTPException(status_code=503,detail='monitoring_backend_not_ready')
        body={}
        try:body=await request.json()
        except Exception:pass
        channel=(body.get('channel') or 'in_app').strip();destination=body.get('destination');monitor=await asyncio.to_thread(store.add_monitor,car_code,channel,destination);initial=None
        try:
            result=await analyze_fn(car_code.upper());initial=await asyncio.to_thread(store.save_snapshot,monitor['id'],store.compact_snapshot(result))
        except Exception as exc:initial={'ok':False,'detail':f'{type(exc).__name__}:{str(exc)[:220]}'}
        return {'ok':True,'backend':store.readiness().get('backend'),'monitor':monitor,'initial_snapshot':initial}

    @app.get('/v1/monitoring/properties')
    async def list_property_monitors():
        if not store.readiness()['ready']:return {'ok':False,'detail':'monitoring_backend_not_ready','monitors':[]}
        return {'ok':True,'backend':store.readiness().get('backend'),'monitors':await asyncio.to_thread(store.list_monitors,True,200)}

    @app.get('/v1/monitoring/alerts')
    async def monitoring_alerts(limit:int=50):
        if not store.readiness()['ready']:return {'ok':False,'detail':'monitoring_backend_not_ready','alerts':[]}
        return {'ok':True,'backend':store.readiness().get('backend'),'alerts':await asyncio.to_thread(store.recent_alerts,limit)}

    @app.post('/v1/monitoring/run')
    async def run_monitoring(request:Request,limit:int=25):
        global _LAST_RUN_MONOTONIC
        if not store.readiness()['ready']:raise HTTPException(status_code=503,detail='monitoring_backend_not_ready')
        auth=_authorize_scheduler(request);now=time.monotonic()
        if now-_LAST_RUN_MONOTONIC<120:return {'ok':True,'skipped':True,'reason':'cooldown','retry_after_seconds':int(120-(now-_LAST_RUN_MONOTONIC)),'auth':auth.get('mode')}
        if _RUN_LOCK.locked():return {'ok':True,'skipped':True,'reason':'run_already_in_progress','auth':auth.get('mode')}
        async with _RUN_LOCK:
            _LAST_RUN_MONOTONIC=time.monotonic();run_id=await asyncio.to_thread(store.begin_run);checked=changed=0;errors=[];deliveries=[]
            try:
                monitors=await asyncio.to_thread(store.list_monitors,True,max(1,min(limit,50)))
                for mon in monitors:
                    try:
                        result=await analyze_fn(mon['car_code'],force_refresh=True);snap=store.compact_snapshot(result);saved=await asyncio.to_thread(store.save_snapshot,mon['id'],snap);checked+=1
                        if saved.get('changed'):changed+=1;deliveries.append({'car_code':mon['car_code'],**(await _deliver_if_configured(mon,saved))})
                    except Exception as exc:errors.append({'car_code':mon['car_code'],'error':f'{type(exc).__name__}:{str(exc)[:220]}'})
                await asyncio.to_thread(store.finish_run,run_id,checked,changed,'ok' if not errors else 'partial',str(errors[:10]) if errors else None);return {'ok':True,'run_id':run_id,'backend':store.readiness().get('backend'),'checked':checked,'changed':changed,'deliveries':deliveries,'errors':errors,'auth':auth.get('mode')}
            except Exception as exc:
                await asyncio.to_thread(store.finish_run,run_id,checked,changed,'failed',f'{type(exc).__name__}:{str(exc)[:400]}');raise

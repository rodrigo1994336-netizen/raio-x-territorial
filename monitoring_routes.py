from __future__ import annotations

import asyncio
import os
import time
from fastapi import HTTPException, Request

import monitoring_store as store

_LAST_RUN_MONOTONIC = 0.0
_RUN_LOCK = asyncio.Lock()


def register_monitoring_routes(app, analyze_fn):
    @app.get('/v1/monitoring/status')
    def monitoring_status():
        r=store.readiness()
        return {
            'ok': True,
            'persistence': 'durable' if r['ready'] else 'not_bound',
            'database_bound': r['database_bound'],
            'driver': r['driver'],
            'scheduler': 'github-actions-15min',
            'fire_target_minutes': 10,
            'note': 'Persistência exige DATABASE_URL e driver PostgreSQL. Sem isso o sistema não finge monitoramento contínuo.'
        }

    @app.post('/v1/monitoring/properties/{car_code}')
    async def add_property_monitor(car_code: str, request: Request):
        if not store.readiness()['ready']:
            raise HTTPException(status_code=503, detail='monitoring_database_not_ready')
        body={}
        try:
            body=await request.json()
        except Exception:
            pass
        channel=(body.get('channel') or 'in_app').strip()
        destination=body.get('destination')
        return {'ok':True,'monitor':await asyncio.to_thread(store.add_monitor,car_code,channel,destination)}

    @app.get('/v1/monitoring/properties')
    async def list_property_monitors():
        if not store.readiness()['ready']:
            return {'ok':False,'detail':'monitoring_database_not_ready','monitors':[]}
        return {'ok':True,'monitors':await asyncio.to_thread(store.list_monitors,True,200)}

    @app.get('/v1/monitoring/alerts')
    async def monitoring_alerts(limit:int=50):
        if not store.readiness()['ready']:
            return {'ok':False,'detail':'monitoring_database_not_ready','alerts':[]}
        return {'ok':True,'alerts':await asyncio.to_thread(store.recent_alerts,limit)}

    @app.post('/v1/monitoring/run')
    async def run_monitoring(request: Request, limit:int=25):
        global _LAST_RUN_MONOTONIC
        if not store.readiness()['ready']:
            raise HTTPException(status_code=503,detail='monitoring_database_not_ready')
        configured=os.getenv('RX_MONITOR_TOKEN')
        if configured:
            supplied=request.headers.get('X-RaioX-Monitor-Token') or request.query_params.get('token')
            if supplied != configured:
                raise HTTPException(status_code=401,detail='invalid_monitor_token')
        # With no token configured, prevent compute abuse on the zero-cost deployment.
        now=time.monotonic()
        if not configured and now-_LAST_RUN_MONOTONIC < 600:
            return {'ok':True,'skipped':True,'reason':'cooldown','retry_after_seconds':int(600-(now-_LAST_RUN_MONOTONIC))}
        if _RUN_LOCK.locked():
            return {'ok':True,'skipped':True,'reason':'run_already_in_progress'}
        async with _RUN_LOCK:
            _LAST_RUN_MONOTONIC=time.monotonic()
            run_id=await asyncio.to_thread(store.begin_run)
            checked=changed=0; errors=[]
            try:
                monitors=await asyncio.to_thread(store.list_monitors,True,max(1,min(limit,50)))
                for mon in monitors:
                    try:
                        result=await analyze_fn(mon['car_code'],force_refresh=True)
                        snap=store.compact_snapshot(result)
                        saved=await asyncio.to_thread(store.save_snapshot,mon['id'],snap)
                        checked+=1
                        if saved.get('changed'): changed+=1
                    except Exception as exc:
                        errors.append({'car_code':mon['car_code'],'error':f'{type(exc).__name__}:{str(exc)[:220]}'})
                await asyncio.to_thread(store.finish_run,run_id,checked,changed,'ok' if not errors else 'partial',str(errors[:10]) if errors else None)
                return {'ok':True,'run_id':run_id,'checked':checked,'changed':changed,'errors':errors}
            except Exception as exc:
                await asyncio.to_thread(store.finish_run,run_id,checked,changed,'failed',f'{type(exc).__name__}:{str(exc)[:400]}')
                raise

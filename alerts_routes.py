from __future__ import annotations

from fastapi import HTTPException

import monitoring_store as store


def _not_ready_payload():
    r=store.readiness()
    return {
        'ok':False,
        'state':'database_link_required',
        'database_bound':r.get('database_bound'),
        'driver':r.get('driver'),
        'message':'O Centro de Alertas está instalado, mas a persistência ainda não está vinculada ao Postgres do Render.',
    }


def register_alert_routes(app):
    @app.get('/v1/alerts/summary')
    def alerts_summary():
        r=store.readiness()
        if not r.get('ready'):
            return {**_not_ready_payload(),'active_monitors':0,'unread_count':0,'critical_unread_count':0,'last_checked_at':None,'last_changed_at':None,'last_run':None}
        return {'ok':True,'state':'active',**store.alert_summary()}

    @app.get('/v1/alerts')
    def alerts_list(limit:int=50, unread_only:bool=False):
        if not store.readiness().get('ready'):
            return {**_not_ready_payload(),'alerts':[]}
        return {'ok':True,'alerts':store.recent_alerts(limit,unread_only)}

    @app.post('/v1/alerts/{alert_id}/read')
    def alerts_mark_read(alert_id:int):
        if not store.readiness().get('ready'):
            raise HTTPException(status_code=503,detail='monitoring_database_not_ready')
        if not store.mark_alert_read(alert_id):
            raise HTTPException(status_code=404,detail='alert_not_found')
        return {'ok':True,'alert_id':alert_id,'read':True}

    @app.post('/v1/alerts/read-all')
    def alerts_mark_all_read():
        if not store.readiness().get('ready'):
            raise HTTPException(status_code=503,detail='monitoring_database_not_ready')
        return {'ok':True,'updated':store.mark_all_alerts_read()}

    @app.post('/v1/monitoring/properties/{car_code}/pause')
    def pause_property_monitor(car_code:str):
        if not store.readiness().get('ready'):
            raise HTTPException(status_code=503,detail='monitoring_database_not_ready')
        row=store.set_monitor_enabled(car_code,False)
        if not row:
            raise HTTPException(status_code=404,detail='monitor_not_found')
        return {'ok':True,'monitor':row}

    @app.post('/v1/monitoring/properties/{car_code}/resume')
    def resume_property_monitor(car_code:str):
        if not store.readiness().get('ready'):
            raise HTTPException(status_code=503,detail='monitoring_database_not_ready')
        row=store.set_monitor_enabled(car_code,True)
        if not row:
            raise HTTPException(status_code=404,detail='monitor_not_found')
        return {'ok':True,'monitor':row}

    @app.get('/v1/alerts/system-status')
    def alert_system_status():
        r=store.readiness()
        return {
            'ok':True,
            'ui':'installed',
            'persistence':'active' if r.get('ready') else 'awaiting_database_binding',
            'database_bound':r.get('database_bound'),
            'driver':r.get('driver'),
            'scheduler':'github-actions-15min',
            'channels':['in_app','whatsapp-prepared'],
            'tracked_events':[
                'embargos_ibama','embargos_icmbio','prodes','fogo_dentro','fogo_proximo',
                'terra_indigena','unidades_conservacao','quilombola','assentamentos',
                'processos_anm','outorgas','pivos','status_car','terras_raras'
            ],
        }

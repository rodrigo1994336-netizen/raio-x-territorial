from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any


def _database_url() -> str | None:
    for key in ('DATABASE_URL', 'POSTGRES_URL', 'RENDER_POSTGRES_URL'):
        value = os.getenv(key)
        if value:
            return value
    return None


def _driver():
    try:
        import psycopg
        return ('psycopg', psycopg)
    except Exception:
        try:
            import psycopg2
            return ('psycopg2', psycopg2)
        except Exception:
            return (None, None)


def readiness() -> dict[str, Any]:
    name, _ = _driver()
    return {
        'database_bound': bool(_database_url()),
        'driver': name,
        'ready': bool(_database_url() and name),
    }


def _connect():
    url = _database_url()
    name, mod = _driver()
    if not url:
        raise RuntimeError('database_link_required')
    if not mod:
        raise RuntimeError('postgres_driver_required')
    return mod.connect(url)


def ensure_schema() -> None:
    ddl = '''
    CREATE TABLE IF NOT EXISTS rx_monitors (
      id BIGSERIAL PRIMARY KEY,
      car_code TEXT NOT NULL UNIQUE,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      channel TEXT NOT NULL DEFAULT 'in_app',
      destination TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_checked_at TIMESTAMPTZ,
      last_changed_at TIMESTAMPTZ
    );
    CREATE TABLE IF NOT EXISTS rx_monitor_snapshots (
      id BIGSERIAL PRIMARY KEY,
      monitor_id BIGINT NOT NULL REFERENCES rx_monitors(id) ON DELETE CASCADE,
      captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      signature TEXT NOT NULL,
      payload JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS rx_monitor_snapshots_monitor_idx
      ON rx_monitor_snapshots(monitor_id, captured_at DESC);
    CREATE TABLE IF NOT EXISTS rx_monitor_alerts (
      id BIGSERIAL PRIMARY KEY,
      monitor_id BIGINT NOT NULL REFERENCES rx_monitors(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      kind TEXT NOT NULL,
      severity TEXT NOT NULL DEFAULT 'info',
      message TEXT NOT NULL,
      diff JSONB NOT NULL DEFAULT '{}'::jsonb,
      delivered_at TIMESTAMPTZ,
      delivery_channel TEXT,
      delivery_status TEXT NOT NULL DEFAULT 'pending',
      read_at TIMESTAMPTZ
    );
    ALTER TABLE rx_monitor_alerts ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
    CREATE INDEX IF NOT EXISTS rx_monitor_alerts_monitor_idx
      ON rx_monitor_alerts(monitor_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS rx_monitor_alerts_unread_idx
      ON rx_monitor_alerts(read_at, created_at DESC);
    CREATE TABLE IF NOT EXISTS rx_monitor_runs (
      id BIGSERIAL PRIMARY KEY,
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      finished_at TIMESTAMPTZ,
      checked_count INTEGER NOT NULL DEFAULT 0,
      changed_count INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'running',
      detail TEXT
    );
    '''
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def add_monitor(car_code: str, channel: str = 'in_app', destination: str | None = None) -> dict[str, Any]:
    ensure_schema()
    code = car_code.strip().upper()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute('''
          INSERT INTO rx_monitors(car_code, channel, destination, enabled)
          VALUES (%s,%s,%s,TRUE)
          ON CONFLICT(car_code) DO UPDATE SET
            channel=EXCLUDED.channel,
            destination=EXCLUDED.destination,
            enabled=TRUE,
            updated_at=NOW()
          RETURNING id, car_code, enabled, channel, destination, created_at, updated_at
        ''', (code, channel, destination))
        row = cur.fetchone()
        conn.commit()
        return {
            'id': row[0], 'car_code': row[1], 'enabled': row[2],
            'channel': row[3], 'destination': row[4],
            'created_at': row[5].isoformat() if row[5] else None,
            'updated_at': row[6].isoformat() if row[6] else None,
        }
    finally:
        conn.close()


def set_monitor_enabled(car_code: str, enabled: bool) -> dict[str, Any] | None:
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute('''
          UPDATE rx_monitors SET enabled=%s, updated_at=NOW()
          WHERE car_code=%s
          RETURNING id,car_code,enabled,channel,destination,updated_at
        ''', (bool(enabled), car_code.strip().upper()))
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        return {'id':row[0],'car_code':row[1],'enabled':row[2],'channel':row[3],'destination':row[4],'updated_at':row[5].isoformat() if row[5] else None}
    finally:
        conn.close()


def list_monitors(enabled_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        sql = '''SELECT id,car_code,enabled,channel,destination,created_at,updated_at,last_checked_at,last_changed_at
                 FROM rx_monitors'''
        params: list[Any] = []
        if enabled_only:
            sql += ' WHERE enabled=TRUE'
        sql += ' ORDER BY updated_at DESC LIMIT %s'
        params.append(max(1, min(int(limit), 500)))
        cur.execute(sql, params)
        out=[]
        for r in cur.fetchall():
            out.append({
                'id':r[0],'car_code':r[1],'enabled':r[2],'channel':r[3],'destination':r[4],
                'created_at':r[5].isoformat() if r[5] else None,
                'updated_at':r[6].isoformat() if r[6] else None,
                'last_checked_at':r[7].isoformat() if r[7] else None,
                'last_changed_at':r[8].isoformat() if r[8] else None,
            })
        return out
    finally:
        conn.close()


def compact_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    car=result.get('car') or {}; props=car.get('properties') or {}
    sigef=result.get('sigef') or {}; emb=result.get('embargos_ibama') or {}; pro=result.get('prodes') or {}
    anm=result.get('anm') or {}; fire=result.get('fire_live') or {}; con=result.get('territorial_constraints') or {}
    water=result.get('water_mg') or {}; piv=result.get('pivots_ana') or {}; minerals=result.get('critical_minerals') or {}
    eex=emb.get('exact') or {}; pex=pro.get('exact') or {}; aex=anm.get('exact') or {}
    services=con.get('services') or {}
    return {
        'car_code': props.get('cod_imovel'),
        'car_status': props.get('status_imovel'),
        'car_condition': props.get('condicao'),
        'area_ha': props.get('area'),
        'sigef_candidates': sigef.get('feature_count_bbox') if sigef.get('feature_count_bbox') is not None else sigef.get('feature_count'),
        'ibama_embargo_count': eex.get('occurrence_count'),
        'ibama_embargo_area_ha': eex.get('area_unique_ha'),
        'prodes_count': pex.get('occurrence_count'),
        'prodes_area_ha': pex.get('area_unique_ha'),
        'anm_count': aex.get('occurrence_count'),
        'anm_area_ha': aex.get('area_unique_ha'),
        'fire_inside_count': fire.get('inside_count'),
        'fire_near_count': fire.get('near_count'),
        'fire_latest_file': fire.get('latest_file'),
        'indigenous_count': (services.get('terra_indigena') or {}).get('occurrence_count'),
        'conservation_count': (services.get('unidade_conservacao') or {}).get('occurrence_count'),
        'quilombola_count': (services.get('quilombola') or {}).get('occurrence_count'),
        'settlement_count': (services.get('assentamento') or {}).get('occurrence_count'),
        'icmbio_embargo_count': (services.get('embargo_icmbio') or {}).get('occurrence_count'),
        'water_inside_count': water.get('inside_count'),
        'water_near_count': water.get('near_count'),
        'pivot_intersection_count': piv.get('intersection_count'),
        'pivot_intersection_area_ha': piv.get('intersection_area_unique_ha'),
        'rare_earth_signal': bool(minerals.get('rare_earth_signal')),
        'critical_minerals': sorted(minerals.get('mineral_codes') or []),
    }


def snapshot_signature(payload: dict[str, Any]) -> str:
    raw=json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _diff(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    if old is None:
        return {'initial_snapshot': True}
    out={}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            out[key]={'before':old.get(key),'after':new.get(key)}
    return out


def _number(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _increased(diff: dict[str, Any], key: str) -> bool:
    row=diff.get(key) or {}
    return _number(row.get('after')) > _number(row.get('before'))


def _classify_alert(diff: dict[str, Any]) -> tuple[str,str]:
    critical=[]; attention=[]
    for key,label in (
        ('ibama_embargo_count','novo embargo IBAMA'),
        ('icmbio_embargo_count','novo embargo ICMBio'),
        ('fire_inside_count','novo foco de calor dentro do imóvel'),
        ('indigenous_count','mudança em Terra Indígena'),
        ('conservation_count','mudança em Unidade de Conservação'),
    ):
        if key in diff and _increased(diff,key): critical.append(label)
    for key,label in (
        ('prodes_count','mudança PRODES'),
        ('anm_count','mudança em processo minerário'),
        ('quilombola_count','mudança em território quilombola'),
        ('settlement_count','mudança em assentamento'),
        ('water_inside_count','mudança em outorga d\'água'),
        ('pivot_intersection_count','mudança em pivô de irrigação'),
        ('rare_earth_signal','mudança em sinal de terras raras'),
        ('car_status','mudança no status do CAR'),
        ('car_condition','mudança na condição de análise do CAR'),
    ):
        if key in diff: attention.append(label)
    if critical:
        return 'critical', 'Alerta crítico: ' + '; '.join(critical[:4])
    if attention:
        return 'attention', 'Atenção: ' + '; '.join(attention[:5])
    return 'info', 'Mudança detectada em dados monitorados do imóvel'


def save_snapshot(monitor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    sig=snapshot_signature(payload)
    conn=_connect()
    try:
        cur=conn.cursor()
        cur.execute('SELECT payload,signature FROM rx_monitor_snapshots WHERE monitor_id=%s ORDER BY captured_at DESC LIMIT 1',(monitor_id,))
        prev=cur.fetchone(); old=prev[0] if prev else None; old_sig=prev[1] if prev else None
        diff=_diff(old,payload)
        changed=bool(prev and old_sig != sig)
        cur.execute('INSERT INTO rx_monitor_snapshots(monitor_id,signature,payload) VALUES (%s,%s,%s)',(monitor_id,sig,json.dumps(payload,ensure_ascii=False)))
        cur.execute('UPDATE rx_monitors SET last_checked_at=NOW(), last_changed_at=CASE WHEN %s THEN NOW() ELSE last_changed_at END, updated_at=NOW() WHERE id=%s',(changed,monitor_id))
        if changed:
            severity,message=_classify_alert(diff)
            cur.execute('INSERT INTO rx_monitor_alerts(monitor_id,kind,severity,message,diff) VALUES (%s,%s,%s,%s,%s)',(monitor_id,'property_change',severity,message,json.dumps(diff,ensure_ascii=False)))
        conn.commit()
        return {'changed':changed,'initial':prev is None,'signature':sig,'diff':diff}
    finally:
        conn.close()


def recent_alerts(limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
    ensure_schema(); conn=_connect()
    try:
        cur=conn.cursor()
        sql='''
          SELECT a.id,m.car_code,a.created_at,a.kind,a.severity,a.message,a.diff,a.delivery_status,a.read_at
          FROM rx_monitor_alerts a JOIN rx_monitors m ON m.id=a.monitor_id
        '''
        params=[]
        if unread_only:
            sql += ' WHERE a.read_at IS NULL'
        sql += ' ORDER BY a.created_at DESC LIMIT %s'
        params.append(max(1,min(int(limit),200)))
        cur.execute(sql,params)
        return [{'id':r[0],'car_code':r[1],'created_at':r[2].isoformat(),'kind':r[3],'severity':r[4],'message':r[5],'diff':r[6],'delivery_status':r[7],'read_at':r[8].isoformat() if r[8] else None,'unread':r[8] is None} for r in cur.fetchall()]
    finally:
        conn.close()


def alert_summary() -> dict[str, Any]:
    ensure_schema(); conn=_connect()
    try:
        cur=conn.cursor()
        cur.execute('SELECT COUNT(*) FROM rx_monitors WHERE enabled=TRUE'); active=int(cur.fetchone()[0] or 0)
        cur.execute('SELECT COUNT(*) FROM rx_monitor_alerts WHERE read_at IS NULL'); unread=int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM rx_monitor_alerts WHERE read_at IS NULL AND severity='critical'"); critical=int(cur.fetchone()[0] or 0)
        cur.execute('SELECT MAX(last_checked_at),MAX(last_changed_at) FROM rx_monitors'); row=cur.fetchone()
        cur.execute('SELECT started_at,finished_at,checked_count,changed_count,status FROM rx_monitor_runs ORDER BY started_at DESC LIMIT 1'); run=cur.fetchone()
        return {
            'active_monitors':active,'unread_count':unread,'critical_unread_count':critical,
            'last_checked_at':row[0].isoformat() if row and row[0] else None,
            'last_changed_at':row[1].isoformat() if row and row[1] else None,
            'last_run':None if not run else {'started_at':run[0].isoformat() if run[0] else None,'finished_at':run[1].isoformat() if run[1] else None,'checked_count':run[2],'changed_count':run[3],'status':run[4]},
        }
    finally:
        conn.close()


def mark_alert_read(alert_id: int) -> bool:
    ensure_schema(); conn=_connect()
    try:
        cur=conn.cursor(); cur.execute('UPDATE rx_monitor_alerts SET read_at=COALESCE(read_at,NOW()) WHERE id=%s RETURNING id',(int(alert_id),)); row=cur.fetchone(); conn.commit(); return bool(row)
    finally: conn.close()


def mark_all_alerts_read() -> int:
    ensure_schema(); conn=_connect()
    try:
        cur=conn.cursor(); cur.execute('UPDATE rx_monitor_alerts SET read_at=NOW() WHERE read_at IS NULL'); n=cur.rowcount; conn.commit(); return int(n or 0)
    finally: conn.close()


def begin_run() -> int:
    ensure_schema(); conn=_connect()
    try:
        cur=conn.cursor(); cur.execute("INSERT INTO rx_monitor_runs(status) VALUES ('running') RETURNING id"); rid=cur.fetchone()[0]; conn.commit(); return rid
    finally: conn.close()


def finish_run(run_id:int, checked:int, changed:int, status:str='ok', detail:str|None=None) -> None:
    conn=_connect()
    try:
        cur=conn.cursor(); cur.execute('UPDATE rx_monitor_runs SET finished_at=NOW(),checked_count=%s,changed_count=%s,status=%s,detail=%s WHERE id=%s',(checked,changed,status,detail,run_id)); conn.commit()
    finally: conn.close()

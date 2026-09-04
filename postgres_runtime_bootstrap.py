from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _database_bound():
    return any(os.getenv(k) for k in ('DATABASE_URL','POSTGRES_URL','RENDER_POSTGRES_URL'))


def _driver_present():
    return bool(importlib.util.find_spec('psycopg') or importlib.util.find_spec('psycopg2'))


def ensure_postgres_driver():
    if not _database_bound():
        print('RX_POSTGRES_BOOTSTRAP=waiting_database_binding',flush=True)
        return False
    if _driver_present():
        print('RX_POSTGRES_BOOTSTRAP=driver_present',flush=True)
        return True
    if os.getenv('RX_POSTGRES_RUNTIME_INSTALL','on').strip().lower() not in {'1','on','true','yes'}:
        print('RX_POSTGRES_BOOTSTRAP=driver_missing_runtime_install_off',flush=True)
        return False
    try:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','psycopg[binary]>=3.2,<4'],check=True,timeout=120,stdout=subprocess.DEVNULL)
        ok=_driver_present()
        print('RX_POSTGRES_BOOTSTRAP=' + ('installed' if ok else 'install_completed_but_not_importable'),flush=True)
        return ok
    except Exception as e:
        print(f'RX_POSTGRES_BOOTSTRAP=failed:{type(e).__name__}:{str(e)[:220]}',flush=True)
        return False

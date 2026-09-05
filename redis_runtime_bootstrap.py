from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _redis_bound():
    return any(os.getenv(k) for k in ('REDIS_URL','RENDER_REDIS_URL','KEY_VALUE_URL'))


def _driver_present():
    return bool(importlib.util.find_spec('redis'))


def ensure_redis_driver():
    if not _redis_bound():
        print('RX_REDIS_BOOTSTRAP=waiting_key_value_binding',flush=True);return False
    if _driver_present():
        print('RX_REDIS_BOOTSTRAP=driver_present',flush=True);return True
    if os.getenv('RX_REDIS_RUNTIME_INSTALL','on').strip().lower() not in {'1','on','true','yes'}:
        print('RX_REDIS_BOOTSTRAP=driver_missing_runtime_install_off',flush=True);return False
    try:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','redis>=5,<7'],check=True,timeout=120,stdout=subprocess.DEVNULL)
        ok=_driver_present();print('RX_REDIS_BOOTSTRAP='+('installed' if ok else 'install_completed_but_not_importable'),flush=True);return ok
    except Exception as e:
        print(f'RX_REDIS_BOOTSTRAP=failed:{type(e).__name__}:{str(e)[:220]}',flush=True);return False

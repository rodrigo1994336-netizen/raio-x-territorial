from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _present():
    return importlib.util.find_spec('jwt') is not None


def ensure_jwt():
    if _present():
        print('RX_JWT_BOOTSTRAP=present',flush=True);return True
    if os.getenv('RX_JWT_RUNTIME_INSTALL','on').strip().lower() not in {'1','on','true','yes'}:
        print('RX_JWT_BOOTSTRAP=missing_install_off',flush=True);return False
    try:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','PyJWT[crypto]>=2.10,<3'],check=True,timeout=120,stdout=subprocess.DEVNULL)
        ok=_present();print('RX_JWT_BOOTSTRAP='+('installed' if ok else 'install_not_importable'),flush=True);return ok
    except Exception as e:
        print(f'RX_JWT_BOOTSTRAP=failed:{type(e).__name__}:{str(e)[:220]}',flush=True);return False

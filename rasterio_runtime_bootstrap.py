from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _present():
    return bool(importlib.util.find_spec('rasterio') and importlib.util.find_spec('numpy'))


def ensure_rasterio():
    if _present():
        print('RX_RASTERIO_BOOTSTRAP=present',flush=True);return True
    if os.getenv('RX_RASTERIO_RUNTIME_INSTALL','on').strip().lower() not in {'1','on','true','yes'}:
        print('RX_RASTERIO_BOOTSTRAP=missing_install_off',flush=True);return False
    try:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','numpy>=2,<3','rasterio>=1.4,<2'],check=True,timeout=180,stdout=subprocess.DEVNULL)
        ok=_present();print('RX_RASTERIO_BOOTSTRAP='+('installed' if ok else 'install_not_importable'),flush=True);return ok
    except Exception as e:
        print(f'RX_RASTERIO_BOOTSTRAP=failed:{type(e).__name__}:{str(e)[:220]}',flush=True);return False

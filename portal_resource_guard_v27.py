from __future__ import annotations

import os
import resource
import sys
import time

import portal_v8

app = portal_v8.app
START = time.monotonic()
HEAVY_FORBIDDEN = ('rasterio', 'mapbiomas_coverage', 'terrain_srtm', 'sentinel_cog', 'report_engine_v8')


def _rss_bytes() -> int:
    # Linux ru_maxrss is KiB.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)


def _heavy_loaded():
    return sorted(name for name in HEAVY_FORBIDDEN if name in sys.modules)


@app.get('/v1/portal/runtime')
def portal_runtime_v27():
    rss = _rss_bytes()
    loaded = _heavy_loaded()
    limit = int(os.getenv('RX_PORTAL_RSS_BUDGET_MB', '220')) * 1024 * 1024
    return {
        'ok': not loaded and rss < limit,
        'rss_bytes': rss,
        'rss_mb': round(rss / 1024 / 1024, 1),
        'rss_budget_mb': round(limit / 1024 / 1024, 1),
        'heavy_modules_loaded': loaded,
        'uptime_seconds': round(time.monotonic() - START, 1),
        'architecture': 'light-portal-heavy-report-worker',
    }


loaded = _heavy_loaded()
print(
    'RX_PORTAL_RESOURCE_GUARD_V27='
    + ('clean' if not loaded else 'heavy_modules:' + ','.join(loaded))
    + f':rss_mb={_rss_bytes()/1024/1024:.1f}',
    flush=True,
)

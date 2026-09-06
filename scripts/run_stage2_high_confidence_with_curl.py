from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import measure_stage2_high_confidence as measurement


def curl_get(url: str, timeout: int = 120, retries: int = 7) -> bytes:
    """Transport-only replacement for urllib on public sources with flaky TLS.

    It intentionally changes no query threshold, geometry or accounting rule from
    CAR_NAME_HIGH_CONFIDENCE_PROTOCOL_V1. curl is already available on GitHub runners.
    """
    last: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        cmd = [
            'curl', '--fail', '--silent', '--show-error', '--location',
            '--connect-timeout', '25', '--max-time', str(max(30, int(timeout))),
            '--retry', '2', '--retry-delay', '2', '--retry-all-errors',
            '-A', 'Raio-X-Territorial/Stage2-HighConfidence-V1',
            url,
        ]
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if p.returncode == 0 and p.stdout:
                return p.stdout
            last = RuntimeError(f'curl_exit_{p.returncode}: {p.stderr.decode("utf-8", "replace")[:500]}')
        except Exception as exc:
            last = exc
        if attempt + 1 < attempts:
            pause = min(15.0, 1.7 * (attempt + 1))
            print(f'HTTP_RETRY attempt={attempt+1}/{attempts} wait={pause:.1f}s error={last}', flush=True)
            time.sleep(pause)
    raise last or RuntimeError('curl transport failed')


def wfs1_page(bbox: tuple[float, float, float, float], start: int, count: int = 5000) -> list[dict]:
    """Use the already-proven SICAR WFS 1.0 transport for statewide CAR enumeration.

    This deliberately replaces only the SICAR page transport used by the frozen
    Stage-2 measurement. The 98% geometry rule, SIGEF bridge, CAFIR resolution,
    denominator/accounting and the frozen 25% stop threshold remain untouched.
    GeoServer supports startIndex as a paging extension on this WFS 1.0 endpoint.
    """
    west, south, east, north = bbox
    params = {
        'service': 'WFS',
        'version': '1.0.0',
        'request': 'GetFeature',
        'typeName': measurement.TYPENAME,
        'outputFormat': 'application/json',
        'srsName': 'EPSG:4674',
        'bbox': f'{west},{south},{east},{north},EPSG:4674',
        'CQL_FILTER': "status_imovel IN ('AT','PE','SU')",
        'maxFeatures': str(count),
        'startIndex': str(start),
    }
    data = measurement.jget(measurement.WFS, params, timeout=180)
    return data.get('features') or []


# Transport-only substitutions. Do not relax the frozen Stage-2 protocol here.
measurement.get = curl_get
measurement.wfs_page = wfs1_page

if __name__ == '__main__':
    raise SystemExit(measurement.main())

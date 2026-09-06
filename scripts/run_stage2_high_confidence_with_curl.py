from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
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
    """Use the proven SICAR WFS 1.0 transport for statewide CAR enumeration.

    Only transport robustness is handled here. The WFS query itself, 98% geometry
    rule, SIGEF bridge, CAFIR resolution, denominator/accounting and frozen 25% stop
    threshold are unchanged. A 2xx HTML/XML/proxy body is treated as a transport
    failure and retried instead of being misread as a measured empty page.
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
    url = measurement.WFS + '?' + urllib.parse.urlencode(params)
    last: Exception | None = None
    payload_attempts = 7
    for attempt in range(payload_attempts):
        try:
            raw = curl_get(url, timeout=180, retries=3)
            text = raw.decode('utf-8', 'replace').lstrip('\ufeff\r\n\t ')
            if not text.startswith('{'):
                snippet = ' '.join(text[:400].split())
                raise RuntimeError(f'SICAR_WFS1_NON_JSON_BODY: {snippet}')
            data = json.loads(text)
            if not isinstance(data, dict):
                raise RuntimeError(f'SICAR_WFS1_UNEXPECTED_JSON_TYPE: {type(data).__name__}')
            if data.get('error') or data.get('exceptions') or data.get('ExceptionReport'):
                raise RuntimeError(f'SICAR_WFS1_SOURCE_ERROR: {str(data)[:800]}')
            features = data.get('features')
            if features is None:
                raise RuntimeError(f'SICAR_WFS1_MISSING_FEATURES: {str(data)[:800]}')
            if not isinstance(features, list):
                raise RuntimeError(f'SICAR_WFS1_FEATURES_NOT_LIST: {type(features).__name__}')
            return features
        except Exception as exc:
            last = exc
            if attempt + 1 < payload_attempts:
                pause = min(20.0, 2.0 * (attempt + 1))
                print(
                    f'SICAR_WFS1_PAYLOAD_RETRY start={start} attempt={attempt+1}/{payload_attempts} '
                    f'wait={pause:.1f}s error={last}',
                    flush=True,
                )
                time.sleep(pause)
    raise last or RuntimeError('SICAR WFS 1.0 payload validation failed')


# Transport-only substitutions. Do not relax the frozen Stage-2 protocol here.
measurement.get = curl_get
measurement.wfs_page = wfs1_page

if __name__ == '__main__':
    raise SystemExit(measurement.main())

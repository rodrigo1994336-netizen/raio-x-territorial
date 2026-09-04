from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v17 import generate_live_report

base.generate_live_report=generate_live_report
v13patch.generate_live_report=generate_live_report
base.APP_VERSION='0.29.0-progressive-v17-highres-srtm-sicarv2'

print('RX_REPORT_V17_RUNTIME=highres_srtm_sicarv2',flush=True)

from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v15 import generate_live_report

base.generate_live_report=generate_live_report
v13patch.generate_live_report=generate_live_report
base.APP_VERSION='0.27.0-progressive-v15-mapbiomas-pasture-sentinel-ndvi'

print('RX_REPORT_V15_RUNTIME=mapbiomas_pasture_sentinel_ndvi',flush=True)

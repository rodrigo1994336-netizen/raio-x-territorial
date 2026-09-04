from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v14 import generate_live_report

base.generate_live_report=generate_live_report
v13patch.generate_live_report=generate_live_report
base.APP_VERSION='0.26.0-progressive-v14-sentinel-cog-ndvi'

print('RX_REPORT_V14_RUNTIME=sentinel_cog_rgb_ndvi',flush=True)

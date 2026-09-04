from __future__ import annotations

import report_api as base
import report_v9_patch  # keeps progressive quick/deep endpoints
from live_report_adapter_v10 import generate_live_report

base.generate_live_report=generate_live_report
base.APP_VERSION='0.19.2-progressive-v10-fluent-report'

print('RX_REPORT_V10_RUNTIME=fluent_v5_renderer',flush=True)

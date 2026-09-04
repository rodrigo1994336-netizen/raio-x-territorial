from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v16 import generate_live_report

base.generate_live_report=generate_live_report
v13patch.generate_live_report=generate_live_report
base.APP_VERSION='0.28.0-progressive-v16-truth-reconciled'

print('RX_REPORT_V16_RUNTIME=truth_reconciled',flush=True)

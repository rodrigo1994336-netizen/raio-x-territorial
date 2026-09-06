from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v18 import generate_live_report
import prodes_truth_v44  # noqa: F401 — patches V9/V11 lens used by the V18 report chain

base.generate_live_report=generate_live_report
v13patch.generate_live_report=generate_live_report
base.APP_VERSION='0.31.0-progressive-v18-street-access'

print('RX_REPORT_V18_RUNTIME=street_access_connected prodes_truth_v44:on',flush=True)

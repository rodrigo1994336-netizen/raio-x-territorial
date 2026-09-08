from __future__ import annotations

import report_api as base
import report_v13_patch as v13patch
from live_report_adapter_v19 import generate_live_report
import prodes_truth_v44  # noqa: F401

base.generate_live_report = generate_live_report
v13patch.generate_live_report = generate_live_report
base.APP_VERSION = "0.47.0-v47-block2-candidate"

print("RX_REPORT_V47_RUNTIME=basedosdados_car_integrity_connected", flush=True)
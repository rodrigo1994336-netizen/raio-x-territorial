from __future__ import annotations

import report_api as base
import report_v9_patch  # mantém endpoints progressivos quick/deep
import parity_public_layers  # mantém SFB/IPHAN no motor territorial
from live_report_adapter_v12 import generate_live_report

base.generate_live_report=generate_live_report
base.APP_VERSION='0.24.0-progressive-v12-book-satellite-groundwater'

print('RX_REPORT_V12_RUNTIME=book_satellite_groundwater',flush=True)

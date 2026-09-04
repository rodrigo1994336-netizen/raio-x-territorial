from __future__ import annotations

import report_api as base
import report_v9_patch  # mantém endpoints progressivos quick/deep
import parity_public_layers  # injeta SFB + IPHAN no motor territorial antes das consultas
from live_report_adapter_v11 import generate_live_report

base.generate_live_report=generate_live_report
base.APP_VERSION='0.20.0-progressive-v11-same-car-parity'

print('RX_REPORT_V11_RUNTIME=same_car_parity',flush=True)

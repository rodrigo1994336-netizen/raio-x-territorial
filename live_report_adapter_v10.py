from __future__ import annotations

import live_report_adapter_v9 as v9
from report_engine_v5 import build_premium_property_report_v5

# v9 owns the truthful coverage/narrative payload. Swap only the final renderer so
# the same real data is emitted through the fluent, auto-flowing V5 layout.
v9.build_premium_property_report_v4 = build_premium_property_report_v5

def generate_live_report(result:dict,car_code:str):
    return v9.generate_live_report(result,car_code)

print('RX_LIVE_REPORT_RENDERER=V5_FLUENT',flush=True)

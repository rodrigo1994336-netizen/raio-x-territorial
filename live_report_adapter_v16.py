from __future__ import annotations

import live_report_adapter_v15 as v15
import live_report_adapter_v13 as v13
from report_truth_guard_v16 import comprehensive_truth_guard


_base_guard=v13.v11._final_truth_guard


def _guard(payload:dict,result:dict):
    return comprehensive_truth_guard(payload,result,base_guard=_base_guard)


v13.v11._final_truth_guard=_guard
generate_live_report=v15.generate_live_report

print('RX_LIVE_REPORT_ADAPTER=V16_FINAL_TRUTH_RECONCILIATION',flush=True)

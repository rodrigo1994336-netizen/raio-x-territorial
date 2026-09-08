from __future__ import annotations

import portal_v8

html = portal_v8.PORTAL_HTML

old_unavailable = "if(!d?.ok){const detail=String(d?.detail||'fonte indisponível').replace(/^RuntimeError:/,'');return `<div class=\"rx45-integrity-unavailable\"><strong>Fonte indisponível</strong><br>${esc(detail)}<br>Não foi interpretado como ausência de ocorrência.</div>`}"
new_unavailable = "if(!d?.ok){if(d?.user_message)return `<div class=\"rx45-integrity-unavailable\"><strong>${esc(d.user_message)}</strong></div>`;const detail=String(d?.detail||'fonte indisponível').replace(/^RuntimeError:/,'');return `<div class=\"rx45-integrity-unavailable\"><strong>Fonte indisponível</strong><br>${esc(detail)}<br>Não foi interpretado como ausência de ocorrência.</div>`}"
if html.count(old_unavailable) != 1:
    raise RuntimeError("v48_canonical_ui_unavailable_injection_point_missing")
html = html.replace(old_unavailable, new_unavailable, 1)

old_snapshot = '<div class="rx45-integrity-snapshot">Snapshot SICAR: <b>${esc(datePt(d.snapshot))}</b></div>'
new_snapshot = '<div class="rx45-integrity-snapshot"><b>${esc(d.snapshot_label||(`Base do CAR: ${datePt(d.snapshot)}`))}</b></div>'
if html.count(old_snapshot) != 1:
    raise RuntimeError("v48_canonical_ui_snapshot_injection_point_missing")
html = html.replace(old_snapshot, new_snapshot, 1)

portal_v8.PORTAL_HTML = html
print("RX_PORTAL_CAR_CANONICAL_UI_V48=client_language_exact_date", flush=True)

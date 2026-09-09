from __future__ import annotations

import json

import portal_v8
import sicar_canonical_manifest_v48 as canonical

html = portal_v8.PORTAL_HTML

old_unavailable = "if(!d?.ok){const detail=String(d?.detail||'fonte indisponível').replace(/^RuntimeError:/,'');return `<div class=\"rx45-integrity-unavailable\"><strong>Fonte indisponível</strong><br>${esc(detail)}<br>Não foi interpretado como ausência de ocorrência.</div>`}"
new_unavailable = "if(!d?.ok){if(d?.user_message){let x=`<div class=\"rx45-integrity-unavailable\"><strong>${esc(d.user_message)}</strong>`;if(d?.snapshot_label)x+=`<div class=\"rx45-integrity-snapshot\"><b>${esc(d.snapshot_label)}</b></div>`;if(d?.snapshot_staleness_note)x+=`<div class=\"rx-v48-stale-note\">${esc(d.snapshot_staleness_note)}</div>`;x+=rxV48AuditPanel();return x+'</div>';}const detail=String(d?.detail||'fonte indisponível').replace(/^RuntimeError:/,'');return `<div class=\"rx45-integrity-unavailable\"><strong>Fonte indisponível</strong><br>${esc(detail)}<br>Não foi interpretado como ausência de ocorrência.</div>`}"
if html.count(old_unavailable) != 1:
    raise RuntimeError("v48_canonical_ui_unavailable_injection_point_missing")
html = html.replace(old_unavailable, new_unavailable, 1)

old_snapshot = '<div class="rx45-integrity-snapshot">Snapshot SICAR: <b>${esc(datePt(d.snapshot))}</b></div>'
new_snapshot = '<div class="rx45-integrity-snapshot"><b>${esc(d.snapshot_label||(`Base do CAR: ${datePt(d.snapshot)}`))}</b>${d.snapshot_staleness_note?`<div class="rx-v48-stale-note">${esc(d.snapshot_staleness_note)}</div>`:""}</div>${rxV48AuditPanel()}'
if html.count(old_snapshot) != 1:
    raise RuntimeError("v48_canonical_ui_snapshot_injection_point_missing")
html = html.replace(old_snapshot, new_snapshot, 1)

snapshot_dates = canonical.canonical_dates_for_audit()
dates_json = json.dumps(snapshot_dates, ensure_ascii=False, separators=(",", ":"))
names_json = json.dumps(canonical.UF_NAMES, ensure_ascii=False, separators=(",", ":"))

audit_ui = f'''\n<style>
.rx-v48-stale-note{{margin-top:6px;color:#d9c89c;font-size:12px;line-height:1.4}}
.rx-v48-audit{{margin-top:10px;border-top:1px solid rgba(255,255,255,.1);padding-top:8px}}
.rx-v48-audit summary{{cursor:pointer;font-weight:800;color:#b9d6c8}}
.rx-v48-audit-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:6px;margin-top:9px}}
.rx-v48-audit-row{{padding:7px 8px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(255,255,255,.025);font-size:11px}}
.rx-v48-audit-row b{{display:block;color:#eef8f2;margin-bottom:2px}}
.rx-v48-audit-old{{color:#d9c89c}}
</style>
<script>
const RX_V48_CANONICAL_DATES={dates_json};
const RX_V48_UF_NAMES={names_json};
function rxV48AgeDays(iso){{
  const now=new Date();
  const todayUtc=Date.UTC(now.getFullYear(),now.getMonth(),now.getDate());
  const base=Date.parse(iso+'T00:00:00Z');
  return Math.max(0,Math.floor((todayUtc-base)/86400000));
}}
function rxV48DatePt(iso){{const [y,m,d]=String(iso).split('-');return `${{d}}/${{m}}/${{y}}`}}
function rxV48AuditPanel(){{
  const rows=Object.keys(RX_V48_CANONICAL_DATES).sort().map(uf=>{{
    const iso=RX_V48_CANONICAL_DATES[uf],age=rxV48AgeDays(iso),unit=age===1?'dia':'dias';
    const old=age>60?' rx-v48-audit-old':'';
    return `<div class="rx-v48-audit-row${{old}}"><b>${{RX_V48_UF_NAMES[uf]||uf}} (${{uf}})</b>${{rxV48DatePt(iso)}} · atualizada há ${{age}} ${{unit}}</div>`;
  }}).join('');
  return `<details class="rx-v48-audit"><summary>Ver auditoria · datas das bases por estado</summary><div class="rx-v48-audit-grid">${{rows}}</div></details>`;
}}
</script>
'''
if html.count("</body>") != 1:
    raise RuntimeError("v48_canonical_ui_body_injection_point_missing")
html = html.replace("</body>", audit_ui + "</body>", 1)

portal_v8.PORTAL_HTML = html
print("RX_PORTAL_CAR_CANONICAL_UI_V48=exact_date_dynamic_age_27uf_audit", flush=True)

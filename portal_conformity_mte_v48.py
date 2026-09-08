from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

import portal_v8
import portal_map_panel_v45 as v45
from car_resilient import CAR_RE
from mte_slave_labor_v48 import query_mte_slave_labor

app = portal_v8.app
_BASE_PANEL_SYNC = v45._panel_sync


def _panel_sync_v48(car_code: str) -> dict[str, Any]:
    out = _BASE_PANEL_SYNC(car_code)
    if not out.get("ok"):
        return out

    # Preserve every V45 conformity row exactly as it existed. V48 Source 1 is
    # additive: the MTE row is appended; future sources do not become UI rows
    # until their connector is actually implemented.
    original = list(out.get("compliance_sources") or [])
    out["compliance_sources"] = [
        *original,
        {
            "id": "mte_slave_labor",
            "label": "MTE — Trabalho Escravo",
            "state": "on_demand",
            "reason": "Aguardando consulta nominal. O CAR não fornece CPF/CNPJ do proprietário com segurança.",
        },
    ]

    audit = dict(out.get("source_audit") or {})
    original_total = int(audit.get("total") or (len(original) + int(audit.get("responded") or 0)))
    audit["total"] = original_total + 1
    audit["visible_compliance"] = len(out["compliance_sources"])
    audit["deep_sources_requested"] = False
    out["source_audit"] = audit
    return out


# Existing /v1/live/map-panel/{car_code} endpoint resolves this global at call time.
v45._panel_sync = _panel_sync_v48


@app.get("/v1/live/conformity/mte/{car_code}")
async def conformity_mte_v48(car_code: str):
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        raise HTTPException(status_code=422, detail="invalid_car_format")
    # V48 Source 1 deliberately passes no owner identity. The current public CAR
    # contract does not expose a trustworthy holder CPF/CNPJ and no substitute is inferred.
    return await asyncio.to_thread(query_mte_slave_labor, None, None)


UI = r'''
<style id="rxConformityMteV48">
.rx45-check[data-source="mte_slave_labor"]{align-items:flex-start!important;min-height:54px!important;padding:8px!important;grid-column:1/-1}
.rx48-check-body{min-width:0;display:grid;gap:3px;line-height:1.35}.rx48-check-label{font-weight:850;color:#e5f3eb;font-size:8px}.rx48-check-status{font-size:7px;font-weight:900;letter-spacing:.35px;text-transform:uppercase;color:#a7bbb0}.rx48-check-reason{font-size:7px;color:#91a69b;line-height:1.38}.rx48-check-meta{font-size:6.5px;color:#6f887b;line-height:1.35}.rx45-dot.checking,.rx45-dot.source_failed{background:var(--rx45-yellow)}.rx45-dot.blocked_missing_owner_identity{background:var(--rx45-gray)}.rx45-dot.checked_clear{background:var(--rx45-green)}.rx45-dot.checked_hit{background:var(--rx45-red)}
@media(max-width:390px){.rx45-check[data-source="mte_slave_labor"]{min-height:0!important}.rx48-check-label{font-size:8.5px}.rx48-check-reason{font-size:7.5px}}
</style>
<script>
(function(){
 const q=s=>document.querySelector(s);
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));
 const currentCar=()=>String((window.current||{}).car_code||'').trim().toUpperCase();
 const fmtDate=v=>{if(!v)return 'não informada';const s=String(v);const m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]}`:s};
 const fmtQuery=v=>{if(!v)return 'não realizada';try{return new Date(v).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})}catch(e){return String(v)}};
 function body(el,label,status,reason,meta){el.innerHTML=`<i class="rx45-dot ${esc(el.dataset.state||'not_consulted')}"></i><span class="rx48-check-body"><span class="rx48-check-label">${esc(label)}</span><span class="rx48-check-status">${esc(status)}</span><span class="rx48-check-reason">${esc(reason)}</span>${meta?`<span class="rx48-check-meta">${esc(meta)}</span>`:''}</span>`}
 function baseExplanation(n){return n>=2?'CAR/SICAR + resolução de identidade':n===1?'CAR/SICAR':'nenhuma resposta-base'}
 function updateCounter(card){if(!card)return;const audit=card.querySelector('.rx45-audit-count');if(!audit)return;if(!audit.dataset.rx48Base){const m=(audit.textContent||'').match(/(\d+)\s+de\s+(\d+)/i);audit.dataset.rx48Base=String(m?Number(m[1]):0);audit.dataset.rx48Total=String(m?Number(m[2]):11)}const base=Number(audit.dataset.rx48Base||0),total=Number(audit.dataset.rx48Total||11),answered=card.querySelectorAll('.rx45-check[data-answered="1"]').length,button=audit.querySelector('#rx45Audit');audit.childNodes.forEach(n=>{if(n.nodeType===3)n.remove()});audit.insertBefore(document.createTextNode(`${base+answered} de ${total} fontes responderam · respostas: ${baseExplanation(base)} · `),button||null)}
 function setState(el,state,label,status,reason,meta,answered){el.dataset.state=state;el.dataset.answered=answered?'1':'0';body(el,label,status,reason,meta);const dot=el.querySelector('.rx45-dot');if(dot)dot.className='rx45-dot '+state;updateCounter(el.closest('.rx45-panel-card'))}
 async function loadMte(card){const el=card.querySelector('.rx45-check[data-source="mte_slave_labor"]');if(!el||el.dataset.rx48Loaded==='1')return;el.dataset.rx48Loaded='1';setState(el,'checking','MTE — Trabalho Escravo','CONSULTANDO','Validando a lista oficial do MTE. Nenhum resultado será presumido antes da resposta.','Fonte: Ministério do Trabalho e Emprego',false);const car=card.dataset.car||'';try{const r=await fetch(`/v1/live/conformity/mte/${encodeURIComponent(car)}`),d=await r.json();if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);if(d.state==='blocked_missing_owner_identity'){setState(el,d.state,'MTE — Trabalho Escravo','NÃO VERIFICADA',d.reason,`Fonte: MTE · dado/publicação: ${fmtDate(d.data_date)} · consulta: ${fmtQuery(d.queried_at)}`,false)}else if(d.state==='checked_clear'){setState(el,d.state,'MTE — Trabalho Escravo','SEM OCORRÊNCIA',d.reason,`Fonte: MTE · dado/publicação: ${fmtDate(d.data_date)} · consulta: ${fmtQuery(d.queried_at)}`,true)}else if(d.state==='checked_hit'){setState(el,d.state,'MTE — Trabalho Escravo','OCORRÊNCIA LOCALIZADA',d.reason,`Fonte: MTE · dado/publicação: ${fmtDate(d.data_date)} · consulta: ${fmtQuery(d.queried_at)}`,true)}else{setState(el,'source_failed','MTE — Trabalho Escravo','FONTE INDISPONÍVEL',d.reason||'A consulta não foi concluída. Nenhuma ausência foi presumida.',`Fonte: MTE · consulta: ${fmtQuery(d.queried_at)}`,false)}}catch(e){setState(el,'source_failed','MTE — Trabalho Escravo','FONTE INDISPONÍVEL','A fonte não respondeu de forma utilizável. Nenhuma ausência foi presumida.',`Fonte: MTE · motivo: ${e.message}`,false)}}
 function enhance(card){if(!card||card.dataset.rx48Enhanced==='1')return;const el=card.querySelector('.rx45-check[data-source="mte_slave_labor"]');if(!el)return;card.dataset.rx48Enhanced='1';el.dataset.answered='0';el.dataset.state='on_demand';body(el,'MTE — Trabalho Escravo','NÃO VERIFICADA','Depende de CPF/CNPJ do proprietário; o CAR não fornece esse vínculo com segurança.','Fonte: Ministério do Trabalho e Emprego · dado: aguardando consulta');updateCounter(card);loadMte(card)}
 function enhanceCar(car){if(!car)return;const card=q(`.rx45-panel-card[data-car="${CSS.escape(car)}"]`);if(card)enhance(card)}
 function schedule(car){[80,620,1950,9700,10600].forEach(ms=>setTimeout(()=>{if(currentCar()===car)enhanceCar(car)},ms))}
 function install(){if(window.__rx48MteShowWrapped)return;const base=window.showProperty;if(typeof base!=='function'){const n=(window.__rx48MteInstallAttempts||0)+1;window.__rx48MteInstallAttempts=n;if(n<=4)setTimeout(install,120);return}window.__rx48MteShowWrapped=true;const wrapped=function(...args){const out=base.apply(this,args),p=args?.[0]||{},car=String(p.car_code||p?.properties?.cod_imovel||p?.properties?.id_imovel||currentCar()||'').trim().toUpperCase();if(car)schedule(car);return out};window.showProperty=wrapped;try{showProperty=wrapped}catch(e){}setTimeout(()=>enhanceCar(currentCar()),250)}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
 window.rxV48UpdateComplianceSource=function(id,result){const el=q(`.rx45-check[data-source="${id}"]`);if(!el)return;setState(el,result.state||'not_consulted',result.label||id,result.status||'NÃO CONSULTADA',result.reason||'',result.meta||'',!!result.answered)};
})();
</script>
'''

if "RX_CONFORMITY_MTE_V48" not in portal_v8.PORTAL_HTML:
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", UI + "<!-- RX_CONFORMITY_MTE_V48 --></body>")

print("RX_PORTAL_CONFORMITY_MTE_V48=additive_original_eight_plus_mte_no_future_wall", flush=True)

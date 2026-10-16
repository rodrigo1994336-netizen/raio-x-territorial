"""F2 — linhas MTE e SINAFLOR do painel: carga por evento e memória por CAR.

Antes, as duas linhas só eram carregadas por timers fixos disparados na
seleção. Se o /v1/live/map-panel demorasse mais que ~11 s, nenhum timer achava
a linha e ela ficava "carregando" para sempre; e cada novo desenho do painel
apagava a linha e disparava outra consulta ao SINAFLOR (~8 s cada).

Agora o painel V45 emite ``rx45:panel-rendered`` a cada desenho; MTE e SINAFLOR
escutam esse evento. O resultado fica guardado por CAR e é reaplicado no novo
desenho sem nova consulta. Consulta que não respondeu (depois de UMA nova
tentativa automática) vira "CONSULTA PENDENTE" com "Consultar de novo" — nunca
verde, nunca "sem pendências".

Lado Python (sem rede, sem dado pessoal): classificação pura das respostas das
duas fontes em estado explícito (found / not_found / blocked / pending) e uma
função de payload que o relatório pode usar com o mesmo vocabulário da tela.
"""
from __future__ import annotations

from typing import Any, Callable

EVENT = "rx45:panel-rendered"
MTE_ID = "mte_slave_labor"
SINAFLOR_ID = "sinaflor"

PENDING_STATUS = "CONSULTA PENDENTE"
PENDING_REASON = "A fonte oficial não respondeu agora."

# Only an answer the source itself marked as answered can become found/not_found.
#
# T2 (correção): o título é o que o comprador lê. Um estado "não confirmado" NUNCA vira, no título,
# a afirmação de que não existe; e um estado "limpo" diz em que base nada foi localizado, porque a
# própria razão logo abaixo lembra que outra base pode ter emitido a autorização. Zero não é ausência.
# Estes rótulos são a ÚNICA fonte de verdade: os módulos de tela leem este dicionário (o texto já esteve
# escrito duas vezes, em Python e no JS, e as duas cópias divergiram).
_MTE_ANSWERED = {
    "checked_clear": ("not_found", "NENHUMA OCORRÊNCIA NESTA LISTA"),
    "checked_hit": ("found", "OCORRÊNCIA LOCALIZADA"),
}
_MTE_BLOCKED = {"blocked_missing_owner_identity": ("blocked", "NÃO DÁ PARA RESPONDER PELO CAR")}
_SINAFLOR_ANSWERED = {
    "checked_clear": ("not_found", "NENHUMA AUTORIZAÇÃO LOCALIZADA NESTA BASE"),
    "checked_spatial_record_unconfirmed": ("found", "REGISTRO NA REGIÃO · LIGAÇÃO COM O IMÓVEL NÃO CONFIRMADA"),
    "checked_authorization_overlap": ("found", "AUTORIZAÇÃO SOBRE O IMÓVEL"),
    "checked_authorization_overlap_unconfirmed": ("found", "AUTORIZAÇÃO SOBRE O IMÓVEL · PRAZO NÃO CONFIRMADO"),
}


def status_labels(source_id: str) -> dict[str, str]:
    """Estado interno -> título que o cliente lê. Lido pelos módulos de tela; não duplicar em JS."""
    if source_id == MTE_ID:
        return {state: status for state, (_kind, status) in {**_MTE_ANSWERED, **_MTE_BLOCKED}.items()}
    if source_id == SINAFLOR_ID:
        return {state: status for state, (_kind, status) in _SINAFLOR_ANSWERED.items()}
    raise KeyError(source_id)


def _pending(source_id: str, response: Any) -> dict[str, Any]:
    d = response if isinstance(response, dict) else {}
    return {
        "source": source_id,
        "state": "pending",
        "source_state": "source_failed",
        "answered": False,
        "status": PENDING_STATUS,
        "reason": PENDING_REASON,
        "queried_at": d.get("queried_at"),
    }


def classify_mte(response: Any) -> dict[str, Any]:
    """Pure: MTE response -> explicit state. Anything unrecognised is pending."""
    d = response if isinstance(response, dict) else {}
    state = str(d.get("state") or "")
    if d.get("ok") is True and d.get("answered") is True and state in _MTE_ANSWERED:
        kind, status = _MTE_ANSWERED[state]
        answered = True
    elif d.get("ok") is True and state in _MTE_BLOCKED:
        # Not a failure and not an answer: the source is nominal and the CAR has no safe owner id.
        kind, status = _MTE_BLOCKED[state]
        answered = False
    else:
        return _pending(MTE_ID, d)
    return {
        "source": MTE_ID,
        "state": kind,
        "source_state": state,
        "answered": answered,
        "status": status,
        "reason": str(d.get("reason") or ""),
        "data_date": d.get("data_date"),
        "queried_at": d.get("queried_at"),
    }


def classify_sinaflor(response: Any) -> dict[str, Any]:
    """Pure: SINAFLOR response -> explicit state. Anything unrecognised is pending."""
    d = response if isinstance(response, dict) else {}
    state = str(d.get("state") or "")
    if not (d.get("ok") is True and d.get("answered") is True and state in _SINAFLOR_ANSWERED):
        return _pending(SINAFLOR_ID, d)
    kind, status = _SINAFLOR_ANSWERED[state]
    numbers = [
        str(m.get("authorization_number"))
        for m in (d.get("matches") or [])
        if isinstance(m, dict) and m.get("authorization_number")
    ]
    return {
        "source": SINAFLOR_ID,
        "state": kind,
        "source_state": state,
        "answered": True,
        "status": status,
        "reason": str(d.get("reason") or ""),
        "data_date": d.get("data_date"),
        "queried_at": d.get("queried_at"),
        "match_count": d.get("match_count"),
        # Public act numbers only; enterprise/establishment names are never copied.
        "authorization_numbers": numbers,
    }


def query_panel_sources(
    car_code: str,
    *,
    mte_query: Callable[[], Any],
    sinaflor_query: Callable[[str], Any],
) -> dict[str, Any]:
    """Runs both source queries; an exception is kept as ``None`` (= pending), never as absence."""
    out: dict[str, Any] = {}
    for key, call in (("mte", lambda: mte_query()), ("sinaflor", lambda: sinaflor_query(car_code))):
        try:
            out[key] = call()
        except Exception:  # noqa: BLE001 — a crash of the source is "not answered", nothing else
            out[key] = None
    return out


def panel_sources_payload(responses: dict[str, Any]) -> dict[str, Any]:
    """Fields for the report, same vocabulary as the panel rows."""
    return {
        "conformity_mte": classify_mte(responses.get("mte")),
        "conformity_sinaflor": classify_sinaflor(responses.get("sinaflor")),
    }


RUNTIME_JS = r'''
<script>
(function(){
 if(window.rxPanelSourcesF2)return;
 // One entry per source+CAR. checking -> done | failed. Re-renders repaint from here, never re-query.
 const EVENT='rx45:panel-rendered',DONE_TTL=15*60*1000,FAIL_TTL=60*1000,memo=new Map();
 const norm=c=>String(c||'').trim().toUpperCase(),key=(id,car)=>id+'|'+norm(car);
 function liveCard(car){car=norm(car);if(!car)return null;const sel=`.rx45-panel-card[data-car="${CSS.escape(car)}"]`;return document.querySelector('#rx43SnapshotHost '+sel)||document.querySelector(sel)}
 function usable(e){if(!e)return false;if(e.phase==='checking')return true;return Date.now()-e.at<(e.phase==='done'?DONE_TTL:FAIL_TTL)}
 // query(car,alive) resolves {done:true,data} | {done:false,data?} | {abandoned:true}; it owns its single automatic retry.
 function settle(id,car,query,paint){car=norm(car);if(!car)return null;const k=key(id,car);let e=memo.get(k);if(!usable(e)){e={phase:'checking',at:Date.now(),data:null};memo.set(k,e);const mine=e;Promise.resolve().then(()=>query(car,()=>!!liveCard(car))).catch(()=>({done:false})).then(r=>{if(memo.get(k)!==mine)return;if(r&&r.abandoned){memo.delete(k);return}mine.phase=r&&r.done?'done':'failed';mine.data=(r&&r.data)||null;mine.at=Date.now();const c=liveCard(car);if(c)try{paint(c,mine)}catch(x){}})}const c=liveCard(car);if(c)try{paint(c,e)}catch(x){}return e}
 function forget(id,car){memo.delete(key(id,car))}
 function onRendered(fn){document.addEventListener(EVENT,ev=>{const car=norm(ev&&ev.detail&&ev.detail.car);if(car)fn(car)});document.querySelectorAll('#rx43SnapshotHost .rx45-panel-card[data-car]').forEach(c=>{const car=norm(c.dataset.car);if(car)fn(car)})}
 window.rxPanelSourcesF2={EVENT,settle,forget,liveCard,onRendered,peek:(id,car)=>memo.get(key(id,car))||null};
})();
</script>
<!-- RX_PANEL_SOURCES_F2 -->
'''


def install() -> None:
    import portal_v8

    if "RX_PANEL_SOURCES_F2" not in portal_v8.PORTAL_HTML:
        portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", RUNTIME_JS + "</body>")

"""F1B 1B.8 — acordar o motor do relatório quando o cartão do imóvel abre.

O relatório sai de outro serviço (``portal_pdf_v21.WORKER``: ``RX_REPORT_WORKER_URL`` ou o endereço
padrão do ``raio-x-territorial-report``). No plano grátis ele dorme e leva ~30–50 s para responder
ao primeiro pedido. Antes, só "Ver análise completa" e "PDF" acordavam o motor; quem pedia o relatório
esperava o despertar inteiro.

Agora:

* Navegador: quando um cartão abre (``window.rxV46SelectProperty``), um POST sem corpo para
  ``/v1/live/report-engine/wake``, disparado depois da pintura e sem esperar resposta. No máximo 1 vez a
  cada 10 minutos POR ABA (``sessionStorage``; memória da página se o armazenamento falhar). Nada na
  tela espera por ele nem muda por causa dele.
* Servidor: trava contra abuso, qualquer que seja o número de abas ou de chamadas forjadas —
  no máximo 1 ping ao motor a cada 10 minutos (um ping em andamento também conta) e no máximo
  ``WAKE_DAILY_CAP`` por dia UTC. 10 minutos é menos que os 15 minutos sem tráfego depois dos quais o
  plano grátis adormece: um ping mais recente que isso significa motor acordado ou acordando.
  O ping é ``GET {WORKER}/health`` (rota leve, não toca órgão público), em segundo plano; a resposta
  do portal sai na hora.
* Sem URL do serviço configurada (vazia ou sem http/https): o navegador não recebe o gatilho e o
  servidor responde ``configured: false`` sem nenhum pedido de rede.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from fastapi.responses import JSONResponse

import portal_pdf_v21
import portal_v8

app = portal_v8.app
MARKER = "RX_REPORT_WAKE_F1B"
ROUTE = "/v1/live/report-engine/wake"
WAKE_INTERVAL_S = 600
WAKE_DAILY_CAP = 48
WAKE_TIMEOUT_S = 60.0  # the sleeping worker measured 32 s to first byte (benchmark D1)
CLIENT_GAP_MS = 600_000

STATE: dict[str, Any] = {"last": None, "day": "", "count": 0, "task": None, "sent": 0, "refused": 0, "last_result": None}


def worker_url() -> str:
    url = str(getattr(portal_pdf_v21, "WORKER", "") or "").strip().rstrip("/")
    return url if url.startswith(("https://", "http://")) and len(url) > len("https://") else ""


def _admit(now: float) -> str | None:
    """Server lock. None = one ping may go now; otherwise the reason it may not."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if STATE["day"] != day:
        STATE["day"], STATE["count"] = day, 0
    task = STATE["task"]
    if task is not None and not task.done():
        return "in_flight"
    if STATE["last"] is not None and now - STATE["last"] < WAKE_INTERVAL_S:
        return "recent"
    if STATE["count"] >= WAKE_DAILY_CAP:
        return "daily_cap"
    return None


async def _ping(url: str) -> None:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(WAKE_TIMEOUT_S), follow_redirects=False,
                                     headers={"User-Agent": "Raio-X-Territorial-Portal/F1B-wake"}) as client:
            r = await client.get(url + "/health")
        STATE["last_result"] = f"http_{r.status_code}"
    except Exception as exc:  # a wake that fails changes nothing on screen; the report flow keeps its own wait
        STATE["last_result"] = type(exc).__name__
    # The elapsed time is the worker's wake-up time as seen from the portal (measured, never assumed).
    print(f"RX_REPORT_WAKE_F1B_PING={STATE['last_result']} ms={round((time.monotonic() - t0) * 1000)}", flush=True)


def _launch(url: str) -> None:
    task = asyncio.ensure_future(_ping(url))
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    STATE["task"] = task


async def _start(url: str) -> None:
    """Starts the ping in the background and returns at once: the portal answer never waits for the worker."""
    _launch(url)


@app.post(ROUTE)
async def report_engine_wake():
    url = worker_url()
    if not url:
        return JSONResponse({"ok": True, "configured": False, "sent": False}, headers={"Cache-Control": "no-store"})
    now = time.monotonic()
    reason = _admit(now)
    if reason:
        STATE["refused"] += 1
        return JSONResponse({"ok": True, "configured": True, "sent": False, "reason": reason},
                            headers={"Cache-Control": "no-store"})
    STATE["last"] = now
    STATE["count"] += 1
    STATE["sent"] += 1
    await _start(url)
    return JSONResponse({"ok": True, "configured": True, "sent": True}, status_code=202, headers={"Cache-Control": "no-store"})


WAKE_UI = r'''
<script id="rxReportWakeScriptF1b">
(function(){
 if(window.rxReportWakeF1b)return;
 const ENABLED=__RX_WAKE_ENABLED__,GAP=__RX_WAKE_GAP_MS__,KEY='rx-report-wake-f1b',ROUTE='__RX_WAKE_ROUTE__';
 let memo=0;
 function last(){let t=memo;try{const v=Number(sessionStorage.getItem(KEY));if(Number.isFinite(v)&&v>t)t=v}catch(e){}return t}
 function mark(t){memo=t;try{sessionStorage.setItem(KEY,String(t))}catch(e){}}
 // At most once per GAP per tab. A clock set backwards never blocks forever.
 function wake(){if(!ENABLED)return false;const now=Date.now(),t=last();if(t&&now>=t&&now-t<GAP)return false;mark(now);
  try{const p=fetch(ROUTE,{method:'POST',cache:'no-store',keepalive:true,credentials:'same-origin'});if(p&&typeof p.catch==='function')p.catch(()=>{})}catch(e){}return true}
 window.rxReportWakeF1b={wake,enabled:ENABLED,gap:GAP};
 // Card open = the final rxV46SelectProperty (after V49 sanitize and the W1a link). The wake runs after
 // the card is painted and nothing waits for it.
 let tries=0;
 function hook(){const sel=window.rxV46SelectProperty;if(typeof sel!=='function'){if(++tries<=20)setTimeout(hook,150);return}if(sel.__rxWakeF1b)return;
  const wrapped=function(p){const out=sel.apply(this,arguments);try{if(p&&String(p.car_code||'').trim())setTimeout(wake,0)}catch(e){}return out};
  for(const k of Object.keys(sel))wrapped[k]=sel[k];wrapped.__rxWakeF1b=true;window.rxV46SelectProperty=wrapped}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',hook);else hook();
})();
</script>
<!-- RX_REPORT_WAKE_F1B -->
'''


def ui_html() -> str:
    return (WAKE_UI.replace("__RX_WAKE_ENABLED__", "true" if worker_url() else "false")
            .replace("__RX_WAKE_GAP_MS__", str(int(CLIENT_GAP_MS)))
            .replace("__RX_WAKE_ROUTE__", ROUTE))


if MARKER not in portal_v8.PORTAL_HTML:
    if "<!-- RX_SHARE_LINK_W1A -->" not in portal_v8.PORTAL_HTML or portal_v8.PORTAL_HTML.count("</body>") != 1:
        raise RuntimeError("f1b_report_wake_anchor_missing")
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", ui_html() + "</body>")

print("RX_REPORT_WAKE_F1B=" + json.dumps({"configured": bool(worker_url()), "server_interval_s": WAKE_INTERVAL_S,
                                          "daily_cap": WAKE_DAILY_CAP, "tab_gap_ms": CLIENT_GAP_MS}), flush=True)

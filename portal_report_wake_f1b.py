"""F1B 1B.8 — acordar o motor do relatório quando o cliente mostra que vai usá-lo.

O relatório sai de outro serviço (``portal_pdf_v21.WORKER``: ``RX_REPORT_WORKER_URL`` ou o endereço
padrão do ``raio-x-territorial-report``). No plano grátis ele dorme e leva ~30–50 s para responder
ao primeiro pedido. Antes, só "Ver análise completa" e "PDF" acordavam o motor; quem pedia o relatório
esperava o despertar inteiro.

Agora:

* Navegador: um POST sem corpo para ``/v1/live/report-engine/wake`` só com sinal de intenção — o cartão
  do imóvel continua aberto ``DWELL_MS`` depois de abrir, ou o cliente aponta/foca/toca em "Ver análise
  completa" ou "PDF". Sem esperar resposta; no máximo 1 vez a cada 10 minutos POR ABA (``sessionStorage``;
  memória da página se o armazenamento falhar). Nada na tela espera por ele nem muda por causa dele.
* Servidor: trava contra abuso, qualquer que seja o número de abas ou de chamadas forjadas —
  no máximo 1 ping ao motor a cada 10 minutos (um ping em andamento também conta), nenhum ping se o
  portal falou com o motor nos últimos 10 minutos (ele está acordado), no máximo ``WAKE_DAILY_CAP`` por
  dia UTC e ``WAKE_PER_CLIENT_DAILY`` por cliente (IP informado pelo proxy; forjável, por isso o teto
  global é o que limita o custo: 12 pings ≈ 2 h de motor acordado por dia no pior caso).
  10 minutos é menos que os 15 minutos sem tráfego depois dos quais o plano grátis adormece.
  O ping é ``GET {WORKER}/health`` (rota leve, não toca órgão público), em segundo plano.
* A resposta é sempre a mesma (``{"ok": true}``, 202): não diz se houve ping, trava ou teto.
* Só liga onde o motor de produção é o alvo de propósito: ``RX_REPORT_WORKER_URL`` explícita ou o
  serviço rodando no Render (variável ``RENDER``). Servidor local, smoke e CI não acordam a produção.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

import portal_pdf_v21
# Este módulo só se enxerta depois do bloco do W1a: sem declarar a dependência, o arranque cai com
# f1b_report_wake_anchor_missing quando a ordem de importação muda.
import portal_share_link_w1a  # noqa: F401 - dono da marca <!-- RX_SHARE_LINK_W1A -->
import portal_v8

app = portal_v8.app
MARKER = "RX_REPORT_WAKE_F1B"
ROUTE = "/v1/live/report-engine/wake"
WAKE_INTERVAL_S = 600
ENGINE_RECENT_S = 600
WAKE_DAILY_CAP = 12
WAKE_PER_CLIENT_DAILY = 3
WAKE_TIMEOUT_S = 60.0  # the sleeping worker measured 32 s to first byte (benchmark D1)
CLIENT_GAP_MS = 600_000
DWELL_MS = 4000
# Explicit worker URL, or the Render service itself. A local server, a smoke run or CI never pings production.
WAKE_ENV_ENABLED = bool(os.getenv("RX_REPORT_WORKER_URL", "").strip() or os.getenv("RENDER", "").strip())

STATE: dict[str, Any] = {"last": None, "day": "", "count": 0, "clients": {}, "task": None, "sent": 0, "refused": 0,
                         "last_refusal": None, "last_result": None}


def worker_url() -> str:
    if not WAKE_ENV_ENABLED:
        return ""
    url = str(getattr(portal_pdf_v21, "WORKER", "") or "").strip().rstrip("/")
    return url if url.startswith(("https://", "http://")) and len(url) > len("https://") else ""


def client_key(request: Any) -> str:
    """The caller as the proxy reports it (first X-Forwarded-For hop), else the socket peer."""
    try:
        first = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        peer = request.client.host if getattr(request, "client", None) else ""
        return (first or peer or "?")[:64]
    except Exception:
        return "?"


def _engine_recently_used(now: float) -> bool:
    last = getattr(portal_pdf_v21, "LAST_WORKER_OK_MONO", None)
    return isinstance(last, (int, float)) and now - last < ENGINE_RECENT_S


def _admit(now: float, client: str = "?") -> str | None:
    """Server lock. None = one ping may go now; otherwise the reason it may not (kept in the log, never answered)."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if STATE["day"] != day:
        STATE["day"], STATE["count"], STATE["clients"] = day, 0, {}
    task = STATE["task"]
    if task is not None and not task.done():
        return "in_flight"
    if STATE["last"] is not None and now - STATE["last"] < WAKE_INTERVAL_S:
        return "recent"
    if _engine_recently_used(now):
        return "engine_recent"
    if STATE["count"] >= WAKE_DAILY_CAP:
        return "daily_cap"
    if STATE["clients"].get(client, 0) >= WAKE_PER_CLIENT_DAILY:
        return "client_cap"
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


ANSWER = {"ok": True}


@app.post(ROUTE)
async def report_engine_wake(request: Request):
    url = worker_url()
    if url:
        now = time.monotonic()
        client = client_key(request)
        reason = _admit(now, client)
        if reason:
            STATE["refused"] += 1
            STATE["last_refusal"] = reason
        else:
            STATE["last"] = now
            STATE["count"] += 1
            STATE["clients"][client] = STATE["clients"].get(client, 0) + 1
            STATE["sent"] += 1
            await _start(url)
    # The same answer whatever happened: no lock, cap or configuration state leaks to the caller.
    return JSONResponse(dict(ANSWER), status_code=202, headers={"Cache-Control": "no-store"})


WAKE_UI = r'''
<script id="rxReportWakeScriptF1b">
(function(){
 if(window.rxReportWakeF1b)return;
 const ENABLED=__RX_WAKE_ENABLED__,GAP=__RX_WAKE_GAP_MS__,DWELL=__RX_WAKE_DWELL_MS__,KEY='rx-report-wake-f1b',ROUTE='__RX_WAKE_ROUTE__';
 const INTENT='[data-rx46-action="full"],#rx45Full,#rx45Pdf';
 let memo=0;
 function last(){let t=memo;try{const v=Number(sessionStorage.getItem(KEY));if(Number.isFinite(v)&&v>t)t=v}catch(e){}return t}
 function mark(t){memo=t;try{sessionStorage.setItem(KEY,String(t))}catch(e){}}
 // At most once per GAP per tab. A clock set backwards never blocks forever.
 function wake(){if(!ENABLED)return false;const now=Date.now(),t=last();if(t&&now>=t&&now-t<GAP)return false;mark(now);
  try{const p=fetch(ROUTE,{method:'POST',cache:'no-store',keepalive:true,credentials:'same-origin'});if(p&&typeof p.catch==='function')p.catch(()=>{})}catch(e){}return true}
 const norm=v=>String(v||'').trim().toUpperCase();
 // Still looking at the same property: its card is on screen and it is the current selection.
 function stillOpen(car){try{return norm((window.current||{}).car_code)===car&&!!document.querySelector(`.rx46-card[data-car="${car}"]`)}catch(e){return false}}
 function dwell(car){if(stillOpen(car))wake()}
 window.rxReportWakeF1b={wake,enabled:ENABLED,gap:GAP,dwell:DWELL};
 // Intention, not a glance: the card stays open DWELL ms after the final rxV46SelectProperty (after V49
 // sanitize and the W1a link), or the pointer/focus/finger reaches "Ver análise completa" or "PDF".
 let tries=0;
 function hook(){const sel=window.rxV46SelectProperty;if(typeof sel!=='function'){if(++tries<=20)setTimeout(hook,150);return}if(sel.__rxWakeF1b)return;
  const wrapped=function(p){const out=sel.apply(this,arguments);try{const car=norm(p&&p.car_code);if(car&&ENABLED)setTimeout(()=>dwell(car),DWELL)}catch(e){}return out};
  for(const k of Object.keys(sel))wrapped[k]=sel[k];wrapped.__rxWakeF1b=true;window.rxV46SelectProperty=wrapped}
 function intent(ev){try{const t=ev&&ev.target;if(t&&t.closest&&t.closest(INTENT))wake()}catch(e){}}
 if(ENABLED){for(const type of ['pointerover','focusin','touchstart'])document.addEventListener(type,intent,{capture:true,passive:true})}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',hook);else hook();
})();
</script>
<!-- RX_REPORT_WAKE_F1B -->
'''


def ui_html() -> str:
    return (WAKE_UI.replace("__RX_WAKE_ENABLED__", "true" if worker_url() else "false")
            .replace("__RX_WAKE_GAP_MS__", str(int(CLIENT_GAP_MS)))
            .replace("__RX_WAKE_DWELL_MS__", str(int(DWELL_MS)))
            .replace("__RX_WAKE_ROUTE__", ROUTE))


if MARKER not in portal_v8.PORTAL_HTML:
    if "<!-- RX_SHARE_LINK_W1A -->" not in portal_v8.PORTAL_HTML or portal_v8.PORTAL_HTML.count("</body>") != 1:
        raise RuntimeError("f1b_report_wake_anchor_missing")
    portal_v8.PORTAL_HTML = portal_v8.PORTAL_HTML.replace("</body>", ui_html() + "</body>")

print("RX_REPORT_WAKE_F1B=" + json.dumps({"configured": bool(worker_url()), "server_interval_s": WAKE_INTERVAL_S,
                                          "daily_cap": WAKE_DAILY_CAP, "per_client_daily": WAKE_PER_CLIENT_DAILY,
                                          "tab_gap_ms": CLIENT_GAP_MS, "dwell_ms": DWELL_MS}), flush=True)

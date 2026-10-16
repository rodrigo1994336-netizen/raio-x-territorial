"""O2 browser gate: a tela nova em /novo num Chromium de verdade, sem rede (todas as fontes respondidas aqui).

Sobe só o módulo ``tela_nova_o2`` num app FastAPI vazio (sem o portal) e responde cada /v1/* com dados montados aqui.
Cada cenário é uma regra que a revisão de 15/09/2026 achou quebrada:

  B1_LEITURA_DE_OUTRO   com o "Raio-X em uma olhada" de A aberto, tocar no imóvel B nunca deixa as respostas de A na tela
                        (regra 1 do dono: nunca informação errada) e o endereço, o cartão e o título passam a ser de B.
  B2_PENDENTE_SOZINHA   consulta pendente é tentada de novo sozinha, sem ninguém tocar em "Consultar de novo" (regra 2).
  B3_MUNICIPIO          buscar um município grande termina com imóveis sendo pedidos ou com uma frase na tela, nunca num
                        mapa vazio e mudo.
  B4_VOLTAR             no celular, o Voltar do aparelho fecha a leitura, depois o cartão, e só então sai do site.
  B5_DEITADO            celular deitado (812x375): o cartão cabe na tela e "Ver Raio-X" pode ser tocado; a régua abre com
                        "Concluir" ao alcance.
  B6_MEDIR_COM_CARTAO   no celular, medir com o cartão aberto mostra o painel da medida com "Concluir" ao alcance.
  B7_CONCLUIR_MANTEM    "Concluir" deixa o desenho e os números na tela.
  B8_CODIGO_INEXISTENTE link com código que o SICAR não tem: o código fica no campo e no endereço.

Em todos: nenhuma rolagem horizontal e nenhum erro de página.

Controle positivo: cada cenário roda de novo com o app.js/app.css mutado para o comportamento antigo (a mutação é aplicada no
arquivo servido, pela interceptação do navegador) e TEM de falhar. Mutante que passa derruba o gate.

Rodar: PYTHONPATH=. python scripts/o2_tela_nova_browser_gate.py   (precisa de playwright + chromium)
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

A = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
B = "MG-3120904-EA87F46E3C0A439CA7230CA9574E67A2"
NONE_CODE = "MG-3120904-00000000000000000000000000000000"
VIEW = "-18.8912,-44.1819,16"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL", msg, flush=True)


def rect(w: float, s: float, e: float, n: float) -> dict:
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


# A is a narrow, tall property at the centre of VIEW; B is a wide neighbour just east of it.
GEOM = {A: rect(-44.1822, -18.8960, -44.1816, -18.8864), B: rect(-44.1810, -18.9000, -44.1600, -18.8820)}
PROPS = {
    A: {"cod_imovel": A, "area": 14.795, "municipio": "Curvelo", "uf": "MG", "m_fiscal": 0.3699, "status_imovel": "AT", "tipo_imovel": "IRU", "condicao": "Aguardando análise", "dat_criacao": "2018-11-28T12:04:45Z"},
    B: {"cod_imovel": B, "area": 321.5, "municipio": "Curvelo", "uf": "MG", "m_fiscal": 8.0375, "status_imovel": "AT", "tipo_imovel": "IRU", "condicao": "Aguardando análise", "dat_criacao": "2019-02-11T10:00:00Z"},
}


def analysis(code: str, pending: bool) -> dict:
    svc = lambda ok=True: {"ok": ok, "occurrence_count": 0 if ok else None, "area_unique_ha": 0 if ok else None}  # noqa: E731
    services = {k: svc() for k in ("terra_indigena", "unidade_conservacao", "quilombola", "assentamento", "floresta_publica")}
    services["embargo_icmbio"] = svc(not pending)
    return {
        "car": {"ok": True, "properties": PROPS[code]},
        "prodes": {"reading": {"state": "not_found", "complete": True, "inside": {"state": "not_found", "count": 0}, "post_cutoff_inside": {"count": 0}}},
        "embargos_ibama": {"ok": True, "exact": {"available": True, "occurrence_count": 0, "area_unique_ha": 0}},
        "autos_ibama": {"ok": True, "occurrence_count": 0, "feature_count_bbox": 3},
        "anm": {"ok": True, "exact": {"available": True, "occurrence_count": 0, "area_unique_ha": 0}},
        "territorial_constraints": {"ok": True, "services": services},
        "water_mg": {"ok": True, "inside_count": 0},
        "pivots_ana": {"ok": True, "intersection_count": 0, "reference_year": 2022, "feature_count_bbox": 0, "parsed_feature_count": 0},
        "climate_nasa": {"ok": True, "rain_sum_mm": 33.0, "temp_avg_c": 25.9, "available_days": 30, "period_start": "20260815", "period_end": "20260913"},
        "ide_layers": {},
    }


class Fixtures:
    """The answers of every /v1 endpoint the page uses, with per-scenario counters."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def count(self, key: str) -> int:
        self.calls[key] = self.calls.get(key, 0) + 1
        return self.calls[key]

    def answer(self, path: str, query: dict) -> tuple[int, object]:
        if path == "/v1/bootstrap/state":
            return 200, {"ready": True}
        if path == "/v1/live/sicar/viewport-v46":
            self.count("viewport")
            feats = [{"type": "Feature", "geometry": GEOM[c], "properties": PROPS[c]} for c in (A, B)]
            return 200, {"type": "FeatureCollection", "features": feats, "truncated": False, "partial_failures": 0}
        if path.startswith("/v1/live/car/"):
            code = unquote(path.rsplit("/", 1)[1]).upper()
            if code in GEOM:
                return 200, {"car": {"ok": True, "geometry": GEOM[code], "properties": PROPS[code]}}
            return 404, {"detail": {"car": {"not_found": True}}}
        if path.startswith("/v1/live/map-panel/"):
            code = unquote(path.rsplit("/", 1)[1]).upper()
            return 200, {"ok": True, "car_code": code, "validated_name_state": "unresolved", "panel_name_eligible": False}
        if path.startswith("/v1/live/quick/"):
            code = unquote(path.rsplit("/", 1)[1]).upper()
            n = self.count("quick:" + code)
            # First answer with one base that did not respond; the engine produced it 200 s ago (its cache already expired).
            an = analysis(code, pending=(n == 1))
            produced = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat().replace("+00:00", "Z")
            return 200, {"ok": True, "mode": "quick-cache", "analysis": an, "deep_state": {"state": "ready", "analysis": an, "completed_at": produced}}
        if path.startswith("/v1/live/progressive/status/"):
            code = unquote(path.rsplit("/", 1)[1]).upper()
            return 200, {"state": "ready", "analysis": analysis(code, pending=False)}
        if path == "/v1/live/cities":
            import municipios_ibge_br

            q = (query.get("q") or [""])[0]
            return 200, {"ok": True, "items": [municipios_ibge_br.city_item(x) for x in municipios_ibge_br.search(q, limit=6)]}
        if path == "/v1/live/resolve":
            return 404, {"detail": "Nenhum imóvel neste ponto."}
        if path == "/v1/live/report-engine/wake":
            return 200, {"ok": True}
        return 404, {"detail": "Not Found"}


# ------------------------------------------------------------------ server (module only, no portal)
def start_server():
    import uvicorn
    from fastapi import FastAPI

    import tela_nova_o2

    app = FastAPI()
    tela_nova_o2.install(app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise RuntimeError("server did not start")


# ------------------------------------------------------------------ browser helpers
HIT = """(sel) => { const el = document.querySelector(sel); if (!el || el.closest('[hidden]')) return 'missing';
  el.scrollIntoView({block: 'nearest'}); const r = el.getBoundingClientRect(); if (!r.width || !r.height) return 'no size';
  const x = r.left + r.width / 2, y = r.top + r.height / 2; if (y < 0 || y > innerHeight || x < 0 || x > innerWidth) return 'off screen';
  const t = document.elementFromPoint(x, y); return t && (t === el || el.contains(t)) ? 'ok' : 'covered by ' + (t ? (t.id || t.className || t.tagName) : 'nothing'); }"""


class Run:
    def __init__(self, browser, base: str, mutation: dict | None, width: int, height: int, mobile: bool):
        self.fx = Fixtures()
        self.errors: list[str] = []
        self.problems: list[str] = []
        self.mutation = mutation or {}
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, device_scale_factor=1, locale="pt-BR", timezone_id="America/Sao_Paulo")
        self.ctx.route("**/*", self.route)
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.base = base
        self.mobile = mobile

    def route(self, route):
        url = urlparse(route.request.url)
        if url.hostname == "server.arcgisonline.com":
            return route.fulfill(status=200, content_type="image/png", body=PNG)
        if url.hostname != "127.0.0.1":
            return route.abort()
        path = url.path
        for kind in ("app.js", "app.css"):
            stem, ext = kind.split(".")
            if path.startswith(f"/novo/a/{stem}.") and path.endswith("." + ext) and self.mutation.get(kind):
                resp = route.fetch()
                text = resp.text()
                for old, new in self.mutation[kind]:
                    if text.count(old) != 1:
                        self.problems.append(f"MUTATION_ANCHOR {kind}: {old[:70]!r}")
                        continue
                    text = text.replace(old, new)
                return route.fulfill(response=resp, body=text)
        if path.startswith("/v1/"):
            status, data = self.fx.answer(path, parse_qs(url.query))
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
        return route.continue_()

    def js(self, expr: str, arg=None):
        return self.page.evaluate(expr, arg) if arg is not None else self.page.evaluate(expr)

    def until(self, expr: str, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(100)
        return False

    def check(self, ok: bool, msg: str) -> None:
        if not ok:
            self.problems.append(msg)

    def open(self, path: str, ready: str = "!!window.RXO2_METRICS") -> None:
        self.page.goto(self.base + path, wait_until="domcontentloaded")
        self.check(self.until(ready, 15), f"page not ready: {path}")

    def tap(self, x: float, y: float) -> None:
        if self.mobile:
            self.page.touchscreen.tap(x, y)
        else:
            self.page.mouse.click(x, y)

    def hit(self, sel: str) -> str:
        return self.js(HIT, sel)

    def finish(self) -> list[str]:
        try:
            self.check(not self.js("document.documentElement.scrollWidth > innerWidth + 1"), "horizontal scroll")
        except Exception as exc:  # the page left the site (B4 mutant) -> counts as a problem already
            self.problems.append(f"page gone: {type(exc).__name__}")
        self.problems += self.errors
        self.ctx.close()
        return self.problems


CARD_READY = "!!document.querySelector('#cartao:not([hidden]) [data-acao=\"abrir-leitura\"]')"
READING_READY = "!!document.querySelector('#leituraCorpo .perguntas:not(.carregando)')"


def b1_leitura_de_outro(browser, base, mutation=None):
    r = Run(browser, base, mutation, 1440, 900, False)
    try:
        r.open(f"/novo/imovel/{A}?v={VIEW}", CARD_READY)
        r.page.click('[data-acao="abrir-leitura"]')
        r.check(r.until(READING_READY, 10), "reading of A did not render")
        r.check("14,80 ha" in r.js("document.querySelector('#leitura').textContent"), "reading of A without A's area")
        r.page.wait_for_timeout(600)
        r.tap(1300, 500)  # B, on the part of the map the reading leaves visible
        r.until(f"location.pathname === '/novo/imovel/{B}'", 5)
        r.page.wait_for_timeout(1200)  # the panel answer for B arrives and repaints
        state = r.js("""({path: location.pathname, title: document.title, reading: document.querySelector('#leitura').hidden ? '' : document.querySelector('#leitura').textContent,
            readingCar: document.querySelector('#leitura').dataset.car || '', card: document.querySelector('#cartao').hidden ? '' : (document.querySelector('#cartao').dataset.car || '')})""")
        r.check(state["path"] == f"/novo/imovel/{B}", f"address is not B: {state['path']}")
        r.check(B in state["title"], "title is not B")
        r.check("14,80 ha" not in state["reading"], "answers of A still on screen after selecting B")
        r.check(state["card"] == B or (state["readingCar"] == B and A not in state["reading"]), f"neither the card nor the reading is B: {state['card']!r} {state['readingCar']!r}")
    except Exception as exc:
        r.problems.append(f"exception: {exc}")
    return r.finish()


def b2_pendente_sozinha(browser, base, mutation=None):
    r = Run(browser, base, mutation, 1440, 900, False)
    try:
        r.open(f"/novo/imovel/{A}?v={VIEW}", CARD_READY)
        r.page.click('[data-acao="abrir-leitura"]')
        r.check(r.until(READING_READY, 10), "reading did not render")
        end = time.time() + 8
        while time.time() < end and r.fx.calls.get("quick:" + A, 0) < 2:
            r.page.wait_for_timeout(150)
        r.check(r.fx.calls.get("quick:" + A, 0) >= 2, f"pending source not asked again by itself (quick calls: {r.fx.calls.get('quick:' + A, 0)})")
        r.check(r.until("!document.querySelector('#leituraCorpo .pergunta.pendente')", 6), "pending row still pending after the automatic retry")
    except Exception as exc:
        r.problems.append(f"exception: {exc}")
    return r.finish()


def b3_municipio(browser, base, mutation=None):
    r = Run(browser, base, mutation, 1440, 900, False)
    try:
        r.open("/novo?v=-15.0,-50.0,5")
        r.page.fill("#q", "Uberaba")
        r.page.press("#q", "Enter")
        r.page.wait_for_timeout(2500)
        said = r.js("document.querySelector('#situacao').hidden ? '' : document.querySelector('#situacao').textContent.trim()")
        asked = r.fx.calls.get("viewport", 0)
        r.check(asked > 0 or bool(said), "municipality ends on an empty, silent map (no parcels asked, no message)")
    except Exception as exc:
        r.problems.append(f"exception: {exc}")
    return r.finish()


def b4_voltar(browser, base, mutation=None):
    r = Run(browser, base, mutation, 375, 812, True)
    try:
        r.open(f"/novo?v={VIEW}", "!!(window.RXO2_METRICS && window.RXO2_METRICS.imoveisDesenhados)")
        box = r.js("(() => { const b = document.getElementById('mapa').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()")
        r.tap(box[0], box[1])
        r.check(r.until(CARD_READY, 8), "tap on A did not open the card")
        r.page.click('[data-acao="abrir-leitura"]')
        r.check(r.until("!document.querySelector('#leitura').hidden", 5), "reading did not open")
        r.page.wait_for_timeout(300)
        seen = r.js("(() => { const t = document.elementFromPoint(innerWidth / 2, innerHeight / 2); return !!(t && t.closest('#leitura')); })()")
        r.check(seen, "reading is open in the page but not on screen (covered or not painted)")
        r.page.go_back()
        r.until("document.querySelector('#leitura').hidden && !document.querySelector('#cartao').hidden", 4)
        s1 = r.js("({path: location.pathname, reading: !document.querySelector('#leitura').hidden, card: !document.querySelector('#cartao').hidden})")
        r.check(s1 == {"path": f"/novo/imovel/{A}", "reading": False, "card": True}, f"first Back did not return to the card: {s1}")
        r.page.go_back()
        r.until("location.pathname === '/novo' && document.querySelector('#cartao').hidden", 4)
        s2 = r.js("({path: location.pathname, card: !document.querySelector('#cartao').hidden})")
        r.check(s2 == {"path": "/novo", "card": False}, f"second Back did not return to the map: {s2}")
    except Exception as exc:
        r.problems.append(f"exception: {type(exc).__name__}: {str(exc)[:160]}")
    return r.finish()


def b5_deitado(browser, base, mutation=None):
    r = Run(browser, base, mutation, 812, 375, True)
    try:
        r.open(f"/novo/imovel/{A}?v={VIEW}", CARD_READY)
        r.page.wait_for_timeout(600)
        card = r.js("(() => { const b = document.getElementById('cartao').getBoundingClientRect(); return [b.top, b.bottom]; })()")
        r.check(card[1] <= 376 and card[0] >= 0, f"card does not fit the screen: {card}")
        r.check(r.hit('[data-acao="abrir-leitura"]') == "ok", f"Ver Raio-X not reachable: {r.hit('[data-acao=\"abrir-leitura\"]')}")
        r.page.click('[data-ferramenta="medir"]', timeout=3000)
        r.page.wait_for_timeout(500)
        r.check(r.hit('[data-acao="concluir-medida"]') == "ok", f"Concluir not reachable: {r.hit('[data-acao=\"concluir-medida\"]')}")
    except Exception as exc:
        r.problems.append(f"exception: {type(exc).__name__}: {str(exc)[:160]}")
    return r.finish()


def b6_medir_com_cartao(browser, base, mutation=None):
    r = Run(browser, base, mutation, 375, 812, True)
    try:
        r.open(f"/novo/imovel/{A}?v={VIEW}", CARD_READY)
        r.page.click('[data-ferramenta="medir"]', timeout=3000)
        r.page.wait_for_timeout(800)
        r.check(r.hit('[data-acao="concluir-medida"]') == "ok", f"measure panel hidden or covered: {r.hit('[data-acao=\"concluir-medida\"]')}")
    except Exception as exc:
        r.problems.append(f"exception: {type(exc).__name__}: {str(exc)[:160]}")
    return r.finish()


def b7_concluir_mantem(browser, base, mutation=None):
    r = Run(browser, base, mutation, 1440, 900, False)
    try:
        r.open(f"/novo?v={VIEW}")
        r.page.click('[data-ferramenta="medir"]')
        for x, y in ((600, 380), (820, 360), (760, 620)):
            r.page.mouse.click(x, y)
            r.page.wait_for_timeout(120)
        r.page.click('[data-acao="concluir-medida"]')
        r.page.wait_for_timeout(400)
        state = r.js("({paths: document.querySelectorAll('.leaflet-medida-pane path').length, panel: !document.querySelector('#medida').hidden, value: document.querySelector('#medidaValor').textContent})")
        r.check(state["paths"] > 0 and state["panel"] and " ha" in state["value"], f"Concluir erased the measure: {state}")
    except Exception as exc:
        r.problems.append(f"exception: {exc}")
    return r.finish()


def b8_codigo_inexistente(browser, base, mutation=None):
    r = Run(browser, base, mutation, 1440, 900, False)
    try:
        r.open(f"/novo/imovel/{NONE_CODE}")
        r.check(r.until("/não tem imóvel/.test(document.querySelector('#situacao').textContent)", 10), "not-found message did not appear")
        r.page.wait_for_timeout(900)
        state = r.js("({q: document.querySelector('#q').value, path: location.pathname})")
        r.check(state == {"q": NONE_CODE, "path": f"/novo/imovel/{NONE_CODE}"}, f"failed code lost from the box or the address: {state}")
    except Exception as exc:
        r.problems.append(f"exception: {exc}")
    return r.finish()


SCENARIOS = {
    "B1_LEITURA_DE_OUTRO": b1_leitura_de_outro,
    "B2_PENDENTE_SOZINHA": b2_pendente_sozinha,
    "B3_MUNICIPIO": b3_municipio,
    "B4_VOLTAR": b4_voltar,
    "B5_DEITADO": b5_deitado,
    "B6_MEDIR_COM_CARTAO": b6_medir_com_cartao,
    "B7_CONCLUIR_MANTEM": b7_concluir_mantem,
    "B8_CODIGO_INEXISTENTE": b8_codigo_inexistente,
}

# Each mutant puts back the behaviour the review found (15/09/2026). Anchors must exist exactly once in the served file.
MUTANTS = [
    ("B1_LEITURA_DE_OUTRO", "reading kept open for another property", {"app.js": [
        ("    if (readingOpen) closeReading(true);\n", ""),
        ("    if (S.sel && el.dataset.car === S.sel.code) return true;", "    return true;"),
    ]}),
    ("B2_PENDENTE_SOZINHA", "pending waits for a tap", {"app.js": [
        ("    if (entry.phase === 'ready' && hasPending(entry.rows)) refill(code, entry);\n", ""),
    ]}),
    ("B3_MUNICIPIO", "fit to a zoom without parcels and wipe the message", {"app.js": [
        ("      if (z >= MIN_Z) map.fitBounds(box, { maxZoom: 13, padding: [20, 20] });", "      if (true) map.fitBounds(box, { maxZoom: 13, padding: [20, 20] });"),
        ("    if (S.sel) closeCard();\n  }\n  var carSeq = 0;", "    say('');\n    if (S.sel) closeCard();\n  }\n  var carSeq = 0;"),
    ]}),
    ("B4_VOLTAR", "address bar only replaced", {"app.js": [
        ("    if (!sync) remember('leitura', S.sel.code, 'push');", "    if (!sync) remember('leitura', S.sel.code, 'replace');"),
        ("remember('cartao', f.code, cur === 'cartao' || cur === 'leitura' ? 'replace' : 'push'); }", "remember('cartao', f.code, 'replace'); }"),
    ]}),
    ("B5_DEITADO", "desktop card on a short screen", {"app.js": [
        ("'(max-width: 639px), (max-height: 520px)'", "'(max-width: 639px)'"),
        ("sheet = el.offsetHeight > size.y - 100; }", "sheet = false; }"),
    ]}),
    ("B6_MEDIR_COM_CARTAO", "measure hidden under the card", {"app.js": [
        ("    if (S.sel && !$('#cartao').hidden && $('#cartao').classList.contains('folha')) closeCard();\n", ""),
    ], "app.css": [
        ("@media (prefers-reduced-motion:reduce){", "body.com-cartao .medida{display:none}\n@media (prefers-reduced-motion:reduce){"),
    ]}),
    ("B4_VOLTAR", "reading panel rule lost (stray brace)", {"app.css": [
        (".leitura{position:absolute;z-index:1100;", "}\n.leitura{position:absolute;z-index:1100;"),
    ]}),
    ("B7_CONCLUIR_MANTEM", "Concluir clears", {"app.js": [
        ("    setMeasure('done'); drawMeasure();", "    clearMeasure();"),
    ]}),
    ("B8_CODIGO_INEXISTENTE", "failed code dropped", {"app.js": [
        ("function currentPath() { var code = S.sel ? S.sel.code : S.wantCar;", "function currentPath() { var code = S.sel ? S.sel.code : null;"),
        ("    S.wantCar = code;\n    $('#q').value = code;\n", "    S.wantCar = code;\n"),
    ]}),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    server, thread, base = start_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
            for sid, fn in SCENARIOS.items():
                problems = fn(browser, base)
                if problems:
                    fail(f"{sid}: {problems[:4]}")
                else:
                    print(f"ok: {sid}", flush=True)
            for sid, label, mutation in MUTANTS:
                problems = SCENARIOS[sid](browser, base, mutation)
                anchors = [x for x in problems if x.startswith("MUTATION_ANCHOR")]
                if anchors:
                    fail(f"CONTROL {sid} ({label}): {anchors}")
                elif not problems:
                    fail(f"CONTROL {sid} ({label}) was not caught")
                else:
                    print(f"control ok: {sid} ({label}) -> {problems[0][:90]}", flush=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    if FAILS:
        print(f"O2_TELA_NOVA_BROWSER_GATE=FAIL {len(FAILS)}")
        return 1
    print(f"O2_TELA_NOVA_BROWSER_GATE=OK scenarios:{len(SCENARIOS)} controls:{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

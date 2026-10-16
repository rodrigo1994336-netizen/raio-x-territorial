"""F2 · alertas validados de desmatamento — gate offline com respostas reais capturadas em 13/09/2026.

Fixtures (scripts/fixtures/f2_mapbiomas_alerta/):
- mba_curvelo_vazio.json        CAR de teste de Curvelo: a fonte conhece o CAR, lista vazia;
- mba_controle_pre_corte.json   vizinho em Curvelo com o alerta 1349329 (detectado 09/12/2024);
- mba_recente_pos_corte.json    CAR em Curvelo com o alerta 1525843 (detectado 15/09/2025);
- mba_inexistente.json          código válido que a fonte não conhece (ruralProperty: null);
- car_geometrias_mapbiomas.json geometria dos três CAR (versão cruzada pela fonte);
- inpe_*.json                   DETER Cerrado e PRODES Cerrado por bbox desses CAR.

Valida a regra, nunca o valor ao vivo: estados, pendência nunca vira "nenhum alerta",
decisão do dono (cliente não vê nome da fonte, laudo nem número; crédito só na página
"Fontes consultadas"), contrato sem exceção, prazo total, intervalo depois de 429,
caminho de produção sem injeção e a regra da seção 5 do relatório 03 (a mesma abertura
no DETER, nos alertas validados e no PRODES conta uma vez).
"""

from __future__ import annotations

import asyncio
import copy
import json
import socket
import threading
import time
from pathlib import Path

import httpx
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import transform, unary_union

import mapbiomas_alerta as m

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "scripts" / "fixtures" / "f2_mapbiomas_alerta"
FORBIDDEN = ("sem desmatamento", "área regular", "sem alertas", "sem alerta", "sem pendências", "em acordo", "regular")
# Decisão do dono (13/09/2026): nada disso chega ao cliente fora de sources_page_credits.
SOURCE_LEAKS = ("mapbiomas", "plataforma.alerta", "laudo", "graphql", "api/v2", "1349329", "1525843", "_geometry_wkt", "multipolygon")
OWNER_CREDIT = (
    "MapBiomas Alerta — CC BY-SA 3.0 BR; dados recortados e combinados pelo Raio-X; "
    "material adaptado sob a mesma licença"
)
CHECKS: list[str] = []


def ok(name: str) -> None:
    CHECKS.append(name)


def load(name: str) -> dict:
    return json.loads((FX / name).read_text(encoding="utf-8"))


def body_of(name: str) -> tuple[str, dict]:
    raw = load(f"mba_{name}.json")
    return raw["_meta"]["car_code"], raw["response"]


CARS = load("car_geometrias_mapbiomas.json")["cars"]


def strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings(v)]
    return []


def text_of(result: dict) -> str:
    return " ".join(strings(result.get("text") or {}))


def assert_clean_public(result: dict) -> None:
    pub = m.public_view(result)
    credits = pub.pop("sources_page_credits", None)
    dumped = json.dumps(pub, ensure_ascii=False).lower()
    leaked = [w for w in SOURCE_LEAKS if w in dumped]
    assert not leaked, f"nome da fonte, laudo ou dado interno na saída pública: {leaked}"
    assert "audit_sum" not in dumped and "witnesses" not in dumped, "auditoria vazou para a saída pública"
    for credit in credits or []:
        assert set(credit) == {"text", "license_url"}, credit
        assert credit["text"] in (m.SOURCES_PAGE_CREDIT, m.DETER_SOURCES_PAGE_CREDIT), credit
    low = text_of(result).lower()
    bad = [w for w in FORBIDDEN if w in low]
    assert not bad, f"texto proibido: {bad} em {text_of(result)!r}"


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ leitura real
def parse_real() -> dict:
    assert m.SOURCES_PAGE_CREDIT == OWNER_CREDIT
    assert m.PENDING_TEXT == "Alertas validados: consulta pendente."
    code, body = body_of("curvelo_vazio")
    empty = m.parse_response(body, code, CARS["curvelo_vazio"]["geometry"])
    assert empty["state"] == "not_found" and empty["answered"] is True, empty
    assert empty["alert_count"] == 0 and empty["alerts"] == [], empty
    assert empty["last_publication_date"] == "2026-09-08" and empty["max_detected_date"] == "2026-07-31", empty
    # Cobertura pela data máxima de detecção, não pela data do lote.
    assert empty["text"]["summary"] == "Nenhum alerta de desmatamento validado sobre este CAR (detecções até 31/07/2026).", empty
    assert empty["sources_page_credits"] == [{"text": OWNER_CREDIT, "license_url": m.LICENSE_URL}], empty
    assert "disclaimer" not in empty
    assert_clean_public(empty)
    ok("curvelo_lista_vazia=not_found_cobertura_por_deteccao")

    code, body = body_of("controle_pre_corte")
    hit = m.parse_response(body, code, CARS["controle_pre_corte"]["geometry"])
    assert hit["state"] == "found" and hit["alert_count"] == 1, hit
    a = hit["alerts"][0]
    assert a["_alert_code"] == 1349329 and a["detected_at"] == "2024-12-09" and a["published_at"] == "2025-02-18", a
    # Interseção nossa contra o cruzamento publicado pela fonte (11,84366 ha): mesma geometria, mesma área.
    assert a["area_in_car_method"] == "intersecao_raio_x" and abs(a["area_in_car_ha"] - 11.84366) / 11.84366 < 0.005, a
    assert a["area_alert_ha"] == 13.1558 and a["biomes"] == ["Cerrado"], a
    assert a["_alert_sources_label"] == "DETER Cerrado, SAD Cerrado", a
    # Laudo fica só na visão interna do servidor.
    assert a["_laudo_car_url"] == "https://plataforma.alerta.mapbiomas.org/alerta/1349329/car/" + code, a
    assert a["_laudo_url"] == "https://plataforma.alerta.mapbiomas.org/alerta/1349329", a
    assert hit["_source_label"] == "MapBiomas Alerta" and hit["_source_id"] == "mapbiomas_alerta"
    assert hit["disclaimer"] == m.DISCLAIMER and hit["text"]["disclaimer"] == m.DISCLAIMER
    assert "não é auto de infração" in m.DISCLAIMER and "conferida por analistas" in m.DISCLAIMER
    assert hit["text"]["summary"] == "1 alerta de desmatamento validado sobre o imóvel.", hit["text"]
    line = hit["text"]["items"][0]["text"]
    assert line.startswith("Alerta de desmatamento validado") and "11,84 ha dentro do imóvel" in line and "09/12/2024" in line, line
    assert set(hit["text"]["items"][0]) == {"text"}, hit["text"]["items"][0]
    assert_clean_public(hit)
    assert "_geometry_wkt" in a, "combinação precisa da geometria interna"
    ok("controle_1349329=found_texto_neutro_laudo_so_interno")

    # Área geodésica de MultiPolygon com partes em sentidos opostos (o alerta 1349329 vem
    # assim): cada parte orientada, bate com a área publicada pela fonte (13,1558 ha).
    raw_alert = shapely_wkt.loads(body["data"]["ruralProperty"]["alerts"][0]["geometryWkt"])
    assert {p.exterior.is_ccw for p in raw_alert.geoms} == {True, False}, "fixture precisa ter as duas orientações"
    assert abs(m._area_ha(m._geometry(raw_alert)) - 13.1558) < 0.01, m._area_ha(m._geometry(raw_alert))
    # Furo no mesmo sentido da casca (GeoJSON e WKT não garantem sentido): é descontado, não somado.
    outer = box(-44.36, -18.94, -44.32, -18.90)
    inner = box(-44.35, -18.93, -44.33, -18.91)
    holed = Polygon(outer.exterior.coords, [inner.exterior.coords])
    assert holed.is_valid and holed.exterior.is_ccw == holed.interiors[0].is_ccw, "cenário de furo no mesmo sentido"
    expected = m._area_ha(outer) - m._area_ha(inner)
    assert abs(m._area_ha(holed) - expected) < 0.01 * expected, (m._area_ha(holed), expected)
    ok("area_de_multipoligono_e_furo_em_qualquer_sentido=area_geodesica_certa")

    no_geom = m.parse_response(body, code)
    b = no_geom["alerts"][0]
    assert b["area_in_car_method"] == "cruzamento_da_fonte" and b["area_in_car_ha"] == 11.8437, b
    ok("sem_geometria=usa_cruzamento_da_fonte")

    code, body = body_of("recente_pos_corte")
    recent = m.parse_response(body, code, CARS["recente_pos_corte"]["geometry"])
    assert recent["state"] == "found" and recent["alerts"][0]["_alert_code"] == 1525843, recent
    assert recent["alerts"][0]["detected_at"] == "2025-09-15"
    ok("recente_1525843=found")

    code, body = body_of("inexistente")
    missing = m.parse_response(body, code)
    assert missing["state"] == "pending" and missing["pending_reason"] == "car_not_in_source", missing
    assert missing["answered"] is False and missing["alert_count"] is None, missing
    assert missing["text"]["summary"] == m.PENDING_TEXT and "nenhum" not in text_of(missing).lower()
    assert m.audit_state(missing) == "FAILED" and m.audit_state(empty) == "ANSWERED_CLEAR" and m.audit_state(hit) == "ANSWERED_HIT"
    assert_clean_public(missing)
    ok("car_fora_da_base=pending_nunca_sem_alertas")
    return {"empty": empty, "hit": hit, "recent": recent}


def parse_rules() -> None:
    code, body = body_of("controle_pre_corte")
    other_geom = CARS["curvelo_vazio"]["geometry"]

    # Alerta que a fonte liga ao CAR mas não cruza o nosso desenho: fica, com marca.
    other = m.parse_response(body, code, other_geom, car_updated_at="2026-01-10")
    assert other["state"] == "found" and other["alert_count"] == 1, other
    oa = other["alerts"][0]
    assert oa.get("outside_current_geometry") is True and oa.get("on_previous_car_version") is True, oa
    assert "area_in_car_ha" not in oa and oa["area_in_car_source_ha"] == 11.8437, oa
    t = text_of(other)
    assert "versão anterior do CAR" in t and "não cruza o desenho atual do imóvel" in t and "nenhum" not in t.lower(), t
    assert other["text"]["summary"].startswith(
        "1 alerta de desmatamento validado sobre versão anterior do CAR (27/09/2025), fora do desenho atual do imóvel."), other["text"]
    assert "sobre o imóvel" not in other["text"]["summary"], other["text"]
    assert_clean_public(other)
    same_version = m.parse_response(body, code, other_geom)
    sv = same_version["alerts"][0]
    assert same_version["state"] == "found" and sv.get("outside_current_geometry") and not sv.get("on_previous_car_version"), sv
    assert "ligado a este CAR" in text_of(same_version) and "versão anterior" not in text_of(same_version)
    no_row = copy.deepcopy(body)
    no_row["data"]["ruralProperty"]["alerts"][0]["crossedRuralProperties"] = []
    r = m.parse_response(no_row, code, other_geom)
    assert r["state"] == "found" and r["alerts"][0].get("outside_current_geometry"), r
    ok("alerta_da_fonte_fora_do_desenho_atual=mantido_com_marca")

    assert m.parse_response(body, CARS["recente_pos_corte"]["car_code"])["pending_reason"] == "car_code_mismatch"
    ok("codigo_diferente=pending")

    token = {"data": None, "errors": [{"message": "Token de acesso inválido", "path": ["ruralProperty"]}]}
    assert m.parse_response(token, code)["pending_reason"] == "auth_required"
    token2 = {"data": {"ruralProperty": None}, "errors": [{"message": "Token de acesso inválido"}]}
    assert m.parse_response(token2, code)["pending_reason"] == "auth_required"
    ok("token_exigido=pending_sem_criar_conta")

    bad = copy.deepcopy(body)
    bad["data"]["ruralProperty"]["alerts"] = None
    assert m.parse_response(bad, code)["pending_reason"] == "schema_unexpected"
    broken = copy.deepcopy(body)
    for al in broken["data"]["ruralProperty"]["alerts"]:
        al.pop("geometryWkt", None)
    r = m.parse_response(broken, code)
    assert r["state"] == "pending" and r["pending_reason"] == "schema_unexpected", r
    swapped = copy.deepcopy(body)
    for al in swapped["data"]["ruralProperty"]["alerts"]:
        al["geometryWkt"] = transform(lambda x, y: (y, x), shapely_wkt.loads(al["geometryWkt"])).wkt
    r = m.parse_response(swapped, code)
    assert r["state"] == "pending" and r["pending_reason"] == "schema_unexpected", r
    ok("alerta_ilegivel_ou_eixo_trocado=pending_nunca_not_found")

    # Estado desconhecido é resposta ilegível; só "cancelado" conhecido é não validado.
    for value in (None, "pending_validation", "validated", "refined", "PUBLICADO", 7):
        unknown = copy.deepcopy(body)
        unknown["data"]["ruralProperty"]["alerts"][0]["statusName"] = value
        r = m.parse_response(unknown, code, CARS["controle_pre_corte"]["geometry"])
        assert r["state"] == "pending" and r["ignored"]["malformed"] == 1, (value, r)
    rejected = copy.deepcopy(body)
    rejected["data"]["ruralProperty"]["alerts"][0]["statusName"] = "rejected"
    r = m.parse_response(rejected, code, CARS["controle_pre_corte"]["geometry"])
    assert r["state"] == "not_found" and r["ignored"]["not_validated"] == 1 and r["alerts"] == [], r
    ok("status_desconhecido=pending_so_cancelado_conhecido_fica_de_fora")

    point = copy.deepcopy(body)
    al = point["data"]["ruralProperty"]["alerts"][0]
    al["geometryWkt"] = shapely_wkt.loads(al["geometryWkt"]).centroid.wkt
    r = m.parse_response(point, code, CARS["controle_pre_corte"]["geometry"])
    assert r["state"] == "pending" and r["ignored"]["malformed"] == 1, r
    ok("geometria_sem_area=pending")

    # Parte legível, parte ilegível: contagem só como mínimo, e nunca "nenhum" na combinação.
    partial = copy.deepcopy(body)
    extra = copy.deepcopy(partial["data"]["ruralProperty"]["alerts"][0])
    extra.update(alertCode=1999999, detectedAt="2026-03-01", geometryWkt=None)
    partial["data"]["ruralProperty"]["alerts"].append(extra)
    r = m.parse_response(partial, code, CARS["controle_pre_corte"]["geometry"])
    assert r["state"] == "found" and r["incomplete"] is True and "alert_count" not in r and r["alert_count_min"] == 1, r
    assert r["needs_retry"] is True and "needs_retry" not in m.parse_response(body, code, CARS["controle_pre_corte"]["geometry"]), r
    assert r["text"]["summary"].startswith("Ao menos 1 alerta"), r["text"]
    alone = m.combine_deforestation_alerts(CARS["controle_pre_corte"]["geometry"], r, None, None)
    assert alone["sources"]["validated_alerts"] == "pending" and alone["state"] == "pending", alone
    deter, prodes = inpe("controle_pre_corte")
    with_deter = m.combine_deforestation_alerts(CARS["controle_pre_corte"]["geometry"], r, deter, prodes)
    assert with_deter["state"] == "partial" and "Alertas validados: consulta pendente." in with_deter["text"]["summary"], with_deter
    assert "Conferido em" not in with_deter["text"]["summary"]
    for res in (alone, with_deter):
        assert_clean_public(res)
    ok("resposta_parcialmente_ilegivel=minimo_e_pending_na_combinacao")

    # Tipo que mudou na resposta: alerta ilegível, nunca exceção nem rótulo quebrado.
    for field, value in (("crossedBiomes", 3), ("sources", "DETER-CERRADO"), ("crossedRuralProperties", "x"), ("alertCode", True)):
        drift = copy.deepcopy(body)
        drift["data"]["ruralProperty"]["alerts"][0][field] = value
        r = m.parse_response(drift, code, CARS["controle_pre_corte"]["geometry"])
        assert r["state"] == "pending" and r["ignored"]["malformed"] == 1, (field, r)
    odd = {"data": {"ruralProperty": {"propertyCode": code, "alerts": []}, "lastAlertPublication": "x"}, "errors": "falha"}
    assert m.parse_response(odd, code)["state"] == "not_found"
    real_geometry = m._geometry

    def explode(value):
        raise RuntimeError("geometria que a biblioteca não aceita")

    m._geometry = explode
    try:
        r = m.parse_response(body, code, CARS["controle_pre_corte"]["geometry"])
        c = m.combine_deforestation_alerts(CARS["controle_pre_corte"]["geometry"], None, None, None)
    finally:
        m._geometry = real_geometry
    assert r["state"] == "pending" and r["pending_reason"] == "schema_unexpected", r
    assert c["state"] == "pending" and c["pending_reason"] == "combine_error", c
    ok("tipo_trocado_ou_erro_interno=pending_sem_excecao")

    assert m.parse_response(body, "XX-123")["pending_reason"] == "invalid_car_code"
    assert m.normalize_car_code(" mg-3120904-67cd.5f6c.3be2.49f3.98a3.ff3e.30d7.b1e8 ") == code
    assert m.normalize_car_code("MG-٣١٢٠٩٠٤-67CD5F6C3BE249F398A3FF3E30D7B1E8") is None
    assert m.normalize_car_code("XX-3120904-67CD5F6C3BE249F398A3FF3E30D7B1E8") is None
    assert m.normalize_car_code(code + "\n0") is None
    ok("codigo_car_ascii_e_uf_valida")


# ------------------------------------------------------------------ rede simulada
def new_workers(before: set) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t not in before and t.name == m._WORKER_NAME]


def transport_rules() -> None:
    # A pausa entre tentativas fica no valor de produção: zerar apagaria a janela em
    # que o cancelamento precisa ser visto (controle N26b).
    assert m.RETRY_PAUSE_SECONDS > 0
    code, body = body_of("controle_pre_corte")
    geom = CARS["controle_pre_corte"]["geometry"]
    seen: list[httpx.Request] = []

    def run(responses, **kw):
        seen.clear()
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            item = queue.pop(0) if queue else httpx.Response(599)
            if callable(item) and not isinstance(item, httpx.Response):
                item = item(request)
            if isinstance(item, Exception):
                raise item
            return item

        m.clear_cache()
        m.reset_rate_limit()
        with client(handler) as c:
            return m.query_mapbiomas_alerta(code, geom, client=c, **kw)

    good = httpx.Response(200, json=body)
    r = run([good])
    req = json.loads(seen[0].content)
    assert seen[0].method == "POST" and req["variables"] == {"c": code} and req["query"] == m.QUERY
    assert r["state"] == "found" and r["attempts"] == 1 and r["cached"] is False
    tmo = seen[0].extensions["timeout"]
    assert tmo["read"] == 16.0 and tmo["connect"] == 6.0, tmo
    ok("consulta_com_prazo_da_tentativa")

    r = run([httpx.ReadTimeout("t")])
    assert r["state"] == "pending" and r["pending_reason"] == "timeout" and len(seen) == 1, r
    r = run([httpx.ConnectTimeout("t")])
    assert r["pending_reason"] == "timeout" and len(seen) == 1, r
    ok("tempo_esgotado=pending_sem_repetir")

    r = run([httpx.Response(502), httpx.Response(200, json=body)])
    assert r["state"] == "found" and r["attempts"] == 2 and len(seen) == 2, r
    r = run([httpx.Response(500), httpx.Response(503)])
    assert r["state"] == "pending" and r["pending_reason"] == "http_5xx" and len(seen) == 2, r
    r = run([httpx.ConnectError("x"), httpx.ConnectError("x")])
    assert r["pending_reason"] == "network_error" and len(seen) == 2, r
    ok("falha_rapida=uma_nova_tentativa_depois_pending")

    r = run([httpx.Response(403, json={"errors": [{"message": "Token de acesso inválido"}]})])
    assert r["pending_reason"] == "http_4xx" and len(seen) == 1, r
    r = run([httpx.Response(200, content=b"<html>manutencao</html>")])
    assert r["pending_reason"] == "invalid_json", r
    r = run([lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"isto nao e gzip")])
    assert r["state"] == "pending" and r["pending_reason"] == "decode_error" and len(seen) == 1, r
    r = run([httpx.Response(200, content=b"\xff\xfe\x00lixo")])
    assert r["state"] == "pending" and r["pending_reason"] == "invalid_json", r
    ok("4xx_html_gzip_quebrado=pending_sem_excecao")

    stop = threading.Event()
    stop.set()
    r = run([good], cancel_event=stop)
    assert r["pending_reason"] == "cancelled" and len(seen) == 0 and r["attempts"] == 0, r

    between = threading.Event()

    def fail_and_cancel(request):
        between.set()  # o pedido foi cancelado enquanto a primeira tentativa corria
        return httpx.Response(502)

    r = run([fail_and_cancel, good], cancel_event=between)
    assert r["pending_reason"] == "cancelled" and len(seen) == 1, r
    ok("cancel_event=nao_consulta_nem_repete")

    # Cancelamento NO MEIO da pausa entre tentativas (pausa de produção, não zero):
    # a guarda depois da pausa impede a segunda tentativa.
    class CancelDuringPause(threading.Event):
        def __init__(self) -> None:
            super().__init__()
            self.pauses: list = []

        def wait(self, timeout=None):
            self.pauses.append(timeout)
            time.sleep(timeout / 2)  # metade da pausa passa de verdade
            self.set()  # o usuário cancela aqui
            return super().wait(timeout / 2)

    mid_pause = CancelDuringPause()
    t0 = time.monotonic()
    r = run([httpx.Response(502), good], cancel_event=mid_pause)
    took = time.monotonic() - t0
    assert mid_pause.pauses == [m.RETRY_PAUSE_SECONDS] and mid_pause.pauses[0] > 0, mid_pause.pauses
    assert r["pending_reason"] == "cancelled" and len(seen) == 1 and r["attempts"] == 1, (r, len(seen))
    assert took >= m.RETRY_PAUSE_SECONDS / 2, took
    ok("cancelamento_no_meio_da_pausa=sem_segunda_tentativa")

    for res in (run([httpx.ReadTimeout("t")]), run([httpx.Response(500), httpx.Response(500)])):
        assert res["state"] == "pending" and res["alert_count"] is None
        assert_clean_public(res)

    # Prazo total: resposta aos pingos não segura a thread; cancelamento no meio da leitura vale.
    payload = json.dumps(body).encode()

    def trickle(request):
        def chunks():
            step = max(1, len(payload) // 40)
            for i in range(0, len(payload), step):
                time.sleep(0.05)
                yield payload[i:i + step]

        return httpx.Response(200, content=chunks())

    before = set(threading.enumerate())
    t0 = time.monotonic()
    r = run([trickle], deadline_s=0.4)
    took = time.monotonic() - t0
    assert r["state"] == "pending" and r["pending_reason"] == "deadline" and took < 0.7, (r, took)
    assert seen[0].extensions["timeout"]["read"] <= 0.4, seen[0].extensions["timeout"]
    # A leitora não segue consumindo a resposta depois que a consulta voltou (os pingos
    # continuariam por ~2 s).
    for worker in new_workers(before):
        worker.join(0.5)
        assert not worker.is_alive(), "leitura seguiu depois do prazo"
    mid = threading.Event()
    threading.Timer(0.3, mid.set).start()
    t0 = time.monotonic()
    r = run([trickle], cancel_event=mid)
    took = time.monotonic() - t0
    assert r["pending_reason"] == "cancelled" and took < 0.7 and len(seen) == 1, (r, took)
    r = run([httpx.Response(502), good], deadline_s=1.0)
    assert r["pending_reason"] == "http_5xx" and len(seen) == 1, r
    assert m.DEADLINE_SECONDS <= 30 and m.MIN_RETRY_SECONDS > 0
    ok("prazo_total_monotonico=tentativa_limitada_ao_restante")

    # Teto rígido: o timeout do httpx vale por leitura, então pingo a cada 1 s ou fonte
    # muda seguravam a thread além do prazo e do cancelamento.
    def drip(request):
        def chunks():
            yield payload[:10]
            for i in range(10, len(payload), 4096):
                time.sleep(1.0)
                yield payload[i:i + 4096]

        return httpx.Response(200, content=chunks())

    def mute(request):
        time.sleep(1.5)
        return httpx.Response(200, json=body)

    t0 = time.monotonic()
    r = run([drip], deadline_s=0.5)
    took = time.monotonic() - t0
    assert r["pending_reason"] == "deadline" and took < 0.8, (r, took)
    t0 = time.monotonic()
    r = run([mute], deadline_s=0.3)
    took = time.monotonic() - t0
    assert r["pending_reason"] == "deadline" and took < 0.6, (r, took)
    silent = threading.Event()
    threading.Timer(0.2, silent.set).start()
    t0 = time.monotonic()
    r = run([mute], cancel_event=silent)
    took = time.monotonic() - t0
    assert r["pending_reason"] == "cancelled" and took < 0.6, (r, took)
    ok("prazo_e_cancelamento=teto_rigido_sem_esperar_byte")

    # Versão async (asyncio.gather do analyze_car): cancelar a tarefa avisa a thread.
    async def cancel_async() -> threading.Event:
        ev = threading.Event()
        m.clear_cache()
        with client(trickle) as c:
            task = asyncio.ensure_future(m.query_mapbiomas_alerta_async(code, geom, client=c, cancel_event=ev, use_cache=False))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return ev

    assert asyncio.run(cancel_async()).is_set(), "tarefa cancelada precisa avisar a thread"
    ok("async_cancelado=cancel_event_na_thread")

    # Erro fora de toda previsão (aqui, o cache quebrado): pendência, nunca exceção.
    real_cache_get = m._cache_get

    def broken_cache(key):
        raise RuntimeError("cache quebrado")

    m._cache_get = broken_cache
    try:
        r = m.query_mapbiomas_alerta(code, geom)
    finally:
        m._cache_get = real_cache_get
    assert r["state"] == "pending" and r["pending_reason"] == "unexpected_error", r
    ok("erro_inesperado_na_consulta=pending_sem_excecao")

    # Limite 429: pendência, e nenhum pedido novo durante o intervalo pedido pela fonte.
    r = run([httpx.Response(429, headers={"Retry-After": "120"}), good])
    assert r["pending_reason"] == "rate_limited" and len(seen) == 1, r
    assert 100 < m.cooldown_remaining() <= 120, m.cooldown_remaining()
    calls = []
    m.clear_cache()
    with client(lambda request: calls.append(1) or httpx.Response(200, json=body)) as c:
        again = m.query_mapbiomas_alerta(code, geom, client=c)
        other = m.query_mapbiomas_alerta(CARS["recente_pos_corte"]["car_code"], client=c)
    assert again["pending_reason"] == "rate_limited" and other["pending_reason"] == "rate_limited" and calls == [], (again, calls)
    assert again["attempts"] == 0 and again["text"]["summary"] == m.PENDING_TEXT
    run([httpx.Response(429)])
    assert 290 < m.cooldown_remaining() <= m.RATE_LIMIT_DEFAULT_SECONDS == 300, m.cooldown_remaining()
    m.reset_rate_limit()
    ok("limite_429=pending_e_intervalo_sem_novo_pedido")

    # Cache curto por CAR: resposta boa fica; pendência não fica (tenta de novo sozinha).
    m.clear_cache()
    calls = []

    def counting(request):
        calls.append(1)
        return httpx.Response(200, json=body)

    with client(counting) as c:
        first = m.query_mapbiomas_alerta(code, geom, client=c)
        second = m.query_mapbiomas_alerta(code.lower(), geom, client=c)
    assert len(calls) == 1 and first["cached"] is False and second["cached"] is True, (calls, second)
    assert second["alerts"][0]["area_in_car_ha"] == first["alerts"][0]["area_in_car_ha"]
    m.clear_cache()
    calls.clear()
    flaky = [httpx.ReadTimeout("t")]

    def once_timeout(request):
        calls.append(1)
        if flaky:
            raise flaky.pop()
        return httpx.Response(200, json=body)

    with client(once_timeout) as c:
        first = m.query_mapbiomas_alerta(code, geom, client=c)
        assert first["state"] == "pending" and first["needs_retry"] is True, first
        assert m.query_mapbiomas_alerta(code, geom, client=c)["state"] == "found"
    assert len(calls) == 2
    assert 0 < m.CACHE_TTL_SECONDS <= 6 * 3600
    # Resposta com parte ilegível: pede nova tentativa e não fica no cache.
    m.clear_cache()
    calls.clear()
    partial_body = copy.deepcopy(body)
    extra = copy.deepcopy(partial_body["data"]["ruralProperty"]["alerts"][0])
    extra.update(alertCode=1999999, detectedAt="2026-03-01", geometryWkt=None)
    partial_body["data"]["ruralProperty"]["alerts"].append(extra)
    replies = [partial_body, body]

    def incomplete_then_good(request):
        calls.append(1)
        return httpx.Response(200, json=replies.pop(0) if replies else body)

    with client(incomplete_then_good) as c:
        r1 = m.query_mapbiomas_alerta(code, geom, client=c)
        r2 = m.query_mapbiomas_alerta(code, geom, client=c)
    assert r1["state"] == "found" and r1["incomplete"] is True and r1["needs_retry"] is True, r1
    assert len(calls) == 2 and r2["cached"] is False and r2["incomplete"] is False and "needs_retry" not in r2, (calls, r2)
    m.clear_cache()
    ok("cache_curto_por_car=so_resposta_valida_e_completa")

    assert m.query_mapbiomas_alerta("nao-e-car")["pending_reason"] == "invalid_car_code"
    ok("codigo_invalido=sem_rede")


def real_socket_rules() -> None:
    """Socket TCP de verdade em 127.0.0.1: o prazo corta a espera e a leitura parada acorda na hora."""
    code, _ = body_of("controle_pre_corte")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(5.0)
    port = srv.getsockname()[1]
    client_hung_up: list[bool] = []

    def serve() -> None:
        try:
            conn, _ = srv.accept()
        except OSError:
            srv.close()
            return
        try:
            conn.settimeout(5.0)
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
            for _ in range(3):  # pingos em 0; 0,4; 0,8 s, depois fica mudo
                conn.sendall(b"1\r\n \r\n")
                time.sleep(0.4)
            conn.settimeout(3.0)
            hung_up = False
            while True:  # descarta o resto do pedido até o cliente desligar
                try:
                    chunk = conn.recv(65536)
                except TimeoutError:
                    break
                except OSError:
                    hung_up = True
                    break
                if not chunk:
                    hung_up = True
                    break
            client_hung_up.append(hung_up)
        finally:
            conn.close()
            srv.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    real_endpoint = m._ENDPOINT
    m._ENDPOINT = f"http://127.0.0.1:{port}/api/v2/graphql"
    m.clear_cache()
    m.reset_rate_limit()
    before = set(threading.enumerate())
    try:
        with httpx.Client(trust_env=False) as c:
            t0 = time.monotonic()
            r = m.query_mapbiomas_alerta(code, client=c, use_cache=False, deadline_s=1.0)
            took = time.monotonic() - t0
            # Sem desligar o socket, a leitura parada em 0,8 s só acordaria no timeout (1,8 s).
            for worker in new_workers(before):
                worker.join(0.4)
                assert not worker.is_alive(), "leitura presa no socket depois do prazo"
    finally:
        m._ENDPOINT = real_endpoint
    server.join(3.0)
    assert r["state"] == "pending" and r["pending_reason"] == "deadline" and took < 1.4, (r, took)
    assert client_hung_up == [True], client_hung_up
    ok("socket_real=prazo_rigido_e_leitura_parada_acorda")


def production_path() -> None:
    """Sem ``client=``: o cliente que a produção cria de verdade, com a rede trocada no transporte."""
    code, body = body_of("controle_pre_corte")
    geom = CARS["controle_pre_corte"]["geometry"]
    captured: dict = {"kwargs": [], "requests": []}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        captured["requests"].append(request)
        return httpx.Response(200, json=body)

    class SpyClient(real_client):
        def __init__(self, *args, **kwargs):
            captured["kwargs"].append(dict(kwargs))
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    m.clear_cache()
    m.reset_rate_limit()
    httpx.Client = SpyClient
    try:
        r = m.query_mapbiomas_alerta(code, geom, use_cache=False)
    finally:
        httpx.Client = real_client
    assert r["state"] == "found" and len(captured["kwargs"]) == 1 and len(captured["requests"]) == 1, (r, captured)
    kw = captured["kwargs"][0]
    assert kw.get("follow_redirects") is False, kw
    timeout = kw.get("timeout")
    assert isinstance(timeout, httpx.Timeout), kw
    for part in (timeout.connect, timeout.read, timeout.write, timeout.pool):
        assert isinstance(part, (int, float)) and 0 < part <= 60, timeout
    req = captured["requests"][0]
    headers = {k.lower() for k in req.headers.keys()}
    assert "authorization" not in headers and "cookie" not in headers, "consulta deve ser pública, sem token"
    assert str(req.url) == m._ENDPOINT and req.method == "POST", req
    for part in req.extensions["timeout"].values():
        assert isinstance(part, (int, float)) and 0 < part <= m.DEADLINE_SECONDS, req.extensions["timeout"]
    ok("caminho_de_producao=sem_token_prazo_finito_sem_redirecionar")


# ------------------------------------------------------------------ combinação
PRODES_CATALOG = ["prodes-cerrado-nb:yearly_deforestation", "prodes-cerrado-nb:accumulated_deforestation_2000"]


def prodes_answer(hits: list, **extra) -> dict:
    """Formato do prodes_fast_v24: catálogo, hits com count e failed_layers separado."""
    rows = [dict(h, count=len(h.get("features") or [])) for h in hits]
    return {"ok": True, "candidate_layers": list(PRODES_CATALOG), "hits": rows, "failed_layers": [], **extra}


def inpe(name: str, **deter_extra):
    fx = load(f"inpe_{name}.json")
    deter = {"state": "answered", "features": fx["deter_features"], "min_area_ha": 3.0, **deter_extra}
    return deter, prodes_answer(fx["prodes_hits"])


def area_of(geom) -> float:
    return m._area_ha(m._geometry(geom))


def combination_rules(parsed: dict) -> None:
    hit, recent, empty = parsed["hit"], parsed["recent"], parsed["empty"]
    car_recent = CARS["recente_pos_corte"]["geometry"]
    car_ctrl = CARS["controle_pre_corte"]["geometry"]
    car_empty = CARS["curvelo_vazio"]["geometry"]

    # 1) Mesma abertura recente no DETER (23546_curr) e nos alertas validados (1525843): um evento, área = união.
    deter, prodes = inpe("recente_pos_corte", latest_image_date="2026-09-04")
    c = m.combine_deforestation_alerts(car_recent, recent, deter, prodes)
    assert c["state"] == "found" and c["event_count"] == 1 and len(c["events"]) == 1, c
    e = c["events"][0]
    assert e["sources"] == ["INPE (DETER)", "Alerta validado"] and e["validated"] is True, e
    assert e["_validated_alert_codes"] == ["1525843"], e
    assert sorted(w["id"] for w in e["_witnesses"]) == ["1525843", "23546_curr"], e
    biggest = max(w["area_in_car_ha"] for w in e["_witnesses"])
    total = sum(w["area_in_car_ha"] for w in e["_witnesses"])
    assert biggest <= e["area_in_car_ha"] + 1e-6 < total, (biggest, e["area_in_car_ha"], total)
    assert c["area_union_ha"] == e["area_in_car_ha"] < c["_audit_sum_of_sources_ha"], c
    assert c["area_union_ha"] <= area_of(car_recent)
    assert e["first_seen"] == "2025-09-15" and c["cut_date"] == "2025-07-31" and c["recent_after"] == "2025-08-01"
    assert "event_count_is_minimum" not in c
    ok(f"deter_mais_validado=1_evento_uniao_{c['area_union_ha']}ha_nao_soma_{c['_audit_sum_of_sources_ha']}ha")

    # Regra 4: área que o PRODES já marcou é dita, não somada.
    assert e["area_already_in_prodes_ha"] > 0 and 2025 in e["prodes_years"], e
    assert e["area_already_in_prodes_ha"] <= e["area_in_car_ha"]
    t = text_of(c)
    assert "já constavam no mapa anual PRODES" in t and "alerta de desmatamento validado" in t and "(INPE)" in t, t
    credits = [x["text"] for x in c["sources_page_credits"]]
    assert credits == [m.DETER_SOURCES_PAGE_CREDIT, OWNER_CREDIT], credits
    assert "credits" not in c["text"]
    assert c["text"]["disclaimer"].startswith(m.DISCLAIMER)
    assert c["credit_note"] and "não entra na conta" in c["credit_note"]
    assert_clean_public(c)
    ok("prodes_ja_marcado=dito_nao_somado_credito_so_na_pagina_de_fontes")

    # 2) Alerta antigo (1349329, dez/2024) + DETER 2061786_hist + PRODES 2025: não é recente; anota o PRODES do ano certo.
    deter, prodes = inpe("controle_pre_corte")
    c = m.combine_deforestation_alerts(car_ctrl, hit, deter, prodes)
    assert c["state"] == "not_found" and c["events"] == [] and c["area_union_ha"] == 0.0 and c["event_count"] == 0, c
    rows = {r["_id"]: r for r in c["before_cutoff"]}
    assert set(rows) == {"1349329", "2061786_hist"}, rows
    assert rows["1349329"]["prodes_years"] == [2025] and rows["1349329"]["prodes_overlap_ha"] > 10, rows
    assert rows["1349329"]["coincides_with_prodes"] is True, rows
    notes = [i["text"] for i in c["text"]["items"] if i.get("section") == "prodes"]
    assert notes == ["Ocorrência PRODES de 2025: coincide com alerta de desmatamento validado (detectado em 09/12/2024)."], notes
    assert c["text"]["summary"] == (
        "Nenhum alerta recente de desmatamento sobre o imóvel desde 01/08/2025. "
        "Conferido em: INPE (alertas a partir de 3 ha) e alertas validados (detecções até 31/07/2026)."
    ), c["text"]["summary"]
    assert_clean_public(c)
    ok("tres_testemunhas_antigas=0_recente_nota_prodes_2025_coincide")

    # Sobreposição mínima (anel de ~5 m na borda do alerta) não gera nota.
    alert_in = m._geometry(hit["alerts"][0]["_geometry_wkt"]).intersection(m._geometry(car_ctrl))
    ring = m._geometry(car_ctrl).difference(alert_in.buffer(-0.00005))
    sliver = prodes_answer([{"layer": "prodes-cerrado-nb:yearly_deforestation", "features": [
        {"type": "Feature", "properties": {"year": 2025}, "geometry": mapping(ring)}]}])
    common = m._area_ha(ring.intersection(alert_in))
    assert 0 < common / m._area_ha(alert_in) < 0.5 and common / m._area_ha(ring) < 0.5, "cenário de borda precisa ser borda"
    s = m.combine_deforestation_alerts(car_ctrl, hit, deter, sliver)
    srow = {r["_id"]: r for r in s["before_cutoff"]}["1349329"]
    assert srow["coincides_with_prodes"] is False and not [i for i in s["text"]["items"] if i.get("section") == "prodes"], s
    ok("sobreposicao_de_borda=sem_nota_prodes")

    # Polígono PRODES grande (~720 ha fora do imóvel) que deixa só uma lasca de ~5 m² dentro
    # do imóvel, e a lasca cai no alerta: contra a parte recortada daria "coincide".
    car_shape = m._geometry(car_ctrl)
    spot = alert_in.representative_point()
    tiny = box(spot.x - 1e-5, spot.y - 1e-5, spot.x + 1e-5, spot.y + 1e-5)
    minx, miny, _, maxy = car_shape.bounds
    far = box(minx - 0.03, miny, minx - 0.01, maxy)
    big_prodes = MultiPolygon([far, tiny])
    clipped = big_prodes.intersection(car_shape)
    assert m.MIN_AREA_HA <= m._area_ha(clipped) < 0.001 and m._area_ha(big_prodes) > 500, "cenário de lasca"
    assert m._area_ha(clipped.intersection(alert_in)) / m._area_ha(clipped) >= 0.5, "recortado diria coincide"
    lasca = prodes_answer([{"layer": "prodes-cerrado-nb:yearly_deforestation", "features": [
        {"type": "Feature", "properties": {"year": 2025}, "geometry": mapping(big_prodes)}]}])
    lp = m.combine_deforestation_alerts(car_ctrl, hit, deter, lasca)
    lrow = {r["_id"]: r for r in lp["before_cutoff"]}["1349329"]
    assert lrow["coincides_with_prodes"] is False and lrow["prodes_overlap_ha"] < 0.001, lrow
    assert not [i for i in lp["text"]["items"] if i.get("section") == "prodes"], lp["text"]
    ok("lasca_de_poligono_prodes_grande=sem_nota_medida_no_poligono_inteiro")

    # Alerta grande (~730 ha) com só 11,84 ha dentro do imóvel, coberto por um polígono PRODES
    # do tamanho do imóvel: contra o alerta recortado daria "coincide"; contra o inteiro, não.
    wide = copy.deepcopy(hit)
    wide_geom = unary_union([m._geometry(hit["alerts"][0]["_geometry_wkt"]), far])
    wide["alerts"][0]["_geometry_wkt"] = wide_geom.wkt
    whole_car = prodes_answer([{"layer": "prodes-cerrado-nb:yearly_deforestation", "features": [
        {"type": "Feature", "properties": {"year": 2025}, "geometry": mapping(car_shape)}]}])
    in_car = m._area_ha(wide_geom.intersection(car_shape))
    assert in_car > 10 and in_car / m._area_ha(wide_geom) < 0.5 and in_car / m._area_ha(car_shape) < 0.5, "cenário"
    wp = m.combine_deforestation_alerts(car_ctrl, wide, None, whole_car)
    wrow = {r["_id"]: r for r in wp["before_cutoff"]}["1349329"]
    assert wrow["prodes_overlap_ha"] > 10 and wrow["coincides_with_prodes"] is False, wrow
    assert not [i for i in wp["text"]["items"] if i.get("section") == "prodes"], wp["text"]
    ok("alerta_grande_com_parte_no_imovel=sem_nota_medida_no_alerta_inteiro")

    # 3) O corte anda quando sai o próximo PRODES: o evento de 2025-09 deixa de ser recente.
    deter, prodes = inpe("recente_pos_corte")
    moved = m.combine_deforestation_alerts(car_recent, recent, deter, prodes, prodes_cutoff="2026-07-31")
    assert moved["events"] == [] and moved["cut_date"] == "2026-07-31" and moved["cut_date_origin"] == "informado", moved
    assert {r["_id"] for r in moved["before_cutoff"]} >= {"1525843", "23546_curr"}
    # Polígono PRODES mais novo no entorno não empurra o corte sozinho (esconderia alerta
    # recente); só sinaliza que o corte pode estar vencido.
    newer = copy.deepcopy(prodes)
    for f in newer["hits"][0]["features"]:
        f["properties"]["year"] = 2026
    kept = m.combine_deforestation_alerts(car_recent, recent, deter, newer)
    assert kept["cut_date"] == m.PRODES_CUTOFF_FALLBACK.isoformat() and kept["cut_date_origin"].startswith("padrao"), kept
    assert kept["event_count"] == 1 and kept.get("prodes_newer_than_cut") is True, kept
    assert c.get("prodes_newer_than_cut") is None
    oldest = m.combine_deforestation_alerts(car_recent, recent, deter, prodes, prodes_cutoff=["2026-07-31", "2025-07-31"])
    assert oldest["cut_date"] == "2025-07-31" and oldest["cut_date_origin"] == "informado_mais_antigo", oldest
    assert oldest["event_count"] == 1
    ok("corte_informado_anda_discordancia_usa_o_mais_antigo")

    # 4) PRODES não consultado, com falha, com camada sem nome ou em formato que não prova
    #    consulta completa: nenhuma afirmação sobre PRODES (nem zero).
    unnamed = prodes_answer([{k: v for k, v in prodes["hits"][0].items() if k != "layer"}])
    legacy_ok = {"ok": True, "candidate_layers": list(PRODES_CATALOG), "hits": prodes["hits"]}  # deploy_app.query_prodes
    legacy_err = {"ok": True, "candidate_layers": list(PRODES_CATALOG),
                  "hits": prodes["hits"] + [{"layer": "prodes-cerrado-nb:accumulated_deforestation_2000", "error": "ReadTimeout"}]}
    error_in_hits = prodes_answer(prodes["hits"])
    error_in_hits["hits"].append({"layer": "prodes-cerrado-nb:yearly_deforestation", "count": 0, "features": [],
                                  "error": "ReadTimeout"})
    empty_catalog = {"ok": True, "hits": [], "failed_layers": [], "candidate_layers": []}
    no_annual = {"ok": True, "hits": [], "failed_layers": [], "candidate_layers": [PRODES_CATALOG[1]]}
    at_limit = prodes_answer(prodes["hits"])
    at_limit["hits"][0]["count"] = m.PRODES_WFS_COUNT_LIMIT
    for bad_prodes in (None, {"ok": False, "failed_layers": [{"layer": "x"}], "hits": []},
                       {"ok": True, "failed_layers": [{"layer": "x", "error": "ReadTimeout"}], "hits": prodes["hits"]},
                       unnamed, legacy_ok, legacy_err, error_in_hits, empty_catalog, no_annual, at_limit):
        n = m.combine_deforestation_alerts(car_recent, recent, deter, bad_prodes)
        assert n["state"] == "found" and n["prodes_checked"] is False, n
        ev = n["events"][0]
        assert ev["area_already_in_prodes_ha"] is None and "prodes_years" not in ev, ev
        said = " ".join([n["text"]["summary"], n.get("credit_note", "")] + [i["text"] for i in n["text"]["items"]])
        assert "PRODES" not in said and "já constavam" not in said, said
        assert "prodes_newer_than_cut" not in n
        old = m.combine_deforestation_alerts(car_ctrl, hit, inpe("controle_pre_corte")[0], bad_prodes)
        assert all("prodes_overlap_ha" not in r and "coincides_with_prodes" not in r for r in old["before_cutoff"]), old
        assert not [i for i in old["text"]["items"] if i.get("section") == "prodes"], old
    # Máscara acumulada não é mapa anual.
    mask = copy.deepcopy(prodes)
    mask["hits"][0]["layer"] = "prodes-cerrado-nb:accumulated_deforestation_2000"
    k = m.combine_deforestation_alerts(car_recent, recent, deter, mask)
    assert k["prodes_checked"] is True and k["events"][0]["area_already_in_prodes_ha"] == 0.0, k
    assert "já constavam" not in text_of(k)
    assert m._prodes_checked(prodes) is True, "formato completo do prodes_fast_v24 precisa valer"
    ok("prodes_falhou_legado_catalogo_vazio_limite_ou_mascara=nenhuma_afirmacao_prodes")

    # 5) Sem módulo DETER: só alertas validados, sem citar o INPE.
    only = m.combine_deforestation_alerts(car_recent, recent, None, prodes)
    assert only["sources"]["inpe_deter"] == "not_queried" and only["state"] == "found", only
    assert abs(only["area_union_ha"] - recent["alerts"][0]["area_in_car_ha"]) < 1e-3, only
    assert "INPE" not in text_of(only), text_of(only)
    assert [x["text"] for x in only["sources_page_credits"]] == [OWNER_CREDIT]
    ok("deter_ausente=so_validados_sem_citar_inpe")

    # 6) Pendências: nunca "nenhum alerta" sem as fontes responderem, e nunca zero inventado.
    pend_deter = {"state": "pending"}
    part = m.combine_deforestation_alerts(car_empty, empty, pend_deter, None)
    assert part["state"] == "partial" and "INPE: consulta pendente." in part["text"]["summary"], part
    assert "Conferido em" not in part["text"]["summary"]
    assert "event_count" not in part and "area_union_ha" not in part, part
    pub_part = m.public_view(part)
    assert "event_count" not in pub_part and "area_union_ha" not in pub_part and "_audit_sum_of_sources_ha" not in pub_part
    mb_pending = m.parse_response(body_of("inexistente")[1], body_of("inexistente")[0])
    empty_deter = {"state": "answered", "features": []}
    part2 = m.combine_deforestation_alerts(car_empty, mb_pending, empty_deter, None)
    assert part2["state"] == "partial" and "Alertas validados: consulta pendente." in part2["text"]["summary"], part2
    assert "event_count" not in part2 and "area_union_ha" not in part2, part2
    both = m.combine_deforestation_alerts(car_empty, mb_pending, pend_deter, None)
    assert both["state"] == "pending" and "event_count" not in both and both.get("area_union_ha") is None, both
    as_list = m.combine_deforestation_alerts(car_empty, mb_pending, [], None)
    assert as_list["sources"]["inpe_deter"] == "pending" and as_list["state"] == "pending", as_list
    no_car = m.combine_deforestation_alerts(None, recent, deter, prodes)
    assert no_car["state"] == "pending" and no_car["pending_reason"] == "car_geometry_missing", no_car
    # Alerta que existe mas chegou sem geometria (ex.: public_view) não some da conta.
    blind = m.combine_deforestation_alerts(car_recent, m.public_view(recent), None, None)
    assert blind["sources"]["validated_alerts"] == "pending" and blind["state"] == "pending", blind
    for res in (part, part2, both, as_list, no_car, blind):
        assert_clean_public(res)
        assert "conferido em" not in text_of(res).lower()
    clear = m.combine_deforestation_alerts(car_empty, empty, empty_deter, None)
    assert clear["state"] == "not_found" and clear["event_count"] == 0, clear
    assert "Conferido em: INPE e alertas validados (detecções até 31/07/2026)" in clear["text"]["summary"], clear
    ok("pendencia=partial_ou_pending_sem_zero_inventado")

    # 7) DETER com feição ilegível: o que foi lido conta, "nenhum" não se afirma.
    broken_deter = copy.deepcopy(inpe("controle_pre_corte")[0])
    broken_deter["features"].append({"type": "Feature", "properties": {"gid": "sem_data"},
                                     "geometry": broken_deter["features"][0]["geometry"]})
    bd = m.combine_deforestation_alerts(car_ctrl, hit, broken_deter, prodes)
    assert bd["sources"]["inpe_deter"] == "pending" and bd["state"] == "partial", bd
    assert "INPE: consulta pendente." in bd["text"]["summary"] and "Conferido em" not in bd["text"]["summary"], bd
    assert any(r["_id"] == "2061786_hist" for r in bd["before_cutoff"]), bd
    point_deter = copy.deepcopy(inpe("recente_pos_corte")[0])
    point_deter["features"].append({"type": "Feature", "properties": {"gid": "ponto", "view_date": "2025-10-01"},
                                    "geometry": {"type": "Point", "coordinates": [-44.2, -18.8]}})
    pd = m.combine_deforestation_alerts(car_recent, recent, point_deter, prodes)
    assert pd["state"] == "found" and pd["event_count_is_minimum"] is True and pd["text"]["summary"].startswith("Ao menos 1"), pd
    assert_clean_public(pd)
    ok("deter_parcialmente_ilegivel=pending_nunca_not_found")

    # 8) Alerta recente que a fonte liga ao CAR, fora do desenho atual: continua na conta, sem "nenhum".
    code_ctrl, body_ctrl = body_of("controle_pre_corte")
    moved_alert = copy.deepcopy(body_ctrl)
    moved_alert["data"]["ruralProperty"]["alerts"][0]["detectedAt"] = "2025-10-01"
    outside = m.parse_response(moved_alert, code_ctrl, car_empty, car_updated_at="2026-01-10")
    o = m.combine_deforestation_alerts(car_empty, outside, empty_deter, None)
    assert o["state"] == "found" and o["event_count"] == 1 and "area_union_ha" not in o, o
    ev = o["events"][0]
    assert ev["outside_current_geometry"] is True and ev["area_in_car_ha"] is None, ev
    assert "versão anterior do CAR" in text_of(o) and "nenhum" not in text_of(o).lower(), text_of(o)
    assert_clean_public(o)
    ok("alerta_recente_fora_do_desenho=evento_marcado_nunca_nenhum")

    # 9) Testemunha repetida (a mesma geometria entregue duas vezes) não aumenta a área.
    twin = {"state": "answered", "features": [
        {"type": "Feature", "id": "copia", "properties": {"view_date": "2025-09-16", "gid": "copia"},
         "geometry": mapping(m._geometry(recent["alerts"][0]["_geometry_wkt"]))}]}
    dup = m.combine_deforestation_alerts(car_recent, recent, twin, None)
    assert dup["event_count"] == 1 and abs(dup["area_union_ha"] - only["area_union_ha"]) < 1e-3, dup
    assert dup["_audit_sum_of_sources_ha"] > 1.9 * dup["area_union_ha"], dup
    ok("mesma_abertura_duas_vezes=area_uma_vez")

    # 10) Evento só do DETER: aviso próprio, sem dizer que foi validado.
    solo = m.combine_deforestation_alerts(car_recent, None, deter, None)
    assert solo["state"] == "found" and all(not ev["validated"] for ev in solo["events"]), solo
    assert "validado" not in " ".join(i["text"] for i in solo["text"]["items"])
    assert solo["text"]["disclaimer"] == m.DETER_DISCLAIMER
    assert_clean_public(solo)
    ok("evento_so_deter=aviso_inpe")

    # 11) A fonte cruzou uma versão ANTERIOR do CAR: o que achou conta, mas "nenhum" não se afirma
    #     para o desenho atual (alerta na área acrescentada não fica ligado ao código).
    code_v, body_v = body_of("curvelo_vazio")
    old_empty = m.parse_response(body_v, code_v, car_empty, car_updated_at="2026-09-01")
    assert old_empty["state"] == "not_found" and old_empty["source_car_version_older"] is True, old_empty
    v_alone = m.combine_deforestation_alerts(car_empty, old_empty, None, None)
    assert v_alone["state"] == "pending" and v_alone["sources"]["validated_alerts"] == "pending", v_alone
    v_deter = m.combine_deforestation_alerts(car_empty, old_empty, empty_deter, None)
    vs = v_deter["text"]["summary"]
    assert v_deter["state"] == "partial" and "Conferido em" not in vs and "event_count" not in v_deter, v_deter
    assert ("Alertas validados: consulta pendente para o desenho atual (consideram a versão do CAR de 27/09/2025)."
            in vs), vs
    assert v_deter["validated_alerts_car_version_date"] == "2025-09-27", v_deter
    code_r, body_r = body_of("recente_pos_corte")
    old_recent = m.parse_response(body_r, code_r, car_recent, car_updated_at="2026-09-01")
    vf = m.combine_deforestation_alerts(car_recent, old_recent, *inpe("recente_pos_corte"))
    assert vf["state"] == "found" and vf["event_count_is_minimum"] is True, vf
    assert vf["text"]["summary"].startswith("Ao menos 1") and "consideram a versão do CAR de 27/09/2025" in vf["text"]["summary"], vf
    for res in (v_alone, v_deter, vf):
        assert_clean_public(res)
    ok("versao_anterior_do_car_sem_achado=nunca_nenhum_e_ressalva_no_combinado")

    # 12) Retificação que deixa só uma lasca do alerta no desenho atual (abaixo de 0,01 ha):
    #     fora do desenho, com o sinal de versão anterior, e nunca "0,00 ha".
    alert_shape = m._geometry(hit["alerts"][0]["_geometry_wkt"])
    spot = alert_shape.intersection(m._geometry(car_ctrl)).representative_point()
    crumb = box(spot.x - 1.85e-5, spot.y - 1.85e-5, spot.x + 1.85e-5, spot.y + 1.85e-5)
    car_crumb = m._geometry(car_ctrl).difference(alert_shape).union(crumb)
    crumb_ha = m._area_ha(car_crumb.intersection(alert_shape))
    assert m.MIN_AREA_HA <= crumb_ha < m.SLIVER_HA and round(crumb_ha, 2) == 0, crumb_ha
    lr = m.parse_response(body_ctrl, code_ctrl, car_crumb, car_updated_at="2026-01-10")
    la = lr["alerts"][0]
    assert la.get("outside_current_geometry") and la.get("on_previous_car_version") and la.get("current_geometry_edge_only"), la
    assert "area_in_car_ha" not in la, la
    t = text_of(lr)
    assert "0,00" not in t and "versão anterior do CAR" in t and "só toca a borda do desenho atual do imóvel" in t, t
    recent_crumb = m.parse_response(moved_alert, code_ctrl, car_crumb, car_updated_at="2026-01-10")
    # Sem geometria na leitura (área pelo cruzamento da fonte), a combinação mede e marca do mesmo jeito.
    blind_crumb = m.parse_response(moved_alert, code_ctrl, car_updated_at="2026-01-10")
    assert not blind_crumb["alerts"][0].get("outside_current_geometry"), blind_crumb
    for parsed_crumb in (recent_crumb, blind_crumb):
        cc = m.combine_deforestation_alerts(car_crumb, parsed_crumb, empty_deter, None)
        assert cc["state"] == "found" and cc["events"][0].get("outside_current_geometry") is True, cc
        assert cc["events"][0].get("current_geometry_edge_only") is True and "area_union_ha" not in cc, cc
        ct = text_of(cc)
        assert "0,00" not in ct and "versão anterior do CAR" in ct and "só toca a borda" in ct, ct
        assert_clean_public(cc)
    # Feição do INPE que só toca a borda do imóvel também não vira "0,00 ha".
    edge_deter = {"state": "answered", "features": [{"type": "Feature", "properties": {"gid": "borda", "view_date": "2025-10-01"},
                                                     "geometry": mapping(MultiPolygon([box(*far.bounds), crumb]))}]}
    ed = m.combine_deforestation_alerts(car_ctrl, hit, edge_deter, None)
    assert ed["events"] == [] and "0,00" not in text_of(ed), ed
    ok("lasca_de_retificacao=fora_do_desenho_com_versao_sem_0_00_ha")

    # 13) Fonte pendente com evento achado: contagem E área só como mínimo.
    blind_min = m.combine_deforestation_alerts(car_recent, m.public_view(recent), inpe("recente_pos_corte")[0], None)
    bs = blind_min["text"]["summary"]
    assert blind_min["state"] == "found" and blind_min["area_union_is_minimum"] is True, blind_min
    assert bs.startswith("Ao menos 1") and f"com ao menos {m._br_ha(blind_min['area_union_ha'])} ha" in bs, bs
    assert all(i["text"].startswith("Ao menos ") for i in blind_min["text"]["items"]), blind_min["text"]["items"]
    assert "ao menos" not in text_of(c).lower() and "area_union_is_minimum" not in c, c["text"]
    ok("fonte_pendente=area_como_minimo")

    # 14) Cobertura dos alertas validados que não chega à janela recente não responde por ela.
    for date_range in ({"maxDetectedAt": "2025-06-30", "maxPublishedAt": "2025-07-15"},
                       {"maxDetectedAt": "2025-07-31", "maxPublishedAt": "2025-08-15"}, None):
        stale = copy.deepcopy(body_v)
        if date_range is None:
            stale["data"].pop("alertDateRange")
        else:
            stale["data"]["alertDateRange"] = date_range
        sp = m.parse_response(stale, code_v, car_empty)
        assert sp["state"] == "not_found", sp
        s_alone = m.combine_deforestation_alerts(car_empty, sp, None, None)
        s_deter = m.combine_deforestation_alerts(car_empty, sp, empty_deter, None)
        assert s_alone["state"] == "pending" and s_deter["state"] == "partial", (date_range, s_alone, s_deter)
        assert "Alertas validados: consulta pendente." in s_deter["text"]["summary"], s_deter["text"]
        assert "Conferido em" not in s_deter["text"]["summary"] and "alertas validados (" not in s_deter["text"]["summary"]
    ok("cobertura_antes_da_janela_recente=nao_responde_por_nenhum")


if __name__ == "__main__":
    parsed = parse_real()
    parse_rules()
    transport_rules()
    real_socket_rules()
    production_path()
    combination_rules(parsed)
    for name in CHECKS:
        print("RX_F2_MAPBIOMAS_ALERTA_CHECK=" + name)
    print(f"RX_F2_MAPBIOMAS_ALERTA_GATE=PASS checks={len(CHECKS)}")

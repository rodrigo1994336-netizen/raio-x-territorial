from __future__ import annotations

"""F2 · MapBiomas Alerta — gate offline com respostas reais capturadas em 13/09/2026.

Fixtures (scripts/fixtures/f2_mapbiomas_alerta/):
- mba_curvelo_vazio.json        CAR de teste de Curvelo: a fonte conhece o CAR, lista vazia;
- mba_controle_pre_corte.json   vizinho em Curvelo com o alerta 1349329 (detectado 09/12/2024);
- mba_recente_pos_corte.json    CAR em Curvelo com o alerta 1525843 (detectado 15/09/2025);
- mba_inexistente.json          código válido que a fonte não conhece (ruralProperty: null);
- car_geometrias_mapbiomas.json geometria dos três CAR (versão cruzada pelo MapBiomas);
- inpe_*.json                   DETER Cerrado e PRODES Cerrado por bbox desses CAR.

Valida a regra, nunca o valor ao vivo: estados, pendência nunca vira "sem alertas",
crédito e link do laudo, API da fonte fora da saída pública e a regra da seção 5 do
relatório 03 (a mesma abertura no DETER, no MapBiomas e no PRODES conta uma vez).
"""

import copy
import json
import threading
from pathlib import Path

import httpx

import mapbiomas_alerta as m

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "scripts" / "fixtures" / "f2_mapbiomas_alerta"
FORBIDDEN = ("sem desmatamento", "área regular", "sem alertas", "sem alerta", "sem pendências", "em acordo", "regular")
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
    pub = json.dumps(m.public_view(result), ensure_ascii=False)
    assert "graphql" not in pub.lower() and "api/v2" not in pub, "API da fonte vazou para a saída pública"
    assert "_geometry_wkt" not in pub and "MULTIPOLYGON" not in pub, "geometria interna vazou para a saída pública"
    low = text_of(result).lower()
    leaked = [w for w in FORBIDDEN if w in low]
    assert not leaked, f"texto proibido: {leaked} em {text_of(result)!r}"


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ leitura real
def parse_real() -> dict:
    code, body = body_of("curvelo_vazio")
    empty = m.parse_response(body, code, CARS["curvelo_vazio"]["geometry"])
    assert empty["state"] == "not_found" and empty["answered"] is True, empty
    assert empty["alert_count"] == 0 and empty["alerts"] == [], empty
    assert empty["last_publication_date"] == "2026-09-08", empty
    assert empty["text"]["summary"].startswith("Nenhum alerta validado de desmatamento sobre este CAR no MapBiomas Alerta")
    assert "publicações até 08/09/2026" in empty["text"]["summary"]
    assert empty["credit"] == m.CREDIT == "Fonte: MapBiomas Alerta (CC BY-SA 3.0 BR)"
    assert "disclaimer" not in empty
    assert_clean_public(empty)
    ok("curvelo_lista_vazia=not_found_com_data_do_lote")

    code, body = body_of("controle_pre_corte")
    hit = m.parse_response(body, code, CARS["controle_pre_corte"]["geometry"])
    assert hit["state"] == "found" and hit["alert_count"] == 1, hit
    a = hit["alerts"][0]
    assert a["alert_code"] == 1349329 and a["detected_at"] == "2024-12-09" and a["published_at"] == "2025-02-18", a
    # Interseção nossa contra o cruzamento publicado pelo MapBiomas (11,84366 ha): mesma geometria, mesma área.
    assert a["area_in_car_method"] == "intersecao_raio_x" and abs(a["area_in_car_ha"] - 11.84366) / 11.84366 < 0.005, a
    assert a["area_alert_ha"] == 13.1558 and a["biomes"] == ["Cerrado"], a
    assert a["alert_sources_label"] == "DETER Cerrado, SAD Cerrado", a
    assert a["laudo_car_url"] == "https://plataforma.alerta.mapbiomas.org/alerta/1349329/car/" + code, a
    assert a["laudo_url"] == "https://plataforma.alerta.mapbiomas.org/alerta/1349329", a
    assert hit["disclaimer"] == m.DISCLAIMER and hit["text"]["disclaimer"] == m.DISCLAIMER
    assert "não é auto de infração" in m.DISCLAIMER and "conferida por analistas" in m.DISCLAIMER
    line = hit["text"]["items"][0]["text"]
    assert "1349329" in line and "11,84 ha dentro do imóvel" in line and "09/12/2024" in line, line
    assert hit["text"]["items"][0]["laudo_url"] == a["laudo_car_url"]
    assert_clean_public(hit)
    assert "_geometry_wkt" in a, "combinação precisa da geometria interna"
    ok("controle_1349329=found_area_laudo_credito_aviso")

    no_geom = m.parse_response(body, code)
    b = no_geom["alerts"][0]
    assert b["area_in_car_method"] == "cruzamento_mapbiomas" and b["area_in_car_ha"] == 11.8437, b
    ok("sem_geometria=usa_cruzamento_da_fonte")

    code, body = body_of("recente_pos_corte")
    recent = m.parse_response(body, code, CARS["recente_pos_corte"]["geometry"])
    assert recent["state"] == "found" and recent["alerts"][0]["alert_code"] == 1525843, recent
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
    other = m.parse_response(body, code, CARS["curvelo_vazio"]["geometry"])
    assert other["state"] == "not_found" and other["ignored"]["outside_current_car"] == 1, other
    ok("alerta_fora_da_geometria_atual=nao_listado")

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
        from shapely import wkt
        from shapely.ops import transform
        al["geometryWkt"] = transform(lambda x, y: (y, x), wkt.loads(al["geometryWkt"])).wkt
    r = m.parse_response(swapped, code)
    assert r["state"] == "pending" and r["pending_reason"] == "schema_unexpected", r
    ok("alerta_ilegivel_ou_eixo_trocado=pending_nunca_not_found")

    unvalidated = copy.deepcopy(body)
    unvalidated["data"]["ruralProperty"]["alerts"][0]["statusName"] = "pending_validation"
    r = m.parse_response(unvalidated, code)
    assert r["state"] == "not_found" and r["ignored"]["not_validated"] == 1 and r["alerts"] == [], r
    ok("so_publicado_conta_como_validado")

    assert m.parse_response(body, "XX-123")["pending_reason"] == "invalid_car_code"
    assert m.normalize_car_code(" mg-3120904-67cd.5f6c.3be2.49f3.98a3.ff3e.30d7.b1e8 ") == code
    ok("codigo_car_normalizado_e_validado")


# ------------------------------------------------------------------ rede simulada
def transport_rules() -> None:
    m.RETRY_PAUSE_SECONDS = 0.0
    code, body = body_of("controle_pre_corte")
    geom = CARS["controle_pre_corte"]["geometry"]
    seen: list[httpx.Request] = []

    def run(responses, **kw):
        seen.clear()
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            item = queue.pop(0) if queue else httpx.Response(599)
            if isinstance(item, Exception):
                raise item
            return item

        m.clear_cache()
        with client(handler) as c:
            return m.query_mapbiomas_alerta(code, geom, client=c, **kw)

    good = httpx.Response(200, json=body)
    r = run([good])
    req = json.loads(seen[0].content)
    assert seen[0].method == "POST" and req["variables"] == {"c": code} and req["query"] == m.QUERY
    assert "authorization" not in {k.lower() for k in seen[0].headers.keys()}, "consulta deve ser sem token"
    assert r["state"] == "found" and r["attempts"] == 1 and r["cached"] is False
    ok("consulta_publica_sem_token")

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

    r = run([httpx.Response(429), good])
    assert r["pending_reason"] == "rate_limited" and len(seen) == 1, r
    ok("limite_429=pending_sem_insistir")

    r = run([httpx.Response(403, json={"errors": [{"message": "Token de acesso inválido"}]})])
    assert r["pending_reason"] == "http_4xx" and len(seen) == 1, r
    r = run([httpx.Response(200, content=b"<html>manutencao</html>")])
    assert r["pending_reason"] == "invalid_json", r
    ok("4xx_e_html=pending")

    stop = threading.Event()
    stop.set()
    r = run([good], cancel_event=stop)
    assert r["pending_reason"] == "cancelled" and len(seen) == 0, r
    ok("cancel_event=nao_consulta")

    for res in (run([httpx.ReadTimeout("t")]), run([httpx.Response(500), httpx.Response(500)])):
        assert res["state"] == "pending" and res["alert_count"] is None
        assert_clean_public(res)

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
        assert m.query_mapbiomas_alerta(code, geom, client=c)["state"] == "pending"
        assert m.query_mapbiomas_alerta(code, geom, client=c)["state"] == "found"
    assert len(calls) == 2
    assert 0 < m.CACHE_TTL_SECONDS <= 6 * 3600
    m.clear_cache()
    ok("cache_curto_por_car=so_resposta_valida")

    assert m.query_mapbiomas_alerta("nao-e-car")["pending_reason"] == "invalid_car_code"
    ok("codigo_invalido=sem_rede")


# ------------------------------------------------------------------ combinação
def inpe(name: str, **deter_extra):
    fx = load(f"inpe_{name}.json")
    deter = {"state": "answered", "features": fx["deter_features"], "min_area_ha": 3.0, **deter_extra}
    prodes = {"ok": True, "hits": fx["prodes_hits"], "failed_layers": []}
    return deter, prodes


def area_of(geom) -> float:
    return m._area_ha(m._geometry(geom))


def combination_rules(parsed: dict) -> None:
    hit, recent, empty = parsed["hit"], parsed["recent"], parsed["empty"]
    car_recent = CARS["recente_pos_corte"]["geometry"]
    car_ctrl = CARS["controle_pre_corte"]["geometry"]

    # 1) Mesma abertura recente no DETER (23546_curr) e no MapBiomas (1525843): um evento, área = união.
    deter, prodes = inpe("recente_pos_corte", latest_image_date="2026-09-04")
    c = m.combine_deforestation_alerts(car_recent, recent, deter, prodes)
    assert c["state"] == "found" and c["event_count"] == 1 and len(c["events"]) == 1, c
    e = c["events"][0]
    assert e["sources"] == ["INPE (DETER)", "MapBiomas Alerta"] and e["mapbiomas_alert_codes"] == [1525843], e
    assert sorted(w["id"] for w in e["witnesses"]) == ["1525843", "23546_curr"], e
    biggest = max(w["area_in_car_ha"] for w in e["witnesses"])
    total = sum(w["area_in_car_ha"] for w in e["witnesses"])
    assert biggest <= e["area_in_car_ha"] + 1e-6 < total, (biggest, e["area_in_car_ha"], total)
    assert c["area_union_ha"] == e["area_in_car_ha"] < c["audit_sum_of_sources_ha"], c
    assert c["area_union_ha"] <= area_of(car_recent)
    assert e["first_seen"] == "2025-09-15" and c["cut_date"] == "2025-07-31" and c["recent_after"] == "2025-08-01"
    ok(f"deter_mais_mapbiomas=1_evento_uniao_{c['area_union_ha']}ha_nao_soma_{c['audit_sum_of_sources_ha']}ha")

    # Regra 4: área que o PRODES já marcou é dita, não somada.
    assert e["area_already_in_prodes_ha"] > 0 and 2025 in e["prodes_years"], e
    assert e["area_already_in_prodes_ha"] <= e["area_in_car_ha"]
    t = text_of(c)
    assert "já constavam no mapa anual PRODES" in t and "laudo nº 1525843" in t and "(INPE)" in t, t
    assert m.CREDIT in c["text"]["credits"] and m.DETER_CREDIT in c["text"]["credits"]
    assert c["text"]["disclaimer"].startswith(m.DISCLAIMER)
    assert c["credit_note"] and "não entra na conta" in c["credit_note"]
    assert_clean_public(c)
    ok("prodes_ja_marcado=dito_nao_somado")

    # 2) Alerta antigo (1349329, dez/2024) + DETER 2061786_hist + PRODES 2025: não é recente; anota o PRODES do ano certo.
    deter, prodes = inpe("controle_pre_corte")
    c = m.combine_deforestation_alerts(car_ctrl, hit, deter, prodes)
    assert c["state"] == "not_found" and c["events"] == [] and c["area_union_ha"] == 0.0, c
    rows = {r["id"]: r for r in c["before_cutoff"]}
    assert set(rows) == {"1349329", "2061786_hist"}, rows
    assert rows["1349329"]["prodes_years"] == [2025] and rows["1349329"]["prodes_overlap_ha"] > 10, rows
    notes = [i["text"] for i in c["text"]["items"] if i.get("section") == "prodes"]
    assert notes == ["Ocorrência PRODES de 2025: validada pelo MapBiomas Alerta (laudo nº 1349329)."], notes
    assert c["text"]["summary"].startswith("Nenhum alerta recente de desmatamento sobre o imóvel desde 01/08/2025.")
    assert_clean_public(c)
    ok("tres_testemunhas_antigas=0_recente_anotacao_prodes_2025")

    # 3) O corte anda quando sai o próximo PRODES: o evento de 2025-09 deixa de ser recente.
    deter, prodes = inpe("recente_pos_corte")
    moved = m.combine_deforestation_alerts(car_recent, recent, deter, prodes, prodes_cutoff="2026-07-31")
    assert moved["events"] == [] and moved["cut_date"] == "2026-07-31" and moved["cut_date_origin"] == "informado", moved
    assert {r["id"] for r in moved["before_cutoff"]} >= {"1525843", "23546_curr"}
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

    # 4) Sem módulo DETER: só MapBiomas, sem citar o INPE.
    only = m.combine_deforestation_alerts(car_recent, recent, None, prodes)
    assert only["sources"]["inpe_deter"] == "not_queried" and only["state"] == "found", only
    assert abs(only["area_union_ha"] - recent["alerts"][0]["area_in_car_ha"]) < 1e-3, only
    assert "INPE" not in text_of(only), text_of(only)
    ok("deter_ausente=so_mapbiomas_sem_citar_inpe")

    # 5) Pendências: nunca "nenhum alerta" sem as fontes responderem.
    pend_deter = {"state": "pending"}
    part = m.combine_deforestation_alerts(CARS["curvelo_vazio"]["geometry"], empty, pend_deter, None)
    assert part["state"] == "partial" and "INPE: consulta pendente." in part["text"]["summary"], part
    assert "Conferido em" not in part["text"]["summary"]
    mb_pending = m.parse_response(body_of("inexistente")[1], body_of("inexistente")[0])
    empty_deter = {"state": "answered", "features": []}
    part2 = m.combine_deforestation_alerts(CARS["curvelo_vazio"]["geometry"], mb_pending, empty_deter, None)
    assert part2["state"] == "partial" and "MapBiomas Alerta: consulta pendente." in part2["text"]["summary"], part2
    both = m.combine_deforestation_alerts(CARS["curvelo_vazio"]["geometry"], mb_pending, pend_deter, None)
    assert both["state"] == "pending" and "event_count" not in both and both.get("area_union_ha") is None, both
    as_list = m.combine_deforestation_alerts(CARS["curvelo_vazio"]["geometry"], mb_pending, [], None)
    assert as_list["sources"]["inpe_deter"] == "pending" and as_list["state"] == "pending", as_list
    no_car = m.combine_deforestation_alerts(None, recent, deter, prodes)
    assert no_car["state"] == "pending" and no_car["pending_reason"] == "car_geometry_missing", no_car
    # Alerta que existe mas chegou sem geometria (ex.: public_view) não some da conta.
    blind = m.combine_deforestation_alerts(car_recent, m.public_view(recent), None, None)
    assert blind["sources"]["mapbiomas_alerta"] == "pending" and blind["state"] == "pending", blind
    for res in (part, part2, both, as_list, no_car, blind):
        assert_clean_public(res)
        assert "nenhum alerta recente de desmatamento sobre o imóvel desde 01/08/2025. conferido" not in text_of(res).lower()
    clear = m.combine_deforestation_alerts(CARS["curvelo_vazio"]["geometry"], empty, empty_deter, None)
    assert clear["state"] == "not_found" and "Conferido em: INPE e MapBiomas Alerta (publicações até 08/09/2026)" in clear["text"]["summary"], clear
    ok("pendencia=partial_ou_pending_nunca_not_found")

    # 6) Testemunha repetida (a mesma geometria entregue duas vezes) não aumenta a área.
    twin = {"state": "answered", "features": [
        {"type": "Feature", "id": "copia", "properties": {"view_date": "2025-09-16", "gid": "copia"},
         "geometry": json.loads(json.dumps(__import__("shapely.geometry", fromlist=["mapping"]).mapping(
             m._geometry(recent["alerts"][0]["_geometry_wkt"]))))}]}
    dup = m.combine_deforestation_alerts(car_recent, recent, twin, None)
    assert dup["event_count"] == 1 and abs(dup["area_union_ha"] - only["area_union_ha"]) < 1e-3, dup
    assert dup["audit_sum_of_sources_ha"] > 1.9 * dup["area_union_ha"], dup
    ok("mesma_abertura_duas_vezes=area_uma_vez")

    # 7) Evento só do DETER: aviso próprio, sem dizer que foi validado pelo MapBiomas.
    solo = m.combine_deforestation_alerts(car_recent, None, deter, None)
    assert solo["state"] == "found" and all(not ev["mapbiomas_alert_codes"] for ev in solo["events"]), solo
    assert "MapBiomas" not in " ".join(i["text"] for i in solo["text"]["items"])
    assert solo["text"]["disclaimer"] == m.DETER_DISCLAIMER
    assert_clean_public(solo)
    ok("evento_so_deter=aviso_inpe")


if __name__ == "__main__":
    parsed = parse_real()
    parse_rules()
    transport_rules()
    combination_rules(parsed)
    for name in CHECKS:
        print("RX_F2_MAPBIOMAS_ALERTA_CHECK=" + name)
    print(f"RX_F2_MAPBIOMAS_ALERTA_GATE=PASS checks={len(CHECKS)}")

"""H1 gate: an empty or broken public layer never becomes "nenhum" for the client.

Usage:
  PYTHONPATH=. RX_RELEASE=OFF python scripts/h1_camadas_vazias_gate.py

Offline. HTTP is served from real responses captured on 14/09/2026
(scripts/fixtures/h1_camadas/embargos_ibama_real.json: official IBAMA embargo layer,
the empty SISCOM layer that production used, and two public SICAR geometries, with
no personal field) plus synthetic answers built from those geometries.

Positive control: the first scenario ("camada vazia") and the real Altamira hit are
written against interfaces that also exist before H1 (deploy_app.analyze_car,
live_report_adapter.build_live_payload). On the pre-H1 code they fail: the empty
layer is read as "Nenhum embargo IBAMA intersectante" and the Altamira property
with two real IBAMA embargoes gets zero.

s10 runs the production report chain end to end (analyze_car -> core retry v30 ->
v13 patches with the v16 truth guard -> client_payload) and s11 covers invalid
geometry, the monitoring snapshot and the retry policy. On commit 02f3b25 they fail
21 checks: a PRODES occurrence with one yearly layer failing ends as 0/BAIXO after
the retry, pending PRODES/SIGEF/ANM print zeros, a pending embargo leaves the
headline BAIXO, and an unmeasurable embargo feature is dropped as "nenhum".
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "scripts/fixtures/h1_camadas/embargos_ibama_real.json").read_text(encoding="utf-8"))
ALTAMIRA = FIXTURE["cars"]["altamira"]
CURVELO = FIXTURE["cars"]["curvelo"]

RESULTS: list[tuple[str, bool, str]] = []
HITS: list[str] = []  # every URL requested, for cache assertions
PARAMS: list[tuple[str, dict]] = []  # (url, query params) of every request, for field assertions

ABSENCE_EMBARGO = re.compile(r"nenhum embargo", re.I)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" — " + detail) if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- HTTP router
ROUTES: list = []  # (predicate(url, params) -> bool, responder(url, params) -> httpx.Response)


def route(pred, resp):
    ROUTES.insert(0, (pred, resp))


def json_response(data, status=200):
    return lambda url, params: httpx.Response(status, json=data)


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url).split("?", 1)[0]
    params = {k: v[-1] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
    HITS.append(url + ("#count" if params.get("returnCountOnly") == "true" else "") + ("#hits" if params.get("resultType") == "hits" else ""))
    PARAMS.append((url, params))
    for pred, resp in ROUTES:
        if pred(url, params):
            return resp(url, params)
    return httpx.Response(599, text="gate: no route for " + url)


_OrigAsync, _OrigSync = httpx.AsyncClient, httpx.Client


class _MockAsync(_OrigAsync):
    def __init__(self, *a, **k):
        k["transport"] = httpx.MockTransport(_handler)
        super().__init__(*a, **k)


class _MockSync(_OrigSync):
    def __init__(self, *a, **k):
        k["transport"] = httpx.MockTransport(_handler)
        super().__init__(*a, **k)


httpx.AsyncClient = _MockAsync
httpx.Client = _MockSync

EMB_ANY = lambda url, p: "embargos_siscom_brasil" in url or "adm_embargos_ibama_a" in url
IS_COUNT = lambda p: p.get("returnCountOnly") == "true"


def reset_routes():
    ROUTES.clear()
    HITS.clear()
    PARAMS.clear()
    try:
        import source_layer_guard
        source_layer_guard.reset_cache()
    except ImportError:
        pass


# ---------------------------------------------------------------- core helpers (pre-H1 compatible)
import deploy_app  # noqa: E402
import live_report_adapter  # noqa: E402


def _stub_ok(**extra):
    async def f(*a, **k):
        return {"ok": True, "feature_count": 0, "features": [], "hits": [], **extra}
    return f


REAL_QUERY_SIGEF = deploy_app.query_sigef
deploy_app.query_sigef = _stub_ok(source="stub")
deploy_app.query_anm = _stub_ok(source="stub")
deploy_app.query_prodes = _stub_ok(source="stub", candidate_layers=[])


def _car_result(car):
    return {"ok": True, "source": "SICAR", "feature_count": 1, "properties": car["properties"], "geometry": car["geometry"], "bbox": car["bbox"]}


async def run_core(car):
    deploy_app.fetch_car_live = lambda code, *a, **k: _car_result(car)
    result = await deploy_app.analyze_car(car["code"])
    payload = live_report_adapter.build_live_payload(result, "gate", "2026-09-14T00:00:00Z", "")
    return result, payload


def payload_text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def call_query_embargos(car):
    fn = deploy_app.query_embargos
    if "geometry" in inspect.signature(fn).parameters:
        return await fn(car["bbox"], car["geometry"])
    return await fn(car["bbox"])


# ---------------------------------------------------------------- scenarios
async def s1_empty_layer_positive_control():
    """The SISCOM case: HTTP 200, zero features, layer with 0 records."""
    reset_routes()
    route(lambda u, p: EMB_ANY(u, p) and IS_COUNT(p), json_response({"count": 0}))
    route(lambda u, p: EMB_ANY(u, p) and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": []}))
    result, payload = await run_core(CURVELO)
    emb = result.get("embargos_ibama") or {}
    check("camada vazia: embargo IBAMA não vira resposta (ok != True)", emb.get("ok") is not True, json.dumps({k: emb.get(k) for k in ("ok", "source", "feature_count")}, ensure_ascii=False))
    text = payload_text(payload)
    check("camada vazia: relatório não afirma 'nenhum embargo'", not ABSENCE_EMBARGO.search(text), (ABSENCE_EMBARGO.search(text) or [""])[0])
    enf = payload.get("enforcement") or {}
    check("camada vazia: relatório marca CONSULTA PENDENTE", enf.get("embargo_status") == "CONSULTA PENDENTE" and enf.get("embargo_pending") is True, str(enf.get("embargo_status")))
    import report_narrative
    try:
        narrative = json.dumps(report_narrative.build_narrative(payload), ensure_ascii=False)
    except Exception as exc:  # narrative needs more payload on some versions
        narrative = f"narrative_error:{exc}"
    check("camada vazia: narrativa não afirma ausência de embargo", "nenhum embargo" not in narrative.lower() and "não mostrou embargo" not in narrative.lower(), narrative[:200])


async def s2_real_hit_altamira():
    reset_routes()
    new_url = FIXTURE["sources"]["new_layer"]
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response(ALTAMIRA["new_layer_query"]))
    route(lambda u, p: "embargos_siscom_brasil" in u and IS_COUNT(p), json_response(FIXTURE["old_count"]))
    route(lambda u, p: "embargos_siscom_brasil" in u and not IS_COUNT(p), json_response(ALTAMIRA["old_layer_query"]))
    result, payload = await run_core(ALTAMIRA)
    emb = result.get("embargos_ibama") or {}
    ex = emb.get("exact") or {}
    check("Altamira real: consulta a camada oficial adm_embargos_ibama_a", any("adm_embargos_ibama_a" in h for h in HITS) and not any("embargos_siscom_brasil" in h for h in HITS), ",".join(sorted(set(HITS))))
    check("Altamira real: 2 embargos IBAMA sobre o imóvel", emb.get("ok") is True and ex.get("occurrence_count") == 2, json.dumps({"ok": emb.get("ok"), "count": ex.get("occurrence_count")}))
    area = float(ex.get("area_unique_ha") or 0)
    check("Altamira real: área embargada dentro do imóvel ≈ 20,93 ha (não a área declarada de 289 ha)", 20.5 < area < 21.4, str(area))
    items = ex.get("items") or []
    dates = [x.get("date") for x in items]
    check("Altamira real: datas e tipo do embargo", dates == ["13/03/2025", "23/11/2021"] and all(x.get("type") == "Desmatamento" and x.get("deforestation") is True for x in items), json.dumps(items, ensure_ascii=False)[:300])
    blob = json.dumps(result, ensure_ascii=False, default=str).lower() + payload_text(payload).lower()
    leaked = [k for k in ("cpf", "cnpj", "nome_embargado", "nome_imovel", "des_localizacao", "nom_pessoa") if k in blob]
    check("Altamira real: nenhum dado pessoal no resultado nem no relatório", not leaked, ",".join(leaked))
    enf = payload.get("enforcement") or {}
    check("Altamira real: relatório ALTO com área e data", enf.get("embargo_status") == "ALTO" and "13/03/2025" in str(enf.get("embargo_summary")) and "20,9" in str(enf.get("embargo_summary")).replace(".", ","), str(enf.get("embargo_summary")))
    con = payload.get("conclusion") or {}
    check("Altamira real: risco geral ALTO mesmo sem PRODES", con.get("overall_risk") == "ALTO", str(con.get("overall_risk")))
    import report_narrative
    try:
        narrative = json.dumps(report_narrative.build_narrative(payload), ensure_ascii=False)
    except Exception as exc:
        narrative = f"narrative_error:{exc}"
    check("Altamira real: narrativa do relatório traz TAD, data, tipo e área dentro do imóvel", "TAD POLKNZGA de 13/03/2025 (Desmatamento) 18.74 ha dentro do imóvel" in narrative, narrative[:400])


async def s3_real_clear_curvelo():
    """Negative control: a live layer with no embargo on the property IS an answer."""
    reset_routes()
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response(CURVELO["new_layer_query"]))
    route(lambda u, p: "embargos_siscom_brasil" in u and IS_COUNT(p), json_response(FIXTURE["old_count"]))
    route(lambda u, p: "embargos_siscom_brasil" in u and not IS_COUNT(p), json_response(CURVELO["old_layer_query"]))
    result, payload = await run_core(CURVELO)
    emb = result.get("embargos_ibama") or {}
    check("Curvelo real: camada viva e sem embargo = resposta (ok=True, 0)", emb.get("ok") is True and (emb.get("exact") or {}).get("occurrence_count") == 0, json.dumps({k: emb.get(k) for k in ("ok", "source_state")}))
    check("Curvelo real: relatório diz 'nenhum embargo' só com a camada viva", bool(ABSENCE_EMBARGO.search(payload_text(payload))))
    check("Curvelo real: contagem global da camada foi conferida", any(h.endswith("adm_embargos_ibama_a/FeatureServer/0/query#count") for h in HITS), ",".join(HITS))


async def s4_broken_answers():
    cases = {
        "HTTP 500": lambda u, p: httpx.Response(500, text="Server error"),
        "JSON com error": json_response({"error": {"code": 400, "message": "Unable to complete operation."}}),
        "HTML com 200": lambda u, p: httpx.Response(200, text="<html>manutenção</html>"),
        "sem lista features": json_response({"type": "FeatureCollection"}),
        "exceededTransferLimit em todas as páginas": json_response({"type": "FeatureCollection", "features": [], "exceededTransferLimit": True, "properties": {"exceededTransferLimit": True}}),
    }
    for label, responder in cases.items():
        reset_routes()
        route(lambda u, p: EMB_ANY(u, p) and IS_COUNT(p), json_response(FIXTURE["new_count"]))
        route(lambda u, p: EMB_ANY(u, p) and not IS_COUNT(p), responder)
        result, payload = await run_core(CURVELO)
        emb = result.get("embargos_ibama") or {}
        check(f"resposta quebrada ({label}): pendente, sem 'nenhum embargo'", emb.get("ok") is not True and not ABSENCE_EMBARGO.search(payload_text(payload)), json.dumps({k: emb.get(k) for k in ("ok", "detail")}, ensure_ascii=False))


async def s5_guard_contract():
    try:
        import source_layer_guard as g
    except ImportError:
        check("módulo source_layer_guard existe", False, "ausente")
        return
    # geometry missing
    reset_routes()
    r = await deploy_app.query_embargos(CURVELO["bbox"])
    check("sem geometria do CAR: embargo pendente", r.get("ok") is False)
    # cache: two zero answers cost one global count; failures are retried after the short TTL
    reset_routes()
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response(CURVELO["new_layer_query"]))
    await call_query_embargos(CURVELO)
    await call_query_embargos(CURVELO)
    counts = [h for h in HITS if h.endswith("#count")]
    check("contagem global em cache (1 pedido para 2 consultas)", len(counts) == 1, str(counts))
    check("cache de horas", g.TTL_OK_SECONDS >= 3600 and g.TTL_FAIL_SECONDS <= 300, f"{g.TTL_OK_SECONDS}/{g.TTL_FAIL_SECONDS}")
    reset_routes()
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), lambda u, p: httpx.Response(503, text="down"))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response(CURVELO["new_layer_query"]))
    r1 = await call_query_embargos(CURVELO)
    check("contagem global falhou: zero vira pendente", r1.get("ok") is False, str(r1.get("layer_guard")))
    original_now = g._now
    try:
        g._now = lambda: original_now() + g.TTL_FAIL_SECONDS + 1
        route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
        r2 = await call_query_embargos(CURVELO)
    finally:
        g._now = original_now
    check("falha não fica presa no cache: nova tentativa responde", r2.get("ok") is True, str(r2.get("layer_guard")))
    # below floor (layer partially emptied)
    reset_routes()
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response({"count": 120}))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response(CURVELO["new_layer_query"]))
    r3 = await call_query_embargos(CURVELO)
    check("camada abaixo do piso: zero vira pendente", r3.get("ok") is False and (r3.get("layer_guard") or {}).get("reason") == "layer_below_floor", str(r3.get("layer_guard")))
    # verdict unit rules
    reset_routes()
    check("base parada: zero nunca é resposta", g.zero_verdict("icmbio_embargos", zero=True)["answer"] is False)
    check("base parada: ocorrência encontrada continua sendo mostrada", g.zero_verdict("icmbio_embargos", zero=False)["answer"] is True)
    check("resposta incompleta: nunca é resposta, nem com ocorrência", g.zero_verdict("ibama_embargos", zero=False, answer_problem="exceeded_transfer_limit")["answer"] is False)
    check("registro sem a camada SISCOM vazia", all("embargos_siscom_brasil" not in e["url"] for e in g.LAYERS.values()))


def _feature(geom, props):
    return {"type": "Feature", "geometry": geom, "properties": props}


async def s6_territorial_constraints():
    try:
        import source_layer_guard  # noqa: F401
    except ImportError:
        check("restrições territoriais com guarda", False, "source_layer_guard ausente")
        return
    import parity_public_layers  # noqa: F401  (registers floresta_publica / sitio_arqueologico)
    import territorial_constraints as tc
    counts = {"lim_terra_indigena_a": 627, "lim_unidades_conserva": 0, "lim_quilombos_incra_a": 440, "assentamentos_incra": 8377,
              "adm_embargo_icmbio_a": 8025, "lim_floresta_publica_a": 4763, "lim_sitios_arqueologicos_iphan_a": 13267}
    layer_ids = {"lim_terra_indigena_a": 12, "assentamentos_incra": 1, "lim_floresta_publica_a": 8}
    hit_icmbio = {"value": False}

    def responder(url, params):
        name = next((k for k in counts if k in url), None)
        if url.endswith("/FeatureServer"):
            return httpx.Response(200, json={"layers": [{"id": layer_ids.get(name, 0)}]})
        if params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": counts[name]})
        feats = []
        if name == "adm_embargo_icmbio_a" and hit_icmbio["value"]:
            feats = [_feature(CURVELO["geometry"], {"nome_uc": "UC teste", "autuado": "PESSOA X", "cpf_cnpj": "000"})]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})

    reset_routes()
    route(lambda u, p: "pamgia.ibama.gov.br" in u and any(k in u for k in counts), responder)
    out = await tc.query_territorial_constraints(CURVELO["geometry"], CURVELO["bbox"])
    s = out["services"]
    check("TI com camada viva e sem sobreposição = resposta", s["terra_indigena"].get("ok") is True, str(s["terra_indigena"].get("layer_guard")))
    check("UC com camada de 0 registros = pendente", s["unidade_conservacao"].get("ok") is False and (s["unidade_conservacao"].get("layer_guard") or {}).get("reason") == "layer_empty", str(s["unidade_conservacao"].get("layer_guard")))
    check("ICMBio (base parada em 2022) sem ocorrência = pendente", s["embargo_icmbio"].get("ok") is False)
    check("Floresta pública PAMGIA (até 2020) sem ocorrência = pendente", s["floresta_publica"].get("ok") is False)
    check("IPHAN PAMGIA (parado 11/2025, sem pontos) sem ocorrência = pendente", s["sitio_arqueologico"].get("ok") is False)
    hit_icmbio["value"] = True
    reset_routes()
    route(lambda u, p: "pamgia.ibama.gov.br" in u and any(k in u for k in counts), responder)
    out = await tc.query_territorial_constraints(CURVELO["geometry"], CURVELO["bbox"])
    ic = out["services"]["embargo_icmbio"]
    check("ICMBio parado com ocorrência: mostra a ocorrência", ic.get("ok") is True and ic.get("occurrence_count") == 1)
    blob = json.dumps(ic.get("occurrences"), ensure_ascii=False).lower()
    check("ICMBio: nome do autuado e CPF/CNPJ não saem", "pessoa x" not in blob and "cpf" not in blob, blob[:200])
    layer_ids["lim_terra_indigena_a"] = 13
    reset_routes()
    route(lambda u, p: "pamgia.ibama.gov.br" in u and any(k in u for k in counts), responder)
    out = await tc.query_territorial_constraints(CURVELO["geometry"], CURVELO["bbox"])
    check("camada trocada de endereço (id novo) = pendente", out["services"]["terra_indigena"].get("ok") is False)


async def s7_autos_prodes_fire():
    try:
        import source_layer_guard  # noqa: F401
    except ImportError:
        check("autos/PRODES/focos com guarda", False, "source_layer_guard ausente")
        return
    import live_extra_sources as les
    reset_routes()
    route(lambda u, p: "adm_auto_infracao_p" in u and IS_COUNT(p), json_response({"count": 0}))
    route(lambda u, p: "adm_auto_infracao_p" in u and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": []}))
    r = await les.query_ibama_autos(CURVELO["geometry"], CURVELO["bbox"])
    check("autos IBAMA com camada vazia = pendente", r.get("ok") is False and (r.get("layer_guard") or {}).get("reason") == "layer_empty", str(r.get("layer_guard")))
    reset_routes()
    route(lambda u, p: "adm_auto_infracao_p" in u and IS_COUNT(p), json_response({"count": 710298}))
    route(lambda u, p: "adm_auto_infracao_p" in u and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": []}))
    r = await les.query_ibama_autos(CURVELO["geometry"], CURVELO["bbox"])
    check("autos IBAMA com camada viva e sem auto = resposta", r.get("ok") is True and r.get("occurrence_count") == 0, json.dumps({k: r.get(k) for k in ("ok", "detail", "layer_guard", "occurrence_count")}, ensure_ascii=False, default=str))

    auto = les._public_auto({"num_auto_infracao": "9A1B2C3D", "des_infracao": "Desmatar area nativa", "val_auto_infracao": 130000, "tipo_multa": "Aberta", "dat_hora_auto_infracao": 1757030400000, "des_status_formulario": "Lavrado"})
    check("autos IBAMA: valor e data vêm dos campos publicados (antes: R$ 0,00 e sem data)", auto["fine_value"] == 130000.0 and auto["date"] == "2025-09-05" and auto["auto_number"] == "9A1B2C3D", json.dumps(auto, ensure_ascii=False))
    import prodes_fast_v24 as pf
    names = ["prodes-cerrado-nb:yearly_deforestation", "prodes-pantanal-nb:yearly_deforestation", "prodes-pampa-nb:yearly_deforestation",
             "prodes-mata-atlantica-nb:yearly_deforestation", "prodes-legal-amz:yearly_deforestation", "prodes-caatinga-nb:yearly_deforestation",
             "prodes-amazon-nb:yearly_deforestation_biome"]
    caps = "<wfs:WFS_Capabilities xmlns:wfs='http://www.opengis.net/wfs/2.0'><FeatureTypeList>" + "".join(f"<FeatureType><Name>{n}</Name><Title>{n}</Title></FeatureType>" for n in names) + "</FeatureTypeList></wfs:WFS_Capabilities>"

    def prodes(mode):
        def resp(url, params):
            if params.get("request") == "GetCapabilities":
                return httpx.Response(200, text=caps)
            name = params.get("typeNames")
            if params.get("resultType") == "hits":
                return httpx.Response(200, text=f'<wfs:FeatureCollection numberMatched="{0 if mode == "empty_layer" and "cerrado" in name else 50000}"/>')
            if mode == "failed" and "cerrado" in name:
                return httpx.Response(504, text="timeout")
            if mode == "truncated" and "cerrado" in name:
                return httpx.Response(200, json={"type": "FeatureCollection", "features": [], "numberMatched": 3500})
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        return resp

    for mode, expect in (("clean", True), ("failed", False), ("truncated", False), ("empty_layer", False)):
        reset_routes()
        pf._layer_cache.update(ts=0.0, layers=[])
        route(lambda u, p: "terrabrasilis" in u, prodes(mode))
        r = await pf.query_prodes_fast(CURVELO["bbox"])
        check(f"PRODES {mode}: ok={expect}", r.get("ok") is expect, json.dumps({k: r.get(k) for k in ("ok", "source_state", "layer_guard")}, ensure_ascii=False))

    import fire_live
    reset_routes()
    route(lambda u, p: u.endswith("/10min/"), lambda u, p: httpx.Response(200, text="focos_10min_20250101_0000.csv"))
    route(lambda u, p: u.endswith(".csv"), lambda u, p: httpx.Response(200, text="lat;lon\n"))
    r = await fire_live.fetch_recent_foci(6)
    check("focos INPE com último arquivo velho = pendente", r.get("ok") is False and r.get("feed_problem") == "focus_feed_stale", str(r.get("feed_problem")))
    reset_routes()
    route(lambda u, p: u.endswith("/10min/"), lambda u, p: httpx.Response(200, text="<html>vazio</html>"))
    r = await fire_live.fetch_recent_foci(6)
    check("focos INPE sem arquivo listado = pendente", r.get("ok") is False)


def s8_sync_sources():
    try:
        import source_layer_guard as g
    except ImportError:
        check("pivôs/ANM/outorgas com guarda", False, "source_layer_guard ausente")
        return
    import pivots_ana, anm_fast_v29, water_mg
    reset_routes()
    route(lambda u, p: "PIVOS_2022" in u and IS_COUNT(p), json_response({"count": 0}))
    pivots_ana._curl_json = lambda url, max_time=55: {"ok": True, "json": {"features": []}}
    r = pivots_ana.query_pivots_ana(CURVELO["geometry"], CURVELO["bbox"])
    check("pivôs ANA com camada vazia = pendente", r.get("ok") is False and (r.get("layer_guard") or {}).get("reason") == "layer_empty", str(r.get("layer_guard")))
    reset_routes()
    route(lambda u, p: "PIVOS_2022" in u and IS_COUNT(p), json_response({"count": 30040}))
    r = pivots_ana.query_pivots_ana(CURVELO["geometry"], CURVELO["bbox"])
    check("pivôs ANA com camada viva = resposta", r.get("ok") is True)
    pivots_ana._curl_json = lambda url, max_time=55: {"ok": True, "json": {"features": [], "exceededTransferLimit": True}}
    r = pivots_ana.query_pivots_ana(CURVELO["geometry"], CURVELO["bbox"])
    check("pivôs ANA truncado = pendente", r.get("ok") is False)

    reset_routes()
    route(lambda u, p: "SIGMINE" in u and IS_COUNT(p), json_response({"count": 0}))
    anm_fast_v29.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b'{"type":"FeatureCollection","features":[]}', stderr=b"")
    r = anm_fast_v29._curl_anm_bbox(CURVELO["bbox"])
    check("ANM com camada vazia = pendente", r.get("ok") is False and (r.get("layer_guard") or {}).get("reason") == "layer_empty", str(r.get("layer_guard")))
    anm_fast_v29.subprocess.run = subprocess.run

    def wfs_curl(features):
        return lambda url, expect_json=False, max_time=40: {"ok": True, "json": {"type": "FeatureCollection", "features": features}}
    reset_routes()
    route(lambda u, p: "meioambiente.mg.gov.br" in u and p.get("resultType") == "hits", lambda u, p: httpx.Response(200, text='<x numberMatched="0"/>'))
    water_mg._curl = wfs_curl([])
    r = water_mg.query_outorgas_mg(CURVELO["geometry"], CURVELO["bbox"], 5.0, "MG")
    check("outorgas MG com camada vazia = pendente", r.get("ok") is False)
    reset_routes()
    route(lambda u, p: "meioambiente.mg.gov.br" in u and p.get("resultType") == "hits", lambda u, p: httpx.Response(200, text='<x numberMatched="58741"/>'))
    r = water_mg.query_outorgas_mg(CURVELO["geometry"], CURVELO["bbox"], 5.0, "MG")
    check("outorgas MG com camadas vivas = resposta", r.get("ok") is True)
    r = water_mg.query_outorgas_mg(ALTAMIRA["geometry"], ALTAMIRA["bbox"], 5.0, "PA")
    check("outorgas fora de MG (IDE-Sisema só cobre MG) = pendente", r.get("ok") is False)


def s9_static_contract():
    runtime = [p for p in ROOT.glob("*.py")]
    offenders = [p.name for p in runtime if re.search(r"https://[^'\"\s]*embargos_siscom_brasil", p.read_text(encoding="utf-8", errors="ignore"))]
    check("nenhum módulo do sistema usa a camada SISCOM vazia", not offenders, ",".join(offenders))
    tabs = (ROOT / "portal_property_tabs.py").read_text(encoding="utf-8")
    check("aba Embargos: consulta pendente discreta, sem 'FONTE PARCIAL' para IBAMA", "ib.ok?fmt(ib.count,0):'CONSULTA PENDENTE'" in tabs and "ib.ok?fmt(ib.count,0):'FONTE PARCIAL'" not in tabs)
    from report_ptbr_v50 import client_payload
    cp = client_payload({"environment": {"layer_rows": [["Sítio Arqueológico — IPHAN", "FONTE INDISPONÍVEL NESTA EMISSÃO", "IPHAN"]]},
                         "compliance": [{"label": "Embargos ICMBio", "text": "Fonte indisponível nesta emissão.", "badge": "NÃO CONSULTADO"}],
                         "enforcement": {"embargo_count": 0, "embargo_pending": True},
                         "sources": [{"name": "SIGEF", "status": "INDISPONÍVEL", "description": "Motivo: consulta_pendente:stale_base"}]})
    check("relatório: camada sem resposta vira CONSULTA PENDENTE discreta, sem motivo técnico",
          cp["environment"]["layer_rows"][0][1] == "CONSULTA PENDENTE" and cp["compliance"][0]["badge"] == "CONSULTA PENDENTE"
          and cp["sources"][0]["status"] == "CONSULTA PENDENTE" and "stale_base" not in json.dumps(cp, ensure_ascii=False), json.dumps(cp, ensure_ascii=False))
    check("relatório: indicador de embargos não mostra 0 quando a consulta ficou pendente",
          cp["enforcement"]["embargo_count"] == "PENDENTE" and "pendente" in cp["enforcement"]["embargo_sources_label"], json.dumps(cp.get("enforcement"), ensure_ascii=False))
    ci = (ROOT / ".github/workflows/quality-gate.yml").read_text(encoding="utf-8")
    check("gate H1 exigido no quality-gate", "scripts/h1_camadas_vazias_gate.py" in ci)


# ---------------------------------------------------------------- report chain (end to end)
PRODES_NAMES = ["prodes-cerrado-nb:yearly_deforestation", "prodes-pantanal-nb:yearly_deforestation", "prodes-pampa-nb:yearly_deforestation",
                "prodes-mata-atlantica-nb:yearly_deforestation", "prodes-legal-amz:yearly_deforestation", "prodes-caatinga-nb:yearly_deforestation",
                "prodes-amazon-nb:yearly_deforestation_biome"]
PRODES_CAPS = ("<wfs:WFS_Capabilities xmlns:wfs='http://www.opengis.net/wfs/2.0'><FeatureTypeList>"
               + "".join(f"<FeatureType><Name>{n}</Name><Title>{n}</Title></FeatureType>" for n in PRODES_NAMES)
               + "</FeatureTypeList></wfs:WFS_Capabilities>")
# Texts that state "no PRODES / 0 parcels / 0 mining processes". Each has a negative control below.
PRODES_ZERO = re.compile(r'Nenhuma interseção PRODES|PRODES: nenhuma|"PRODES", "0 ocorrência|PRODES completo", "0 ocorrência|PRODES: 0 ocorrência|"label": "PRODES", "text": "0 ')
SIGEF_ZERO = re.compile(r"\b0 parcela")
ANM_ZERO = re.compile(r'minerais críticos", 0\]|Terras raras - processos ANM", 0\]|não identificou sinal classificado|"process_count": 0\b')


def _prodes_feature_inside(car):
    from shapely.geometry import mapping, shape
    inner = shape(car["geometry"]).buffer(-0.0005)
    return {"type": "Feature", "id": "gate-prodes-1", "geometry": mapping(inner),
            "properties": {"year": 2021, "class_name": "d2021", "image_date": "2021-07-01", "main_class": "desmatamento"}}


def _prodes_feature_outside(car):
    """A PRODES polygon inside the query envelope but outside the property."""
    from shapely.geometry import box, mapping, shape
    minx, miny, maxx, maxy = car["bbox"]
    w, h = (maxx - minx) / 50, (maxy - miny) / 50
    for corner in (box(minx, miny, minx + w, miny + h), box(maxx - w, miny, maxx, miny + h), box(minx, maxy - h, minx + w, maxy), box(maxx - w, maxy - h, maxx, maxy)):
        if not shape(car["geometry"]).intersects(corner):
            return {"type": "Feature", "id": "gate-prodes-out", "geometry": mapping(corner), "properties": {"year": 2020, "class_name": "d2020"}}
    raise AssertionError("gate: no envelope corner outside the property")


def _prodes_responder(mode, car):
    def resp(url, params):
        if params.get("request") == "GetCapabilities":
            return httpx.Response(200, text=PRODES_CAPS)
        name = params.get("typeNames") or ""
        if params.get("resultType") == "hits":
            return httpx.Response(200, text=f'<x numberMatched="{0 if mode == "empty_layer" and "caatinga" in name else 50000}"/>')
        if mode == "hit_and_failed" and "cerrado" in name:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [_prodes_feature_inside(car)]})
        if mode == "outside_and_failed" and "cerrado" in name:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [_prodes_feature_outside(car)]})
        if mode in ("failed", "hit_and_failed", "outside_and_failed") and "caatinga" in name:
            return httpx.Response(504, text="gateway timeout")
        if mode == "truncated" and "caatinga" in name:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [], "numberMatched": 3500})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    return resp


def s10_report_chain():
    """analyze_car -> production core retry (v30) -> v13 patch chain with the v16 truth guard -> client_payload.

    Positive control (commit 02f3b25): a PRODES occurrence with one yearly layer failing
    ends as "0 ocorrência(s) exatas" and BAIXO after the retry; a pending PRODES, SIGEF or
    ANM prints zeros in the final report; a pending embargo leaves the headline BAIXO.
    """
    import os
    os.environ["RX_RASTERIO_RUNTIME_INSTALL"] = "off"
    # Same order as sitecustomize._load_report_after_report_api (production).
    import prodes_fast_v24 as pf
    import anm_fast_v29  # noqa: F401
    import report_perf_v24  # noqa: F401
    import core_retry_fast_v29 as cr
    import report_v19_patch  # noqa: F401
    import report_visual_identity_v28  # noqa: F401
    import report_extras_perf_v30  # noqa: F401
    import live_report_adapter_v13 as v13
    import report_api
    import report_ptbr_v50
    import critical_minerals

    async def offline_extras(*a, **k):
        return [{"ok": False, "detail": "gate_offline"}] * 8

    def final_payload(result):
        v13._extras = offline_extras
        v13.v11._patch_agro = lambda payload, result, car_code: payload
        v13.build_premium_property_report_v6 = lambda path, payload: (Path(path).write_bytes(b"%PDF-1.4"), "gate")[1]
        v13.build_technical_map = lambda result, path, include_prodes=True: str(path)
        v13._stamp_image = lambda *a, **k: None
        meta = v13.generate_live_report(result, (result["car"].get("properties") or {}).get("cod_imovel") or "GATE")
        payload = json.loads(Path(meta["payload_path"]).read_text(encoding="utf-8"))
        return report_ptbr_v50.client_payload(payload)

    async def anm_answered(bbox):
        return {"ok": True, "status": 200, "feature_count": 0, "features": [], "source": "ANM/SIGMINE", "source_state": "answered_clear"}

    async def anm_pending(bbox):
        return {"ok": False, "status": 200, "feature_count": 0, "features": [], "source": "ANM/SIGMINE", "source_state": "pending",
                "layer_guard": {"reason": "layer_empty"}, "detail": "consulta_pendente:layer_empty"}

    async def sigef_answered(bbox):
        return {"ok": True, "feature_count": 1, "features": [{"attributes": {}}], "source": "stub"}

    def core(car, *, prodes="clean", emb_total=91197, sigef=None, anm=None, sgb_fail=False):
        reset_routes()
        pf._layer_cache.update(ts=0.0, layers=[])
        deploy_app.query_prodes = pf.query_prodes_fast
        report_api.query_prodes = pf.query_prodes_fast
        deploy_app.query_anm = anm or anm_answered
        deploy_app.query_sigef = sigef or sigef_answered
        report_api.query_sigef = deploy_app.query_sigef  # production: the same function in both modules
        deploy_app.fetch_car_live = lambda code, *a, **k: _car_result(car)
        route(lambda u, p: "terrabrasilis" in u, _prodes_responder(prodes, car))
        route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response({"count": emb_total}))
        route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": []}))
        route(lambda u, p: "lim_imovel_sigef_publico_a" in u, json_response({"features": []}))
        caps = ("<WMT_MS_Capabilities><Capability><Layer><Title>SGB</Title>"
                "<Layer queryable='1'><Name>geosgb:ocorrencias_terras_raras</Name><Title>Ocorrências de terras raras</Title></Layer>"
                "</Layer></Capability></WMT_MS_Capabilities>")
        route(lambda u, p: "sgb.gov.br" in u and p.get("request") == "GetCapabilities", lambda u, p: httpx.Response(200, text=caps))
        route(lambda u, p: "sgb.gov.br" in u and p.get("REQUEST") == "GetFeatureInfo",
              (lambda u, p: httpx.Response(500, text="erro")) if sgb_fail else json_response({"type": "FeatureCollection", "features": []}))

        async def run():
            r = await deploy_app.analyze_car(car["code"])
            r = await cr._retry_failed_core_v30(r)
            r["critical_minerals"] = await critical_minerals.query_critical_minerals(car["geometry"], r.get("anm"))
            return r
        result = asyncio.run(run())
        cp = final_payload(result)
        return result, cp, json.dumps(cp, ensure_ascii=False)

    def compliance(cp, label):
        return next((x for x in cp.get("compliance") or [] if x.get("label") == label), {})

    # A. a real PRODES occurrence survives a failed yearly layer and the retry
    result, cp, text = core(CURVELO, prodes="hit_and_failed")
    pro = result.get("prodes") or {}
    check("cadeia: PRODES com ocorrência e camada anual falhando continua com a ocorrência depois da nova tentativa",
          pro.get("ok") is True and (pro.get("exact") or {}).get("occurrence_count") == 1,
          json.dumps({k: pro.get(k) for k in ("ok", "source_state", "detail")} | {"count": (pro.get("exact") or {}).get("occurrence_count")}, ensure_ascii=False))
    check("cadeia: essa ocorrência mantém risco MODERADO e selo ATENÇÃO no relatório final",
          (cp.get("conclusion") or {}).get("overall_risk") == "MODERADO" and compliance(cp, "PRODES").get("badge") == "ATENÇÃO",
          json.dumps({"overall": (cp.get("conclusion") or {}).get("overall_risk"), "prodes": compliance(cp, "PRODES")}, ensure_ascii=False))
    check("cadeia: leitura parcial do PRODES não se apresenta como contagem fechada",
          "pode aumentar" in str(((cp.get("environment") or {}).get("prodes") or {}).get("summary")), str(((cp.get("environment") or {}).get("prodes") or {}).get("summary")))

    # B. PRODES without an answer never becomes "nenhuma" / 0 / BAIXO
    for mode in ("failed", "truncated", "empty_layer", "outside_and_failed"):
        result, cp, text = core(CURVELO, prodes=mode)
        pro = result.get("prodes") or {}
        m = PRODES_ZERO.search(text)
        check(f"cadeia: PRODES {mode} = pendente, sem contagem", pro.get("ok") is False and (pro.get("exact") or {}).get("occurrence_count") is None,
              json.dumps({k: pro.get(k) for k in ("ok", "source_state")}, ensure_ascii=False))
        check(f"cadeia: PRODES {mode} não vira 'nenhuma'/0 no relatório final", not m, text[max(0, m.start() - 120): m.end() + 60] if m else "")
        check(f"cadeia: PRODES {mode} marca CONSULTA PENDENTE e o risco geral não fica BAIXO",
              compliance(cp, "PRODES").get("badge") == "CONSULTA PENDENTE" and (cp.get("conclusion") or {}).get("overall_risk") != "BAIXO",
              json.dumps({"overall": (cp.get("conclusion") or {}).get("overall_risk"), "prodes": compliance(cp, "PRODES")}, ensure_ascii=False))

    # C. negative control: every layer answered and the embargo layer is alive
    result, cp, text = core(CURVELO, prodes="clean")
    check("cadeia (controle negativo): PRODES e embargo respondendo = 'nenhuma interseção' e risco BAIXO",
          bool(PRODES_ZERO.search(text)) and (cp.get("conclusion") or {}).get("overall_risk") == "BAIXO",
          str((cp.get("conclusion") or {}).get("overall_risk")))
    check("cadeia (controle negativo): ANM e SGB respondendo = triagem 'não identificou'", "não identificou sinal classificado" in text)

    # D. pending embargo: the headline never reads BAIXO
    result, cp, text = core(CURVELO, prodes="clean", emb_total=0)
    check("cadeia: embargo pendente não deixa o risco geral BAIXO", (cp.get("conclusion") or {}).get("overall_risk") != "BAIXO",
          str((cp.get("conclusion") or {}).get("overall_risk")))

    # E. SIGEF mirror stopped in 2022: pending, no parcel count, not retried
    result, cp, text = core(CURVELO, prodes="clean", sigef=REAL_QUERY_SIGEF)
    m = SIGEF_ZERO.search(text)
    check("cadeia: SIGEF parado sem parcela = pendente, sem '0 parcela' no relatório final",
          (result.get("sigef") or {}).get("ok") is False and not m, text[max(0, m.start() - 120): m.end() + 40] if m else "")
    sigef_calls = [h for h in HITS if "lim_imovel_sigef_publico_a" in h]
    check("cadeia: SIGEF parado não é consultado de novo na nova tentativa", len(sigef_calls) == 1, str(len(sigef_calls)))

    # F. ANM pending: no zero processes, no "não identificou"
    result, cp, text = core(CURVELO, prodes="clean", anm=anm_pending)
    m = ANM_ZERO.search(text)
    check("cadeia: ANM pendente não vira 0 processo nem 'não identificou' no relatório final", not m, text[max(0, m.start() - 120): m.end() + 40] if m else "")
    check("cadeia: ANM pendente não tem contagem de processo na triagem mineral",
          ((result.get("critical_minerals") or {}).get("anm") or {}).get("process_count") is None)

    # G. SGB GetFeatureInfo failing is not "no signal"
    result, cp, text = core(CURVELO, prodes="clean", sgb_fail=True)
    cm = result.get("critical_minerals") or {}
    check("SGB com camada sem resposta = triagem pendente (ok=False), sem 'não identificou'",
          cm.get("ok") is False and "não identificou sinal classificado" not in text, json.dumps((cm.get("sgb") or {}).get("detail"), ensure_ascii=False))


async def s11_geometry_monitoring_retry():
    import source_layer_guard as g
    from shapely.geometry import box, mapping
    # H. invalid CAR geometry (bow-tie) with a real embargo inside: never silently "nenhum"
    minx, miny, maxx, maxy = CURVELO["bbox"]
    bow = {"type": "Polygon", "coordinates": [[[minx, miny], [maxx, maxy], [maxx, miny], [minx, maxy], [minx, miny]]]}
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    dx, dy = (maxx - minx) / 6, (maxy - miny) / 6
    inside = mapping(box(cx - dx, miny + dy / 4, cx + dx, cy - dy / 4))
    props = {"objectid": 1, "num_tad": "GATE1", "dat_embargo": 1700000000000, "tipo_area": "Desmatamento", "sit_desmatamento": "D", "origem_geom": "Polígono",
             "nome_embargado": "PESSOA FICTICIA GATE", "cpf_cnpj_embargado": "000.000.000-00"}
    reset_routes()
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": inside, "properties": props}]}))
    r = await deploy_app.query_embargos(CURVELO["bbox"], bow)
    check("CAR com geometria inválida e embargo dentro: embargo não some", r.get("ok") is True and ((r.get("exact") or {}).get("occurrence_count") or 0) >= 1,
          json.dumps({k: r.get(k) for k in ("ok", "source_state", "detail")} | {"count": (r.get("exact") or {}).get("occurrence_count")}, ensure_ascii=False))
    blob = json.dumps(r, ensure_ascii=False, default=str).lower()
    check("feição com nome e CPF/CNPJ do autuado: nada disso sai no resultado", "pessoa ficticia" not in blob and "000.000.000-00" not in blob and "cpf" not in blob, blob[:200])
    fields = [p.get("outFields") for u, p in PARAMS if "adm_embargos_ibama_a" in u and not IS_COUNT(p)]
    personal = re.compile(r"\*|nome|cpf|cnpj|des_localizacao|nom_pessoa", re.I)
    check("pedido à camada de embargos só pede campos não pessoais (outFields explícito)", bool(fields) and not any(personal.search(str(f or "*")) for f in fields), str(fields[:1]))
    reset_routes()
    broken = {"type": "Polygon", "coordinates": [[[cx, cy], [cx + dx, cy]]]}
    route(lambda u, p: "adm_embargos_ibama_a" in u and IS_COUNT(p), json_response(FIXTURE["new_count"]))
    route(lambda u, p: "adm_embargos_ibama_a" in u and not IS_COUNT(p), json_response({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": broken, "properties": props}]}))
    r = await deploy_app.query_embargos(CURVELO["bbox"], CURVELO["geometry"])
    check("feição de embargo que não pode ser medida = pendente (nunca 'nenhum')", r.get("ok") is False and "geometry_error" in str((r.get("layer_guard") or {}).get("reason")),
          json.dumps({k: r.get(k) for k in ("ok", "layer_guard")}, ensure_ascii=False))

    # I. pending result carries no number
    res = g.apply_verdict({"ok": True, "exact": {"available": True, "occurrence_count": 0, "area_unique_ha": 0.0, "occurrences": []}}, {"answer": False, "state": "pending", "reason": "layer_empty"})
    check("resultado pendente fica sem contagem (exact.occurrence_count=None)", res["exact"]["occurrence_count"] is None and res["exact"]["available"] is False, json.dumps(res["exact"]))

    # K. monitoring: pending is unknown, SISCOM-era counts are not comparable
    import monitoring_store as ms
    car = {"ok": True, "properties": CURVELO["properties"]}
    answered = lambda n: {"car": car, "embargos_ibama": {"ok": True, "exact": {"occurrence_count": n, "area_unique_ha": 1.0 * n}}}
    pending = {"car": car, "embargos_ibama": {"ok": False, "exact": {"occurrence_count": 0, "area_unique_ha": 0.0}}}
    snap_pending = ms.compact_snapshot(pending)
    check("monitoramento: embargo pendente grava contagem desconhecida (None), não 0", snap_pending["ibama_embargo_count"] is None, str(snap_pending["ibama_embargo_count"]))
    old_siscom = {k: v for k, v in ms.compact_snapshot(answered(0)).items() if k != "ibama_embargo_base"}
    diff = ms._diff(old_siscom, ms.compact_snapshot(answered(1)))
    check("monitoramento: foto antiga da camada SISCOM vazia não gera 'novo embargo' com a base oficial", "ibama_embargo_count" not in diff, json.dumps(diff, ensure_ascii=False))
    base0 = ms.compact_snapshot(answered(0))
    carried = ms._carry_forward(base0, snap_pending)
    check("monitoramento: consulta pendente não apaga a última resposta", carried["ibama_embargo_count"] == 0 and not ms._diff(base0, carried), json.dumps(ms._diff(base0, carried)))
    sev, msg = ms._classify_alert(ms._diff(carried, ms.compact_snapshot(answered(1))))
    check("monitoramento: embargo novo depois de uma consulta pendente ainda alerta", sev == "critical" and "embargo IBAMA" in msg, f"{sev}:{msg}")
    sev, msg = ms._classify_alert(ms._diff(base0, ms.compact_snapshot(pending)))
    check("monitoramento: resposta seguida de pendência não alerta", ms._diff(base0, ms.compact_snapshot(pending)) == {}, f"{sev}:{msg}")

    # J. retry policy
    check("nova tentativa pula base parada/vazia", not g.worth_retry({"ok": False, "layer_guard": {"reason": "stale_base"}}) and not g.worth_retry({"ok": False, "layer_guard": {"reason": "layer_empty"}}))
    check("nova tentativa repete falha de transporte e leitura parcial", g.worth_retry({"ok": False, "detail": "ReadTimeout"}) and g.worth_retry({"ok": True, "source_state": "partial", "layer_guard": {"reason": "prodes_layer_failed"}}))
    check("nova tentativa nunca troca resposta por pendente", g.keep_better({"ok": True, "source_state": "partial"}, {"ok": False})["ok"] is True)

    # L. WhatsApp mining intent reads ANM ok
    wa = (ROOT / "whatsapp_gateway.py").read_text(encoding="utf-8")
    check("WhatsApp mineração: contagem ANM só com a fonte respondendo", "anm_ok=(result.get('anm') or {}).get('ok') is True" in wa and "a.get('process_count',0) if anm_ok else 'consulta pendente'" in wa)


async def main_async():
    await s1_empty_layer_positive_control()
    await s2_real_hit_altamira()
    await s3_real_clear_curvelo()
    await s4_broken_answers()
    await s5_guard_contract()
    await s6_territorial_constraints()
    await s7_autos_prodes_fire()
    await s11_geometry_monitoring_retry()


def main() -> int:
    for step in (lambda: asyncio.run(main_async()), s8_sync_sources, s9_static_contract, s10_report_chain):
        try:
            step()
        except Exception:
            check("gate executou sem exceção", False, traceback.format_exc()[-600:])
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"RX_H1_GATE checks={len(RESULTS)} failed={len(failed)}", flush=True)
    if failed:
        print("RX_H1_GATE_FAIL " + " | ".join(failed), flush=True)
        return 1
    print("RX_H1_GATE_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

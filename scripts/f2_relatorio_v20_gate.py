"""Gate F2 · ligação das frentes ao relatório (live_report_adapter_v20 + report_engine_v10).

Roda offline: rede e curl recusados; as fontes das frentes respondem por fixture ou falham
de propósito. As regras são sobre o PDF de verdade (cadeia real report_v20_patch -> V20 ->
V19 -> ... -> V10) e sobre o payload.json gravado ao lado dele.

Regras
  R1 ligação: o runtime do relatório usa o V20, e o V20 encadeia o V19.
  R2 seções: com as fontes respondendo, o PDF mostra armazéns CONAB, alertas recentes,
     outorga com vazão/regime/validade e o satélite PRODES com prova; o payload.json guarda
     os campos novos e nunca a geometria dos alertas.
  R3 crédito: o nome da fonte de alertas validados só aparece na página final de fontes
     ("Rastreabilidade e limitações"); nunca antes, nunca em campo do payload fora dos créditos.
  R4 pendência: fonte que falhou vira "Consulta pendente." e nunca "Nenhum ..." nas seções.
  R5 licença: "CC BY-SA 4.0" e "CC BY-SA 3.0 BR" sobrevivem ao normalizador pt-BR.
  R6 INCRA: a resposta completa do INCRA volta para a análise em cache pelo caminho do servidor.

Controles positivos (sempre rodam): cada regra é desligada por mutação e tem de reprovar
pelo motivo dela.

  PYTHONPATH=. python scripts/f2_relatorio_v20_gate.py
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RX_RELEASE", "OFF")
os.environ["RX_INCRA_ACERVO_ENABLED"] = "off"  # sem rede: o V19 marca o INCRA como não realizado, sem chamada

CAR_CODE = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
VALIDATED_SOURCE_NAME = "MapBiomas Alerta"
SOURCES_PAGE = "Rastreabilidade e limitações"
ABSENCE = re.compile(r"Nenhum armazém|Nenhum alerta recente|Nenhuma captação|Nenhum alerta de desmatamento validado")


class NetworkRefused(RuntimeError):
    pass


_REAL_CONNECT = socket.socket.connect
_REAL_POPEN = subprocess.Popen


class _NoCurlPopen(_REAL_POPEN):
    def __init__(self, args, *a, **k):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if "curl" in str(first).lower():
            raise NetworkRefused("curl recusado no gate offline")
        super().__init__(args, *a, **k)


def _connect(self, address, *args):
    if isinstance(address, tuple) and address and address[0] in ("127.0.0.1", "::1"):
        return _REAL_CONNECT(self, address, *args)
    raise NetworkRefused(f"rede recusada no gate offline: {address!r}"[:120])


def offline():
    socket.socket.connect = _connect
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(NetworkRefused("rede recusada"))
    subprocess.Popen = _NoCurlPopen


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _iphan_gate():
    spec = importlib.util.spec_from_file_location("f2_iphan_outorga_gate_helpers", ROOT / "scripts" / "f2_iphan_outorga_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ análise e fontes por fixture
def curvelo_result():
    import deploy_app
    import prodes_reading_f2

    fx = load_json(ROOT / "tests" / "fixtures" / "f2_prodes_leitura" / "curvelo_mg.json")
    car = fx["car"]
    from shapely.geometry import shape

    result = {
        "car": {"ok": True, "properties": dict(car["properties"]), "geometry": car["geometry"], "bbox": list(shape(car["geometry"]).bounds)},
        "sigef": {"ok": False, "detail": "consulta_pendente:gate"},
        "embargos_ibama": {"ok": True, "feature_count": 0, "source_state": "answered_clear", "exact": {"available": True, "occurrence_count": 0, "area_unique_ha": 0.0}},
        "anm": {"ok": True, "feature_count": 0, "exact": {"available": True, "occurrence_count": 0, "area_unique_ha": 0.0}},
        "prodes": deploy_app.finalize_prodes(copy.deepcopy(fx["prodes"]), car["geometry"]),
    }
    prodes_reading_f2.apply_reading_to_result(result)
    result["water_mg"] = _iphan_gate().run_water(geometry=car["geometry"])
    return result


def conab_answer(_geometry, **_kwargs):
    import conab_armazens

    itens = [
        {"cda": "1", "nome": "ARMAZEM TESTE A LTDA", "tipo": "Graneleiro", "entidade": "Privada", "municipio": "Curvelo", "uf": "MG", "capacidade_t": 2875.0, "distancia_km": 31.1},
        {"cda": "2", "nome": "COOPERATIVA TESTE B", "tipo": "Convencional", "entidade": "Cooperativa", "municipio": "Curvelo", "uf": "MG", "capacidade_t": 15930.0, "distancia_km": 47.1},
    ]
    resultado = {"raio_km": 50.0, "itens": itens, "excluidos_localizacao": [], "sem_conferencia": [], "candidatos": 2}
    return conab_armazens.build_warehouses_payload(resultado, {"data_base": "2026-09-14"})


def deter_answer(_geometry, _prodes=None, **_kwargs):
    import deter_alertas

    payload = {"source": "inpe_deter", "state": "not_found", "complete": True, "title": "Alertas recentes de desmatamento",
               "text": "Nenhum alerta recente de satélite do INPE sobre o imóvel desde 01/08/2025 (alertas a partir de 3 ha; imagens até 04/09/2026).",
               "notes": [], "events": [], "other_events": [], "source_text": deter_alertas.FONTE_TEXTO, "cutoff": "2025-07-31"}
    combiner = {"state": "answered", "features": [], "min_area_ha": 3.0, "latest_image_date": "2026-09-04", "cutoff": "2025-07-31"}
    return {"payload": payload, "combiner": combiner}


def alerts_answer(code, geometry=None, **_kwargs):
    import mapbiomas_alerta

    body = load_json(ROOT / "scripts" / "fixtures" / "f2_mapbiomas_alerta" / "mba_curvelo_vazio.json")["response"]
    return mapbiomas_alerta.parse_response(body, code, geometry)


def landsat_post(_url, body):
    day = body["datetime"][:10]
    return load_json(ROOT / "tests" / "fixtures" / "f2_clima_landsat" / "planetary_computer_landsat_218073_by_date.json")["responses"].get(day)


def failing(*_a, **_k):
    raise TimeoutError("fonte fora do ar (gate)")


def sources_patches(mode: str):
    import conab_armazens
    import deter_alertas
    import mapbiomas_alerta
    import prodes_image_platform_f2 as pip

    original_platforms = pip.query_platforms_for_occurrences
    if mode == "answered":
        return [patch.object(conab_armazens, "conab_warehouses_payload", conab_answer),
                patch.object(deter_alertas, "deter_alerts_bundle", deter_answer),
                patch.object(mapbiomas_alerta, "query_mapbiomas_alerta", alerts_answer),
                patch.object(pip, "query_platforms_for_occurrences", lambda occ, lonlat=None: original_platforms(occ, lonlat=lonlat, post=landsat_post))]
    return [patch.object(conab_armazens, "conab_warehouses_payload", failing),
            patch.object(deter_alertas, "deter_alerts_bundle", failing),
            patch.object(mapbiomas_alerta, "query_mapbiomas_alerta", lambda code, geometry=None, **k: mapbiomas_alerta._pending(code, "timeout")),
            patch.object(pip, "query_platforms_for_occurrences", lambda occ, lonlat=None: original_platforms(occ, lonlat=lonlat, post=lambda u, b: None))]


# ------------------------------------------------------------------ PDF
def pdf_pages(path) -> list[str]:
    from pypdf import PdfReader

    return [re.sub(r"\s+", " ", page.extract_text() or "") for page in PdfReader(str(path)).pages]


def render_chain(mode: str, capture: dict | None = None):
    """Cadeia real do relatório com as fontes da frente por fixture (ou falhando)."""
    import live_report_adapter_v20 as v20
    import report_api

    patches = sources_patches(mode)
    original_render = v20.render_v20

    def spy(path, payload):
        if capture is not None:
            capture["base_payload"] = copy.deepcopy(payload)
            capture["ctx_result"] = (getattr(v20._CTX, "value", None) or {}).get("result")
        return original_render(path, payload)

    import live_report_adapter_v18 as v18
    for p in patches:
        p.start()
    try:
        with patch.object(v18, "build_premium_property_report_v8", spy):
            meta = report_api.generate_live_report(curvelo_result(), CAR_CODE)
    finally:
        for p in reversed(patches):
            p.stop()
    pages = pdf_pages(meta["pdf_path"])
    payload = load_json(Path(meta["payload_path"]))
    shutil.copyfile(meta["pdf_path"], OUT / f"curvelo_{mode}.pdf")
    shutil.rmtree(Path(meta["pdf_path"]).parent, ignore_errors=True)
    return pages, payload


def render_payload(payload, renderer=None) -> list[str]:
    import report_engine_v10

    path = OUT / "render_tmp" / "raio_x_territorial.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    (renderer or report_engine_v10.build_premium_property_report_v10)(path, copy.deepcopy(payload))
    return pdf_pages(path)


def ctx_with(result, conab, deter, alerts):
    def done(value):
        f = Future()
        if isinstance(value, BaseException):
            f.set_exception(value)
        else:
            f.set_result(value)
        return f

    return {"result": result, "futures": {"conab": done(conab), "deter": done(deter), "alerts": done(alerts)}}


# ------------------------------------------------------------------ regras
def rule_runtime():
    import live_report_adapter_v19 as v19
    import live_report_adapter_v20 as v20
    import report_api
    import report_v20_patch  # noqa: F401

    assert report_api.generate_live_report.__module__ == "live_report_adapter_v20", report_api.generate_live_report.__module__
    assert report_api.APP_VERSION == "0.50.0-f2-verdade-e-lacunas", report_api.APP_VERSION
    assert v20.v19 is v19 and "v19.generate_live_report(working, car_code)" in (ROOT / "live_report_adapter_v20.py").read_text(encoding="utf-8")
    site = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
    assert site.index("import report_v19_patch") < site.index("import report_v20_patch"), "sitecustomize não carrega o V20 depois do V19"


def rule_sections(pages, payload):
    text = " ".join(pages)
    required = {
        "armazéns CONAB": "Armazéns cadastrados na CONAB (até 50 km)",
        "distância CONAB": "31,1 km",
        "alertas recentes": "Nenhum alerta recente de desmatamento sobre o imóvel desde 01/08/2025",
        "outorga com vazão": "10 m³/h",
        "regime da outorga": "8 h 22 min por dia",
        "validade da outorga": "válida até 15/12/2030",
        "satélite PRODES com prova": "21/07/2006 · Landsat 5 (TM)",
    }
    missing = [k for k, v in required.items() if v not in text]
    assert not missing, f"PDF sem as seções ligadas: {missing}"
    env = payload.get("environment") or {}
    assert (env.get("deforestation_alerts") or {}).get("state") == "not_found", env.get("deforestation_alerts")
    assert ((payload.get("infrastructure") or {}).get("warehouses_f2") or {}).get("state") == "found"
    assert ((payload.get("water") or {}).get("grants_f2") or {}).get("state") == "found"
    blob = json.dumps(env.get("deforestation_alerts"), ensure_ascii=False)
    assert "coordinates" not in blob and "_geometry" not in blob, "geometria dos alertas no payload.json"


def rule_credit_only_on_sources_page(pages, payload):
    final = next(i for i, p in enumerate(pages) if re.search(r"Página \d+ " + SOURCES_PAGE, p))
    early = [i + 1 for i, p in enumerate(pages[:final]) if VALIDATED_SOURCE_NAME in p]
    assert not early, f"nome da fonte de alertas validados antes da página final de fontes: páginas {early}"
    assert any(VALIDATED_SOURCE_NAME in p for p in pages[final:]), "crédito ausente da página final de fontes"
    body = {k: v for k, v in payload.items() if k != "sources_page_credits"}
    assert VALIDATED_SOURCE_NAME not in json.dumps(body, ensure_ascii=False), "nome da fonte de alertas validados no payload fora dos créditos"


def rule_pending(pages, payload):
    text = " ".join(pages)
    hits = sorted(set(ABSENCE.findall(text)))
    assert not hits, f"fonte que falhou virou ausência: {hits}"
    section = next((p for p in pages if "Armazéns cadastrados na CONAB (até 50 km)" in p), "")
    assert "Consulta pendente." in section, "seção de armazéns sem 'Consulta pendente.'"
    assert "Alertas recentes de desmatamento: consulta pendente." in text, "seção de alertas sem pendência"
    assert VALIDATED_SOURCE_NAME not in text, "crédito de fonte que não respondeu"
    assert ((payload.get("infrastructure") or {}).get("warehouses_f2") or {}).get("state") == "pending"


def rule_license():
    import report_ptbr_v50

    for raw in ("MapBiomas Alerta — CC BY-SA 3.0 BR; dados", "INPE/TerraBrasilis (DETER) — CC BY-SA 4.0; dados"):
        out = report_ptbr_v50.normalize_text(raw)
        assert out == raw, f"versão da licença reescrita: {raw!r} -> {out!r}"
    assert report_ptbr_v50.normalize_text("área 12.50 ha") == "área 12,50 ha", "normalizador de número deixou de funcionar"


def rule_incra_write_back():
    import incra_acervo_f2
    import report_api
    import report_v13_patch
    import report_v20_patch  # noqa: F401

    cached = {"car": {"ok": True, "properties": {}}}
    complete = {"state": "complete-gate"}

    async def analyze(_code):
        return cached

    def generate(working, _code):
        working["incra_acervo"] = complete
        return {"report_id": "gate"}

    with patch.object(report_api, "_analyze_with_live_addons", analyze), patch.object(report_v13_patch, "generate_live_report", generate), \
            patch.object(report_api, "_release_memory", lambda: None), \
            patch.object(incra_acervo_f2, "is_complete", lambda a: isinstance(a, dict) and a.get("state") == "complete-gate"):
        result, _meta = asyncio.run(report_v13_patch._build_v13(CAR_CODE))
    assert result is cached and cached.get("incra_acervo") is complete, "a resposta completa do INCRA não voltou para a análise em cache"


# ------------------------------------------------------------------ execução
OUT = Path(os.environ.get("RX_F2_V20_GATE_OUT") or (Path(tempfile.gettempdir()) / "rx_f2_relatorio_v20_gate"))


def main() -> int:
    offline()
    OUT.mkdir(parents=True, exist_ok=True)
    import sitecustomize  # noqa: F401
    import report_v20_patch  # noqa: F401
    import live_report_adapter_v20 as v20
    import report_engine_v9
    import report_ptbr_v50

    failures: list[str] = []

    def check(name, fn, *args):
        try:
            fn(*args)
            print(f"PASS {name}", flush=True)
        except Exception as exc:
            failures.append(name)
            print(f"FAIL {name}: {''.join(traceback.format_exception_only(type(exc), exc)).strip()[:700]}", flush=True)

    def control(name, fn, *args, expect: str):
        try:
            fn(*args)
        except AssertionError as exc:
            if expect in str(exc):
                print(f"CONTROLE_POSITIVO_OK {name}: {str(exc)[:160]}", flush=True)
                return
            failures.append(f"controle:{name}")
            print(f"FAIL controle {name}: reprovou pelo motivo errado: {str(exc)[:300]}", flush=True)
            return
        except Exception as exc:
            failures.append(f"controle:{name}")
            print(f"FAIL controle {name}: erro fora da regra: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            return
        failures.append(f"controle:{name}")
        print(f"FAIL controle {name}: a regra passou com a mutação", flush=True)

    check("R1 runtime usa o V20 encadeando o V19", rule_runtime)
    capture: dict = {}
    pages, payload = render_chain("answered", capture)
    check("R2 seções novas no PDF e no payload", rule_sections, pages, payload)
    check("R3 crédito só na página final de fontes", rule_credit_only_on_sources_page, pages, payload)
    failed_pages, failed_payload = render_chain("failing")
    check("R4 fonte que falhou vira consulta pendente", rule_pending, failed_pages, failed_payload)
    check("R5 versão de licença intacta", rule_license)
    check("R6 INCRA volta para a análise em cache", rule_incra_write_back)

    # ---- controles positivos: cada mutação desliga uma regra ----
    base_payload = capture["base_payload"]
    result = capture["ctx_result"]
    answered_ctx = lambda: ctx_with(result, conab_answer(None), deter_answer(None), alerts_answer(CAR_CODE, result["car"]["geometry"]))

    # M1: V20 não ligado (render do V9 sobre o payload sem as frentes).
    control("sem_v20_ligado", rule_sections, render_payload(base_payload, report_engine_v9.build_premium_property_report_v9), base_payload,
            expect="PDF sem as seções ligadas")

    # M2: crédito copiado para a tabela de fontes (aparece em "Cobertura das fontes").
    def credits_in_sources(payload, res, bundle, alerts):
        original_apply_alerts(payload, res, bundle, alerts)
        for c in payload.get("sources_page_credits") or []:
            payload.setdefault("sources", []).append({"name": c["text"], "description": "crédito", "status": "CONSULTADA", "level": "ok"})

    original_apply_alerts = v20._apply_alerts
    with patch.object(v20, "_apply_alerts", credits_in_sources):
        mutated = v20.apply_f2_payload(copy.deepcopy(base_payload), answered_ctx())
    control("credito_na_tabela_de_fontes", rule_credit_only_on_sources_page, render_payload(mutated), mutated,
            expect="antes da página final de fontes")

    # M3: falha lida como "nenhum" (fallback que devolve resposta vazia).
    import conab_armazens

    empty = {"raio_km": 50.0, "itens": [], "excluidos_localizacao": [], "sem_conferencia": [], "candidatos": 0}
    with patch.object(v20, "_pending_conab", lambda: conab_armazens.build_warehouses_payload(empty, {})):
        failing_ctx = ctx_with(result, TimeoutError("gate"), TimeoutError("gate"), {"state": "pending"})
        mutated = v20.apply_f2_payload(copy.deepcopy(base_payload), failing_ctx)
    control("falha_vira_nenhum", rule_pending, render_payload(mutated), mutated, expect="fonte que falhou virou ausência")

    # M4: normalizador sem a proteção da versão da licença.
    with patch.object(report_ptbr_v50, "_LICENSE_VERSION", re.compile(r"(?!x)x")):
        control("licenca_reescrita", rule_license, expect="versão da licença reescrita")

    # M5: caminho do servidor sem a volta do INCRA (o _build_v13 original).
    import report_v13_patch
    import report_v20_patch as p20

    assert p20._build_v13_previous.__module__ == "report_v13_patch", p20._build_v13_previous.__module__
    with patch.object(report_v13_patch, "_build_v13", p20._build_v13_previous):
        control("sem_volta_do_incra", rule_incra_write_back, expect="não voltou para a análise em cache")

    if failures:
        print(f"RX_F2_RELATORIO_V20_GATE=FAIL {len(failures)}: {', '.join(failures)}", flush=True)
        return 1
    print("RX_F2_RELATORIO_V20_GATE=PASS regras=6 controles_positivos=5", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

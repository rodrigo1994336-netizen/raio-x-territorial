"""F2 gate: sítios arqueológicos do IPHAN (oficial, até 10 km) e vazão da outorga.

Uso:
  PYTHONPATH=. python scripts/f2_iphan_outorga_gate.py          # offline, fixtures reais
  PYTHONPATH=. python scripts/f2_iphan_outorga_gate.py --live   # + controle positivo no IPHAN real

Fixtures em tests/fixtures/f2_iphan_outorga/, gravadas de respostas reais:
13/09/2026 para o CAR MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F (Curvelo/MG)
— SICAR (polígono), IPHAN GeoServer SICG:sitios e SICG:sitios_pol, PAMGIA
lim_sitios_arqueologicos_iphan_a, IDE-Sisema (IGAM e ANA em MG) e ANA CNARH —
e 14/09/2026 para as categorias: uma outorga real de cada tipo de uso do IGAM e
da ANA em MG (barramento sem captação, dragagem, hidrelétrica, desvio,
travessia, captações; lançamento, ponto de referência, barragem, preventiva,
revogada, pouca expressão) e um polígono inválido real do SICG
(MG3158508BAST00005, autointerseção). As outorgas foram gravadas sem CPF/CNPJ,
nome de empreendimento, de responsável ou de requerente. O servidor falso deste
gate só faz o que o servidor real faz (filtrar pela caixa pedida); as falhas
são injetadas por cima. Variações derivadas de uma fixture ficam declaradas no
próprio teste.

O teste valida a regra, não o valor de uma fonte viva: os valores conferidos
vêm das fixtures congeladas. Controle positivo: mutação no código novo (cada
regra reprova com a guarda desligada) e o código de origin/main no PYTHONPATH.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import importlib.util
import io
import json
import re
import sys
import tempfile
import traceback
import types
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from shapely.geometry import Polygon, box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "f2_iphan_outorga"
FIXTURE_COUNT = 11
TODAY = date(2026, 9, 13)
CHECKS: list[tuple[str, callable]] = []
BARRA_DO_LUIS = (-44.1637, -18.9509)  # ponto real do IPHAN (sem polígono)
PII_PARTS = ("cpf", "cnpj", "empto", "emp_nm", "empreend", "respons", "titular", "requer", "nome", "nm_",
             "usuario", "usuário", "email", "fone", "proprie")
# Campos públicos que casam com as partes acima e não são dado de pessoa.
PUBLIC_FIELDS = {"ing_nm_municipio", "nivdinm_4", "identificacao_bem"}
EMPTY_FC = {"type": "FeatureCollection", "features": []}


def check(fn):
    CHECKS.append((fn.__name__, fn))
    return fn


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def car_geometry() -> dict:
    return load("sicar_curvelo_car_geom.json")["features"][0]["geometry"]


def square(lon: float, lat: float, half_m: float, shift_east_m: float = 0.0) -> dict:
    dlat = half_m / 111_000.0
    dlon = half_m / 105_000.0  # cos(-18.95°) ≈ 0,946
    sx = shift_east_m / 105_000.0
    return mapping(box(lon - dlon + sx, lat - dlat, lon + dlon + sx, lat + dlat))


def _module(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ptbr_text_ok(texts: list[str]) -> None:
    """Texto do cliente passa pelo normalizador do PDF sem mudar e sem acusação do lint R1."""
    import report_ptbr_v50

    r1 = _module("scripts/r1_report_ptbr_gate.py")
    for text in texts:
        if not text:
            continue
        assert report_ptbr_v50.normalize_text(text) == text, ("normalizador reescreveu", text, report_ptbr_v50.normalize_text(text))
        hits = r1.lint_text(text)
        assert not hits, (text, hits)


def pii_keys(props: dict) -> list[str]:
    return [k for k in props if k.lower() not in PUBLIC_FIELDS and any(p in k.lower() for p in PII_PARTS)]


# ------------------------------------------------------------ servidor falso --
def _filter_fc(fc: dict, bbox: tuple[float, float, float, float]) -> dict:
    env = box(*bbox)
    features = [copy.deepcopy(f) for f in fc.get("features") or [] if env.intersects(shape(f["geometry"]))]
    out = {k: v for k, v in fc.items() if k not in ("features", "bbox")}
    out["features"] = features
    for key in ("numberMatched", "numberReturned", "totalFeatures"):
        if key in fc:
            out[key] = len(features)
    return out


class FakeServers:
    """IPHAN WFS 1.0.0 e PAMGIA ArcGIS com filtro de caixa; falhas injetadas por camada."""

    def __init__(self, faults: dict[str, list] | None = None, points: dict | None = None, polygons: dict | None = None):
        self.points = points if points is not None else load("iphan_sicg_sitios_pontos.geojson")
        self.polygons = polygons if polygons is not None else load("iphan_sicg_sitios_poligonos.geojson")
        self.pamgia_polygons = load("pamgia_sitios_poligonos.geojson")
        self.faults = {k: list(v) for k, v in (faults or {}).items()}
        self.iphan_requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        params = dict(request.url.params)
        if host == "geoserver.iphan.gov.br":
            self.iphan_requests.append(params)
            typename = params.get("typeName") or params.get("typeNames")
            queue = self.faults.get(typename) or []
            fault = queue.pop(0) if queue else None
            if fault == "http500":
                return httpx.Response(500, text="Server error")
            if fault == "xml":
                return httpx.Response(200, text='<?xml version="1.0"?><ServiceExceptionReport><ServiceException>erro</ServiceException></ServiceExceptionReport>')
            fc = self.points if typename == "SICG:sitios" else self.polygons if typename == "SICG:sitios_pol" else None
            if fc is None:
                return httpx.Response(200, text='<ServiceExceptionReport/>')
            if params.get("version") == "1.0.0":
                x1, y1, x2, y2 = (float(v) for v in params["bbox"].split(",")[:4])
            else:  # WFS 1.1/2.0 com EPSG:4674 lê a caixa como lat/lon
                y1, x1, y2, x2 = (float(v) for v in params["bbox"].split(",")[:4])
            out = _filter_fc(fc, (x1, y1, x2, y2))
            if fault == "truncated":
                out["numberMatched"] = len(out["features"]) + 5
            if fault == "axis_swapped":
                for f in out["features"]:
                    g = f["geometry"]
                    if g["type"] == "Point":
                        g["coordinates"] = [g["coordinates"][1], g["coordinates"][0]]
            return httpx.Response(200, json=out)
        if host == "pamgia.ibama.gov.br":
            path = request.url.path
            if not path.endswith("/query"):
                return httpx.Response(200, json={"layers": [{"id": 0}]})
            x1, y1, x2, y2 = (float(v) for v in params["geometry"].split(","))
            fc = self.pamgia_polygons if "lim_sitios_arqueologicos_iphan_a" in path else EMPTY_FC
            return httpx.Response(200, json=_filter_fc(fc, (x1, y1, x2, y2)))
        return httpx.Response(404, text="not found")


def run_constraints(geometry: dict, servers: FakeServers) -> dict:
    """Caminho real do relatório: territorial_constraints + parity_public_layers."""
    import territorial_constraints as tc
    import parity_public_layers  # noqa: F401  (registra e troca a fonte do IPHAN)

    try:
        import iphan_sicg

        iphan_sicg.RETRY_PAUSE_S = 0.0
    except ImportError:
        pass
    real = httpx.AsyncClient
    transport = httpx.MockTransport(servers.handler)
    shim = types.SimpleNamespace(AsyncClient=lambda **kw: real(transport=transport, **kw))
    previous = tc.httpx
    tc.httpx = shim
    try:
        geom = shape(geometry)
        return asyncio.run(tc.query_territorial_constraints(geometry, list(geom.bounds)))
    finally:
        tc.httpx = previous


def report_v11(res: dict) -> dict:
    """Payload do relatório como o cliente recebe: v11._patch_extra_territorial + client_payload."""
    import live_report_adapter_v11 as v11
    import report_ptbr_v50

    payload = {"environment": {}, "sources": [], "compliance": [], "attention_points": [], "conclusion": {}}
    result = {"territorial_constraints": res, "car": {"properties": {"area": 100.0}}}
    return report_ptbr_v50.client_payload(v11._patch_extra_territorial(payload, result))


def iphan_texts(payload: dict) -> dict[str, list]:
    def about(text: str) -> bool:
        low = str(text).lower()
        return "arqueol" in low or "iphan" in low

    return {
        "layer_rows": [r for r in payload["environment"].get("layer_rows") or [] if about(r[0])],
        "compliance": [c for c in payload.get("compliance") or [] if about(c.get("label"))],
        "sources": [s for s in payload.get("sources") or [] if about(s.get("name"))],
        "attention": [a for a in payload.get("attention_points") or [] if about(a)],
        "diligence": [d for d in (payload.get("conclusion") or {}).get("diligence") or [] if about(d)],
    }


# ------------------------------------------------------------------ IPHAN --
@check
def iphan_curvelo_zero_dentro_dois_vizinhos():
    import iphan_sicg

    iphan_sicg.RETRY_PAUSE_S = 0.0
    servers = FakeServers()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(servers.handler)) as client:
            return await iphan_sicg.query_iphan_sites(car_geometry(), client=client)

    r = asyncio.run(go())
    assert r["ok"] and r["state_inside"] == "not_found" and r["state_near"] == "found", r
    assert not r["inside"] and not r["border"], r
    near = [(x["nome"], x["distancia_texto"], x["geometria"]) for x in r["near"]]
    assert near == [("Sítio Barra do Luis 01", "6,7 km", "ponto"), ("Sítio Rio das Velhas 03", "8,6 km", "ponto e polígono")], near
    typenames = {p.get("typeName") for p in servers.iphan_requests}
    assert typenames == {"SICG:sitios", "SICG:sitios_pol"}, typenames


@check
def iphan_parametros_wfs_lon_lat():
    import iphan_sicg

    env = iphan_sicg.query_envelope(car_geometry())
    for layer in (iphan_sicg.LAYER_POINTS, iphan_sicg.LAYER_POLYGONS):
        p = iphan_sicg.build_params(layer, env)
        assert p["version"] == "1.0.0" and p["srsName"] == "EPSG:4674", p
        x1, y1, x2, y2 = (float(v) for v in p["bbox"].split(","))
        assert -75 < x1 < x2 < -28 and -35 < y1 < y2 < 6, p["bbox"]
    assert "geoserver.iphan.gov.br" in iphan_sicg.WFS_URL and "pamgia" not in iphan_sicg.WFS_URL


@check
def restricao_ponto_iphan_dentro_do_imovel():
    # Imóvel de teste de 1 km em volta do ponto real do Barra do Luis 01 (só ponto, sem polígono).
    res = run_constraints(square(*BARRA_DO_LUIS, 500), FakeServers())
    svc = res["services"]["sitio_arqueologico"]
    assert svc.get("ok") is True, svc
    assert svc.get("occurrence_count") == 1, ("ponto dentro do imóvel não detectado", svc.get("occurrence_count"), svc.get("source"))
    attrs = svc["occurrences"][0]["attributes"]
    assert attrs.get("nome") == "Sítio Barra do Luis 01" and attrs.get("situacao") == "dentro", attrs


@check
def restricao_ponto_junto_a_divisa():
    # Divisa leste do imóvel a 8 m do ponto: nem "dentro" nem "fora", e nunca "interseção".
    geom = square(*BARRA_DO_LUIS, 500, shift_east_m=-508)
    assert not shape(geom).contains(shape({"type": "Point", "coordinates": list(BARRA_DO_LUIS)}))
    res = run_constraints(geom, FakeServers())
    svc = res["services"]["sitio_arqueologico"]
    assert svc.get("ok") is True, svc
    import iphan_sicg

    iphan = svc["iphan"]
    assert [x["nome"] for x in iphan["border"]] == ["Sítio Barra do Luis 01"] and not iphan["inside"], ("ponto a 8 m da divisa não visto", iphan)
    # O formato antigo lê occurrence_count como "interseção exata": junto à divisa não entra nele.
    assert svc.get("occurrence_count") == 0 and svc.get("border_count") == 1, (svc.get("occurrence_count"), svc.get("border_count"))
    payload = iphan_sicg.iphan_payload(iphan)
    assert "junto à divisa" in payload["headline"] and payload["level"] == "attention", payload
    # A fonte só garante o arredondamento publicado (4 casas), não a precisão do cadastro.
    assert "precisão" not in payload["headline"] and "arredondada" in payload["headline"], payload["headline"]
    ptbr_text_ok([payload["headline"], payload["layer_text"], payload["compliance_row"]["text"]])


@check
def iphan_relatorio_v11_sem_intersecao_falsa():
    """O relatório (v11 + client_payload) nunca chama ponto ou divisa de interseção, nem imprime hectare de ponto."""
    border = iphan_texts(report_v11(run_constraints(square(*BARRA_DO_LUIS, 500, shift_east_m=-508), FakeServers())))
    inside = iphan_texts(report_v11(run_constraints(square(*BARRA_DO_LUIS, 500), FakeServers())))
    pending = iphan_texts(report_v11(run_constraints(car_geometry(), FakeServers({"SICG:sitios": ["http500", "http500"]}))))
    for name, t in (("divisa", border), ("dentro", inside), ("pendente", pending)):
        assert len(t["layer_rows"]) == 1 and len(t["compliance"]) == 1 and len(t["sources"]) == 1, (name, t)
        client = [t["layer_rows"][0][1], t["compliance"][0]["text"], t["sources"][0]["description"], *t["attention"], *t["diligence"]]
        blob = " ".join(client).lower()
        for forbidden in ("interseç", "intersect", " ha", "0.0", "0,0", "sem sobreposição", "indisponível"):
            assert forbidden not in blob, (name, forbidden, client)
        assert "SEM SOBREPOSIÇÃO" not in json.dumps(t, ensure_ascii=False), (name, t)
        ptbr_text_ok(client)
    assert "junto à divisa" in border["compliance"][0]["text"] and border["compliance"][0]["badge"] == "ATENÇÃO", border
    assert len(border["attention"]) == 1 and "junto à divisa" in border["attention"][0], border["attention"]
    assert "1 dentro do imóvel" in inside["compliance"][0]["text"] and len(inside["attention"]) == 1, inside
    assert "dentro do imóvel" in inside["attention"][0], inside["attention"]
    assert pending["compliance"][0]["text"] == "Consulta pendente." and not pending["attention"], pending
    assert pending["sources"][0]["status"] == "CONSULTA PENDENTE", pending["sources"]
    assert "nenhum" not in json.dumps(pending, ensure_ascii=False).lower(), pending


@check
def iphan_poligono_invalido_e_car_invalido():
    """Polígono real inválido do SICG (autointerseção) e CAR inválido: achado dentro, nunca pendente, área sem inflar."""
    invalid = load("iphan_sicg_sitio_poligono_invalido.geojson")
    site = shape(invalid["features"][0]["geometry"])
    assert not site.is_valid, "a fixture tem de continuar inválida"
    x1, y1, x2, y2 = site.bounds
    mid = (x1 + x2) / 2
    for half in ("oeste", "leste"):
        car = box(x1 - 0.002, y1 - 0.002, mid, y2 + 0.002) if half == "oeste" else box(mid, y1 - 0.002, x2 + 0.002, y2 + 0.002)
        res = run_constraints(mapping(car), FakeServers(points=EMPTY_FC, polygons=invalid))
        svc = res["services"]["sitio_arqueologico"]
        assert svc.get("ok") is True, (half, "polígono inválido derrubou a checagem", svc.get("detail"), (svc.get("iphan") or {}).get("detail"))
        inside = svc["iphan"]["inside"]
        assert [r["codigo_iphan"] for r in inside] == ["MG3158508BAST00005"], (half, inside)
        # O sítio inteiro, corrigido, tem cerca de 0,105 ha; a geometria inválida chegava a 5,6 ha na metade leste.
        assert 0 < inside[0]["area_dentro_ha"] < 0.12, (half, inside[0]["area_dentro_ha"])
    # CAR com autointerseção (gravata) sobre o polígono válido do Sítio Rio das Velhas 03.
    rio = shape(load("iphan_sicg_sitios_poligonos.geojson")["features"][0]["geometry"])
    rx1, ry1, rx2, ry2 = rio.bounds
    cx, cy, d = (rx1 + rx2) / 2, (ry1 + ry2) / 2, max(rx2 - rx1, ry2 - ry1)
    bow = Polygon([(cx - d, cy - d), (cx + d, cy + d), (cx + d, cy - d), (cx - d, cy + d), (cx - d, cy - d)])
    assert not bow.is_valid
    res = run_constraints(mapping(bow), FakeServers())
    svc = res["services"]["sitio_arqueologico"]
    assert svc.get("ok") is True, ("CAR inválido derrubou a checagem", svc.get("detail"), (svc.get("iphan") or {}).get("detail"))
    assert "Sítio Rio das Velhas 03" in [r["nome"] for r in svc["iphan"]["inside"]], svc["iphan"]["inside"]


@check
def restricao_iphan_falha_nunca_vira_zero():
    scenarios = {
        "pontos_http500": {"SICG:sitios": ["http500", "http500"]},
        "poligonos_xml_de_erro": {"SICG:sitios_pol": ["xml", "xml"]},
        "pontos_truncados": {"SICG:sitios": ["truncated", "truncated"]},
        "eixo_trocado": {"SICG:sitios": ["axis_swapped", "axis_swapped"]},
    }
    for name, faults in scenarios.items():
        res = run_constraints(car_geometry(), FakeServers(faults))
        svc = res["services"]["sitio_arqueologico"]
        assert svc.get("ok") is not True, (name, "fonte falhou e a checagem respondeu", svc.get("occurrence_count"), svc.get("source"))
        import iphan_sicg

        payload = iphan_sicg.iphan_payload(svc.get("iphan"))
        assert payload["state"] == "pending" and payload["inside"]["count"] is None, (name, payload)
        text = json.dumps(payload, ensure_ascii=False).lower()
        assert "nenhum" not in text and "0 dentro" not in text, (name, payload)


@check
def restricao_iphan_tenta_de_novo_sozinha():
    res = run_constraints(car_geometry(), FakeServers({"SICG:sitios": ["http500"]}))
    svc = res["services"]["sitio_arqueologico"]
    assert svc.get("ok") is True and svc.get("occurrence_count") == 0, svc
    assert svc["iphan"]["layers"]["SICG:sitios"]["attempts"] == 2, svc["iphan"]["layers"]


@check
def iphan_payload_textos_do_cliente():
    import iphan_sicg

    points, polygons = load("iphan_sicg_sitios_pontos.geojson"), load("iphan_sicg_sitios_poligonos.geojson")
    car = car_geometry()
    env = iphan_sicg.query_envelope(car)
    r = iphan_sicg.build_result(car, iphan_sicg.parse_layer_response(200, json.dumps(points), env),
                                iphan_sicg.parse_layer_response(200, json.dumps(polygons), env))
    p = iphan_sicg.iphan_payload(r)
    assert p["state"] == "found" and p["inside"]["state"] == "not_found" and p["near"]["count"] == 2, p
    assert p["near"]["rows"] == [["Sítio Barra do Luis 01", "bem arqueológico", "6,7 km"],
                                 ["Sítio Rio das Velhas 03", "bem arqueológico, pré-colonial", "8,6 km"]], p["near"]["rows"]
    assert p["headline"] == "Nenhum sítio arqueológico cadastrado no IPHAN dentro do imóvel.", p["headline"]
    assert any(n.startswith("Sítio próximo não é restrição dentro do imóvel.") for n in p["notes"]), p["notes"]
    assert p["level"] == "neutral", p["level"]
    blob = json.dumps(p, ensure_ascii=False).lower()
    for forbidden in ("não há sítios", "sem sítios", "dt_sync", "2050", "logradouro"):
        assert forbidden not in blob, forbidden
    ptbr_text_ok([p["headline"], p["near_text"], *p["notes"], p["source_row"]["description"], p["compliance_row"]["text"],
                  *[c for row in p["near"]["rows"] for c in row]])


# --------------------------------------------------------------- outorgas --
def run_water(igam_ok: bool = True, igam_fc: dict | None = None, ana_fc: dict | None = None, geometry: dict | None = None) -> dict:
    import water_mg

    igam = copy.deepcopy(igam_fc or load("idesisema_igam_outorgas_sem_pii.geojson"))
    for f in igam["features"]:  # colunas pessoais que a resposta real traz (valores fictícios)
        f["properties"]["cpfcnpj_4"] = "00000000000"
        f["properties"]["empto_4"] = "EMPREENDIMENTO FICTICIO"
    ana = copy.deepcopy(ana_fc or load("idesisema_ana_outorgas_mg_sem_pii.geojson"))
    for f in ana["features"]:
        f["properties"]["nome_do_re"] = "REQUERENTE FICTICIO"
        f["properties"]["cpf_cnpj"] = "00000000000"

    def fake_curl(url, expect_json=False, max_time=40):
        typename = parse_qs(urlparse(url).query).get("typeNames", [""])[0]
        if typename.endswith("outorgas_uso_recursos_hidricos_pto"):
            return {"ok": True, "json": igam} if igam_ok else {"ok": False, "detail": "timeout"}
        if typename.endswith("federais_ana_outorgas_pto"):
            return {"ok": True, "json": ana}
        return {"ok": False, "detail": "camada desconhecida"}

    previous = water_mg._curl
    water_mg._curl = fake_curl
    try:
        geom = geometry or car_geometry()
        return water_mg.query_outorgas_mg(geom, list(shape(geom).bounds), 5.0)
    finally:
        water_mg._curl = previous


def categories_water() -> dict:
    """Imóvel de teste que cobre as outorgas de categoria (IGAM e ANA): todas caem dentro."""
    igam, ana = load("idesisema_igam_outorgas_categorias_sem_pii.geojson"), load("idesisema_ana_outorgas_mg_categorias_sem_pii.geojson")
    pts = [shape(f["geometry"]) for f in igam["features"] + ana["features"]]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    geom = mapping(box(min(xs) - 0.01, min(ys) - 0.01, max(xs) + 0.01, max(ys) + 0.01))
    return run_water(igam_fc=igam, ana_fc=ana, geometry=geom)


@check
def outorga_campos_de_vazao_guardados_sem_pii():
    water = run_water()
    assert water["ok"] and water["inside_count"] == 1, water.get("inside_count")
    props = water["inside"][0]["properties"]
    needed = ("unvazao_4", "vazjan_4", "vazjulh_4", "tcapjan_4", "tcapdez_4", "dia_fev_4", "finuso1_4", "diavenc_4", "mesvenc_4", "anovenc_4", "datverbase")
    missing = [k for k in needed if k not in props]
    assert not missing, ("campos da outorga descartados", missing)
    for item in water["inside"] + water["near"]:
        leaked = pii_keys(item["properties"])
        assert not leaked, leaked


@check
def outorga_payload_curvelo():
    import outorga_vazao as ov

    p = ov.water_grants_payload(run_water(), uf="MG", today=TODAY)
    assert p["state"] == "found" and p["count"] == 1 and p["complete"], p
    assert p["headers"] == ["Uso", "Ato", "Situação", "Vazão autorizada", "Regime de captação", "Finalidade", "Validade"], p["headers"]
    assert p["rows"] == [["Poço tubular", "Portaria 1309443/2020", "Deferido", "10 m³/h", "8 h 22 min por dia · o ano todo",
                          "irrigação, consumo humano e dessedentação de animais", "válida até 15/12/2030"]], p["rows"]
    expected = ("Há 1 captação registrada no cadastro de outorgas dentro do imóvel. Poço tubular com outorga do IGAM: autoriza captar "
                "10 m³ por hora, 8 h 22 min por dia, o ano todo, para irrigação, consumo humano e dessedentação de animais. "
                "Portaria 1309443/2020, válida até 15/12/2030.")
    assert p["headline"] == expected, p["headline"]
    assert p["near_text"] == "Outras 6 captações registradas no cadastro de outorgas a até 5 km; a mais próxima a 1,3 km.", p["near_text"]
    assert p["notes"][0].startswith("Vazão outorgada não é água garantida"), p["notes"]
    assert "Situação conforme o cadastro do IGAM de 17/10/2025." in p["notes"], p["notes"]
    assert p["level"] == "neutral", p["level"]
    blob = json.dumps(p, ensure_ascii=False).lower()
    for forbidden in ("volume", "202,1", "cpf", "cnpj", "empreendimento", "titular:"):
        assert forbidden not in blob, forbidden
    ptbr_text_ok([p["headline"], p["near_text"], *p["notes"], p["source_row"]["description"], p["compliance_row"]["text"],
                  *[c for row in p["rows"] for c in row]])


@check
def outorga_unidade_meses_validade():
    import outorga_vazao as ov

    igam = load("idesisema_igam_outorgas_sem_pii.geojson")
    by_port = {f["properties"]["numport_4"]: f["properties"] for f in igam["features"]}
    ls = ov.grant_view({"properties": by_port["1306118/2021"], "authority": "IGAM - Outorgas estaduais"}, TODAY)
    assert ls["vazao"]["flow_text"] == "44,4 l/s" and "litros por segundo" in ov.grant_sentence(ls), ls
    no_unit = dict(by_port["1309443/2020"])
    no_unit.pop("unvazao_4")
    v = ov.grant_view({"properties": no_unit, "authority": "IGAM - Outorgas estaduais"}, TODAY)
    assert v["vazao"]["state"] != "found" and ov.grant_row(v)[3] == "" and "autoriza captar" not in ov.grant_sentence(v), v
    seasonal = dict(by_port["1309443/2020"])
    for m in ("jan", "fev", "mar"):
        seasonal[f"vaz{m}_4"] = 2.5
    for m in ("jun", "julh", "ago", "set", "out", "nov"):
        seasonal[f"vaz{m}_4"] = 0
    seasonal["diavenc_4"], seasonal["mesvenc_4"], seasonal["anovenc_4"] = "10", "8", "2026"
    s = ov.grant_view({"properties": seasonal, "authority": "IGAM - Outorgas estaduais"}, TODAY)
    assert s["vazao"]["flow_text"] == "de 2,5 a 10 m³/h conforme o mês", s["vazao"]
    assert s["vazao"]["months_text"] == "6 meses por ano" and s["validade"] == "vencida em 10/08/2026", s
    assert "autorizava captar" in ov.grant_sentence(s), ov.grant_sentence(s)


@check
def outorga_fonte_parcial_nunca_vira_nenhuma():
    import outorga_vazao as ov

    partial = run_water(igam_ok=False)
    assert partial["ok"] is True  # comportamento herdado: basta uma camada responder
    p = ov.water_grants_payload(partial, uf="MG", today=TODAY)
    assert p["state"] == "pending" and p["count"] is None and "nenhuma" not in p["headline"].lower(), p
    capped = run_water()
    capped["layers"]["igam"]["feature_count_bbox"] = 3000
    assert ov.water_grants_payload(capped, uf="MG", today=TODAY)["complete"] is False
    outside = ov.water_grants_payload(run_water(), uf="GO", today=TODAY)
    assert outside["state"] == "not_covered" and outside["headline"] is None, outside
    assert ov.water_grants_payload(None, uf="MG")["state"] == "pending"


@check
def outorga_tipo_de_uso_so_captacao_autoriza_captar():
    """Barramento, dragagem, desvio, hidrelétrica e travessia não são captação; lançamento e referência saem."""
    import outorga_vazao as ov

    water = categories_water()
    assert water["inside_count"] == 16 and len(water["inside"]) == 16, water.get("inside_count")
    p = ov.water_grants_payload(water, uf="MG", today=TODAY)
    assert p["complete"] and p["count"] == 8 and p["other_count"] == 6, (p["count"], p["other_count"], p["complete"])
    assert p["headline"].startswith("Há 8 captações registradas no cadastro de outorgas dentro do imóvel."), p["headline"]
    assert "Há também 6 registros de outorga de outro tipo dentro do imóvel." in p["headline"], p["headline"]
    igam_kinds = {v["portaria"]: v["kind"] for v in p["items"] if v["source"] == "igam"}
    assert igam_kinds == {"1204272/2024": "barramento", "1308963/2020": "dragagem", "1205291/2019": "hidreletrica",
                          "0300656/2023": "intervencao_curso", "1303036/2021": "travessia", "1806028/2023": "captacao",
                          "1301857/2024": "captacao", "0001314/2010": "captacao"}, igam_kinds
    blob = json.dumps(p, ensure_ascii=False)
    for excluded in ("1704/2018", "654/2011"):  # lançamento de efluente e ponto de referência
        assert excluded not in blob, (excluded, "fora da leitura")
    shown = 0
    for view, row in zip(p["items"], p["rows"]):
        sentence = ov.grant_sentence(view)
        if view["kind"] != "captacao" or not view["autoriza"]:
            # O item também é lido por quem liga o relatório: vazão "found" aqui seria vazão autorizada.
            assert view["vazao"].get("state") != "found" and "flow_text" not in view["vazao"], (view["portaria"], view["vazao"])
        if view["kind"] != "captacao":
            assert "captar" not in sentence and not sentence.lower().startswith("captação"), sentence
            assert not re.search(r"(?<!sem )captação,? com outorga", sentence.lower()) and row[3] == "" and row[4] == "", (sentence, row)
        elif not view["autoriza"]:
            assert "captar" not in sentence and row[3] == "", (sentence, row)
        if "autoriza captar" in sentence or "autorizava captar" in sentence:
            shown += 1
            assert view["kind"] == "captacao" and view["autoriza"] and row[3], (sentence, row)
    assert shown == 4, shown
    assert p["headline"].count("autoriza captar") + p["headline"].count("autorizava captar") == 4, p["headline"]
    dam = next(ov.grant_sentence(v) for v in p["items"] if v["portaria"] == "1204272/2024")
    assert dam.startswith("Barramento sem captação, com outorga do IGAM: para paisagismo."), dam
    preventive = next(ov.grant_sentence(v) for v in p["items"] if v["portaria"] == "252/2010")
    revoked = next(ov.grant_sentence(v) for v in p["items"] if v["portaria"] == "530/2012")
    assert "outorga preventiva" in preventive and "revogada" in revoked, (preventive, revoked)
    ptbr_text_ok([p["headline"], *p["notes"], p["compliance_row"]["text"], *[c for row in p["rows"] for c in row]])

    # Variações derivadas das fixtures (declaradas): modo sem "sem captação", modo desconhecido, situação que não autoriza.
    base = next(f["properties"] for f in load("idesisema_igam_outorgas_categorias_sem_pii.geojson")["features"]
                if f["properties"]["numport_4"] == "1301857/2024")
    variants = {
        "Barramento Em Curso De Água Para Fins De Regularização De Vazão": "barramento",
        "Rebaixamento De Nível De Água Subterrânea": "outro",
        "": "outro",
    }
    for mode, kind in variants.items():
        v = ov.grant_view({"properties": {**base, "moduso_4": mode}, "authority": "IGAM - Outorgas estaduais"}, TODAY)
        s = ov.grant_sentence(v)
        assert v["kind"] == kind and "captar" not in s and not s.lower().startswith("captação") and ov.grant_row(v)[3] == "", (mode, v["kind"], s)
    denied = ov.grant_view({"properties": {**base, "statuspa_4": "Indeferido"}, "authority": "IGAM - Outorgas estaduais"}, TODAY)
    assert "captar" not in ov.grant_sentence(denied) and ov.grant_row(denied)[3] == "", ov.grant_sentence(denied)

    # Vizinhança: só captação conta, e a "mais próxima" não pode ser um lançamento ou barramento.
    curvelo = run_water()
    others = [copy.deepcopy(it) for it in water["inside"]
              if ov.item_kind(it) in ("lancamento", "referencia", "barramento", "dragagem")]
    assert len(others) == 5, [ov.item_kind(it) for it in others]
    for it in others:
        it.update({"inside": False, "distance_m": 900.0})
    curvelo["near"] = sorted(curvelo["near"] + others, key=lambda x: x["distance_m"])
    curvelo["near_count"] = len(curvelo["near"])
    near = ov.water_grants_payload(curvelo, uf="MG", today=TODAY)
    assert near["near_text"] == "Outras 6 captações registradas no cadastro de outorgas a até 5 km; a mais próxima a 1,3 km.", near["near_text"]


@check
def outorga_ana_federal_mg_campos_e_unidade():
    """Camada federal da ANA no IDE-Sisema: campos lidos, unidade provada pelo volume, sem dado pessoal."""
    import outorga_vazao as ov

    water = categories_water()
    ana_items = [it for it in water["inside"] if "tipo_inter" in it["properties"]]
    assert len(ana_items) == 8, len(ana_items)
    for it in ana_items:
        assert not pii_keys(it["properties"]), pii_keys(it["properties"])
    kept = ana_items[0]["properties"]
    for key in ("horas_dia1", "horas_di_3", "dia_mês1", "dias_mê_2", "vazão_12_", "numero_pro", "resolucao", "data_de_ve", "volumeanua", "categoria"):
        assert any(key in it["properties"] for it in ana_items), ("campo da ANA descartado", key, sorted(kept))
    p = ov.water_grants_payload(water, uf="MG", today=TODAY)
    rows = {v["portaria"] or v["processo"]: (v, row) for v, row in zip(p["items"], p["rows"]) if v["source"] == "ana_mg"}
    v59, row59 = rows["59/2012"]
    assert row59 == ["Captação", "Resolução 59/2012", "Direito de uso", "350 m³/h", "20 h por dia · 17 dias por mês · 7 meses por ano",
                     "irrigação", "vencida em 19/03/2017"], row59
    assert "Captação com outorga da ANA: autorizava captar 350 m³ por hora" in ov.grant_sentence(v59), ov.grant_sentence(v59)
    v922, row922 = rows["922/2016"]
    assert v922["vazao"]["state"] == "unit_unproven" and row922[3] == "" and "captar" not in ov.grant_sentence(v922), (v922["vazao"], row922)
    assert rows["02501.0048362/2021"][1][1] == "Processo 02501.0048362/2021", rows["02501.0048362/2021"][1]
    for key, (view, row) in rows.items():
        assert sum(1 for c in row if c) >= 3, (key, "linha quase vazia", row)
        assert ov.grant_sentence(view) not in ("Captação com outorga da ANA.",), (key, ov.grant_sentence(view))
    # Unidade não provada: o mesmo registro com volume anual incompatível (derivado) esconde a vazão.
    wrong = copy.deepcopy(ana_items[[it["properties"].get("resolucao") for it in ana_items].index("59/2012")])
    wrong["properties"]["volumeanua"] = 833000 * 3.6
    wv = ov.grant_view(wrong, TODAY)
    assert wv["vazao"]["state"] == "unit_unproven" and ov.grant_row(wv)[3] == "", wv["vazao"]


@check
def outorga_contagem_cortada_nunca_afirma_total():
    """water_mg corta inside em 150 e near em 300: acima do teto o texto diz "ao menos" e a leitura fica parcial."""
    import outorga_vazao as ov

    cut_inside = run_water()
    cut_inside["inside_count"] = 151
    p = ov.water_grants_payload(cut_inside, uf="MG", today=TODAY)
    assert p["count_is_minimum"] and not p["complete"], p
    assert p["headline"].startswith("Há ao menos 1 captação registrada"), p["headline"]
    assert p["compliance_row"]["badge"] == "PARCIAL" and ov.NOTE_PARTIAL in p["notes"], (p["compliance_row"], p["notes"])
    assert "nenhuma" not in json.dumps(p, ensure_ascii=False).lower(), p
    cut_near = run_water()
    cut_near["near_count"] = 400
    n = ov.water_grants_payload(cut_near, uf="MG", today=TODAY)
    assert n["near_text"].startswith("Ao menos outras 6 captações"), n["near_text"]
    empty_cut = run_water()
    empty_cut["inside"], empty_cut["inside_count"] = [], 200
    e = ov.water_grants_payload(empty_cut, uf="MG", today=TODAY)
    assert e["state"] == "pending" and "nenhuma" not in json.dumps(e, ensure_ascii=False).lower(), e
    ptbr_text_ok([p["headline"], n["near_text"], p["compliance_row"]["text"]])


@check
def ana_cnarh_sem_pii_chave_desligada_e_unidade():
    import outorga_vazao as ov

    for key in ov.ANA_CNARH_LAYERS:
        fields = ov.ana_query_params(key, [-44.3, -19.0, -44.0, -18.8])["outFields"]
        assert fields != "*" and "*" not in fields and not any(p in fields.lower() for p in ("cpf", "cnpj", "emp_", "nm_resp")), fields

    class Explode:
        def get(self, *a, **k):
            raise AssertionError("chave desligada não pode consultar a rede")

    assert ov.ana_enabled({}) is False
    off = ov.query_outorgas_ana_cnarh(car_geometry(), [-44.2, -18.9, -44.1, -18.8], enabled=False, client=Explode())
    assert off["state"] == "disabled" and not off["ok"], off

    car = car_geometry()
    sub_in, sub_near = ov.ana_items(car, load("ana_cnarh_estaduais_subterraneas_sem_pii.geojson")["features"], "ana_estaduais_subterraneas")
    assert [i["properties"]["out_nu_ato"] for i in sub_in] == ["1309443/2020"], sub_in
    ana = {"ok": True, "inside": sub_in, "near": sub_near, "radius_km": 5.0,
           "layers": {"ana_estaduais_subterraneas": {"ok": True, "feature_count_bbox": 5}},
           "layer_units": {k: v["flow_unit"] for k, v in ov.ANA_CNARH_LAYERS.items()}}
    go = ov.water_grants_payload(None, uf="GO", today=TODAY, ana=ana)
    assert go["state"] == "found" and go["rows"][0][3] == "10 m³/h" and go["rows"][0][6] == "válida até 15/12/2030", go
    assert "Captação subterrânea com outorga do IGAM: autoriza captar 10 m³ por hora" in go["headline"], go["headline"]
    ptbr_text_ok([go["headline"], *go["notes"]])
    sup = load("ana_cnarh_estaduais_superficiais_sem_pii.geojson")["features"][0]["properties"]
    view = ov.grant_view({"properties": ov.safe_grant_props(sup), "authority": "ANA (CNARH)"}, TODAY, None)
    assert view["vazao"]["state"] == "unit_unproven" and ov.grant_row(view)[3] == "", view

    water = run_water()
    kept = ov.water_grants_payload(water, uf="MG", today=TODAY, ana=ana)
    assert kept["rows"][0][3] == "10 m³/h", kept["rows"]
    divergent = copy.deepcopy(ana)
    divergent["inside"][0]["properties"]["int_qt_vazaomedia"] = 12
    hidden = ov.water_grants_payload(water, uf="MG", today=TODAY, ana=divergent)
    assert hidden["rows"][0][3] == "" and hidden["items"][0]["vazao"]["state"] == "divergent", hidden["rows"]


@check
def fixtures_sem_dado_pessoal():
    # Controle positivo do detector: as colunas pessoais reais das camadas têm de ser pegas.
    planted = {"cpfcnpj_4": 1, "empto_4": 1, "nome_do_re": 1, "nm_titular": 1, "emp_nm_empreendimento": 1}
    assert sorted(pii_keys(planted)) == sorted(planted), pii_keys(planted)
    files = sorted(FIX.glob("*.json")) + sorted(FIX.glob("*.geojson"))
    on_disk = [p for p in FIX.iterdir() if p.is_file()]
    assert len(files) == len(on_disk) == FIXTURE_COUNT, ("fixture fora da leitura", [p.name for p in files], [p.name for p in on_disk])
    features = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for f in data.get("features") or []:
            features += 1
            leaked = pii_keys(f.get("properties") or {})
            assert not leaked, (path.name, leaked)
        raw = path.read_bytes()
        assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf"), (path.name, "CRLF ou BOM")
    assert features > 40, features


@check
def gate_de_texto_reprova_byte_de_controle():
    """O gate de mojibake reprova byte de controle (um "\\b" gravado como backspace já passou)."""
    for name in ("outorga_vazao.py", "iphan_sicg.py", "water_mg.py", "scripts/f2_iphan_outorga_gate.py"):
        raw = (ROOT / name).read_bytes()
        bad = [b for b in raw if b < 0x20 and b not in (0x09, 0x0A)] + [b for b in raw if b == 0x7F]
        assert not bad, (name, sorted(set(bad)))
    gate = _module("scripts/text_mojibake_gate.py")
    cases = (
        (b'm = re.search(r"\x08rios?\x08", low)\n', True),
        (b"antes\x0cdepois\n", True),
        ("texto limpo, com acentuação: captação\n".encode("utf-8"), False),
    )
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.py"
        gate.ROOT = Path(td)
        gate.tracked_files = lambda: [probe]
        for content, must_fail in cases:
            probe.write_bytes(content)
            try:
                with contextlib.redirect_stdout(io.StringIO()):  # não confundir com a linha do gate real no log
                    gate.main()
                failed = False
            except AssertionError:
                failed = True
            assert failed is must_fail, (content, "reprovou" if failed else "aprovou")


# -------------------------------------------------------------------- ao vivo --
def live() -> int:
    import iphan_sicg

    r = asyncio.run(iphan_sicg.query_iphan_sites(car_geometry()))
    if not r.get("ok"):
        print(f"LIVE PENDENTE: IPHAN não respondeu por inteiro ({r.get('layers')}); isto não é aprovação.")
        return 2
    codes = {x["codigo_iphan"] for x in r["inside"] + r["border"] + r["near"]}
    if "MG3120904BAST00003" not in codes:
        print(f"LIVE FALHOU: controle positivo MG3120904BAST00003 ausente; recebidos {sorted(codes)}")
        return 1
    print(f"LIVE OK: controle positivo presente; {len(r['near'])} vizinho(s), {len(r['inside'])} dentro, {len(r['border'])} na divisa.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--only", action="append", default=[], help="roda só as regras com este nome (diagnóstico)")
    args = parser.parse_args()
    selected = [(n, f) for n, f in CHECKS if not args.only or n in args.only]
    assert selected, ("nenhuma regra selecionada", args.only)
    failures = 0
    for name, fn in selected:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # cada regra reprova sozinha, sem derrubar as outras
            failures += 1
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            print(f"FAIL {name}: {detail[:600]}")
    code = 1 if failures else 0
    if args.live and not failures:
        code = live()
    print(f"F2_IPHAN_OUTORGA_GATE={'OK' if code == 0 else 'FAILED'} checks={len(selected)} failures={failures}")
    return code


if __name__ == "__main__":
    sys.exit(main())

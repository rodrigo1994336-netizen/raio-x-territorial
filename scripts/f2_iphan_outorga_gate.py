"""F2 gate: sítios arqueológicos do IPHAN (oficial, até 10 km) e vazão da outorga.

Uso:
  PYTHONPATH=. python scripts/f2_iphan_outorga_gate.py          # offline, fixtures reais
  PYTHONPATH=. python scripts/f2_iphan_outorga_gate.py --live   # + controle positivo no IPHAN real

Fixtures em tests/fixtures/f2_iphan_outorga/, gravadas em 13/09/2026 de respostas
reais para o CAR MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F (Curvelo/MG):
SICAR (polígono), IPHAN GeoServer SICG:sitios e SICG:sitios_pol, PAMGIA
lim_sitios_arqueologicos_iphan_a, IDE-Sisema (IGAM e ANA em MG) e ANA CNARH.
As outorgas foram gravadas sem CPF/CNPJ, nome de empreendimento ou de
responsável. O servidor falso deste gate só faz o que o servidor real faz
(filtrar pela caixa pedida); as falhas são injetadas por cima.

O teste valida a regra, não o valor de uma fonte viva: os valores conferidos
vêm das fixtures congeladas. Controle positivo: rodar este arquivo com o
código de origin/main no PYTHONPATH tem de reprovar (a checagem antiga lia só
polígonos do PAMGIA e descartava as horas de captação).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
import traceback
import types
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "f2_iphan_outorga"
TODAY = date(2026, 9, 13)
CHECKS: list[tuple[str, callable]] = []
BARRA_DO_LUIS = (-44.1637, -18.9509)  # ponto real do IPHAN (sem polígono)
PII_KEYS = ("cpf", "cnpj", "empto", "emp_nm", "respons", "titular", "requer")


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

    def __init__(self, faults: dict[str, list] | None = None):
        self.points = load("iphan_sicg_sitios_pontos.geojson")
        self.polygons = load("iphan_sicg_sitios_poligonos.geojson")
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
            fc = self.pamgia_polygons if "lim_sitios_arqueologicos_iphan_a" in path else {"type": "FeatureCollection", "features": []}
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
    # Divisa leste do imóvel a 8 m do ponto: nem "dentro" nem "fora".
    geom = square(*BARRA_DO_LUIS, 500, shift_east_m=-508)
    res = run_constraints(geom, FakeServers())
    svc = res["services"]["sitio_arqueologico"]
    assert svc.get("ok") is True and svc.get("occurrence_count") == 1, ("ponto a 8 m da divisa não contado", svc.get("occurrence_count"), svc.get("source"))
    import iphan_sicg

    iphan = svc["iphan"]
    assert [x["nome"] for x in iphan["border"]] == ["Sítio Barra do Luis 01"] and not iphan["inside"], iphan
    payload = iphan_sicg.iphan_payload(iphan)
    assert "junto à divisa" in payload["headline"] and payload["level"] == "attention", payload


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
def run_water(igam_ok: bool = True, igam_fc: dict | None = None) -> dict:
    import water_mg

    igam = copy.deepcopy(igam_fc or load("idesisema_igam_outorgas_sem_pii.geojson"))
    for f in igam["features"]:  # colunas pessoais que a resposta real traz (valores fictícios)
        f["properties"]["cpfcnpj_4"] = "00000000000"
        f["properties"]["empto_4"] = "EMPREENDIMENTO FICTICIO"
    ana = load("idesisema_ana_outorgas_mg_sem_pii.geojson")

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
        geom = car_geometry()
        return water_mg.query_outorgas_mg(geom, list(shape(geom).bounds), 5.0)
    finally:
        water_mg._curl = previous


@check
def outorga_campos_de_vazao_guardados_sem_pii():
    water = run_water()
    assert water["ok"] and water["inside_count"] == 1, water.get("inside_count")
    props = water["inside"][0]["properties"]
    needed = ("unvazao_4", "vazjan_4", "vazjulh_4", "tcapjan_4", "tcapdez_4", "dia_fev_4", "finuso1_4", "diavenc_4", "mesvenc_4", "anovenc_4", "datverbase")
    missing = [k for k in needed if k not in props]
    assert not missing, ("campos da outorga descartados", missing)
    for item in water["inside"] + water["near"]:
        leaked = [k for k in item["properties"] if any(p in k.lower() for p in PII_KEYS)]
        assert not leaked, leaked


@check
def outorga_payload_curvelo():
    import outorga_vazao as ov

    p = ov.water_grants_payload(run_water(), uf="MG", today=TODAY)
    assert p["state"] == "found" and p["count"] == 1 and p["complete"], p
    assert p["rows"] == [["1309443/2020", "Deferido", "10 m³/h", "8 h 22 min por dia · o ano todo",
                          "irrigação, consumo humano e dessedentação de animais", "válida até 15/12/2030"]], p["rows"]
    expected = ("Há 1 captação com outorga registrada dentro do imóvel. Poço tubular com outorga do IGAM: autoriza captar "
                "10 m³ por hora, 8 h 22 min por dia, o ano todo, para irrigação, consumo humano e dessedentação de animais. "
                "Portaria 1309443/2020, válida até 15/12/2030.")
    assert p["headline"] == expected, p["headline"]
    assert p["near_text"] == "Outras 6 captações com outorga a até 5 km; a mais próxima a 1,3 km.", p["near_text"]
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
    assert v["vazao"]["state"] != "found" and ov.grant_row(v)[2] == "" and "autoriza captar" not in ov.grant_sentence(v), v
    seasonal = dict(by_port["1309443/2020"])
    for m in ("jan", "fev", "mar"):
        seasonal[f"vaz{m}_4"] = 2.5
    for m in ("jun", "julh", "ago", "set", "out", "nov"):
        seasonal[f"vaz{m}_4"] = 0
    seasonal["diavenc_4"], seasonal["mesvenc_4"], seasonal["anovenc_4"] = "10", "8", "2026"
    s = ov.grant_view({"properties": seasonal, "authority": "IGAM - Outorgas estaduais"}, TODAY)
    assert s["vazao"]["flow_text"] == "de 2,5 a 10 m³/h conforme o mês", s["vazao"]
    assert s["vazao"]["months_text"] == "6 meses por ano" and s["validade"] == "vencida em 10/08/2026", s


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
    assert go["state"] == "found" and go["rows"][0][2] == "10 m³/h" and go["rows"][0][5] == "válida até 15/12/2030", go
    assert "Captação subterrânea com outorga do IGAM: autoriza captar 10 m³ por hora" in go["headline"], go["headline"]
    ptbr_text_ok([go["headline"], *go["notes"]])
    sup = load("ana_cnarh_estaduais_superficiais_sem_pii.geojson")["features"][0]["properties"]
    view = ov.grant_view({"properties": ov.safe_grant_props(sup), "authority": "ANA (CNARH)"}, TODAY, None)
    assert view["vazao"]["state"] == "unit_unproven" and ov.grant_row(view)[2] == "", view

    water = run_water()
    kept = ov.water_grants_payload(water, uf="MG", today=TODAY, ana=ana)
    assert kept["rows"][0][2] == "10 m³/h", kept["rows"]
    divergent = copy.deepcopy(ana)
    divergent["inside"][0]["properties"]["int_qt_vazaomedia"] = 12
    hidden = ov.water_grants_payload(water, uf="MG", today=TODAY, ana=divergent)
    assert hidden["rows"][0][2] == "" and hidden["items"][0]["vazao"]["state"] == "divergent", hidden["rows"]


@check
def fixtures_sem_dado_pessoal():
    for path in sorted(FIX.glob("*.json*")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for f in data.get("features") or []:
            leaked = [k for k in (f.get("properties") or {}) if any(p in k.lower() for p in PII_KEYS)]
            assert not leaked, (path.name, leaked)
        raw = path.read_bytes()
        assert b"\r\n" not in raw, (path.name, "CRLF")


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
    args = parser.parse_args()
    failures = 0
    for name, fn in CHECKS:
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
    print(f"F2_IPHAN_OUTORGA_GATE={'OK' if code == 0 else 'FAILED'} checks={len(CHECKS)} failures={failures}")
    return code


if __name__ == "__main__":
    sys.exit(main())

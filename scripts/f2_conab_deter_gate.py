"""F2 gate: armazéns CONAB e alertas DETER dizem só o que as fontes sustentam.

Uso:
  PYTHONPATH=. python scripts/f2_conab_deter_gate.py            # offline, respostas reais gravadas
  PYTHONPATH=. python scripts/f2_conab_deter_gate.py --ao-vivo  # + regra (nunca valor) contra as fontes

As respostas em tests/fixtures/f2_conab_deter/ foram gravadas das fontes reais por
scripts/f2_conab_deter_fixtures.py (sem dado pessoal). Cada regra tem controle
positivo: o gate injeta o defeito antigo (distância ao centro, ETag com "-gzip",
CQL sem SRID, falha virando zero, soma entre fontes, nome de pessoa física, sem
corte do PRODES, "15.930 t" no PDF, polígono grande no CSV) e exige que a checagem reprove.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "f2_conab_deter"

from shapely import wkt as shapely_wkt  # noqa: E402
from shapely.geometry import box, shape  # noqa: E402

import conab_armazens as ca  # noqa: E402
import deter_alertas as da  # noqa: E402
import report_ptbr_v50  # noqa: E402


def _carregar(nome: str, arquivo: Path):
    spec = importlib.util.spec_from_file_location(nome, arquivo)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R1 = _carregar("r1_report_ptbr_gate", ROOT / "scripts" / "r1_report_ptbr_gate.py")
INDICE = json.loads((FIX / "respostas.json").read_text(encoding="utf-8"))
CAR = json.loads((FIX / "car_curvelo.geojson").read_text(encoding="utf-8"))
CAR_GEOM = CAR["features"][0]["geometry"]
PROVA = [("54.7645.0001-4", "31,1 km", "2.875 toneladas"), ("54.0472.0006-4", "47,1 km", "15.930 toneladas")]
CAIXAS = {
    "controle_alerta_recente_cerrado": box(-44.2045, -18.5455, -44.1955, -18.5385),
    "controle_alerta_18km_cerrado": box(-44.36, -18.92, -44.34, -18.90),
    "controle_alerta_amazonia": box(-55.36, -12.05, -55.33, -12.035),
    "controle_fora_cobertura": box(-46.64, -23.56, -46.62, -23.54),
}
PROIBIDO = re.compile(r"sem alerta|sem desmatamento|área regular|sem pendência|SEM ALERTA|Traceback|Error\b", re.I)
PII = ("removido@example.invalid", "ENDERECO REMOVIDO", "PESSOA FISICA FICTICIA", "EMPRESARIO FICTICIO")


# ---------------------------------------------------------------- servidor gravado

class Resposta:
    def __init__(self, status: int, corpo: bytes, headers: dict | None = None):
        self.status_code = status
        self.content = corpo
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content)


def _cql_igual(a: str, b: str) -> bool:
    rx = re.compile(r"^(\w+)\(geom,SRID=4674;(.+)\)$")
    ma, mb = rx.match(a), rx.match(b)
    if not (ma and mb):
        return a == b
    ga, gb = shapely_wkt.loads(ma.group(2)).normalize(), shapely_wkt.loads(mb.group(2)).normalize()
    return ma.group(1) == mb.group(1) and ga.equals_exact(gb, 1e-9)


def _bbox_igual(a: str, b: str) -> bool:
    pa, pb = a.split(","), b.split(",")
    if len(pa) != len(pb) or pa[-1] != pb[-1]:
        return False
    return all(abs(float(x) - float(y)) < 1e-9 for x, y in zip(pa[:-1], pb[:-1]))


def _params_iguais(gravado: dict, pedido: dict) -> bool:
    if set(gravado) != set(pedido):
        return False
    for k, v in gravado.items():
        w = str(pedido[k])
        if k == "CQL_FILTER" and not _cql_igual(str(v), w):
            return False
        if k == "bbox" and not _bbox_igual(str(v), w):
            return False
        if k not in ("CQL_FILTER", "bbox") and str(v) != w:
            return False
    return True


class Servidor:
    """Responde com as respostas reais gravadas; pedido não gravado = erro (nunca inventa)."""

    def __init__(self, falhar: tuple[str, ...] = (), trocar: dict | None = None):
        self.falhar = falhar
        self.trocar = trocar or {}
        self.pedidos: list[tuple[str, dict, dict]] = []

    def __call__(self, url, *, params=None, headers=None):
        params, headers = dict(params or {}), dict(headers or {})
        self.pedidos.append((url, params, headers))
        for padrao in self.falhar:
            if padrao in url or padrao in json.dumps(params):
                raise ConnectionError("simulado")
        if url == ca.URL_ARMAZENS:
            return self._conab(headers)
        for r in INDICE["respostas"]:
            if r["url"] == url and _params_iguais(r["params"], params):
                corpo = (FIX / r["arquivo"]).read_bytes()
                for trecho, novo in self.trocar.items():
                    if trecho in url or trecho in json.dumps(params):
                        corpo = novo
                return Resposta(r["status"], corpo, r["headers"])
        raise LookupError(f"pedido sem resposta gravada: {url} {params}")

    def _conab(self, headers: dict) -> Resposta:
        cheio = next(r for r in INDICE["respostas"] if r["cenario"] == "conab_download")
        cond = next(r for r in INDICE["respostas"] if r["cenario"] == "conab_condicional")
        etag_gzip = cheio["headers"]["etag"]
        etag = re.sub(r'-gzip"$', '"', etag_gzip)
        inm, ims = headers.get("If-None-Match"), headers.get("If-Modified-Since")
        # Comportamento medido em 13/09/2026: 304 com o ETag sem "-gzip" ou com
        # If-Modified-Since igual ao Last-Modified; ETag "-gzip", fraco ou lista = 200.
        if inm == etag or (ims == cheio["headers"]["last-modified"] and inm in (None, etag, etag_gzip)):
            return Resposta(304, b"", cond["headers"])
        corpo = self.trocar.get(ca.URL_ARMAZENS) or gzip.decompress((FIX / cheio["arquivo"]).read_bytes())
        return Resposta(200, corpo, cheio["headers"])


# ---------------------------------------------------------------- utilidades

@contextmanager
def cache_temporario():
    d = Path(tempfile.mkdtemp(prefix="f2gate-"))
    ca._MEMORIA.clear()
    try:
        yield d
    finally:
        ca._MEMORIA.clear()
        shutil.rmtree(d, ignore_errors=True)


@contextmanager
def recorte_pequeno():
    antigo = ca.MIN_LINHAS
    ca.MIN_LINHAS = 1  # o recorte gravado tem 14 linhas; o arquivo nacional exige 5.000
    try:
        yield
    finally:
        ca.MIN_LINHAS = antigo


@contextmanager
def trocar(mod, nome: str, novo):
    antigo = getattr(mod, nome)
    setattr(mod, nome, novo)
    try:
        yield
    finally:
        setattr(mod, nome, antigo)


def textos(obj) -> list[str]:
    saida = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "notes", "line", "table_rows", "capacity_text", "distance_text", "area_in_property_text",
                     "source_text", "label", "municipality_uf", "first_seen_text", "base_date_text", "classes", "satellites"):
                saida += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)] + [
                    y for x in (v if isinstance(v, list) else []) if isinstance(x, list) for y in x if isinstance(y, str)]
            if isinstance(v, (dict, list)):
                saida += textos(v)
    elif isinstance(obj, list):
        for v in obj:
            saida += textos(v) if isinstance(v, (dict, list)) else []
    return saida


def conferir_textos(payload: dict) -> None:
    for t in textos(payload):
        assert report_ptbr_v50.normalize_text(t) == t, f"o PDF reescreveria o texto: {t!r} -> {report_ptbr_v50.normalize_text(t)!r}"
        assert not R1.lint_text(t), (t, R1.lint_text(t))
        assert not PROIBIDO.search(t), t


def conab(geom, servidor=None, base=None):
    return ca.conab_warehouses_payload(geom, base_dir=base, http_get=servidor or Servidor())


def deter(geom, servidor=None, prodes=None):
    da._CACHE.clear()
    return da.deter_alerts_payload(geom, prodes, http_get=servidor or Servidor())


def alerta_csv(cenario: str) -> list[dict]:
    r = next(x for x in INDICE["respostas"] if x["cenario"] == f"deter_{cenario}" and x["params"].get("outputFormat") == "csv")
    sistema = "amazonia" if "deter-amz" in r["url"] else "cerrado"
    return da.parse_alerts_csv((FIX / r["arquivo"]).read_text(encoding="utf-8"), sistema)[0]


# ---------------------------------------------------------------- CONAB

def check_conab_curvelo_prova() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, base=base)
    assert p["state"] == "found" and p["complete"] is True, p
    got = [(i["cda"], i["distance_text"], i["capacity_text"]) for i in p["items"]]
    assert got == PROVA, got
    assert all(i["municipality_uf"] == "Curvelo/MG" for i in p["items"]), p["items"]
    assert p["text"].startswith("2 armazéns cadastrados na CONAB em até 50 km do imóvel"), p["text"]
    assert re.search(r"base de \d{2}/\d{2}/\d{4}$", p["source_text"]), p["source_text"]
    assert p["table_rows"][0][1:] == ["Curvelo/MG", "2.875 toneladas", "31,1 km"], p["table_rows"]
    # borda, não centro: o centro dá 31,4 e 47,4 km para os mesmos pontos
    c = shape(CAR_GEOM).centroid
    por_cda = {r["cda"]: r for r in _linhas_fixture()}
    for item in p["items"]:
        r = por_cda[item["cda"]]
        centro = ca._haversine_km(c.y, c.x, r["lat"], r["lon"])
        assert item["distance_km"] < centro - 0.2, (item, centro)


def check_conab_textos_sobrevivem_ao_pdf() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, base=base)
    assert p["items"], p
    conferir_textos(p)


def check_conab_lgpd() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, base=base)
        gravado = b"".join(x.read_bytes() for x in base.rglob("*") if x.is_file())
        linhas, _ = ca.load_conab_warehouses(base)
    texto_gravado = gravado.decode("utf-8")
    for marcador in PII:
        assert marcador not in texto_gravado, f"dado pessoal gravado em disco: {marcador}"
        assert marcador not in json.dumps(p, ensure_ascii=False), f"dado pessoal no payload: {marcador}"
    assert linhas and all("email" not in r and "endereco" not in r for r in linhas)
    assert all(r["nome"] is None for r in linhas if r["pessoa"] != "juridica"), "nome de pessoa física guardado"
    casos = {
        ("SAO GERALDO PROD COM SERV LTDA", "PESSOA JURÍDICA", "PRIVADA"): True,
        ("COOPERATIVA CENTRAL DOS PRODUTORES RURAIS DE MINAS GERAIS LTDA.", "PESSOA JURÍDICA", "COOPERATIVA"): True,
        ("AGRO EXEMPLO S/A", "PESSOA JURÍDICA", "PRIVADA"): True,
        ("FULANO DE TAL LTDA", "PESSOA FÍSICA", "PRIVADA"): False,
        ("FULANO DE TAL ME", "PESSOA JURÍDICA", "PRIVADA"): False,
        ("FULANO DE TAL EIRELI", "PESSOA JURÍDICA", "PRIVADA"): False,
        ("12.345.678 FULANO DE TAL", "PESSOA JURÍDICA", "PRIVADA"): False,
        ("FULANO DE TAL", "PESSOA JURÍDICA", "PRIVADA"): False,
        ("FULANO DE TAL", "NÃO INFORMADO", "PRIVADA"): False,
    }
    for (nome, pessoa, ent), mostra in casos.items():
        assert (ca.nome_publicavel(nome, pessoa, ent) is not None) is mostra, (nome, pessoa, ent)


def _bruto_fixture() -> bytes:
    return gzip.decompress((FIX / next(r for r in INDICE["respostas"] if r["cenario"] == "conab_download")["arquivo"]).read_bytes())


def _linhas_fixture() -> list[dict]:
    return ca.ler_arquivo_armazens(_bruto_fixture())[0]


def check_conab_arquivo_real() -> None:
    bruto = _bruto_fixture()
    texto, cod = ca.decodificar(bruto)
    assert cod == "latin-1", cod
    linhas, stats = ca.ler_arquivo_armazens(bruto)
    assert stats["duplicados"] == 1 and sum(1 for r in linhas if r["cda"] == "56.A873.0021-1") == 1, stats
    assert any(r["especie"] == "Granel sólido" for r in linhas), "acento perdido na leitura ISO-8859-1"
    assert not any("Ã" in json.dumps(r, ensure_ascii=False) for r in linhas), "mojibake"
    ro = next(r for r in linhas if r["cda"] == "69.0000.0034-2")
    assert ro["lat"] is None and stats["coord_invalidas"] == 1, "coordenada fora do Brasil entrou"
    try:
        ca.ler_arquivo_armazens(b"a;b;c\r\n1;2;3\r\n")
    except ValueError as exc:
        assert "cabecalho" in str(exc)
    else:
        raise AssertionError("cabeçalho diferente foi aceito")
    zero = ca.build_warehouses_payload({"raio_km": 50, "itens": [{**next(r for r in linhas if r["cda"] == "79.G955.0001-1"), "distancia_km": 1.0}],
                                        "excluidos_localizacao": [], "sem_conferencia": [], "candidatos": 1}, {})
    assert "capacity_text" not in zero["items"][0], "capacidade 0 aparece como informação"


def _caixa_mal_localizada():
    r = next(x for x in _linhas_fixture() if x["cda"] == INDICE["conab_mal_localizado"]["cda"])
    return box(r["lon"] + 0.09, r["lat"] - 0.005, r["lon"] + 0.10, r["lat"] + 0.005)  # ~10 km a leste do ponto


def check_conab_ponto_fora_do_municipio() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(_caixa_mal_localizada(), base=base)
    assert p["state"] == "not_found" and not p["items"], p
    assert p["audit"]["location_mismatch"] == 1, p["audit"]
    assert "com localização conferida" in p["text"], p["text"]
    conferir_textos(p)


def check_conab_malha_indisponivel_pendente() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, Servidor(falhar=("servicodados.ibge.gov.br",)), base=base)
    assert p["state"] == "pending" and p["text"] == "Consulta pendente.", p


def check_conab_sem_base_e_download_falhou() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, Servidor(falhar=("portaldeinformacoes.conab.gov.br",)), base=base)
    assert p["state"] == "pending" and p["items"] == [] and p["text"] == "Consulta pendente.", p


def check_conab_base_anterior_mantida() -> None:
    with cache_temporario() as base, recorte_pequeno():
        assert ca.sync_conab_warehouses(base, http_get=Servidor())["estado"] == "baixado"
        r = ca.sync_conab_warehouses(base, http_get=Servidor(falhar=("portaldeinformacoes",)), forcar=True)
        assert r["estado"] == "falhou" and r["tem_base"], r
        # arquivo novo com cabeçalho diferente (servidor devolve 200 porque não mandamos condicional)
        meta = json.loads((base / "conab" / "armazens.meta.json").read_text(encoding="utf-8"))
        meta.pop("etag"), meta.pop("last_modified")
        (base / "conab" / "armazens.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        r = ca.sync_conab_warehouses(base, http_get=Servidor(trocar={ca.URL_ARMAZENS: b"x;y\r\n1;2\r\n"}), forcar=True)
        assert r["estado"] == "invalido", r
        cheio = _bruto_fixture()
        encolhido = b"\r\n".join(cheio.split(b"\r\n")[:4]) + b"\r\n"
        r = ca.sync_conab_warehouses(base, http_get=Servidor(trocar={ca.URL_ARMAZENS: encolhido}), forcar=True)
        assert r["estado"] == "invalido" and r["erro"] == "linhas_insuficientes", r
        p = conab(CAR_GEOM, base=base)
        assert p["state"] == "found" and len(p["items"]) == 2, p


def check_conab_condicional_etag_gzip() -> None:
    with cache_temporario() as base, recorte_pequeno():
        assert ca.sync_conab_warehouses(base, http_get=Servidor())["estado"] == "baixado"
        s = Servidor()
        r = ca.sync_conab_warehouses(base, http_get=s, forcar=True)
        enviado = s.pedidos[-1][2]
    assert r["estado"] == "sem_mudanca", (r, enviado)
    assert "-gzip" not in enviado.get("If-None-Match", "") and enviado.get("If-Modified-Since"), enviado


def check_conab_raio_vazio_nao_e_pendente() -> None:
    with cache_temporario() as base, recorte_pequeno():
        p = conab(box(-40.01, -10.01, -40.0, -10.0), base=base)
    assert p["state"] == "not_found" and p["complete"] is True, p
    assert p["text"] == "Nenhum armazém cadastrado na CONAB em até 50 km do imóvel.", p["text"]


# ---------------------------------------------------------------- DETER

def check_deter_curvelo() -> None:
    p = deter(CAR_GEOM)
    assert p["state"] == "not_found" and p["complete"] is True, p
    cer, amz = p["systems"]["cerrado"], p["systems"]["amazonia"]
    assert cer["coverage"] == "total" and cer["state"] == "not_found", cer
    assert amz["state"] == "not_covered" and amz["text"] == "Não coberto por este sistema.", amz
    corte = date.fromisoformat(p["cutoff"])
    assert f"desde {(corte + timedelta(days=1)).strftime('%d/%m/%Y')}" in p["text"], p["text"]
    assert p["text"].startswith("Nenhum alerta recente de satélite do INPE sobre o imóvel"), p["text"]
    assert "a partir de 3 ha" in p["text"], p["text"]
    assert not p["events"] and not p["notes"], p
    conferir_textos(p)


def check_deter_controle_alerta_recente() -> None:
    geom = CAIXAS["controle_alerta_recente_cerrado"]
    p = deter(geom)
    assert p["state"] == "found" and len(p["events"]) == 1, p
    ev = p["events"][0]
    alerta = next(a for a in alerta_csv("controle_alerta_recente_cerrado") if a["id"] == ev["alert_ids"][0])
    assert date.fromisoformat(ev["first_seen"]) > date.fromisoformat(p["cutoff"]), ev
    assert ev["classes"] == ["Desmatamento (corte raso)"] and ev["satellites"][0].startswith("AMAZONIA-1"), ev
    assert abs(ev["area_in_property_ha"] - da.area_ha(alerta["geometria"])) < 1e-3, ev
    assert any("não é multa nem auto de infração" in n for n in p["notes"]), p["notes"]
    assert da.NOTA_CREDITO in p["notes"], p["notes"]
    conferir_textos(p)


def check_deter_controle_alerta_18km() -> None:
    geom = CAIXAS["controle_alerta_18km_cerrado"]
    alerta = next(a for a in alerta_csv("controle_alerta_18km_cerrado") if a["id"] == "deter_cerrado.2061786_hist")
    from pyproj import Geod
    from shapely.ops import nearest_points
    car = shape(CAR_GEOM)
    a, b = nearest_points(alerta["geometria"], car)
    km = Geod(ellps="GRS80").inv(a.x, a.y, b.x, b.y)[2] / 1000
    assert 15 < km < 20, km  # o alerta real citado na prova, ~18 km a oeste do imóvel
    assert not alerta["geometria"].intersects(car)
    p = deter(geom)
    assert p["audit"]["alerts_up_to_cutoff_in_property"] == 1, p["audit"]  # o instrumento vê o alerta
    assert p["state"] == "not_found" and not p["events"], p  # e não o mostra como recente (PRODES já cobre)
    coords = re.findall(r"-44\.\d+", alerta["geometria"].wkt)
    assert any(len(c.split(".")[1]) > 4 for c in coords), "geometria arredondada (JSON), não a do CSV"


def _prodes_18km() -> dict:
    r = next(x for x in INDICE["respostas"] if x["cenario"] == "prodes_controle_18km")
    return {"hits": [{"layer": "prodes-cerrado-nb:yearly_deforestation", "features": json.loads((FIX / r["arquivo"]).read_bytes())["features"]}]}


def _respostas(geom, corte: str | None = None) -> dict:
    da._CACHE.clear()
    resp = da.query_deter_live(geom, http_get=Servidor())
    if corte:
        for s in resp.values():
            if s.get("corte"):
                s["corte"] = corte
    return resp


def check_deter_prodes_sem_dupla_contagem() -> None:
    geom = CAIXAS["controle_alerta_18km_cerrado"]
    # Se o último PRODES publicado fosse o de 2024, o alerta de 09/12/2024 seria recente
    # e cairia sobre a ocorrência PRODES 2025 real deste lugar.
    p = da.deter_alerts_in_property(geom, _respostas(geom, "2024-07-31"), da.prodes_features_from_result(_prodes_18km()))
    assert p["state"] == "found" and len(p["events"]) == 1, p
    ev = p["events"][0]
    assert ev["prodes_overlap_ha"] > 1 and 2025 in ev["prodes_overlap_years"], ev
    assert ev["line"].endswith("já constavam no mapa anual do PRODES de 2025"), ev["line"]  # anos com lasca abaixo de 1 pixel não entram
    assert abs(p["area_union_ha"] - ev["area_in_property_ha"]) < 1e-3, p  # sobreposição informada, não somada
    conferir_textos(p)


def check_deter_uniao_entre_sistemas() -> None:
    geom = CAIXAS["controle_alerta_recente_cerrado"]
    resp = _respostas(geom)
    copia = [{**a, "id": "deter_amz.copia_do_mesmo_lugar", "sistema": "amazonia"} for a in resp["cerrado"]["alertas"]]
    resp["amazonia"] = {**resp["cerrado"], "cobertura": "total", "alertas": copia}
    p = da.deter_alerts_in_property(geom, resp)
    assert len(p["events"]) == 1 and p["events"][0]["systems"] == ["amazonia", "cerrado"], p["events"]
    unico = da.area_ha(resp["cerrado"]["alertas"][0]["geometria"].intersection(geom))
    assert abs(p["area_union_ha"] - unico) < 1e-3, (p["area_union_ha"], unico)
    assert p["audit"]["sum_by_source_ha"] > 1.9 * unico, p["audit"]


def check_deter_falha_vira_pendente() -> None:
    for falha in ("outputFormat\": \"csv", "biome_border"):
        p = deter(CAR_GEOM, Servidor(falhar=(falha,)))
        assert p["state"] == "pending" and p["text"] == "Consulta pendente.", (falha, p)
        assert p["complete"] is False
    resp = _respostas(CAIXAS["controle_alerta_recente_cerrado"])
    resp["cerrado"]["truncado"] = True
    p = da.deter_alerts_in_property(CAIXAS["controle_alerta_recente_cerrado"], resp)
    assert p["state"] == "pending", p  # página cheia: 2000 não é todos


def check_deter_fora_da_cobertura() -> None:
    p = deter(CAIXAS["controle_fora_cobertura"])
    assert p["state"] == "not_covered", p
    assert "não cobre" in p["text"] and "Nenhum" not in p["text"], p["text"]
    assert all(s["state"] == "not_covered" for s in p["systems"].values())
    conferir_textos(p)


def check_deter_amazonia() -> None:
    p = deter(CAIXAS["controle_alerta_amazonia"])
    assert p["systems"]["cerrado"]["state"] == "not_covered", p["systems"]
    assert p["systems"]["amazonia"]["coverage"] == "total", p["systems"]
    assert p["state"] == "found" and all(e["systems"] == ["amazonia"] for e in p["events"]), p
    conferir_textos(p)


def check_deter_consulta_sem_armadilhas() -> None:
    geom = shape(CAR_GEOM)
    pa = da.params_alerts("cerrado", geom)
    minx, miny, maxx, maxy = geom.bounds
    assert pa["bbox"] == f"{minx},{miny},{maxx},{maxy},EPSG:4674" and pa["outputFormat"] == "csv", pa
    for pred in ("INTERSECTS", "CONTAINS"):
        cql = da.params_coverage("cerrado", geom, pred)["CQL_FILTER"]
        assert cql.startswith(f"{pred}(geom,SRID=4674;"), cql
        assert shapely_wkt.loads(cql.split(";", 1)[1][:-1]).covers(geom), "envoltória não contém o CAR"


def check_deter_csv_poligono_grande() -> None:
    from shapely.geometry import Point
    grande = Point(-55.0, -10.0).buffer(0.2, quad_segs=4000)  # ~16 mil vértices, WKT > 131.072 caracteres
    assert len(grande.wkt) > 131072
    cabecalho = "FID,gid,classname,view_date,sensor,satellite,geom"
    csv_txt = f'{cabecalho}\r\ndeter_amz.1_curr,1_curr,DESMATAMENTO_CR,2026-01-01,WFI,AMAZONIA-1,"{grande.wkt}"\r\n'
    alertas, linhas = da.parse_alerts_csv(csv_txt, "amazonia")
    assert linhas == 1 and len(alertas) == 1 and alertas[0]["geometria"].area > 0


def check_deter_corte_discordante() -> None:
    hist = json.dumps({"type": "FeatureCollection", "features": [{"properties": {"view_date": "2025-06-30"}}]}).encode()
    da._CACHE.clear()
    s = Servidor(trocar={"gid LIKE": hist})
    corte = da.query_cutoff("cerrado", s)
    assert corte["data"] == "2025-06-30" and corte["discordancia"], corte  # vale a mais antiga, e registra


CHECKS = [v for k, v in dict(globals()).items() if k.startswith("check_")]


# ---------------------------------------------------------------- controles positivos

def _falha(check) -> bool:
    try:
        check()
    except Exception:
        return True
    return False


def _centro(linhas, geom, raio_km=50.0):
    g = ca._geometria(geom)
    c = g.centroid
    return sorted(({**r, "distancia_km": ca._haversine_km(c.y, c.x, r["lat"], r["lon"])} for r in linhas
                   if r.get("lat") is not None and ca._haversine_km(c.y, c.x, r["lat"], r["lon"]) <= raio_km),
                  key=lambda x: x["distancia_km"])


def _cobertura_sem_srid(sistema, geom, predicado):
    p = ORIGINAIS["params_coverage"](sistema, geom, predicado)
    return {**p, "CQL_FILTER": p["CQL_FILTER"].replace("SRID=4674;", "")}


def _falha_vira_zero(geom, respostas, prodes=None):
    r = {k: dict(v) for k, v in respostas.items()}
    for v in r.values():
        if v.get("alertas") is None:
            v["alertas"], v["cobertura"], v["corte"] = [], v.get("cobertura") or "total", v.get("corte") or "2025-07-31"
    return ORIGINAIS["deter_alerts_in_property"](geom, r, prodes)


def _soma_entre_fontes(geom, respostas, prodes=None):
    p = ORIGINAIS["deter_alerts_in_property"](geom, respostas, prodes)
    p["area_union_ha"] = p["audit"]["sum_by_source_ha"]
    return p


def _sem_corte(geom, respostas, prodes=None):
    r = {k: {**v, "corte": "1900-01-01" if v.get("corte") else v.get("corte")} for k, v in respostas.items()}
    return ORIGINAIS["deter_alerts_in_property"](geom, r, prodes)


def _csv_sem_limite(texto, sistema):
    import csv
    import io

    csv.field_size_limit(131072)  # limite padrão do módulo csv, como antes da correção
    return [dict(r) for r in csv.DictReader(io.StringIO(texto))], 1


ORIGINAIS = {"params_coverage": da.params_coverage, "deter_alerts_in_property": da.deter_alerts_in_property}
MUTANTES = [
    ("distância ao centro do imóvel", ca, "candidates_in_radius", _centro, check_conab_curvelo_prova),
    ("If-None-Match com o ETag '-gzip' e sem If-Modified-Since", ca, "cabecalhos_condicionais",
     lambda meta: {"If-None-Match": (meta or {}).get("etag")} if meta else {}, check_conab_condicional_etag_gzip),
    ("nome de pessoa física guardado", ca, "nome_publicavel", lambda nome, tp, ent: (nome or "").strip() or None, check_conab_lgpd),
    ("ponto não conferido no município declarado", ca, "point_matches_municipality", lambda *a, **k: True, check_conab_ponto_fora_do_municipio),
    ("'15.930 t' no PDF", ca, "_capacidade_texto", lambda v: f"{report_ptbr_v50.format_decimal(v, 0)} t" if v else None, check_conab_textos_sobrevivem_ao_pdf),
    ("CQL de cobertura sem SRID", da, "params_coverage", _cobertura_sem_srid, check_deter_curvelo),
    ("consulta que falhou vira zero", da, "deter_alerts_in_property", _falha_vira_zero, check_deter_falha_vira_pendente),
    ("área somada entre fontes", da, "deter_alerts_in_property", _soma_entre_fontes, check_deter_uniao_entre_sistemas),
    ("sem corte do PRODES", da, "deter_alerts_in_property", _sem_corte, check_deter_controle_alerta_18km),
    ("polígono grande estoura o limite do csv", da, "parse_alerts_csv", _csv_sem_limite, check_deter_csv_poligono_grande),
]


def main() -> int:
    falhas = []
    for check in CHECKS:
        try:
            check()
            print(f"PASS {check.__name__}")
        except Exception as exc:
            falhas.append(check.__name__)
            print(f"FAIL {check.__name__}: {type(exc).__name__}: {str(exc)[:400]}")
    controles = 0
    for nome, mod, attr, mutante, check in MUTANTES:
        with trocar(mod, attr, mutante):
            viu = _falha(check)
        if viu:
            controles += 1
            print(f"CONTROLE_POSITIVO_OK {check.__name__} reprova com defeito: {nome}")
        else:
            falhas.append(f"controle:{nome}")
            print(f"CONTROLE_POSITIVO_FALHOU {check.__name__} não viu o defeito: {nome}")
    if "--ao-vivo" in sys.argv:
        falhas += ao_vivo()
    if falhas:
        print(f"F2_CONAB_DETER_GATE=FAIL falhas={falhas}")
        return 1
    print(f"F2_CONAB_DETER_GATE=PASS checks={len(CHECKS)} controles_positivos={controles}")
    return 0


def ao_vivo() -> list[str]:
    """Regra, nunca valor: estado válido, texto limpo, pendência nunca vira zero."""
    falhas = []
    with cache_temporario() as base:
        for nome, fn in (("conab", lambda: ca.conab_warehouses_payload(CAR_GEOM, base_dir=base)),
                         ("deter", lambda: da.deter_alerts_payload(CAR_GEOM))):
            try:
                p = fn()
                assert p["state"] in ("found", "not_found", "pending", "not_covered"), p["state"]
                if p["state"] == "pending":
                    assert p["text"] == "Consulta pendente." and not p.get("items") and not p.get("events"), p
                conferir_textos(p)
                print(f"AO_VIVO_OK {nome} state={p['state']}")
            except Exception as exc:
                falhas.append(f"ao_vivo:{nome}")
                print(f"AO_VIVO_FAIL {nome}: {type(exc).__name__}: {str(exc)[:300]}")
    return falhas


if __name__ == "__main__":
    raise SystemExit(main())

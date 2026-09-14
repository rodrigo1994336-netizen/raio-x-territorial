"""F2 gate: armazéns CONAB e alertas DETER dizem só o que as fontes sustentam.

Uso:
  PYTHONPATH=. python scripts/f2_conab_deter_gate.py            # offline, respostas reais gravadas
  PYTHONPATH=. python scripts/f2_conab_deter_gate.py --ao-vivo  # + regra (nunca valor) contra as fontes

As respostas em tests/fixtures/f2_conab_deter/ foram gravadas das fontes reais por
scripts/f2_conab_deter_fixtures.py (sem dado pessoal). Cada regra tem controle
positivo: o gate injeta o defeito antigo (distância ao centro, ETag com "-gzip",
CQL sem SRID, falha virando zero, soma entre fontes, nome de pessoa física, sem
corte do PRODES, "15.930 t" no PDF, polígono grande no CSV) e exige que a checagem reprove.
Pós-revisão, também: envoltória decidindo a divisa do bioma, corte de cada sistema
com o texto usando o mais antigo, degradação contada como desmatamento, "mais próximo"
com armazém sem conferência, falha não guardada, trava que faz fila e o contrato do
combinador MapBiomas × DETER × PRODES.
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
from shapely.geometry import box, mapping, shape  # noqa: E402

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
    # alerta real deter_amz.11108_curr (DEGRADACAO, 09/01/2026, Juara/MT)
    "controle_degradacao_amazonia": box(-57.562, -10.371, -57.549, -10.360),
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
    ca._FALHAS.clear()
    try:
        yield d
    finally:
        ca._MEMORIA.clear()
        ca._FALHAS.clear()
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
    da._COB_FALHA.clear()
    return da.deter_alerts_payload(geom, prodes, http_get=servidor or Servidor(), base_dir=_BASE_DETER)


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
    da._COB_FALHA.clear()
    resp = da.query_deter_live(geom, http_get=Servidor(), base_dir=_BASE_DETER)
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


# ---------------------------------------------------------------- pós-revisão (divisa, corte único, classes, trava, combinador)

class ServidorBioma:
    """Servidor sintético que avalia o CQL de cobertura contra uma área monitorada inventada.

    Serve só para a divisa do bioma: a geometria real (14–19 MB) não vira fixture. Alertas: CSV
    vazio; corte e última imagem: datas fixas. ``falhar_geometria`` derruba o download da área.
    """

    def __init__(self, areas: dict, falhar_geometria: bool = False):
        self.areas = areas  # sistema -> geometria (ou None = não monitora)
        self.falhar_geometria = falhar_geometria
        self.pedidos: list[tuple[str, dict]] = []

    def _sistema(self, params: dict) -> str | None:
        nome = str(params.get("typeNames") or "")
        for k, cfg in da.SISTEMAS.items():
            if nome in (cfg["cobertura_camada"], cfg["camada"]):
                return k
        return None

    def __call__(self, url, *, params=None, headers=None):
        params = dict(params or {})
        self.pedidos.append((url, params))
        nome = str(params.get("typeNames") or "")
        k = self._sistema(params)
        area = self.areas.get(k) if k else None
        if params.get("resultType") == "hits":
            m = re.match(r"^(INTERSECTS|CONTAINS)\(geom,SRID=4674;(.+)\)$", params["CQL_FILTER"])
            env = shapely_wkt.loads(m.group(2))
            ok = area is not None and (area.intersects(env) if m.group(1) == "INTERSECTS" else area.contains(env))
            return Resposta(200, f'<wfs:FeatureCollection numberMatched="{int(ok)}" numberReturned="0"/>'.encode())
        if k and nome == da.SISTEMAS[k]["cobertura_camada"] and params.get("outputFormat") == "csv":
            if self.falhar_geometria:
                raise ConnectionError("simulado")
            return Resposta(200, f'FID,geom\r\nb.1,"{area.wkt}"\r\n'.encode())
        if k and params.get("outputFormat") == "csv":
            col = da.SISTEMAS[k]["coluna_geom"]
            return Resposta(200, f"FID,gid,classname,view_date,sensor,satellite,{col}\r\n".encode())
        campo = {"deter-amz:prodes_reference": ("end_date", "2025-07-31"), "deter-amz:updated_date": ("updated_date", "2026-09-04"),
                 "prodes-cerrado-nb:yearly_deforestation": ("year", "2025")}.get(nome)
        if campo is None and "deter_cerrado" in nome:
            campo = ("view_date", "2025-07-31" if "_hist" in str(params.get("CQL_FILTER")) else "2026-09-04")
        if campo:
            return Resposta(200, json.dumps({"features": [{"properties": {campo[0]: campo[1]}}]}).encode())
        raise LookupError(f"pedido inesperado: {url} {params}")


def _imovel_redondo():
    from shapely.geometry import Point
    car = Point(-47.0, -12.0).buffer(0.02, quad_segs=64)  # ~4,4 km: envoltória com 257 vértices vira retângulo mínimo
    env = shapely_wkt.loads(da.envelope_wkt(car))
    assert env.buffer(1e-9).covers(car) and da.area_ha(env) > 1.2 * da.area_ha(car), "cenário não reproduz a envoltória folgada"
    return car, env


def _deter_bioma(car, areas: dict, falhar_geometria: bool = False, agora: float | None = None) -> dict:
    da._CACHE.clear()
    da._COB_FALHA.clear()
    base = Path(tempfile.mkdtemp(prefix="f2gate-deter-"))
    try:
        s = ServidorBioma(areas, falhar_geometria=falhar_geometria)
        p = da.deter_alerts_payload(car, http_get=s, base_dir=base, agora=agora)
        return {**p, "_pedidos": s.pedidos}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def check_deter_divisa_dentro_do_imovel_total() -> None:
    car, env = _imovel_redondo()
    # (A) área monitorada contém o imóvel mas não a envoltória: cobertura total, sem nota de parte fora
    dentro = car.buffer(0.002)
    assert dentro.contains(car) and not dentro.contains(env) and dentro.intersects(env)
    p = _deter_bioma(car, {"cerrado": dentro, "amazonia": None})
    cer = p["systems"]["cerrado"]
    assert cer["coverage"] == "total" and cer["coverage_detail"]["metodo"] == "poligono_real", cer
    assert p["state"] == "not_found" and da.NOTA_COBERTURA_PARCIAL not in p["notes"], p
    conferir_textos(p)


def check_deter_divisa_fora_do_imovel_nao_coberto() -> None:
    from shapely.geometry import Point
    car, env = _imovel_redondo()
    # (B) área monitorada toca a envoltória mas não o imóvel: não coberto, nunca "nenhum alerta"
    canto = Point(env.exterior.coords[0]).buffer(0.003)
    assert canto.intersects(env) and not canto.intersects(car)
    p = _deter_bioma(car, {"cerrado": canto, "amazonia": None})
    assert p["state"] == "not_covered" and p["systems"]["cerrado"]["state"] == "not_covered", p
    assert "não cobre" in p["text"] and "Nenhum" not in p["text"], p["text"]
    assert not any(pr.get("bbox") for _, pr in p["_pedidos"]), "alertas consultados fora da área monitorada"
    conferir_textos(p)


def check_deter_divisa_no_meio_do_imovel_parcial() -> None:
    car, env = _imovel_redondo()
    # (C) divisa passa pelo meio do imóvel: parcial, com a nota
    minx, miny, maxx, maxy = car.bounds
    metade = box(minx - 1, miny - 1, (minx + maxx) / 2, maxy + 1)
    p = _deter_bioma(car, {"cerrado": metade, "amazonia": None})
    assert p["systems"]["cerrado"]["coverage"] == "parcial" and da.NOTA_COBERTURA_PARCIAL in p["notes"], p
    # "nenhum alerta" vale só para a parte monitorada, e o texto diz isso
    assert p["state"] == "not_found" and "sobre a parte monitorada do imóvel" in p["text"], p["text"]
    conferir_textos(p)
    # outro sistema cobre o imóvel inteiro: o imóvel todo foi olhado, sem ressalva
    p = _deter_bioma(car, {"cerrado": metade, "amazonia": box(minx - 1, miny - 1, maxx + 1, maxy + 1)})
    assert p["systems"]["amazonia"]["coverage"] == "total" and p["systems"]["cerrado"]["coverage"] == "parcial", p["systems"]
    assert "sobre o imóvel" in p["text"] and da.NOTA_COBERTURA_PARCIAL not in p["notes"], p
    conferir_textos(p)


def check_deter_divisa_sem_geometria_pendente() -> None:
    car, env = _imovel_redondo()
    dentro = car.buffer(0.002)
    p = _deter_bioma(car, {"cerrado": dentro, "amazonia": None}, falhar_geometria=True, agora=1_000_000.0)
    assert p["state"] == "pending" and p["text"] == "Consulta pendente." and p["complete"] is False, p
    # falha guardada: o relatório seguinte (1 min depois) não espera o mesmo download que caiu
    da._CACHE.clear()
    base = Path(tempfile.mkdtemp(prefix="f2gate-deter-"))
    try:
        s = ServidorBioma({"cerrado": dentro, "amazonia": None}, falhar_geometria=True)
        da._COB_FALHA.clear()
        da.deter_alerts_payload(car, http_get=s, base_dir=base, agora=1_000_000.0)
        n1 = sum(1 for _, pr in s.pedidos if pr.get("outputFormat") == "csv" and "biome_border" in str(pr.get("typeNames")))
        da.deter_alerts_payload(car, http_get=s, base_dir=base, agora=1_000_060.0)
        n2 = sum(1 for _, pr in s.pedidos if pr.get("outputFormat") == "csv" and "biome_border" in str(pr.get("typeNames")))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    assert n1 == 1 and n2 == 1, (n1, n2)


def _alerta_com_data(resp: dict, sistema: str, data_iso: str) -> None:
    resp[sistema]["alertas"] = [{**a, "data_imagem": data_iso} for a in resp[sistema]["alertas"]]


def _desde_coerente(p: dict, resp: dict, geom) -> None:
    """O "desde" do texto é o filtro: nenhum alerta dentro do imóvel depois dele ficou de fora."""
    m = re.search(r"desde (\d{2})/(\d{2})/(\d{4})", p["text"])
    if not m or p["state"] != "not_found":
        return
    desde = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    for s in resp.values():
        for a in s.get("alertas") or []:
            if da.area_ha(geom.intersection(a["geometria"])) >= da.AREA_MINIMA_LEITURA_HA:
                assert da._data(a["data_imagem"]) < desde, ("alerta dentro do imóvel depois do 'desde'", a["id"], p["text"])


def check_deter_corte_unico_entre_sistemas() -> None:
    geom = CAIXAS["controle_alerta_recente_cerrado"]
    amz = {"cobertura": "total", "cobertura_detalhe": {}, "alertas": [], "truncado": False, "corte": "2024-07-31",
           "ultima_imagem": "2026-09-04", "erros": []}
    # Cerrado com corte 31/07/2025, Amazônia Legal com 31/07/2024; alerta real do Cerrado com a data entre os dois
    resp = _respostas(geom)
    resp["cerrado"]["corte"] = "2025-07-31"
    _alerta_com_data(resp, "cerrado", "2025-03-10")
    resp["amazonia"] = dict(amz)
    p = da.deter_alerts_in_property(geom, resp)
    assert p["cutoff"] == "2024-07-31" and p["audit"]["cutoff_by_system"] == {"cerrado": "2025-07-31", "amazonia": "2024-07-31"}, p
    assert p["state"] == "found" and [e["first_seen"] for e in p["events"]] == ["2025-03-10"], p
    assert p["systems"]["cerrado"]["state"] == "found", p["systems"]
    conferir_textos(p)
    # mesmo alerta antes dos dois cortes: não é recente, e o "desde" do texto continua sendo o filtro
    resp = _respostas(geom)
    resp["cerrado"]["corte"] = "2025-07-31"
    _alerta_com_data(resp, "cerrado", "2024-07-15")
    resp["amazonia"] = dict(amz)
    p = da.deter_alerts_in_property(geom, resp)
    assert p["state"] == "not_found" and "desde 01/08/2024" in p["text"], p["text"]
    _desde_coerente(p, resp, geom)


def check_deter_classe_que_nao_e_desmatamento() -> None:
    geom = CAIXAS["controle_degradacao_amazonia"]
    da._CACHE.clear()
    b = da.deter_alerts_bundle(geom, http_get=Servidor(), base_dir=_BASE_DETER)
    p, comb = b["payload"], b["combiner"]
    assert p["systems"]["amazonia"]["coverage"] == "total" and p["state"] == "found", p
    assert not p["events"], ("degradação contada como desmatamento", p["events"])
    assert len(p["other_events"]) == 1 and p["other_events"][0]["classes"] == ["Degradação"], p["other_events"]
    assert p["other_events"][0]["alert_ids"] == ["deter_amz.11108_curr"], p["other_events"]
    assert da.NOTA_CREDITO not in p["notes"] and da.FRASE_PRODES not in p["notes"], p["notes"]
    assert any("não é multa nem auto de infração" in n for n in p["notes"]), p["notes"]
    assert "Nenhum alerta recente de desmatamento" in p["text"] and "outra mudança na vegetação (degradação)" in p["text"], p["text"]
    assert "prodes_overlap_ha" not in p["other_events"][0], p["other_events"][0]
    # o combinador (MapBiomas × DETER × PRODES) trata toda feição como desmatamento: degradação não entra
    assert comb["state"] == "answered" and comb["features"] == [] and comb["other_classes_excluded"] >= 1, comb
    conferir_textos(p)


def check_deter_contrato_combinador() -> None:
    geom = CAIXAS["controle_alerta_recente_cerrado"]
    da._CACHE.clear()
    b = da.deter_alerts_bundle(geom, http_get=Servidor(), base_dir=_BASE_DETER)
    comb = b["combiner"]
    assert comb["state"] == "answered" and len(comb["features"]) == 1, comb
    f = comb["features"][0]
    assert f["type"] == "Feature" and f["geometry"]["type"] in ("Polygon", "MultiPolygon"), f
    assert f["properties"]["gid"] == b["payload"]["events"][0]["alert_ids"][0] and da._data(f["properties"]["view_date"]), f
    assert comb["cutoff"] == b["payload"]["cutoff"] and comb["min_area_ha"] == 3.0 and comb["latest_image_date"], comb
    assert "_geom" not in json.dumps(b["payload"], default=str), "geometria interna vazou para o payload do relatório"
    json.dumps(b["payload"], ensure_ascii=False)  # payload.json do relatório tem de ser serializável
    fora = da.deter_alerts_bundle(CAIXAS["controle_fora_cobertura"], http_get=Servidor(), base_dir=_BASE_DETER)["combiner"]
    assert fora["state"] == "not_covered" and fora["features"] == [], fora
    for falha in ("outputFormat\": \"csv", "biome_border"):
        da._CACHE.clear()
        c = da.deter_alerts_bundle(geom, http_get=Servidor(falhar=(falha,)), base_dir=_BASE_DETER)["combiner"]
        assert c["state"] == "pending" and c["features"] == [], (falha, c)  # lista vazia só é resposta com "answered"
    # Com a frente MapBiomas presente, o combinador real tem de ler o DETER como respondido
    if importlib.util.find_spec("mapbiomas_alerta") is not None:
        import mapbiomas_alerta
        out = mapbiomas_alerta.combine_deforestation_alerts(mapping(geom), None, comb, None, comb["cutoff"])
        assert out["sources"]["inpe_deter"] == "answered", out
        print("INFO check_deter_contrato_combinador: combine_deforestation_alerts real conferido")


def check_conab_malha_falha_em_um_municipio() -> None:
    # O armazém de 31,1 km passa a declarar Belo Horizonte, e a malha de BH não responde:
    # sobra conferido só o de 47,1 km, que NÃO é o mais próximo.
    bruto = _bruto_fixture()
    col = ca.COLUNAS.index("cod_ibge")
    linhas = []
    for linha in bruto.split(b"\r\n"):
        campos = linha.split(b";")
        if len(campos) > col and campos[0].strip() == PROVA[0][0].encode():
            campos[col] = b"3106200"
        linhas.append(b";".join(campos))
    trocado = b"\r\n".join(linhas)
    with cache_temporario() as base, recorte_pequeno():
        p = conab(CAR_GEOM, Servidor(falhar=("3106200",), trocar={ca.URL_ARMAZENS: trocado}), base=base)
    assert p["state"] == "found" and p["complete"] is False and p["audit"]["location_unverified"] == 1, p
    assert [i["cda"] for i in p["items"]] == [PROVA[1][0]], p["items"]
    assert p["text"] == "Pelo menos 1 armazém cadastrado na CONAB em até 50 km do imóvel.", p["text"]
    assert "km do imóvel," not in p["text"] and "mais próximo" not in p["text"], p["text"]
    conferir_textos(p)


def check_conab_sincronizacao_nao_trava_relatorio() -> None:
    import threading

    t0 = 1_000_000.0
    vencido = t0 + ca.SYNC_TTL_S + 1
    with cache_temporario() as base, recorte_pequeno():
        assert ca.sync_conab_warehouses(base, http_get=Servidor(), agora=t0)["estado"] == "baixado"
        # (1) com cópia boa e fonte fora: uma tentativa, e a falha fica guardada por 15 min
        fora = Servidor(falhar=("portaldeinformacoes",))
        assert ca.sync_conab_warehouses(base, http_get=fora, agora=vencido)["estado"] == "falhou"
        p = ca.conab_warehouses_payload(CAR_GEOM, base_dir=base, http_get=fora, agora=vencido + 60)
        assert p["state"] == "found" and len(p["items"]) == 2 and p["audit"]["sync"] == "falhou_recentemente", p
        conab_pedidos = [x for x in fora.pedidos if x[0] == ca.URL_ARMAZENS]
        assert len(conab_pedidos) == 1, f"fonte fora consultada {len(conab_pedidos)} vezes em 1 min"
        assert ca.sync_conab_warehouses(base, http_get=fora, agora=vencido + ca.SYNC_NOVA_TENTATIVA_S + 1)["estado"] == "falhou"
        assert len([x for x in fora.pedidos if x[0] == ca.URL_ARMAZENS]) == 2, "nova tentativa depois do intervalo não aconteceu"
        # (2) outra linha de execução baixando: quem chega responde com a cópia boa, sem fila
        entrou, liberar = threading.Event(), threading.Event()

        def lento(url, *, params=None, headers=None):
            entrou.set()
            liberar.wait(10)
            raise ConnectionError("simulado")

        quando = vencido + 3 * ca.SYNC_NOVA_TENTATIVA_S
        ta = threading.Thread(target=lambda: ca.sync_conab_warehouses(base, http_get=lento, agora=quando), daemon=True)
        ta.start()
        try:
            assert entrou.wait(5), "download lento não começou"
            saida: dict = {}
            tb = threading.Thread(target=lambda: saida.update(r=ca.sync_conab_warehouses(base, http_get=lento, agora=quando)), daemon=True)
            tb.start()
            tb.join(2)
            assert not tb.is_alive() and saida["r"]["estado"] == "em_andamento" and saida["r"]["tem_base"], "relatório ficou na fila do download"
        finally:
            liberar.set()
            ta.join(10)
    # (3) sem cópia nenhuma e fonte fora: pendente, e a falha também é guardada (1 min)
    with cache_temporario() as base, recorte_pequeno():
        fora = Servidor(falhar=("portaldeinformacoes",))
        for dt in (0, 30):
            p = ca.conab_warehouses_payload(CAR_GEOM, base_dir=base, http_get=fora, agora=t0 + dt)
            assert p["state"] == "pending" and p["text"] == "Consulta pendente.", p
        assert len([x for x in fora.pedidos if x[0] == ca.URL_ARMAZENS]) == 1, fora.pedidos


def check_conab_malha_ibge_fora_nao_repete() -> None:
    t0 = 1_000_000.0
    # malha do IBGE fora: um relatório seguido não espera de novo o mesmo tempo limite
    with cache_temporario() as base, recorte_pequeno():
        s = Servidor(falhar=("servicodados.ibge.gov.br",))
        for dt in (0, 60):
            p = ca.conab_warehouses_payload(CAR_GEOM, base_dir=base, http_get=s, agora=t0 + dt)
            assert p["state"] == "pending" and p["text"] == "Consulta pendente.", p
        malhas = [x for x in s.pedidos if "servicodados.ibge.gov.br" in x[0]]
        assert len(malhas) == 1, f"malha que caiu pedida {len(malhas)} vezes"


def check_deter_geometria_da_area_monitorada_enxuta() -> None:
    """Leitor anel por anel (pico de memória 127 MB contra 429 MB) dá a MESMA geometria do GEOS."""
    import shapely

    casos = [
        "MULTIPOLYGON (((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 2 4, 4 4, 4 2, 2 2), (6 6, 6 8, 8 8, 8 6, 6 6)), "
        "((20 20, 30 20, 30 30, 20 20)), ((-47.123456789012345 -12.5, -47.1 -12.5, -47.1 -12.4, -47.123456789012345 -12.5)))",
        "MULTIPOLYGON(((0 0,1 0,1 1,0 0)),((5 5,6 5,6 6,5 5),(5.2 5.1,5.8 5.1,5.8 5.7,5.2 5.1)))",
        "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (1 1, 1 2, 2 2, 1 1))",
        "POLYGON Z ((0 0 1, 1 0 1, 1 1 1, 0 0 1))",  # forma não prevista: cai no leitor do GEOS
    ]
    def _csv(*linhas: str, fim: str = "\r\n") -> bytes:
        return ("\r\n".join(linhas) + fim).encode("utf-8")

    for wkt in casos:
        g = da.parse_coverage_csv(_csv("FID,nome,geom", f'b.1,"Amazônia Legal, teste","{wkt}"'))
        ref = shapely.from_wkt(wkt)
        ref = ref if ref.is_valid else shapely.make_valid(ref)
        assert g.equals_exact(ref, 0) and g.geom_type == ref.geom_type, (wkt, g.wkt, ref.wkt)
    duas = _csv("FID,geom", 'b.1,"POLYGON ((0 0, 1 0, 1 1, 0 0))"', 'b.2,"POLYGON ((1 0, 2 0, 2 1, 1 0))"')
    assert abs(da.parse_coverage_csv(duas).area - 1.0) < 1e-12
    for ruim in (_csv("FID,geom", 'b.1,"MULTIPOLYGON (((0 0, 1 0, 1 1', fim=""), _csv("FID,geom", "b.1,"),
                 _csv("FID,nome", "b.1,x"), _csv("FID,geom", 'b.1,"POLYGON ((0 0, 1 0, 1 1, 0 0))"', "b.2,")):
        try:
            da.parse_coverage_csv(ruim)
        except ValueError:
            continue
        raise AssertionError(f"CSV incompleto aceito como área monitorada: {ruim!r}")


def _aneis_viram_poligonos(wkt, ini=0, fim=None):
    """Defeito plausível do leitor enxuto: buraco lido como polígono à parte."""
    import numpy as np
    import shapely

    fim = len(wkt) if fim is None else fim
    polys = []
    for m in da._ANEL_WKT.finditer(wkt, ini, fim):
        c = np.fromstring(m.group(1).replace(b",", b" ").decode("ascii"), dtype=np.float64, sep=" ")
        polys.append(shapely.polygons(shapely.linearrings(c.reshape(-1, 2))))
    return shapely.multipolygons(polys)


_BASE_DETER = Path(tempfile.mkdtemp(prefix="f2gate-deter-base-"))
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


def _cobertura_so_envoltoria(sistema, geom, http_get, *, base_dir=None, agora=None, detalhe=None):
    """Defeito revisado: a envoltória decidia sozinha (cruzou a divisa = parcial)."""
    url = da.URL_TB.format(ws=da.SISTEMAS[sistema]["cobertura_ws"])
    if da._number_matched(da._get_ok(http_get, url, da.params_coverage(sistema, geom, "INTERSECTS")).text) == 0:
        return "nenhuma"
    if da._number_matched(da._get_ok(http_get, url, da.params_coverage(sistema, geom, "CONTAINS")).text) > 0:
        return "total"
    return "parcial"


def _cobertura_falha_vira_nenhuma(*a, **k):
    try:
        return ORIGINAIS["query_coverage"](*a, **k)
    except Exception:
        return "nenhuma"


def _corte_por_sistema(geom, respostas, prodes=None):
    """Defeito revisado: cada sistema descartava alertas pelo próprio corte, e o texto usava o mais antigo."""
    r = {}
    for k, v in respostas.items():
        v = dict(v)
        c = da._data(v.get("corte"))
        if c and v.get("alertas"):
            v["alertas"] = [a for a in v["alertas"] if (da._data(a["data_imagem"]) or date.max) > c]
        r[k] = v
    return ORIGINAIS["deter_alerts_in_property"](geom, r, prodes)


def _parcial_sem_ressalva(geom, respostas, prodes=None):
    """Defeito revisado: imóvel só em parte monitorado lia 'nenhum alerta sobre o imóvel'."""
    p = ORIGINAIS["deter_alerts_in_property"](geom, respostas, prodes)
    p["text"] = p["text"].replace("sobre a parte monitorada do imóvel", "sobre o imóvel")
    return p


def _combinador_falha_vira_vazio(geom, respostas):
    out = ORIGINAIS["deter_combiner_input"](geom, respostas)
    if out["state"] == "pending":
        out["state"] = "answered"
    return out


def _mais_proximo_sem_conferencia(resultado, meta, *, pendente=False):
    """Defeito revisado: com armazém sem conferência, o texto ainda afirmava o mais próximo."""
    if resultado and resultado.get("sem_conferencia") and resultado.get("itens"):
        r = {**resultado, "excluidos_localizacao": [*resultado["excluidos_localizacao"], *resultado["sem_conferencia"]],
             "sem_conferencia": []}
        out = ORIGINAIS["build_warehouses_payload"](r, meta, pendente=pendente)
        out["audit"].update(location_unverified=len(resultado["sem_conferencia"]),
                            location_mismatch=len(resultado["excluidos_localizacao"]))
        return out
    return ORIGINAIS["build_warehouses_payload"](resultado, meta, pendente=pendente)


class _TravaQueEspera:
    """Defeito revisado: lock global segurado durante a rede; quem chega espera na fila."""

    def __init__(self):
        import threading

        self._l = threading.Lock()

    def acquire(self, blocking=True, timeout=-1):
        return self._l.acquire()

    def release(self):
        self._l.release()


ORIGINAIS = {"params_coverage": da.params_coverage, "deter_alerts_in_property": da.deter_alerts_in_property,
             "query_coverage": da.query_coverage, "deter_combiner_input": da.deter_combiner_input,
             "build_warehouses_payload": ca.build_warehouses_payload}
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
    # pós-revisão
    ("envoltória decide a cobertura na divisa (imóvel dentro)", da, "query_coverage", _cobertura_so_envoltoria,
     check_deter_divisa_dentro_do_imovel_total),
    ("envoltória decide a cobertura na divisa (imóvel fora)", da, "query_coverage", _cobertura_so_envoltoria,
     check_deter_divisa_fora_do_imovel_nao_coberto),
    ("'nenhum alerta sobre o imóvel' com o imóvel só em parte monitorado", da, "deter_alerts_in_property", _parcial_sem_ressalva,
     check_deter_divisa_no_meio_do_imovel_parcial),
    ("geometria da área monitorada que falhou vira 'não coberto'", da, "query_coverage", _cobertura_falha_vira_nenhuma,
     check_deter_divisa_sem_geometria_pendente),
    ("falha da geometria da área monitorada não guardada", da, "COBERTURA_NOVA_TENTATIVA_S", 0, check_deter_divisa_sem_geometria_pendente),
    ("corte de cada sistema e texto com o mais antigo", da, "deter_alerts_in_property", _corte_por_sistema, check_deter_corte_unico_entre_sistemas),
    ("degradação contada como desmatamento", da, "_e_desmatamento", lambda classe: True, check_deter_classe_que_nao_e_desmatamento),
    ("combinador recebe falha como lista vazia respondida", da, "deter_combiner_input", _combinador_falha_vira_vazio, check_deter_contrato_combinador),
    ("texto afirma o mais próximo com armazém sem conferência", ca, "build_warehouses_payload", _mais_proximo_sem_conferencia,
     check_conab_malha_falha_em_um_municipio),
    ("falha da CONAB não guardada", ca, "falha_recente", lambda *a, **k: False, check_conab_sincronizacao_nao_trava_relatorio),
    ("falha da malha do IBGE não guardada", ca, "falha_recente", lambda *a, **k: False, check_conab_malha_ibge_fora_nao_repete),
    ("trava da sincronização espera o download", ca, "_SYNC_LOCK", _TravaQueEspera(), check_conab_sincronizacao_nao_trava_relatorio),
    ("leitor enxuto lê buraco como polígono", da, "poligonal_de_wkt", _aneis_viram_poligonos, check_deter_geometria_da_area_monitorada_enxuta),
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
    shutil.rmtree(_BASE_DETER, ignore_errors=True)
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

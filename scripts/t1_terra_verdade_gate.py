"""Gate T1 · terra-verdade: o relatório não diz relevo, solo, chuva e crédito de um jeito errado.

Roda sem rede (conexão e curl recusados). As regras olham a cadeia efetiva do relatório (a mesma
lista de módulos que o ``sitecustomize`` carrega) e o PDF gerado por ela, não só funções soltas.

Regras
  relevo_em_porcentagem          classes de relevo da Embrapa em % (plano 0–3 ... escarpado > 75), 25° só
                                 como marco legal; plano de 2,5° (4,4 %) é "suave ondulado", nunca "plano".
  sem_maximo_de_percentil        nenhum "máximo" de inclinação (o antigo era o percentil 99,5).
  soilgrids_power_fora           SoilGrids fora da cadeia do relatório; a comparação da chuva recente da
                                 NASA POWER com o normal não vira adjetivo no portal.
  caminho_nacional_sem_filtro_mg solo/aptidão/erodibilidade pela base nacional em qualquer UF, ligada na
                                 análise do servidor e no resumo da tela; sem caixa de MG no caminho.
  mcr_datas_da_5303              Res. CMN 5.303/2026: 04/01/2027 (> 15 MF), 01/07/2027 (> 4 a 15),
                                 03/01/2028 (até 4); condição com documento, nunca vedação.
  relatorio_real                 PDF da cadeia real (Curvelo/MG por fixture): relevo em %, solo nacional com a
                                 escala, erodibilidade com o conceito, sem "Risco potencial de erosão: muito
                                 baixo", sem SoilGrids/IDE-Sisema, sem adjetivo da chuva, crédito com a data
                                 do porte, cobertura do relevo medida; lint R1 limpo na página de solo.
  ci_roda_o_gate                 o quality-gate roda este gate.

Controles positivos (sempre rodam): cada mutação é aplicada no ARQUIVO, num processo novo que importa a
cópia mutada antes da original, e tem de reprovar pela regra dela.

  PYTHONPATH=. python scripts/t1_terra_verdade_gate.py
"""
from __future__ import annotations

import ast
import copy
import importlib
import importlib.util
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # append: a cópia mutada de um controle positivo vem antes no PYTHONPATH
os.environ.setdefault("RX_RELEASE", "OFF")
os.environ["RX_INCRA_ACERVO_ENABLED"] = "off"

CAR_CODE = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
FIX = ROOT / "scripts" / "fixtures" / "t1_terra" / "solo_nacional_curvelo.json"
WORKFLOW = Path(os.environ.get("T1_WORKFLOW") or (ROOT / ".github" / "workflows" / "quality-gate.yml"))
OLD_MCR_TEXTS = ("MCR 2-9: verificação de supressão de vegetação nativa após 31/07/2019.",
                 "MCR 2-9: verificar supressão de vegetação nativa após 31/07/2019.")
EMBRAPA = [("plano", "0–3%"), ("suave ondulado", "3–8%"), ("ondulado", "8–20%"), ("forte ondulado", "20–45%"),
           ("montanhoso", "45–75%"), ("escarpado", "acima de 75%")]


# ------------------------------------------------------------------ sem rede
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


def load_runtime():
    """Importa a cadeia do relatório na ordem do sitecustomize (a lista é lida do próprio arquivo)."""
    import report_api  # noqa: F401

    tree = ast.parse((ROOT / "sitecustomize.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_load_report_after_report_api")
    names = [alias.name for node in ast.walk(fn) if isinstance(node, ast.Import) for alias in node.names]
    assert "report_extras_perf_v30" in names and "report_v20_patch" in names, names
    for name in names:
        importlib.import_module(name)
    return names


# ------------------------------------------------------------------ fixtures
def curvelo_car():
    fx = json.loads((ROOT / "tests" / "fixtures" / "f2_prodes_leitura" / "curvelo_mg.json").read_text(encoding="utf-8"))
    return fx["car"]


def pa_geometry():
    return {"type": "Polygon", "coordinates": [[[-55.0928, -7.0138], [-55.0724, -7.0138], [-55.0724, -6.9710], [-55.0928, -6.9710], [-55.0928, -7.0138]]]}


def split_features(geometry, pieces):
    """Faixas verticais da caixa do imóvel: [(fração da largura, propriedades)]."""
    from shapely.geometry import box, mapping, shape

    minx, miny, maxx, maxy = shape(geometry).buffer(0.01).bounds
    x, feats = minx, []
    for frac, props in pieces:
        nx = x + (maxx - minx) * frac
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(box(x, miny, nx, maxy))})
        x = nx
    return feats


def fake_get_for(geometry, aptitude_units=("PVe34", "CXbd69")):
    fx = json.loads(FIX.read_text(encoding="utf-8"))
    layers = {
        "BDIA:pedo_area": [(0.57, fx["pedo"]["PVe34"]), (0.43, fx["pedo"]["CXbd69"])],
        "geonode:aptidao_agr_bra": [(0.57, fx["apt"][aptitude_units[0]]), (0.43, fx["apt"][aptitude_units[-1]])],
        "geonode:bra_erodibilidade_2024_sirgas2000": [(0.57, fx["erod"]["PVe34"]), (0.43, fx["erod"]["CXbd69"])],
    }

    def get(url, params):
        return {"type": "FeatureCollection", "features": split_features(geometry, layers[params["typeNames"]])}
    return get


def terrain_fixture(grades_pct=(5.0, 12.0), deg=None):
    """Resultado no formato do terrain_srtm, calculado pelo próprio _slope_stats sobre um terreno sintético."""
    import numpy as np
    import terrain_srtm

    res = 1 / 3600
    lat = -18.89
    dx = res * 111_320.0 * math.cos(math.radians(lat))
    cols = np.arange(60, dtype="float64")
    rows = []
    for i in range(40):
        grade = (math.tan(math.radians(deg)) * 100 if deg is not None else (grades_pct[0] if i < 28 else grades_pct[1]))
        rows.append(600.0 + cols * dx * grade / 100.0)
    dem = np.vstack(rows)
    mask = np.ones(dem.shape, dtype=bool)
    mask[26:30, :] = False  # a borda entre os dois declives não entra na estatística
    st = terrain_srtm._slope_stats(dem, mask, lat, res)
    return {"ok": True, "source": "fixture", "elevation_min_m": 641.0, "elevation_median_m": 657.0, "elevation_max_m": 668.0, **st}


def texture_fixture():
    import numpy as np
    import solo_nacional_t1 as S

    clay = np.array([44, 46, 49, 50, 53, 48, 47, 51, 0], dtype="float64")
    sand = np.array([18, 17, 18, 19, 16, 18, 20, 17, 0], dtype="float64")
    silt = 100 - clay - sand
    silt[-1] = 0
    return {"ok": True, "source": S.SOURCE_TEXTURA, "version": "T1", "ms": 5, **S.texture_summary(clay, sand, silt)}


# ------------------------------------------------------------------ regras
def rule_relevo_em_porcentagem():
    import terra_verdade_t1 as T
    import terrain_srtm

    labels = [(r[0], r[1]) for r in terrain_srtm.RELIEF_CLASSES_PCT]
    assert labels == EMBRAPA, f"classes de relevo fora da Embrapa: {labels}"
    gentle = terrain_fixture(deg=2.5)
    shares = {r["class"]: r["share_pct"] for r in gentle["slope_classes"]}
    assert shares.get("suave ondulado", 0) > 99 and shares.get("plano", 0) < 1, f"2,5° (4,4 %) tem de ser suave ondulado, não plano: {shares}"
    assert gentle.get("slope_unit") == "%" and 4.2 <= gentle["slope_median_pct"] <= 4.6, f"inclinação não está em %: {gentle.get('slope_median_pct')}"
    steep = terrain_fixture(deg=30)
    shares = {r["class"]: r["share_pct"] for r in steep["slope_classes"]}
    assert shares.get("montanhoso", 0) > 99 and steep["slope_ge_25deg_share_pct"] > 99, f"30° (57,7 %) é montanhoso e passa do marco legal: {shares} {steep.get('slope_ge_25deg_share_pct')}"
    mixed = terrain_fixture()
    for r in mixed["slope_classes"]:
        assert "°" not in r["class"] + r["range"], f"classe de relevo em graus: {r}"
    kpis = T.relief_kpis(mixed)
    text = json.dumps(kpis, ensure_ascii=False)
    assert "°" not in text and "Declive" not in text and "Plano ou suave ondulado" in text, f"KPI de relevo errado: {text}"
    rows = T.relief_rows(mixed)
    assert not any(r[0] == T.LEGAL_ROW_LABEL for r in rows), "marco legal de 25° apareceu sem área acima de 25°"
    assert any(r[0] == T.LEGAL_ROW_LABEL for r in T.relief_rows(steep)), "marco legal de 25° sumiu com área acima de 25°"


def rule_sem_maximo_de_percentil():
    import terra_verdade_t1 as T

    t = terrain_fixture()
    keys = [k for k in t if k.startswith("slope") and "max" in k]
    assert not keys, f"inclinação com 'máximo': {keys}"
    blob = json.dumps([T.relief_kpis(t), T.relief_rows(t), T.relief_check(t), T.relief_source(t)], ensure_ascii=False)
    assert not re.search(r"(?i)m[áa]xim", blob), f"texto de relevo fala em máximo: {blob[:300]}"


def _code_names(code) -> set[str]:
    names = set(code.co_names)
    for const in code.co_consts:
        if hasattr(const, "co_names"):
            names |= _code_names(const)
    return names


def rule_soilgrids_power_fora():
    offline()
    load_runtime()
    import live_report_adapter_v13 as v13
    import live_report_adapter_v17 as v17
    import terra_verdade_t1 as T

    for label, fn in (("v13._extras", v13._extras), ("v17._extras_v17", v17._extras_v17)):
        names = _code_names(fn.__code__)
        assert "query_soilgrids_wcs" not in names, f"SoilGrids ainda na cadeia do relatório ({label} = {fn.__module__}.{fn.__name__})"
        assert "query_soil_texture" in names, f"textura do MapBiomas Solo fora da cadeia ({label} = {fn.__module__}.{fn.__name__})"
    origin = importlib.util.find_spec("portal_property_tabs").origin  # cópia que o Python importaria
    tree = ast.parse(Path(origin).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "climate_detail")
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    drought = next(v for k, v in zip(ret.value.keys, ret.value.values) if isinstance(k, ast.Constant) and k.value == "drought")
    assert isinstance(drought, ast.Call) and getattr(drought.func, "id", "") == "withhold_rain_comparison", "portal: chuva recente da POWER comparada ao normal sem retenção"
    held = T.withhold_rain_comparison({"status": "found", "state": "acima do normal", "summary": "Acima do normal", "rain_sum_mm": 33.0, "method": "ratio_climatology"})
    assert held["status"] != "found" and not held.get("state") and not held.get("summary") and held.get("rain_sum_mm") == 33.0, held


def rule_caminho_nacional_sem_filtro_mg():
    import report_api
    import solo_nacional_t1 as S

    geom = pa_geometry()
    solo = S.query_solo_nacional(geom, get=fake_get_for(geom, aptitude_units=("Naodesm",)))
    assert (solo.get("pedologia") or {}).get("state") == "found", f"imóvel fora de MG sem solo nacional: {solo.get('pedologia') or solo}"
    reading = S.soil_reading(solo, None)
    assert any(r[0] == "Tipo de solo no mapa oficial" for r in reading["soil_rows"]), reading["soil_rows"]
    assert not reading["aptitude_rows"], f"'Área não desmatada' do mapa de aptidão virou classe: {reading['aptitude_rows']}"
    src = S.__file__ and Path(S.__file__).read_text(encoding="utf-8")
    assert "MG_BBOX" not in src and "_intersects_mg" not in src, "caixa de MG no caminho nacional"
    api_src = Path(report_api.__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(api_src).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_analyze_uncached")
    called = {getattr(a, "id", None) for n in ast.walk(fn) if isinstance(n, ast.Call) for a in n.args}
    assert "query_solo_nacional" in called, "análise do servidor não chama a base nacional de solo"
    import terra_verdade_t1 as T

    not_applicable = {"ok": False, "state": "not_applicable", "detail": "IDE-Sisema é fonte estadual de Minas Gerais; não aplicável a este imóvel."}
    payload = {"productive": {}, "sources": [{"name": "IDE-Sisema / Uso e Cobertura", "status": "INDISPONÍVEL"},
                                             {"name": "IDE-Sisema / Recomposição de Reserva Legal declarada", "status": "INDISPONÍVEL"}]}
    out = T.apply_payload(payload, {"terra_nacional": solo, "ide_layers": {"soil": dict(not_applicable), "landcover_centro_norte": {"ok": False, "state": "superseded"}}})
    left = [s["name"] for s in out["sources"] if str(s.get("name", "")).startswith("IDE-Sisema")]
    assert not left, f"camada estadual de MG como consulta pendente num imóvel fora de MG: {left}"
    summary = report_api._report_summary({"car": {"ok": True, "properties": {}}, "terra_nacional": solo})
    assert (summary.get("terra_nacional") or {}).get("texts", {}).get("solo"), f"resumo da tela sem o solo nacional: {summary.get('terra_nacional')}"


def rule_mcr_datas_da_5303():
    from datetime import date

    import mcr_regra_t1 as M
    import prodes_reading_f2 as P

    today = date(2026, 9, 15)
    cases = {0.37: "03/01/2028", 4.0: "03/01/2028", 4.01: "01/07/2027", 15.0: "01/07/2027", 15.5: "04/01/2027", 23.97: "04/01/2027"}
    for mf, expected in cases.items():
        text = M.basis_text(mf, "IRU", today)
        sentence = M.property_sentence(mf, "IRU", today)
        assert expected in sentence and "começa em" in sentence, f"{mf} MF: data errada na frase do porte: {sentence!r}"
        for d in ("04/01/2027", "01/07/2027", "03/01/2028"):
            assert d in text, f"calendário da 5.303 incompleto: {text!r}"
        assert "5.303" in text and "31/07/2019" in text and "condicionado" in text and "não é proibição automática" in text, f"regra do MCR incompleta: {text!r}"
        assert not re.search(r"(?i)veda[çc][ãa]o|vedad", text), f"MCR dito como vedação: {text!r}"
    assert "03/01/2028" in M.property_sentence(40, "AST", today), "assentamento/comunidade tradicional (17-A) usa a data de até 4 MF"
    assert "vale desde" in M.property_sentence(0.37, "IRU", date(2028, 2, 1)), "data já passada dita como futura"
    assert M.property_sentence(None, "IRU", today) == "", "porte desconhecido virou data"
    car = curvelo_car()
    result = {"car": {"ok": True, "properties": dict(car["properties"]), "geometry": car["geometry"]}, "prodes": {"ok": False, "detail": "gate"}}
    rows = dict((r[0], r[1]) for r in P.prodes_reading_payload(result)["rows"])
    basis = rows.get("Base regulatória", "")
    assert "03/01/2028" in basis and "0,37 módulo fiscal" in basis, f"linha do relatório sem a data do porte: {basis!r}"
    offenders = []
    for py in ROOT.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if any(old in text for old in OLD_MCR_TEXTS):
            offenders.append(py.name)
    assert not offenders, f"texto antigo do MCR ainda no sistema: {offenders}"


def render_real_chain():
    """PDF da cadeia real do relatório com as fontes de rede trocadas por fixture."""
    offline()
    load_runtime()
    spec = importlib.util.spec_from_file_location("f2_v20_gate_helpers", ROOT / "scripts" / "f2_relatorio_v20_gate.py")
    f2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f2)
    spec = importlib.util.spec_from_file_location("f2_clima_gate_helpers", ROOT / "scripts" / "f2_clima_landsat_gate.py")
    clima = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = clima
    spec.loader.exec_module(clima)
    import live_report_adapter_v13 as v13
    import live_report_adapter_v17 as v17
    import report_api
    import solo_nacional_t1 as S

    result = f2.curvelo_result()
    geom = result["car"]["geometry"]
    result["terra_nacional"] = S.query_solo_nacional(geom, get=fake_get_for(geom))
    result["climate_nasa"] = clima.load("production_climate_detail_curvelo_30d.json")["recent"]
    ide_hit = lambda props: {"ok": True, "layer": "IDE:gate", "feature_count_bbox": 1, "exact_count": 1,  # noqa: E731
                             "samples": [{"properties": props, "intersection_area_ha": 14.8, "intersection_pct_car": 100.0}]}
    result["ide_layers"] = {"soil": ide_hit({"legenda": "Cambissolo háplico Tb distrófico"}),
                            "aptitude": ide_hit({"legenda": "4(p) Aptidão RESTRITA para pastagem plantada"}),
                            "erosion": ide_hit({"indicador": "Muito baixo"}),
                            "slope": {"ok": False, "state": "superseded"}}
    clim = clima.climatology_via_module()
    terrain = terrain_fixture()
    patches = f2.sources_patches("answered") + [
        patch.object(v17, "query_terrain_srtm", lambda g: copy.deepcopy(terrain)),
        patch.object(v13, "query_soil_texture", lambda g: texture_fixture()),
        patch.object(v13, "query_climatology_nasa", lambda g: copy.deepcopy(clim)),
    ]
    for p in patches:
        p.start()
    try:
        meta = report_api.generate_live_report(result, CAR_CODE)
    finally:
        for p in reversed(patches):
            p.stop()
    from pypdf import PdfReader

    pages = [re.sub(r"\s+", " ", page.extract_text() or "") for page in PdfReader(meta["pdf_path"]).pages]
    payload = json.loads(Path(meta["payload_path"]).read_text(encoding="utf-8"))
    return pages, payload


def rule_relatorio_real():
    pages, payload = render_real_chain()
    text = " ".join(pages)
    # (a) relevo em %
    assert re.search(r"(?i)plano ou suave ondulado", text) and "suave ondulado (3–8%)" in text, "(a) relevo sem as classes da Embrapa em % no PDF"
    assert "Declive" not in text and not re.search(r"\d° mediana|P90 \d+[,.]\d+°", text), "(a) relevo em graus no PDF"
    # (b) sem máximo
    assert not re.search(r"(?i)m[áa]xim\w*[^.]{0,40}(inclina|decliv|relevo)|(inclina|decliv|relevo)[^.]{0,40}m[áa]xim", text), "(b) inclinação com 'máximo' no PDF"
    # (c) solo nacional, sem SoilGrids/IDE-Sisema
    assert "SoilGrids" not in text, "(c) SoilGrids no PDF"
    assert not re.search(r"IDE-Sisema / (Mapa de Solos|Aptidão|Risco Potencial)", text), "(c) camada estadual de solo no PDF"
    assert "Tipo de solo no mapa oficial" in text and "1:250.000" in text and "Textura de 0 a 30 cm" in text, "(c) solo nacional ou textura fora do PDF"
    # (d) erosão com o conceito certo
    assert "Risco potencial de erosão" not in text and not re.search(r"(?i)muito baixo\b", text), "(d) erosão 'muito baixo' sozinha no PDF"
    assert "Erodibilidade" in text and "fragilidade do próprio solo" in text, "(d) erodibilidade sem o conceito no PDF"
    # (e) chuva recente sem adjetivo; os milímetros ficam
    assert "Chuva recente comparada" not in text and not re.search(r"(?i)(acima|abaixo) do normal|da média da época|dentro do normal", text), "(e) chuva da POWER comparada ao normal no PDF"
    assert "31,46 mm" in text, "(e) a chuva medida sumiu do PDF"
    assert ((payload.get("water") or {}).get("drought_screening") or {}).get("status") != "found", "(e) payload guarda a comparação como resposta"
    # (f) crédito com a data do porte
    m = re.search(r"Base regulatória (.{0,1500}?)(Como medimos|$)", text)
    assert m and "03/01/2028" in m.group(1) and "condicionado" in m.group(1), f"(f) base regulatória sem a data do porte: {m.group(1)[:300] if m else None}"
    assert not re.search(r"(?i)veda[çc][ãa]o", text), "(f) crédito dito como vedação"
    # (g) cobertura: relevo medido, solo e aptidão pela base nacional
    cov = (payload.get("conclusion") or {}).get("coverage") or {}
    for key in ("declividade", "solo", "aptidão"):
        assert key in (cov.get("consulted_core") or []), f"(g) cobertura diz '{key}' indisponível com a fonte respondendo: {cov}"
    # (h) lint R1 na página de solo
    sys.path.insert(0, str(ROOT / "scripts"))
    from r1_report_ptbr_gate import lint_text

    soil_page = next((p for p in pages if "Solo, aptidão, relevo e uso da terra" in p), "")
    assert soil_page, "(h) página de solo não encontrada"
    hits = lint_text(soil_page)
    assert not hits, f"(h) lint R1 na página de solo: {hits}"
    # (i) uso e cobertura: camada substituída pelo MapBiomas não aparece como indisponível ao lado da resposta
    m = re.search(r"Uso e cobertura (.*)", soil_page)
    assert m and "MapBiomas" in m.group(1) and not re.search(r"(INDISPONÍVEL|NÃO EXECUTADA|CONSULTA PENDENTE) -", m.group(1)), f"(i) uso e cobertura com linha vazia de fonte substituída: {m.group(1)[-200:] if m else None}"


def rule_ci_roda_o_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "PYTHONPATH=. python scripts/t1_terra_verdade_gate.py" in text, "quality-gate não roda o gate T1"


RULES = {
    "relevo_em_porcentagem": rule_relevo_em_porcentagem,
    "sem_maximo_de_percentil": rule_sem_maximo_de_percentil,
    "soilgrids_power_fora": rule_soilgrids_power_fora,
    "caminho_nacional_sem_filtro_mg": rule_caminho_nacional_sem_filtro_mg,
    "mcr_datas_da_5303": rule_mcr_datas_da_5303,
    "relatorio_real": rule_relatorio_real,
    "ci_roda_o_gate": rule_ci_roda_o_gate,
}

# (regra, arquivo, trecho, troca, motivo esperado na reprovação)
MUTANTS = [
    ("relevo_em_porcentagem", "terrain_srtm.py", "slope_pct=np.hypot(gx,gy)*100.0", "slope_pct=np.degrees(np.arctan(np.hypot(gx,gy)))", "suave ondulado, não plano"),
    ("relevo_em_porcentagem", "terra_verdade_t1.py", "\"value\": f\"{num(r['median_pct'], 1)}% mediana\"", "\"value\": f\"{num(r['median_pct'], 1)}° mediana\"", "KPI de relevo errado"),
    ("sem_maximo_de_percentil", "terrain_srtm.py", "        'slope_classes':rows,\n        'slope_ge_25deg",
     "        'slope_max_pct':round(float(np.percentile(vals,99.5)),2),\n        'slope_classes':rows,\n        'slope_ge_25deg", "com 'máximo'"),
    ("soilgrids_power_fora", "report_extras_perf_v30.py", "asyncio.to_thread(v13.query_soil_texture,geom),16)", "asyncio.to_thread(v13.query_soilgrids_wcs,geom),16)", "SoilGrids ainda na cadeia"),
    ("soilgrids_power_fora", "portal_property_tabs.py", "'drought':withhold_rain_comparison(build_drought_screening(recent,clim))", "'drought':build_drought_screening(recent,clim)", "sem retenção"),
    ("caminho_nacional_sem_filtro_mg", "solo_nacional_t1.py", "    t0 = time.monotonic()\n    with ThreadPoolExecutor(max_workers=len(LAYERS)",
     "    from shapely.geometry import shape as _shape\n    _w, _s, _e, _n = _shape(car_geometry).bounds\n    if _e < -51.2 or _w > -39.7 or _n < -23.0 or _s > -14.1:\n        return {\"ok\": False, \"state\": \"not_applicable\"}\n    t0 = time.monotonic()\n    with ThreadPoolExecutor(max_workers=len(LAYERS)",
     "fora de MG sem solo nacional"),
    ("caminho_nacional_sem_filtro_mg", "report_api.py", "        _safe_thread('terra_nacional',query_solo_nacional,geometry,source='IBGE BDiA + Embrapa GeoInfo'),\n",
     "        _safe_async('terra_nacional',asyncio.sleep(0,{'ok':False}),'IBGE BDiA + Embrapa GeoInfo'),\n", "não chama a base nacional"),
    ("caminho_nacional_sem_filtro_mg", "terra_verdade_t1.py", "    if _state_layer_only_elsewhere(result):", "    if False:", "camada estadual de MG como consulta pendente"),
    ("mcr_datas_da_5303", "mcr_regra_t1.py", "(float(\"-inf\"), date(2028, 1, 3), \"até 4 módulos fiscais\")", "(float(\"-inf\"), date(2027, 1, 4), \"até 4 módulos fiscais\")", "data errada"),
    ("mcr_datas_da_5303", "mcr_regra_t1.py", "; não é proibição automática.", "; é vedação ao crédito.", "regra do MCR incompleta"),
    ("mcr_datas_da_5303", "prodes_reading_f2.py", "\"rows\": _rows(reading, texts, mcr_regra_t1.basis_text(props.get(\"m_fiscal\"), props.get(\"tipo_imovel\"))),", "\"rows\": _rows(reading, texts),", "sem a data do porte"),
    ("relatorio_real", "live_report_adapter_v20.py", "        payload = terra_verdade_t1.apply_payload(payload, ctx.get(\"result\") or {})\n", "", "(a)"),
    ("relatorio_real", "terra_verdade_t1.py", "    prod[\"erosion_rows\"] = []", "    pass", "(d)"),
    ("relatorio_real", "terra_verdade_t1.py", "        prod[\"landcover_rows\"] = [r for r in landcover", "        prod[\"landcover_rows_off\"] = [r for r in landcover", "(i)"),
    ("relatorio_real", "terra_verdade_t1.py", "not (isinstance(r, (list, tuple)) and r and str(r[0]).startswith(RAIN_COMPARISON_PREFIX))", "True", "(e)"),
    ("relatorio_real", "terra_verdade_t1.py", "\"note\": f\"9 de cada 10 pontos até {num(r['p90_pct'], 1)}%\"", "\"note\": f\"inclinação máxima {num(r['p90_pct'], 1)}%\"", "(b)"),
    ("relatorio_real", "report_truth_guard_v16.py", "'declividade':(result.get('terrain_srtm') or {}).get('ok') is True", "'declividade':slope.get('ok') is True", "(g)"),
    ("ci_roda_o_gate", ".github/workflows/quality-gate.yml", "PYTHONPATH=. python scripts/t1_terra_verdade_gate.py", "echo sem-gate-t1", "não roda o gate T1"),
]


def run_rule(name: str) -> tuple[bool, str]:
    try:
        RULES[name]()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - a regra reprova com o motivo dela
        return False, "".join(traceback.format_exception_only(type(exc), exc)).strip()[:900]


def run_mutant(rule: str, rel: str, old: str, new: str, expect: str) -> tuple[bool, str]:
    src = (ROOT / rel).read_text(encoding="utf-8")
    if src.count(old) != 1:
        return False, f"âncora da mutação não casa 1x em {rel}: {old[:80]!r} ({src.count(old)}x)"
    with tempfile.TemporaryDirectory(prefix="rx_t1_mut_") as td:
        target = Path(td) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src.replace(old, new), encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([td, str(ROOT)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        env["PYTHONIOENCODING"] = "utf-8"
        env["RX_T1_GATE_CHILD"] = "1"
        if rel.endswith(".yml"):
            env["T1_WORKFLOW"] = str(target)
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--rule", rule], cwd=str(ROOT), env=env,
                              capture_output=True, timeout=900)
    out = (proc.stdout + proc.stderr).decode("utf-8", "ignore")
    line = next((x for x in out.splitlines() if x.startswith(f"FAIL {rule}")), "")
    if proc.returncode == 0 or not line:
        return False, f"a regra passou com a mutação (saída: {out[-300:]!r})"
    if expect not in line:
        return False, f"reprovou pelo motivo errado: {line[:400]}"
    return True, line[:200]


def main() -> int:
    offline()
    if "--rule" in sys.argv:
        name = sys.argv[sys.argv.index("--rule") + 1]
        ok, why = run_rule(name)
        print(f"PASS {name}" if ok else f"FAIL {name}: {why}", flush=True)
        return 0 if ok else 1
    failures = []
    for name in RULES:
        ok, why = run_rule(name)
        print(f"PASS {name}" if ok else f"FAIL {name}: {why}", flush=True)
        if not ok:
            failures.append(name)
    for rule, rel, old, new, expect in MUTANTS:
        ok, why = run_mutant(rule, rel, old, new, expect)
        label = f"{rule} <- {rel}"
        print(f"CONTROLE_POSITIVO_OK {label}: {why}" if ok else f"FAIL controle {label}: {why}", flush=True)
        if not ok:
            failures.append(f"controle:{label}")
    if failures:
        print(f"RX_T1_TERRA_VERDADE_GATE=FAIL {len(failures)}: {', '.join(failures)}", flush=True)
        return 1
    print(f"RX_T1_TERRA_VERDADE_GATE=PASS regras={len(RULES)} controles_positivos={len(MUTANTS)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

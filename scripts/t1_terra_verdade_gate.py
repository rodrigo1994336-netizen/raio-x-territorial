"""Gate T1 · terra-verdade: o relatório não diz relevo, solo, chuva e crédito de um jeito errado.

Roda sem rede (conexão e curl recusados; só 127.0.0.1 para o servidor lento de teste). As regras olham
a cadeia efetiva do relatório (a mesma lista de módulos que o ``sitecustomize`` carrega), o PDF gerado
por ela e, além do caminho feliz, os caminhos de falha e de fronteira que a revisão de 15/09/2026 achou
passando sem ninguém ver.

Regras
  relevo_em_porcentagem          classes de relevo da Embrapa em % com os limites certos (0–3–8–20–45–75);
                                 25° a 45° é uso restrito (art. 11) e acima de 45° é APP (art. 4º, V), em
                                 linhas separadas; percentuais inteiros.
  relevo_mosaico_emenda          folhas SRTM sintéticas: imóvel que cruza a latitude inteira não ganha degrau
                                 falso na emenda, cada pixel conta uma vez, mediana e P90 do imóvel inteiro
                                 (não média das folhas), vazio de dado não vira encosta; limite do método
                                 (imóvel minúsculo, folhas demais) some e falha de download fica pendente.
  relevo_horn_ruido              terreno plano com ruído de ±2 m continua "plano" (Horn 3x3, não diferença
                                 central, que amplifica o ruído do SRTM).
  sem_maximo_de_percentil        nenhum "máximo" de inclinação (o antigo era o percentil 99,5).
  soilgrids_power_fora           SoilGrids fora da cadeia; comparação da chuva recente da POWER com o normal
                                 retida no portal e no payload.
  caminho_nacional_sem_filtro_mg solo/aptidão/erodibilidade pela base nacional em qualquer UF, ligada na
                                 análise do servidor (com prazo total) e no resumo da tela.
  solo_falhas_viram_pendente     fonte fora, resposta no teto, numberMatched maior que o que veio, mancha
                                 que não se deixa cruzar, servidor que pinga bytes e prazo total -> pendente;
                                 CAR em laço é consertado; eixos da caixa na ordem do EPSG:4326; selo e
                                 "Produtivo" só verdes com solo, aptidão e relevo respondidos; guardião não
                                 conta pendente como consultado.
  partes_do_imovel               "todo o imóvel" só com 99,5 %; 96/4, 97 e 15 manchas iguais com texto
                                 verdadeiro; lasca de borda fora da contagem de classes; o mesmo número com
                                 o mesmo formato no KPI e na erodibilidade.
  textura_prazo_e_trava          MapBiomas Solo: trava com espera limitada pelo prazo, prazo entre arquivos,
                                 pixel 0/0/0 fora da conta, timeout do slot vira "pendente".
  chuva_estimativa_regional      milímetros da POWER inteiros e ditos estimativa regional (PDF, portal, /novo).
  mcr_datas_da_5303              Res. CMN 5.303/2026 em todos os caminhos (linha do PRODES, lente, v8,
                                 narrativa), com AST e PCT na regra do 17-A.
  relatorio_real                 PDF da cadeia real (Curvelo/MG por fixture).
  ci_roda_o_gate                 o quality-gate roda este gate.

Controles positivos (sempre rodam): cada mutação é aplicada no ARQUIVO, num processo novo que importa a
cópia mutada antes da original, e tem de reprovar pela regra dela.

  PYTHONPATH=. python scripts/t1_terra_verdade_gate.py
"""
from __future__ import annotations

import ast
import asyncio
import atexit
import copy
import http.server
import importlib
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
EMBRAPA = [("plano", "0–3%", 0.0, 3.0), ("suave ondulado", "3–8%", 3.0, 8.0), ("ondulado", "8–20%", 8.0, 20.0),
           ("forte ondulado", "20–45%", 20.0, 45.0), ("montanhoso", "45–75%", 45.0, 75.0), ("escarpado", "acima de 75%", 75.0, math.inf)]


# ------------------------------------------------------------------ sem rede
class NetworkRefused(RuntimeError):
    pass


_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection
_REAL_POPEN = subprocess.Popen
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


class _NoCurlPopen(_REAL_POPEN):
    def __init__(self, args, *a, **k):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        if "curl" in str(first).lower():
            raise NetworkRefused("curl recusado no gate offline")
        super().__init__(args, *a, **k)


def _connect(self, address, *args):
    if isinstance(address, tuple) and address and address[0] in _LOOPBACK:
        return _REAL_CONNECT(self, address, *args)
    raise NetworkRefused(f"rede recusada no gate offline: {address!r}"[:120])


def _create_connection(address, *args, **kwargs):
    if isinstance(address, tuple) and address and address[0] in _LOOPBACK:
        return _REAL_CREATE(address, *args, **kwargs)
    raise NetworkRefused("rede recusada")


def offline():
    socket.socket.connect = _connect
    socket.create_connection = _create_connection
    subprocess.Popen = _NoCurlPopen


def src_path(rel: str) -> Path:
    """Arquivo que a regra deve ler: a cópia mutada de um controle positivo, quando houver."""
    root = os.environ.get("T1_GATE_SRC_ROOT")
    if root and (Path(root) / rel).exists():
        return Path(root) / rel
    return ROOT / rel


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


def fake_get_for(geometry, aptitude_units=("PVe34", "CXbd69"), seen=None):
    fx = json.loads(FIX.read_text(encoding="utf-8"))
    layers = {
        "BDIA:pedo_area": [(0.57, fx["pedo"]["PVe34"]), (0.43, fx["pedo"]["CXbd69"])],
        "geonode:aptidao_agr_bra": [(0.57, fx["apt"][aptitude_units[0]]), (0.43, fx["apt"][aptitude_units[-1]])],
        "geonode:bra_erodibilidade_2024_sirgas2000": [(0.57, fx["erod"]["PVe34"]), (0.43, fx["erod"]["CXbd69"])],
    }

    def get(url, params):
        if seen is not None:
            seen.append(dict(params))
        return {"type": "FeatureCollection", "features": split_features(geometry, layers[params["typeNames"]])}
    return get


def terrain_fixture(grades_pct=(5.0, 12.0), deg=None, rows=40, cols=60):
    """Resultado no formato do terrain_srtm, calculado pelo próprio _slope_stats sobre um terreno sintético."""
    import numpy as np
    import terrain_srtm

    res = 1 / 3600
    lat = -18.89
    dx = float(terrain_srtm._cell_metres(lat, res)[0])
    xs = np.arange(cols, dtype="float64")
    lines = []
    for i in range(rows):
        grade = (math.tan(math.radians(deg)) * 100 if deg is not None else (grades_pct[0] if i < int(rows * 0.7) else grades_pct[1]))
        lines.append(600.0 + xs * dx * grade / 100.0)
    dem = np.vstack(lines)
    mask = np.ones(dem.shape, dtype=bool)
    cut = int(rows * 0.7)
    mask[cut - 2:cut + 2, :] = False  # a borda entre os dois declives não entra na estatística
    st = terrain_srtm._slope_stats(dem, mask, lat, res)
    return {"ok": True, "state": "found", "source": "fixture", "elevation_min_m": 641.0, "elevation_median_m": 657.0, "elevation_max_m": 668.0, **st}


def texture_fixture():
    import numpy as np
    import solo_nacional_t1 as S

    clay = np.array([44, 46, 49, 50, 53, 48, 47, 51, 0], dtype="float64")
    sand = np.array([18, 17, 18, 19, 16, 18, 20, 17, 0], dtype="float64")
    silt = 100 - clay - sand
    silt[-1] = 0
    return {"ok": True, "source": S.SOURCE_TEXTURA, "version": "T1", "ms": 5, **S.texture_summary(clay, sand, silt)}


# ------------------------------------------------------------------ folhas SRTM sintéticas
SEAM_M = -7 * 3600  # latitude -7,0 em amostras de 1"
VOID_NM = (-198288, -25250)  # canto do vazio 2x2 (n = lon*3600, m = lat*3600), dentro do imóvel da emenda
SEAM_CAR = {"type": "Polygon", "coordinates": [[[-55.09013, -7.03011], [-55.07007, -7.03011], [-55.07007, -6.98013], [-55.09013, -6.98013], [-55.09013, -7.03011]]]}
STEP_SOUTH_M, STEP_NORTH_M = 2, 4  # metros inteiros por linha: sem ruído de arredondamento (≈6,5% e ≈13%)


def _synthetic_z(m):
    import numpy as np
    import terrain_srtm

    m = np.asarray(m, dtype="float64")
    steps = m - SEAM_M
    return 500.0 + np.where(steps <= 0, steps * STEP_SOUTH_M, steps * STEP_NORTH_M)


def _write_tile(path: Path, lat: int, lon: int):
    import numpy as np
    import rasterio
    from affine import Affine

    spd = 3600
    m = (lat + 1) * spd - np.arange(spd + 1)
    arr = np.repeat(np.rint(_synthetic_z(m)).astype("int16")[:, None], spd + 1, axis=1)
    n0, m0 = VOID_NM
    r0, c0 = (lat + 1) * spd - m0, n0 - lon * spd
    if 0 <= r0 < spd and 0 <= c0 < spd:
        arr[r0:r0 + 2, c0:c0 + 2] = -32768
    tr = Affine(1 / spd, 0, lon - 0.5 / spd, 0, -1 / spd, lat + 1 + 0.5 / spd)
    with rasterio.open(path, "w", driver="GTiff", width=spd + 1, height=spd + 1, count=1, dtype="int16", crs="EPSG:4326",
                       transform=tr, nodata=-32768, compress="deflate", tiled=True) as dst:
        dst.write(arr, 1)


def synthetic_tiles() -> Path:
    shared = os.environ.get("T1_GATE_TILES")
    if shared and (Path(shared) / "S07W056.tif").exists():
        return Path(shared)
    d = Path(tempfile.mkdtemp(prefix="rx_t1_tiles_"))
    atexit.register(shutil.rmtree, d, True)
    for lat in (-8, -7):
        _write_tile(d / f"{'S'}{abs(lat):02d}W056.tif", lat, -56)
    return d


def tile_download(tiles: Path, fail: set[str] | None = None):
    import terrain_srtm

    def download(lat, lon, cache):
        tid = terrain_srtm._tile_id(lat, lon)
        if fail and tid in fail:
            raise RuntimeError(f"download {tid}: gate")
        fp = tiles / f"{tid}.tif"
        if not fp.exists():
            raise RuntimeError(f"download {tid}: fora das folhas sintéticas")
        return fp
    return download


def expected_pixels(geometry):
    """Pixels (centro dentro do polígono) contados de fora do terrain_srtm, cada um uma vez."""
    import numpy as np
    import shapely
    from shapely.geometry import shape

    g = shape(geometry)
    minx, miny, maxx, maxy = g.bounds
    ns = np.arange(math.floor(minx * 3600) - 1, math.ceil(maxx * 3600) + 2)
    ms = np.arange(math.floor(miny * 3600) - 1, math.ceil(maxy * 3600) + 2)
    N, M = np.meshgrid(ns, ms)
    inside = shapely.contains_xy(g, N / 3600.0, M / 3600.0)
    n0, m0 = VOID_NM
    void = (N >= n0) & (N <= n0 + 1) & (M <= m0) & (M >= m0 - 1)
    ring = (N >= n0 - 1) & (N <= n0 + 2) & (M <= m0 + 1) & (M >= m0 - 2)
    return int(inside.sum()), int((inside & void).sum()), int((inside & ring).sum())


# ------------------------------------------------------------------ regras: relevo
def rule_relevo_em_porcentagem():
    import terra_verdade_t1 as T
    import terrain_srtm

    labels = [(r[0], r[1], float(r[2]), float(r[3])) for r in terrain_srtm.RELIEF_CLASSES_PCT]
    assert labels == EMBRAPA, f"classes de relevo fora da Embrapa: {labels}"
    for grade, cls in ((2.0, "plano"), (5.5, "suave ondulado"), (9.0, "ondulado"), (19.0, "ondulado"), (21.0, "forte ondulado"), (60.0, "montanhoso"), (80.0, "escarpado")):
        fx = terrain_fixture(grades_pct=(grade, grade))
        shares = {r["class"]: r["share_pct"] for r in fx["slope_classes"]}
        assert shares.get(cls, 0) > 99, f"inclinação de {grade}% tem de ser {cls}: {shares}"
    gentle = terrain_fixture(deg=2.5)
    shares = {r["class"]: r["share_pct"] for r in gentle["slope_classes"]}
    assert shares.get("suave ondulado", 0) > 99 and shares.get("plano", 0) < 1, f"2,5° (4,4 %) tem de ser suave ondulado, não plano: {shares}"
    assert gentle.get("slope_unit") == "%" and 4.2 <= gentle["slope_median_pct"] <= 4.6, f"inclinação não está em %: {gentle.get('slope_median_pct')}"
    steep = terrain_fixture(deg=30)
    shares = {r["class"]: r["share_pct"] for r in steep["slope_classes"]}
    assert shares.get("montanhoso", 0) > 99 and steep["slope_25_45deg_share_pct"] > 99 and steep["slope_gt_45deg_share_pct"] == 0, \
        f"30° (57,7 %) é montanhoso, uso restrito e não APP: {shares} {steep.get('slope_25_45deg_share_pct')} {steep.get('slope_gt_45deg_share_pct')}"
    app = terrain_fixture(deg=50)
    assert app["slope_gt_45deg_share_pct"] > 99 and app["slope_25_45deg_share_pct"] == 0, \
        f"50° é APP (art. 4º, V), não uso restrito: {app.get('slope_25_45deg_share_pct')} {app.get('slope_gt_45deg_share_pct')}"
    mixed = terrain_fixture()
    for r in mixed["slope_classes"]:
        assert "°" not in r["class"] + r["range"], f"classe de relevo em graus: {r}"
    kpis = T.relief_kpis(mixed)
    text = json.dumps(kpis, ensure_ascii=False)
    assert "°" not in text and "Declive" not in text and "Plano ou suave ondulado" in text, f"KPI de relevo errado: {text}"
    assert not re.search(r"\d,\d%", text), f"KPI de relevo com casa decimal (precisão falsa num modelo de 30 m): {text}"
    rows = T.relief_rows(mixed)
    assert not re.search(r"\d,\d%", json.dumps(rows, ensure_ascii=False)), f"classes de relevo com casa decimal: {rows}"
    assert not any(r[0] in (T.LEGAL_RESTRICTED_LABEL, T.LEGAL_APP_LABEL) for r in rows), "marco legal apareceu sem área acima de 25°"
    steep_rows = [r[0] for r in T.relief_rows(steep)]
    assert T.LEGAL_RESTRICTED_LABEL in steep_rows and T.LEGAL_APP_LABEL not in steep_rows, f"30°: uso restrito sim, APP não: {steep_rows}"
    app_rows = [r[0] for r in T.relief_rows(app)]
    assert T.LEGAL_APP_LABEL in app_rows and T.LEGAL_RESTRICTED_LABEL not in app_rows, f"50° é APP, não uso restrito: {app_rows}"
    assert "art. 11" in T.LEGAL_RESTRICTED_LABEL and "25° e 45°" in T.LEGAL_RESTRICTED_LABEL and "art. 4º, V" in T.LEGAL_APP_LABEL, "rótulos legais trocados"


def rule_relevo_mosaico_emenda():
    import terra_verdade_t1 as T
    import terrain_srtm

    tiles = synthetic_tiles()
    with patch.object(terrain_srtm, "_download", tile_download(tiles)):
        r = terrain_srtm.query_terrain_srtm(SEAM_CAR)
    assert r.get("ok") and r.get("state") == "found", f"imóvel na emenda das folhas sem resposta: {r}"
    assert sorted(r.get("tiles") or []) == ["S07W056", "S08W056"], f"o imóvel cruza a latitude -7: {r.get('tiles')}"
    shares = {c["class"]: c["share_pct"] for c in r["slope_classes"]}
    assert shares.get("escarpado", 0) == 0 and shares.get("montanhoso", 0) == 0 and shares.get("forte ondulado", 0) == 0 \
        and r["slope_ge_25deg_share_pct"] == 0, f"degrau falso (emenda das folhas ou vazio de dado) num terreno de 6,5% e 13%: {shares} ≥25°={r['slope_ge_25deg_share_pct']}"
    inside, void, ring = expected_pixels(SEAM_CAR)
    assert void == 4 and ring == 16, (void, ring)
    assert r["elevation_sample_pixels"] == inside - void, f"pixel contado duas vezes (ou vazio contado): {r['elevation_sample_pixels']} contra {inside - void}"
    assert r["slope_sample_pixels"] == inside - ring, f"inclinação com vizinho vazio ou pixel repetido: {r['slope_sample_pixels']} contra {inside - ring}"
    # 60% do imóvel a 6,5% e 40% a 13%: mediana é 6,5%, P90 é 13% (média das medianas por folha daria ~9%)
    dy = float(terrain_srtm._cell_metres(-7.0, 1 / 3600)[1])
    south, north = STEP_SOUTH_M / dy * 100, STEP_NORTH_M / dy * 100
    assert abs(r["slope_median_pct"] - south) < 0.05, f"mediana do imóvel inteiro errada (média de folhas?): {r['slope_median_pct']} contra {south:.2f}"
    assert abs(r["slope_p90_pct"] - north) < 0.05, f"P90 do imóvel inteiro errado: {r['slope_p90_pct']} contra {north:.2f}"
    kpi = next(k for k in T.relief_kpis(r) if k["label"] == "Inclinação")
    assert kpi["value"] == "7% mediana" and kpi["note"] == "9 de cada 10 pontos até 13%", f"KPI da mediana: {kpi}"
    # limite do método: some, sem "pendente" que nunca vai se resolver
    tiny = {"type": "Polygon", "coordinates": [[[-55.08005, -7.02005], [-55.07995, -7.02005], [-55.07995, -7.01995], [-55.08005, -7.01995], [-55.08005, -7.02005]]]}
    with patch.object(terrain_srtm, "_download", tile_download(tiles)):
        small = terrain_srtm.query_terrain_srtm(tiny)
    assert small.get("state") == "not_found", f"imóvel abaixo de 9 pixels é limite do método: {small}"
    assert T.relief_kpis(small) == [] and T.relief_rows(small) == [], f"limite do método virou pendente: {T.relief_kpis(small)} {T.relief_rows(small)}"
    huge = {"type": "Polygon", "coordinates": [[[-56.5, -9.5], [-53.5, -9.5], [-53.5, -7.5], [-56.5, -7.5], [-56.5, -9.5]]]}
    big = terrain_srtm.query_terrain_srtm(huge)
    assert big.get("state") == "not_found" and "tile_guard" in str(big.get("detail")), f"folhas demais é limite do método: {big}"
    with patch.object(terrain_srtm, "_download", tile_download(tiles, fail={"S07W056"})):
        down = terrain_srtm.query_terrain_srtm(SEAM_CAR)
    assert down.get("state") == "pending" and not down.get("ok"), f"folha que não baixou tem de ficar pendente, não meia resposta: {down}"
    kp = T.relief_kpis(down)
    assert kp and kp[0]["status"] == "CONSULTA PENDENTE" and kp[0]["level"] != "ok" and kp[0]["value"] == "CONSULTA PENDENTE", f"relevo pendente pintado de resposta: {kp}"
    assert T.relief_rows(down) == [["Relevo medido (classes da Embrapa)", "Consulta pendente."]], T.relief_rows(down)
    assert T.relief_kpis({"ok": False, "detail": "RuntimeError"})[0]["status"] == "CONSULTA PENDENTE", "erro sem estado tem de ser pendente"


def rule_relevo_horn_ruido():
    import numpy as np
    import terrain_srtm

    rng = np.random.default_rng(20260915)
    dem = 500.0 + rng.integers(-2, 3, size=(200, 200)).astype("float64")
    mask = np.ones(dem.shape, dtype=bool)
    st = terrain_srtm._slope_stats(dem, mask, -12.43, 1 / 3600)
    shares = {r["class"]: r["share_pct"] for r in st["slope_classes"]}
    assert shares.get("plano", 0) > 50, f"terreno plano com ruído de ±2 m deixou de ser plano (método que amplifica ruído?): {shares}"
    assert "Horn" in st.get("slope_method", ""), st.get("slope_method")


def rule_sem_maximo_de_percentil():
    import terra_verdade_t1 as T

    t = terrain_fixture()
    keys = [k for k in t if k.startswith("slope") and "max" in k]
    assert not keys, f"inclinação com 'máximo': {keys}"
    blob = json.dumps([T.relief_kpis(t), T.relief_rows(t), T.relief_check(t), T.relief_source(t)], ensure_ascii=False)
    assert not re.search(r"(?i)m[áa]xim", blob), f"texto de relevo fala em máximo: {blob[:300]}"


# ------------------------------------------------------------------ regras: fontes na cadeia
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
    payload = T.apply_payload({"productive": {}, "water": {"drought_screening": {"status": "found", "state": "abaixo do normal", "summary": "Abaixo do normal"}}}, {})
    assert payload["water"]["drought_screening"]["status"] != "found" and not payload["water"]["drought_screening"].get("state"), \
        f"payload guarda a comparação da chuva como resposta: {payload['water']['drought_screening']}"


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
    tree = ast.parse(api_src)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_analyze_uncached")
    called = {getattr(n.func, "id", None) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_terra_nacional" in called, "análise do servidor não chama a base nacional de solo"
    helper = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_terra_nacional")
    assert "query_solo_nacional" in {getattr(a, "id", None) for n in ast.walk(helper) if isinstance(n, ast.Call) for a in n.args}, "_terra_nacional não chama a base nacional"
    import terra_verdade_t1 as T

    not_applicable = {"ok": False, "state": "not_applicable", "detail": "IDE-Sisema é fonte estadual de Minas Gerais; não aplicável a este imóvel."}
    payload = {"productive": {}, "sources": [{"name": "IDE-Sisema / Uso e Cobertura", "status": "INDISPONÍVEL"},
                                             {"name": "IDE-Sisema / Recomposição de Reserva Legal declarada", "status": "INDISPONÍVEL"}]}
    out = T.apply_payload(payload, {"terra_nacional": solo, "ide_layers": {"soil": dict(not_applicable), "landcover_centro_norte": {"ok": False, "state": "superseded"}}})
    left = [s["name"] for s in out["sources"] if str(s.get("name", "")).startswith("IDE-Sisema")]
    assert not left, f"camada estadual de MG como consulta pendente num imóvel fora de MG: {left}"
    summary = report_api._report_summary({"car": {"ok": True, "properties": {}}, "terra_nacional": solo})
    assert (summary.get("terra_nacional") or {}).get("texts", {}).get("solo"), f"resumo da tela sem o solo nacional: {summary.get('terra_nacional')}"


# ------------------------------------------------------------------ regras: falhas do solo
class _DripHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - nome da API
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "100000")
        self.end_headers()
        try:
            for _ in range(40):  # um byte a cada 0,25 s por 10 s: cada leitura cabe no tempo limite do httpx
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.25)
        except OSError:
            pass

    def log_message(self, *a):
        pass


def _states(solo):
    return {k: (solo.get(k) or {}).get("state") for k in ("pedologia", "aptidao", "erodibilidade")}


def rule_solo_falhas_viram_pendente():
    import report_api
    import report_truth_guard_v16 as G
    import solo_nacional_t1 as S
    import terra_verdade_t1 as T

    geom = pa_geometry()

    def down(url, params):
        raise ConnectionError("fonte fora")
    solo = S.query_solo_nacional(geom, get=down)
    assert set(_states(solo).values()) == {"pending"}, f"fonte fora do ar tem de ser pendente, nunca 'nenhuma unidade': {_states(solo)}"
    reading = S.soil_reading(solo, None)
    rows = dict((r[0], r[1]) for r in reading["soil_rows"])
    assert rows.get("Tipo de solo no mapa oficial") == S.PENDING_TEXT, f"linha de solo pendente sumiu: {reading['soil_rows']}"
    assert rows.get("Erodibilidade do solo (mapa oficial)") == S.PENDING_TEXT, f"linha de erodibilidade pendente sumiu: {reading['soil_rows']}"
    assert ["Aptidão agrícola", S.PENDING_TEXT] in reading["aptitude_rows"], f"aptidão pendente sumiu: {reading['aptitude_rows']}"
    statuses = {s["name"]: s["status"] for s in reading["sources"]}
    assert statuses.get(S.SOURCE_PEDOLOGIA) == "INDISPONÍVEL", f"fonte fora com selo de consultada: {statuses}"
    guard = G.comprehensive_truth_guard({}, {"terra_nacional": solo, "terrain_srtm": {"ok": False, "detail": "download"}})
    ready = guard["conclusion"]["coverage"]["consulted_core"]
    assert "solo" not in ready and "aptidão" not in ready and "declividade" not in ready, f"guardião conta pendente como consultado: {ready}"
    limit = G.comprehensive_truth_guard({}, {"terra_nacional": solo, "terrain_srtm": {"ok": False, "state": "not_found", "detail": "tile_guard:6"}})
    assert "declividade" in limit["conclusion"]["coverage"]["consulted_core"], "limite do método do relevo virou ponto cego"

    feats = split_features(geom, [(1.0, {"nom_unidad": "X", "legenda": "X - Xis"})])
    capped = S.query_solo_nacional(geom, get=lambda u, p: {"features": feats * S.FEATURE_CAP})
    assert set(_states(capped).values()) == {"pending"}, f"resposta no teto de feições (cortada) tem de ser pendente: {_states(capped)}"
    matched = S.query_solo_nacional(geom, get=lambda u, p: {"features": feats, "numberMatched": 152, "numberReturned": 1})
    assert set(_states(matched).values()) == {"pending"}, f"numberMatched maior que o que veio é resposta cortada: {_states(matched)}"
    broken = [{"type": "Feature", "properties": {"nom_unidad": "Y"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1]]]}}] + feats
    bad = S.query_solo_nacional(geom, get=lambda u, p: {"features": broken})
    assert set(_states(bad).values()) == {"pending"}, f"mancha que não se deixa cruzar virou 'nenhuma unidade': {_states(bad)}"
    bow = {"type": "Polygon", "coordinates": [[[-55.09, -7.02], [-55.07, -6.98], [-55.07, -7.02], [-55.09, -6.98], [-55.09, -7.02]]]}
    looped = S.query_solo_nacional(bow, get=lambda u, p: {"features": split_features(bow, [(1.0, {"nom_unidad": "X", "legenda": "X - Xis"})])})
    ped = looped.get("pedologia") or {}
    assert ped.get("state") == "found" and abs(sum(u["share_pct"] for u in ped.get("units") or []) - 100.0) < 0.5, \
        f"CAR em laço (comum no SICAR) tem de ser consertado com make_valid: {ped}"

    seen: list = []
    S.query_solo_nacional(geom, get=fake_get_for(geom, seen=seen))
    bbox = [float(x) for x in seen[0]["bbox"].split(",")[:4]]
    assert abs(bbox[0] - (-7.0138)) < 1e-6 and abs(bbox[1] - (-55.0928)) < 1e-6, f"caixa do WFS 2.0 em EPSG:4326 é lat,lon: {seen[0]['bbox']}"

    def slow(url, params):
        time.sleep(3.0)
        return {"features": feats}
    t0 = time.monotonic()
    late = S.query_solo_nacional(geom, get=slow, deadline_s=1.0)
    took = time.monotonic() - t0
    assert took < 2.0 and set(_states(late).values()) == {"pending"}, f"prazo total não segurou a consulta: {took:.1f}s {_states(late)}"

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _DripHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/ows"
        t0 = time.monotonic()
        with patch.dict(S.LAYERS, {"pedologia": (url, "BDIA:pedo_area")}):
            drip = S._layer("pedologia", geom, None, time.monotonic() + 1.5)
        took = time.monotonic() - t0
    finally:
        server.shutdown()
        server.server_close()
    assert drip.get("state") == "pending" and took < 3.5, f"servidor que pinga bytes segurou a camada: {took:.1f}s {drip}"

    def stuck(g):
        time.sleep(3.0)
        return {"ok": True}
    async def outer_call():
        t0 = time.monotonic()
        value = await report_api._terra_nacional(geom)
        return value, time.monotonic() - t0  # medido antes de o asyncio.run esperar a thread abandonada no fechamento
    with patch.object(report_api, "query_solo_nacional", stuck), patch.object(report_api, "SOLO_NACIONAL_OUTER_S", 0.5):
        outer, took = asyncio.run(outer_call())
    assert took < 2.0 and set(_states(outer).values()) == {"pending"}, f"análise espera a base nacional sem limite: {took:.1f}s {outer}"

    answered = S.query_solo_nacional(geom, get=fake_get_for(geom))
    half = copy.deepcopy(answered)
    half["aptidao"] = {"state": "pending", "layer": "geonode:aptidao_agr_bra", "detail": "gate"}
    terrain = terrain_fixture()
    base = lambda: {"productive": {}, "compliance": [{"label": "Solo / aptidão", "text": "", "badge": "", "level": ""}],  # noqa: E731
                    "conclusion": {"categories": [{"label": "Produtivo", "text": "Solo indisponível", "risk": "ATENÇÃO", "level": "attention"}]}}
    full = T.apply_payload(base(), {"terra_nacional": answered, "terrain_srtm": terrain})
    assert full["compliance"][0]["badge"] == "CONSULTADO" and full["compliance"][0]["level"] == "ok", full["compliance"]
    cat = full["conclusion"]["categories"][0]
    assert cat["risk"] == "TRIAGEM DISPONÍVEL" and cat["level"] == "info", f"'Produtivo' herdou risco antigo com tudo respondido: {cat}"
    partial = T.apply_payload(base(), {"terra_nacional": half, "terrain_srtm": terrain})
    comp = partial["compliance"][0]
    assert comp["badge"] == "PARCIAL" and comp["level"] != "ok" and "Aptidão: consulta pendente" in comp["text"], f"selo verde com aptidão pendente: {comp}"
    cat = partial["conclusion"]["categories"][0]
    assert cat["risk"] == "CONSULTA PENDENTE" and cat["level"] != "ok", f"'Produtivo' sem dizer a pendência: {cat}"
    no_relief = T.apply_payload(base(), {"terra_nacional": answered, "terrain_srtm": {"ok": False, "detail": "download"}})
    assert no_relief["compliance"][0]["badge"] == "PARCIAL" and "Relevo: consulta pendente" in no_relief["compliance"][0]["text"], no_relief["compliance"]


# ------------------------------------------------------------------ regras: partes do imóvel
_ALL = re.compile(r"(?<!quase )todo o imóvel")


def rule_partes_do_imovel():
    import solo_nacional_t1 as S
    import terra_verdade_t1 as T

    line = S._parts_line([("U0", 96.0), ("U1", 4.0)])
    assert not _ALL.search(line) and "quase todo o imóvel" in line and "U1" not in line and "manchas menores" in line, f"96/4: {line}"
    line = S._parts_line([("U0", 97.0)])
    assert not _ALL.search(line) and "quase todo" in line, f"97% (3% de água ou fora do mapa) não é 'todo o imóvel': {line}"
    line = S._parts_line([("U0", 99.6), ("U1", 0.4)])
    assert _ALL.search(line) and ";" not in line, f"99,6% com lasca de borda é todo o imóvel, sem outra parte: {line}"
    line = S._parts_line([(f"U{i}", 100 / 15) for i in range(15)])
    phrases = {p.split(" em ", 1)[1].rstrip(".") for p in line.split("; ")}
    assert len(phrases) == 1 and "menos de 1 de cada 10" in phrases.pop(), f"15 manchas iguais com textos diferentes: {line}"
    line = S._parts_line([("A", 57.1), ("B", 42.9)])
    assert "A em cerca de 6 de cada 10" in line and "B em cerca de 4 de cada 10" in line, line
    for shares in ([96.0, 4.0], [55.0, 45.0], [74.0, 26.0], [33.4, 33.3, 33.3], [88.0, 8.0, 4.0]):
        text = S._parts_line([(f"U{i}", s) for i, s in enumerate(shares)])
        assert not (_ALL.search(text) and ";" in text), f"'todo o imóvel' junto com outra parte: {text}"
    geom = pa_geometry()
    fx = json.loads(FIX.read_text(encoding="utf-8"))

    from shapely.geometry import box, mapping, shape

    minx, miny, maxx, maxy = shape(geom).bounds
    w = maxx - minx
    strips = [(minx - 0.01, minx + 0.60 * w, fx["apt"]["PVe34"]), (minx + 0.60 * w, minx + 0.97 * w, fx["apt"]["CXbd69"]),
              (minx + 0.97 * w, maxx + 0.01, {"nom_unidad": "LASCA", "simb_apt": "2(b)c", "legenda_ap": "2(b)c  Aptidão REGULAR para lavouras."})]

    def get(url, params):
        if params["typeNames"] == "geonode:aptidao_agr_bra":
            return {"features": [{"type": "Feature", "properties": props, "geometry": mapping(box(a, miny - 0.01, b, maxy + 0.01))} for a, b, props in strips]}
        return fake_get_for(geom)(url, params)
    solo = S.query_solo_nacional(geom, get=get)
    apt_units = (solo.get("aptidao") or {}).get("units") or []
    assert len(apt_units) == 3 and min(u["share_pct"] for u in apt_units) < 5, apt_units
    terrain = terrain_fixture()
    payload = T.apply_payload({"productive": {}}, {"terra_nacional": solo, "terrain_srtm": terrain})
    kpi = next((k for k in payload["productive"]["terrain_kpis"] if k["label"] == "Aptidão (mapa regional)"), None)
    assert kpi and kpi["value"] == "2 classes", f"lasca de 3% contada como classe de aptidão: {kpi}"
    flat = next(k for k in payload["productive"]["terrain_kpis"] if k["label"] == "Plano ou suave ondulado")["value"]
    ero = next(r[1] for r in payload["productive"]["soil_rows"] if r[0] == "Erodibilidade do solo (mapa oficial)")
    assert f"No imóvel, {flat} do terreno" in ero, f"o mesmo número com formatos diferentes no KPI ({flat}) e na erodibilidade: {ero}"
    assert any(r[0] == "Escala do mapa" for r in payload["productive"]["soil_rows"]), "linha da escala do mapa sumiu com o solo respondido"


# ------------------------------------------------------------------ regras: textura
def rule_textura_prazo_e_trava():
    import live_report_adapter_v13 as v13
    import solo_nacional_t1 as S

    summary = texture_fixture()
    assert summary["pixels"] == 8, f"pixel 0/0/0 (fora do mapeamento) contado como solo: {summary['pixels']}"
    geom = pa_geometry()
    fake = lambda url, car: __import__("numpy").array([45.0, 46.0, 47.0])  # noqa: E731
    holder_ready, release = threading.Event(), threading.Event()

    def hold():
        with S._TEXTURE_LOCK:
            holder_ready.set()
            release.wait(10)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    holder_ready.wait(5)
    out: dict = {}
    t0 = time.monotonic()
    worker = threading.Thread(target=lambda: out.update(S.query_mapbiomas_solo(geom, reader=fake, deadline=time.monotonic() + 5.0)), daemon=True)
    worker.start()
    worker.join(3.0)
    alive = worker.is_alive()
    release.set()
    holder.join(5)
    worker.join(5)
    assert not alive and out.get("state") == "pending" and time.monotonic() - t0 < 6, f"textura esperou a trava sem prazo: vivo={alive} {out}"

    def slow_reader(url, car):
        time.sleep(1.0)
        return fake(url, car)
    t0 = time.monotonic()
    res = S.query_mapbiomas_solo(geom, reader=slow_reader, deadline=time.monotonic() + 1.5)
    took = time.monotonic() - t0
    assert res.get("state") == "pending" and took < 2.7, f"leitura passou do prazo entre arquivos: {took:.1f}s {res.get('state')}"
    assert S._TEXTURE_LOCK.acquire(blocking=False), "trava ficou presa depois do prazo"
    S._TEXTURE_LOCK.release()
    timeout = {"ok": False, "source": "mapbiomas_solo", "detail": "timeout_after_16s", "partial": True}
    payload = v13._patch_soilgrids({"productive": {}}, timeout)
    assert (payload["productive"].get("solo_textura_t1") or {}).get("state") == "pending", f"timeout do slot sumiu com a textura: {payload['productive']}"
    rows = dict((r[0], r[1]) for r in S.soil_reading({}, payload["productive"]["solo_textura_t1"])["soil_rows"])
    assert rows.get("Textura de 0 a 30 cm (estimativa no imóvel)") == S.PENDING_TEXT, rows
    src = src_path("report_extras_perf_v30.py").read_text(encoding="utf-8")
    call = next((n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "to_thread"
                 and any(getattr(a, "attr", "") == "query_soil_texture" for a in n.args)), None)
    assert call is not None and len(call.args) == 4, "o slot da textura não passa o prazo contado do pedido"


# ------------------------------------------------------------------ regras: chuva
def rule_chuva_estimativa_regional():
    import terra_verdade_t1 as T

    climate = {"ok": True, "rain_sum_mm": 33.0149, "rain_daily_avg_mm": 1.1, "temp_avg_c": 25.86}
    payload = {"water": {"rain_30d": "33.01 mm", "rain_period": "20260815 a 20260913 • 30 dias válidos", "rain_rows": [
        ["Precipitação acumulada - janela recente", "33.01 mm"], ["Precipitação média diária", "1.1 mm/dia"],
        ["Climatologia JAN", "chuva ≈ 188 mm no mês (6,08 mm/dia) • temperatura média 24,0 °C"],
        ["Chuva recente comparada ao normal", "abaixo do normal"]]},
        "agropecuaria": {"property_screening": {"checks": [{"factor": "Clima recente", "value": {"rain_30d_mm": 33.01}}]}}}
    out = T.apply_payload(payload, {"climate_nasa": climate})
    rows = dict((r[0], r[1]) for r in out["water"]["rain_rows"])
    assert rows.get("Precipitação acumulada - janela recente") == "33 mm (estimativa regional NASA POWER, grade de ~50 km)", \
        f"Precipitação acumulada da POWER sem estimativa regional ou com casas decimais: {rows.get('Precipitação acumulada - janela recente')!r}"
    assert "mm/dia" not in rows.get("Climatologia JAN", "") and "188 mm" in rows.get("Climatologia JAN", ""), f"Climatologia com mm/dia de duas casas: {rows.get('Climatologia JAN')!r}"
    assert T.RAIN_BASIS_LABEL in rows and "~50 km" in rows[T.RAIN_BASIS_LABEL] and "não é comparada" in rows[T.RAIN_BASIS_LABEL], rows
    assert not any(k.startswith(T.RAIN_COMPARISON_PREFIX) for k in rows), rows
    assert out["water"]["rain_30d"] == "33 mm" and "estimativa regional" in out["water"]["rain_period"], out["water"]
    check = out["agropecuaria"]["property_screening"]["checks"][0]["value"]
    assert isinstance(check, str) and "33 mm" in check and "estimativa regional" in check and "33,01" not in check, check
    portal = src_path("portal_property_tabs.py").read_text(encoding="utf-8")
    msg = re.search(r"const NORMAL_MSG=\{(.*?)\};", portal)
    assert msg and T.RAIN_WITHHELD_REASON + ":" in msg.group(1), "portal: a comparação retida de propósito aparece como 'pendente'"
    js = src_path("static/novo/app.js").read_text(encoding="utf-8")
    assert re.search(r"mm de chuva'.{0,200}estimativa regional da NASA POWER", js), "/novo: milímetros da POWER sem dizer que são estimativa regional"


# ------------------------------------------------------------------ regras: crédito
def rule_mcr_datas_da_5303():
    from datetime import date

    import live_report_adapter_v8 as v8
    import mcr_regra_t1 as M
    import prodes_reading_f2 as P
    import report_narrative

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
    for kind in ("AST", "PCT"):
        assert "03/01/2028" in M.property_sentence(40, kind, today), f"{kind} (MCR 2-9-17-A) usa a data de até 4 MF"
    assert "vale desde" in M.property_sentence(0.37, "IRU", date(2028, 2, 1)), "data já passada dita como futura"
    assert M.property_sentence(None, "IRU", today) == "", "porte desconhecido virou data"
    car = curvelo_car()
    result = {"car": {"ok": True, "properties": dict(car["properties"]), "geometry": car["geometry"]}, "prodes": {"ok": False, "detail": "gate"}}
    rows = dict((r[0], r[1]) for r in P.prodes_reading_payload(result)["rows"])
    basis = rows.get("Base regulatória", "")
    assert "03/01/2028" in basis and "0,37 módulo fiscal" in basis, f"linha do relatório sem a data do porte: {basis!r}"
    answered = {"car": {"ok": True, "properties": dict(car["properties"]), "geometry": car["geometry"]},
                "prodes": {"ok": True, "exact": {"available": True, "occurrence_count": 0, "occurrences": []}}}
    v8rows = dict((r[0], r[1]) for r in v8._patch_prodes_truth({}, answered)["environment"]["prodes"]["rows"])
    assert "03/01/2028" in v8rows.get("Base regulatória", "") and "5.303" in v8rows.get("Base regulatória", ""), f"caminho v8 com texto antigo do MCR: {v8rows.get('Base regulatória')!r}"
    nar = report_narrative.build_narrative({"property": {"area_ha": 14.8, "municipality": "Curvelo", "uf": "MG"},
                                            "environment": {"prodes": {"lens": {"historical": {"occurrence_count": 2}, "post_2019_07_31": {"occurrence_count": 1}}}}})
    why = " ".join(str(x) for x in nar.get("why_it_matters") or [])
    assert "condicionado a documento" in why and "atenção especial" not in why, f"narrativa com a regra antiga do MCR: {why[:400]!r}"
    offenders = []
    for py in ROOT.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if any(old in text for old in OLD_MCR_TEXTS):
            offenders.append(py.name)
    assert not offenders, f"texto antigo do MCR ainda no sistema: {offenders}"


# ------------------------------------------------------------------ regras: PDF real
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
        patch.object(v13, "query_soil_texture", lambda *a, **k: texture_fixture()),
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
    # (e) chuva recente sem adjetivo; os milímetros ficam, inteiros e ditos estimativa regional
    assert "Chuva recente comparada" not in text and not re.search(r"(?i)(acima|abaixo) do normal|da média da época|dentro do normal", text), "(e) chuva da POWER comparada ao normal no PDF"
    assert "31 mm (estimativa regional NASA POWER" in text and "31,46 mm" not in text, "(e) chuva do PDF sem a estimativa regional ou com casas decimais"
    assert "Base da chuva e da climatologia" in text and not re.search(r"Climatologia \w+ chuva ≈ \d+ mm no mês \(", text), "(e) climatologia da POWER sem a base dita ou com mm/dia"
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
    "relevo_mosaico_emenda": rule_relevo_mosaico_emenda,
    "relevo_horn_ruido": rule_relevo_horn_ruido,
    "sem_maximo_de_percentil": rule_sem_maximo_de_percentil,
    "soilgrids_power_fora": rule_soilgrids_power_fora,
    "caminho_nacional_sem_filtro_mg": rule_caminho_nacional_sem_filtro_mg,
    "solo_falhas_viram_pendente": rule_solo_falhas_viram_pendente,
    "partes_do_imovel": rule_partes_do_imovel,
    "textura_prazo_e_trava": rule_textura_prazo_e_trava,
    "chuva_estimativa_regional": rule_chuva_estimativa_regional,
    "mcr_datas_da_5303": rule_mcr_datas_da_5303,
    "relatorio_real": rule_relatorio_real,
    "ci_roda_o_gate": rule_ci_roda_o_gate,
}

# (regra, arquivo, trecho, troca, motivo esperado na reprovação)
MUTANTS = [
    # relevo
    ("relevo_em_porcentagem", "terrain_srtm.py", "    slope_pct=np.hypot(dzdx,dzdy)*100.0", "    slope_pct=np.degrees(np.arctan(np.hypot(dzdx,dzdy)))", "tem de ser"),
    ("relevo_em_porcentagem", "terra_verdade_t1.py", "\"value\": f\"{pct_text(r['median_pct'])} mediana\"", "\"value\": f\"{num(r['median_pct'], 1)}° mediana\"", "KPI de relevo errado"),
    ("relevo_em_porcentagem", "terrain_srtm.py", "('suave ondulado','3–8%',3.0,8.0),('ondulado','8–20%',8.0,20.0)", "('suave ondulado','3–8%',3.0,13.0),('ondulado','8–20%',13.0,20.0)", "classes de relevo fora da Embrapa"),
    ("relevo_em_porcentagem", "terrain_srtm.py", "(vals>=LEGAL_RESTRICTED_PCT)&(vals<=LEGAL_APP_PCT)", "(vals>=LEGAL_RESTRICTED_PCT)", "APP (art. 4º, V), não uso restrito"),
    ("relevo_em_porcentagem", "terra_verdade_t1.py", "    parts = [f\"{name} ({rng}) {pct_text(share)}\"", "    parts = [f\"{name} ({rng}) {num(share, 1)}%\"", "casa decimal"),
    ("relevo_mosaico_emenda", "terrain_srtm.py", "    zf=np.where(ok,z,0.0).astype('float32')\n", "    zf=np.where(ok,z,float(np.median(z[ok]))).astype('float32');ok=np.ones_like(ok)\n", "degrau falso"),
    ("relevo_mosaico_emenda", "terrain_srtm.py", "    counted=inside&full\n", "    counted=inside\n", "degrau falso"),
    ("relevo_mosaico_emenda", "terrain_srtm.py", "    vals=mosaic[inside&ok]\n", "    vals=mosaic[inside]\n", "pixel contado duas vezes"),
    ("relevo_mosaico_emenda", "terrain_srtm.py", "'slope_median_pct':round(float(np.median(vals)),2),", "'slope_median_pct':round(float(np.mean(vals)),2),", "mediana do imóvel inteiro"),
    ("relevo_mosaico_emenda", "terra_verdade_t1.py", "    return \"not_found\" if terrain.get(\"state\") == \"not_found\" else \"pending\"", "    return \"pending\"", "limite do método virou pendente"),
    ("relevo_mosaico_emenda", "terra_verdade_t1.py", "\"value\": \"CONSULTA PENDENTE\", \"note\": \"modelo de elevação não respondeu nesta emissão\", \"status\": \"CONSULTA PENDENTE\", \"level\": \"attention\"",
     "\"value\": \"Plano\", \"note\": \"modelo de elevação\", \"status\": \"CONSULTADA\", \"level\": \"ok\"", "pintado de resposta"),
    ("relevo_horn_ruido", "terrain_srtm.py", "    dzdx=((c+2*f+i)-(a+2*d+g))/(8.0*ew[:,None].astype('float32'))\n    dzdy=((g+2*h+i)-(a+2*b+c))/(8.0*ns[:,None].astype('float32'))\n",
     "    dzdx=(f-d)/(2.0*ew[:,None].astype('float32'))\n    dzdy=(h-b)/(2.0*ns[:,None].astype('float32'))\n", "deixou de ser plano"),
    ("sem_maximo_de_percentil", "terrain_srtm.py", "        'slope_classes':rows_out,\n",
     "        'slope_max_pct':round(float(np.percentile(vals,99.5)),2),\n        'slope_classes':rows_out,\n", "com 'máximo'"),
    # fontes na cadeia
    ("soilgrids_power_fora", "report_extras_perf_v30.py", "asyncio.to_thread(v13.query_soil_texture,geom,None,time.monotonic()+15.0),16)", "asyncio.to_thread(v13.query_soilgrids_wcs,geom),16)", "SoilGrids ainda na cadeia"),
    ("soilgrids_power_fora", "portal_property_tabs.py", "'drought':withhold_rain_comparison(build_drought_screening(recent,clim))", "'drought':build_drought_screening(recent,clim)", "sem retenção"),
    ("soilgrids_power_fora", "terra_verdade_t1.py", "            water[\"drought_screening\"] = withhold_rain_comparison(water.get(\"drought_screening\"))", "            pass", "payload guarda a comparação"),
    ("caminho_nacional_sem_filtro_mg", "solo_nacional_t1.py", "    t0 = time.monotonic()\n    deadline = t0 + (WFS_DEADLINE_S if deadline_s is None",
     "    from shapely.geometry import shape as _shape\n    _w, _s, _e, _n = _shape(car_geometry).bounds\n    if _e < -51.2 or _w > -39.7 or _n < -23.0 or _s > -14.1:\n        return {\"ok\": False, \"state\": \"not_applicable\"}\n    t0 = time.monotonic()\n    deadline = t0 + (WFS_DEADLINE_S if deadline_s is None",
     "fora de MG sem solo nacional"),
    ("caminho_nacional_sem_filtro_mg", "report_api.py", "        _terra_nacional(geometry),\n", "        _safe_async('terra_nacional',asyncio.sleep(0,{'ok':False}),'IBGE BDiA + Embrapa GeoInfo'),\n", "não chama a base nacional"),
    ("caminho_nacional_sem_filtro_mg", "terra_verdade_t1.py", "    if _state_layer_only_elsewhere(result):", "    if False:", "camada estadual de MG como consulta pendente"),
    # falhas do solo
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "    return {\"state\": \"pending\", \"layer\": layer, \"detail\": last, \"ms\": ms()}",
     "    return {\"state\": \"not_found\", \"layer\": layer, \"units\": [], \"coverage_pct\": 0.0, \"detail\": last, \"ms\": ms()}", "fonte fora do ar"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "            if len(feats) >= FEATURE_CAP:", "            if False:", "teto de feições"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "declared > len(feats):", "False and declared > len(feats):", "numberMatched"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "            raise GeometryUnusable(f\"mancha:{type(exc).__name__}\") from exc", "            continue", "não se deixa cruzar"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "    if not geom.is_valid:\n        geom = make_valid(geom)", "    if False:\n        geom = make_valid(geom)", "make_valid"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "\"bbox\": f\"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326\"", "\"bbox\": f\"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::4326\"", "lat,lon"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "        wait(list(futures.values()), timeout=max(0.0, deadline - time.monotonic()))", "        wait(list(futures.values()))", "prazo total"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "            chunks.append(chunk)\n            if time.monotonic() > deadline:\n                raise TimeoutError(\"prazo_total\")", "            chunks.append(chunk)", "pinga bytes"),
    ("solo_falhas_viram_pendente", "report_api.py", "asyncio.wait_for(asyncio.to_thread(query_solo_nacional,geometry),timeout=SOLO_NACIONAL_OUTER_S)", "asyncio.to_thread(query_solo_nacional,geometry)", "sem limite"),
    ("solo_falhas_viram_pendente", "report_truth_guard_v16.py", "get('pedologia') or {}).get('state') in ('found','not_found'),", "get('pedologia') or {}).get('state') in ('found','not_found','pending'),", "guardião conta pendente"),
    ("solo_falhas_viram_pendente", "report_truth_guard_v16.py", "'declividade':(result.get('terrain_srtm') or {}).get('ok') is True or", "'declividade':True or", "guardião conta pendente"),
    ("solo_falhas_viram_pendente", "terra_verdade_t1.py", "            item[\"badge\"] = \"CONSULTADO\" if all_answered else \"PARCIAL\"", "            item[\"badge\"] = \"CONSULTADO\"", "selo verde"),
    ("solo_falhas_viram_pendente", "terra_verdade_t1.py", "                cat[\"risk\"] = \"TRIAGEM DISPONÍVEL\" if all_answered else \"CONSULTA PENDENTE\"", "                cat[\"risk\"] = cat.get(\"risk\")", "herdou risco antigo"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "    if ped.get(\"state\") == \"pending\":\n        rows.append([\"Tipo de solo no mapa oficial\", PENDING_TEXT])", "    if False:\n        rows.append([\"Tipo de solo no mapa oficial\", PENDING_TEXT])", "linha de solo pendente sumiu"),
    ("solo_falhas_viram_pendente", "solo_nacional_t1.py", "    elif ero.get(\"state\") == \"pending\":\n        rows.append([\"Erodibilidade do solo (mapa oficial)\", PENDING_TEXT])", "    elif False:\n        pass", "erodibilidade pendente sumiu"),
    # partes
    ("partes_do_imovel", "solo_nacional_t1.py", "    if share >= 99.5:\n        return \"todo o imóvel\"", "    if share >= 95.0:\n        return \"todo o imóvel\"", "96/4"),
    ("partes_do_imovel", "solo_nacional_t1.py", "    if len(order) > need and abs(rems[order[need - 1]] - rems[order[need]]) < 1e-9:", "    if False:", "15 manchas iguais"),
    ("partes_do_imovel", "solo_nacional_t1.py", "    main = [g for g in kept if g[1] >= MINOR_PCT]", "    main = list(kept)", "96/4"),
    ("partes_do_imovel", "solo_nacional_t1.py", "No imóvel, {pct_text(relief['flat_gentle_pct'])} do terreno", "No imóvel, {num(relief['flat_gentle_pct'], 1)}% do terreno", "formatos diferentes"),
    ("partes_do_imovel", "solo_nacional_t1.py", "        rows.append([\"Escala do mapa\", SCALE_NOTE])", "        pass", "escala do mapa sumiu"),
    # textura
    ("textura_prazo_e_trava", "solo_nacional_t1.py", "    ok = (c + a + s) > 0", "    ok = (c + a + s) >= 0", "pixel 0/0/0"),
    ("textura_prazo_e_trava", "solo_nacional_t1.py", "    got = _TEXTURE_LOCK.acquire(timeout=wait_s) if wait_s > 0 else _TEXTURE_LOCK.acquire(blocking=False)", "    got = _TEXTURE_LOCK.acquire()", "sem prazo"),
    ("textura_prazo_e_trava", "solo_nacional_t1.py", "            if time.monotonic() > deadline:\n                raise TimeoutError(\"prazo_da_textura\")\n", "", "prazo entre arquivos"),
    ("textura_prazo_e_trava", "live_report_adapter_v13.py", "in ('found','not_found','pending') else 'pending'", "in ('found','not_found','pending') else 'not_found'", "timeout do slot"),
    ("textura_prazo_e_trava", "report_extras_perf_v30.py", "asyncio.to_thread(v13.query_soil_texture,geom,None,time.monotonic()+15.0),16)", "asyncio.to_thread(v13.query_soil_texture,geom),16)", "prazo contado do pedido"),
    # chuva
    ("chuva_estimativa_regional", "terra_verdade_t1.py", "            rows.append([label, f\"{num(total)} mm ({RAIN_REGIONAL_TAG})\"])", "            rows.append([label, f\"{total} mm\"])", "Precipitação acumulada"),
    ("chuva_estimativa_regional", "terra_verdade_t1.py", "            rows.append([label, _MM_DAY.sub(\"\", str(row[1]) if len(row) > 1 else \"\")])", "            rows.append([label, str(row[1]) if len(row) > 1 else \"\"])", "Climatologia"),
    ("chuva_estimativa_regional", "portal_property_tabs.py", ",fonte_recente_grade_grossa:'", ",fonte_recente_grade_grossa_off:'", "retida de propósito"),
    ("chuva_estimativa_regional", "static/novo/app.js", " (estimativa regional da NASA POWER, grade de ~50 km; não é medição no imóvel).');", ".');", "/novo"),
    # crédito
    ("mcr_datas_da_5303", "mcr_regra_t1.py", "(float(\"-inf\"), date(2028, 1, 3), \"até 4 módulos fiscais\")", "(float(\"-inf\"), date(2027, 1, 4), \"até 4 módulos fiscais\")", "data errada"),
    ("mcr_datas_da_5303", "mcr_regra_t1.py", "; não é proibição automática.", "; é vedação ao crédito.", "regra do MCR incompleta"),
    ("mcr_datas_da_5303", "mcr_regra_t1.py", "COLLECTIVE_TYPES = {\"AST\", \"PCT\"}", "COLLECTIVE_TYPES = {\"AST\"}", "PCT"),
    ("mcr_datas_da_5303", "prodes_reading_f2.py", "\"rows\": _rows(reading, texts, mcr_regra_t1.basis_text(props.get(\"m_fiscal\"), props.get(\"tipo_imovel\"))),", "\"rows\": _rows(reading, texts),", "sem a data do porte"),
    ("mcr_datas_da_5303", "live_report_adapter_v8.py", "['Base regulatória',mcr_regra_t1.basis_text(props.get('m_fiscal'),props.get('tipo_imovel'))]", "['Base regulatória','MCR 2-9: verificação da supressão após 31/07/2019.']", "caminho v8"),
    ("mcr_datas_da_5303", "report_narrative.py", "why.append(mcr_regra_t1.why_text() + ' O Raio-X não mistura tudo em um único número.')", "why.append('Para crédito rural, o MCR exige atenção especial à supressão de vegetação nativa posterior a 31/07/2019.')", "narrativa com a regra antiga"),
    # PDF real
    ("relatorio_real", "live_report_adapter_v20.py", "        payload = terra_verdade_t1.apply_payload(payload, ctx.get(\"result\") or {})\n", "", "(a)"),
    ("relatorio_real", "terra_verdade_t1.py", "    prod[\"erosion_rows\"] = []", "    pass", "(d)"),
    ("relatorio_real", "terra_verdade_t1.py", "        prod[\"landcover_rows\"] = [r for r in landcover", "        prod[\"landcover_rows_off\"] = [r for r in landcover", "(i)"),
    ("relatorio_real", "terra_verdade_t1.py", "        if label.startswith(RAIN_COMPARISON_PREFIX) or label == RAIN_BASIS_LABEL:\n            continue\n", "", "(e)"),
    ("relatorio_real", "terra_verdade_t1.py", "\"note\": f\"9 de cada 10 pontos até {pct_text(r['p90_pct'])}\"", "\"note\": f\"inclinação máxima {pct_text(r['p90_pct'])}\"", "(b)"),
    ("relatorio_real", "report_truth_guard_v16.py", "'declividade':(result.get('terrain_srtm') or {}).get('ok') is True or (result.get('terrain_srtm') or {}).get('state')=='not_found',", "'declividade':slope.get('ok') is True,", "(g)"),
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
        env["T1_GATE_SRC_ROOT"] = td
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
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    failures = []
    for name in RULES:
        if only and name not in only:
            continue
        ok, why = run_rule(name)
        print(f"PASS {name}" if ok else f"FAIL {name}: {why}", flush=True)
        if not ok:
            failures.append(name)
    os.environ["T1_GATE_TILES"] = str(synthetic_tiles())  # os processos filhos usam as mesmas folhas sintéticas
    for rule, rel, old, new, expect in MUTANTS:
        if only and rule not in only:
            continue
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

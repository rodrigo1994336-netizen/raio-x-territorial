"""T1 · terra-verdade: solo, aptidão, erodibilidade e textura com base nacional.

Por que existe (estudo F1, seção 0, E2/E3/E6):

* solo, aptidão e erosão só existiam em Minas (IDE-Sisema, filtro pela caixa de MG). A base
  nacional equivalente responde para o Brasil inteiro em menos de 2 s:
  - IBGE BDiA ``BDIA:pedo_area`` — Base Contínua de Pedologia, escala 1:250.000;
  - Embrapa GeoInfo ``geonode:aptidao_agr_bra`` — aptidão agrícola nas mesmas unidades de mapa;
  - Embrapa GeoInfo ``geonode:bra_erodibilidade_2024_sirgas2000`` — erodibilidade do solo (2024).
* argila e "textura" vinham do SoilGrids (modelo global de 250 m, num ponto no centro do imóvel):
  em Curvelo/MG davam 32,7 % de argila contra 47–49 % do MapBiomas Solo (30 m) no polígono.
  A textura agora sai do MapBiomas Solo (coleção 2, 0–30 cm) dentro do imóvel, classificada pelos
  grupamentos texturais do SiBCS; SoilGrids não é mais consultado no relatório.
* "risco potencial de erosão: muito baixo" aparecia sozinho. O conceito certo nesta base é a
  erodibilidade (fragilidade do solo), dita junto com o que ela não é.

Regras:

* mapa regional descreve, não mede: percentual de unidade de mapa sai arredondado em partes de 10
  ("cerca de 6 de cada 10 partes"), nunca "57,1 %"; a escala é dita. "Todo o imóvel" só com 99,5 % ou
  mais; acima de 90 % é "quase todo"; partes iguais recebem o mesmo texto; mancha abaixo de 5 % vira
  "outras manchas menores" e lasca de borda abaixo de 0,5 % (menor que a precisão do mapa) não é citada;
* fonte que não respondeu, resposta cortada (``numberMatched`` maior que o que veio ou teto de feições)
  ou geometria que não se deixa cruzar -> ``pending`` (tenta de novo na próxima emissão); resposta sem
  unidade no imóvel -> ``not_found`` e a linha não aparece (campo vazio não aparece; zero não é ausência);
* geometria inválida (CAR com laço, comum no SICAR, ou mancha inválida) é consertada com ``make_valid``
  antes do cruzamento;
* cada fonte tem prazo próprio e a consulta inteira tem prazo total; nenhuma espera a outra.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from external_process_lifecycle import in_current_scope
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

IBGE_WFS = "https://geoservicos.ibge.gov.br/geoserver/ows"
EMBRAPA_WFS = "https://geoinfo.dados.embrapa.br/geoserver/ows"
LAYERS = {
    "pedologia": (IBGE_WFS, "BDIA:pedo_area"),
    "aptidao": (EMBRAPA_WFS, "geonode:aptidao_agr_bra"),
    "erodibilidade": (EMBRAPA_WFS, "geonode:bra_erodibilidade_2024_sirgas2000"),
}
FEATURE_CAP = 200  # 1:250.000: unidades de km²; bater no teto quer dizer resposta cortada -> pendente
# Prazo por camada. Medido de um computador no Brasil em 15/09/2026: 0,3–1,6 s por camada nos três
# imóveis de prova (MG, PA, MT). O prazo cobre uma nova tentativa sem segurar a análise.
# WFS_DEADLINE_S é o prazo TOTAL da consulta (as três camadas em paralelo, com a nova tentativa e com
# servidor que manda bytes aos poucos): passou dele, a camada que não terminou é "pending".
WFS_TIMEOUT_S = 12.0
WFS_DEADLINE_S = 20.0
SLIVER_IGNORE_PCT = 0.5  # lasca de borda: menor que a precisão de um mapa 1:250.000, não é citada
MINOR_PCT = 5.0  # abaixo disso a mancha não ganha frase própria: "outras manchas menores"
MINOR_TEXT = "outras manchas menores (menos de 5% do imóvel cada)"

SOURCE_PEDOLOGIA = "IBGE — Base Contínua de Pedologia (BDiA), escala 1:250.000"
SOURCE_APTIDAO = "Embrapa Solos — aptidão agrícola do Brasil (GeoInfo), unidades 1:250.000"
SOURCE_ERODIBILIDADE = "Embrapa Solos — erodibilidade dos solos do Brasil, 2024 (GeoInfo)"
SOURCE_TEXTURA = "MapBiomas Solo, coleção 2 — granulometria 0–30 cm, 30 m"
SCALE_NOTE = ("Mapa regional (1:250.000): descreve a região do imóvel e não mede o imóvel; "
              "cada mancha do mapa pode misturar mais de um tipo de solo.")
ERODIBILITY_MEANING = ("Erodibilidade é a fragilidade do próprio solo à erosão. O risco no terreno também "
                       "depende da inclinação, da chuva e da cobertura do solo.")
TEXTURE_NOTE = ("Estimativa de modelo feita com amostras de solo brasileiras; não substitui análise de "
                "laboratório.")
PENDING_TEXT = "Consulta pendente."

# MapBiomas Solo (coleção 2): GeoTIFF nacional em blocos de 256 px, sem pirâmide, uint8 em %.
MAPBIOMAS_SOLO = "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/collection_9/soil_2/"
TEXTURE_FILES = {
    "argila": MAPBIOMAS_SOLO + "mbsoil_c02_granulometry_clay_percent/mbsoil_c02_granulometry_clay_percent_clay_000_030cm.tif",
    "areia": MAPBIOMAS_SOLO + "mbsoil_c02_granulometry_sand_percent/mbsoil_c02_granulometry_sand_percent_sand_000_030cm.tif",
    "silte": MAPBIOMAS_SOLO + "mbsoil_c02_granulometry_silt_percent/mbsoil_c02_granulometry_silt_percent_silt_000_030cm.tif",
}
TEXTURE_WINDOW_GUARD_PX = 4_000_000
# Leitura em série e com trava: três threads abrindo arquivos diferentes ao mesmo tempo travaram o GDAL
# numa medição local (15/09/2026). Em série, frio: 4,9–8,7 s para os três arquivos; imóvel novo com os
# arquivos já abertos: 3,7–4,4 s (revisão de 15/09/2026); o mesmo polígono de novo: 23–30 ms.
_TEXTURE_LOCK = threading.Lock()
TEXTURE_LOCK_WAIT_S = 14.0
# Prazo interno da leitura: 1 s antes do slot de 16 s do relatório (report_extras_perf_v30), contado de
# quando o pedido foi feito (a fila de threads come parte do slot). Sem prazo, a thread abandonada pelo
# slot seguia segurando a trava e o relatório seguinte esperava até 14 s.
TEXTURE_DEADLINE_S = 15.0
TEXTURE_MIN_READ_S = 4.4  # imóvel novo com os arquivos abertos (pior medida); sem esse tempo, nem espera a trava

_TEXTURE_ORDER = ("muito argilosa", "argilosa", "média", "siltosa", "arenosa")


class TextureWindowTooLarge(ValueError):
    """Imóvel maior que a janela segura de leitura: limite do método, não consulta pendente."""


class GeometryUnusable(ValueError):
    """Geometria que não se deixa cruzar nem depois do make_valid: consulta pendente, nunca "nenhuma unidade"."""


class TruncatedResponse(ValueError):
    """O servidor disse que há mais feições do que mandou: resposta cortada, consulta pendente."""
_APTITUDE_SKIP = re.compile(r"n[ãa]o\s*desm|naodesm|^\s*(?:água|agua|urbano|área urbana)\s*$", re.I)


# ------------------------------------------------------------------ números e partes
def num(value: Any, digits: int = 0) -> str:
    try:
        v = Decimal(str(float(value))).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    whole, _, frac = f"{abs(v):f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + ".".join(groups) + ("," + frac if digits > 0 else "")


def pct_text(value: Any) -> str:
    """Percentual inteiro para o cliente, sem precisão falsa e sem apagar o que é pequeno."""
    try:
        v = float(value)
    except Exception:
        return ""
    if v != v:
        return ""
    if v <= 0:
        return "0%"
    if v < 0.5:
        return "menos de 1%"
    if 99.5 <= v < 100:
        return "mais de 99%"
    return num(v) + "%"


def tenths(shares: list[float]) -> list[int]:
    """Partes de 10 pelo maior resto: a soma mostrada nunca passa de 10.

    Empate no corte (restos iguais, como 15 manchas de 6,67 %) não é desempatado pela ordem da lista:
    nenhuma das empatadas ganha a parte extra, para que partes iguais tenham o mesmo texto.
    """
    vals = [max(0.0, float(s)) for s in shares]
    total = sum(vals)
    if total <= 0:
        return [0 for _ in shares]
    # shares are % of the property: 57,1 % -> 5,71 parts of 10; the whole shown never exceeds the mapped part
    raw = [v / 10.0 for v in vals]
    target = int(Decimal(str(min(total, 100.0) / 10.0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    base = [int(math.floor(x + 1e-9)) for x in raw]
    need = target - sum(base)
    if need <= 0:
        return base
    rems = [raw[i] - base[i] for i in range(len(raw))]
    order = sorted(range(len(raw)), key=lambda i: -rems[i])
    chosen = order[:need]
    if len(order) > need and abs(rems[order[need - 1]] - rems[order[need]]) < 1e-9:
        cut = rems[order[need - 1]]
        chosen = [i for i in chosen if rems[i] - cut > 1e-9]
    for i in chosen:
        base[i] += 1
    return base


def share_phrase(share_pct: float, n: int) -> str:
    """Frase da parte do imóvel. "Todo" só com 99,5 % ou mais: 97 % com 3 % de água não é "todo"."""
    share = float(share_pct or 0.0)
    if share >= 99.5:
        return "todo o imóvel"
    if share > 90.0:
        return "quase todo o imóvel (mais de 9 de cada 10 partes)"
    if n >= 1:
        return f"cerca de {min(n, 9)} de cada 10 partes do imóvel"
    return "pequena parte do imóvel (menos de 1 de cada 10 partes)"


def split_minor(groups: list[tuple[str, float]]) -> tuple[list[tuple[str, float]], bool]:
    """Separa as manchas com frase própria (5 % ou mais) das menores; lasca abaixo de 0,5 % some."""
    kept = [g for g in groups if g[1] >= SLIVER_IGNORE_PCT]
    main = [g for g in kept if g[1] >= MINOR_PCT]
    if not main:
        return kept, False
    return main, len(main) < len(kept)


# ------------------------------------------------------------------ consulta WFS
def _http_json(url: str, params: dict[str, str], deadline: float) -> Any:
    """GET com prazo total: o tempo limite do httpx vale por leitura, então quem manda bytes aos poucos
    é cortado pelo relógio entre os pedaços."""
    import httpx

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("prazo_total")
    timeout = httpx.Timeout(min(WFS_TIMEOUT_S, remaining), connect=min(5.0, remaining))
    chunks = []
    with httpx.stream("GET", url, params=params, timeout=timeout,
                      headers={"User-Agent": "Raio-X-Territorial/t1-solo-nacional"}) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            chunks.append(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError("prazo_total")
    return json.loads(b"".join(chunks))


def _wfs_features(url: str, layer: str, bounds: tuple[float, float, float, float], get=None, deadline: float | None = None) -> list[dict[str, Any]]:
    minx, miny, maxx, maxy = bounds
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
              "outputFormat": "application/json", "count": str(FEATURE_CAP),
              "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326"}
    if get is None:
        limit = deadline if deadline is not None else time.monotonic() + WFS_DEADLINE_S

        def get(u, p):
            return _http_json(u, p, limit)
    data = get(url, params)
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError("wfs_sem_features")
    feats = data["features"]
    for key in ("numberMatched", "totalFeatures"):
        declared = data.get(key)
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > len(feats):
            raise TruncatedResponse(f"{key}={declared}>{len(feats)}")
    return feats


def _polygonal(geom):
    """Parte de área da geometria, consertada com make_valid quando inválida (laço, auto-interseção)."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    if geom is None or geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = make_valid(geom)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon)) and not g.is_empty]
    return unary_union(parts) if parts else Polygon()


def units_in_property(features: list[dict[str, Any]], car_geometry: Any) -> list[dict[str, Any]]:
    """Unidades de mapa que cruzam o imóvel, com a parte do imóvel (planar em graus: razão, não área).

    Erro de geometria levanta ``GeometryUnusable``: engolir o erro e seguir fazia a camada virar
    "nenhuma unidade no imóvel" (not_found) e o selo dizer CONSULTADO.
    """
    from shapely.geometry import shape

    try:
        car = _polygonal(shape(car_geometry))
    except Exception as exc:  # noqa: BLE001 - geometria do CAR ilegível
        raise GeometryUnusable(f"car:{type(exc).__name__}") from exc
    if car is None or car.is_empty or car.area <= 0:
        raise GeometryUnusable("car_sem_area")
    by_unit: dict[str, dict[str, Any]] = {}
    for feat in features:
        try:
            geom = _polygonal(shape(feat.get("geometry")))
            if geom is None or geom.is_empty or not geom.intersects(car):
                continue
            share = geom.intersection(car).area / car.area * 100.0
        except Exception as exc:  # noqa: BLE001 - mancha que não se deixa cruzar: resposta incompleta
            raise GeometryUnusable(f"mancha:{type(exc).__name__}") from exc
        if share <= 0:
            continue
        props = {k: v for k, v in (feat.get("properties") or {}).items() if not isinstance(v, (dict, list))}
        key = str(props.get("nom_unidad") or props.get("cod_um2") or props.get("legenda") or len(by_unit))
        item = by_unit.setdefault(key, {"unit": key, "share_pct": 0.0, "properties": props})
        item["share_pct"] += share
    out = sorted(by_unit.values(), key=lambda x: -x["share_pct"])
    for x in out:
        x["share_pct"] = round(min(x["share_pct"], 100.0), 2)
    return out


def _layer(key: str, car_geometry: Any, get=None, deadline: float | None = None) -> dict[str, Any]:
    from shapely.geometry import shape

    url, layer = LAYERS[key]
    t0 = time.monotonic()
    deadline = deadline if deadline is not None else t0 + WFS_DEADLINE_S
    ms = lambda: round((time.monotonic() - t0) * 1000)  # noqa: E731
    last = "sem_resposta"
    try:
        bounds = shape(car_geometry).bounds
    except Exception as exc:  # noqa: BLE001 - geometria ilegível não é "nenhuma unidade"
        return {"state": "pending", "layer": layer, "detail": f"geometria:{type(exc).__name__}", "ms": ms()}
    for attempt in (1, 2):
        if time.monotonic() >= deadline:
            last = "prazo_total"
            break
        try:
            feats = _wfs_features(url, layer, bounds, get=get, deadline=deadline)
            if len(feats) >= FEATURE_CAP:
                return {"state": "pending", "layer": layer, "detail": "resposta_no_teto", "ms": ms()}
            units = units_in_property(feats, car_geometry)
            return {"state": "found" if units else "not_found", "layer": layer, "units": units,
                    "coverage_pct": round(min(sum(u["share_pct"] for u in units), 100.0), 2),
                    "ms": ms(), "attempts": attempt}
        except (GeometryUnusable, TruncatedResponse) as exc:  # repetir não muda a resposta
            return {"state": "pending", "layer": layer, "detail": f"{type(exc).__name__}:{exc}"[:160], "ms": ms()}
        except Exception as exc:  # noqa: BLE001 - fonte fora do ar vira pendência, nunca "nenhum"
            last = type(exc).__name__
    return {"state": "pending", "layer": layer, "detail": last, "ms": ms()}


def query_solo_nacional(car_geometry: Any, get=None, deadline_s: float | None = None) -> dict[str, Any]:
    """Pedologia (IBGE), aptidão e erodibilidade (Embrapa) no polígono do CAR, em paralelo e com prazo total."""
    t0 = time.monotonic()
    deadline = t0 + (WFS_DEADLINE_S if deadline_s is None else float(deadline_s))
    ex = ThreadPoolExecutor(max_workers=len(LAYERS), thread_name_prefix="rx-t1-solo")
    layers: dict[str, dict[str, Any]] = {}
    try:
        futures = {k: ex.submit(in_current_scope(_layer), k, car_geometry, get, deadline) for k in LAYERS}
        wait(list(futures.values()), timeout=max(0.0, deadline - time.monotonic()))
        for k, f in futures.items():
            if not f.done():
                layers[k] = {"state": "pending", "layer": LAYERS[k][1], "detail": "prazo_total",
                             "ms": round((time.monotonic() - t0) * 1000)}
                continue
            try:
                layers[k] = f.result()
            except Exception as exc:  # noqa: BLE001 - erro inesperado é pendência
                layers[k] = {"state": "pending", "layer": LAYERS[k][1], "detail": type(exc).__name__}
    finally:
        # não espera thread presa: a camada atrasada já foi dada como pendente e a leitura dela é cortada
        # pelo prazo entre os pedaços da resposta
        ex.shutdown(wait=False, cancel_futures=True)
    states = {k: v.get("state") for k, v in layers.items()}
    print(f"RX_T1_SOLO_NACIONAL={states}:{round((time.monotonic() - t0) * 1000)}ms", flush=True)
    return {"ok": any(s in ("found", "not_found") for s in states.values()), "version": "T1", **layers}


def pending_result(detail: str) -> dict[str, Any]:
    """Todas as camadas pendentes (prazo total estourado antes de a thread começar, erro inesperado)."""
    return {"ok": False, "version": "T1", **{k: {"state": "pending", "layer": layer, "detail": detail} for k, (_url, layer) in LAYERS.items()}}


# ------------------------------------------------------------------ textura (MapBiomas Solo)
def sibcs_texture_group(clay: float, sand: float, silt: float) -> str:
    """Grupamento textural do SiBCS pela composição granulométrica (%).

    arenosa = classes areia e areia franca (silte + 2 x argila < 30); média = menos de 35 % de argila e
    mais de 15 % de areia, fora das arenosas; argilosa = 35 a 60 % de argila; muito argilosa = mais de
    60 %; siltosa = menos de 35 % de argila e menos de 15 % de areia.
    """
    if clay > 60:
        return "muito argilosa"
    if clay >= 35:
        return "argilosa"
    if silt + 2 * clay < 30:
        return "arenosa"
    if sand < 15:
        return "siltosa"
    return "média"


def texture_summary(clay, sand, silt) -> dict[str, Any]:
    import numpy as np

    c, a, s = (np.asarray(x, dtype="float64").ravel() for x in (clay, sand, silt))
    ok = (c + a + s) > 0  # os três em zero = fora do mapeamento (água, área urbana), não "0 % de argila"
    c, a, s = c[ok], a[ok], s[ok]
    if c.size == 0:
        return {"state": "not_found", "pixels": 0}
    groups: dict[str, int] = {}
    for ci, ai, si in zip(c.tolist(), a.tolist(), s.tolist()):
        g = sibcs_texture_group(ci, ai, si)
        groups[g] = groups.get(g, 0) + 1
    shares = {g: round(n / c.size * 100.0, 2) for g, n in groups.items()}
    q = lambda v, p: float(np.percentile(v, p))  # noqa: E731
    return {
        "state": "found", "pixels": int(c.size), "group_shares_pct": shares,
        "clay": {"median": float(np.median(c)), "p10": q(c, 10), "p90": q(c, 90)},
        "sand": {"median": float(np.median(a)), "p10": q(a, 10), "p90": q(a, 90)},
        "silt": {"median": float(np.median(s)), "p10": q(s, 10), "p90": q(s, 90)},
    }


def query_mapbiomas_solo(car_geometry: Any, reader=None, deadline: float | None = None) -> dict[str, Any]:
    """Argila, areia e silte (0–30 cm) dentro do polígono; leitura em série, com trava e prazo.

    ``deadline`` (relógio monotônico) vem de quem pediu, para contar a fila de threads; sem ele, o prazo
    conta daqui. Sem tempo para uma leitura inteira, nem espera a trava: devolve pendente na hora.
    """
    t0 = time.monotonic()
    deadline = deadline if deadline is not None else t0 + TEXTURE_DEADLINE_S
    base = {"ok": False, "source": SOURCE_TEXTURA, "version": "T1"}
    wait_s = min(TEXTURE_LOCK_WAIT_S, deadline - time.monotonic() - TEXTURE_MIN_READ_S)
    got = _TEXTURE_LOCK.acquire(timeout=wait_s) if wait_s > 0 else _TEXTURE_LOCK.acquire(blocking=False)
    if not got:
        return {**base, "state": "pending", "detail": "leitura_anterior_ocupada", "ms": round((time.monotonic() - t0) * 1000)}
    try:
        from shapely.geometry import shape

        car = shape(car_geometry)
        arrays = {}
        for key, url in TEXTURE_FILES.items():
            if time.monotonic() > deadline:
                raise TimeoutError("prazo_da_textura")
            arrays[key] = (reader or _read_polygon)(url, car)
        summary = texture_summary(arrays["argila"], arrays["areia"], arrays["silte"])
        ms = round((time.monotonic() - t0) * 1000)
        print(f"RX_T1_MAPBIOMAS_SOLO={summary.get('state')}:{ms}ms:pixels={summary.get('pixels')}", flush=True)
        return {**base, "ok": summary["state"] == "found", **summary, "ms": ms}
    except TextureWindowTooLarge:
        # campo vazio não aparece: a linha some, sem "pendente" que nunca vai se resolver
        return {**base, "state": "not_found", "detail": "area_acima_da_janela_de_leitura", "ms": round((time.monotonic() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001 - fonte fora do ar vira pendência
        ms = round((time.monotonic() - t0) * 1000)
        print(f"RX_T1_MAPBIOMAS_SOLO=pending:{ms}ms:{type(exc).__name__}", flush=True)
        return {**base, "state": "pending", "detail": type(exc).__name__, "ms": ms}
    finally:
        _TEXTURE_LOCK.release()


_TEXTURE_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                    GDAL_HTTP_TIMEOUT="10", GDAL_HTTP_CONNECTTIMEOUT="5", GDAL_HTTP_MAX_RETRY="1", VSI_CACHE="TRUE")
# Arquivo aberto uma vez por processo: o cabeçalho destes GeoTIFFs (sem pirâmide, ~340 mil blocos) custa
# 7–9 s para abrir e o cache de /vsicurl é dividido com as outras leituras do relatório (Sentinel, MapBiomas
# cobertura), então reabrir a cada relatório pagava o frio de novo (medido no servidor local: 8,9 s e 7,0 s
# em dois relatórios seguidos). Só é usado sob _TEXTURE_LOCK; erro de leitura descarta o arquivo aberto.
_DATASETS: dict[str, Any] = {}


def _dataset(url: str):
    import rasterio

    ds = _DATASETS.get(url)
    if ds is None or ds.closed:
        ds = rasterio.open("/vsicurl/" + url)
        _DATASETS[url] = ds
    return ds


def _drop_dataset(url: str) -> None:
    ds = _DATASETS.pop(url, None)
    try:
        if ds is not None:
            ds.close()
    except Exception:  # noqa: BLE001 - fechar arquivo quebrado não pode derrubar a leitura
        pass


def warm_texture_files() -> dict[str, Any]:
    """Abre os três arquivos do MapBiomas Solo antes do primeiro relatório (só o cabeçalho, sem ler pixels).

    Medido no servidor local em 15/09/2026: o primeiro relatório depois do arranque gastou 9,4 s na textura e
    esperou mais 5,8 s na fila de threads, perto do prazo de 16 s do slot. Falha aqui não é erro: o relatório
    abre o arquivo na hora, como antes.
    """
    import rasterio

    t0 = time.monotonic()
    opened = 0
    if not _TEXTURE_LOCK.acquire(timeout=TEXTURE_LOCK_WAIT_S):
        return {"opened": 0, "detail": "leitura_ocupada"}
    try:
        with rasterio.Env(**_TEXTURE_ENV):
            for url in TEXTURE_FILES.values():
                try:
                    _dataset(url)
                    opened += 1
                except Exception:  # noqa: BLE001 - aquecimento não derruba nada
                    _drop_dataset(url)
    finally:
        _TEXTURE_LOCK.release()
    ms = round((time.monotonic() - t0) * 1000)
    print(f"RX_T1_MAPBIOMAS_SOLO_WARM=opened:{opened}/{len(TEXTURE_FILES)}:{ms}ms", flush=True)
    return {"opened": opened, "ms": ms}


def warm_texture_in_background() -> None:
    threading.Thread(target=warm_texture_files, name="rx-t1-textura-aquece", daemon=True).start()


def _read_polygon(url: str, car):
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from shapely.geometry import mapping

    with rasterio.Env(**_TEXTURE_ENV):
        try:
            ds = _dataset(url)
            win = from_bounds(*car.bounds, transform=ds.transform).round_offsets().round_lengths()
            if win.width <= 0 or win.height <= 0 or win.width * win.height > TEXTURE_WINDOW_GUARD_PX:
                raise TextureWindowTooLarge("janela_fora_do_limite")
            # Sem boundless: a leitura "boundless" monta um VRT e travou a leitura remota nos testes.
            arr = ds.read(1, window=win)
            inside = geometry_mask([mapping(car)], out_shape=arr.shape, transform=ds.window_transform(win), invert=True, all_touched=False)
        except TextureWindowTooLarge:
            raise
        except Exception:
            _drop_dataset(url)
            raise
    return arr[inside]


# ------------------------------------------------------------------ leitura para o cliente
def _title(text: str) -> str:
    words = str(text or "").strip().split()
    small = {"de", "da", "do", "das", "dos", "e"}
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if re.fullmatch(r"t[ab]", lw):
            out.append(lw[0].upper() + lw[1])
        elif i and lw in small:
            out.append(lw)
        else:
            out.append("-".join(part[:1].upper() + part[1:] for part in lw.split("-")))
    return " ".join(out)


def _soil_name(props: dict[str, Any]) -> str:
    legend = str(props.get("legenda") or "")
    name = legend.split(" - ", 1)[1] if " - " in legend else legend
    return _title(name) if name else _title(" ".join(str(props.get(k) or "") for k in ("ordem", "subordem", "grande_gru")))


def _group_units(units: list[dict[str, Any]], label) -> list[tuple[str, float]]:
    agg: dict[str, float] = {}
    for u in units:
        name = label(u.get("properties") or {})
        if not name:
            continue
        agg[name] = agg.get(name, 0.0) + float(u.get("share_pct") or 0.0)
    return sorted(agg.items(), key=lambda kv: -kv[1])


def _parts_line(groups: list[tuple[str, float]], joiner=" em ") -> str:
    main, minor = split_minor(groups)
    counts = tenths([g[1] for g in main])
    pieces = [f"{name}{joiner}{share_phrase(share, n)}" for (name, share), n in zip(main, counts)]
    if minor:
        pieces.append(MINOR_TEXT)
    return "; ".join(pieces) + "."


def _aptitude_label(props: dict[str, Any]) -> str:
    symbol = str(props.get("simb_apt") or "").strip()
    legend = re.sub(r"\s+", " ", str(props.get("legenda_ap") or "")).strip()
    if _APTITUDE_SKIP.search(symbol) or _APTITUDE_SKIP.search(legend):
        return ""
    body = legend[len(symbol):].strip() if symbol and legend.startswith(symbol) else legend
    body = re.sub(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,})\b", lambda m: m.group(1).lower(), body).rstrip(".")
    body = body[:1].lower() + body[1:] if body else ""
    return f"{symbol} — {body}" if symbol and body else (body or symbol)


def _map_texture_label(props: dict[str, Any]) -> str:
    texture = re.sub(r"\s+", " ", str(props.get("textura") or "")).strip()
    return f"{_soil_name(props)}: {texture}" if texture else ""


def _source(name: str, answered_text: str, state: str | None) -> dict[str, str]:
    pending = state == "pending"
    return {"name": name, "description": PENDING_TEXT if pending else answered_text,
            "status": "INDISPONÍVEL" if pending else "CONSULTADA", "level": "attention" if pending else "ok"}


def _erodibility_label(props: dict[str, Any]) -> str:
    value = str(props.get("erod_um") or "").strip()
    return value[:1].lower() + value[1:] if value else ""


def soil_reading(solo: dict[str, Any] | None, texture: dict[str, Any] | None, relief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Linhas do relatório e resumo curto da tela, sem inventar o que a fonte não disse."""
    solo = solo if isinstance(solo, dict) else {}
    texture = texture if isinstance(texture, dict) else {}
    rows: list[list[str]] = []
    aptitude_rows: list[list[str]] = []
    sources: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    screen: dict[str, Any] = {}

    ped = solo.get("pedologia") or {}
    if ped.get("state") == "found":
        groups = _group_units(ped.get("units") or [], _soil_name)
        if groups:
            line = _parts_line(groups)
            rows.append(["Tipo de solo no mapa oficial", line])
            screen["solo"] = line
            checks.append({"factor": "Solo", "scope": "mapa regional IBGE 1:250.000", "status": "consultada", "value": line})
    if ped.get("state"):
        sources.append(_source(SOURCE_PEDOLOGIA, "Unidades de mapa que cruzam o polígono do CAR. " + SCALE_NOTE, ped.get("state")))
    if ped.get("state") == "pending":
        rows.append(["Tipo de solo no mapa oficial", PENDING_TEXT])

    tex_state = texture.get("state")
    if tex_state == "found":
        shares = texture.get("group_shares_pct") or {}
        groups = [(g, shares[g]) for g in _TEXTURE_ORDER if shares.get(g)]
        groups.sort(key=lambda kv: -kv[1])
        line = _parts_line([(f"textura {g}", s) for g, s in groups])
        clay, sand, silt = texture.get("clay") or {}, texture.get("sand") or {}, texture.get("silt") or {}
        rows.append(["Textura de 0 a 30 cm (estimativa no imóvel)", line[:1].upper() + line[1:]])
        rows.append(["Argila, areia e silte (valor típico no imóvel)",
                     f"argila {num(clay.get('median'))}% (de {num(clay.get('p10'))}% a {num(clay.get('p90'))}% em 8 de cada 10 pontos) · "
                     f"areia {num(sand.get('median'))}% · silte {num(silt.get('median'))}%. {TEXTURE_NOTE}"])
        screen["textura"] = line
        sources.append({"name": SOURCE_TEXTURA, "description": "Argila, areia e silte de 0 a 30 cm nos pixels de 30 m dentro do CAR; grupamento textural do SiBCS. " + TEXTURE_NOTE,
                        "status": "CONSULTADA", "level": "ok"})
    elif tex_state == "pending":
        rows.append(["Textura de 0 a 30 cm (estimativa no imóvel)", PENDING_TEXT])
        sources.append({"name": SOURCE_TEXTURA, "description": PENDING_TEXT, "status": "INDISPONÍVEL", "level": "attention"})
        if ped.get("state") == "found":
            described = _group_units(ped.get("units") or [], _map_texture_label)
            if described:
                rows.append(["Textura descrita no mapa oficial", "; ".join(name for name, _ in described) + "."])

    apt = solo.get("aptidao") or {}
    aptitude_classes = 0
    if apt.get("state") == "found":
        groups, minor = split_minor(_group_units(apt.get("units") or [], _aptitude_label))
        if groups:
            counts = tenths([g[1] for g in groups])
            phrases = [share_phrase(share, n) for (_name, share), n in zip(groups, counts)]
            for (name, _share), phrase in zip(groups, phrases):
                aptitude_rows.append([name[:1].upper() + name[1:], phrase[:1].upper() + phrase[1:]])
            if minor:
                aptitude_rows.append(["Outras classes", MINOR_TEXT[:1].upper() + MINOR_TEXT[1:] + "."])
            aptitude_rows.append(["Escala", "Mapa regional de aptidão (unidades 1:250.000): não enxerga detalhes de uma área deste tamanho; "
                                            "a decisão de uso pede visita técnica."])
            aptitude_classes = len(groups)
            screen["aptidao"] = "; ".join([f"{name} em {phrase}" for (name, _s), phrase in zip(groups, phrases)] + ([MINOR_TEXT] if minor else [])) + "."
            checks.append({"factor": "Aptidão agrícola", "scope": "mapa regional Embrapa", "status": "consultada", "value": screen["aptidao"]})
    elif apt.get("state") == "pending":
        aptitude_rows.append(["Aptidão agrícola", PENDING_TEXT])
    if apt.get("state"):
        sources.append(_source(SOURCE_APTIDAO, "Classes de aptidão das unidades de mapa que cruzam o CAR.", apt.get("state")))

    ero = solo.get("erodibilidade") or {}
    if ero.get("state") == "found":
        groups = _group_units(ero.get("units") or [], _erodibility_label)
        if groups:
            line = _parts_line([(f"erodibilidade {g}", s) for g, s in groups])
            text = line[:1].upper() + line[1:] + " " + ERODIBILITY_MEANING
            if relief and relief.get("flat_gentle_pct") is not None:
                text += f" No imóvel, {pct_text(relief['flat_gentle_pct'])} do terreno medido é plano ou suave ondulado (até 8% de inclinação)."
            rows.append(["Erodibilidade do solo (mapa oficial)", text])
            screen["erodibilidade"] = line[:1].upper() + line[1:]
    elif ero.get("state") == "pending":
        rows.append(["Erodibilidade do solo (mapa oficial)", PENDING_TEXT])
    if ero.get("state"):
        sources.append(_source(SOURCE_ERODIBILIDADE, "Erodibilidade das unidades de mapa que cruzam o CAR. " + ERODIBILITY_MEANING, ero.get("state")))

    if any(r[0] == "Tipo de solo no mapa oficial" and r[1] != PENDING_TEXT for r in rows):
        rows.append(["Escala do mapa", SCALE_NOTE])
    return {"soil_rows": rows, "aptitude_rows": aptitude_rows, "aptitude_classes": aptitude_classes,
            "sources": sources, "checks": checks, "screen": screen}


def compact_for_screen(solo: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resumo leve para a tela (sem geometria): estado de cada camada e as frases prontas."""
    if not isinstance(solo, dict):
        return None
    reading = soil_reading(solo, None)
    return {"version": "T1", "states": {k: (solo.get(k) or {}).get("state") for k in LAYERS}, "texts": reading["screen"]}

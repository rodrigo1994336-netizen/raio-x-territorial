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
  ("cerca de 6 de cada 10 partes"), nunca "57,1 %"; a escala é dita;
* fonte que não respondeu -> ``pending`` (tenta de novo na próxima emissão); resposta sem unidade no
  imóvel -> ``not_found`` e a linha não aparece (campo vazio não aparece; zero não é ausência);
* cada fonte tem prazo próprio; nenhuma espera a outra.
"""
from __future__ import annotations

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
WFS_TIMEOUT_S = 12.0
WFS_DEADLINE_S = 20.0

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
# numa medição local (15/09/2026). Em série, frio: 4,9–8,7 s para os três arquivos; quente: 23–30 ms.
_TEXTURE_LOCK = threading.Lock()
TEXTURE_LOCK_WAIT_S = 14.0

_TEXTURE_ORDER = ("muito argilosa", "argilosa", "média", "siltosa", "arenosa")


class TextureWindowTooLarge(ValueError):
    """Imóvel maior que a janela segura de leitura: limite do método, não consulta pendente."""
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


def tenths(shares: list[float]) -> list[int]:
    """Partes de 10 pelo maior resto: a soma mostrada nunca passa de 10."""
    total = sum(max(0.0, s) for s in shares)
    if total <= 0:
        return [0 for _ in shares]
    # shares are % of the property: 57,1 % -> 5,71 parts of 10; the whole shown never exceeds the mapped part
    raw = [max(0.0, s) / 10.0 for s in shares]
    target = int(Decimal(str(min(total, 100.0) / 10.0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    base = [int(math.floor(x)) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[: max(0, target - sum(base))]:
        base[i] += 1
    return base


def parts_text(n: int) -> str:
    if n >= 10:
        return "todo o imóvel"
    if n <= 0:
        return "pequena parte do imóvel (menos de 1 de cada 10 partes)"
    return f"cerca de {n} de cada 10 partes do imóvel"


# ------------------------------------------------------------------ consulta WFS
def _wfs_features(url: str, layer: str, bounds: tuple[float, float, float, float], get=None) -> list[dict[str, Any]]:
    minx, miny, maxx, maxy = bounds
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
              "outputFormat": "application/json", "count": str(FEATURE_CAP),
              "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326"}
    if get is None:
        import httpx

        def get(u, p):
            r = httpx.get(u, params=p, timeout=httpx.Timeout(WFS_TIMEOUT_S, connect=5.0),
                          headers={"User-Agent": "Raio-X-Territorial/t1-solo-nacional"})
            r.raise_for_status()
            return r.json()
    data = get(url, params)
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError("wfs_sem_features")
    return data["features"]


def units_in_property(features: list[dict[str, Any]], car_geometry: Any) -> list[dict[str, Any]]:
    """Unidades de mapa que cruzam o imóvel, com a parte do imóvel (planar em graus: razão, não área)."""
    from shapely.geometry import shape

    car = shape(car_geometry)
    if car.is_empty or car.area <= 0:
        return []
    by_unit: dict[str, dict[str, Any]] = {}
    for feat in features:
        try:
            geom = shape(feat.get("geometry"))
            if not geom.intersects(car):
                continue
            share = geom.intersection(car).area / car.area * 100.0
        except Exception:
            continue
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


def _layer(key: str, car_geometry: Any, get=None) -> dict[str, Any]:
    from shapely.geometry import shape

    url, layer = LAYERS[key]
    t0 = time.monotonic()
    last = "sem_resposta"
    for attempt in (1, 2):
        try:
            bounds = shape(car_geometry).bounds
            feats = _wfs_features(url, layer, bounds, get=get)
            if len(feats) >= FEATURE_CAP:
                return {"state": "pending", "layer": layer, "detail": "resposta_no_teto", "ms": round((time.monotonic() - t0) * 1000)}
            units = units_in_property(feats, car_geometry)
            return {"state": "found" if units else "not_found", "layer": layer, "units": units,
                    "coverage_pct": round(min(sum(u["share_pct"] for u in units), 100.0), 2),
                    "ms": round((time.monotonic() - t0) * 1000), "attempts": attempt}
        except Exception as exc:  # noqa: BLE001 - fonte fora do ar vira pendência, nunca "nenhum"
            last = type(exc).__name__
            if time.monotonic() - t0 > WFS_DEADLINE_S - WFS_TIMEOUT_S:
                break
    return {"state": "pending", "layer": layer, "detail": last, "ms": round((time.monotonic() - t0) * 1000)}


def query_solo_nacional(car_geometry: Any, get=None) -> dict[str, Any]:
    """Pedologia (IBGE), aptidão e erodibilidade (Embrapa) no polígono do CAR, em paralelo."""
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(LAYERS), thread_name_prefix="rx-t1-solo") as ex:
        futures = {k: ex.submit(_layer, k, car_geometry, get) for k in LAYERS}
        layers = {k: f.result() for k, f in futures.items()}
    states = {k: v.get("state") for k, v in layers.items()}
    print(f"RX_T1_SOLO_NACIONAL={states}:{round((time.monotonic() - t0) * 1000)}ms", flush=True)
    return {"ok": any(s in ("found", "not_found") for s in states.values()), "version": "T1", **layers}


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


def query_mapbiomas_solo(car_geometry: Any, reader=None) -> dict[str, Any]:
    """Argila, areia e silte (0–30 cm) dentro do polígono; leitura em série, com trava e prazo de espera."""
    t0 = time.monotonic()
    base = {"ok": False, "source": SOURCE_TEXTURA, "version": "T1"}
    if not _TEXTURE_LOCK.acquire(timeout=TEXTURE_LOCK_WAIT_S):
        return {**base, "state": "pending", "detail": "leitura_anterior_ocupada"}
    try:
        from shapely.geometry import shape

        car = shape(car_geometry)
        arrays = {}
        for key, url in TEXTURE_FILES.items():
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
    counts = tenths([g[1] for g in groups])
    pieces = [f"{name}{joiner}{parts_text(n)}" for (name, _share), n in zip(groups, counts)]
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
    if apt.get("state") == "found":
        groups = _group_units(apt.get("units") or [], _aptitude_label)
        if groups:
            counts = tenths([g[1] for g in groups])
            for (name, _share), n in zip(groups, counts):
                aptitude_rows.append([name[:1].upper() + name[1:], parts_text(n)[:1].upper() + parts_text(n)[1:]])
            aptitude_rows.append(["Escala", "Mapa regional de aptidão (unidades 1:250.000): não enxerga detalhes de uma área deste tamanho; "
                                            "a decisão de uso pede visita técnica."])
            screen["aptidao"] = "; ".join(f"{name} em {parts_text(n)}" for (name, _s), n in zip(groups, counts)) + "."
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
                text += f" No imóvel, {num(relief['flat_gentle_pct'])}% do terreno medido é plano ou suave ondulado (até 8% de inclinação)."
            rows.append(["Erodibilidade do solo (mapa oficial)", text])
            screen["erodibilidade"] = line[:1].upper() + line[1:]
    elif ero.get("state") == "pending":
        rows.append(["Erodibilidade do solo (mapa oficial)", PENDING_TEXT])
    if ero.get("state"):
        sources.append(_source(SOURCE_ERODIBILIDADE, "Erodibilidade das unidades de mapa que cruzam o CAR. " + ERODIBILITY_MEANING, ero.get("state")))

    if any(r[0] == "Tipo de solo no mapa oficial" and r[1] != PENDING_TEXT for r in rows):
        rows.append(["Escala do mapa", SCALE_NOTE])
    return {"soil_rows": rows, "aptitude_rows": aptitude_rows, "sources": sources, "checks": checks, "screen": screen}


def compact_for_screen(solo: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resumo leve para a tela (sem geometria): estado de cada camada e as frases prontas."""
    if not isinstance(solo, dict):
        return None
    reading = soil_reading(solo, None)
    return {"version": "T1", "states": {k: (solo.get(k) or {}).get("state") for k in LAYERS}, "texts": reading["screen"]}

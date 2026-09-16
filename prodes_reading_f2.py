"""F2 · leitura correta do PRODES (uma leitura só para portal e relatório).

O problema medido em Curvelo/MG (13/09/2026): as quatro interseções PRODES eram
desmatamento anual de verdade, mas só uma ficava dentro do imóvel. As outras três
eram faixas de 4 a 9 m na divisa, mais estreitas que um pixel do satélite, e mesmo
assim entravam na contagem, no risco e na frase da capa.

Critério declarado (medido, não nota):
  * conta como DENTRO DO IMÓVEL a mancha que está inteira dentro do CAR, ou cuja parte
    dentro do CAR comporta um pixel do Landsat (círculo de 30 m de diâmetro), ou cuja parte
    dentro do CAR tem 1 ha ou mais, mesmo sendo uma faixa estreita;
  * o resto é TOQUE NA DIVISA (mais estreita que um pixel E com menos de 1 ha dentro do CAR):
    aparece numa linha à parte, com área, e não entra em contagem, área, risco nem frase.
Medições (fixtures em tests/fixtures/f2_prodes_leitura): faixas de divisa reais têm
largura máxima (maior círculo inscrito) de 8,1 a 14,9 m e área de 0,03 a 0,16 ha; a menor
parte interna que não está inteira no CAR tem 69,8 m. O limiar de 30 m fica no meio, com
folga dos dois lados. O teto de 1 ha é 6 vezes a maior faixa medida e é a menor mancha que o
PRODES mapeia sozinha nos biomas fora da Amazônia: uma faixa longa com hectares de
desmatamento dentro do CAR não pode virar "nenhum desmatamento dentro do imóvel".

Outras regras:
  * camada acumulada (accumulated_deforestation_*) nunca vira ocorrência anual;
  * classe do PRODES que não é desmatamento (reservatório, queimada) não entra na contagem;
  * a mesma mancha devolvida por duas camadas (Amazônia Legal e bioma Amazônia) conta uma vez:
    mesmo ano e interseção com sobreposição acima de 95 % (não só geometria idêntica byte a byte);
  * camada anual que voltou no limite de feições do WFS está cortada: sem achado vira pendente;
  * o recorte pós-31/07/2019 continua separado; mancha do ano PRODES 2019 ou anterior com imagem
    depois de 31/07/2019 é "detectada em imagem posterior", nunca "posterior" sem ressalva;
  * área em ha com 2 casas ("< 0,01 ha" quando menor); percentual inteiro;
  * fonte que não respondeu por completo e nada achado = consulta pendente, nunca "nenhum".

Funções públicas:
  classify_prodes(prodes, car_geometry, car_area_ha)  -> leitura pura (sem rede)
  prodes_reading_payload(result)                      -> campos para o cliente, com estado
  apply_reading_to_result(result)                     -> normaliza result['prodes'] (portal)
  apply_reading_to_report_payload(payload, result)    -> reescreve o payload do relatório
  lens_from_reading(reading, fiscal_modules)          -> lente compatível com prodes_lens
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import mcr_regra_t1

try:
    import shapely
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    from pyproj import Geod, Transformer

    _GEO_OK = True
    _GEOD = Geod(ellps="GRS80")
except Exception:  # pragma: no cover - runtime sem motor geométrico
    _GEO_OK = False
    shapely = shape = transform = unary_union = Transformer = _GEOD = None

READING_VERSION = "f2-prodes-leitura-2"
PIXEL_M = 30.0
# Teto de área da faixa de divisa (ver docstring): 1 ha ou mais dentro do CAR conta como dentro.
BOUNDARY_MAX_HA = 1.0
# Duas camadas anuais que devolvem a mesma mancha: mesmo ano e sobreposição (IoU) acima disto.
DUPLICATE_IOU = 0.95
# Limite de feições pedido por camada em prodes_fast_v24 (count=2000). Resposta nesse limite está cortada.
WFS_FEATURE_LIMIT = 2000
_HALF_PIXEL_M = PIXEL_M / 2.0
# Uma forma que comporta um círculo de 30 m tem pelo menos pi * 15^2 m² de área.
_MIN_PIXEL_AREA_M2 = math.pi * _HALF_PIXEL_M ** 2
# "Mancha inteira dentro do CAR": 99,9 % da área da mancha (tolera arredondamento de vértice).
_CONTAINED_SHARE = 0.999
CREDIT_CUTOFF = date(2019, 7, 31)
MARCO_2008 = date(2008, 7, 22)
# PRODES Cerrado: até 2012 o mapeamento é bienal (contagem por ano da camada inteira:
# 2002, 2004, 2006, 2008, 2010, 2012 com 89 mil a 416 mil feições; anos ímpares 0 a 5).
_CERRADO_BIENNIAL_LAST_YEAR = 2012
_DETAIL_LIMIT = 12
_DEFORESTATION_CLASS = "DESMATAMENTO"
_CLASS_LABELS = {"RESERVATORIO": "reservatório", "QUEIMADA": "queimada"}
_BIOMES = (
    ("cerrado", "Cerrado"), ("legal-amz", "Amazônia Legal"), ("amazon", "Amazônia"),
    ("caatinga", "Caatinga"), ("mata-atlantica", "Mata Atlântica"), ("pampa", "Pampa"),
    ("pantanal", "Pantanal"),
)
PENDING_TEXT = "Consulta ao PRODES pendente nesta emissão; a leitura é refeita na próxima."
# T1: a regra do MCR (Res. CMN 5.303/2026) com as datas por porte mora em mcr_regra_t1; aqui fica o texto sem porte.
MCR_BASIS = mcr_regra_t1.basis_text()
METHOD_TEXT = (
    "Interseção geométrica exata com o CAR. Conta como dentro do imóvel a mancha que está "
    "inteira no CAR, cuja parte interna comporta um pixel do satélite (30 m) ou tem 1 ha ou mais. "
    "Faixas na divisa mais estreitas que um pixel e com menos de 1 ha aparecem à parte e não "
    "entram na contagem, na área nem no risco. A mesma mancha em duas camadas conta uma vez."
)
RULE_TEXT = (
    "PRODES: conta como desmatamento dentro do imóvel só a mancha inteira no CAR, cuja parte "
    "interna comporta um pixel do satélite (30 m) ou tem 1 ha ou mais. Faixas de divisa mais "
    "estreitas e menores aparecem à parte. Máscara de desmatamento acumulado nunca conta como "
    "ocorrência anual."
)
INCOMPLETE_TEXT = "A consulta ao PRODES não veio completa nesta emissão; pode haver mais."


# ---------------------------------------------------------------- formatação pt-BR

def _grouped(value: Decimal, digits: int) -> str:
    quant = Decimal(1).scaleb(-digits)
    text = f"{value.quantize(quant, rounding=ROUND_HALF_UP):,.{digits}f}"
    return text.translate(str.maketrans(",.", ".,"))


def format_ha(value: Any) -> str:
    """Área com precisão honesta para dado de 30 m: 2 casas, "< 0,01 ha" quando menor."""
    try:
        v = Decimal(str(float(value)))
    except Exception:
        return ""
    if v <= 0:
        return "0,00 ha"
    if v < Decimal("0.01"):
        return "< 0,01 ha"
    return f"{_grouped(v, 2)} ha"


def format_pct(part: Any, total: Any) -> str:
    try:
        p, t = float(part), float(total)
    except Exception:
        return ""
    if t <= 0:
        return ""
    pct = p / t * 100.0
    if pct <= 0:
        return "0%"
    if pct < 1:
        return "< 1%"
    return f"{_grouped(Decimal(str(min(pct, 100.0))), 0)}%"


def format_date_br(value: Any) -> str:
    d = _parse_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _years_text(years: list[int]) -> str:
    ys = [str(y) for y in sorted(set(int(y) for y in years if y is not None))]
    if len(ys) <= 1:
        return "".join(ys)
    return ", ".join(ys[:-1]) + " e " + ys[-1]


# ---------------------------------------------------------------- atributos da feição

def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    s = str(raw)[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _year(props: dict[str, Any]) -> int | None:
    y = props.get("year") or props.get("ano") or props.get("year_prodes")
    try:
        return int(str(y)[:4])
    except Exception:
        return None


def _post_cutoff(props: dict[str, Any]) -> bool:
    # Mesma regra de prodes_lens._post_cutoff (recorte preservado).
    d = _parse_date(props.get("image_date") or props.get("data_imagem") or props.get("date"))
    if d is not None:
        return d > CREDIT_CUTOFF
    y = _year(props)
    return bool(y and y >= 2020)


def _straddles_cutoff(props: dict[str, Any]) -> bool:
    # O ano PRODES N vai de agosto de N-1 a julho de N. Mancha do ano 2019 (ou anterior) com
    # imagem depois de 31/07/2019 foi detectada depois do corte, mas pode ter ocorrido antes.
    y = _year(props)
    return bool(_post_cutoff(props) and y is not None and y <= CREDIT_CUTOFF.year)


def _hit_truncated(hit: dict[str, Any]) -> bool:
    """200 não é todos: camada que voltou no limite de feições do WFS está cortada."""
    features = hit.get("features") or []
    try:
        returned = max(int(hit.get("count") or 0), len(features))
    except Exception:
        returned = len(features)
    try:
        limit = int(hit.get("limit") or WFS_FEATURE_LIMIT)
    except Exception:
        limit = WFS_FEATURE_LIMIT
    matched = hit.get("number_matched")
    if isinstance(matched, (int, float)) and not isinstance(matched, bool) and matched > returned:
        return True
    return returned >= limit


def layer_kind(layer: Any) -> str:
    name = str(layer or "").lower()
    if "accumulated" in name or "acumulad" in name:
        return "accumulated"
    if "yearly_deforestation" in name or "increment" in name:
        return "annual"
    return "other"


def _biome(layer: Any) -> str:
    name = str(layer or "").lower()
    for key, label in _BIOMES:
        if key in name:
            return label
    return ""


def _accumulated_until(layer: Any) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(layer or ""))
    return int(m.group(0)) if m else None


def _period_label(layer: str, year: int | None, image_date: date | None) -> str:
    img = image_date.strftime("%d/%m/%Y") if image_date else ""
    start = None
    if year and "cerrado" in str(layer).lower():
        start = year - 2 if year <= _CERRADO_BIENNIAL_LAST_YEAR else year - 1
    if start and img:
        return f"entre {start} e a imagem de {img}"
    if img:
        return f"imagem de {img}"
    return f"ano PRODES {year}" if year else "data não informada pela fonte"


# ---------------------------------------------------------------- geometria

def _area_ha(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    try:
        return abs(_GEOD.geometry_area_perimeter(geom)[0]) / 10000.0
    except Exception:
        return 0.0


def _valid(geom):
    if geom is None or geom.is_empty or geom.is_valid:
        return geom
    return shapely.make_valid(geom)


def _polygonal(geom):
    if geom is None or geom.is_empty:
        return None
    parts = [g for g in shapely.get_parts(geom) if g.geom_type == "Polygon" and not g.is_empty]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else shapely.union_all(parts)


_THREAD = threading.local()


def _local_metric(lon: float, lat: float):
    # Transversa de Mercator centrada no imóvel: distorção desprezível na escala de uma fazenda.
    # A leitura roda em asyncio.to_thread e o Transformer do pyproj não é seguro entre threads:
    # um cache por thread.
    cache = getattr(_THREAD, "metric", None)
    if cache is None:
        cache = _THREAD.metric = {}
    key = (lon, lat)
    fn = cache.get(key)
    if fn is None:
        if len(cache) >= 64:
            cache.clear()
        proj = f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs"
        fn = cache[key] = Transformer.from_crs("EPSG:4674", proj, always_xy=True).transform
    return fn


def _max_width_m(metric_geom) -> float | None:
    try:
        return 2.0 * shapely.maximum_inscribed_circle(metric_geom, tolerance=0.25).length
    except Exception:
        return None


# ---------------------------------------------------------------- leitura pura

def _pending(reason: str, **extra) -> dict[str, Any]:
    empty = {"state": "pending", "count": 0, "area_ha": 0.0, "years": [], "occurrences": []}
    return {
        "version": READING_VERSION, "state": "pending", "complete": False, "pending_reasons": [reason],
        "criterion": _criterion(), "car_area_ha": None,
        "inside": dict(empty), "post_cutoff_inside": dict(empty), "boundary": dict(empty, post_cutoff_count=0, post_cutoff_years=[], max_width_m=None),
        "accumulated": dict(empty), "other_classes": dict(empty), **extra,
    }


def _criterion() -> dict[str, Any]:
    return {
        "pixel_m": PIXEL_M,
        "inside_when": "mancha inteira no CAR, parte interna com largura máxima >= 30 m (maior círculo inscrito) ou parte interna >= 1 ha",
        "boundary_when": "parte interna com largura máxima < 30 m e área < 1 ha",
        "boundary_max_ha": BOUNDARY_MAX_HA,
        "duplicate_iou": DUPLICATE_IOU,
        "credit_cutoff": CREDIT_CUTOFF.isoformat(),
    }


def classify_prodes(prodes: dict[str, Any] | None, car_geometry: dict[str, Any] | None, car_area_ha: Any = None) -> dict[str, Any]:
    """Classifica cada interseção PRODES × CAR. Função pura: não consulta rede."""
    p = prodes if isinstance(prodes, dict) else None
    if p is None:
        return _pending("fonte_nao_consultada")
    if p.get("ok") is False:
        return _pending("fonte_nao_respondeu")
    if not _GEO_OK:
        return _pending("motor_geometrico_indisponivel")
    try:
        car = _valid(shape(car_geometry)) if car_geometry else None
    except Exception:
        car = None
    if car is None or car.is_empty:
        return _pending("geometria_do_car_indisponivel")

    try:
        declared = float(car_area_ha)
    except Exception:
        declared = 0.0
    total_ha = declared if declared > 0 else _area_ha(car)
    centroid = car.centroid
    to_m = _local_metric(round(centroid.x, 4), round(centroid.y, 4))
    car_box = shapely.box(*car.bounds)

    candidates = p.get("candidate_layers")
    failed = [str(x.get("layer") or "") for x in (p.get("failed_layers") or []) if isinstance(x, dict)]
    failed += [str(h.get("layer") or "") for h in (p.get("hits") or []) if isinstance(h, dict) and h.get("error")]
    pending_reasons: list[str] = []
    if isinstance(candidates, list) and not any(layer_kind(x) == "annual" for x in candidates):
        pending_reasons.append("nenhuma_camada_anual_consultada")
    failed_annual = sorted({x for x in failed if layer_kind(x) == "annual"})
    failed_accumulated = sorted({x for x in failed if layer_kind(x) == "accumulated"})
    if failed_annual:
        pending_reasons.append("camada_anual_sem_resposta")
    truncated_annual = sorted({str(h.get("layer") or "") for h in (p.get("hits") or [])
                               if isinstance(h, dict) and layer_kind(h.get("layer")) == "annual" and _hit_truncated(h)})
    truncated_accumulated = [h for h in (p.get("hits") or [])
                             if isinstance(h, dict) and layer_kind(h.get("layer")) == "accumulated" and _hit_truncated(h)]
    if truncated_annual:
        pending_reasons.append("camada_anual_truncada")

    buckets: dict[str, list[dict[str, Any]]] = {"inside": [], "boundary": [], "accumulated": [], "other_classes": []}
    geoms: dict[str, list] = {k: [] for k in ("inside", "post", "boundary", "accumulated", "other_classes")}
    seen: set = set()
    kept: dict[tuple, list] = {}
    duplicates = 0
    geometry_errors = 0

    def near_duplicate(group: tuple, geom) -> bool:
        for other in kept.get(group, ()):
            if not other.intersects(geom):
                continue
            common = other.intersection(geom).area
            union = other.area + geom.area - common
            if union > 0 and common / union > DUPLICATE_IOU:
                return True
        return False

    for hit in p.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        layer = str(hit.get("layer") or "")
        kind = layer_kind(layer)
        if kind == "other":
            continue
        for feature in hit.get("features") or []:
            try:
                src = shape(feature.get("geometry"))
                if src is None or src.is_empty or not car_box.intersects(shapely.box(*src.bounds)):
                    continue
                src = _valid(src)
                if not car.intersects(src):
                    continue
                inter = _polygonal(car.intersection(src))
            except Exception:
                geometry_errors += 1
                continue
            if inter is None:
                continue
            area = _area_ha(inter)
            if area <= 0:
                continue
            props = feature.get("properties") or {}
            year = _year(props)
            key = (kind, year, inter.wkb_hex)
            if key in seen:
                continue
            group = (kind, year)
            try:
                if near_duplicate(group, inter):
                    duplicates += 1
                    continue
            except Exception:
                geometry_errors += 1
                continue
            feature_area = _area_ha(_polygonal(src))
            contained = feature_area > 0 and area >= feature_area * _CONTAINED_SHARE
            fits_pixel = False
            max_width = None
            try:
                metric = transform(to_m, inter)
                if metric.area >= _MIN_PIXEL_AREA_M2:
                    fits_pixel = not metric.buffer(-_HALF_PIXEL_M).is_empty
                if not (contained or fits_pixel):
                    max_width = _max_width_m(metric)
            except Exception:
                geometry_errors += 1
                continue
            seen.add(key)
            kept.setdefault(group, []).append(inter)
            large_strip = not (contained or fits_pixel) and area >= BOUNDARY_MAX_HA
            inside = bool(contained or fits_pixel or large_strip)
            img = _parse_date(props.get("image_date") or props.get("data_imagem") or props.get("date"))
            post = _post_cutoff(props)
            main_class = str(props.get("main_class") or "").strip().upper()
            if contained:
                reason = "mancha inteira dentro do CAR"
            elif fits_pixel:
                reason = "parte interna comporta um pixel de 30 m"
            elif large_strip:
                reason = "faixa estreita na divisa com 1 ha ou mais dentro do CAR"
            else:
                reason = "faixa na divisa mais estreita que um pixel de 30 m e menor que 1 ha"
            item = {
                "id": feature.get("id"),
                "layer": layer,
                "biome": _biome(layer),
                "year": year,
                "image_date": img.isoformat() if img else None,
                "period_label": _period_label(layer, year, img),
                "area_ha": round(area, 6),
                "feature_area_ha": round(feature_area, 6),
                "share_of_feature_pct": round(area / feature_area * 100.0, 1) if feature_area > 0 else None,
                "placement": "inside" if inside else "boundary",
                "reason": reason,
                "narrow_strip": large_strip,
                "max_width_m": round(max_width, 1) if max_width is not None else None,
                "post_cutoff": post,
                "post_cutoff_straddles": _straddles_cutoff(props),
            }
            if kind == "accumulated":
                item["until_year"] = _accumulated_until(layer) or year
                if inside:
                    buckets["accumulated"].append(item)
                    geoms["accumulated"].append(inter)
                continue
            if main_class and main_class != _DEFORESTATION_CLASS:
                item["class"] = _CLASS_LABELS.get(main_class, main_class.lower())
                if inside:
                    buckets["other_classes"].append(item)
                    geoms["other_classes"].append(inter)
                continue
            if inside:
                buckets["inside"].append(item)
                geoms["inside"].append(inter)
                if post:
                    geoms["post"].append(inter)
            else:
                buckets["boundary"].append(item)
                geoms["boundary"].append(inter)

    if geometry_errors:
        pending_reasons.append("geometria_da_fonte_nao_processada")
    complete = not pending_reasons

    def union_ha(items):
        return round(_area_ha(unary_union(items)), 6) if items else 0.0

    def order(items):
        return sorted(items, key=lambda x: (not x["post_cutoff"], -(x["year"] or 0), -x["area_ha"]))

    def state(found: bool, may_be_incomplete: bool) -> str:
        if found:
            return "found"
        return "pending" if may_be_incomplete else "not_found"

    inside_items = order(buckets["inside"])
    post_items = [x for x in inside_items if x["post_cutoff"]]
    boundary_items = order(buckets["boundary"])
    widths = [x["max_width_m"] for x in boundary_items if x["max_width_m"] is not None]
    accumulated_items = order(buckets["accumulated"])
    other_items = order(buckets["other_classes"])
    inside_area = union_ha(geoms["inside"])
    post_area = union_ha(geoms["post"])
    accumulated_area = union_ha(geoms["accumulated"])

    reading = {
        "version": READING_VERSION,
        "state": state(bool(inside_items), not complete),
        "complete": complete,
        "pending_reasons": pending_reasons,
        "truncated_layers": truncated_annual,
        "duplicates_removed": duplicates,
        "criterion": _criterion(),
        "car_area_ha": round(total_ha, 6),
        "inside": {
            "state": state(bool(inside_items), not complete),
            "count": len(inside_items),
            "area_ha": inside_area,
            "pct_car": round(inside_area / total_ha * 100.0, 4) if total_ha > 0 else None,
            "years": sorted({x["year"] for x in inside_items if x["year"] is not None}),
            "occurrences": inside_items,
        },
        "post_cutoff_inside": {
            "state": state(bool(post_items), not complete),
            "count": len(post_items),
            "area_ha": post_area,
            "pct_car": round(post_area / total_ha * 100.0, 4) if total_ha > 0 else None,
            "years": sorted({x["year"] for x in post_items if x["year"] is not None}),
            "occurrences": post_items,
        },
        "boundary": {
            "state": state(bool(boundary_items), not complete),
            "count": len(boundary_items),
            "area_ha": union_ha(geoms["boundary"]),
            "years": sorted({x["year"] for x in boundary_items if x["year"] is not None}),
            "max_width_m": round(max(widths), 1) if widths else None,
            "post_cutoff_count": sum(1 for x in boundary_items if x["post_cutoff"]),
            "post_cutoff_years": sorted({x["year"] for x in boundary_items if x["post_cutoff"] and x["year"] is not None}),
            "occurrences": boundary_items,
        },
        "accumulated": {
            "state": state(bool(accumulated_items), bool(failed_accumulated) or bool(truncated_accumulated) or bool(geometry_errors)),
            "count": len(accumulated_items),
            "area_ha": accumulated_area,
            "pct_car": round(accumulated_area / total_ha * 100.0, 4) if total_ha > 0 else None,
            "years": [],
            "occurrences": accumulated_items,
        },
        "other_classes": {
            "state": state(bool(other_items), not complete),
            "count": len(other_items),
            "area_ha": union_ha(geoms["other_classes"]),
            "years": sorted({x["year"] for x in other_items if x["year"] is not None}),
            "occurrences": other_items,
        },
    }
    return reading


# ---------------------------------------------------------------- textos para o cliente

def _occurrence_line(item: dict[str, Any], total_ha: float) -> str:
    parts = [item["period_label"][:1].upper() + item["period_label"][1:], format_ha(item["area_ha"])]
    pct = format_pct(item["area_ha"], total_ha)
    if pct:
        parts[-1] += f" ({pct} do CAR)"
    img = _parse_date(item.get("image_date"))
    if item.get("narrow_strip") and item.get("max_width_m") is not None:
        parts.append(f"faixa de até {_grouped(Decimal(str(item['max_width_m'])), 0)} m de largura na divisa")
    if item["post_cutoff"] and item.get("post_cutoff_straddles"):
        parts.append(f"detectado em imagem posterior a 31/07/2019; o período PRODES {item['year']} começa antes dessa data")
    elif item["post_cutoff"]:
        parts.append("posterior a 31/07/2019")
    elif img and img < MARCO_2008:
        parts.append("anterior a 22/07/2008")
    return " • ".join(parts)


def _texts(reading: dict[str, Any]) -> dict[str, Any]:
    total = reading.get("car_area_ha") or 0.0
    ins, post, bnd = reading["inside"], reading["post_cutoff_inside"], reading["boundary"]
    acc, oth = reading["accumulated"], reading["other_classes"]
    n, m, k = ins["count"], post["count"], bnd["count"]
    pending = reading["state"] == "pending"
    incomplete = not reading.get("complete", False)
    # Tentar não é responder: "nenhum" só vale para as camadas que responderam por completo.
    scope = " nas camadas que responderam" if incomplete else ""
    straddling = sorted({x["year"] for x in post["occurrences"] if x.get("post_cutoff_straddles") and x.get("year")})
    after_cutoff = "em imagem posterior a 31/07/2019" if straddling else "depois de 31/07/2019"
    straddle_note = (
        f" A de PRODES {_years_text(straddling)} foi detectada em imagem posterior a 31/07/2019,"
        " mas o período do PRODES começa antes dessa data." if len(straddling) == 1 else
        (f" As de PRODES {_years_text(straddling)} foram detectadas em imagem posterior a 31/07/2019,"
         " mas o período do PRODES começa antes dessa data." if straddling else "")
    )

    if n:
        # "d2006" é o período entre a imagem anterior e a de 2006, não "desmatado em 2006".
        when = ins["occurrences"][0]["period_label"] if n == 1 else f"anos PRODES {_years_text(ins['years'])}"
        inside_text = (
            f"Dentro do imóvel: {n} {_plural(n, 'desmatamento mapeado', 'desmatamentos mapeados')} pelo PRODES"
            f" ({when}), {format_ha(ins['area_ha'])} ({format_pct(ins['area_ha'], total)} do CAR)."
        )
        if m:
            if straddling:
                inside_text += f" {_plural(m, 'Um tem imagem posterior', f'{m} têm imagem posterior')} a 31/07/2019 (PRODES {_years_text(post['years'])}; {format_ha(post['area_ha'])})."
            else:
                inside_text += f" {_plural(m, 'Um é posterior', f'{m} são posteriores')} a 31/07/2019 (PRODES {_years_text(post['years'])}; {format_ha(post['area_ha'])})."
            inside_text += straddle_note
        else:
            inside_text += f" Nenhum é posterior a 31/07/2019{scope}."
        if incomplete:
            inside_text += " " + INCOMPLETE_TEXT
        headline = f"PRODES: {n} {_plural(n, 'desmatamento', 'desmatamentos')} dentro do imóvel • {format_ha(ins['area_ha'])}"
        summary_short = f"{n} {_plural(n, 'desmatamento', 'desmatamentos')} dentro do imóvel • {format_ha(ins['area_ha'])}"
    elif pending:
        inside_text = PENDING_TEXT
        headline = "PRODES: consulta pendente"
        summary_short = "Consulta pendente"
    else:
        inside_text = "Dentro do imóvel: nenhum desmatamento mapeado pelo PRODES nas camadas anuais consultadas."
        headline = "PRODES: nenhum desmatamento dentro do imóvel"
        summary_short = "Nenhum desmatamento dentro do imóvel"

    boundary_text = boundary_core = boundary_short = ""
    if k:
        areas = " · ".join(format_ha(x["area_ha"]) for x in bnd["occurrences"][:6])
        if k > 6:
            areas += f" e mais {k - 6}"
        width = f"até {_grouped(Decimal(str(bnd['max_width_m'])), 0)} m" if bnd.get("max_width_m") is not None else "menos de 30 m"
        boundary_text = (
            f"Na divisa: {k} {_plural(k, 'mancha mapeada', 'manchas mapeadas')} pelo PRODES ({_years_text(bnd['years'])})"
            f" {_plural(k, 'cruza o limite do imóvel só numa faixa', 'cruzam o limite do imóvel só em faixas')} de {width} de largura ({areas}),"
            f" mais {_plural(k, 'estreita', 'estreitas')} que um pixel do satélite (30 m)."
            f" {_plural(k, 'Não conta', 'Não contam')} como desmatamento dentro do imóvel."
        )
        boundary_core = boundary_text
        boundary_short = (
            f"Na divisa: {k} {_plural(k, 'faixa estreita', 'faixas estreitas')} do PRODES ({_years_text(bnd['years'])}),"
            f" mais {_plural(k, 'fina', 'finas')} que um pixel do satélite, {_plural(k, 'não conta', 'não contam')} como desmatamento dentro do imóvel."
        )
        if bnd["post_cutoff_count"]:
            yrs = _years_text(bnd["post_cutoff_years"])
            many = len(bnd["post_cutoff_years"]) > 1
            which = f"as de {yrs} são posteriores" if many else ("é posterior" if k == 1 else f"a de {yrs} é posterior")
            boundary_short = boundary_short[:-1] + f"; {which} a 31/07/2019."
            boundary_text += (
                f" {f'As de {yrs} são posteriores' if many else ('Ela é posterior' if k == 1 else f'A de {yrs} é posterior')} a 31/07/2019"
                f" e {'podem' if many else 'pode'} aparecer em checagem automática de crédito."
            )
        summary_short += f" • {k} {_plural(k, 'faixa', 'faixas')} na divisa, não {_plural(k, 'contada', 'contadas')}"

    accumulated_text = ""
    if acc["count"]:
        until = acc["occurrences"][0].get("until_year")
        biome = acc["occurrences"][0].get("biome")
        label = f"PRODES {biome}" if biome else "PRODES"
        accumulated_text = (
            f"Já desmatado até {until}: {format_ha(acc['area_ha'])} dentro do imóvel ({format_pct(acc['area_ha'], total)} do CAR),"
            f" pela máscara acumulada do {label}. É o retrato do que já estava aberto em {until},"
            " não um desmatamento de ano específico, e não entra na contagem."
        )

    other_text = ""
    if oth["count"]:
        classes = sorted({x.get("class") or "outra classe" for x in oth["occurrences"]})
        other_text = (
            f"Área que o PRODES marca como {' e '.join(classes)}, e não como desmatamento: "
            f"{format_ha(oth['area_ha'])} dentro do imóvel. Não entra na contagem."
        )

    if pending and not m:
        credit_text = "Triagem pós-31/07/2019 pendente: o PRODES não respondeu por completo nesta emissão."
    elif m:
        credit_text = (
            f"Há desmatamento PRODES dentro do imóvel {after_cutoff} (PRODES {_years_text(post['years'])}; {format_ha(post['area_ha'])})."
            f"{straddle_note}"
            " Para crédito rural, isso deve ser conferido conforme o MCR vigente e a documentação ambiental;"
            " não equivale automaticamente a impedimento."
        )
        if incomplete:
            credit_text += " " + INCOMPLETE_TEXT
    elif bnd["post_cutoff_count"]:
        credit_text = (
            f"Nenhum desmatamento PRODES dentro do imóvel depois de 31/07/2019{scope}. "
            f"A faixa de divisa de {_years_text(bnd['post_cutoff_years'])} é posterior a essa data e pode aparecer em"
            " checagem automática de crédito que cruza o CAR com o PRODES."
        )
        if incomplete:
            credit_text += " " + INCOMPLETE_TEXT
    else:
        credit_text = (
            f"Nenhum desmatamento PRODES dentro do imóvel depois de 31/07/2019{scope or ' nas camadas consultadas'}."
            + (f" {INCOMPLETE_TEXT}" if incomplete else "")
            + " Isso não substitui a verificação da instituição financeira."
        )

    if m:
        risk = {"level": "attention", "status": "ATENÇÃO", "criterion": "desmatamento dentro do imóvel depois de 31/07/2019"}
    elif n:
        risk = {"level": "attention", "status": "ATENÇÃO", "criterion": f"desmatamento mapeado dentro do imóvel, todo anterior a 31/07/2019{scope}"}
    elif pending:
        risk = {"level": "neutral", "status": "CONSULTA PENDENTE", "criterion": "PRODES não respondeu por completo e nada foi achado nas camadas que responderam"}
    else:
        risk = {"level": "ok", "status": "BAIXO", "criterion": "nenhum desmatamento dentro do imóvel; faixas de divisa não contam"}

    return {
        "headline": headline, "summary_short": summary_short, "inside_text": inside_text,
        "boundary_text": boundary_text, "boundary_core": boundary_core, "boundary_short": boundary_short, "accumulated_text": accumulated_text, "other_classes_text": other_text,
        "credit_text": credit_text, "risk": risk, "post_straddle_years": straddling,
    }


def _rows(reading: dict[str, Any], texts: dict[str, Any], basis: str = MCR_BASIS) -> list[list[str]]:
    total = reading.get("car_area_ha") or 0.0
    ins, post, bnd = reading["inside"], reading["post_cutoff_inside"], reading["boundary"]
    if reading["state"] == "pending" and not ins["count"]:
        rows = [["Desmatamento PRODES", PENDING_TEXT]]
    else:
        n = ins["count"]
        if n:
            value = f"{n} {_plural(n, 'desmatamento', 'desmatamentos')} • {format_ha(ins['area_ha'])} • {format_pct(ins['area_ha'], total)} do CAR"
        else:
            value = "Nenhum desmatamento mapeado nas camadas anuais consultadas"
        rows = [["Dentro do imóvel", value]]
        for item in ins["occurrences"][:_DETAIL_LIMIT]:
            rows.append([f"PRODES {item['year']}" if item["year"] else "PRODES", _occurrence_line(item, total)])
        rest = n - _DETAIL_LIMIT
        if rest > 0:
            rows.append(["Demais ocorrências", f"mais {rest} dentro do imóvel, já somadas no total acima"])
        if post["count"]:
            pc = post["count"]
            value = f"{pc} {_plural(pc, 'desmatamento', 'desmatamentos')} • {format_ha(post['area_ha'])} • PRODES {_years_text(post['years'])}"
            if texts.get("post_straddle_years"):
                value += f" • PRODES {_years_text(texts['post_straddle_years'])}: detectado em imagem posterior; o período começa antes de 31/07/2019"
            rows.append(["Depois de 31/07/2019", value])
        else:
            rows.append(["Depois de 31/07/2019", "Nenhum dentro do imóvel nas camadas que responderam; a consulta não veio completa" if not reading.get("complete", False) else "Nenhum dentro do imóvel nas camadas consultadas"])
    if texts["boundary_core"]:
        # Faixa de divisa pós-31/07/2019 nunca some: quando a linha de triagem já fala dela
        # (sem desmatamento interno recente), a linha da divisa não repete o aviso.
        text = texts["boundary_core"] if (bnd["post_cutoff_count"] and not post["count"]) else texts["boundary_text"]
        rows.append(["Na divisa (não conta)", text.replace("Na divisa: ", "", 1)])
    if texts["accumulated_text"]:
        rows.append(["Já desmatado antes", texts["accumulated_text"]])
    if texts["other_classes_text"]:
        rows.append(["Outras classes PRODES", texts["other_classes_text"]])
    rows += [
        ["Triagem para crédito rural", texts["credit_text"]],
        ["Base regulatória", basis],
        ["Como medimos", METHOD_TEXT],
        ["Fonte", "INPE / TerraBrasilis / PRODES"],
    ]
    return rows


def _narrative(reading: dict[str, Any], texts: dict[str, Any], why_credit: str | None = None) -> dict[str, Any]:
    ins, post, bnd = reading["inside"], reading["post_cutoff_inside"], reading["boundary"]
    total = reading.get("car_area_ha") or 0.0
    n, m = ins["count"], post["count"]
    one = None
    straddling = texts.get("post_straddle_years") or []
    after_cutoff = "em imagem posterior a 31/07/2019" if straddling else "depois de 31/07/2019"
    if m:
        one = (
            f"tem desmatamento mapeado pelo PRODES dentro da área {after_cutoff} (PRODES {_years_text(post['years'])}; "
            f"{format_ha(post['area_ha'])})"
            + (f", e o período PRODES {_years_text(straddling)} começa antes dessa data" if straddling else "")
            + "; isso exige diligência para crédito rural, mas não prova irregularidade por si só."
        )
    elif n:
        complete = reading.get("complete", False)
        one = (
            f"tem desmatamento mapeado pelo PRODES dentro da área ("
            f"{ins['occurrences'][0]['period_label'] if n == 1 else 'anos PRODES ' + _years_text(ins['years'])}; {format_ha(ins['area_ha'])}, "
            f"{format_pct(ins['area_ha'], total)} do CAR), "
            + ("todo anterior a 31/07/2019" if complete else "anterior a 31/07/2019 nas camadas que responderam, mas a consulta ao PRODES não veio completa")
            + "; isso não prova irregularidade por si só."
        )
    found = [x for x in (texts["inside_text"], texts["boundary_short"], texts["accumulated_text"], texts["other_classes_text"]) if x]
    why = []
    if n:
        why.append("O PRODES ajuda a reconstruir quando houve desmatamento mapeado. Ocorrência cartográfica não equivale automaticamente a infração; data, autorização e enquadramento ambiental continuam necessários.")
    if m:
        why.append(why_credit or mcr_regra_t1.why_text())
    attention, next_steps, money = [], [], []
    if m:
        attention.append(f"Há desmatamento PRODES dentro do imóvel {after_cutoff} (PRODES {_years_text(post['years'])}). A análise de crédito deve conferir documentação ambiental e a regra vigente; o Raio-X não transforma isso em impedimento automático.")
        next_steps.append(f"Conferir cada desmatamento PRODES {after_cutoff.replace('depois de', 'posterior a')} por data, autorização e documento ambiental aplicável à operação de crédito.")
        money.append(f"Desmatamento PRODES {after_cutoff.replace('depois de', 'posterior a')} pode exigir documentação adicional na análise de crédito rural e deve ser verificado antes de fechar a operação.")
    elif n:
        next_steps.append("Interpretar o desmatamento PRODES antigo por data e contexto ambiental, sem tratá-lo automaticamente como infração atual.")
        money.append("Desmatamento PRODES antigo pode gerar custo de diligência ou regularização dependendo do enquadramento real.")
    if bnd["post_cutoff_count"] and not m:
        attention.append(f"Faixa de divisa de {_years_text(bnd['post_cutoff_years'])}, posterior a 31/07/2019, pode aparecer em checagem automática de crédito; pela medição ela é mais estreita que um pixel do satélite e não é desmatamento dentro do imóvel.")
        next_steps.append("Se a checagem de crédito apontar a faixa de divisa, apresentar a medição do Raio-X: largura menor que um pixel do satélite (30 m).")
    if reading["state"] == "pending" or not reading.get("complete", False):
        next_steps.append("Refazer a consulta ao PRODES, pendente nesta emissão.")
    return {"one_sentence": one, "found": found, "why": why, "attention": attention, "next_steps": next_steps, "money": money}


def lens_from_reading(reading: dict[str, Any], fiscal_modules: Any = None, property_type: Any = None) -> dict[str, Any]:
    """Lente no formato de prodes_lens.derive_prodes_lens, só com o que está dentro do imóvel."""
    ins, post, bnd = reading["inside"], reading["post_cutoff_inside"], reading["boundary"]
    texts = _texts(reading)
    try:
        mf = float(fiscal_modules)
    except Exception:
        mf = None
    return {
        "historical": {
            "occurrence_count": ins["count"], "area_unique_ha": ins["area_ha"], "pct_car": ins.get("pct_car"), "years": ins["years"],
        },
        "post_2019_07_31": {
            "occurrence_count": post["count"], "area_unique_ha": post["area_ha"], "pct_car": post.get("pct_car"),
            "years": post["years"], "cutoff": CREDIT_CUTOFF.isoformat(),
        },
        "boundary_touch": {
            "occurrence_count": bnd["count"], "area_unique_ha": bnd["area_ha"], "years": bnd["years"],
            "max_width_m": bnd.get("max_width_m"), "post_cutoff_count": bnd["post_cutoff_count"],
        },
        "credit_screening": {
            "mcr_check_required": post["count"] > 0,
            "automatic_check_may_flag": post["count"] > 0 or bnd["post_cutoff_count"] > 0,
            "fiscal_modules": mf,
            "reading": texts["credit_text"],
            "regulatory_basis": mcr_regra_t1.basis_text(fiscal_modules, property_type),
        },
        "state": reading["state"],
        "calculation_method": "exact_geometry_union_after_intersection",
        "inside_criterion": "inteira_no_car_ou_comporta_pixel_30m",
        "explanation": METHOD_TEXT,
    }


def _result_parts(result: dict[str, Any]) -> tuple[dict, dict, Any]:
    car = (result or {}).get("car") or {}
    props = car.get("properties") or {}
    return car, props, (result or {}).get("prodes")


def prodes_reading_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Campos novos do PRODES para portal e relatório, com estado explícito."""
    car, props, prodes = _result_parts(result)
    reading = classify_prodes(prodes, car.get("geometry"), props.get("area"))
    texts = _texts(reading)
    panel_state = {"found": "ANSWERED_HIT", "not_found": "ANSWERED_CLEAR", "pending": "FAILED"}[reading["state"]]
    return {
        **reading,
        **texts,
        "rows": _rows(reading, texts, mcr_regra_t1.basis_text(props.get("m_fiscal"), props.get("tipo_imovel"))),
        "narrative": _narrative(reading, texts, mcr_regra_t1.why_text(props.get("m_fiscal"), props.get("tipo_imovel"))),
        "lens": lens_from_reading(reading, props.get("m_fiscal"), props.get("tipo_imovel")),
        "panel": {"id": "prodes", "label": "PRODES", "state": reading["state"], "audit_state": panel_state, "text": texts["headline"],
                  **_card(reading, texts)},
    }


def _card(reading: dict[str, Any], texts: dict[str, Any]) -> dict[str, str]:
    """Linha do PRODES no cartão do mapa: o mesmo texto do relatório, curto."""
    ins, post = reading["inside"], reading["post_cutoff_inside"]
    n, m = ins["count"], post["count"]
    incomplete = not reading.get("complete", False)
    if n:
        straddling = texts.get("post_straddle_years") or []
        if m and straddling:
            reason = f"{_plural(m, 'Um tem imagem posterior', f'{m} têm imagem posterior')} a 31/07/2019 (PRODES {_years_text(post['years'])}); o período PRODES {_years_text(straddling)} começa antes dessa data."
        elif m:
            reason = f"{_plural(m, 'Um é posterior', f'{m} são posteriores')} a 31/07/2019 (PRODES {_years_text(post['years'])})."
        else:
            reason = "Nenhum é posterior a 31/07/2019" + (" nas camadas que responderam." if incomplete else ".")
        if incomplete:
            reason += " " + INCOMPLETE_TEXT
        # O status do cartão sai em caixa alta (CSS): número e unidade ("14,28 ha") ficam no motivo.
        total = reading.get("car_area_ha") or 0.0
        size = f"{n} {_plural(n, 'desmatamento', 'desmatamentos')} • {format_ha(ins['area_ha'])} ({format_pct(ins['area_ha'], total)} do CAR). "
        return {"dot": "diligence", "status": "Desmatamento dentro do imóvel", "reason": size + reason}
    if reading["state"] == "pending":
        return {"dot": "source_failed", "status": "Consulta pendente",
                "reason": "O mapa de desmatamento do INPE não respondeu por completo nesta consulta."}
    return {"dot": "checked_clear", "status": "Sem desmatamento dentro do imóvel", "reason": texts["boundary_short"]}


def compact_reading(payload: dict[str, Any]) -> dict[str, Any]:
    """Resumo leve (sem listas de feições) para o JSON que o portal recebe."""
    keep = ("version", "state", "complete", "headline", "summary_short", "inside_text", "boundary_text",
            "accumulated_text", "other_classes_text", "credit_text", "risk", "panel")
    out = {k: payload.get(k) for k in keep}
    for block in ("inside", "post_cutoff_inside", "boundary", "accumulated"):
        b = payload.get(block) or {}
        out[block] = {k: b.get(k) for k in ("state", "count", "area_ha", "years") if k in b}
    return out


def apply_reading_to_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza result['prodes'] em lugar: 'exact' passa a conter só o que está dentro do imóvel.

    O cálculo bruto fica em 'exact_raw'. Idempotente: recalcula a partir de hits + geometria.
    """
    if not isinstance(result, dict) or not isinstance(result.get("prodes"), dict):
        return None
    prodes = result["prodes"]
    key = _reading_key(result)
    payload = _cache_get(key)
    if payload is None:
        payload = prodes_reading_payload(result)
        _cache_put(key, payload)
    if "exact_raw" not in prodes and isinstance(prodes.get("exact"), dict):
        prodes["exact_raw"] = prodes["exact"]
    if payload["state"] == "pending" and not payload["inside"]["count"]:
        # Zero não é ausência: consulta incompleta sem achado não vira "0 ocorrências".
        prodes["exact"] = {"available": False, "state": "pending", "occurrence_count": None,
                           "area_unique_ha": None, "occurrences": [], "reading_version": READING_VERSION}
    else:
        prodes["exact"] = {
            "available": True,
            "occurrence_count": payload["inside"]["count"],
            "area_unique_ha": payload["inside"]["area_ha"],
            "occurrences": [
                {"id": x["id"], "area_intersection_ha": x["area_ha"],
                 "properties": {"year": x["year"], "image_date": x["image_date"], "layer": x["layer"]}}
                for x in payload["inside"]["occurrences"]
            ],
            "boundary_touch_count": payload["boundary"]["count"],
            "reading_version": READING_VERSION,
        }
    prodes["reading"] = compact_reading(payload)
    return payload


# A mesma análise passa pela leitura duas vezes (analyze_car e o patch do relatório). O cache é
# pelo CONTEÚDO de tudo o que a leitura usa (hits, camadas, geometria, área, regras), nunca pela
# identidade dos objetos: id() se repete depois que o objeto some e daria a leitura de outro imóvel.
_CACHE_LOCK = threading.Lock()
_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_CACHE_MAX = 64


def _reading_key(result: dict[str, Any]) -> str | None:
    car, props, prodes = _result_parts(result)
    p = prodes if isinstance(prodes, dict) else {}
    basis = [READING_VERSION, PIXEL_M, BOUNDARY_MAX_HA, DUPLICATE_IOU, WFS_FEATURE_LIMIT, CREDIT_CUTOFF.isoformat(), _GEO_OK,
             p.get("ok"), p.get("hits"), p.get("failed_layers"), p.get("candidate_layers"),
             car.get("geometry"), props.get("area"), props.get("m_fiscal")]
    try:
        raw = json.dumps(basis, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            return None
        _CACHE.move_to_end(key)
    return copy.deepcopy(hit)


def _cache_put(key: str | None, payload: dict[str, Any]) -> None:
    if key is None:
        return
    frozen = copy.deepcopy(payload)
    with _CACHE_LOCK:
        _CACHE[key] = frozen
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


# ---------------------------------------------------------------- relatório

_OLD_MAIN_ATTENTION = "O maior ponto de atenção desta emissão é a necessidade de interpretar corretamente as ocorrências PRODES e completar a diligência registral; nenhum desses pontos deve ser inferido além do que as fontes consultadas suportam."
_NEW_MAIN_ATTENTION = "O maior ponto de atenção desta emissão é completar a diligência registral; nenhum ponto deve ser inferido além do que as fontes consultadas suportam."
_OLD_PRODES_LINE = re.compile(r"^\s*\d+ ocorrência\(s\) PRODES")


def apply_reading_to_report_payload(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Escreve a leitura única em todos os pontos do payload do relatório que falam de PRODES."""
    rp = apply_reading_to_result(result) or prodes_reading_payload(result or {})
    inside, risk = rp["inside"], rp["risk"]
    pending = rp["state"] == "pending" and not inside["count"]

    env = payload.setdefault("environment", {})
    pd = env.setdefault("prodes", {})
    old_count = pd.get("count")
    pd["count"] = "Pendente" if pending else inside["count"]
    # H1 lê este campo (KPI do PDF e narrativa): a leitura F2 é quem decide se está pendente.
    pd["pending"] = pending
    pd["area_ha"] = "—" if pending else format_ha(inside["area_ha"]).replace(" ha", "")
    pd["area_ha_value"] = inside["area_ha"]
    pd["status"] = risk["status"]
    pd["summary"] = rp["inside_text"]
    pd["rows"] = rp["rows"]
    pd["meaning"] = (
        "PRODES mapeia desmatamento por satélite. A interseção não prova, isoladamente, infração ambiental;"
        " data, autorização e enquadramento continuam necessários."
    )
    pd["lens"] = rp["lens"]
    pd["reading"] = {**compact_reading(rp), "narrative": rp["narrative"]}
    payload["credit_screening"] = rp["lens"]["credit_screening"]

    for row in env.get("layer_rows") or []:
        if isinstance(row, list) and row and row[0] == "PRODES" and len(row) > 1:
            row[1] = rp["summary_short"]
    for row in payload.get("executive_summary_rows") or []:
        if isinstance(row, list) and row and row[0] == "Ambiental / PRODES":
            for i, value in enumerate((rp["summary_short"], risk["status"], risk["level"]), start=1):
                if i < len(row):
                    row[i] = value
    for item in payload.get("compliance") or []:
        if isinstance(item, dict) and item.get("label") == "PRODES":
            item.update(text=rp["summary_short"], badge=risk["status"], level=risk["level"])

    points = [x for x in (payload.get("attention_points") or []) if not str(x).startswith("PRODES:")]
    if inside["count"] or pending:
        points.insert(0, rp["inside_text"] if not pending else PENDING_TEXT)
    if "attention_points" in payload or points:
        payload["attention_points"] = points

    con = payload.get("conclusion")
    if isinstance(con, dict):
        for cat in con.get("categories") or []:
            if isinstance(cat, dict) and cat.get("label") == "Ambiental":
                cat.update(text=rp["inside_text"], risk=risk["status"], level=risk["level"])
        _reconcile_overall_risk(con, payload, inside["count"], pending, old_count)
        if con.get("main_attention") == _OLD_MAIN_ATTENTION and not inside["count"]:
            con["main_attention"] = _NEW_MAIN_ATTENTION
        risks = [x for x in (con.get("risks") or []) if not _OLD_PRODES_LINE.match(str(x))]
        if inside["count"]:
            label = "ano" if len(inside["years"]) == 1 else "anos"
            risks.insert(0, f"Desmatamento PRODES dentro do imóvel ({label} PRODES {_years_text(inside['years'])}) exige análise temporal e documental.")
        if "risks" in con:
            con["risks"] = risks
        diligence = [x for x in (con.get("diligence") or []) if "ocorrência PRODES" not in str(x)]
        if inside["count"]:
            diligence.insert(1 if diligence else 0, "Conferir cada desmatamento PRODES dentro do imóvel por data, autorização e enquadramento aplicável.")
        elif rp["boundary"]["post_cutoff_count"]:
            diligence.append("Se a checagem de crédito apontar a faixa de divisa do PRODES, apresentar a medição do Raio-X.")
        if "diligence" in con:
            con["diligence"] = diligence

    quick = payload.get("quick_read")
    if isinstance(quick, str):
        if pending:
            phrase = "consulta PRODES pendente"
        elif inside["count"]:
            phrase = f"{inside['count']} {_plural(inside['count'], 'desmatamento', 'desmatamentos')} PRODES dentro do imóvel"
        else:
            phrase = "nenhum desmatamento PRODES dentro do imóvel"
        payload["quick_read"] = re.sub(r"\d+ ocorrência\(s\) PRODES em interseção exata", phrase, quick)

    rules = payload.setdefault("interpretation_rules", [])
    if RULE_TEXT not in rules:
        rules.append(RULE_TEXT)
    return payload


_PENDING_REASON = " A consulta ao PRODES ficou pendente nesta emissão."


def _reconcile_overall_risk(con: dict[str, Any], payload: dict[str, Any], inside_count: int, pending: bool, old_count: Any) -> None:
    """Risco geral coerente com a leitura, nos dois sentidos, sem nunca baixar ALTO/CRÍTICO.

    build_live_payload decide MODERADO pela contagem bruta de result['prodes']['exact'].
    Na nova tentativa essa contagem pode faltar (dicionário cru da consulta) e o risco sair
    BAIXO enquanto a leitura acha desmatamento dentro: aqui ele sobe para MODERADO.
    """
    emb = (payload.get("enforcement") or {}).get("embargo_count")
    try:
        emb = int(emb or 0)
    except Exception:
        emb = 0
    current = con.get("overall_risk")
    reason = str(con.get("overall_reason") or "")
    if inside_count:
        if current in (None, "", "BAIXO", "NÃO CLASSIFICADO"):
            con["overall_risk"] = "MODERADO"
            con["overall_reason"] = reason.replace(_PENDING_REASON, "")
        return
    if pending:
        if current == "BAIXO" or (current == "MODERADO" and old_count):
            con["overall_risk"] = "ALTO" if emb else "NÃO CLASSIFICADO"
            if _PENDING_REASON not in reason:
                con["overall_reason"] = reason + _PENDING_REASON
        return
    if current == "MODERADO" and old_count:
        con["overall_risk"] = "ALTO" if emb else "BAIXO"
    elif current == "NÃO CLASSIFICADO" and _PENDING_REASON in reason:
        con["overall_risk"] = "ALTO" if emb else "BAIXO"
        con["overall_reason"] = reason.replace(_PENDING_REASON, "")

"""Sítios arqueológicos do IPHAN (SICG oficial): dentro do imóvel e vizinhança de 10 km.

Fonte: GeoServer oficial do IPHAN, camadas ``SICG:sitios`` (pontos) e
``SICG:sitios_pol`` (polígonos). O espelho do IBAMA (PAMGIA) não é usado para
afirmar ausência: em 13/09/2026 ele estava parado desde 15/11/2025 e mais da
metade dos sítios só existe como ponto, que a checagem antiga não lia.

Regras que este módulo garante:

* as duas camadas precisam responder, completas, para haver resposta; se uma
  falhar (ou vier truncada, ou com eixo trocado), o resultado é ``pending`` e
  nunca "0 sítios";
* um sítio é uma linha só (ponto e polígono juntados pelo código IPHAN);
* distância medida da divisa do imóvel até o ponto ou a borda do polígono;
* posição a menos de 15 m da divisa é "junto à divisa" (a coordenada do IPHAN
  tem 4 casas decimais, cerca de 11 m), nem "dentro" nem "fora";
* sítio na vizinhança não é restrição dentro do imóvel.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

WFS_URL = "https://geoserver.iphan.gov.br/geoserver/ows"
LAYER_POINTS = "SICG:sitios"
LAYER_POLYGONS = "SICG:sitios_pol"
SOURCE_LABEL = "IPHAN — Cadastro de sítios arqueológicos (SICG)"
RADIUS_KM = 10.0
ENVELOPE_PAD_KM = 0.5
BORDER_TOLERANCE_M = 15.0
MAX_FEATURES = 5000
# Mesmo tempo limite do cliente das restrições territoriais (40 s). O IPHAN
# respondeu abaixo de 300 ms nas medições de 13/09/2026; só mudar com medição.
TIMEOUT_S = 40.0
ATTEMPTS = 2
RETRY_PAUSE_S = 0.7
USER_AGENT = "Raio-X-Territorial/iphan-sicg"
_ENVELOPE_EPS_DEG = 0.01
_GEOD = Geod(ellps="GRS80")

STATE_FOUND = "found"
STATE_NOT_FOUND = "not_found"
STATE_PENDING = "pending"

NOTE_NEAR = (
    "Sítio próximo não é restrição dentro do imóvel. Ele indica que a região tem histórico de "
    "ocupação antiga; numa obra com movimento de terra, achados arqueológicos devem ser comunicados ao IPHAN."
)
NOTE_REGISTRY = (
    "O IPHAN só mostra os sítios já cadastrados com localização. Um sítio que não está no cadastro "
    "não aparece nesta leitura."
)


# ---------------------------------------------------------------- consulta --
def _as_geometry(car: Any) -> BaseGeometry:
    if isinstance(car, BaseGeometry):
        return car
    if isinstance(car, dict) and car.get("type") == "FeatureCollection":
        car = (car.get("features") or [{}])[0].get("geometry")
    elif isinstance(car, dict) and car.get("type") == "Feature":
        car = car.get("geometry")
    return shape(car)


def query_envelope(car: Any, radius_km: float = RADIUS_KM) -> list[float]:
    """Caixa lon/lat do imóvel ampliada pelo raio + folga. O corte real é pela distância."""
    geom = _as_geometry(car)
    xmin, ymin, xmax, ymax = geom.bounds
    pad_km = float(radius_km) + ENVELOPE_PAD_KM
    lat_ref = max(abs(ymin), abs(ymax))
    dlat = pad_km / 111.0
    dlon = pad_km / (111.0 * max(0.2, math.cos(math.radians(lat_ref))))
    return [xmin - dlon, ymin - dlat, xmax + dlon, ymax + dlat]


def build_params(typename: str, envelope: list[float]) -> dict[str, str]:
    """WFS 1.0.0 com EPSG:4674 usa eixo lon/lat.

    Em WFS 1.1.0/2.0.0 a mesma caixa com EPSG:4674 é lida como lat/lon e o
    servidor devolve zero feições sem erro (medido em Lagoa Santa: 87 contra 0).
    """
    xmin, ymin, xmax, ymax = envelope
    return {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": typename,
        "srsName": "EPSG:4674",
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "maxFeatures": str(MAX_FEATURES),
        "outputFormat": "application/json",
    }


def parse_layer_response(status_code: int, body: bytes | str, envelope: list[float]) -> dict[str, Any]:
    """Valida uma resposta WFS. Qualquer dúvida vira falha (nunca lista vazia)."""
    if status_code != 200:
        return {"ok": False, "http": status_code, "detail": "http_status"}
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except Exception:
        return {"ok": False, "http": status_code, "detail": "resposta_nao_json"}
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        return {"ok": False, "http": status_code, "detail": "resposta_sem_featurecollection"}
    features = data["features"]
    matched, returned = data.get("numberMatched"), data.get("numberReturned")
    if isinstance(returned, int) and returned != len(features):
        return {"ok": False, "http": status_code, "detail": "contagem_divergente"}
    if isinstance(matched, int) and matched != len(features):
        return {"ok": False, "http": status_code, "detail": "resposta_truncada"}
    if not isinstance(matched, int) and len(features) >= MAX_FEATURES:
        return {"ok": False, "http": status_code, "detail": "resposta_possivelmente_truncada"}
    env_box = box(*envelope).buffer(_ENVELOPE_EPS_DEG)
    for feature in features:
        try:
            geom = shape(feature.get("geometry"))
        except Exception:
            return {"ok": False, "http": status_code, "detail": "geometria_invalida"}
        if geom.is_empty or not env_box.intersects(geom):
            # Eixo trocado ou filtro ignorado: a resposta não é confiável.
            return {"ok": False, "http": status_code, "detail": "feicao_fora_da_caixa"}
    return {"ok": True, "http": status_code, "features": features, "number_matched": matched if isinstance(matched, int) else len(features)}


async def fetch_layer(client: httpx.AsyncClient, typename: str, envelope: list[float], attempts: int = ATTEMPTS) -> dict[str, Any]:
    """Consulta uma camada; tenta de novo sozinha antes de desistir."""
    last: dict[str, Any] = {"ok": False, "detail": "nao_consultado"}
    for attempt in range(max(1, attempts)):
        try:
            response = await client.get(WFS_URL, params=build_params(typename, envelope))
            last = parse_layer_response(response.status_code, response.content, envelope)
        except Exception as exc:  # rede, tempo limite, TLS
            last = {"ok": False, "detail": type(exc).__name__}
        last["attempts"] = attempt + 1
        if last.get("ok"):
            return last
        if attempt < attempts - 1:
            await asyncio.sleep(RETRY_PAUSE_S)
    return last


# ------------------------------------------------------------------ análise --
def _metric_transformer(geom: BaseGeometry) -> Transformer:
    c = geom.centroid
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs")
    return Transformer.from_crs("EPSG:4674", local, always_xy=True)


def _site_key(props: dict[str, Any], feature: dict[str, Any]) -> str:
    code = str(props.get("co_iphan") or "").strip()
    if code:
        return code
    if props.get("id_bem") not in (None, ""):
        return f"id_bem:{props.get('id_bem')}"
    return f"feature:{feature.get('id')}"


def _date_br(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return None


def format_distance(distance_m: float) -> str:
    """6656 m -> "6,7 km"; 850 m -> "850 m" (pt-BR, arredondamento comercial)."""
    d = Decimal(str(max(0.0, float(distance_m))))
    if d < 1000:
        return f"{int((d / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10)} m"
    km = (d / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{km:,.1f}".translate(str.maketrans(",.", ".,")) + " km"


def _clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def analyze_sites(
    car: Any,
    point_features: list[dict[str, Any]],
    polygon_features: list[dict[str, Any]],
    radius_km: float = RADIUS_KM,
    keep_geoms: bool = False,
) -> dict[str, Any]:
    """Função pura: junta pontos e polígonos e classifica cada sítio.

    Devolve ``inside`` (dentro), ``border`` (junto à divisa) e ``near`` (fora,
    até ``radius_km`` da divisa), ordenados por distância.
    """
    car_geom = _as_geometry(car)
    tr = _metric_transformer(car_geom)
    car_m = transform(tr.transform, car_geom)
    boundary_m = car_m.boundary
    sites: dict[str, dict[str, Any]] = {}
    for kind, features in (("ponto", point_features or []), ("polígono", polygon_features or [])):
        for feature in features:
            try:
                geom = shape(feature.get("geometry"))
            except Exception:
                continue
            if geom.is_empty:
                continue
            props = feature.get("properties") or {}
            key = _site_key(props, feature)
            site = sites.setdefault(key, {"props": {}, "geoms": []})
            for k, v in props.items():
                if site["props"].get(k) in (None, "") and v not in (None, ""):
                    site["props"][k] = v
            site["geoms"].append((kind, geom))

    inside: list[dict[str, Any]] = []
    border: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    intersections: list[BaseGeometry] = []
    limit_m = float(radius_km) * 1000.0
    for key, site in sites.items():
        kinds = [k for k in ("ponto", "polígono") if any(kind == k for kind, _ in site["geoms"])]
        poly_area_m2 = 0.0
        point_inside_far_from_border = False
        distance_m = math.inf
        border_distance_m = math.inf
        site_intersections: list[BaseGeometry] = []
        for kind, geom in site["geoms"]:
            gm = transform(tr.transform, geom)
            distance_m = min(distance_m, float(car_m.distance(gm)))
            border_distance_m = min(border_distance_m, float(boundary_m.distance(gm)))
            if kind == "polígono" and geom.geom_type in ("Polygon", "MultiPolygon"):
                inter_m = car_m.intersection(gm)
                if not inter_m.is_empty and inter_m.area > 0:
                    poly_area_m2 += float(inter_m.area)
                    site_intersections.append(car_geom.intersection(geom))
            elif kind == "ponto" and car_m.contains(gm) and float(boundary_m.distance(gm)) > BORDER_TOLERANCE_M:
                point_inside_far_from_border = True
        if poly_area_m2 > 0 or point_inside_far_from_border:
            situacao = "dentro"
        elif distance_m <= BORDER_TOLERANCE_M or border_distance_m <= BORDER_TOLERANCE_M:
            situacao = "junto_a_divisa"
        elif distance_m <= limit_m:
            situacao = "vizinhanca"
        else:
            continue
        props = site["props"]
        classificacao = _clean(props.get("ds_classificacao"))
        if classificacao and classificacao.lower() == "sem classificação":
            classificacao = None
        area_ha = None
        if site_intersections:
            area_ha = round(sum(abs(_GEOD.geometry_area_perimeter(g)[0]) for g in site_intersections) / 10000.0, 4)
        row = {
            "codigo_iphan": _clean(props.get("co_iphan")) or key,
            "nome": _clean(props.get("identificacao_bem")),
            "tipo": _clean(props.get("ds_tipo_bem")),
            "natureza": _clean(props.get("ds_natureza")),
            "classificacao": classificacao,
            "cadastro": _date_br(props.get("dt_cadastro")),
            "geometria": " e ".join(kinds),
            "situacao": situacao,
            "distancia_m": 0 if situacao == "dentro" else int(round(distance_m)),
            "distancia_texto": None if situacao != "vizinhanca" else format_distance(distance_m),
            "area_dentro_ha": area_ha,
        }
        {"dentro": inside, "junto_a_divisa": border, "vizinhanca": near}[situacao].append(row)
        intersections.extend(site_intersections)
    for rows in (inside, border, near):
        rows.sort(key=lambda r: (r["distancia_m"], r["nome"] or ""))
    out: dict[str, Any] = {"inside": inside, "border": border, "near": near, "radius_km": float(radius_km)}
    if keep_geoms:
        out["_geoms"] = intersections
    return out


def _layer_meta(layer: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in layer.items() if k != "features"}


def build_result(car: Any, points: dict[str, Any], polygons: dict[str, Any], radius_km: float = RADIUS_KM, keep_geoms: bool = False) -> dict[str, Any]:
    """Resultado com estado explícito a partir das duas respostas já validadas."""
    base: dict[str, Any] = {
        "source": SOURCE_LABEL,
        "service": WFS_URL,
        "radius_km": float(radius_km),
        "consulted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layers": {LAYER_POINTS: _layer_meta(points), LAYER_POLYGONS: _layer_meta(polygons)},
    }
    if not (points.get("ok") and polygons.get("ok")):
        # Resultado parcial nunca vira "0": a seção inteira fica pendente.
        return {**base, "ok": False, "state_inside": STATE_PENDING, "state_near": STATE_PENDING,
                "inside": [], "border": [], "near": [], "detail": "consulta_pendente"}
    analysis = analyze_sites(car, points.get("features") or [], polygons.get("features") or [], radius_km, keep_geoms)
    found_inside = bool(analysis["inside"] or analysis["border"])
    return {
        **base,
        **analysis,
        "ok": True,
        "state_inside": STATE_FOUND if found_inside else STATE_NOT_FOUND,
        "state_near": STATE_FOUND if analysis["near"] else STATE_NOT_FOUND,
        "features_in_envelope": int(points.get("number_matched") or 0) + int(polygons.get("number_matched") or 0),
    }


async def query_iphan_sites(car_geometry: Any, client: httpx.AsyncClient | None = None, radius_km: float = RADIUS_KM, keep_geoms: bool = False) -> dict[str, Any]:
    """Consulta pura do IPHAN oficial: dentro do imóvel e vizinhança de ``radius_km``."""
    car = _as_geometry(car_geometry)
    envelope = query_envelope(car, radius_km)
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as own:
            points, polygons = await asyncio.gather(fetch_layer(own, LAYER_POINTS, envelope), fetch_layer(own, LAYER_POLYGONS, envelope))
    else:
        points, polygons = await asyncio.gather(fetch_layer(client, LAYER_POINTS, envelope), fetch_layer(client, LAYER_POLYGONS, envelope))
    return build_result(car, points, polygons, radius_km, keep_geoms)


# ----------------------------------------------- checagem de restrição (tc) --
def constraint_service_result(iphan: dict[str, Any], label: str = "Sítio Arqueológico") -> dict[str, Any]:
    """Converte o resultado para o formato de ``territorial_constraints._query_one``.

    ``ok`` só é verdadeiro com as duas camadas completas. ``occurrence_count``
    conta sítios dentro e junto à divisa (precisão da coordenada). O resultado
    completo segue em ``iphan`` para a vizinhança de 10 km.
    """
    geoms = list(iphan.pop("_geoms", []) or [])
    if not iphan.get("ok"):
        return {"ok": False, "label": label, "source": SOURCE_LABEL, "service": WFS_URL,
                "detail": "consulta pendente", "iphan": iphan}
    hits = list(iphan.get("inside") or []) + list(iphan.get("border") or [])
    area = sum(float(r.get("area_dentro_ha") or 0.0) for r in hits)
    occurrences = [
        {
            "area_intersection_ha": r.get("area_dentro_ha"),
            "attributes": {k: r.get(k) for k in ("nome", "tipo", "natureza", "classificacao", "codigo_iphan", "situacao", "geometria") if r.get(k)},
        }
        for r in hits
    ]
    return {
        "ok": True,
        "status": 200,
        "label": label,
        "source": SOURCE_LABEL,
        "service": WFS_URL,
        "layer_id": f"{LAYER_POINTS}+{LAYER_POLYGONS}",
        "feature_count_bbox": iphan.get("features_in_envelope"),
        "occurrence_count": len(hits),
        "area_unique_ha": round(area, 6),
        "occurrences": occurrences[:20],
        "iphan": iphan,
        "_geoms": geoms,
    }


async def query_constraint(client: httpx.AsyncClient, car: Any, label: str = "Sítio Arqueológico") -> dict[str, Any]:
    """Substituto do ``_query_one`` para a chave ``sitio_arqueologico``."""
    try:
        iphan = await query_iphan_sites(car, client=client, keep_geoms=True)
    except Exception as exc:
        iphan = {"ok": False, "state_inside": STATE_PENDING, "state_near": STATE_PENDING, "detail": type(exc).__name__,
                 "source": SOURCE_LABEL, "inside": [], "border": [], "near": []}
    return constraint_service_result(iphan, label)


def iphan_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Acha o resultado IPHAN dentro do ``result`` da análise (restrições territoriais)."""
    services = ((result or {}).get("territorial_constraints") or {}).get("services") or {}
    service = services.get("sitio_arqueologico") or {}
    iphan = service.get("iphan")
    return iphan if isinstance(iphan, dict) else None


# ------------------------------------------------------------------ payload --
def _describe(row: dict[str, Any]) -> str:
    parts = [p for p in (row.get("natureza"), row.get("classificacao")) if p]
    if not parts and row.get("tipo"):
        parts = [row["tipo"]]
    return ", ".join(parts).lower()


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def iphan_payload(iphan: dict[str, Any] | None) -> dict[str, Any]:
    """Campos novos para o relatório, com estado explícito.

    ``state``: ``found`` (há sítio dentro, junto à divisa ou na vizinhança),
    ``not_found`` (as duas camadas responderam e não há sítio até 10 km) ou
    ``pending`` (a fonte não respondeu por inteiro; nada é afirmado).
    """
    title = "Patrimônio arqueológico (IPHAN)"
    if not iphan or not iphan.get("ok"):
        return {
            "state": STATE_PENDING,
            "title": title,
            "inside": {"state": STATE_PENDING, "count": None, "rows": []},
            "near": {"state": STATE_PENDING, "count": None, "rows": [], "radius_km": RADIUS_KM},
            "headline": "Patrimônio arqueológico: consulta pendente.",
            "near_text": None,
            "notes": [],
            "level": "neutral",
            "source_row": {"name": "Patrimônio arqueológico — IPHAN", "description": "A fonte não respondeu nesta emissão.",
                           "status": "INDISPONÍVEL", "level": "neutral"},
            "compliance_row": {"label": "Sítio arqueológico — IPHAN", "text": "Consulta pendente.", "badge": "CONSULTA PENDENTE", "level": "neutral"},
        }
    radius = float(iphan.get("radius_km") or RADIUS_KM)
    radius_txt = f"{radius:g}".replace(".", ",")
    inside = list(iphan.get("inside") or [])
    border = list(iphan.get("border") or [])
    near = list(iphan.get("near") or [])
    hits = inside + border
    inside_rows = [[r.get("nome") or r.get("codigo_iphan"), _describe(r), "dentro do imóvel" if r["situacao"] == "dentro" else "junto à divisa"] for r in hits]
    near_rows = [[r.get("nome") or r.get("codigo_iphan"), _describe(r), r.get("distancia_texto")] for r in near]
    sentences: list[str] = []
    if inside:
        names = "; ".join(f"{r.get('nome') or r.get('codigo_iphan')} ({_describe(r)})" if _describe(r) else str(r.get("nome") or r.get("codigo_iphan")) for r in inside)
        n = len(inside)
        sentences.append(
            f"{n} {_plural(n, 'sítio arqueológico cadastrado', 'sítios arqueológicos cadastrados')} no IPHAN "
            f"{_plural(n, 'está', 'estão')} dentro do imóvel: {names}. Antes de obra ou movimento de terra nesse local, é preciso consultar o IPHAN."
        )
    if border:
        names = "; ".join(str(r.get("nome") or r.get("codigo_iphan")) for r in border)
        n = len(border)
        sentences.append(
            f"{n} {_plural(n, 'sítio arqueológico cadastrado', 'sítios arqueológicos cadastrados')} no IPHAN "
            f"{_plural(n, 'está', 'estão')} junto à divisa do imóvel: {names}. A posição cadastrada tem precisão de cerca de 11 m; "
            "antes de obra ou movimento de terra perto da divisa, é preciso consultar o IPHAN."
        )
    if not hits:
        sentences.append("Nenhum sítio arqueológico cadastrado no IPHAN dentro do imóvel.")
    if near:
        n = len(near)
        near_text = f"Há {n} {_plural(n, 'sítio cadastrado', 'sítios cadastrados')} na vizinhança, a até {radius_txt} km da divisa."
    else:
        near_text = f"Nenhum sítio arqueológico cadastrado no IPHAN a até {radius_txt} km da divisa."
    notes = [NOTE_NEAR, NOTE_REGISTRY] if near else [NOTE_REGISTRY]
    level = "attention" if hits else "neutral"
    def _count(n: int, where: str) -> str:
        return f"nenhum {where}" if n == 0 else f"{n} {where}"

    parts = [_count(len(inside), "dentro do imóvel")]
    if border:
        parts.append(_count(len(border), "junto à divisa"))
    parts.append(_count(len(near), f"a até {radius_txt} km"))
    count_txt = "Sítios cadastrados: " + "; ".join(parts) + "."
    return {
        "state": STATE_FOUND if (hits or near) else STATE_NOT_FOUND,
        "title": title,
        "inside": {"state": STATE_FOUND if hits else STATE_NOT_FOUND, "count": len(hits), "rows": inside_rows,
                   "headers": ["Sítio", "Tipo", "Posição"]},
        "near": {"state": STATE_FOUND if near else STATE_NOT_FOUND, "count": len(near), "rows": near_rows,
                 "headers": ["Sítio", "Tipo", "Distância da divisa"], "radius_km": radius},
        "headline": " ".join(sentences),
        "near_text": near_text,
        "notes": notes,
        "level": level,
        "source_row": {
            "name": "Patrimônio arqueológico — IPHAN",
            "description": f"Cadastro oficial de sítios do IPHAN (pontos e polígonos), consultado nesta emissão. {count_txt}",
            "status": "CONSULTADA",
            "level": "attention" if hits else "ok",
        },
        "compliance_row": {
            "label": "Sítio arqueológico — IPHAN",
            "text": count_txt,
            "badge": "ATENÇÃO" if hits else "CONSULTADA",
            "level": level,
        },
    }


print("RX_IPHAN_SICG=official_points_polygons_radius10km", flush=True)

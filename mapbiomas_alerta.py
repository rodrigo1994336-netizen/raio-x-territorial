from __future__ import annotations

"""MapBiomas Alerta: alertas validados de desmatamento sobre um CAR.

Forma de uso decidida pelo dono (13/09/2026):

* só a consulta pública e sem token ``ruralProperty(propertyCode)``; nunca conta,
  token ou tentativa de contornar limite (HTTP 429 vira pendência, sem repetir);
* crédito explícito "Fonte: MapBiomas Alerta (CC BY-SA 3.0 BR)" e link para o
  laudo público de cada alerta; nenhuma imagem do laudo é copiada;
* cache curto por CAR; o endereço da API nunca vai para o cliente
  (``public_view`` remove os campos internos, que começam com ``_``).

Estados da consulta:

* ``found``     a fonte conhece o CAR e há alerta validado sobre ele;
* ``not_found`` a fonte conhece o CAR e a lista de alertas validados veio vazia;
* ``pending``   erro, tempo esgotado, limite, resposta estranha ou CAR fora da
  base deles (``ruralProperty: null``). Nunca vira "sem alertas".

``combine_deforestation_alerts`` junta MapBiomas, DETER e PRODES pela regra da
seção 5 do relatório 03: corte na data do último ano PRODES publicado, uma
abertura vista por várias fontes é um evento só, e a área é a união das
geometrias dentro do CAR (nunca a soma entre fontes).
"""

import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

try:
    from pyproj import Geod
    from shapely import wkt as _wkt
    from shapely.geometry import shape as _shape
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    _GEOD = Geod(ellps="GRS80")
    GEO_AVAILABLE = True
except Exception:  # pragma: no cover - produção e CI têm shapely/pyproj
    _GEOD = _wkt = _shape = BaseGeometry = unary_union = make_valid = None
    GEO_AVAILABLE = False

SOURCE_ID = "mapbiomas_alerta"
SOURCE_LABEL = "MapBiomas Alerta"
LICENSE = "CC BY-SA 3.0 BR"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/br/"
CREDIT = "Fonte: MapBiomas Alerta (CC BY-SA 3.0 BR)"
DISCLAIMER = (
    "Alerta validado de desmatamento não é auto de infração; indica abertura "
    "detectada por satélite e conferida por analistas."
)
PENDING_TEXT = "MapBiomas Alerta: consulta pendente."
DETER_LABEL = "INPE (DETER)"
DETER_CREDIT = "Fonte: INPE/TerraBrasilis (CC BY-SA 4.0)"
DETER_DISCLAIMER = (
    "Alerta do INPE não é auto de infração; é aviso de satélite de que a vegetação foi "
    "retirada, e o mapa anual PRODES confirma ou não."
)

_ENDPOINT = "https://plataforma.alerta.mapbiomas.org/api/v2/graphql"
_LAUDO_BASE = "https://plataforma.alerta.mapbiomas.org/alerta"
_HEADERS = {
    "User-Agent": "Raio-X-Territorial/F2 (+consulta publica por CAR)",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
# Mesmo padrão do prodes_fast_v24 (read 16 s), como pede o relatório 03.
# Medido deste computador em 13/09/2026: 1,1 a 2,0 s. Do Render ainda não foi
# medido; só mudar depois de medir de lá.
TIMEOUT = httpx.Timeout(16.0, connect=6.0, read=16.0, write=10.0, pool=8.0)
MAX_ATTEMPTS = 2  # uma nova tentativa só para falha rápida (rede ou 5xx)
RETRY_PAUSE_SECONDS = 0.5
CACHE_TTL_SECONDS = 3600
CACHE_MAX_ENTRIES = 512
# Fim do último ano PRODES publicado, medido em 13/09/2026 (DETER Cerrado
# `_hist` até 31/07/2025 e deter-amz:prodes_reference end_date). Só vale quando
# o chamador não informa o corte e nenhum polígono PRODES mostra ano mais novo.
PRODES_CUTOFF_FALLBACK = date(2025, 7, 31)
MIN_AREA_HA = 0.0001  # 1 m²: abaixo disso é ruído numérico de borda

QUERY = """query RaioXAlertaCar($c:String){
  lastAlertPublication{ publishedAt }
  alertDateRange{ maxDetectedAt maxPublishedAt }
  ruralProperty(propertyCode:$c){
    propertyCode areaHa carUpdatedAt version stateAcronym
    alerts{
      alertCode areaHa detectedAt publishedAt statusName sources
      crossedBiomes crossedCities deforestationClasses geometryWkt
      crossedRuralProperties{ code alertAreaInCar }
    }
  }
}"""

_CAR_RE = re.compile(r"^[A-Z]{2}-\d{7}-[0-9A-F]{32}$")
_SOURCE_WORDS = {
    "CERRADO": "Cerrado", "AMAZONIA": "Amazônia", "CAATINGA": "Caatinga",
    "PANTANAL": "Pantanal", "PAMPA": "Pampa", "MATA": "Mata", "ATLANTICA": "Atlântica",
}
_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- utilidades
def normalize_car_code(value: Any) -> str | None:
    code = re.sub(r"[\s.]", "", str(value or "")).upper()
    return code if _CAR_RE.match(code) else None


def laudo_url(alert_code: int) -> str:
    return f"{_LAUDO_BASE}/{int(alert_code)}"


def laudo_car_url(alert_code: int, car_code: str) -> str:
    return f"{_LAUDO_BASE}/{int(alert_code)}/car/{car_code}"


def _iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _br_date(value: Any) -> str:
    d = _iso_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _br_ha(value: float) -> str:
    return f"{float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _source_label(code: Any) -> str:
    parts = [p for p in str(code or "").strip().upper().split("-") if p]
    if not parts:
        return ""
    return " ".join([parts[0]] + [_SOURCE_WORDS.get(p, p.capitalize()) for p in parts[1:]])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- geometria
def _area_ha(geom: Any) -> float:
    if not GEO_AVAILABLE or geom is None or geom.is_empty:
        return 0.0
    return abs(_GEOD.geometry_area_perimeter(geom)[0]) / 10000.0


def _valid(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return None
    return geom if geom.is_valid else make_valid(geom)


def _inside_brazil(geom: Any) -> bool:
    # Guarda contra eixo trocado (lat/lon): o Brasil fica em lon -75..-28, lat -35..6.
    minx, miny, maxx, maxy = geom.bounds
    return -75.0 <= minx <= maxx <= -28.0 and -35.0 <= miny <= maxy <= 6.0


def _geometry(value: Any) -> Any:
    """GeoJSON (dict), WKT/EWKT (str) ou shapely → shapely válido em lon/lat."""
    if not GEO_AVAILABLE or value is None:
        return None
    try:
        if isinstance(value, BaseGeometry):
            geom = value
        elif isinstance(value, dict):
            geom = _shape(value.get("geometry") if value.get("type") == "Feature" else value)
        else:
            geom = _wkt.loads(re.sub(r"^\s*SRID=\d+;", "", str(value)))
        geom = _valid(geom)
    except Exception:
        return None
    if geom is None or geom.is_empty or not _inside_brazil(geom):
        return None
    return geom


# ---------------------------------------------------------------- resposta
def _pending(code: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    out = {
        "source_id": SOURCE_ID,
        "source": SOURCE_LABEL,
        "state": "pending",
        "answered": False,
        "car_code": code,
        "pending_reason": reason,
        "alert_count": None,
        "alerts": [],
        "text": {"summary": PENDING_TEXT, "items": []},
    }
    out.update(extra)
    return out


def parse_response(
    body: Any,
    car_code: str,
    car_geometry: Any = None,
    car_updated_at: Any = None,
) -> dict[str, Any]:
    """Transforma a resposta GraphQL no resultado do Raio-X (sem rede)."""
    code = normalize_car_code(car_code)
    if not code:
        return _pending(None, "invalid_car_code")
    if not isinstance(body, dict):
        return _pending(code, "schema_unexpected")
    data = body.get("data")
    errors = body.get("errors") or []
    if not isinstance(data, dict) or "ruralProperty" not in data:
        auth = any("token" in str((e or {}).get("message", "")).lower() for e in errors)
        return _pending(code, "auth_required" if auth else ("graphql_error" if errors else "schema_unexpected"))
    prop = data.get("ruralProperty")
    if prop is None:
        auth = any("token" in str((e or {}).get("message", "")).lower() for e in errors)
        if auth:
            return _pending(code, "auth_required")
        return _pending(code, "graphql_error" if errors else "car_not_in_source")
    if not isinstance(prop, dict):
        return _pending(code, "schema_unexpected")
    if normalize_car_code(prop.get("propertyCode")) != code:
        return _pending(code, "car_code_mismatch")
    raw_alerts = prop.get("alerts")
    if not isinstance(raw_alerts, list):
        return _pending(code, "schema_unexpected")

    pub = data.get("lastAlertPublication") or {}
    rng = data.get("alertDateRange") or {}
    last_pub = _iso_date(pub.get("publishedAt") if isinstance(pub, dict) else None)
    max_detected = _iso_date(rng.get("maxDetectedAt") if isinstance(rng, dict) else None)
    source_car_date = _iso_date(prop.get("carUpdatedAt"))
    car = _geometry(car_geometry)
    if car_geometry is not None and car is None:
        return _pending(code, "car_geometry_invalid")

    alerts: list[dict[str, Any]] = []
    ignored = {"not_validated": 0, "outside_current_car": 0, "malformed": 0}
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            ignored["malformed"] += 1
            continue
        if str(raw.get("statusName") or "").lower() != "published":
            ignored["not_validated"] += 1
            continue
        detected = _iso_date(raw.get("detectedAt"))
        published = _iso_date(raw.get("publishedAt"))
        geom = _geometry(raw.get("geometryWkt"))
        try:
            alert_code = int(raw.get("alertCode"))
        except (TypeError, ValueError):
            alert_code = None
        if alert_code is None or alert_code <= 0 or detected is None or geom is None:
            ignored["malformed"] += 1
            continue
        crossing = None
        for row in raw.get("crossedRuralProperties") or []:
            if isinstance(row, dict) and normalize_car_code(row.get("code")) == code:
                try:
                    crossing = float(row.get("alertAreaInCar"))
                except (TypeError, ValueError):
                    crossing = None
        if car is not None:
            inside = car.intersection(geom)
            area_in_car = _area_ha(inside)
            if area_in_car < MIN_AREA_HA:
                ignored["outside_current_car"] += 1
                continue
            method = "intersecao_raio_x"
        elif crossing is not None and crossing > 0:
            area_in_car, method = crossing, "cruzamento_mapbiomas"
        else:
            area_in_car, method = None, None
        try:
            area_alert = float(raw.get("areaHa"))
        except (TypeError, ValueError):
            area_alert = None
        sources = [str(s) for s in raw.get("sources") or [] if s]
        item = {
            "alert_code": alert_code,
            "detected_at": detected.isoformat(),
            "published_at": published.isoformat() if published else None,
            "area_alert_ha": round(area_alert, 4) if area_alert is not None else None,
            "area_in_car_ha": round(area_in_car, 4) if area_in_car is not None else None,
            "area_in_car_method": method,
            "area_in_car_mapbiomas_ha": round(crossing, 4) if crossing is not None else None,
            "biomes": [str(b) for b in raw.get("crossedBiomes") or [] if b],
            "municipalities": [str(c) for c in raw.get("crossedCities") or [] if c],
            "alert_sources": sources,
            "alert_sources_label": ", ".join(filter(None, (_source_label(s) for s in sources))),
            "pressure_vectors": [str(c) for c in raw.get("deforestationClasses") or [] if c],
            "laudo_url": laudo_url(alert_code),
            "laudo_car_url": laudo_car_url(alert_code, code),
            "_geometry_wkt": geom.wkt,
        }
        alerts.append({k: v for k, v in item.items() if v not in (None, [], "")})

    if not alerts and ignored["malformed"]:
        # Havia alerta, mas não deu para ler: isso não é ausência.
        return _pending(code, "schema_unexpected", ignored=ignored)

    alerts.sort(key=lambda a: (a["detected_at"], a["alert_code"]), reverse=True)
    state = "found" if alerts else "not_found"
    out: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "source": SOURCE_LABEL,
        "state": state,
        "answered": True,
        "car_code": code,
        "alert_count": len(alerts),
        "alerts": alerts,
        "ignored": ignored,
        "incomplete": bool(ignored["malformed"]),
        "last_publication_date": last_pub.isoformat() if last_pub else None,
        "max_detected_date": max_detected.isoformat() if max_detected else None,
        "source_car_version_date": source_car_date.isoformat() if source_car_date else None,
        "credit": CREDIT,
        "license": LICENSE,
        "license_url": LICENSE_URL,
    }
    ours = _iso_date(car_updated_at)
    out["source_car_version_older"] = bool(ours and source_car_date and ours > source_car_date)
    if state == "found":
        out["disclaimer"] = DISCLAIMER
    out["text"] = _source_text(out)
    return {k: v for k, v in out.items() if v is not None}


def _source_text(res: dict[str, Any]) -> dict[str, Any]:
    items = []
    if res["state"] == "found":
        n = res["alert_count"]
        summary = (
            f"{n} {_plural(n, 'alerta validado', 'alertas validados')} de desmatamento "
            f"sobre o imóvel no MapBiomas Alerta."
        )
        for a in res["alerts"]:
            parts = [f"Alerta nº {a['alert_code']}"]
            if a.get("area_in_car_ha") is not None:
                parts.append(f"{_br_ha(a['area_in_car_ha'])} ha dentro do imóvel")
            when = f"detectado em {_br_date(a['detected_at'])}"
            if a.get("published_at"):
                when += f" e publicado em {_br_date(a['published_at'])}"
            parts.append(when)
            extra = []
            if a.get("biomes"):
                extra.append(", ".join(a["biomes"]))
            if a.get("alert_sources_label"):
                extra.append(f"fontes: {a['alert_sources_label']}")
            line = ", ".join(parts) + (f" ({'; '.join(extra)})" if extra else "") + "."
            items.append({"text": line, "laudo_url": a["laudo_car_url"]})
    else:
        summary = "Nenhum alerta validado de desmatamento sobre este CAR no MapBiomas Alerta"
        if res.get("last_publication_date"):
            summary += f" (publicações até {_br_date(res['last_publication_date'])})"
        summary += "."
    if res.get("source_car_version_older") and res.get("source_car_version_date"):
        summary += f" O MapBiomas cruzou a versão do CAR de {_br_date(res['source_car_version_date'])}."
    out = {"summary": summary, "items": items, "credit": CREDIT}
    if res["state"] == "found":
        out["disclaimer"] = DISCLAIMER
    return out


def public_view(result: Any) -> Any:
    """Cópia sem campos internos (``_geometry_wkt`` etc.) para cliente/PDF."""
    if isinstance(result, dict):
        return {k: public_view(v) for k, v in result.items() if not str(k).startswith("_")}
    if isinstance(result, list):
        return [public_view(v) for v in result]
    return result


def audit_state(result: dict[str, Any] | None) -> str:
    """Estado no vocabulário do source_audit_registry_v49."""
    state = (result or {}).get("state")
    return {"found": "ANSWERED_HIT", "not_found": "ANSWERED_CLEAR", "pending": "FAILED"}.get(state, "NOT_QUERIED")


# ---------------------------------------------------------------- rede
def _cache_get(code: str) -> dict[str, Any] | None:
    with _cache_lock:
        item = _cache.get(code)
        if not item:
            return None
        if time.monotonic() - item[0] >= CACHE_TTL_SECONDS:
            _cache.pop(code, None)
            return None
        _cache.move_to_end(code)
        return item[1]


def _cache_put(code: str, body: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[code] = (time.monotonic(), body)
        _cache.move_to_end(code)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _pause(cancel_event: Any, seconds: float) -> None:
    if seconds <= 0:
        return
    if cancel_event is not None and hasattr(cancel_event, "wait"):
        cancel_event.wait(seconds)
    else:
        time.sleep(seconds)


def query_mapbiomas_alerta(
    car_code: Any,
    car_geometry: Any = None,
    *,
    car_updated_at: Any = None,
    cancel_event: Any = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Consulta pública por código do CAR. Nunca levanta exceção."""
    started = time.monotonic()
    code = normalize_car_code(car_code)
    if not code:
        return _pending(None, "invalid_car_code", queried_at=_now_iso(), elapsed_ms=0, attempts=0)

    body = _cache_get(code) if use_cache else None
    cached = body is not None
    attempts = 0
    reason = None
    if body is None:
        own = client is None
        http = client or httpx.Client(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=False)
        try:
            while attempts < MAX_ATTEMPTS:
                if cancel_event is not None and cancel_event.is_set():
                    reason = "cancelled"
                    break
                if attempts:
                    _pause(cancel_event, RETRY_PAUSE_SECONDS)
                    if cancel_event is not None and cancel_event.is_set():
                        reason = "cancelled"
                        break
                attempts += 1
                try:
                    resp = http.post(_ENDPOINT, json={"query": QUERY, "variables": {"c": code}})
                except httpx.TimeoutException:
                    reason = "timeout"  # já gastou o prazo inteiro: não repete
                    break
                except httpx.TransportError:
                    reason = "network_error"
                    continue
                if resp.status_code == 429:
                    reason = "rate_limited"  # respeita o limite da fonte: não repete
                    break
                if resp.status_code >= 500:
                    reason = "http_5xx"
                    continue
                if resp.status_code != 200:
                    reason = "http_4xx"
                    break
                try:
                    body = resp.json()
                except ValueError:
                    reason = "invalid_json"
                    break
                reason = None
                break
        finally:
            if own:
                http.close()

    elapsed = round((time.monotonic() - started) * 1000)
    meta = {"queried_at": _now_iso(), "elapsed_ms": elapsed, "attempts": attempts, "cached": cached}
    if body is None:
        result = _pending(code, reason or "no_response", **meta)
    else:
        result = parse_response(body, code, car_geometry, car_updated_at)
        result.update(meta)
        if result["answered"] and not cached and use_cache:
            _cache_put(code, body)  # só resposta válida entra no cache; pendência tenta de novo
    print(
        f"RX_MAPBIOMAS_ALERTA={result['state']}:{elapsed}ms:attempts={attempts}:cached={cached}"
        + (f":reason={result.get('pending_reason')}" if result["state"] == "pending" else ""),
        flush=True,
    )
    return result


async def query_mapbiomas_alerta_async(car_code: Any, car_geometry: Any = None, **kwargs: Any) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(query_mapbiomas_alerta, car_code, car_geometry, **kwargs)


# ---------------------------------------------------------------- combinação
def _prodes_year(props: dict[str, Any]) -> int | None:
    for key in ("year", "ano", "year_prodes"):
        try:
            return int(str(props.get(key))[:4])
        except (TypeError, ValueError):
            continue
    return None


def _prodes_year_of(d: date) -> int:
    """Ano PRODES N vai de 1º/08/N-1 a 31/07/N."""
    return d.year + 1 if d.month >= 8 else d.year


def _resolve_cutoff(prodes_cutoff: Any) -> tuple[date, str]:
    """Corte = fim do último ano PRODES publicado.

    Não é deduzido dos polígonos do entorno: uma camada de outro bioma com ano
    mais novo empurraria o corte para frente e esconderia alerta recente ainda
    não confirmado. Errar para trás só repete informação verdadeira (a regra 4
    avisa o que o PRODES já marcou); errar para frente omite risco.
    Várias datas (Amazônia × Cerrado) → a mais antiga, como pede o relatório 03.
    """
    values = prodes_cutoff if isinstance(prodes_cutoff, (list, tuple, set)) else [prodes_cutoff]
    dates = [d for d in (_iso_date(v) for v in values) if d]
    if not dates:
        return PRODES_CUTOFF_FALLBACK, "padrao_medido_2026-09-13"
    return min(dates), ("informado" if len(set(dates)) == 1 else "informado_mais_antigo")


def _deter_status(deter: Any) -> str:
    if deter is None:
        return "not_queried"
    if not isinstance(deter, dict):
        return "pending"
    state = str(deter.get("state") or "").lower()
    if state == "not_covered":
        return "not_covered"
    if state in ("pending", "failed") or deter.get("ok") is False:
        return "pending"
    if (state in ("answered", "found", "not_found") or deter.get("ok") is True) and isinstance(deter.get("features"), list):
        return "answered"
    return "pending"


def combine_deforestation_alerts(
    car_geometry: Any,
    mapbiomas: dict[str, Any] | None = None,
    deter: dict[str, Any] | None = None,
    prodes: dict[str, Any] | None = None,
    prodes_cutoff: Any = None,
) -> dict[str, Any]:
    """Uma área, várias testemunhas (relatório 03, seção 5).

    ``mapbiomas``: resultado de ``query_mapbiomas_alerta`` (com campos internos).
    ``deter``: ``None`` quando o módulo DETER não rodou; senão
    ``{"state": "answered"|"pending"|"not_covered", "features": [GeoJSON],
    "min_area_ha": 3.0, "latest_image_date": "AAAA-MM-DD"}``. Cada feição usa
    ``properties.view_date`` (ou ``date``) e ``properties.gid`` (ou ``id``).
    Lista vazia só conta como resposta quando o estado diz ``answered``.
    ``prodes``: saída do ``prodes_fast_v24`` (``hits`` com feições e ``year``).
    ``prodes_cutoff``: fim do último ano PRODES publicado, se já conhecido.
    """
    car = _geometry(car_geometry)
    cutoff, cutoff_origin = _resolve_cutoff(prodes_cutoff)
    mb_state = (mapbiomas or {}).get("state")
    status = {
        "mapbiomas_alerta": "not_queried" if mapbiomas is None else ("answered" if mb_state in ("found", "not_found") else "pending"),
        "inpe_deter": _deter_status(deter),
    }
    base = {
        "cut_date": cutoff.isoformat(),
        "cut_date_origin": cutoff_origin,
        "recent_after": (cutoff + timedelta(days=1)).isoformat(),
        "sources": status,
    }
    if car is None:
        return {**base, "state": "pending", "pending_reason": "car_geometry_missing", "events": [],
                "event_count": None, "area_union_ha": None,
                "text": {"summary": "Alertas recentes de desmatamento: consulta pendente.", "items": []}}

    witnesses: list[dict[str, Any]] = []
    deter_malformed = 0
    if status["mapbiomas_alerta"] == "answered":
        for a in mapbiomas.get("alerts") or []:
            geom = _geometry(a.get("_geometry_wkt"))
            d = _iso_date(a.get("detected_at"))
            if geom is None or d is None:
                # Ex.: recebeu public_view (sem geometria). Alerta que existe e não
                # pôde ser medido nunca deixa a resposta virar "nenhum alerta".
                status["mapbiomas_alerta"] = "pending"
                continue
            inside = car.intersection(geom)
            if _area_ha(inside) < MIN_AREA_HA:
                continue
            witnesses.append({"source": SOURCE_ID, "id": str(a["alert_code"]), "date": d, "geom": inside,
                              "laudo_url": a.get("laudo_car_url") or laudo_car_url(a["alert_code"], mapbiomas["car_code"])})
    if status["inpe_deter"] == "answered":
        feats = deter.get("features") or []
        for f in feats:
            props = (f or {}).get("properties") or {}
            geom = _geometry((f or {}).get("geometry"))
            d = _iso_date(props.get("view_date") or props.get("date"))
            if geom is None or d is None:
                deter_malformed += 1
                continue
            inside = car.intersection(geom)
            if _area_ha(inside) < MIN_AREA_HA:
                continue
            witnesses.append({"source": "inpe_deter", "id": str(props.get("gid") or f.get("id") or ""), "date": d, "geom": inside})
        if feats and deter_malformed == len(feats):
            status["inpe_deter"] = "pending"
            witnesses = [w for w in witnesses if w["source"] != "inpe_deter"]

    prodes_geoms: list[tuple[Any, int | None]] = []
    prodes_checked = bool((prodes or {}).get("ok")) and not (prodes or {}).get("failed_layers")
    for hit in (prodes or {}).get("hits") or []:
        for f in hit.get("features") or []:
            geom = _geometry((f or {}).get("geometry"))
            if geom is None:
                continue
            inside = car.intersection(geom)
            if _area_ha(inside) >= MIN_AREA_HA:
                prodes_geoms.append((inside, _prodes_year((f or {}).get("properties") or {})))

    def prodes_overlap(geom: Any, only_years: set[int] | None = None) -> tuple[float, list[int]]:
        touched = [(g, y) for g, y in prodes_geoms
                   if (only_years is None or y in only_years) and g.intersects(geom)]
        if not touched:
            return 0.0, []
        area = _area_ha(unary_union([g for g, _ in touched]).intersection(geom))
        years = sorted({y for g, y in touched if y and _area_ha(g.intersection(geom)) >= MIN_AREA_HA})
        return (area, years) if area >= MIN_AREA_HA else (0.0, [])

    recent = [w for w in witnesses if w["date"] > cutoff]
    older = [w for w in witnesses if w["date"] <= cutoff]

    # Agrupa por lugar: testemunhas recentes que se tocam são o mesmo evento.
    parent = list(range(len(recent)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            if recent[i]["geom"].intersects(recent[j]["geom"]):
                parent[root(j)] = root(i)
    groups: dict[int, list[dict[str, Any]]] = {}
    for i, w in enumerate(recent):
        groups.setdefault(root(i), []).append(w)

    events = []
    for members in groups.values():
        union = unary_union([m["geom"] for m in members])
        in_prodes, years = prodes_overlap(union)
        srcs = sorted({m["source"] for m in members}, key=lambda s: (s != "inpe_deter", s))
        events.append({
            "first_seen": min(m["date"] for m in members).isoformat(),
            "last_seen": max(m["date"] for m in members).isoformat(),
            "area_in_car_ha": round(_area_ha(union), 4),
            "sources": [DETER_LABEL if s == "inpe_deter" else SOURCE_LABEL for s in srcs],
            "mapbiomas_alert_codes": sorted({int(m["id"]) for m in members if m["source"] == SOURCE_ID}),
            "laudo_urls": sorted({m["laudo_url"] for m in members if m.get("laudo_url")}),
            "area_already_in_prodes_ha": round(in_prodes, 4),
            "prodes_years": years,
            "witnesses": [{"source": m["source"], "id": m["id"], "date": m["date"].isoformat(),
                           "area_in_car_ha": round(_area_ha(m["geom"]), 4)} for m in members],
        })
    events.sort(key=lambda e: e["first_seen"], reverse=True)
    for n, e in enumerate(events, 1):
        e["event_id"] = f"evento-{n}"

    before = []
    for w in older:
        # Alerta antigo só "valida" a ocorrência PRODES do ano que contém a data
        # dele (ou do ano seguinte, quando o mapa anual pegou a abertura depois).
        n = _prodes_year_of(w["date"])
        area_p, years = prodes_overlap(w["geom"], {n, n + 1})
        row = {"source": w["source"], "id": w["id"], "date": w["date"].isoformat(),
               "area_in_car_ha": round(_area_ha(w["geom"]), 4),
               "prodes_overlap_ha": round(area_p, 4), "prodes_years": years}
        if w.get("laudo_url"):
            row["laudo_url"] = w["laudo_url"]
        before.append(row)

    all_recent = unary_union([w["geom"] for w in recent]) if recent else None
    answered = [k for k, v in status.items() if v == "answered"]
    pending = [k for k, v in status.items() if v == "pending"]
    if events:
        state = "found"
    elif answered and not pending:
        state = "not_found"
    elif answered:
        state = "partial"
    else:
        state = "pending"
    out = {
        **base,
        "state": state,
        "events": events,
        "event_count": len(events) if answered else None,
        "area_union_ha": round(_area_ha(all_recent), 4) if all_recent is not None else (0.0 if answered else None),
        "audit_sum_of_sources_ha": round(sum(_area_ha(w["geom"]) for w in recent), 4),
        "before_cutoff": before,
        "prodes_checked": prodes_checked,
        # Sinal de corte vencido (ex.: saiu PRODES novo e o chamador não informou a data).
        "prodes_newer_than_cut": any(y and date(y, 7, 31) > cutoff for _, y in prodes_geoms) or None,
        "credit_note": (
            "Há alerta recente ainda não confirmado pelo mapa anual PRODES; ele não entra na conta "
            "de supressão posterior a 31/07/2019." if events else None
        ),
    }
    out["text"] = _combined_text(out, mapbiomas, deter)
    return {k: v for k, v in out.items() if v is not None}


def _combined_text(res: dict[str, Any], mapbiomas: dict[str, Any] | None, deter: dict[str, Any] | None) -> dict[str, Any]:
    st = res["sources"]
    after = _br_date(res["recent_after"])
    items: list[dict[str, Any]] = []
    credits = []
    if st["inpe_deter"] == "answered":
        credits.append(DETER_CREDIT)
    if st["mapbiomas_alerta"] == "answered":
        credits.append(CREDIT)

    def checked_in() -> str:
        parts = []
        if st["inpe_deter"] == "answered":
            limits = []
            if (deter or {}).get("min_area_ha"):
                limits.append(f"alertas a partir de {_br_ha(deter['min_area_ha']).replace(',00', '')} ha")
            if (deter or {}).get("latest_image_date"):
                limits.append(f"imagens até {_br_date(deter['latest_image_date'])}")
            parts.append("INPE" + (f" ({', '.join(limits)})" if limits else ""))
        if st["mapbiomas_alerta"] == "answered":
            pub = (mapbiomas or {}).get("last_publication_date")
            parts.append("MapBiomas Alerta" + (f" (publicações até {_br_date(pub)})" if pub else ""))
        return " e ".join(parts)

    labels = {"inpe_deter": "INPE", "mapbiomas_alerta": "MapBiomas Alerta"}
    pending_line = " ".join(f"{labels[k]}: consulta pendente." for k, v in st.items() if v == "pending")
    state = res["state"]
    if state == "found":
        n = len(res["events"])
        summary = (
            f"{n} {_plural(n, 'alerta recente', 'alertas recentes')} de desmatamento sobre o imóvel, "
            f"com {_br_ha(res['area_union_ha'])} ha (a mesma abertura vista por mais de uma fonte conta uma vez)."
        )
        for e in res["events"]:
            line = f"{_br_ha(e['area_in_car_ha'])} ha, visto por satélite em {_br_date(e['first_seen'])}"
            if DETER_LABEL in e["sources"]:
                line += " (INPE)"
            if e["mapbiomas_alert_codes"]:
                codes = ", ".join(str(c) for c in e["mapbiomas_alert_codes"])
                line += f" e validado pelo MapBiomas Alerta (laudo nº {codes})"
            line += "."
            if e["area_already_in_prodes_ha"] > 0:
                yrs = ", ".join(str(y) for y in e["prodes_years"])
                line += f" {_br_ha(e['area_already_in_prodes_ha'])} ha já constavam no mapa anual PRODES" + (f" de {yrs}" if yrs else "") + "."
            items.append({"text": line, "laudo_urls": e["laudo_urls"]})
        if pending_line:
            summary += " " + pending_line
    elif state == "not_found":
        summary = f"Nenhum alerta recente de desmatamento sobre o imóvel desde {after}. Conferido em: {checked_in()}."
    elif state == "partial":
        summary = f"Nenhum alerta recente de desmatamento sobre o imóvel desde {after} em: {checked_in()}. {pending_line}"
    else:
        summary = "Alertas recentes de desmatamento: consulta pendente."
    for b in res.get("before_cutoff") or []:
        if b["source"] == SOURCE_ID and b["prodes_years"]:
            yrs = ", ".join(str(y) for y in b["prodes_years"])
            items.append({
                "text": f"Ocorrência PRODES de {yrs}: validada pelo MapBiomas Alerta (laudo nº {b['id']}).",
                "laudo_urls": [b["laudo_url"]] if b.get("laudo_url") else [],
                "section": "prodes",
            })
    out = {"summary": summary.strip(), "items": items, "credits": credits}
    events = res.get("events") or []
    notes = []
    if any(e["mapbiomas_alert_codes"] for e in events) or any(i.get("section") == "prodes" for i in items):
        notes.append(DISCLAIMER)
    if any(not e["mapbiomas_alert_codes"] for e in events):
        notes.append(DETER_DISCLAIMER)
    if notes:
        out["disclaimer"] = " ".join(notes)
    return out


print("RX_MAPBIOMAS_ALERTA_F2=public_car_query_pending_never_clear_union_not_sum", flush=True)

"""Alertas validados de desmatamento sobre um CAR.

Fonte interna: MapBiomas Alerta, só a consulta pública e sem token
``ruralProperty(propertyCode)``. Nunca conta, token ou contorno de limite.

Decisão do dono (13/09/2026, painel, etapa f2-verdade-e-lacunas):

* card e relatório dizem só "alerta de desmatamento validado": sem nome da
  fonte, sem link e sem número de laudo;
* o crédito da licença existe só em ``sources_page_credits``, lido pela página
  final "Fontes consultadas" do relatório; ``SOURCES_PAGE_CREDIT`` declara a
  adaptação dos dados, como pede o compartilhamento pela mesma licença;
* nome da fonte, código do alerta, laudo e geometria ficam em campos que
  começam com ``_``: ``public_view`` os remove e só o servidor os lê.

Estados da consulta:

* ``found``     a fonte conhece o CAR e há alerta validado ligado a ele;
* ``not_found`` a fonte conhece o CAR, a lista veio vazia e tudo foi lido;
* ``pending``   erro, prazo, limite, cancelamento, resposta estranha, alerta
  ilegível sem outro legível, ou CAR fora da base. Nunca vira "nenhum alerta".

``combine_deforestation_alerts`` junta alertas validados, DETER e PRODES pela
regra da seção 5 do relatório 03: corte na data do último ano PRODES publicado,
uma abertura vista por várias fontes é um evento só, e a área é a união das
geometrias dentro do CAR (nunca a soma entre fontes).
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

SOURCE_ID = "mapbiomas_alerta"  # só servidor: registro de fontes e log
_SOURCE_LABEL = "MapBiomas Alerta"  # só servidor
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/br/"
SOURCES_PAGE_CREDIT = (
    "MapBiomas Alerta — CC BY-SA 3.0 BR; dados recortados e combinados pelo Raio-X; "
    "material adaptado sob a mesma licença"
)
DETER_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
DETER_SOURCES_PAGE_CREDIT = (
    "INPE/TerraBrasilis (DETER) — CC BY-SA 4.0; dados recortados e combinados pelo Raio-X; "
    "material adaptado sob a mesma licença"
)
VALIDATED_LABEL = "Alerta validado"
DISCLAIMER = (
    "Alerta validado de desmatamento não é auto de infração; indica abertura "
    "detectada por satélite e conferida por analistas."
)
PENDING_TEXT = "Alertas validados: consulta pendente."
COMBINED_PENDING_TEXT = "Alertas recentes de desmatamento: consulta pendente."
DETER_LABEL = "INPE (DETER)"
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
TIMEOUT = httpx.Timeout(16.0, connect=6.0, read=16.0, write=10.0, pool=8.0)
# Prazo total da consulta, todas as tentativas somadas. Medido deste computador em
# 13/09/2026: 1,1 a 4,0 s, arranque frio incluído. Do Render ainda não foi medido;
# só mudar depois de medir de lá.
DEADLINE_SECONDS = 20.0
MIN_RETRY_SECONDS = 2.0  # com menos prazo que isso, não abre nova tentativa
MAX_ATTEMPTS = 2  # uma nova tentativa só para falha rápida (rede ou 5xx)
RETRY_PAUSE_SECONDS = 0.5
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
RATE_LIMIT_DEFAULT_SECONDS = 300.0  # sem Retry-After, 5 min sem perguntar de novo
RATE_LIMIT_MAX_SECONDS = 3600.0
CACHE_TTL_SECONDS = 3600
CACHE_MAX_ENTRIES = 512
# Fim do último ano PRODES publicado, medido em 13/09/2026 (DETER Cerrado
# `_hist` até 31/07/2025 e deter-amz:prodes_reference end_date). Só vale quando
# o chamador não informa o corte.
PRODES_CUTOFF_FALLBACK = date(2025, 7, 31)
MIN_AREA_HA = 0.0001  # 1 m²: abaixo disso é ruído numérico de borda
SIGNIFICANT_OVERLAP = 0.5  # nota PRODES só com metade do alerta ou do polígono em comum
_PRODES_ANNUAL_LAYER = "yearly_deforestation"

# "published" é o único estado visto nas respostas reais (13/09/2026). A plataforma
# avisa que alerta revisto depois de publicado fica CANCELADO e continua consultável
# pelo código (chave note_concern_rejected_alert). Qualquer outro valor é resposta
# que não sabemos ler: vira pendência, nunca "não validado".
_VALIDATED_STATUS = frozenset({"published"})
_KNOWN_NOT_VALIDATED_STATUS = frozenset({"rejected", "canceled", "cancelled"})

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

_UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)
_CAR_RE = re.compile(r"(?:" + "|".join(_UFS) + r")-[0-9]{7}-[0-9A-F]{32}", re.ASCII)
_SOURCE_WORDS = {
    "CERRADO": "Cerrado", "AMAZONIA": "Amazônia", "CAATINGA": "Caatinga",
    "PANTANAL": "Pantanal", "PAMPA": "Pampa", "MATA": "Mata", "ATLANTICA": "Atlântica",
}
_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_rate_limited_until = 0.0


# ---------------------------------------------------------------- utilidades
def normalize_car_code(value: Any) -> str | None:
    try:
        code = re.sub(r"[\s.]", "", str(value or "")).upper()
    except Exception:
        return None
    return code if _CAR_RE.fullmatch(code) else None


def laudo_url(alert_code: int) -> str:
    return f"{_LAUDO_BASE}/{int(alert_code)}"


def laudo_car_url(alert_code: int, car_code: str) -> str:
    return f"{_LAUDO_BASE}/{int(alert_code)}/car/{car_code}"


def _iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _br_date(value: Any) -> str:
    d = _iso_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _br_ha(value: float) -> str:
    return f"{float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _num(value: Any) -> float | None:
    """Número finito e não negativo; qualquer outra coisa é ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _str_list(value: Any) -> list[str] | None:
    """Lista de textos; ``None`` quando o campo mudou de tipo (resposta ilegível)."""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    out = []
    for v in value:
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            return None
        out.append(str(v))
    return out


def _source_label(code: str) -> str:
    parts = [p for p in code.strip().upper().split("-") if p]
    if not parts:
        return ""
    return " ".join([parts[0]] + [_SOURCE_WORDS.get(p, p.capitalize()) for p in parts[1:]])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_set(event: Any) -> bool:
    try:
        return bool(event is not None and event.is_set())
    except Exception:
        return False


def _credit(text: str, url: str) -> dict[str, str]:
    return {"text": text, "license_url": url}


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
        elif isinstance(value, str):
            geom = _wkt.loads(re.sub(r"^\s*SRID=\d+;", "", value))
        else:
            return None
        geom = _valid(geom)
        if geom is None or geom.is_empty or not _inside_brazil(geom):
            return None
    except Exception:
        return None
    return geom


def _areal(value: Any) -> Any:
    """Geometria com área; ponto, linha ou polígono degenerado → ``None``."""
    geom = _geometry(value)
    return geom if geom is not None and _area_ha(geom) >= MIN_AREA_HA else None


# ---------------------------------------------------------------- resposta
def _pending(code: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    out = {
        "_source_id": SOURCE_ID,
        "_source_label": _SOURCE_LABEL,
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


def _auth_error(errors: list[Any]) -> bool:
    return any("token" in str(e.get("message") if isinstance(e, dict) else e).lower() for e in errors)


def parse_response(
    body: Any,
    car_code: str,
    car_geometry: Any = None,
    car_updated_at: Any = None,
) -> dict[str, Any]:
    """Transforma a resposta GraphQL no resultado do Raio-X (sem rede). Nunca levanta exceção."""
    try:
        return _parse_response(body, car_code, car_geometry, car_updated_at)
    except Exception as exc:
        return _pending(normalize_car_code(car_code), "schema_unexpected", error_type=type(exc).__name__)


def _parse_response(body: Any, car_code: str, car_geometry: Any, car_updated_at: Any) -> dict[str, Any]:
    code = normalize_car_code(car_code)
    if not code:
        return _pending(None, "invalid_car_code")
    if not isinstance(body, dict):
        return _pending(code, "schema_unexpected")
    data = body.get("data")
    errors = body.get("errors")
    errors = errors if isinstance(errors, list) else ([errors] if errors else [])
    if not isinstance(data, dict) or "ruralProperty" not in data:
        return _pending(code, "auth_required" if _auth_error(errors) else ("graphql_error" if errors else "schema_unexpected"))
    prop = data.get("ruralProperty")
    if prop is None:
        if _auth_error(errors):
            return _pending(code, "auth_required")
        return _pending(code, "graphql_error" if errors else "car_not_in_source")
    if not isinstance(prop, dict):
        return _pending(code, "schema_unexpected")
    if normalize_car_code(prop.get("propertyCode")) != code:
        return _pending(code, "car_code_mismatch")
    raw_alerts = prop.get("alerts")
    if not isinstance(raw_alerts, list):
        return _pending(code, "schema_unexpected")

    pub = data.get("lastAlertPublication")
    rng = data.get("alertDateRange")
    last_pub = _iso_date(pub.get("publishedAt") if isinstance(pub, dict) else None)
    max_detected = _iso_date(rng.get("maxDetectedAt") if isinstance(rng, dict) else None)
    source_car_date = _iso_date(prop.get("carUpdatedAt"))
    ours = _iso_date(car_updated_at)
    version_older = bool(ours and source_car_date and ours > source_car_date)
    car = _geometry(car_geometry)
    if car_geometry is not None and car is None:
        return _pending(code, "car_geometry_invalid")

    alerts: list[dict[str, Any]] = []
    ignored = {"not_validated": 0, "malformed": 0}
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            ignored["malformed"] += 1
            continue
        status = raw.get("statusName")
        status = status.strip().lower() if isinstance(status, str) else None
        if status in _KNOWN_NOT_VALIDATED_STATUS:
            ignored["not_validated"] += 1
            continue
        if status not in _VALIDATED_STATUS:
            ignored["malformed"] += 1  # estado desconhecido não é "não validado"
            continue
        detected = _iso_date(raw.get("detectedAt"))
        published = _iso_date(raw.get("publishedAt"))
        geom = _areal(raw.get("geometryWkt"))
        alert_code = raw.get("alertCode")
        if isinstance(alert_code, str) and alert_code.isascii() and alert_code.isdigit():
            alert_code = int(alert_code)
        lists = {key: _str_list(raw.get(key)) for key in ("sources", "crossedBiomes", "crossedCities", "deforestationClasses")}
        rows = raw.get("crossedRuralProperties")
        rows = [] if rows is None else rows
        if (
            isinstance(alert_code, bool) or not isinstance(alert_code, int) or alert_code <= 0
            or detected is None or geom is None
            or any(v is None for v in lists.values()) or not isinstance(rows, list)
        ):
            ignored["malformed"] += 1
            continue
        crossing = None
        for row in rows:
            if isinstance(row, dict) and normalize_car_code(row.get("code")) == code:
                crossing = _num(row.get("alertAreaInCar"))
        outside = False
        if car is not None:
            area_in_car = _area_ha(car.intersection(geom))
            method = "intersecao_raio_x"
            if area_in_car < MIN_AREA_HA:
                # A fonte liga o alerta a este CAR, mas ele não cruza o desenho atual
                # (retificação ou outra versão): continua listado, com marca.
                outside = True
                area_in_car, method = None, None
        elif crossing is not None and crossing > 0:
            area_in_car, method = crossing, "cruzamento_da_fonte"
        else:
            area_in_car, method = None, None
        area_alert = _num(raw.get("areaHa"))
        item = {
            "detected_at": detected.isoformat(),
            "published_at": published.isoformat() if published else None,
            "area_alert_ha": round(area_alert, 4) if area_alert is not None else None,
            "area_in_car_ha": round(area_in_car, 4) if area_in_car is not None else None,
            "area_in_car_method": method,
            "area_in_car_source_ha": round(crossing, 4) if crossing is not None else None,
            "outside_current_geometry": True if outside else None,
            "on_previous_car_version": True if outside and version_older else None,
            "biomes": lists["crossedBiomes"],
            "municipalities": lists["crossedCities"],
            "pressure_vectors": lists["deforestationClasses"],
            "_alert_code": alert_code,
            "_alert_sources": lists["sources"],
            "_alert_sources_label": ", ".join(filter(None, (_source_label(s) for s in lists["sources"]))),
            "_laudo_url": laudo_url(alert_code),
            "_laudo_car_url": laudo_car_url(alert_code, code),
            "_geometry_wkt": geom.wkt,
        }
        alerts.append({k: v for k, v in item.items() if v not in (None, [], "")})

    incomplete = bool(ignored["malformed"])
    if not alerts and incomplete:
        # Havia alerta, mas não deu para ler: isso não é ausência.
        return _pending(code, "schema_unexpected", ignored=ignored)

    alerts.sort(key=lambda a: (a["detected_at"], a["_alert_code"]), reverse=True)
    state = "found" if alerts else "not_found"
    outside_count = sum(1 for a in alerts if a.get("outside_current_geometry"))
    out: dict[str, Any] = {
        "_source_id": SOURCE_ID,
        "_source_label": _SOURCE_LABEL,
        "state": state,
        "answered": True,
        "car_code": code,
        # Resposta com parte ilegível não tem contagem fechada: só o mínimo.
        "alert_count": None if incomplete else len(alerts),
        "alert_count_min": len(alerts) if incomplete else None,
        "alerts": alerts,
        "ignored": ignored,
        "incomplete": incomplete,
        "outside_current_geometry_count": outside_count or None,
        "last_publication_date": last_pub.isoformat() if last_pub else None,
        "max_detected_date": max_detected.isoformat() if max_detected else None,
        "source_car_version_date": source_car_date.isoformat() if source_car_date else None,
        "source_car_version_older": version_older,
        "sources_page_credits": [_credit(SOURCES_PAGE_CREDIT, LICENSE_URL)],
    }
    if state == "found":
        out["disclaimer"] = DISCLAIMER
    out["text"] = _source_text(out)
    return {k: v for k, v in out.items() if v is not None}


def _outside_phrase(res: dict[str, Any] | None) -> str:
    res = res or {}
    if res.get("source_car_version_older") and res.get("source_car_version_date"):
        return f"sobre versão anterior do CAR ({_br_date(res['source_car_version_date'])})"
    return "ligado a este CAR"


def _source_text(res: dict[str, Any]) -> dict[str, Any]:
    items = []
    if res["state"] == "found":
        lead = "Ao menos " if res.get("incomplete") else ""
        chunks = []
        n = sum(1 for a in res["alerts"] if not a.get("outside_current_geometry"))
        k = len(res["alerts"]) - n
        if n:
            chunks.append(f"{lead}{n} {_plural(n, 'alerta de desmatamento validado', 'alertas de desmatamento validados')} sobre o imóvel.")
            lead = ""
        if k:
            chunks.append(f"{lead}{k} {_plural(k, 'alerta de desmatamento validado', 'alertas de desmatamento validados')} "
                          f"{_outside_phrase(res)}, fora do desenho atual do imóvel.")
        summary = " ".join(chunks)
        for a in res["alerts"]:
            when = f"detectado em {_br_date(a['detected_at'])}"
            if a.get("published_at"):
                when += f" e publicado em {_br_date(a['published_at'])}"
            biomes = f" ({', '.join(a['biomes'])})" if a.get("biomes") else ""
            if a.get("outside_current_geometry"):
                line = f"Alerta de desmatamento validado {_outside_phrase(res)}, {when}{biomes}; não cruza o desenho atual do imóvel."
            else:
                parts = ["Alerta de desmatamento validado"]
                if a.get("area_in_car_ha") is not None:
                    parts.append(f"{_br_ha(a['area_in_car_ha'])} ha dentro do imóvel")
                parts.append(when)
                line = ", ".join(parts) + biomes + "."
            items.append({"text": line})
    else:
        summary = "Nenhum alerta de desmatamento validado sobre este CAR"
        if res.get("max_detected_date"):
            summary += f" (detecções até {_br_date(res['max_detected_date'])})"
        summary += "."
    if res.get("source_car_version_older") and res.get("source_car_version_date"):
        summary += f" Os alertas validados consideram a versão do CAR de {_br_date(res['source_car_version_date'])}."
    out: dict[str, Any] = {"summary": summary, "items": items}
    if res["state"] == "found":
        out["disclaimer"] = DISCLAIMER
    return out


def public_view(result: Any) -> Any:
    """Cópia para cliente/PDF: sem campos internos (nome da fonte, laudo, código do alerta, geometria)."""
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


def _retry_after_seconds(value: Any) -> float:
    text = str(value or "").strip()
    seconds: float | None = None
    if re.fullmatch(r"[0-9]{1,7}", text, re.ASCII):
        seconds = float(text)
    elif text:
        try:
            seconds = (parsedate_to_datetime(text) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, IndexError, OverflowError):
            seconds = None
    if seconds is None or seconds <= 0:
        return RATE_LIMIT_DEFAULT_SECONDS
    return min(seconds, RATE_LIMIT_MAX_SECONDS)


def _start_cooldown(retry_after: Any) -> None:
    global _rate_limited_until
    with _rate_lock:
        _rate_limited_until = max(_rate_limited_until, time.monotonic() + _retry_after_seconds(retry_after))


def cooldown_remaining() -> float:
    with _rate_lock:
        return max(0.0, _rate_limited_until - time.monotonic())


def reset_rate_limit() -> None:
    global _rate_limited_until
    with _rate_lock:
        _rate_limited_until = 0.0


def _pause(cancel_event: Any, seconds: float) -> None:
    if seconds <= 0:
        return
    if cancel_event is not None and hasattr(cancel_event, "wait"):
        cancel_event.wait(seconds)
    else:
        time.sleep(seconds)


def _attempt_timeout(remaining: float) -> httpx.Timeout:
    def cap(value: float | None) -> float:
        return remaining if value is None else min(float(value), remaining)

    return httpx.Timeout(connect=cap(TIMEOUT.connect), read=cap(TIMEOUT.read), write=cap(TIMEOUT.write), pool=cap(TIMEOUT.pool))


class _Abort(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _post(http: httpx.Client, code: str, remaining: float, deadline: float, cancel_event: Any) -> tuple[int, bytes, Any]:
    payload = {"query": QUERY, "variables": {"c": code}}
    with http.stream("POST", _ENDPOINT, json=payload, timeout=_attempt_timeout(remaining)) as resp:
        if resp.status_code != 200:
            return resp.status_code, b"", resp.headers.get("Retry-After")
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _Abort("response_too_large")
            chunks.append(chunk)
            # Resposta que chega aos pingos não segura a thread além do prazo total.
            if _is_set(cancel_event):
                raise _Abort("cancelled")
            if time.monotonic() > deadline:
                raise _Abort("deadline")
        return 200, b"".join(chunks), None


def _fetch(code: str, cancel_event: Any, client: httpx.Client | None, deadline: float) -> tuple[Any, int, str | None]:
    attempts = 0
    reason: str | None = None
    body = None
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=False)
    try:
        while attempts < MAX_ATTEMPTS:
            if _is_set(cancel_event):
                reason = "cancelled"
                break
            if attempts:
                if deadline - time.monotonic() < MIN_RETRY_SECONDS:
                    break  # sem prazo para outra tentativa: fica o motivo da falha anterior
                _pause(cancel_event, RETRY_PAUSE_SECONDS)
                if _is_set(cancel_event):
                    reason = "cancelled"
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = reason or "deadline"
                break
            attempts += 1
            try:
                status, raw, retry_after = _post(http, code, remaining, deadline, cancel_event)
            except _Abort as exc:
                reason = exc.reason
                break
            except httpx.TimeoutException:
                reason = "timeout"  # já gastou o prazo da tentativa: não repete
                break
            except httpx.TransportError:
                reason = "network_error"
                continue
            except httpx.DecodingError:
                reason = "decode_error"
                break
            except httpx.HTTPError:
                reason = "http_error"
                break
            if status == 429:
                _start_cooldown(retry_after)  # respeita o limite da fonte: não repete
                reason = "rate_limited"
                break
            if status >= 500:
                reason = "http_5xx"
                continue
            if status != 200:
                reason = "http_4xx" if 400 <= status < 500 else "http_unexpected_status"
                break
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError):
                reason = "invalid_json"
                break
            reason = None
            break
    finally:
        if own:
            http.close()
    return body, attempts, reason


def query_mapbiomas_alerta(
    car_code: Any,
    car_geometry: Any = None,
    *,
    car_updated_at: Any = None,
    cancel_event: Any = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Consulta pública por código do CAR. Nunca levanta exceção."""
    started = time.monotonic()
    try:
        return _query(car_code, car_geometry, car_updated_at, cancel_event, client, use_cache, deadline_s, started)
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        print(f"RX_MAPBIOMAS_ALERTA=pending:{elapsed}ms:reason=unexpected_error:{type(exc).__name__}", flush=True)
        return _pending(normalize_car_code(car_code), "unexpected_error", queried_at=_now_iso(), elapsed_ms=elapsed)


def _query(car_code: Any, car_geometry: Any, car_updated_at: Any, cancel_event: Any,
           client: httpx.Client | None, use_cache: bool, deadline_s: float | None, started: float) -> dict[str, Any]:
    code = normalize_car_code(car_code)
    if not code:
        return _pending(None, "invalid_car_code", queried_at=_now_iso(), elapsed_ms=0, attempts=0)
    budget = DEADLINE_SECONDS if deadline_s is None else max(0.0, float(deadline_s))

    body = _cache_get(code) if use_cache else None
    cached = body is not None
    attempts = 0
    reason = None
    if body is None:
        if cooldown_remaining() > 0:
            reason = "rate_limited"  # intervalo depois de 429: não pergunta de novo
        else:
            body, attempts, reason = _fetch(code, cancel_event, client, started + budget)

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
    """Versão para ``asyncio.gather``: cancelar a tarefa avisa a thread pelo ``cancel_event``."""
    import asyncio

    cancel = kwargs.get("cancel_event") or threading.Event()
    kwargs["cancel_event"] = cancel
    try:
        return await asyncio.to_thread(query_mapbiomas_alerta, car_code, car_geometry, **kwargs)
    except asyncio.CancelledError:
        cancel.set()
        raise


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


def _prodes_checked(prodes: Any) -> bool:
    """PRODES só conta como consultado com resposta completa e camadas identificadas."""
    if not isinstance(prodes, dict) or not prodes.get("ok") or prodes.get("failed_layers"):
        return False
    hits = prodes.get("hits")
    return isinstance(hits, list) and all(isinstance(h, dict) and isinstance(h.get("layer"), str) for h in hits)


def combine_deforestation_alerts(
    car_geometry: Any,
    mapbiomas: dict[str, Any] | None = None,
    deter: dict[str, Any] | None = None,
    prodes: dict[str, Any] | None = None,
    prodes_cutoff: Any = None,
) -> dict[str, Any]:
    """Uma área, várias testemunhas (relatório 03, seção 5). Nunca levanta exceção.

    ``mapbiomas``: resultado de ``query_mapbiomas_alerta`` (com campos internos).
    ``deter``: ``None`` quando o módulo DETER não rodou; senão
    ``{"state": "answered"|"pending"|"not_covered", "features": [GeoJSON],
    "min_area_ha": 3.0, "latest_image_date": "AAAA-MM-DD"}``. Cada feição usa
    ``properties.view_date`` (ou ``date``) e ``properties.gid`` (ou ``id``).
    Lista vazia só conta como resposta quando o estado diz ``answered``.
    ``prodes``: saída do ``prodes_fast_v24`` (``hits`` com ``layer`` e feições com ``year``).
    ``prodes_cutoff``: fim do último ano PRODES publicado, se já conhecido.
    """
    try:
        return _combine(car_geometry, mapbiomas, deter, prodes, prodes_cutoff)
    except Exception as exc:
        return {"state": "pending", "pending_reason": "combine_error", "error_type": type(exc).__name__,
                "events": [], "text": {"summary": COMBINED_PENDING_TEXT, "items": []}}


def _combine(car_geometry: Any, mapbiomas: Any, deter: Any, prodes: Any, prodes_cutoff: Any) -> dict[str, Any]:
    car = _geometry(car_geometry)
    cutoff, cutoff_origin = _resolve_cutoff(prodes_cutoff)
    mb = mapbiomas if isinstance(mapbiomas, dict) else None
    mb_readable = mb is not None and mb.get("state") in ("found", "not_found")
    status = {
        "validated_alerts": "not_queried" if mapbiomas is None else ("answered" if mb_readable else "pending"),
        "inpe_deter": _deter_status(deter),
    }
    if mb_readable:
        ignored = mb.get("ignored") if isinstance(mb.get("ignored"), dict) else {}
        if mb.get("incomplete") or _num(ignored.get("malformed")):
            # Parte da resposta ilegível: o que foi lido conta, mas "nenhum" não se afirma.
            status["validated_alerts"] = "pending"
    base = {
        "cut_date": cutoff.isoformat(),
        "cut_date_origin": cutoff_origin,
        "recent_after": (cutoff + timedelta(days=1)).isoformat(),
        "sources": status,
    }
    if car is None:
        return {**base, "state": "pending", "pending_reason": "car_geometry_missing", "events": [],
                "text": {"summary": COMBINED_PENDING_TEXT, "items": []}}

    witnesses: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    if mb_readable:
        for a in mb.get("alerts") or []:
            a = a if isinstance(a, dict) else {}
            geom = _areal(a.get("_geometry_wkt"))
            d = _iso_date(a.get("detected_at"))
            if geom is None or d is None:
                # Ex.: recebeu public_view (sem geometria). Alerta que existe e não
                # pôde ser medido nunca deixa a resposta virar "nenhum alerta".
                status["validated_alerts"] = "pending"
                continue
            inside = car.intersection(geom)
            ident = str(a.get("_alert_code") or "")
            if a.get("outside_current_geometry") or _area_ha(inside) < MIN_AREA_HA:
                outside.append({"source": "validated_alert", "id": ident, "date": d})
                continue
            witnesses.append({"source": "validated_alert", "id": ident, "date": d, "geom": inside})
    deter_used = False
    if status["inpe_deter"] == "answered":
        deter_used = True
        malformed = 0
        for f in deter.get("features") or []:
            f = f if isinstance(f, dict) else {}
            props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            geom = _areal(f.get("geometry"))
            d = _iso_date(props.get("view_date") or props.get("date"))
            if geom is None or d is None:
                malformed += 1
                continue
            inside = car.intersection(geom)
            if _area_ha(inside) < MIN_AREA_HA:
                continue
            witnesses.append({"source": "inpe_deter", "id": str(props.get("gid") or f.get("id") or ""), "date": d, "geom": inside})
        if malformed:
            status["inpe_deter"] = "pending"  # feição ilegível: "nenhum" não se afirma

    prodes_checked = _prodes_checked(prodes)
    prodes_geoms: list[tuple[Any, int | None]] = []
    if prodes_checked:
        for hit in prodes["hits"]:
            if _PRODES_ANNUAL_LAYER not in hit["layer"]:
                continue  # máscara acumulada não é mapa anual
            for f in hit.get("features") or []:
                f = f if isinstance(f, dict) else {}
                geom = _geometry(f.get("geometry"))
                if geom is None:
                    continue
                inside = car.intersection(geom)
                if _area_ha(inside) >= MIN_AREA_HA:
                    props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
                    prodes_geoms.append((inside, _prodes_year(props)))

    def prodes_overlap(geom: Any, only_years: set[int] | None = None) -> tuple[float, list[int], bool]:
        touched = []
        for g, y in prodes_geoms:
            if (only_years is None or y in only_years) and g.intersects(geom):
                common = _area_ha(g.intersection(geom))
                if common >= MIN_AREA_HA:
                    touched.append((g, y, common))
        if not touched:
            return 0.0, [], False
        area = _area_ha(unary_union([g for g, _, _ in touched]).intersection(geom))
        base_area = _area_ha(geom)
        of_alert = base_area > 0 and area / base_area >= SIGNIFICANT_OVERLAP
        of_polygon = [(g, y) for g, y, c in touched if _area_ha(g) > 0 and c / _area_ha(g) >= SIGNIFICANT_OVERLAP]
        years = sorted({y for _, y, _ in touched if y})
        return area, years, bool(of_alert or of_polygon)

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
        kinds = {m["source"] for m in members}
        event = {
            "first_seen": min(m["date"] for m in members).isoformat(),
            "last_seen": max(m["date"] for m in members).isoformat(),
            "area_in_car_ha": round(_area_ha(union), 4),
            "sources": ([DETER_LABEL] if "inpe_deter" in kinds else []) + ([VALIDATED_LABEL] if "validated_alert" in kinds else []),
            "validated": "validated_alert" in kinds,
            "area_already_in_prodes_ha": None,
            "_validated_alert_codes": sorted({m["id"] for m in members if m["source"] == "validated_alert"}),
            "_witnesses": [{"source": m["source"], "id": m["id"], "date": m["date"].isoformat(),
                            "area_in_car_ha": round(_area_ha(m["geom"]), 4)} for m in members],
        }
        if prodes_checked:
            in_prodes, years, _ = prodes_overlap(union)
            event["area_already_in_prodes_ha"] = round(in_prodes, 4)
            event["prodes_years"] = years
        events.append(event)
    for w in outside:
        if w["date"] <= cutoff:
            continue
        events.append({
            "first_seen": w["date"].isoformat(), "last_seen": w["date"].isoformat(),
            "area_in_car_ha": None, "sources": [VALIDATED_LABEL], "validated": True,
            "outside_current_geometry": True, "area_already_in_prodes_ha": None,
            "_validated_alert_codes": [w["id"]],
            "_witnesses": [{"source": w["source"], "id": w["id"], "date": w["date"].isoformat()}],
        })
    events.sort(key=lambda e: e["first_seen"], reverse=True)
    for n, e in enumerate(events, 1):
        e["event_id"] = f"evento-{n}"

    before = []
    for w in older:
        row = {"source": w["source"], "_id": w["id"], "date": w["date"].isoformat(),
               "area_in_car_ha": round(_area_ha(w["geom"]), 4)}
        if prodes_checked:
            # Alerta antigo só coincide com a ocorrência PRODES do ano que contém a
            # data dele (ou do ano seguinte, quando o mapa anual pegou a abertura depois).
            n = _prodes_year_of(w["date"])
            area_p, years, significant = prodes_overlap(w["geom"], {n, n + 1})
            row.update(prodes_overlap_ha=round(area_p, 4), prodes_years=years, coincides_with_prodes=significant)
        before.append(row)
    for w in outside:
        if w["date"] <= cutoff:
            before.append({"source": w["source"], "_id": w["id"], "date": w["date"].isoformat(),
                           "outside_current_geometry": True})

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
    if events:
        event_count = len(events)
        area_union = round(_area_ha(all_recent), 4) if all_recent is not None else None
    elif state == "not_found":
        event_count, area_union = 0, 0.0
    else:
        event_count, area_union = None, None  # parcial ou pendente: zero seria ausência inventada
    credits = []
    if deter_used:
        credits.append(_credit(DETER_SOURCES_PAGE_CREDIT, DETER_LICENSE_URL))
    if mb_readable:
        credits.append(_credit(SOURCES_PAGE_CREDIT, LICENSE_URL))
    if not events:
        credit_note = None
    elif prodes_checked:
        credit_note = ("Há alerta recente ainda não confirmado pelo mapa anual PRODES; ele não entra na conta "
                       "de supressão posterior a 31/07/2019.")
    else:
        credit_note = "Alerta recente não entra na conta de supressão posterior a 31/07/2019."
    out = {
        **base,
        "state": state,
        "events": events,
        "event_count": event_count,
        "event_count_is_minimum": True if events and pending else None,
        "area_union_ha": area_union,
        "_audit_sum_of_sources_ha": round(sum(_area_ha(w["geom"]) for w in recent), 4),
        "before_cutoff": before,
        "prodes_checked": prodes_checked,
        # Sinal de corte vencido (ex.: saiu PRODES novo e o chamador não informou a data).
        "prodes_newer_than_cut": (any(y and date(y, 7, 31) > cutoff for _, y in prodes_geoms) or None) if prodes_checked else None,
        "credit_note": credit_note,
        "sources_page_credits": credits or None,
    }
    out["text"] = _combined_text(out, mb, deter)
    return {k: v for k, v in out.items() if v is not None}


def _combined_text(res: dict[str, Any], mb: dict[str, Any] | None, deter: Any) -> dict[str, Any]:
    st = res["sources"]
    after = _br_date(res["recent_after"])
    items: list[dict[str, Any]] = []

    def checked_in() -> str:
        parts = []
        if st["inpe_deter"] == "answered":
            limits = []
            if _num(deter.get("min_area_ha")):
                limits.append(f"alertas a partir de {_br_ha(deter['min_area_ha']).replace(',00', '')} ha")
            if _iso_date(deter.get("latest_image_date")):
                limits.append(f"imagens até {_br_date(deter['latest_image_date'])}")
            parts.append("INPE" + (f" ({', '.join(limits)})" if limits else ""))
        if st["validated_alerts"] == "answered":
            max_detected = (mb or {}).get("max_detected_date")
            parts.append("alertas validados" + (f" (detecções até {_br_date(max_detected)})" if _iso_date(max_detected) else ""))
        return " e ".join(parts)

    labels = {"inpe_deter": "INPE", "validated_alerts": "Alertas validados"}
    pending_line = " ".join(f"{labels[k]}: consulta pendente." for k, v in st.items() if v == "pending")
    state = res["state"]
    if state == "found":
        events = res["events"]
        inside = [e for e in events if not e.get("outside_current_geometry")]
        outside = [e for e in events if e.get("outside_current_geometry")]
        lead = "Ao menos " if res.get("event_count_is_minimum") else ""
        chunks = []
        if inside:
            n = len(inside)
            chunks.append(
                f"{lead}{n} {_plural(n, 'alerta recente', 'alertas recentes')} de desmatamento sobre o imóvel, "
                f"com {_br_ha(res['area_union_ha'])} ha (a mesma abertura vista por mais de uma fonte conta uma vez)."
            )
            lead = ""
        if outside:
            k = len(outside)
            chunks.append(
                f"{lead}{k} {_plural(k, 'alerta recente de desmatamento validado', 'alertas recentes de desmatamento validados')} "
                f"{_outside_phrase(mb)}, fora do desenho atual do imóvel."
            )
        summary = " ".join(chunks)
        for e in events:
            if e.get("outside_current_geometry"):
                line = (f"Alerta de desmatamento validado {_outside_phrase(mb)}, visto por satélite em "
                        f"{_br_date(e['first_seen'])}; não cruza o desenho atual do imóvel.")
            else:
                line = f"{_br_ha(e['area_in_car_ha'])} ha, visto por satélite em {_br_date(e['first_seen'])}"
                if DETER_LABEL in e["sources"]:
                    line += " (INPE)"
                if e.get("validated"):
                    line += "; alerta de desmatamento validado"
                line += "."
                in_prodes = e.get("area_already_in_prodes_ha")
                if in_prodes is not None and round(in_prodes, 2) > 0:
                    yrs = ", ".join(str(y) for y in e.get("prodes_years") or [])
                    line += f" {_br_ha(in_prodes)} ha já constavam no mapa anual PRODES" + (f" de {yrs}" if yrs else "") + "."
            items.append({"text": line})
        if pending_line:
            summary += " " + pending_line
    elif state == "not_found":
        summary = f"Nenhum alerta recente de desmatamento sobre o imóvel desde {after}. Conferido em: {checked_in()}."
    elif state == "partial":
        summary = f"Nenhum alerta recente de desmatamento sobre o imóvel desde {after} em: {checked_in()}. {pending_line}"
    else:
        summary = COMBINED_PENDING_TEXT
    for b in res.get("before_cutoff") or []:
        if b["source"] == "validated_alert" and b.get("coincides_with_prodes") and b.get("prodes_years"):
            yrs = ", ".join(str(y) for y in b["prodes_years"])
            items.append({
                "text": f"Ocorrência PRODES de {yrs}: coincide com alerta de desmatamento validado (detectado em {_br_date(b['date'])}).",
                "section": "prodes",
            })
    out: dict[str, Any] = {"summary": summary.strip(), "items": items}
    events = res.get("events") or []
    notes = []
    if any(e.get("validated") for e in events) or any(i.get("section") == "prodes" for i in items):
        notes.append(DISCLAIMER)
    if any(not e.get("validated") for e in events):
        notes.append(DETER_DISCLAIMER)
    if notes:
        out["disclaimer"] = " ".join(notes)
    return out


print("RX_MAPBIOMAS_ALERTA_F2=public_car_query_pending_never_clear_union_not_sum_neutral_text", flush=True)

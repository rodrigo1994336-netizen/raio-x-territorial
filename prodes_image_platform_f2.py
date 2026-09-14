"""F2 · Data e satélite da imagem do PRODES sem copiar atributo errado.

Por que existe: o WFS do PRODES Cerrado (TerraBrasilis) devolve
``satellite: "Landsat8"`` e ``sensor: "OLI"`` até em feições de 2000 a 2010
(o Landsat 8 é de 2013). Na camada inteira os poucos valores diferentes são
incoerentes (``landsat/MSI`` em 2006, ``sentinel/OLI`` em 2021). A data da
imagem (``image_date``) confere com o catálogo de cenas; o satélite, não.

Regra:
- o atributo ``satellite``/``sensor`` do WFS nunca é exibido;
- a data da imagem é exibida sempre que for uma data válida;
- o satélite só aparece quando o catálogo de cenas Landsat Collection 2
  (Microsoft Planetary Computer, espelho do USGS) tem cena naquela data e
  naquela órbita/ponto (ou sobre o centróide) e todas as cenas são do mesmo
  satélite. Nenhuma cena ou mais de um satélite -> ``not_found`` (omite).
  Catálogo que não respondeu -> ``pending`` (omite, sem aviso técnico).
- não se deduz satélite pela data (2002 foi Landsat 7, não Landsat 5).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import re
import subprocess
from typing import Any, Callable, Iterable

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
COLLECTIONS = ("landsat-c2-l2", "landsat-c2-l1")
# Measured 13/09/2026 from Brazil: 233-1170 ms per search (7 calls, fields-filtered, ~500 B).
STAC_MAX_TIME_S = 8
STAC_CONNECT_TIMEOUT_S = 4
FIELDS = {
    "include": ["id", "collection", "properties.platform", "properties.instruments", "properties.datetime",
                "properties.landsat:wrs_path", "properties.landsat:wrs_row"],
    "exclude": ["assets", "links", "geometry", "bbox", "stac_extensions"],
}
CATALOG_NAME = "catálogo de cenas Landsat (USGS, via Planetary Computer)"

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_PATH_ROW = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")
_INSTRUMENT_LABEL = {"tm": "TM", "etm+": "ETM+", "oli": "OLI", "mss": "MSS"}


def image_day(value: Any) -> date | None:
    m = _ISO_DAY.match(str(value or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def wrs_path_row(value: Any) -> tuple[str, str] | None:
    m = _PATH_ROW.match(str(value or ""))
    if not m:
        return None
    return m.group(1).zfill(3), m.group(2).zfill(3)


def search_body(day: date, path_row: tuple[str, str] | None = None, lonlat: tuple[float, float] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "collections": list(COLLECTIONS),
        "datetime": f"{day.isoformat()}T00:00:00Z/{day.isoformat()}T23:59:59Z",
        "limit": 20,
        "fields": FIELDS,
    }
    if path_row:
        body["query"] = {"landsat:wrs_path": {"eq": path_row[0]}, "landsat:wrs_row": {"eq": path_row[1]}}
    elif lonlat:
        body["intersects"] = {"type": "Point", "coordinates": [float(lonlat[0]), float(lonlat[1])]}
    return body


def platform_from_stac(response: dict[str, Any] | None, day: date) -> dict[str, Any]:
    """Pure: decide the satellite from a STAC search response for one image date."""
    if not isinstance(response, dict) or not isinstance(response.get("features"), list):
        return {"status": "pending", "reason": "catalogo_resposta_invalida", "image_date": day.isoformat()}
    platforms: dict[str, set[str]] = {}
    scene_ids = []
    for feature in response["features"]:
        props = feature.get("properties") or {}
        when = image_day(props.get("datetime"))
        platform = str(props.get("platform") or "").strip().lower()
        if when != day or not re.fullmatch(r"landsat-\d", platform):
            continue
        instruments = {str(x).strip().lower() for x in (props.get("instruments") or [])}
        platforms.setdefault(platform, set()).update(instruments)
        scene_ids.append(str(feature.get("id") or ""))
    if not platforms:
        return {"status": "not_found", "reason": "nenhuma_cena_landsat_na_data", "image_date": day.isoformat()}
    if len(platforms) > 1:
        return {"status": "not_found", "reason": "mais_de_um_satelite_na_data", "image_date": day.isoformat(),
                "platforms": sorted(platforms)}
    platform, instruments = next(iter(platforms.items()))
    optical = [label for key, label in _INSTRUMENT_LABEL.items() if key in instruments]
    return {
        "status": "found",
        "image_date": day.isoformat(),
        "platform": f"Landsat {platform.split('-')[1]}",
        "instrument": "/".join(optical) or None,
        "scene_ids": sorted(set(x for x in scene_ids if x)),
        "source": CATALOG_NAME,
    }


def _curl_post_json(url: str, body: dict[str, Any]) -> dict[str, Any] | None:
    proc = subprocess.run(
        ["curl", "-sS", "--connect-timeout", str(STAC_CONNECT_TIMEOUT_S), "--max-time", str(STAC_MAX_TIME_S),
         "-A", "Raio-X-Territorial/f2-landsat-platform", "-H", "Content-Type: application/json",
         "-X", "POST", "--data-binary", "@-", url],
        input=json.dumps(body).encode("utf-8"), capture_output=True, timeout=STAC_MAX_TIME_S + 5,
    )
    if proc.returncode:
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except Exception:
        return None


def query_landsat_platform(image_date: Any, path_row: Any = None, lonlat: tuple[float, float] | None = None,
                           post: Callable[[str, dict[str, Any]], dict[str, Any] | None] = _curl_post_json) -> dict[str, Any]:
    """One catalog search for one PRODES image date. ``post`` is injectable for offline tests."""
    day = image_day(image_date)
    if day is None:
        return {"status": "not_found", "reason": "data_da_imagem_invalida", "image_date": None}
    pr = wrs_path_row(path_row)
    if not pr and not lonlat:
        return {"status": "not_found", "reason": "sem_orbita_ponto_nem_centroide", "image_date": day.isoformat()}
    try:
        response = post(STAC_SEARCH, search_body(day, pr, lonlat))
    except Exception as exc:
        return {"status": "pending", "reason": f"catalogo:{type(exc).__name__}", "image_date": day.isoformat()}
    if response is None:
        return {"status": "pending", "reason": "catalogo_nao_respondeu", "image_date": day.isoformat()}
    return platform_from_stac(response, day)


def _src(occurrence: dict[str, Any]) -> dict[str, Any]:
    props = occurrence.get("properties")
    return props if isinstance(props, dict) else occurrence


def lookup_key(occurrence: dict[str, Any]) -> str | None:
    """JSON-safe key 'YYYY-MM-DD|PPP/RRR' (or 'YYYY-MM-DD|' without orbit).

    Accepts an extracted occurrence row or a raw WFS feature (with ``properties``).
    """
    src = _src(occurrence)
    day = image_day(src.get("image_date"))
    if day is None:
        return None
    pr = wrs_path_row(src.get("path_row"))
    return f"{day.isoformat()}|{'/'.join(pr) if pr else ''}"


def query_platforms_for_occurrences(occurrences: Iterable[dict[str, Any]], lonlat: tuple[float, float] | None = None,
                                    post: Callable[[str, dict[str, Any]], dict[str, Any] | None] = _curl_post_json,
                                    max_workers: int = 4) -> dict[str, dict[str, Any]]:
    """Unique (date, path/row) searches in parallel; returns lookups for ``prodes_image_payload``."""
    keys = sorted({k for k in (lookup_key(o) for o in occurrences) if k})
    if not keys:
        return {}

    def one(key: str):
        day, path_row = key.split("|", 1)
        return key, query_landsat_platform(day, path_row or None, None if path_row else lonlat, post)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(keys)))) as pool:
        return dict(pool.map(one, keys))


def image_date_ptbr(value: Any) -> str:
    day = image_day(value)
    return day.strftime("%d/%m/%Y") if day else ""


def prodes_image_text(occurrence: dict[str, Any], lookup: dict[str, Any] | None = None) -> str:
    """'31/07/2004' or '31/07/2004 · Landsat 5 (TM)'. Never the WFS satellite attribute."""
    raw = _src(occurrence).get("image_date")
    when = image_date_ptbr(raw)
    if not when:
        return ""
    if lookup and lookup.get("status") == "found" and lookup.get("image_date") == image_day(raw).isoformat():
        sat = lookup.get("platform")
        inst = lookup.get("instrument")
        return f"{when} · {sat}" + (f" ({inst})" if inst else "")
    return when


def prodes_image_payload(occurrences: Iterable[dict[str, Any]], lookups: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Per occurrence: date (always when valid) and satellite with explicit status."""
    lookups = lookups or {}
    out = []
    for occ in occurrences:
        key = lookup_key(occ)
        lookup = lookups.get(key) if key else None
        status = (lookup or {}).get("status") or ("pending" if key else "not_found")
        item = {
            "year": _src(occ).get("year"),
            "image_date": key.split("|", 1)[0] if key else None,
            "image_date_ptbr": image_date_ptbr(_src(occ).get("image_date")) or None,
            "platform_status": status,
            "platform": lookup.get("platform") if status == "found" else None,
            "instrument": lookup.get("instrument") if status == "found" else None,
            "platform_source": lookup.get("source") if status == "found" else None,
            "text": prodes_image_text(occ, lookup),
        }
        out.append(item)
    return out


def image_row(occurrence: dict[str, Any], lookup: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """PDF key/value row for one occurrence, or None when there is no valid date (campo vazio não aparece)."""
    text = prodes_image_text(occurrence, lookup)
    if not text:
        return None
    return ("Imagem (data e satélite)" if " · " in text else "Data da imagem", text)

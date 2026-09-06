from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pyproj import Geod
from shapely.geometry import shape
from shapely.strtree import STRtree

WFS = "https://geoserver.car.gov.br/geoserver/sicar/ows"
TYPENAME = "sicar:sicar_imoveis_mg"
SIGEF = (
    ("PUBLICO", "https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10/query"),
    ("PRIVADO", "https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases/lim_imovel_sigef_privado_a/FeatureServer/9/query"),
)
DAV = "https://arquivos.receitafederal.gov.br/public.php/dav/files/RRmcpB2tf5cXskz"
SNAPSHOT = "D60901"
CAFIR_PARTS = ("MG01", "MG02", "MG03")
INCLUDED = {"AT", "PE", "SU"}
CODE_RE = re.compile(r"^MG-\d{7}-[A-F0-9]{32}$", re.I)
REF = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
GEOD = Geod(ellps="GRS80")
OVERLAP_THRESHOLD = 0.98
TILE = 1.0
# Covers MG with a small safety margin; assignment is by representative point.
WEST, SOUTH, EAST, NORTH = -51.2, -23.2, -39.7, -13.9


def norm(s: object) -> str:
    t = "".join(c for c in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(c))
    return " ".join(t.upper().split())


def get(url: str, timeout: int = 120, retries: int = 7) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Raio-X-Territorial/Stage2-HighConfidence-V1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            last = exc
            time.sleep(min(15, 1.7 * (i + 1)))
    raise last  # type: ignore[misc]


def jget(base: str, params: dict, timeout: int = 120) -> dict:
    url = base + "?" + urllib.parse.urlencode(params)
    raw = get(url, timeout=timeout)
    data = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(data, dict) and (data.get("error") or data.get("exceptions") or data.get("ExceptionReport")):
        raise RuntimeError(f"source error: {str(data)[:800]}")
    return data


def geod_area(g) -> float:
    if g.is_empty:
        return 0.0
    try:
        area, _ = GEOD.geometry_area_perimeter(g)
        return abs(float(area))
    except Exception:
        if g.geom_type.startswith("Multi") or g.geom_type == "GeometryCollection":
            return sum(geod_area(x) for x in g.geoms)
        return 0.0


def decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin1"):
        try:
            return b.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return b.decode("latin1", "replace").strip()


def build_cafir_maps() -> tuple[dict[str, str], dict[str, list[tuple[float, str | None]]], dict]:
    by_incra_records: dict[str, list[str | None]] = defaultdict(list)
    by_mun: dict[str, list[tuple[float, str | None]]] = defaultdict(list)
    stats = Counter()
    file_stats = []
    for part in CAFIR_PARTS:
        url = f"{DAV}/K34313UF.{SNAPSHOT}.{part}.csv"
        raw = get(url, timeout=240, retries=6)
        total = active = bad = 0
        for line in raw.splitlines():
            total += 1
            r = line.rstrip(b"\r\n")
            if len(r) != 245:
                bad += 1
                continue
            if r[85:87] != b"02":
                continue
            active += 1
            area_raw = r[8:17].decode("ascii", "ignore")
            incra = r[17:30].decode("ascii", "ignore").strip()
            name = decode_bytes(r[30:85]) or None
            mun = norm(decode_bytes(r[185:225]))
            if incra:
                by_incra_records[incra].append(name)
            if area_raw.isdigit() and mun:
                by_mun[mun].append((int(area_raw) / 10.0, name))
        file_stats.append({"part": part, "total": total, "active": active, "bad": bad})
        stats["total"] += total
        stats["active"] += active
        stats["bad"] += bad
        print(f"CAFIR {part}: total={total} active={active} bad={bad}", flush=True)
    exact: dict[str, str] = {}
    duplicate_incra = conflicting_incra = blank_single = 0
    for incra, records in by_incra_records.items():
        if len(records) == 1 and records[0]:
            exact[incra] = records[0]
        elif len(records) == 1:
            blank_single += 1
        else:
            duplicate_incra += 1
            if len({norm(x) for x in records if x}) > 1:
                conflicting_incra += 1
    for mun in by_mun:
        by_mun[mun].sort(key=lambda x: x[0])
    meta = {
        "snapshot": SNAPSHOT,
        "files": file_stats,
        "active_records": stats["active"],
        "unique_incra_codes": len(by_incra_records),
        "exact_single_active_named_incra_codes": len(exact),
        "duplicate_active_incra_codes": duplicate_incra,
        "conflicting_active_incra_codes": conflicting_incra,
        "single_active_blank_name_incra_codes": blank_single,
    }
    return exact, by_mun, meta


def fallback_classify(by_mun: dict[str, list[tuple[float, str | None]]], mun: str, area: float) -> str:
    tol = max(area * 0.005, 0.01)
    candidates = [(a, n) for a, n in by_mun.get(mun, ()) if area - tol - 1e-9 <= a <= area + tol + 1e-9]
    if not candidates:
        return "SEM_NOME_AUSENCIA_CAFIR"
    if len(candidates) == 1 and candidates[0][1]:
        return "NOME_MUNICIPIO_AREA"
    if len(candidates) == 1 and not candidates[0][1]:
        return "SEM_NOME_AUSENCIA_CAFIR"
    return "SEM_NOME_AMBIGUIDADE"


def wfs_page(bbox: tuple[float, float, float, float], start: int, count: int = 5000) -> list[dict]:
    west, south, east, north = bbox
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": TYPENAME,
        "outputFormat": "application/json",
        "srsName": "EPSG:4674",
        "bbox": f"{west},{south},{east},{north},EPSG:4674",
        "CQL_FILTER": "status_imovel IN ('AT','PE','SU')",
        "count": str(count),
        "startIndex": str(start),
    }
    data = jget(WFS, params, timeout=180)
    return data.get("features") or []


def fetch_cars(bbox: tuple[float, float, float, float]) -> list[dict]:
    out = []
    start = 0
    while True:
        rows = wfs_page(bbox, start)
        out.extend(rows)
        if len(rows) < 5000:
            break
        start += len(rows)
    return out


def fetch_sigef_source(source: str, base: str, bbox: tuple[float, float, float, float]) -> list[dict]:
    west, south, east, north = bbox
    env = f"{west},{south},{east},{north}"
    out = []
    offset = 0
    while True:
        params = {
            "f": "geojson",
            "where": "uf_id=31 AND codigo_imo IS NOT NULL",
            "geometry": env,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4674",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "objectid,parcela_co,codigo_imo,nome_area,municipio_,uf_id,status,situacao_i",
            "returnGeometry": "true",
            "outSR": "4674",
            "resultOffset": str(offset),
            "resultRecordCount": "2000",
        }
        data = jget(base, params, timeout=180)
        rows = data.get("features") or []
        for f in rows:
            f.setdefault("properties", {})["_rx_source"] = source
        out.extend(rows)
        if len(rows) < 2000:
            break
        offset += len(rows)
    return out


def fetch_sigef(bbox: tuple[float, float, float, float]) -> list[dict]:
    rows = []
    errors = []
    for source, base in SIGEF:
        try:
            part = fetch_sigef_source(source, base, bbox)
            rows.extend(part)
        except Exception as exc:
            errors.append(f"{source}:{type(exc).__name__}:{exc}")
    if errors:
        # Both public and private are required by the frozen statewide route; a partial
        # layer cannot be silently interpreted as a measured zero for the missing side.
        raise RuntimeError("SIGEF_SOURCE_UNAVAILABLE | " + " | ".join(errors))
    return rows


def core_tiles():
    y = SOUTH
    while y < NORTH - 1e-9:
        x = WEST
        y2 = min(NORTH, y + TILE)
        while x < EAST - 1e-9:
            x2 = min(EAST, x + TILE)
            yield (x, y, x2, y2)
            x = x2
        y = y2


def assigned_to_core(g, core: tuple[float, float, float, float]) -> bool:
    p = g.representative_point()
    west, south, east, north = core
    # Half-open grid, except global east/north edge.
    east_ok = p.x < east or math.isclose(east, EAST)
    north_ok = p.y < north or math.isclose(north, NORTH)
    return p.x >= west and p.y >= south and east_ok and p.y >= south and north_ok


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "benchmark/cafir_mg_real_coverage_2026-09-01.json").read_text(encoding="utf-8"))
    protocol = (root / "benchmark/CAR_NAME_HIGH_CONFIDENCE_PROTOCOL_V1.md").read_text(encoding="utf-8")
    if "98.0%" not in protocol or ">= 25.00000%" not in protocol:
        raise RuntimeError("frozen high-confidence protocol missing")
    exact_cafir, by_mun, cafir_meta = build_cafir_maps()

    seen: set[str] = set()
    high: dict[str, dict] = {}
    promoted_from = Counter()
    sigef_seen = set()
    car_rows = 0
    tiles_done = 0
    source_counts = Counter()
    ref = {"car": REF, "high_confidence": False, "method": None, "incra_code": None, "name": None}

    for core in core_tiles():
        cars_raw = fetch_cars(core)
        cars = []
        bounds = []
        for f in cars_raw:
            props = f.get("properties") or {}
            code = str(props.get("cod_imovel") or "").strip().upper()
            if not CODE_RE.match(code) or code in seen:
                continue
            try:
                g = shape(f.get("geometry"))
                if g.is_empty or not g.is_valid:
                    g = g.buffer(0)
                if g.is_empty or not assigned_to_core(g, core):
                    continue
            except Exception:
                continue
            seen.add(code)
            car_rows += 1
            cars.append((code, props, g, geod_area(g)))
            bounds.append(g.bounds)
        if not cars:
            tiles_done += 1
            continue
        minx = min(b[0] for b in bounds) - 0.0001
        miny = min(b[1] for b in bounds) - 0.0001
        maxx = max(b[2] for b in bounds) + 0.0001
        maxy = max(b[3] for b in bounds) + 0.0001
        sig_raw = fetch_sigef((minx, miny, maxx, maxy))
        s_geoms = []
        s_meta = []
        for f in sig_raw:
            props = f.get("properties") or {}
            incra = str(props.get("codigo_imo") or "").strip()
            if not incra:
                continue
            try:
                g = shape(f.get("geometry"))
                if g.is_empty or not g.is_valid:
                    g = g.buffer(0)
                if g.is_empty:
                    continue
            except Exception:
                continue
            key = (props.get("_rx_source"), props.get("objectid"), incra)
            sigef_seen.add(key)
            s_geoms.append(g)
            s_meta.append((incra, props, geod_area(g)))
            source_counts[str(props.get("_rx_source") or "UNKNOWN")] += 1
        tree = STRtree(s_geoms) if s_geoms else None
        for code, props, car_g, car_area_m2 in cars:
            if not tree or car_area_m2 <= 0:
                continue
            qualified = []
            try:
                idxs = tree.query(car_g, predicate="intersects")
            except TypeError:
                idxs = tree.query(car_g)
            for idx in idxs:
                i = int(idx)
                sg = s_geoms[i]
                incra, sp, sig_area_m2 = s_meta[i]
                if sig_area_m2 <= 0:
                    continue
                try:
                    inter = car_g.intersection(sg)
                    ia = geod_area(inter)
                except Exception:
                    continue
                car_share = ia / car_area_m2
                sig_share = ia / sig_area_m2
                if car_share + 1e-12 >= OVERLAP_THRESHOLD and sig_share + 1e-12 >= OVERLAP_THRESHOLD:
                    qualified.append((incra, car_share, sig_share, sp))
            distinct_codes = sorted({q[0] for q in qualified})
            if len(distinct_codes) != 1:
                continue
            incra = distinct_codes[0]
            name = exact_cafir.get(incra)
            if not name:
                continue
            best = max((q for q in qualified if q[0] == incra), key=lambda q: min(q[1], q[2]))
            try:
                car_area_ha = float(str(props.get("area") or "").replace(",", "."))
            except Exception:
                car_area_ha = None
            mun = norm(props.get("municipio"))
            fallback = fallback_classify(by_mun, mun, car_area_ha) if car_area_ha and mun else None
            if fallback:
                promoted_from[fallback] += 1
            high[code] = {
                "name": name,
                "incra_code": incra,
                "method": "INCRA_CODE_EXACT_GEOMETRY_98",
                "confidence": "MUITO_ALTA",
                "car_overlap_ratio": round(best[1], 8),
                "sigef_overlap_ratio": round(best[2], 8),
                "sigef_source": best[3].get("_rx_source"),
                "sigef_parcel_code": best[3].get("parcela_co"),
                "fallback_previous_class": fallback,
            }
            if code == REF:
                ref.update({"high_confidence": True, "method": "INCRA_CODE_EXACT_GEOMETRY_98", "incra_code": incra, "name": name, "car_overlap_ratio": round(best[1], 8), "sigef_overlap_ratio": round(best[2], 8)})
        tiles_done += 1
        print(f"tile {tiles_done}: core={core} cars={len(cars)} sigef={len(s_geoms)} high_total={len(high)} seen_cars={len(seen)}", flush=True)

    frozen_den = int(baseline["eligible_denominator"])
    baseline_counts = Counter({k: int(v) for k, v in baseline["counts"].items()})
    final_counts = Counter(baseline_counts)
    for bucket, n in promoted_from.items():
        final_counts[bucket] -= n
    final_counts["NOME_GRATUITO_ALTA"] = len(high)
    # A high-confidence success that could not be assigned a fallback class (drift/bad
    # attributes) must not corrupt frozen accounting; report it outside final cascade.
    unaccounted_high = len(high) - sum(promoted_from.values())
    accounted_high = len(high) - unaccounted_high
    total_automatic = final_counts["NOME_GRATUITO_ALTA"] + final_counts["NOME_MUNICIPIO_AREA"]
    total_pct = total_automatic * 100.0 / frozen_den
    high_pct = len(high) * 100.0 / frozen_den
    stop = "IMPLEMENT_AND_CLOSE_STAGE_2" if total_pct >= 25.0 else "ACCEPT_RESULT_CLOSE_STAGE_2_NO_OPTIMIZATION"

    result = {
        "protocol": "CAR_NAME_HIGH_CONFIDENCE_PROTOCOL_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Minas Gerais",
        "frozen_stop_threshold_total_coverage_pct": 25.0,
        "method": {
            "id": "INCRA_CODE_EXACT_GEOMETRY_98",
            "confidence": "MUITO_ALTA",
            "car_overlap_min": OVERLAP_THRESHOLD,
            "sigef_overlap_min": OVERLAP_THRESHOLD,
            "distinct_incra_codes_required": 1,
            "active_cafir_records_for_incra_required": 1,
            "personal_data_used": False,
        },
        "sources": {
            "sicar": WFS,
            "sigef": [x[1] for x in SIGEF],
            "cafir": DAV,
            "cafir_snapshot": SNAPSHOT,
        },
        "scan": {
            "tiles_completed": tiles_done,
            "unique_car_codes_seen": len(seen),
            "frozen_eligible_denominator": frozen_den,
            "denominator_drift": len(seen) - frozen_den,
            "sigef_features_seen_unique": len(sigef_seen),
            "sigef_features_by_source_tile_sum": dict(source_counts),
        },
        "cafir_exact_code_index": cafir_meta,
        "high_confidence": {
            "count": len(high),
            "accounted_against_frozen_fallback": accounted_high,
            "unaccounted_due_to_live_drift_or_missing_fallback_fields": unaccounted_high,
            "pct_of_frozen_denominator": round(high_pct, 6),
            "promoted_from": dict(promoted_from),
        },
        "cascade_after_high_confidence": {
            "counts": dict(final_counts),
            "percentages": {k: round(v * 100.0 / frozen_den, 6) for k, v in final_counts.items()},
            "total_automatic_name_count": total_automatic,
            "total_automatic_name_pct": round(total_pct, 6),
        },
        "stop_decision": stop,
        "reference_property": ref,
        "optimization_after_result_allowed": False,
        "notes": [
            "Known benchmark denomination is never used as an input to matching.",
            "Raw SIGEF names do not become CAR names; only exact INCRA-code bridge followed by one active CAFIR denomination is accepted.",
            "The 25% stop criterion was frozen before this measurement."
        ],
    }
    out = root / "benchmark/car_name_high_confidence_mg_2026-09-06.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Runtime index contains only validated CAR->name links, no personal data.
    idx_path = root / "data/cafir/mg_high_confidence_car_names.json.gz"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    import gzip
    with gzip.open(idx_path, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"meta": {"protocol": result["protocol"], "method": result["method"], "generated_at_utc": result["generated_at_utc"]}, "cars": high}, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"RESULT_PATH={out}", flush=True)
    print(f"INDEX_PATH={idx_path} entries={len(high)} bytes={idx_path.stat().st_size}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

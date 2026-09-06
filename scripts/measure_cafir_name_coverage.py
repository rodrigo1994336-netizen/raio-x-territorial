from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import national_property_name_registry_v44 as registry


def _records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("features"), list):
        for f in payload["features"]:
            if isinstance(f, dict):
                yield f.get("properties") or {}
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item.get("properties") if isinstance(item.get("properties"), dict) else item
        return
    raise ValueError("expected JSON array or GeoJSON FeatureCollection")


def _first(p: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if p.get(k) not in (None, ""):
            return p.get(k)
    return None


def measure(payload: Any, db: str | None = None) -> dict[str, Any]:
    counts = Counter()
    details: list[dict[str, Any]] = []
    for p in _records(payload):
        code = str(_first(p, "cod_imovel", "car_code", "car") or "").strip().upper()
        uf = _first(p, "uf") or (code[:2] if len(code) >= 2 else None)
        ibge = _first(p, "cod_municipio_ibge", "ibge_code")
        if not ibge and len(code) >= 10 and code[2:3] == "-":
            ibge = code[3:10]
        hit = registry.lookup_unique_by_location_area(
            ibge_code=ibge,
            uf=uf,
            municipality=_first(p, "municipio", "municipality"),
            area_ha=_first(p, "area", "area_ha", "area_total"),
            municipality_id=_first(p, "id_municipio", "municipality_id"),
            district=_first(p, "distrito", "district"),
            address=_first(p, "endereco", "address"),
            path=db,
        )
        status = hit.get("resolution_status") or "other"
        counts[status] += 1
        details.append({
            "car_code": code or None,
            "status": status,
            "name": (hit.get("chosen") or {}).get("name"),
            "candidate_count": hit.get("count", 0),
            "area_tolerance_ha": hit.get("area_tolerance_ha"),
            "area_tolerance_rule": hit.get("area_tolerance_rule"),
            "match_basis": hit.get("match_basis"),
            "disambiguation_used": hit.get("disambiguation_used") or [],
        })
    total = sum(counts.values())
    primary = {k: int(counts.get(k, 0)) for k in ("matched", "ambiguous", "absent")}
    pct = {k: (round(v * 100.0 / total, 2) if total else 0.0) for k, v in primary.items()}
    other = total - sum(primary.values())
    return {
        "total": total,
        "counts": {**primary, "other": other},
        "percent": {**pct, "other": (round(other * 100.0 / total, 2) if total else 0.0)},
        "area_tolerance_rule": registry.AREA_TOLERANCE_RULE,
        "details": details,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure CAFIR/SNCR rural-property name coverage for CAR records")
    ap.add_argument("--cars-json", required=True, help="JSON array or GeoJSON FeatureCollection with CAR properties")
    ap.add_argument("--db", default=None, help="property-name SQLite registry")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    if args.db:
        os.environ[registry.DB_ENV] = args.db
    payload = json.loads(Path(args.cars_json).read_text(encoding="utf-8"))
    out = measure(payload, args.db)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

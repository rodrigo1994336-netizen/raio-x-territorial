"""W1a: UF lookup without any external service.

The state borders come from the IBGE malhas API v3 (qualidade maxima, UF level), simplified
to 0.002 degree and embedded in data/uf_br_ibge_v3.json.gz. Measured against the 853
municipalities of the IBGE municipal mesh, the MG border of that file deviates p99 0.69 km,
max 1.46 km (details inside the data file). AMBIGUITY_DEG (0.03 degree, about 3.3 km) is more
than twice that measured maximum:

* a map cell closer than that to a border asks the SICAR layer of every UF it touches;
* a single point closer than that to a border is "ambiguous" and returns None, so the caller
  keeps its previous resolver (Nominatim) instead of guessing a state.

Never used to measure anything: it only chooses which public SICAR layer to query.
"""
from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path
from typing import Any

from shapely.geometry import Point, box, shape
from shapely.prepared import prep

DATA_PATH = Path(__file__).resolve().parent / "data" / "uf_br_ibge_v3.json.gz"
AMBIGUITY_DEG = 0.03
EXPECTED_UFS = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

_LOCK = threading.Lock()
_STATES: list[tuple[str, Any, Any, tuple[float, float, float, float]]] | None = None


def _load() -> list[tuple[str, Any, Any, tuple[float, float, float, float]]]:
    global _STATES
    if _STATES is None:
        with _LOCK:
            if _STATES is None:
                raw = json.loads(gzip.decompress(DATA_PATH.read_bytes()).decode("utf-8"))
                items = []
                for uf, g in (raw.get("ufs") or {}).items():
                    geom = shape({"type": g["type"], "coordinates": g["coordinates"]})
                    items.append((uf, geom, prep(geom), geom.bounds))
                if {x[0] for x in items} != EXPECTED_UFS:
                    raise RuntimeError("uf_locator_br: embedded mesh must contain the 27 UFs")
                _STATES = items
    return _STATES


def ufs_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """Every UF within AMBIGUITY_DEG of the box, the one covering most of the box first."""
    a = AMBIGUITY_DEG
    wide = box(west - a, south - a, east + a, north + a)
    core = box(west, south, east, north)
    hits: list[tuple[float, float, str]] = []
    for uf, geom, prepared, (bw, bs, be, bn) in _load():
        if be < west - a or bw > east + a or bn < south - a or bs > north + a:
            continue
        if not prepared.intersects(wide):
            continue
        try:
            inside = geom.intersection(core).area
        except Exception:
            inside = 0.0
        near = 0.0 if inside else geom.distance(core)
        hits.append((-inside, near, uf))
    hits.sort()
    return [uf for _, _, uf in hits]


def uf_for_point(lat: float, lon: float) -> str | None:
    """The UF of a point, or None when outside Brazil or too close to a border to be sure."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    candidates = ufs_for_bbox(lon, lat, lon, lat)
    if len(candidates) != 1:
        return None
    uf = candidates[0]
    for code, _geom, prepared, _bounds in _load():
        if code == uf:
            return uf if prepared.contains(Point(lon, lat)) else None
    return None

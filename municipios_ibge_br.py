"""F1B (1B.6): municipality search without any external service.

Every IBGE municipality (name, UF, centroid and bounding box) is embedded in
data/municipios_br_ibge.json.gz (built by scripts/build_municipios_ibge.py from the IBGE APIs). The
portal's municipality search and the advanced search's municipality box read it from memory: no
Nominatim, no IBGE call at request time, the same answer every time.

Matching ignores accents, case, hyphens and apostrophes ("sao joao del rei" finds "São João del-Rei";
"Brasilia" finds "Brasília"). A trailing UF ("Curvelo MG", "Curvelo - MG", "Curvelo/MG") or state name
("Formosa, Goiás") narrows the answer to that UF. Municipalities IBGE lists without a published mesh
(created after it) are not navigable and are never given an invented position.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent / "data" / "municipios_br_ibge.json.gz"
STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
MIN_EXPECTED = 5500  # IBGE had 5,570 municipalities with mesh on 2026-09-14; a truncated file must not load

_LOCK = threading.Lock()
_DATA: dict[str, Any] | None = None


def norm(value: Any) -> str:
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[-'’`´./,;:()]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STATE_BY_NORM = {norm(v): k for k, v in STATE_NAMES.items()}


def _load() -> dict[str, Any]:
    global _DATA
    if _DATA is None:
        with _LOCK:
            if _DATA is None:
                doc = json.loads(gzip.decompress(DATA_PATH.read_bytes()).decode("utf-8"))
                rows = doc.get("rows") or []
                if len(rows) < MIN_EXPECTED or {r[2] for r in rows} != set(STATE_NAMES):
                    raise RuntimeError("municipios_ibge_br: embedded list must cover the 27 UFs")
                items = []
                for code, name, uf, lat, lon, west, south, east, north in rows:
                    items.append({
                        "ibge": int(code), "name": name, "uf": uf, "lat": float(lat), "lon": float(lon),
                        "west": float(west), "south": float(south), "east": float(east), "north": float(north),
                        "key": norm(name),
                    })
                _DATA = {"items": items, "downloaded": doc.get("downloaded"), "source": doc.get("source")}
    return _DATA


def _split_uf(text: str) -> tuple[str, str | None]:
    """'curvelo mg' -> ('curvelo', 'MG'); 'formosa goias' -> ('formosa', 'GO'). Never strips the whole query."""
    t = norm(text)
    words = t.split(" ")
    if len(words) >= 2 and words[-1].upper() in STATE_NAMES:
        return " ".join(words[:-1]), words[-1].upper()
    for state_key, uf in sorted(_STATE_BY_NORM.items(), key=lambda kv: -len(kv[0])):
        if t.endswith(" " + state_key) and len(t) > len(state_key) + 1:
            return t[: -len(state_key) - 1].strip(), uf
    return t, None


def search(query: str, uf: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """Municipalities matching the query: exact name first, then name prefix, then word prefix, then substring."""
    term, uf_in_text = _split_uf(query)
    found = _search(term, (uf or uf_in_text or "").upper() or None, limit)
    if not found and uf_in_text and not uf:
        # the trailing word was part of the name, not a UF
        found = _search(norm(query), None, limit)
    return found


def _search(term: str, wanted_uf: str | None, limit: int) -> list[dict[str, Any]]:
    if len(term) < 2:
        return []
    ranked = []
    compact = term.replace(" ", "")
    for it in _load()["items"]:
        if wanted_uf and it["uf"] != wanted_uf:
            continue
        key = it["key"]
        if key == term:
            rank = 0
        elif key.startswith(term):
            rank = 1
        elif (" " + term) in (" " + key):
            rank = 2
        elif term in key:
            rank = 3
        elif compact in key.replace(" ", ""):
            rank = 4  # "alta floresta doeste" -> "Alta Floresta D'Oeste"
        else:
            continue
        ranked.append((rank, len(key), key, it["uf"], it))
    ranked.sort(key=lambda r: r[:4])
    return [r[4] for r in ranked[: max(1, int(limit))]]


def find(municipality: str, uf: str) -> dict[str, Any] | None:
    """The single municipality of that name in that UF (accent-insensitive), or None."""
    code = str(uf or "").strip().upper()
    term = norm(municipality)
    if not term or code not in STATE_NAMES:
        return None
    exact = [it for it in _load()["items"] if it["uf"] == code and it["key"] == term]
    if exact:
        return exact[0]
    prefix = [it for it in _load()["items"] if it["uf"] == code and it["key"].startswith(term)]
    return prefix[0] if len(prefix) == 1 else None


def city_item(it: dict[str, Any]) -> dict[str, Any]:
    """The item contract of /v1/live/cities (kept from the Nominatim era: name, display_name, state, uf, lat, lon, boundingbox)."""
    state = STATE_NAMES[it["uf"]]
    return {
        "name": it["name"],
        "display_name": f"{it['name']}, {state}, Brasil",
        "state": state,
        "uf": it["uf"],
        "lat": it["lat"],
        "lon": it["lon"],
        # Nominatim order and type (strings: south, north, west, east), so existing readers keep working.
        "boundingbox": [f"{it['south']:.4f}", f"{it['north']:.4f}", f"{it['west']:.4f}", f"{it['east']:.4f}"],
        "ibge": it["ibge"],
        "source": "IBGE — lista oficial de municípios",
    }

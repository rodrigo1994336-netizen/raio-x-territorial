"""Builds data/municipios_br_ibge.json.gz: every IBGE municipality with UF, centroid and bounding box.

Sources (IBGE, public, no key):
  * names and UF: /api/v1/localidades/municipios?view=nivelado
  * centroid and bounding box: /api/v3/malhas/paises/BR/metadados?intrarregiao=municipio
    ("centroide" and "regiao-limitrofe", same mesh family as data/uf_br_ibge_v3.json.gz)

The portal answers the municipality search from this file (municipios_ibge_br.py); nothing is asked to
Nominatim or IBGE at request time. Run by hand when IBGE publishes a new municipality:
    python scripts/build_municipios_ibge.py
The build refuses to write a file that does not pair every municipality with its metadata.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "municipios_br_ibge.json.gz"
NAMES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
META_URL = "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR/metadados?intrarregiao=municipio"
UFS = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Raio-X-Territorial/municipios-build"})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":  # IBGE answers gzip even when it was not asked for
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def main() -> int:
    names = get(NAMES_URL)
    meta = {str(m["id"]): m for m in get(META_URL)}
    rows = []
    missing = []
    for n in names:
        code = str(n["municipio-id"])
        m = meta.get(code)
        if not m:
            missing.append(code)
            continue
        c = m["centroide"]
        a, b = m["regiao-limitrofe"]
        west, east = sorted((float(a["longitude"]), float(b["longitude"])))
        south, north = sorted((float(a["latitude"]), float(b["latitude"])))
        lat, lon = float(c["latitude"]), float(c["longitude"])
        uf = str(n["UF-sigla"]).upper()
        if uf not in UFS or not (south <= north and west <= east):
            raise SystemExit(f"invalid row {code} {uf}")
        rows.append([int(code), n["municipio-nome"], uf, round(lat, 4), round(lon, 4),
                     round(west, 4), round(south, 4), round(east, 4), round(north, 4)])
    # A municipality IBGE lists but has no mesh for yet (created after the last mesh) is kept out of the
    # navigable rows: its position is not published, and the portal never guesses one. It is recorded.
    without_mesh = []
    by_code = {str(n["municipio-id"]): n for n in names}
    for code in missing:
        try:
            get(f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{code}/metadados")
            raise SystemExit(f"metadata exists for {code} but was not in the country listing: rerun")
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 500):
                raise
        n = by_code[code]
        without_mesh.append([int(code), n["municipio-nome"], str(n["UF-sigla"]).upper()])
    if len(rows) + len(without_mesh) != len(names) or {r[2] for r in rows} != UFS or len(without_mesh) > 5:
        raise SystemExit(f"incomplete build: rows={len(rows)} without_mesh={without_mesh}")
    rows.sort(key=lambda r: r[0])
    doc = {
        "source": f"IBGE: {NAMES_URL} + {META_URL}",
        "downloaded": dt.date.today().isoformat(),
        "fields": ["ibge", "nome", "uf", "lat", "lon", "west", "south", "east", "north"],
        "count": len(rows),
        "rows": rows,
        "without_mesh": without_mesh,
    }
    OUT.write_bytes(gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"wrote {OUT} municipalities={len(rows)} bytes={OUT.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

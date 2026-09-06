from __future__ import annotations

"""Nationwide local registry for public rural-property denominations.

The registry intentionally stores only non-personal fields needed by Raio-X Territorial:
property identifiers, public denomination, municipality, UF, area and provenance.

Primary public sources supported:
- SNCR/INCRA Consulta Pública (ODbL): denomination + IBGE municipality + area.
- CAFIR/RFB Dados Abertos (CC BY): CIB/NIRF + INCRA/SNCR code + name + area + locality.

No owner/titular fields are imported or exposed by this module.
"""

import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DB_ENV = "RX_PROPERTY_NAMES_DB"
DEFAULT_DB = "data/rx_property_names.sqlite3"

SOURCE_SNCR = "SNCR/INCRA — Consulta Pública de Imóveis Rurais"
SOURCE_CAFIR = "CAFIR/RFB — Dados Abertos"
LICENSE_SNCR = "ODbL"
LICENSE_CAFIR = "CC BY"

_GENERIC = {
    "IMOVEL RURAL", "IMÓVEL RURAL", "SEM DENOMINACAO", "SEM DENOMINAÇÃO",
    "FAZENDA", "SITIO", "SÍTIO", "CHACARA", "CHÁCARA", "GLEBA", "AREA RURAL", "ÁREA RURAL",
}
_UFS = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"
}


def _norm_text(value: Any) -> str:
    s = " ".join(str(value or "").strip().split())
    return unicodedata.normalize("NFKC", s)


def _fold(value: Any) -> str:
    s = _norm_text(value)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def clean_name(value: Any) -> str | None:
    s = _norm_text(value)
    if len(s) < 3 or _fold(s) in {_fold(x) for x in _GENERIC}:
        return None
    return s[:180]


def clean_sncr(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 8 <= len(digits) <= 13 else None


def clean_cib(value: Any) -> str | None:
    s = re.sub(r"[^0-9A-Z]", "", str(value or "").upper())
    return s if 8 <= len(s) <= 10 else None


def clean_ibge(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 7 else None


def parse_area(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        x = float(s)
        return x if 0 < x < 100_000_000 else None
    except Exception:
        return None


def db_path() -> Path:
    return Path(os.getenv(DB_ENV, DEFAULT_DB)).expanduser()


def connect(path: str | os.PathLike[str] | None = None, *, readonly: bool = False) -> sqlite3.Connection | None:
    p = Path(path) if path else db_path()
    if readonly and not p.exists():
        return None
    if not readonly:
        p.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=3.0)
    else:
        con = sqlite3.connect(str(p), timeout=30.0)
    con.row_factory = sqlite3.Row
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS property_names (
          id INTEGER PRIMARY KEY,
          source TEXT NOT NULL,
          license TEXT,
          source_record_id TEXT,
          sncr_code TEXT,
          cib TEXT,
          name TEXT NOT NULL,
          name_norm TEXT NOT NULL,
          uf TEXT,
          municipality TEXT,
          municipality_norm TEXT,
          ibge_code TEXT,
          area_ha REAL,
          status TEXT,
          source_date TEXT,
          origin_url TEXT,
          fingerprint TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS ix_property_names_sncr ON property_names(sncr_code);
        CREATE INDEX IF NOT EXISTS ix_property_names_cib ON property_names(cib);
        CREATE INDEX IF NOT EXISTS ix_property_names_ibge_area ON property_names(ibge_code, area_ha);
        CREATE INDEX IF NOT EXISTS ix_property_names_uf_mun_area ON property_names(uf, municipality_norm, area_ha);
        CREATE INDEX IF NOT EXISTS ix_property_names_name_norm ON property_names(name_norm);

        CREATE TABLE IF NOT EXISTS registry_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    con.commit()


@dataclass(frozen=True)
class Candidate:
    name: str
    source: str
    license: str | None
    sncr_code: str | None
    cib: str | None
    ibge_code: str | None
    uf: str | None
    municipality: str | None
    area_ha: float | None
    source_date: str | None
    origin_url: str | None
    area_delta_ha: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _rows_to_candidates(rows: list[sqlite3.Row], target_area: float | None = None) -> list[Candidate]:
    out: list[Candidate] = []
    for r in rows:
        area = r["area_ha"]
        delta = abs(float(area) - float(target_area)) if area is not None and target_area is not None else None
        out.append(Candidate(
            name=r["name"], source=r["source"], license=r["license"], sncr_code=r["sncr_code"], cib=r["cib"],
            ibge_code=r["ibge_code"], uf=r["uf"], municipality=r["municipality"], area_ha=area,
            source_date=r["source_date"], origin_url=r["origin_url"], area_delta_ha=delta,
        ))
    return out


def _choose_unique_name(candidates: list[Candidate]) -> tuple[Candidate | None, bool]:
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        n = clean_name(c.name)
        if n:
            groups.setdefault(_fold(n), []).append(c)
    if len(groups) != 1:
        return None, len(groups) > 1
    group = next(iter(groups.values()))
    def rank(c: Candidate):
        src = 0 if c.source.startswith("SNCR/") else 1
        delta = c.area_delta_ha if c.area_delta_ha is not None else 999999.0
        return (src, delta, -(len(c.source_date or "")))
    return sorted(group, key=rank)[0], False


def lookup_by_sncr(sncr_code: Any, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    code = clean_sncr(sncr_code)
    if not code:
        return {"ok": False, "chosen": None, "conflict": False, "detail": "invalid_sncr_code", "items": []}
    con = connect(path, readonly=True)
    if con is None:
        return {"ok": False, "chosen": None, "conflict": False, "detail": "registry_unavailable", "items": []}
    try:
        rows = con.execute("SELECT * FROM property_names WHERE sncr_code=? ORDER BY source, source_date DESC", (code,)).fetchall()
    finally:
        con.close()
    items = _rows_to_candidates(rows)
    chosen, conflict = _choose_unique_name(items)
    return {"ok": True, "chosen": chosen.as_dict() if chosen else None, "conflict": conflict, "items": [x.as_dict() for x in items[:20]], "count": len(items), "method": "official_sncr_code"}


def lookup_unique_by_location_area(
    *, ibge_code: Any = None, uf: Any = None, municipality: Any = None, area_ha: Any = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Conservative CAR -> official-registry fallback.

    Stage A uses exact IBGE municipality + a tight area tolerance suitable for SNCR's
    four-decimal areas. If no rows exist there, stage B uses UF + municipality name and a
    0.051 ha window so CAFIR's fixed-width one-decimal area can still match safely.
    A name is promoted only when all matching records collapse to exactly one denomination.
    """
    area = parse_area(area_ha)
    ibge = clean_ibge(ibge_code)
    ufv = _fold(uf)[:2] if uf else None
    mun = _fold(municipality) if municipality else None
    if area is None or (not ibge and not (ufv and mun)):
        return {"ok": False, "chosen": None, "conflict": False, "detail": "missing_location_or_area", "items": []}

    tight_tol = min(0.05, max(0.002, area * 0.00002))
    cafir_tol = 0.051
    con = connect(path, readonly=True)
    if con is None:
        return {"ok": False, "chosen": None, "conflict": False, "detail": "registry_unavailable", "items": []}
    rows: list[sqlite3.Row] = []
    basis: str | None = None
    tolerance = tight_tol
    try:
        if ibge:
            rows = con.execute(
                "SELECT * FROM property_names WHERE ibge_code=? AND area_ha BETWEEN ? AND ? ORDER BY ABS(area_ha-?) ASC, source, source_date DESC LIMIT 80",
                (ibge, area-tight_tol, area+tight_tol, area),
            ).fetchall()
            if rows:
                basis = "ibge_area"
        # CAFIR fixed-width data has UF+municipality but not IBGE code. Only fall back
        # when the stronger IBGE-stage produced no candidate at all.
        if not rows and ufv and mun:
            tolerance = cafir_tol
            rows = con.execute(
                "SELECT * FROM property_names WHERE uf=? AND municipality_norm=? AND area_ha BETWEEN ? AND ? ORDER BY ABS(area_ha-?) ASC, source, source_date DESC LIMIT 80",
                (ufv, mun, area-cafir_tol, area+cafir_tol, area),
            ).fetchall()
            if rows:
                basis = "uf_municipality_area"
    finally:
        con.close()

    items = _rows_to_candidates(rows, area)
    chosen, conflict = _choose_unique_name(items)
    return {
        "ok": True, "chosen": chosen.as_dict() if chosen else None, "conflict": conflict,
        "items": [x.as_dict() for x in items[:20]], "count": len(items),
        "area_tolerance_ha": tolerance, "match_basis": basis,
        "method": "unique_official_municipality_area",
    }


def registry_status(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    con = connect(path, readonly=True)
    if con is None:
        return {"available": False, "path": str(Path(path) if path else db_path()), "records": 0, "ufs_present": [], "national_ready": False}
    try:
        records = int(con.execute("SELECT COUNT(*) FROM property_names").fetchone()[0])
        sources = {r[0]: int(r[1]) for r in con.execute("SELECT source,COUNT(*) FROM property_names GROUP BY source")}
        by_uf = {r[0]: int(r[1]) for r in con.execute("SELECT uf,COUNT(*) FROM property_names WHERE uf IS NOT NULL AND LENGTH(uf)=2 GROUP BY uf ORDER BY uf") if r[0]}
        ufs_present = sorted(set(by_uf) & _UFS)
        meta = {r[0]: r[1] for r in con.execute("SELECT key,value FROM registry_meta")}
        return {
            "available": True, "path": str(Path(path) if path else db_path()), "records": records,
            "sources": sources, "by_uf": by_uf, "ufs_present": ufs_present,
            "uf_count": len(ufs_present), "national_ready": set(ufs_present) == _UFS, "meta": meta,
        }
    finally:
        con.close()

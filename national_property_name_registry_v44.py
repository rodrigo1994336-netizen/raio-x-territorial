from __future__ import annotations

"""Nationwide local registry for public rural-property denominations.

Only non-personal fields needed for name resolution are stored. Owner/titular,
CPF/CNPJ, telephone and e-mail are intentionally excluded.
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
AREA_TOLERANCE_REL = 0.005
AREA_TOLERANCE_ABS_HA = 0.01
AREA_TOLERANCE_RULE = "max(0.5% da área CAR, 0.01 ha)"

_GENERIC = {
    "IMOVEL RURAL", "IMÓVEL RURAL", "SEM DENOMINACAO", "SEM DENOMINAÇÃO",
    "FAZENDA", "SITIO", "SÍTIO", "CHACARA", "CHÁCARA", "GLEBA", "AREA RURAL", "ÁREA RURAL",
}
_UFS = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"
}


def _norm_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", " ".join(str(value or "").strip().split()))


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


def clean_municipality_id(value: Any) -> str | None:
    s = re.sub(r"[^0-9A-Z_-]", "", str(value or "").upper())
    return s[:40] if s else None


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


def area_tolerance_ha(area_ha: Any) -> float | None:
    area = parse_area(area_ha)
    return max(AREA_TOLERANCE_ABS_HA, area * AREA_TOLERANCE_REL) if area is not None else None


def db_path() -> Path:
    return Path(os.getenv(DB_ENV, DEFAULT_DB)).expanduser()


def connect(path: str | os.PathLike[str] | None = None, *, readonly: bool = False) -> sqlite3.Connection | None:
    p = Path(path) if path else db_path()
    if readonly and not p.exists():
        return None
    if not readonly:
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=3.0) if readonly else sqlite3.connect(str(p), timeout=30.0)
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
          municipality_id TEXT,
          ibge_code TEXT,
          area_ha REAL,
          district TEXT,
          district_norm TEXT,
          address TEXT,
          address_norm TEXT,
          status TEXT,
          source_date TEXT,
          origin_url TEXT,
          fingerprint TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS registry_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(property_names)")}
    for name, decl in (
        ("municipality_id", "TEXT"), ("district", "TEXT"), ("district_norm", "TEXT"),
        ("address", "TEXT"), ("address_norm", "TEXT"),
    ):
        if name not in cols:
            con.execute(f"ALTER TABLE property_names ADD COLUMN {name} {decl}")
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS ix_property_names_sncr ON property_names(sncr_code);
        CREATE INDEX IF NOT EXISTS ix_property_names_cib ON property_names(cib);
        CREATE INDEX IF NOT EXISTS ix_property_names_ibge_area ON property_names(ibge_code, area_ha);
        CREATE INDEX IF NOT EXISTS ix_property_names_uf_mun_area ON property_names(uf, municipality_norm, area_ha);
        CREATE INDEX IF NOT EXISTS ix_property_names_munid_area ON property_names(municipality_id, area_ha);
        CREATE INDEX IF NOT EXISTS ix_property_names_name_norm ON property_names(name_norm);
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
    municipality_id: str | None
    uf: str | None
    municipality: str | None
    area_ha: float | None
    district: str | None
    address: str | None
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
        keys = set(r.keys())
        out.append(Candidate(
            name=r["name"], source=r["source"], license=r["license"], sncr_code=r["sncr_code"], cib=r["cib"],
            ibge_code=r["ibge_code"], municipality_id=r["municipality_id"] if "municipality_id" in keys else None,
            uf=r["uf"], municipality=r["municipality"], area_ha=area,
            district=r["district"] if "district" in keys else None, address=r["address"] if "address" in keys else None,
            source_date=r["source_date"], origin_url=r["origin_url"], area_delta_ha=delta,
        ))
    return out


def _name_groups(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        n = clean_name(c.name)
        if n:
            groups.setdefault(_fold(n), []).append(c)
    return groups


def _choose_unique_name(candidates: list[Candidate]) -> tuple[Candidate | None, bool]:
    groups = _name_groups(candidates)
    if len(groups) != 1:
        return None, len(groups) > 1
    group = next(iter(groups.values()))
    def rank(c: Candidate):
        src = 0 if c.source.startswith("SNCR/") else 1
        delta = c.area_delta_ha if c.area_delta_ha is not None else 999999.0
        return (src, delta, -(len(c.source_date or "")))
    return sorted(group, key=rank)[0], False


def _filter_extra(candidates: list[Candidate], *, municipality_id: Any = None, district: Any = None, address: Any = None) -> tuple[list[Candidate], list[str]]:
    current = list(candidates)
    used: list[str] = []
    mid = clean_municipality_id(municipality_id)
    if mid:
        matched = [c for c in current if clean_municipality_id(c.municipality_id) == mid]
        if matched:
            current = matched; used.append("id_municipio")
    d = _fold(district) if district else ""
    if d:
        matched = [c for c in current if _fold(c.district) == d]
        if matched:
            current = matched; used.append("distrito")
    a = _fold(address) if address else ""
    if a:
        matched = [c for c in current if _fold(c.address) == a]
        if matched:
            current = matched; used.append("endereco")
    return current, used


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
    return {"ok": True, "chosen": chosen.as_dict() if chosen else None, "conflict": conflict, "items": [x.as_dict() for x in items[:20]], "count": len(items), "method": "official_sncr_code", "resolution_status": "matched" if chosen else ("ambiguous" if conflict else "absent")}


def lookup_unique_by_location_area(
    *, ibge_code: Any = None, uf: Any = None, municipality: Any = None, area_ha: Any = None,
    municipality_id: Any = None, district: Any = None, address: Any = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """CAR -> official-registry fallback with declared-area tolerance and strict uniqueness.

    Area window: max(0.5% of CAR-declared area, 0.01 ha). A name is promoted only if all
    remaining candidates collapse to one denomination. Optional CAFIR locality fields may
    disambiguate only when equivalent trusted context is supplied by the caller.
    """
    area = parse_area(area_ha)
    ibge = clean_ibge(ibge_code)
    ufv = _fold(uf)[:2] if uf else None
    mun = _fold(municipality) if municipality else None
    tol = area_tolerance_ha(area)
    if area is None or tol is None or (not ibge and not (ufv and mun)):
        return {"ok": False, "chosen": None, "conflict": False, "detail": "missing_location_or_area", "items": [], "resolution_status": "insufficient_context"}

    con = connect(path, readonly=True)
    if con is None:
        return {"ok": False, "chosen": None, "conflict": False, "detail": "registry_unavailable", "items": [], "resolution_status": "registry_unavailable"}
    rows: list[sqlite3.Row] = []
    basis: str | None = None
    try:
        if ibge:
            rows = con.execute(
                "SELECT * FROM property_names WHERE ibge_code=? AND area_ha BETWEEN ? AND ? ORDER BY ABS(area_ha-?) ASC, source, source_date DESC LIMIT 200",
                (ibge, area-tol, area+tol, area),
            ).fetchall()
            if rows:
                basis = "ibge_area"
        if not rows and ufv and mun:
            rows = con.execute(
                "SELECT * FROM property_names WHERE uf=? AND municipality_norm=? AND area_ha BETWEEN ? AND ? ORDER BY ABS(area_ha-?) ASC, source, source_date DESC LIMIT 200",
                (ufv, mun, area-tol, area+tol, area),
            ).fetchall()
            if rows:
                basis = "uf_municipality_area"
    finally:
        con.close()

    items = _rows_to_candidates(rows, area)
    if not items:
        return {
            "ok": True, "chosen": None, "conflict": False, "items": [], "count": 0,
            "area_tolerance_ha": tol, "area_tolerance_rule": AREA_TOLERANCE_RULE,
            "match_basis": basis, "method": "unique_official_municipality_area",
            "resolution_status": "absent", "disambiguation_used": [],
        }

    chosen, conflict = _choose_unique_name(items)
    disambiguation_used: list[str] = []
    considered = items
    if not chosen and conflict and any((municipality_id, district, address)):
        considered, disambiguation_used = _filter_extra(items, municipality_id=municipality_id, district=district, address=address)
        chosen, conflict = _choose_unique_name(considered)

    status = "matched" if chosen else ("ambiguous" if conflict or len(_name_groups(considered)) > 1 else "absent")
    return {
        "ok": True, "chosen": chosen.as_dict() if chosen else None, "conflict": status == "ambiguous",
        "items": [x.as_dict() for x in items[:20]], "count": len(items), "considered_count": len(considered),
        "area_tolerance_ha": tol, "area_tolerance_rule": AREA_TOLERANCE_RULE,
        "match_basis": basis, "method": "unique_official_municipality_area",
        "resolution_status": status, "disambiguation_used": disambiguation_used,
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
            "area_tolerance_rule": AREA_TOLERANCE_RULE,
        }
    finally:
        con.close()

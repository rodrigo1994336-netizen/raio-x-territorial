from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from national_property_name_registry_v44 import (  # noqa: E402
    LICENSE_CAFIR,
    LICENSE_SNCR,
    SOURCE_CAFIR,
    SOURCE_SNCR,
    clean_cib,
    clean_ibge,
    clean_name,
    clean_sncr,
    init_schema,
    parse_area,
)

# Official CAFIR flat-file layout used by RFB public data. The area field is 8,1:
# nine digits with ONE implicit decimal and no comma in the raw fixed-width file.
CAFIR_WIDTHS = (8, 9, 13, 55, 2, 56, 40, 2, 40, 8, 1, 8, 1)
CAFIR_FIELDS = (
    "cib", "area", "sncr", "name", "status", "address", "district", "uf",
    "municipality", "cep", "immune", "registration_date", "sncr_flag",
)


def norm(value: object) -> str:
    s = " ".join(str(value or "").strip().split())
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s.upper()


def pick(row: dict[str, str], *names: str) -> str | None:
    folded = {norm(k): v for k, v in row.items()}
    for name in names:
        value = folded.get(norm(name))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def fingerprint(*parts: object) -> str:
    raw = "\x1f".join(norm(x) for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "iso-8859-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample[:65536], delimiters=";,\t|").delimiter
    except csv.Error:
        counts = {d: sample[:8192].count(d) for d in (";", ",", "\t", "|")}
        return max(counts, key=counts.get)


def parse_cafir_fixed_area(value: object) -> float | None:
    s = str(value or "").strip()
    if not s:
        return None
    # RFB's fixed-width CAFIR layout defines AREA_TOTAL as 8,1 and stores the decimal
    # implicitly. Example raw 000000350 means 35.0 ha, not 350 ha.
    if re.fullmatch(r"\d{1,9}", s):
        x = int(s) / 10.0
        return x if x > 0 else None
    return parse_area(s)


def iter_delimited(path: Path) -> Iterator[dict[str, str]]:
    text = decode_text(path)
    delim = sniff_delimiter(text)
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    if not reader.fieldnames:
        return
    yield from reader


def iter_sncr_rows(path: Path) -> Iterator[dict[str, object]]:
    for row in iter_delimited(path):
        code = clean_sncr(pick(row, "CÓDIGO DO IMOVEL", "CÓDIGO DO IMÓVEL", "CODIGO DO IMOVEL", "CODIGO IMOVEL", "COD_IMOVEL"))
        name = clean_name(pick(row, "DENOMINAÇÃO DO IMÓVEL", "DENOMIÇÃO DO IMÓVEL", "DENOMINACAO DO IMOVEL", "NOME DO IMÓVEL RURAL", "NOME DO IMOVEL RURAL", "NOME IMOVEL"))
        if not code or not name:
            continue
        ibge = clean_ibge(pick(row, "CÓDIGO DO MUNICÍPIO (IBGE)", "CODIGO DO MUNICIPIO (IBGE)", "CÓDIGO DO MUNICÍPIO", "CODIGO DO MUNICIPIO", "COD_MUNICIPIO"))
        municipality = pick(row, "MUNICÍPIO", "MUNICIPIO")
        uf = pick(row, "UF", "UNIDADE DA FEDERAÇÃO", "UNIDADE DA FEDERACAO")
        area = parse_area(pick(row, "ÁREA TOTAL", "AREA TOTAL", "AREA_TOTAL"))
        yield {
            "source": SOURCE_SNCR, "license": LICENSE_SNCR, "source_record_id": code,
            "sncr_code": code, "cib": None, "name": name,
            "uf": (uf or "").strip().upper()[:2] or None, "municipality": municipality,
            "ibge_code": ibge, "area_ha": area, "status": None,
        }


def split_fixed(line: str) -> dict[str, str]:
    pos = 0
    out: dict[str, str] = {}
    for name, width in zip(CAFIR_FIELDS, CAFIR_WIDTHS):
        out[name] = line[pos:pos + width].strip()
        pos += width
    return out


def iter_cafir_fixed_rows(path: Path) -> Iterator[dict[str, object]]:
    # Since July/2024 RFB has published CAFIR public files separated by UF. The parser
    # streams each part and therefore scales to the entire country without holding it in RAM.
    with path.open("r", encoding="iso-8859-1", errors="replace", newline="") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            f = split_fixed(line)
            name = clean_name(f["name"])
            if not name:
                continue
            cib = clean_cib(f["cib"])
            sncr = clean_sncr(f["sncr"])
            if not cib and not sncr:
                continue
            yield {
                "source": SOURCE_CAFIR, "license": LICENSE_CAFIR, "source_record_id": cib or sncr,
                "sncr_code": sncr, "cib": cib, "name": name,
                "uf": f["uf"].strip().upper()[:2] or None,
                "municipality": f["municipality"].strip() or None,
                "ibge_code": None, "area_ha": parse_cafir_fixed_area(f["area"]),
                "status": f["status"].strip() or None,
            }


def iter_cafir_csv_rows(path: Path) -> Iterator[dict[str, object]]:
    """Import current/converted CAFIR CSV while discarding all person-related columns."""
    for row in iter_delimited(path):
        cib = clean_cib(pick(row, "CIB", "NIRF", "NR-IMOVEL", "NR IMOVEL", "CÓDIGO CIB", "CODIGO CIB"))
        sncr = clean_sncr(pick(row, "CÓDIGO DO IMÓVEL NO INCRA", "CODIGO DO IMOVEL NO INCRA", "NR-INCRA", "NR INCRA", "CÓDIGO INCRA", "CODIGO INCRA"))
        name = clean_name(pick(row, "NOME DO IMÓVEL RURAL", "NOME DO IMOVEL RURAL", "NOME-IMOVEL", "NOME IMOVEL", "NOME"))
        if not name or (not cib and not sncr):
            continue
        uf = pick(row, "UF")
        municipality = pick(row, "MUNICÍPIO", "MUNICIPIO")
        ibge = clean_ibge(pick(row, "CÓDIGO DO MUNICÍPIO (IBGE)", "CODIGO DO MUNICIPIO (IBGE)", "CÓDIGO IBGE", "CODIGO IBGE"))
        raw_area = pick(row, "ÁREA TOTAL", "AREA TOTAL", "AREA_TOTAL", "AREA-TOTAL")
        yield {
            "source": SOURCE_CAFIR, "license": LICENSE_CAFIR, "source_record_id": cib or sncr,
            "sncr_code": sncr, "cib": cib, "name": name,
            "uf": (uf or "").strip().upper()[:2] or None,
            "municipality": municipality, "ibge_code": ibge, "area_ha": parse_area(raw_area),
            "status": pick(row, "SITUAÇÃO", "SITUACAO", "SIT-IMOVEL", "STATUS"),
        }


def insert_rows(
    con: sqlite3.Connection,
    rows: Iterable[dict[str, object]],
    *, source_date: str | None,
    origin_url: str | None,
    batch_size: int = 5000,
) -> tuple[int, int]:
    added = skipped = 0
    sql = """
      INSERT OR IGNORE INTO property_names(
        source,license,source_record_id,sncr_code,cib,name,name_norm,uf,municipality,
        municipality_norm,ibge_code,area_ha,status,source_date,origin_url,fingerprint
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    pending: list[tuple[object, ...]] = []
    for row in rows:
        name = clean_name(row.get("name"))
        if not name:
            skipped += 1
            continue
        fp = fingerprint(
            row.get("source"), row.get("source_record_id"), row.get("sncr_code"), row.get("cib"),
            name, row.get("uf"), row.get("municipality"), row.get("ibge_code"), row.get("area_ha"),
        )
        pending.append((
            row.get("source"), row.get("license"), row.get("source_record_id"), row.get("sncr_code"), row.get("cib"),
            name, norm(name), row.get("uf"), row.get("municipality"), norm(row.get("municipality")), row.get("ibge_code"),
            row.get("area_ha"), row.get("status"), source_date, origin_url, fp,
        ))
        if len(pending) >= batch_size:
            before = con.total_changes
            con.executemany(sql, pending)
            added += con.total_changes - before
            con.commit(); pending.clear()
    if pending:
        before = con.total_changes
        con.executemany(sql, pending)
        added += con.total_changes - before
        con.commit()
    return added, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Build minimal nationwide rural-property name registry for Raio-X Territorial")
    ap.add_argument("--db", default="data/rx_property_names.sqlite3")
    ap.add_argument("--sncr-csv", action="append", default=[], help="SNCR public CSV (repeatable)")
    ap.add_argument("--cafir-file", action="append", default=[], help="Official CAFIR fixed-width UF/part file (repeatable)")
    ap.add_argument("--cafir-csv", action="append", default=[], help="Official/converted CAFIR delimited CSV (repeatable)")
    ap.add_argument("--source-date", default=None, help="Source extraction/publication date, e.g. 2026-09-01")
    ap.add_argument("--sncr-origin", default="https://sncr.serpro.gov.br/sncr-web/consultaPublica.jsf")
    ap.add_argument("--cafir-origin", default="https://dados.gov.br/dados/conjuntos-dados/cadastro-de-imoveis-rurais---cafir")
    args = ap.parse_args()

    db = Path(args.db); db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=60)
    try:
        init_schema(con)
        stats: dict[str, dict[str, int]] = {}
        jobs = [
            (args.sncr_csv, iter_sncr_rows, args.sncr_origin),
            (args.cafir_file, iter_cafir_fixed_rows, args.cafir_origin),
            (args.cafir_csv, iter_cafir_csv_rows, args.cafir_origin),
        ]
        for paths, parser, origin in jobs:
            for value in paths:
                path = Path(value)
                added, skipped = insert_rows(con, parser(path), source_date=args.source_date, origin_url=origin)
                stats[str(path)] = {"added": added, "skipped": skipped}
        con.execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES('builder_version','v44-national-minimal-v2')")
        if args.source_date:
            con.execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES('last_source_date',?)", (args.source_date,))
        con.commit()
        total = int(con.execute("SELECT COUNT(*) FROM property_names").fetchone()[0])
        sources = {r[0]: int(r[1]) for r in con.execute("SELECT source,COUNT(*) FROM property_names GROUP BY source")}
    finally:
        con.close()
    print(json.dumps({"ok": True, "db": str(db), "records": total, "sources": sources, "files": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

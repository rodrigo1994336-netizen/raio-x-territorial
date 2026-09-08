from __future__ import annotations

"""MTE Cadastro de Empregadores adapter for V48 conformity.

The registry is nominal (CPF/CNPJ + employer name), not territorial. A public
CAR polygon alone is never linked to a registry row. Source availability and a
property-specific answer are deliberately separate facts.
"""

import csv
import io
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

SOURCE_NAME = "Ministério do Trabalho e Emprego — Cadastro de Empregadores"
SOURCE_PAGE = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.csv/view"
SOURCE_CSV = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.csv/@@download/file"
TTL_SECONDS = 6 * 60 * 60
_REQUIRED = {"Empregador", "CNPJ/CPF"}
_CACHE: tuple[float, dict[str, Any]] | None = None


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_registry_csv(raw: bytes) -> list[dict[str, str]]:
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    fields = {str(x or "").strip() for x in (reader.fieldnames or [])}
    if not _REQUIRED.issubset(fields):
        raise ValueError("mte_registry_schema_unexpected")
    rows: list[dict[str, str]] = []
    for row in reader:
        clean = {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
        if clean.get("Empregador") or clean.get("CNPJ/CPF"):
            rows.append(clean)
    if not rows:
        raise ValueError("mte_registry_empty")
    return rows


def _published_date(page_html: str) -> str | None:
    m = re.search(r"Publicado\s+em\s+(\d{2}/\d{2}/\d{4})", page_html or "", re.I)
    if not m:
        return None
    day, month, year = m.group(1).split("/")
    return f"{year}-{month}-{day}"


def _fetch_registry() -> dict[str, Any]:
    global _CACHE
    now_mono = time.monotonic()
    if _CACHE and now_mono - _CACHE[0] < TTL_SECONDS:
        out = dict(_CACHE[1])
        out["cached"] = True
        return out
    queried_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    headers = {
        "User-Agent": "Raio-X-Territorial/V48 (+public-compliance-check)",
        "Accept": "text/csv,text/plain,text/html;q=0.9,*/*;q=0.5",
    }
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
            csv_resp = client.get(SOURCE_CSV)
            csv_resp.raise_for_status()
            rows = parse_registry_csv(csv_resp.content)
            data_date = None
            page_detail = None
            try:
                page_resp = client.get(SOURCE_PAGE)
                page_resp.raise_for_status()
                data_date = _published_date(page_resp.text)
            except Exception as exc:
                page_detail = f"metadata:{type(exc).__name__}"
        out = {
            "ok": True,
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "data_date": data_date,
            "queried_at": queried_at,
            "registry_row_count": len(rows),
            "rows": rows,
            "detail": page_detail,
            "cached": False,
        }
    except Exception as exc:
        out = {
            "ok": False,
            "source": SOURCE_NAME,
            "source_page": SOURCE_PAGE,
            "data_date": None,
            "queried_at": queried_at,
            "registry_row_count": None,
            "rows": [],
            "detail": f"{type(exc).__name__}:{str(exc)[:180]}",
            "cached": False,
        }
    _CACHE = (now_mono, out)
    return dict(out)


def query_mte_slave_labor(owner_document: str | None = None, owner_name: str | None = None) -> dict[str, Any]:
    """Query the official registry without inventing a CAR-to-employer link.

    V48 accepts exact CPF/CNPJ as the property binding key. A name alone is not
    a unique identifier and is intentionally not promoted to a property result.
    """
    source = _fetch_registry()
    common = {
        "source": source.get("source"),
        "source_page": source.get("source_page"),
        "data_date": source.get("data_date"),
        "queried_at": source.get("queried_at"),
        "registry_row_count": source.get("registry_row_count"),
    }
    if not source.get("ok"):
        return {
            **common,
            "ok": False,
            "answered": False,
            "state": "source_failed",
            "match_count": None,
            "reason": "A fonte oficial do MTE não respondeu de forma utilizável; nenhuma ausência foi presumida.",
            "detail": source.get("detail"),
        }

    document = _digits(owner_document)
    if not document:
        return {
            **common,
            "ok": True,
            "answered": False,
            "state": "blocked_missing_owner_identity",
            "match_count": None,
            "reason": (
                "Esta fonte é nominal (CPF/CNPJ). O CAR público analisado não informa com segurança "
                "o proprietário; nenhum vínculo entre empregador e imóvel foi inferido."
            ),
            "owner_name_supplied": bool(str(owner_name or "").strip()),
        }

    matches = [row for row in source.get("rows") or [] if _digits(row.get("CNPJ/CPF")) == document]
    public_matches = [
        {
            "uf": row.get("UF"),
            "employer": row.get("Empregador"),
            "establishment": row.get("Estabelecimento"),
            "inclusion_date": row.get("Incluso no Cadastro de Empregadores"),
        }
        for row in matches[:20]
    ]
    return {
        **common,
        "ok": True,
        "answered": True,
        "state": "checked_hit" if matches else "checked_clear",
        "match_count": len(matches),
        "matches": public_matches,
        "reason": (
            f"{len(matches)} registro(s) com correspondência exata de CPF/CNPJ no Cadastro de Empregadores."
            if matches
            else "Nenhuma correspondência exata de CPF/CNPJ foi localizada no Cadastro de Empregadores consultado."
        ),
    }


print("RX_MTE_SLAVE_LABOR_V48=nominal_registry_fail_closed_owner_binding", flush=True)

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA = "v48-sicar-canonical-snapshots-1"
PINNED_MANIFEST_PATH = ""  # Filled only after the audited immutable manifest is committed.
MANIFEST_NAME_RE = re.compile(r"^sicar-canonical-snapshots-v1-([0-9a-f]{64})\.json$")
UF_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}


def _path() -> Path:
    raw = (os.getenv("RX_SICAR_CANONICAL_MANIFEST") or PINNED_MANIFEST_PATH).strip()
    if not raw:
        raise RuntimeError("canonical_snapshot_manifest_not_pinned")
    path = Path(raw)
    match = MANIFEST_NAME_RE.match(path.name)
    if not match:
        raise RuntimeError("canonical_snapshot_manifest_not_content_addressed")
    return path


def _fingerprint(data: dict[str, Any]) -> str:
    payload = dict(data)
    payload.pop("content_fingerprint_sha256", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_manifest() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("canonical_snapshot_manifest_missing") from exc
    except Exception as exc:
        raise RuntimeError("canonical_snapshot_manifest_invalid_json") from exc
    if data.get("schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError("canonical_snapshot_manifest_schema_mismatch")
    if not isinstance(data.get("ufs"), dict):
        raise RuntimeError("canonical_snapshot_manifest_ufs_missing")
    actual = _fingerprint(data)
    declared = str(data.get("content_fingerprint_sha256") or "")
    filename_hash = MANIFEST_NAME_RE.match(path.name).group(1)  # already validated in _path
    if not declared or actual != declared or filename_hash != declared:
        raise RuntimeError("canonical_snapshot_manifest_fingerprint_mismatch")
    return data


def canonical_entry(uf: str) -> dict[str, Any] | None:
    code = str(uf or "").upper().strip()
    entry = (load_manifest().get("ufs") or {}).get(code) or {}
    return entry if entry.get("status") == "canonical" else None


def canonical_snapshot_for_uf(uf: str) -> dt.date | None:
    code = str(uf or "").upper().strip()
    entry = canonical_entry(code)
    if not entry:
        return None
    try:
        return dt.date.fromisoformat(str(entry.get("snapshot") or ""))
    except Exception as exc:
        raise RuntimeError(f"canonical_snapshot_invalid_date:{code}") from exc


def date_pt(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def base_label(uf: str, snapshot: dt.date) -> str:
    code = str(uf or "").upper().strip()
    return f"Base do CAR de {UF_NAMES.get(code, code)}: {date_pt(snapshot)}"

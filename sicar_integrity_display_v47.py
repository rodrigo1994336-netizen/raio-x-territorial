from __future__ import annotations

"""Customer-facing display contract for V47 CAR integrity.

The geometry/truth engine remains untouched. This module only translates known
engine states into unambiguous pt-BR presentation shared by portal and report.
"""

import re
from typing import Any

from sicar_integrity_v47 import panel_table_rows as _engine_panel_table_rows

ABSENCE_TEXT = "Nenhuma feição desta classe no imóvel"
_DECIMAL_RE = re.compile(r"(?<!\d)(-?\d+)\.(\d+)")


def _ptbr_numeric_text(value: Any) -> str:
    text = str(value or "")
    return _DECIMAL_RE.sub(lambda m: f"{m.group(1)},{m.group(2)}", text)


def format_number_ptbr(value: Any, digits: int = 4, fallback: str = "Indisponível") -> str:
    try:
        return f"{float(value):.{digits}f}".replace(".", ",")
    except Exception:
        return fallback


def format_snapshot_ptbr(value: Any) -> str:
    text = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else (text or "Não informado")


def panel_table_rows(result: dict[str, Any]) -> list[dict[str, str]]:
    """Render engine rows without collapsing known absence into unavailability."""
    raw = _engine_panel_table_rows(result)
    states = {
        str(row.get("key") or ""): str(row.get("state") or "unknown")
        for row in (result.get("composition_rows") or [])
    }
    out: list[dict[str, str]] = []
    for source in raw:
        row = dict(source)
        state = states.get(str(row.get("key") or ""), str(row.get("state") or "unknown"))
        if state == "not_declared_or_not_found":
            row["inside"] = ABSENCE_TEXT
            row["measured"] = ABSENCE_TEXT
        for field in ("declared", "inside", "measured"):
            row[field] = _ptbr_numeric_text(row.get(field))
        out.append(row)
    return out


print("RX_SICAR_INTEGRITY_DISPLAY_V47=ptbr_absence_truth_shared", flush=True)

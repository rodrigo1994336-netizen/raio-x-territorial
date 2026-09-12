from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SOURCE_STATES = frozenset({
    "NOT_QUERIED", "QUERYING", "ANSWERED_CLEAR", "ANSWERED_HIT", "BLOCKED", "FAILED"
})
ANSWERED_STATES = frozenset({"ANSWERED_CLEAR", "ANSWERED_HIT"})

# Canonical implemented-source catalog. The source counter is forbidden from
# declaring or incrementing sources anywhere else.
_SOURCE_CATALOG = (
    ("car", "CAR / SICAR"),
    ("embargo", "Embargos"),
    ("prodes", "PRODES"),
    ("indigenous_land", "Terra Indígena"),
    ("legal_reserve", "Reserva Legal"),
    ("conservation_unit", "Un. Conservação"),
    ("registry", "Matrícula"),
    ("public_forest", "Floresta Pública"),
    ("snci", "SNCI"),
    ("mte_slave_labor", "MTE — Trabalho Escravo"),
    ("sinaflor", "SINAFLOR — Supressão"),
)
SOURCE_IDS = tuple(source_id for source_id, _ in _SOURCE_CATALOG)
SOURCE_COUNT = len(_SOURCE_CATALOG)


def source_registry(states: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    chosen = dict(states or {})
    unknown = set(chosen) - set(SOURCE_IDS)
    if unknown:
        raise ValueError(f"source_not_registered:{','.join(sorted(unknown))}")
    registry: list[dict[str, str]] = []
    for source_id, label in _SOURCE_CATALOG:
        state = chosen.get(source_id, "NOT_QUERIED")
        if state not in SOURCE_STATES:
            raise ValueError(f"invalid_source_state:{source_id}:{state}")
        registry.append({"id": source_id, "label": label, "state": state})
    return registry


def audit_from_registry(registry: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in registry]
    ids = [str(row.get("id") or "") for row in rows]
    if tuple(ids) != SOURCE_IDS or len(set(ids)) != len(ids):
        raise ValueError("source_registry_contract_drift")
    for row in rows:
        if row.get("state") not in SOURCE_STATES:
            raise ValueError(f"invalid_source_state:{row.get('id')}:{row.get('state')}")
    return {
        "available": True,
        "registry": rows,
        "responded": sum(1 for row in rows if row["state"] in ANSWERED_STATES),
        "total": len(rows),
        "deep_sources_requested": False,
    }


def build_source_audit(states: Mapping[str, str] | None = None) -> dict[str, Any]:
    return audit_from_registry(source_registry(states))


def compliance_sources(audit: Mapping[str, Any]) -> list[dict[str, str]]:
    registry = audit.get("registry")
    if not isinstance(registry, list):
        raise ValueError("source_registry_missing")
    return [
        {
            "id": str(row["id"]),
            "label": str(row["label"]),
            "state": "not_consulted",
            "audit_state": str(row["state"]),
        }
        for row in registry
        if row.get("id") != "car"
    ]

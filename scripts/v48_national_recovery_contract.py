from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

ALL_UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GEOMETRY_NORMALIZATION_VERSION = "v48-polygonal-extraction-1"


class PublicationState(str, Enum):
    BUILD_REQUIRED = "BUILD_REQUIRED"
    RECONCILE_MANIFEST_ONLY = "RECONCILE_MANIFEST_ONLY"
    COMPLETE_REUSED = "COMPLETE_REUSED"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class RecoveryDecision:
    state: PublicationState
    reason: str


def normalize_uf(uf: str) -> str:
    value = str(uf or "").strip().upper()
    if value not in ALL_UFS:
        raise ValueError(f"invalid_uf:{value}")
    return value


def require_sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label}_must_be_sha256")
    return normalized


def publication_root(canonical_manifest_fingerprint: str) -> str:
    fp = require_sha256(canonical_manifest_fingerprint, "canonical_manifest_fingerprint")
    return f"car/national/{fp}"


def object_names(*, canonical_manifest_fingerprint: str, uf: str, source_fingerprint: str) -> dict[str, str]:
    state = normalize_uf(uf)
    root = publication_root(canonical_manifest_fingerprint)
    source_fp = require_sha256(source_fingerprint, "source_fingerprint")
    return {
        "receipt": f"{root}/receipts/{state}/{source_fp}.json",
        "pmtiles": f"{root}/uf/{state}/{source_fp}.pmtiles",
        "manifest": f"{root}/manifests/uf/{state}/{source_fp}.json",
    }


def expected_identity(
    *,
    canonical_manifest_fingerprint: str,
    uf: str,
    snapshot_date: str,
    source_fingerprint: str,
    pmtiles_sha256: str,
    pmtiles_size_bytes: int,
) -> dict[str, str]:
    state = normalize_uf(uf)
    manifest_fp = require_sha256(canonical_manifest_fingerprint, "canonical_manifest_fingerprint")
    source_fp = require_sha256(source_fingerprint, "source_fingerprint")
    psha = require_sha256(pmtiles_sha256, "pmtiles_sha256")
    size = int(pmtiles_size_bytes)
    if size <= 127:
        raise ValueError("pmtiles_size_invalid")
    snapshot = str(snapshot_date or "").strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", snapshot):
        raise ValueError("snapshot_date_invalid")
    return {
        "canonical_manifest_fingerprint": manifest_fp,
        "uf": state,
        "snapshot_date": snapshot,
        "analysis_snapshot": snapshot,
        "map_snapshot": snapshot,
        "source_fingerprint": source_fp,
        "pmtiles_sha256": psha,
        "pmtiles_size_bytes": str(size),
    }


def _validate_geometry_truth(receipt: Mapping[str, Any]) -> None:
    analysis_norm = str(receipt.get("analysis_geometry_normalization") or "")
    map_norm = str(receipt.get("map_geometry_normalization") or "")
    if analysis_norm != GEOMETRY_NORMALIZATION_VERSION or map_norm != GEOMETRY_NORMALIZATION_VERSION:
        raise ValueError("receipt_geometry_normalization_mismatch")
    require_sha256(str(receipt.get("source_geometry_set_fingerprint_sha256") or ""), "source_geometry_set_fingerprint")
    require_sha256(str(receipt.get("render_geometry_set_fingerprint_sha256") or ""), "render_geometry_set_fingerprint")

    distinct = int(receipt.get("distinct_car_count") or 0)
    features = int(receipt.get("feature_count") or 0)
    no_geometry = int(receipt.get("no_geometry_car_count") or 0)
    no_polygon = int(receipt.get("no_polygonal_car_count") or 0)
    no_geometry_ids = list(receipt.get("no_geometry_car_ids") or [])
    no_polygon_ids = list(receipt.get("no_polygonal_car_ids") or [])
    normalized = int(receipt.get("normalization_applied_count") or 0)

    if distinct <= 0 or features <= 0:
        raise ValueError("receipt_geometry_counts_invalid")
    if len(no_geometry_ids) != no_geometry:
        raise ValueError("receipt_no_geometry_id_count_mismatch")
    if len(no_polygon_ids) != no_polygon:
        raise ValueError("receipt_no_polygon_id_count_mismatch")
    if len(set(map(str, no_geometry_ids))) != len(no_geometry_ids):
        raise ValueError("receipt_duplicate_no_geometry_ids")
    if len(set(map(str, no_polygon_ids))) != len(no_polygon_ids):
        raise ValueError("receipt_duplicate_no_polygon_ids")
    if features + no_geometry + no_polygon != distinct:
        raise ValueError("receipt_geometry_classification_reconciliation_failed")
    if normalized < 0 or normalized > features:
        raise ValueError("receipt_normalization_count_invalid")


def validate_receipt(
    receipt: Mapping[str, Any],
    *,
    canonical_manifest_fingerprint: str,
    uf: str,
    snapshot_date: str,
) -> dict[str, str]:
    if receipt.get("schema_version") != "v48-national-prepared-receipt-1":
        raise ValueError("receipt_schema_mismatch")
    if receipt.get("status") != "prepared":
        raise ValueError("receipt_status_mismatch")
    identity = expected_identity(
        canonical_manifest_fingerprint=str(receipt.get("canonical_manifest_fingerprint") or ""),
        uf=str(receipt.get("uf") or ""),
        snapshot_date=str(receipt.get("snapshot_date") or ""),
        source_fingerprint=str(receipt.get("source_fingerprint_sha256") or ""),
        pmtiles_sha256=str((receipt.get("pmtiles") or {}).get("sha256") or ""),
        pmtiles_size_bytes=int((receipt.get("pmtiles") or {}).get("size_bytes") or 0),
    )
    if identity["canonical_manifest_fingerprint"] != require_sha256(canonical_manifest_fingerprint, "canonical_manifest_fingerprint"):
        raise ValueError("receipt_manifest_fingerprint_mismatch")
    if identity["uf"] != normalize_uf(uf):
        raise ValueError("receipt_uf_mismatch")
    if identity["snapshot_date"] != str(snapshot_date):
        raise ValueError("receipt_snapshot_mismatch")
    if int(receipt.get("source_row_count_expected") or 0) != int(receipt.get("source_row_count_actual") or -1):
        raise ValueError("receipt_source_row_count_mismatch")
    _validate_geometry_truth(receipt)
    return identity


def build_recovery_commit(
    *,
    receipt: Mapping[str, Any],
    receipt_object: str,
    receipt_generation: str,
    pmtiles_object: str,
    pmtiles_generation: str,
) -> dict[str, Any]:
    _validate_geometry_truth(receipt)
    return {
        "schema_version": "v48-national-publication-commit-1",
        "status": "committed",
        "canonical_manifest_fingerprint": receipt["canonical_manifest_fingerprint"],
        "uf": receipt["uf"],
        "snapshot_date": receipt["snapshot_date"],
        "analysis_snapshot": receipt["snapshot_date"],
        "map_snapshot": receipt["snapshot_date"],
        "analysis_geometry_normalization": receipt["analysis_geometry_normalization"],
        "map_geometry_normalization": receipt["map_geometry_normalization"],
        "source_fingerprint_sha256": receipt["source_fingerprint_sha256"],
        "source_geometry_set_fingerprint_sha256": receipt["source_geometry_set_fingerprint_sha256"],
        "render_geometry_set_fingerprint_sha256": receipt["render_geometry_set_fingerprint_sha256"],
        "source_row_count_expected": int(receipt["source_row_count_expected"]),
        "source_row_count_actual": int(receipt["source_row_count_actual"]),
        "distinct_car_count": int(receipt["distinct_car_count"]),
        "feature_count": int(receipt["feature_count"]),
        "no_geometry_car_count": int(receipt["no_geometry_car_count"]),
        "no_geometry_car_ids": list(receipt.get("no_geometry_car_ids") or []),
        "no_polygonal_car_count": int(receipt["no_polygonal_car_count"]),
        "no_polygonal_car_ids": list(receipt.get("no_polygonal_car_ids") or []),
        "normalization_applied_count": int(receipt["normalization_applied_count"]),
        "pmtiles": {
            "object": pmtiles_object,
            "generation": str(pmtiles_generation),
            "sha256": receipt["pmtiles"]["sha256"],
            "size_bytes": int(receipt["pmtiles"]["size_bytes"]),
        },
        "prepared_receipt": {
            "object": receipt_object,
            "generation": str(receipt_generation),
        },
        "recovery": {
            "mode": "RECONCILE_MANIFEST_ONLY",
            "original_run_metrics_available": False,
        },
        "active_json_updated": False,
    }

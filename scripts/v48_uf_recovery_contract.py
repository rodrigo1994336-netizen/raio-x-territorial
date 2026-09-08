from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

SNAPSHOT = "2026-08-04"
SNAPSHOT_ID = "sicar-2026-08-04"
ALL_UFS = (
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG",
    "PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO",
)
_JOB_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class PublicationState(str, Enum):
    BUILD_REQUIRED = "BUILD_REQUIRED"
    RECONCILE_MANIFEST_ONLY = "RECONCILE_MANIFEST_ONLY"
    COMPLETE_REUSED = "COMPLETE_REUSED"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class RemoteObject:
    name: str
    size_bytes: int
    sha256: str
    metadata: Mapping[str, str]
    generation: str = ""


@dataclass(frozen=True)
class RecoveryDecision:
    state: PublicationState
    reason: str


def _uf(uf: str) -> str:
    value = str(uf).strip().upper()
    if value not in ALL_UFS:
        raise ValueError(f"invalid_uf:{value}")
    return value


def batch_job_id(uf: str, run_id: str | int, run_attempt: str | int) -> str:
    """
    One Cloud Batch job per UF and per GitHub run attempt.
    Re-run failed jobs therefore creates a fresh Batch job only for the
    matrix cells GitHub actually re-runs.
    """
    state = _uf(uf).lower()
    rid = re.sub(r"[^0-9]", "", str(run_id))
    attempt = re.sub(r"[^0-9]", "", str(run_attempt))
    if not rid or not attempt:
        raise ValueError("run_id_and_attempt_must_be_numeric")
    value = f"rx-v48-{state}-sicar-20260804-r{rid}-a{attempt}"
    if not _JOB_RE.fullmatch(value):
        raise ValueError(f"invalid_batch_job_id:{value}")
    return value


def object_names(uf: str, fingerprint: str) -> dict[str, str]:
    state = _uf(uf)
    fp = str(fingerprint).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise ValueError("fingerprint_must_be_sha256")
    return {
        "receipt": f"car/{SNAPSHOT_ID}/receipts/{state}/{fp}.json",
        "pmtiles": f"car/{SNAPSHOT_ID}/uf/{state}.pmtiles",
        "manifest": f"car/{SNAPSHOT_ID}/manifests/uf/{state}/{fp}.json",
    }


def canonical_identity(
    *,
    uf: str,
    fingerprint: str,
    pmtiles_sha256: str,
    pmtiles_size_bytes: int,
) -> dict[str, str]:
    state = _uf(uf)
    fp = str(fingerprint).strip().lower()
    psha = str(pmtiles_sha256).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise ValueError("fingerprint_must_be_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", psha):
        raise ValueError("pmtiles_sha256_must_be_sha256")
    size = int(pmtiles_size_bytes)
    if size <= 127:
        raise ValueError("pmtiles_size_invalid")
    return {
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_date": SNAPSHOT,
        "analysis_snapshot": SNAPSHOT,
        "map_snapshot": SNAPSHOT,
        "uf": state,
        "source_fingerprint": fp,
        "pmtiles_sha256": psha,
        "pmtiles_size_bytes": str(size),
    }


def _matches_identity(obj: RemoteObject, identity: Mapping[str, str]) -> tuple[bool, str]:
    if obj.size_bytes <= 0:
        return False, f"empty_object:{obj.name}"
    meta = {str(k): str(v) for k, v in obj.metadata.items()}
    for key, expected in identity.items():
        actual = meta.get(key)
        if actual != str(expected):
            return False, f"metadata_mismatch:{obj.name}:{key}:{actual}!={expected}"
    if obj.sha256 and obj.sha256 != identity["pmtiles_sha256"] and obj.name.endswith(".pmtiles"):
        return False, f"pmtiles_sha256_mismatch:{obj.name}"
    if obj.name.endswith(".pmtiles") and int(obj.size_bytes) != int(identity["pmtiles_size_bytes"]):
        return False, f"pmtiles_size_mismatch:{obj.name}"
    return True, "identity_match"


def decide_recovery(
    *,
    identity: Mapping[str, str],
    receipt: RemoteObject | None,
    pmtiles: RemoteObject | None,
    manifest: RemoteObject | None,
) -> RecoveryDecision:
    """
    Publication protocol:
      1. immutable receipt (prepared only after local PMTiles is complete);
      2. immutable PMTiles;
      3. immutable commit manifest.

    A manifest is the commit marker. PMTiles without a manifest is never
    considered published.
    """
    expected_keys = {
        "snapshot_id","snapshot_date","analysis_snapshot","map_snapshot","uf",
        "source_fingerprint","pmtiles_sha256","pmtiles_size_bytes",
    }
    if set(identity) != expected_keys:
        return RecoveryDecision(PublicationState.FAIL_CLOSED, "identity_contract_invalid")
    if identity["analysis_snapshot"] != identity["map_snapshot"]:
        return RecoveryDecision(PublicationState.FAIL_CLOSED, "map_analysis_snapshot_divergence")

    if manifest is not None:
        if receipt is None or pmtiles is None:
            return RecoveryDecision(PublicationState.FAIL_CLOSED, "commit_marker_without_prepared_objects")
        for obj in (receipt, pmtiles, manifest):
            ok, reason = _matches_identity(obj, identity)
            if not ok:
                return RecoveryDecision(PublicationState.FAIL_CLOSED, reason)
        return RecoveryDecision(PublicationState.COMPLETE_REUSED, "committed_identity_verified")

    if pmtiles is not None:
        if receipt is None:
            return RecoveryDecision(
                PublicationState.FAIL_CLOSED,
                "orphan_pmtiles_without_receipt_cannot_be_reconciled",
            )
        for obj in (receipt, pmtiles):
            ok, reason = _matches_identity(obj, identity)
            if not ok:
                return RecoveryDecision(PublicationState.FAIL_CLOSED, reason)
        return RecoveryDecision(
            PublicationState.RECONCILE_MANIFEST_ONLY,
            "prepared_receipt_and_pmtiles_verified_manifest_missing",
        )

    if receipt is not None:
        ok, reason = _matches_identity(receipt, identity)
        if not ok:
            return RecoveryDecision(PublicationState.FAIL_CLOSED, reason)
        return RecoveryDecision(
            PublicationState.BUILD_REQUIRED,
            "receipt_exists_but_pmtiles_missing_rebuild_same_uf_only",
        )

    return RecoveryDecision(PublicationState.BUILD_REQUIRED, "no_prepared_objects")


def build_commit_manifest(
    *,
    identity: Mapping[str, str],
    receipt_object: RemoteObject,
    pmtiles_object: RemoteObject,
) -> dict[str, Any]:
    decision = decide_recovery(
        identity=identity,
        receipt=receipt_object,
        pmtiles=pmtiles_object,
        manifest=None,
    )
    if decision.state is not PublicationState.RECONCILE_MANIFEST_ONLY:
        raise RuntimeError(f"cannot_commit:{decision.state}:{decision.reason}")
    return {
        "schema_version": "v48-uf-publication-commit-1",
        "status": "committed",
        "snapshot_id": identity["snapshot_id"],
        "snapshot_date": identity["snapshot_date"],
        "analysis_snapshot": identity["analysis_snapshot"],
        "map_snapshot": identity["map_snapshot"],
        "uf": identity["uf"],
        "source_fingerprint": identity["source_fingerprint"],
        "pmtiles": {
            "object": pmtiles_object.name,
            "generation": pmtiles_object.generation,
            "sha256": identity["pmtiles_sha256"],
            "size_bytes": int(identity["pmtiles_size_bytes"]),
        },
        "prepared_receipt": {
            "object": receipt_object.name,
            "generation": receipt_object.generation,
        },
        "active_json_updated": False,
    }

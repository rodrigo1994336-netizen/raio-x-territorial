from __future__ import annotations

from v48_uf_recovery_contract import (
    PublicationState,
    RemoteObject,
    batch_job_id,
    build_commit_manifest,
    canonical_identity,
    decide_recovery,
    object_names,
)

FP = "a" * 64
SHA = "b" * 64
IDENTITY = canonical_identity(
    uf="ES",
    fingerprint=FP,
    pmtiles_sha256=SHA,
    pmtiles_size_bytes=167_000_688,
)
NAMES = object_names("ES", FP)


def obj(name: str, *, generation: str = "1", bad: dict[str, str] | None = None) -> RemoteObject:
    metadata = dict(IDENTITY)
    if bad:
        metadata.update(bad)
    size = 167_000_688 if name.endswith(".pmtiles") else 1024
    sha = SHA if name.endswith(".pmtiles") else ""
    return RemoteObject(name=name, size_bytes=size, sha256=sha, metadata=metadata, generation=generation)


def expect(state: PublicationState, receipt, pmtiles, manifest) -> None:
    got = decide_recovery(identity=IDENTITY, receipt=receipt, pmtiles=pmtiles, manifest=manifest)
    assert got.state is state, got


def main() -> None:
    receipt = obj(NAMES["receipt"], generation="11")
    pmtiles = obj(NAMES["pmtiles"], generation="22")
    manifest = obj(NAMES["manifest"], generation="33")

    expect(PublicationState.BUILD_REQUIRED, None, None, None)
    expect(PublicationState.BUILD_REQUIRED, receipt, None, None)
    expect(PublicationState.RECONCILE_MANIFEST_ONLY, receipt, pmtiles, None)
    expect(PublicationState.COMPLETE_REUSED, receipt, pmtiles, manifest)
    expect(PublicationState.FAIL_CLOSED, None, pmtiles, None)
    expect(PublicationState.FAIL_CLOSED, None, None, manifest)

    bad_pmtiles = obj(NAMES["pmtiles"], bad={"source_fingerprint": "c" * 64})
    expect(PublicationState.FAIL_CLOSED, receipt, bad_pmtiles, None)

    rebuilt = build_commit_manifest(
        identity=IDENTITY,
        receipt_object=receipt,
        pmtiles_object=pmtiles,
    )
    assert rebuilt["status"] == "committed"
    assert rebuilt["pmtiles"]["sha256"] == SHA
    assert rebuilt["active_json_updated"] is False

    first = batch_job_id("ES", 123456789, 1)
    second = batch_job_id("ES", 123456789, 2)
    other = batch_job_id("MG", 123456789, 2)
    assert first != second
    assert second != other
    assert len(first) <= 63 and len(second) <= 63 and len(other) <= 63

    print("RX_V48_UF_RECOVERY_SELFTEST=PASS")
    print("RX_V48_PMTILES_WITHOUT_MANIFEST=RECONCILE_ONLY")
    print("RX_V48_BATCH_JOB_PER_UF=PASS")
    print("RX_V48_ACTIVE_JSON=UNTOUCHED")


if __name__ == "__main__":
    main()

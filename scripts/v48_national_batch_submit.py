from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT = "metodo-afp-plataforma"
REGION = "us-central1"
RUNTIME_SA = f"rx-v48-vector-worker@{PROJECT}.iam.gserviceaccount.com"
BUCKET = "raio-x-territorial-car-metodo-afp-plataforma"
CANONICAL_FP = "25e14900fd0ea92d3ff82cb6f46da24449fb2b3bd233aff215ec8a2b645b64a4"
CANONICAL_MANIFEST_REL = f"car/manifests/sicar-canonical-snapshots-v1-{CANONICAL_FP}.json"
WORKER_REL = "scripts/v48_national_worker.py"
CANONICAL_HELPER_REL = "sicar_canonical_manifest_v48.py"
RECOVERY_REL = "scripts/v48_national_recovery_contract.py"
TIPPECANOE_VERSION = "2.79.0"
TIPPECANOE_SHA256 = "b0fd9df49b6efc988288ea48774822c6de19eb48428017f27ee0b3b01d44f05d"
TIPPECANOE_URL = f"https://github.com/felt/tippecanoe/archive/refs/tags/{TIPPECANOE_VERSION}.tar.gz"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "DELETION_IN_PROGRESS"}
POLL_SECONDS = 20
MAX_WAIT_SECONDS = 15_000
OUT_ROOT = REPO_ROOT / "artifacts" / "national"
PUBLICATION_ROOT = f"car/national/{CANONICAL_FP}"


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def die(message: str) -> None:
    raise RuntimeError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_payload(relative: str) -> tuple[str, str]:
    data = (REPO_ROOT / relative).read_bytes()
    return sha256_bytes(data), base64.b64encode(data).decode("ascii")


def request_json(session: Any, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
    response = session.request(method, url, timeout=90, **kwargs)
    try:
        body = response.json()
    except Exception:
        body = {"text": (response.text or "")[:5000]}
    return response.status_code, body


def list_objects(session: Any, prefix: str) -> list[dict[str, Any]]:
    url = f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}/o"
    token = ""
    items: list[dict[str, Any]] = []
    while True:
        params = {"prefix": prefix, "fields": "items(name,size,generation,metadata),nextPageToken"}
        if token:
            params["pageToken"] = token
        status, body = request_json(session, "GET", url, params=params)
        if status != 200:
            die(f"gcs_list_http_{status}:{prefix}:{str(body)[:1200]}")
        items.extend(body.get("items") or [])
        token = str(body.get("nextPageToken") or "")
        if not token:
            return items


def download_json(session: Any, object_name: str) -> dict[str, Any]:
    url = f"https://storage.googleapis.com/download/storage/v1/b/{quote(BUCKET, safe='')}/o/{quote(object_name, safe='')}"
    response = session.get(url, params={"alt": "media"}, timeout=90)
    if response.status_code != 200:
        die(f"gcs_download_http_{response.status_code}:{object_name}:{response.text[:1000]}")
    return response.json()


def canonical_context(uf: str) -> tuple[str, int]:
    import sicar_canonical_manifest_v48 as canonical

    manifest = canonical.load_manifest()
    if manifest.get("content_fingerprint_sha256") != CANONICAL_FP:
        die("canonical_manifest_fingerprint_mismatch")
    entry = canonical.canonical_entry(uf)
    if not entry:
        die(f"canonical_snapshot_unavailable:{uf}")
    snapshot = str(entry.get("snapshot") or "")
    expected_rows = int(((entry.get("tables") or {}).get("area_imovel") or {}).get("row_count") or 0)
    if expected_rows <= 0:
        die(f"canonical_area_imovel_count_missing:{uf}")
    return snapshot, expected_rows


def validate_pmtiles_object(obj: dict[str, Any], receipt: dict[str, Any]) -> None:
    meta = {str(k): str(v) for k, v in (obj.get("metadata") or {}).items()}
    expected = {
        "canonical_manifest_fingerprint": CANONICAL_FP,
        "uf": receipt["uf"],
        "snapshot_date": receipt["snapshot_date"],
        "analysis_snapshot": receipt["snapshot_date"],
        "map_snapshot": receipt["snapshot_date"],
        "source_fingerprint": receipt["source_fingerprint_sha256"],
        "pmtiles_sha256": receipt["pmtiles"]["sha256"],
        "pmtiles_size_bytes": str(receipt["pmtiles"]["size_bytes"]),
        "sha256": receipt["pmtiles"]["sha256"],
    }
    for key, value in expected.items():
        if meta.get(key) != str(value):
            die(f"pmtiles_metadata_mismatch:{key}:{meta.get(key)}!={value}")
    if int(obj.get("size") or -1) != int(receipt["pmtiles"]["size_bytes"]):
        die("pmtiles_size_mismatch")


def validate_commit(commit: dict[str, Any], *, uf: str, snapshot: str, expected_rows: int) -> None:
    if commit.get("schema_version") != "v48-national-publication-commit-1" or commit.get("status") != "committed":
        die("commit_schema_or_status_invalid")
    if commit.get("canonical_manifest_fingerprint") != CANONICAL_FP:
        die("commit_manifest_fingerprint_mismatch")
    if commit.get("uf") != uf or commit.get("snapshot_date") != snapshot:
        die("commit_uf_snapshot_mismatch")
    if commit.get("analysis_snapshot") != snapshot or commit.get("map_snapshot") != snapshot:
        die("commit_map_analysis_divergence")
    if int(commit.get("source_row_count_expected") or 0) != expected_rows:
        die("commit_expected_row_count_mismatch")
    if int(commit.get("source_row_count_actual") or -1) != expected_rows:
        die("commit_source_row_count_drift")
    if commit.get("active_json_updated") is not False:
        die("active_json_mutation_detected")


def publication_preflight(session: Any, *, uf: str, snapshot: str, expected_rows: int) -> dict[str, Any]:
    from scripts import v48_national_recovery_contract as recovery

    receipt_prefix = f"{PUBLICATION_ROOT}/receipts/{uf}/"
    pmtiles_prefix = f"{PUBLICATION_ROOT}/uf/{uf}/"
    manifest_prefix = f"{PUBLICATION_ROOT}/manifests/uf/{uf}/"
    receipts = [x for x in list_objects(session, receipt_prefix) if str(x.get("name") or "").endswith(".json")]
    pmtiles = [x for x in list_objects(session, pmtiles_prefix) if str(x.get("name") or "").endswith(".pmtiles")]
    manifests = [x for x in list_objects(session, manifest_prefix) if str(x.get("name") or "").endswith(".json")]
    if len(receipts) > 1 or len(pmtiles) > 1 or len(manifests) > 1:
        die(f"ambiguous_immutable_objects:r{len(receipts)}:p{len(pmtiles)}:m{len(manifests)}")

    if manifests:
        if not receipts or not pmtiles:
            die("commit_marker_without_prepared_objects")
        commit = download_json(session, str(manifests[0]["name"]))
        validate_commit(commit, uf=uf, snapshot=snapshot, expected_rows=expected_rows)
        receipt = download_json(session, str(receipts[0]["name"]))
        recovery.validate_receipt(receipt, canonical_manifest_fingerprint=CANONICAL_FP, uf=uf, snapshot_date=snapshot)
        validate_pmtiles_object(pmtiles[0], receipt)
        if str(commit.get("source_fingerprint_sha256")) != str(receipt.get("source_fingerprint_sha256")):
            die("commit_receipt_fingerprint_mismatch")
        return {"state": "COMPLETE_REUSED", "commit": commit, "batch_required": False}

    if pmtiles and not receipts:
        die("orphan_pmtiles_without_receipt_cannot_be_reconciled")

    if receipts and pmtiles:
        receipt = download_json(session, str(receipts[0]["name"]))
        recovery.validate_receipt(receipt, canonical_manifest_fingerprint=CANONICAL_FP, uf=uf, snapshot_date=snapshot)
        if int(receipt["source_row_count_expected"]) != expected_rows:
            die("receipt_expected_row_count_mismatch")
        validate_pmtiles_object(pmtiles[0], receipt)
        commit = recovery.build_recovery_commit(
            receipt=receipt,
            receipt_object=str(receipts[0]["name"]),
            receipt_generation=str(receipts[0].get("generation") or ""),
            pmtiles_object=str(pmtiles[0]["name"]),
            pmtiles_generation=str(pmtiles[0].get("generation") or ""),
        )
        return {
            "state": "RECONCILE_MANIFEST_ONLY",
            "commit": commit,
            "commit_object": f"{manifest_prefix}{receipt['source_fingerprint_sha256']}.json",
            "metadata": {
                "canonical_manifest_fingerprint": CANONICAL_FP,
                "uf": uf,
                "snapshot_date": snapshot,
                "analysis_snapshot": snapshot,
                "map_snapshot": snapshot,
                "source_fingerprint": receipt["source_fingerprint_sha256"],
                "pmtiles_sha256": receipt["pmtiles"]["sha256"],
                "pmtiles_size_bytes": str(receipt["pmtiles"]["size_bytes"]),
            },
            "batch_required": False,
        }

    if receipts and not pmtiles:
        receipt = download_json(session, str(receipts[0]["name"]))
        recovery.validate_receipt(receipt, canonical_manifest_fingerprint=CANONICAL_FP, uf=uf, snapshot_date=snapshot)
        if int(receipt["source_row_count_expected"]) != expected_rows:
            die("receipt_expected_row_count_mismatch")
        return {"state": "BUILD_REQUIRED", "expected_source_fingerprint": receipt["source_fingerprint_sha256"], "batch_required": True}

    return {"state": "BUILD_REQUIRED", "expected_source_fingerprint": "", "batch_required": True}


def reconcile_manifest(preflight: dict[str, Any]) -> dict[str, Any]:
    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    blob = bucket.blob(preflight["commit_object"])
    raw = json.dumps(preflight["commit"], ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    blob.metadata = {**preflight["metadata"], "sha256": digest}
    try:
        blob.upload_from_string(raw, content_type="application/json", if_generation_match=0, timeout=120)
        disposition = "created"
    except PreconditionFailed:
        die("recovery_manifest_conflict")
    blob.reload()
    result = dict(preflight["commit"])
    result["reconciliation_upload"] = {"object": blob.name, "generation": str(blob.generation or ""), "sha256": digest, "disposition": disposition}
    return result


def job_id(uf: str, run_id: str, run_attempt: str) -> str:
    rid = re.sub(r"\D", "", run_id)
    attempt = re.sub(r"\D", "", run_attempt)
    if not rid or not attempt:
        die("github_run_identity_missing")
    value = f"rx-v48-{uf.lower()}-{CANONICAL_FP[:8]}-r{rid}-a{attempt}"
    if len(value) > 63 or not re.fullmatch(r"[a-z][a-z0-9-]*", value):
        die(f"invalid_batch_job_id:{value}")
    return value


def build_task_script(*, uf: str, snapshot: str, expected_source_fingerprint: str) -> str:
    worker_sha, worker_b64 = file_payload(WORKER_REL)
    helper_sha, helper_b64 = file_payload(CANONICAL_HELPER_REL)
    manifest_sha, manifest_b64 = file_payload(CANONICAL_MANIFEST_REL)
    expectation = expected_source_fingerprint or ""
    return f"""#!/usr/bin/env bash
set -euo pipefail
export PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
export DEBIAN_FRONTEND=noninteractive
SECONDS=0
if [ \"$(id -u)\" -eq 0 ]; then SUDO=\"\"; elif command -v sudo >/dev/null 2>&1; then SUDO='sudo -n'; else exit 20; fi
S=$SECONDS; $SUDO apt-get update -y; T_APT_UPDATE=$((SECONDS-S))
S=$SECONDS; $SUDO apt-get install -y --no-install-recommends build-essential gcc g++ coreutils ca-certificates curl libsqlite3-dev python3 python3-pip python3-venv zlib1g-dev; T_APT_INSTALL=$((SECONDS-S))
cd /tmp
S=$SECONDS; curl --fail --location --retry 3 --connect-timeout 20 --output tippecanoe.tar.gz '{TIPPECANOE_URL}'; T_TIPPECANOE_DOWNLOAD=$((SECONDS-S))
echo '{TIPPECANOE_SHA256}  tippecanoe.tar.gz' | sha256sum -c -
tar -xzf tippecanoe.tar.gz
cd 'tippecanoe-{TIPPECANOE_VERSION}'
S=$SECONDS; make -j8; T_TIPPECANOE_BUILD=$((SECONDS-S))
$SUDO make install PREFIX=/usr/local
S=$SECONDS; python3 -m venv /tmp/rxv48-venv; /tmp/rxv48-venv/bin/pip install --disable-pip-version-check --no-cache-dir 'google-cloud-bigquery==3.45.0' 'google-cloud-storage==3.13.1' 'shapely==2.1.2' 'psutil==7.2.2'; T_PYTHON_ENV=$((SECONDS-S))
cat > /tmp/worker.b64 <<'RX_WORKER'
{worker_b64}
RX_WORKER
base64 --decode /tmp/worker.b64 > /tmp/v48_national_worker.py
echo '{worker_sha}  /tmp/v48_national_worker.py' | sha256sum -c -
cat > /tmp/helper.b64 <<'RX_HELPER'
{helper_b64}
RX_HELPER
base64 --decode /tmp/helper.b64 > /tmp/sicar_canonical_manifest_v48.py
echo '{helper_sha}  /tmp/sicar_canonical_manifest_v48.py' | sha256sum -c -
cat > /tmp/manifest.b64 <<'RX_MANIFEST'
{manifest_b64}
RX_MANIFEST
base64 --decode /tmp/manifest.b64 > /tmp/sicar-canonical-snapshots-v1-{CANONICAL_FP}.json
echo '{manifest_sha}  /tmp/sicar-canonical-snapshots-v1-{CANONICAL_FP}.json' | sha256sum -c -
rm -f /tmp/*.b64
export GCP_PROJECT_ID='{PROJECT}' GOOGLE_CLOUD_PROJECT='{PROJECT}' RX_V48_UF='{uf}' RX_V48_SNAPSHOT='{snapshot}' RX_V48_GCS_BUCKET='{BUCKET}' RX_V48_WORKDIR='/mnt/disks/rxv48'
export RX_SICAR_CANONICAL_MANIFEST='/tmp/sicar-canonical-snapshots-v1-{CANONICAL_FP}.json'
export RX_V48_EXPECT_SOURCE_FINGERPRINT='{expectation}'
mkdir -p /mnt/disks/rxv48
printf 'RX_V48_BATCH_UF={uf} RX_V48_BATCH_SNAPSHOT={snapshot} RX_V48_BATCH_REGION={REGION} RX_V48_BATCH_MACHINE=e2-standard-8 RX_V48_BATCH_PROVISIONING=SPOT RX_V48_BATCH_RETRY_COUNT=0\\n'
S=$SECONDS; cd /tmp; /tmp/rxv48-venv/bin/python /tmp/v48_national_worker.py; T_WORKER=$((SECONDS-S))
printf 'RX_V48_BATCH_BOOTSTRAP_METRICS=apt_update_s=%s apt_install_s=%s tippecanoe_download_s=%s tippecanoe_build_s=%s python_env_s=%s worker_s=%s total_s=%s\\n' \"$T_APT_UPDATE\" \"$T_APT_INSTALL\" \"$T_TIPPECANOE_DOWNLOAD\" \"$T_TIPPECANOE_BUILD\" \"$T_PYTHON_ENV\" \"$T_WORKER\" \"$SECONDS\"
echo 'RX_V48_NATIONAL_BATCH_RUNNABLE=PASS'
"""


def build_job(*, uf: str, snapshot: str, expected_source_fingerprint: str) -> dict[str, Any]:
    script_text = build_task_script(uf=uf, snapshot=snapshot, expected_source_fingerprint=expected_source_fingerprint)
    return {
        "taskGroups": [{
            "taskCount": "1", "parallelism": "1",
            "taskSpec": {
                "runnables": [{"script": {"text": script_text}}],
                "computeResource": {"cpuMilli": "8000", "memoryMib": "30000"},
                "maxRetryCount": 0,
                "maxRunDuration": "14400s",
                "volumes": [{"deviceName": "rxv48", "mountPath": "/mnt/disks/rxv48", "mountOptions": ["rw", "async"]}],
            },
        }],
        "allocationPolicy": {
            "instances": [{"policy": {"machineType": "e2-standard-8", "provisioningModel": "SPOT", "disks": [{"newDisk": {"type": "pd-balanced", "sizeGb": "100"}, "deviceName": "rxv48"}]}}],
            "serviceAccount": {"email": RUNTIME_SA, "scopes": ["https://www.googleapis.com/auth/cloud-platform"]},
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
        "labels": {"rx-phase": "v48-national-car", "rx-uf": uf.lower(), "rx-canonical": CANONICAL_FP[:12], "rx-retry": "zero"},
    }


def batch_url(jid: str) -> str:
    return f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs/{jid}"


def create_or_reuse_batch(session: Any, *, jid: str, payload: dict[str, Any]) -> str:
    status, body = request_json(session, "GET", batch_url(jid))
    if status == 200:
        return "existing"
    if status != 404:
        die(f"batch_precheck_http_{status}:{str(body)[:1500]}")
    endpoint = f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs"
    status, body = request_json(session, "POST", endpoint, params={"jobId": jid}, json=payload)
    if status not in {200, 201}:
        die(f"batch_create_http_{status}:{str(body)[:2000]}")
    return "created"


def poll_batch(session: Any, jid: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    started = time.monotonic()
    observations: list[dict[str, str]] = []
    last = ""
    while True:
        status, body = request_json(session, "GET", batch_url(jid))
        if status != 200:
            die(f"batch_poll_http_{status}:{str(body)[:1500]}")
        state = str(((body or {}).get("status") or {}).get("state") or "UNKNOWN")
        if state != last:
            observations.append({"observed_at": utcnow(), "state": state})
            print(f"RX_V48_NATIONAL_BATCH_STATE={state}")
            last = state
        if state in TERMINAL_STATES:
            return body, observations
        if time.monotonic() - started > MAX_WAIT_SECONDS:
            die("batch_observation_timeout")
        time.sleep(POLL_SECONDS)


def terminal_detail(job: dict[str, Any]) -> dict[str, Any]:
    status = job.get("status") or {}
    return {
        "state": status.get("state"),
        "run_duration": status.get("runDuration"),
        "status_events": status.get("statusEvents") or [],
        "create_time": job.get("createTime"),
        "update_time": job.get("updateTime"),
    }


def fetch_committed_result(session: Any, *, uf: str, snapshot: str, expected_rows: int) -> dict[str, Any]:
    prefix = f"{PUBLICATION_ROOT}/manifests/uf/{uf}/"
    items = [x for x in list_objects(session, prefix) if str(x.get("name") or "").endswith(".json")]
    if len(items) != 1:
        die(f"expected_one_commit_manifest_found_{len(items)}")
    commit = download_json(session, str(items[0]["name"]))
    validate_commit(commit, uf=uf, snapshot=snapshot, expected_rows=expected_rows)
    return commit


def write_out(uf: str, payload: dict[str, Any]) -> Path:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = OUT_ROOT / f"{uf}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def execute(uf: str) -> dict[str, Any]:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    snapshot, expected_rows = canonical_context(uf)
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    preflight = publication_preflight(session, uf=uf, snapshot=snapshot, expected_rows=expected_rows)
    if preflight["state"] == "COMPLETE_REUSED":
        return {"schema_version": "v48-national-uf-result-1", "status": "success", "uf": uf, "snapshot": snapshot, "publication_state": "COMPLETE_REUSED", "batch": None, "commit": preflight["commit"]}
    if preflight["state"] == "RECONCILE_MANIFEST_ONLY":
        commit = reconcile_manifest(preflight)
        validate_commit(commit, uf=uf, snapshot=snapshot, expected_rows=expected_rows)
        return {"schema_version": "v48-national-uf-result-1", "status": "success", "uf": uf, "snapshot": snapshot, "publication_state": "RECONCILE_MANIFEST_ONLY", "batch": None, "commit": commit}

    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    jid = job_id(uf, run_id, run_attempt)
    payload = build_job(uf=uf, snapshot=snapshot, expected_source_fingerprint=str(preflight.get("expected_source_fingerprint") or ""))
    disposition = create_or_reuse_batch(session, jid=jid, payload=payload)
    job, observations = poll_batch(session, jid)
    detail = terminal_detail(job)
    batch = {"job_id": jid, "region": REGION, "disposition": disposition, "observations": observations, **detail}
    if detail["state"] != "SUCCEEDED":
        return {"schema_version": "v48-national-uf-result-1", "status": "failed", "uf": uf, "snapshot": snapshot, "publication_state": "BUILD_FAILED", "batch": batch, "commit": None}
    commit = fetch_committed_result(session, uf=uf, snapshot=snapshot, expected_rows=expected_rows)
    return {"schema_version": "v48-national-uf-result-1", "status": "success", "uf": uf, "snapshot": snapshot, "publication_state": "COMMITTED", "batch": batch, "commit": commit}


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uf")
    parser.add_argument("--import-check", action="store_true")
    args = parser.parse_args()
    if args.import_check:
        import sicar_canonical_manifest_v48  # noqa: F401
        from scripts import v48_national_recovery_contract  # noqa: F401
        for relative in (WORKER_REL, CANONICAL_HELPER_REL, CANONICAL_MANIFEST_REL, RECOVERY_REL):
            if not (REPO_ROOT / relative).exists():
                die(f"required_file_missing:{relative}")
        print("RX_V48_NATIONAL_BATCH_SUBMIT_IMPORT_CHECK=PASS")
        return
    uf = str(args.uf or os.getenv("RX_V48_UF") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", uf):
        die(f"invalid_uf:{uf}")
    result: dict[str, Any]
    try:
        result = execute(uf)
    except Exception as exc:
        result = {"schema_version": "v48-national-uf-result-1", "status": "failed", "uf": uf, "error": f"{type(exc).__name__}:{exc}", "recorded_at": utcnow()}
        write_out(uf, result)
        print(f"RX_V48_NATIONAL_SUBMIT=FAIL_CLOSED:{uf}:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise
    write_out(uf, result)
    print(f"RX_V48_NATIONAL_SUBMIT_UF={uf}")
    print(f"RX_V48_NATIONAL_PUBLICATION_STATE={result['publication_state']}")
    print(f"RX_V48_NATIONAL_SUBMIT_STATUS={result['status'].upper()}")
    if result["status"] != "success":
        raise RuntimeError(f"uf_generation_failed:{uf}")


if __name__ == "__main__":
    cli()

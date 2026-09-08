from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = "metodo-afp-plataforma"
REGION = "southamerica-east1"
JOB_ID = "rx-v48-es-sicar-20260804-pilot-001"
RUNTIME_SA = f"rx-v48-vector-worker@{PROJECT}.iam.gserviceaccount.com"
BUCKET = "raio-x-territorial-car-metodo-afp-plataforma"
WORKER_PATH = Path("scripts/v48_vector_pilot_worker.py")
PILOT_CONFIG_PATH = Path("config/v48_vector_pilot.json")
OUT_PATH = Path("artifacts/v48_es_batch_result.json")
TIPPECANOE_VERSION = "2.79.0"
TIPPECANOE_SHA256 = "b0fd9df49b6efc988288ea48774822c6de19eb48428017f27ee0b3b01d44f05d"
TIPPECANOE_URL = f"https://github.com/felt/tippecanoe/archive/refs/tags/{TIPPECANOE_VERSION}.tar.gz"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "DELETION_IN_PROGRESS"}
POLL_SECONDS = 15
MAX_WAIT_SECONDS = 4500


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def die(message: str) -> None:
    raise RuntimeError(message)


def request_json(session: AuthorizedSession, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
    response = session.request(method, url, timeout=60, **kwargs)
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text[:4000]}
    return response.status_code, body


def assert_frozen_contract() -> dict[str, Any]:
    config = json.loads(PILOT_CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("pilot_uf") != "ES":
        die("pilot_uf_not_es")
    if config.get("snapshot_date") != "2026-08-04":
        die("snapshot_not_2026_08_04")
    if config.get("analysis_snapshot") != "2026-08-04" or config.get("map_snapshot") != "2026-08-04":
        die("map_analysis_snapshot_divergence")
    if config.get("national_generation_allowed") is not False:
        die("national_generation_must_remain_false")
    if not WORKER_PATH.exists():
        die("worker_missing")
    return config


def worker_payload() -> tuple[str, str]:
    data = WORKER_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode("ascii")
    return digest, encoded


def build_task_script(worker_sha256: str, worker_b64: str) -> str:
    # No repository credential reaches the VM. The already-reviewed worker is
    # embedded in the Batch request, decoded locally, and hash-verified before
    # execution. All external build inputs are public and Tippecanoe is pinned.
    return f"""#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
if [ \"$(id -u)\" -eq 0 ]; then
  SUDO=\"\"
elif command -v sudo >/dev/null 2>&1; then
  SUDO=\"sudo -n\"
else
  echo 'RX_V48_BATCH_BOOTSTRAP=FAIL_NO_ROOT_OR_SUDO' >&2
  exit 20
fi

$SUDO apt-get update -y
$SUDO apt-get install -y --no-install-recommends \\
  build-essential ca-certificates curl libsqlite3-dev python3 python3-pip python3-venv zlib1g-dev

cd /tmp
curl --fail --location --retry 3 --connect-timeout 20 \\
  --output tippecanoe.tar.gz \\
  '{TIPPECANOE_URL}'
echo '{TIPPECANOE_SHA256}  tippecanoe.tar.gz' | sha256sum -c -
tar -xzf tippecanoe.tar.gz
cd 'tippecanoe-{TIPPECANOE_VERSION}'
make -j8
$SUDO make install PREFIX=/usr/local
/usr/local/bin/tippecanoe --version

python3 -m venv /tmp/rxv48-venv
/tmp/rxv48-venv/bin/pip install --disable-pip-version-check --no-cache-dir \\
  'google-cloud-bigquery==3.45.0' \\
  'google-cloud-storage==3.13.1' \\
  'shapely==2.1.2' \\
  'psutil==7.2.2'

cat > /tmp/v48_worker.py.b64 <<'RX_V48_WORKER_B64'
{worker_b64}
RX_V48_WORKER_B64
base64 --decode /tmp/v48_worker.py.b64 > /tmp/v48_worker.py
echo '{worker_sha256}  /tmp/v48_worker.py' | sha256sum -c -
rm -f /tmp/v48_worker.py.b64

export GCP_PROJECT_ID='{PROJECT}'
export GOOGLE_CLOUD_PROJECT='{PROJECT}'
export RX_V48_PILOT_UF='ES'
export RX_V48_SNAPSHOT='2026-08-04'
export RX_V48_ALLOW_NATIONAL='false'
export RX_V48_GCS_BUCKET='{BUCKET}'
export RX_V48_WORKDIR='/mnt/disks/rxv48'

mkdir -p /mnt/disks/rxv48
printf '%s\\n' \\
  'RX_V48_BATCH_MACHINE=e2-standard-8' \\
  'RX_V48_BATCH_PROVISIONING=SPOT' \\
  'RX_V48_BATCH_REGION={REGION}' \\
  'RX_V48_BATCH_UF=ES' \\
  'RX_V48_BATCH_SNAPSHOT=2026-08-04' \\
  'RX_V48_BATCH_NATIONAL=false' \\
  'RX_V48_BATCH_RETRY_COUNT=0'

exec /tmp/rxv48-venv/bin/python /tmp/v48_worker.py
"""


def build_job(worker_sha256: str, worker_b64: str) -> dict[str, Any]:
    script_text = build_task_script(worker_sha256, worker_b64)
    return {
        "taskGroups": [
            {
                "taskCount": "1",
                "parallelism": "1",
                "taskSpec": {
                    "runnables": [{"script": {"text": script_text}}],
                    "computeResource": {"cpuMilli": "8000", "memoryMib": "30000"},
                    "maxRetryCount": 0,
                    "maxRunDuration": "3600s",
                    "volumes": [
                        {
                            "deviceName": "rxv48",
                            "mountPath": "/mnt/disks/rxv48",
                            "mountOptions": ["rw", "async"],
                        }
                    ],
                },
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": "e2-standard-8",
                        "provisioningModel": "SPOT",
                        "disks": [
                            {
                                "newDisk": {"type": "pd-balanced", "sizeGb": "100"},
                                "deviceName": "rxv48",
                            }
                        ],
                    }
                }
            ],
            "serviceAccount": {
                "email": RUNTIME_SA,
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
            },
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
        "labels": {
            "rx-phase": "v48-vector-pilot",
            "rx-uf": "es",
            "rx-snapshot": "20260804",
            "rx-national": "false",
            "rx-attempts": "one",
        },
    }


def job_url() -> str:
    return f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs/{JOB_ID}"


def create_or_reuse_job(session: AuthorizedSession, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    status, body = request_json(session, "GET", job_url())
    if status == 200:
        print(f"RX_V48_BATCH_JOB_CREATE=SKIPPED_EXISTING:{JOB_ID}")
        return False, body
    if status != 404:
        die(f"batch_job_precheck_http_{status}:{str(body)[:800]}")

    endpoint = f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs"
    status, body = request_json(session, "POST", endpoint, params={"jobId": JOB_ID}, json=payload)
    if status not in {200, 201}:
        die(f"batch_job_create_http_{status}:{str(body)[:2000]}")
    print(f"RX_V48_BATCH_JOB_CREATE=CREATED:{JOB_ID}")
    return True, body


def poll_job(session: AuthorizedSession) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    last_state = ""
    while True:
        status, body = request_json(session, "GET", job_url())
        if status != 200:
            die(f"batch_job_poll_http_{status}:{str(body)[:1200]}")
        state = str(((body or {}).get("status") or {}).get("state") or "UNKNOWN")
        if state != last_state:
            event = {"observed_at": utcnow(), "state": state}
            observations.append(event)
            print(f"RX_V48_BATCH_STATE={state}")
            last_state = state
        if state in TERMINAL_STATES:
            return body, observations
        if time.monotonic() - started > MAX_WAIT_SECONDS:
            die("batch_job_observation_timeout_no_second_job_created")
        time.sleep(POLL_SECONDS)


def fetch_pilot_manifest(session: AuthorizedSession) -> tuple[str, dict[str, Any]]:
    prefix = "car/sicar-2026-08-04/manifests/pilot/ES/"
    list_url = f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}/o"
    status, body = request_json(
        session,
        "GET",
        list_url,
        params={"prefix": prefix, "fields": "items(name,size,generation,metadata),nextPageToken"},
    )
    if status != 200:
        die(f"manifest_list_http_{status}:{str(body)[:1200]}")
    items = [item for item in (body.get("items") or []) if str(item.get("name") or "").endswith(".json")]
    if len(items) != 1:
        die(f"expected_exactly_one_pilot_manifest_found_{len(items)}")
    name = str(items[0]["name"])
    media_url = (
        f"https://storage.googleapis.com/download/storage/v1/b/{quote(BUCKET, safe='')}/o/"
        f"{quote(name, safe='')}"
    )
    response = session.get(media_url, params={"alt": "media"}, timeout=60)
    if response.status_code != 200:
        die(f"manifest_download_http_{response.status_code}:{response.text[:800]}")
    payload = response.json()
    return name, payload


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = assert_frozen_contract()
    worker_sha256, worker_b64 = worker_payload()
    payload = build_job(worker_sha256, worker_b64)

    # Hard safety assertions over the exact payload sent to Cloud Batch.
    policy = payload["allocationPolicy"]["instances"][0]["policy"]
    task_spec = payload["taskGroups"][0]["taskSpec"]
    if policy.get("machineType") != "e2-standard-8":
        die("machine_type_changed")
    if policy.get("provisioningModel") != "SPOT":
        die("spot_required")
    if task_spec.get("maxRetryCount") != 0:
        die("automatic_retry_forbidden_for_single_attempt_pilot")
    if payload["taskGroups"][0].get("taskCount") != "1" or payload["taskGroups"][0].get("parallelism") != "1":
        die("single_task_contract_changed")
    if payload["allocationPolicy"]["serviceAccount"].get("email") != RUNTIME_SA:
        die("runtime_service_account_changed")
    if config.get("national_generation_allowed") is not False:
        die("national_generation_not_blocked")

    credentials, detected_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if detected_project and detected_project != PROJECT:
        die(f"credential_project_mismatch:{detected_project}")
    session = AuthorizedSession(credentials)

    workflow_started_at = utcnow()
    created, initial = create_or_reuse_job(session, payload)
    final_job, observations = poll_job(session)
    state = str(((final_job or {}).get("status") or {}).get("state") or "UNKNOWN")

    result: dict[str, Any] = {
        "schema_version": "v48-es-batch-result-1",
        "project": PROJECT,
        "region": REGION,
        "job_id": JOB_ID,
        "job_created_by_this_run": created,
        "worker_sha256": worker_sha256,
        "pilot_uf": "ES",
        "snapshot": "2026-08-04",
        "national_generation_allowed": False,
        "provisioning_model": "SPOT",
        "machine_type": "e2-standard-8",
        "persistent_disk": {"type": "pd-balanced", "size_gb": 100},
        "max_retry_count": 0,
        "workflow_started_at": workflow_started_at,
        "workflow_finished_at": utcnow(),
        "observations": observations,
        "batch_job": final_job,
        "initial_batch_job": initial,
    }

    if state == "SUCCEEDED":
        manifest_name, manifest = fetch_pilot_manifest(session)
        result["pilot_manifest_object"] = manifest_name
        result["pilot_manifest"] = manifest
        print(f"RX_V48_BATCH_RESULT=SUCCEEDED:{JOB_ID}")
        print(f"RX_V48_BATCH_MANIFEST={manifest_name}")
        print(f"RX_V48_BATCH_PMTILES_BYTES={manifest.get('pmtiles', {}).get('size_bytes')}")
        print(f"RX_V48_BATCH_WORKER_SECONDS={manifest.get('total_elapsed_seconds')}")
    else:
        print(f"RX_V48_BATCH_RESULT={state}:{JOB_ID}", file=sys.stderr)

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if state != "SUCCEEDED":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not OUT_PATH.exists():
            OUT_PATH.write_text(
                json.dumps({
                    "schema_version": "v48-es-batch-result-1",
                    "status": "fail_closed_before_or_during_batch",
                    "job_id": JOB_ID,
                    "error": f"{type(exc).__name__}:{exc}",
                    "national_generation_allowed": False,
                    "recorded_at": utcnow(),
                }, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        print(f"RX_V48_ES_BATCH_SUBMITTER=FAIL_CLOSED:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise

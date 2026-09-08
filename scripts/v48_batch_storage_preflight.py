from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = "metodo-afp-plataforma"
REGION = "southamerica-east1"
RUNTIME_SA = os.getenv(
    "RX_V48_BATCH_RUNTIME_SA",
    f"rx-v48-vector-worker@{PROJECT}.iam.gserviceaccount.com",
).strip()
BUCKET = os.getenv(
    "RX_V48_GCS_BUCKET",
    "raio-x-territorial-car-metodo-afp-plataforma",
).strip()
REQUIRED_APIS = (
    "batch.googleapis.com",
    "compute.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
)
PROJECT_PERMISSIONS = (
    "batch.jobs.create",
    "batch.jobs.get",
    "batch.jobs.list",
    "batch.jobs.delete",
    "serviceusage.services.use",
    # Deliberately observed but not required for the orchestrator. Batch's
    # service agent provisions Compute resources; the GitHub caller must not
    # receive direct VM/disk creation power merely to submit a Batch job.
    "compute.instances.create",
    "compute.disks.create",
)
BUCKET_PERMISSIONS = (
    "storage.objects.create",
    "storage.objects.get",
    "storage.objects.list",
    "storage.objects.delete",
)


def request_json(session: AuthorizedSession, method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    response = session.request(method, url, timeout=30, **kwargs)
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text[:500]}
    return response.status_code, body if isinstance(body, dict) else {"value": body}


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        raise AssertionError(f"unexpected project:{project or '<missing>'}")

    credentials, detected_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if detected_project and detected_project != PROJECT:
        raise AssertionError(f"credential project mismatch:{detected_project}")
    caller_email = str(getattr(credentials, "service_account_email", "") or "")
    session = AuthorizedSession(credentials)
    print(f"RX_V48_PREFLIGHT_CALLER_EMAIL={caller_email or 'UNKNOWN'}")
    print(f"RX_V48_PREFLIGHT_TARGET_REGION={REGION}")
    print(f"RX_V48_PREFLIGHT_TARGET_RUNTIME_SA={RUNTIME_SA}")
    print(f"RX_V48_PREFLIGHT_TARGET_BUCKET={BUCKET}")

    project_http, project_meta = request_json(
        session, "GET", f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}"
    )
    project_number = str(project_meta.get("projectNumber") or "")
    print(f"RX_V48_PREFLIGHT_PROJECT_HTTP={project_http}")
    print(f"RX_V48_PREFLIGHT_PROJECT_NUMBER_RESOLVED={'TRUE' if project_number else 'FALSE'}")
    if project_http != 200 or not project_number:
        raise AssertionError("project metadata unavailable")

    iam_http, iam_body = request_json(
        session,
        "POST",
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions",
        json={"permissions": list(PROJECT_PERMISSIONS)},
    )
    granted = set(iam_body.get("permissions") or []) if iam_http == 200 else set()
    print(f"RX_V48_PREFLIGHT_PROJECT_IAM_HTTP={iam_http}")
    for permission in PROJECT_PERMISSIONS:
        key = permission.upper().replace(".", "_")
        print(f"RX_V48_PREFLIGHT_PERMISSION_{key}={'TRUE' if permission in granted else 'FALSE'}")

    api_results: dict[str, Any] = {}
    for service in REQUIRED_APIS:
        service_http, service_body = request_json(
            session,
            "GET",
            f"https://serviceusage.googleapis.com/v1/projects/{project_number}/services/{service}",
        )
        state = str(service_body.get("state") or "UNKNOWN")
        api_results[service] = {"http": service_http, "state": state}
        safe_name = service.split(".", 1)[0].upper()
        print(f"RX_V48_PREFLIGHT_API_{safe_name}_HTTP={service_http}")
        print(f"RX_V48_PREFLIGHT_API_{safe_name}_STATE={state}")

    bucket_http, bucket_meta = request_json(
        session,
        "GET",
        f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}",
        params={"fields": "name,location,storageClass,iamConfiguration"},
    )
    bucket_location = str(bucket_meta.get("location") or "") if bucket_http == 200 else ""
    print(f"RX_V48_PREFLIGHT_BUCKET_GET_HTTP={bucket_http}")
    print(f"RX_V48_PREFLIGHT_BUCKET_LOCATION={bucket_location or 'UNKNOWN'}")

    bucket_iam_http, bucket_iam_body = request_json(
        session,
        "GET",
        f"https://storage.googleapis.com/storage/v1/b/{quote(BUCKET, safe='')}/iam/testPermissions",
        params=[("permissions", p) for p in BUCKET_PERMISSIONS],
    )
    bucket_granted = set(bucket_iam_body.get("permissions") or []) if bucket_iam_http == 200 else set()
    print(f"RX_V48_PREFLIGHT_BUCKET_IAM_HTTP={bucket_iam_http}")
    for permission in BUCKET_PERMISSIONS:
        key = permission.upper().replace(".", "_")
        print(f"RX_V48_PREFLIGHT_BUCKET_PERMISSION_{key}={'TRUE' if permission in bucket_granted else 'FALSE'}")

    batch_http, _batch_body = request_json(
        session,
        "GET",
        f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs",
        params={"pageSize": 1},
    )
    print(f"RX_V48_PREFLIGHT_BATCH_LIST_HTTP={batch_http}")

    sa_http, sa_body = request_json(
        session,
        "POST",
        f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{quote(RUNTIME_SA, safe='')}:testIamPermissions",
        json={"permissions": ["iam.serviceAccounts.actAs"]},
    )
    sa_permissions = set(sa_body.get("permissions") or []) if sa_http == 200 else set()
    can_act_as_runtime = "iam.serviceAccounts.actAs" in sa_permissions
    print(f"RX_V48_PREFLIGHT_RUNTIME_SA_TEST_HTTP={sa_http}")
    print(f"RX_V48_PREFLIGHT_RUNTIME_SA_ACTAS={'TRUE' if can_act_as_runtime else 'FALSE'}")

    hard_project = {
        "batch.jobs.create",
        "batch.jobs.get",
        "batch.jobs.list",
        "batch.jobs.delete",
        "serviceusage.services.use",
    }
    hard_bucket = set(BUCKET_PERMISSIONS)
    missing_project = sorted(hard_project - granted)
    missing_bucket = sorted(hard_bucket - bucket_granted)
    api_not_enabled_or_unreadable = sorted(
        name for name, result in api_results.items()
        if result.get("http") != 200 or result.get("state") != "ENABLED"
    )
    location_ok = bucket_location.upper() == REGION.upper()

    result = {
        "schema_version": "v48-batch-storage-preflight-2",
        "project": PROJECT,
        "project_number": project_number,
        "caller_email": caller_email or None,
        "region": REGION,
        "runtime_service_account": RUNTIME_SA,
        "bucket": BUCKET,
        "project_permissions": {p: p in granted for p in PROJECT_PERMISSIONS},
        "required_apis": api_results,
        "bucket_get_http": bucket_http,
        "bucket_location": bucket_location or None,
        "bucket_location_matches_batch_region": location_ok,
        "bucket_permissions": {p: p in bucket_granted for p in BUCKET_PERMISSIONS},
        "batch_list_http": batch_http,
        "runtime_service_account_test_http": sa_http,
        "can_act_as_runtime_service_account": can_act_as_runtime,
        "missing_hard_project_permissions": missing_project,
        "missing_hard_bucket_permissions": missing_bucket,
        "api_not_enabled_or_unreadable": api_not_enabled_or_unreadable,
        "mutation_performed": False,
    }
    out = Path("artifacts/v48_batch_storage_preflight.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    ready = (
        not missing_project
        and not missing_bucket
        and not api_not_enabled_or_unreadable
        and batch_http == 200
        and can_act_as_runtime
        and bucket_http == 200
        and location_ok
    )
    print("RX_V48_PREFLIGHT_MUTATION_PERFORMED=FALSE")
    print(f"RX_V48_BATCH_STORAGE_PREFLIGHT={'PASS' if ready else 'FAIL_CLOSED'}")
    if not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

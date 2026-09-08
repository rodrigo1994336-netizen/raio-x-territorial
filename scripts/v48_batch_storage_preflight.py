from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = "metodo-afp-plataforma"
REGION = "us-central1"
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
    "storage.buckets.create",
    "storage.buckets.list",
    "serviceusage.services.use",
    "compute.instances.create",
    "compute.disks.create",
    "compute.networks.use",
    "compute.subnetworks.use",
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
    session = AuthorizedSession(credentials)

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

    storage_http, storage_body = request_json(
        session,
        "GET",
        "https://storage.googleapis.com/storage/v1/b",
        params={"project": PROJECT, "maxResults": 10, "fields": "items(name,location,storageClass),nextPageToken"},
    )
    bucket_items = storage_body.get("items") or [] if storage_http == 200 else []
    print(f"RX_V48_PREFLIGHT_STORAGE_LIST_HTTP={storage_http}")
    print(f"RX_V48_PREFLIGHT_STORAGE_VISIBLE_BUCKETS={len(bucket_items)}")

    batch_http, batch_body = request_json(
        session,
        "GET",
        f"https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs",
        params={"pageSize": 1},
    )
    print(f"RX_V48_PREFLIGHT_BATCH_LIST_HTTP={batch_http}")

    default_compute_sa = f"{project_number}-compute@developer.gserviceaccount.com"
    sa_http, sa_body = request_json(
        session,
        "POST",
        f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{default_compute_sa}:testIamPermissions",
        json={"permissions": ["iam.serviceAccounts.actAs"]},
    )
    sa_permissions = set(sa_body.get("permissions") or []) if sa_http == 200 else set()
    can_act_as_default = "iam.serviceAccounts.actAs" in sa_permissions
    print(f"RX_V48_PREFLIGHT_DEFAULT_COMPUTE_SA_TEST_HTTP={sa_http}")
    print(f"RX_V48_PREFLIGHT_DEFAULT_COMPUTE_SA_ACTAS={'TRUE' if can_act_as_default else 'FALSE'}")

    hard_required = {
        "batch.jobs.create",
        "batch.jobs.get",
        "batch.jobs.list",
        "batch.jobs.delete",
        "serviceusage.services.use",
    }
    missing_hard = sorted(hard_required - granted)
    api_definitely_disabled = sorted(
        name for name, result in api_results.items() if result.get("http") == 200 and result.get("state") != "ENABLED"
    )

    result = {
        "schema_version": "v48-batch-storage-preflight-1",
        "project": PROJECT,
        "project_number": project_number,
        "region": REGION,
        "project_permissions": {p: p in granted for p in PROJECT_PERMISSIONS},
        "required_apis": api_results,
        "storage_list_http": storage_http,
        "visible_bucket_count": len(bucket_items),
        "visible_buckets": [
            {
                "name": item.get("name"),
                "location": item.get("location"),
                "storageClass": item.get("storageClass"),
            }
            for item in bucket_items
        ],
        "batch_list_http": batch_http,
        "default_compute_service_account": default_compute_sa,
        "default_compute_service_account_test_http": sa_http,
        "can_act_as_default_compute_service_account": can_act_as_default,
        "missing_hard_orchestration_permissions": missing_hard,
        "definitely_disabled_apis": api_definitely_disabled,
        "mutation_performed": false if False else False,
    }
    out = Path("artifacts/v48_batch_storage_preflight.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    ready = not missing_hard and not api_definitely_disabled and batch_http == 200 and can_act_as_default
    print(f"RX_V48_PREFLIGHT_MUTATION_PERFORMED=FALSE")
    print(f"RX_V48_BATCH_STORAGE_PREFLIGHT={'PASS' if ready else 'FAIL_CLOSED'}")
    if not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

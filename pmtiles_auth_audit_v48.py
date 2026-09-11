from __future__ import annotations

"""Read-only V48 runtime credential audit.

This module never refreshes a token, queries BigQuery, signs bytes, or touches GCS.
It logs only non-secret credential metadata required before the V4 signing gate.
"""

import json
import os


def _safe_audit() -> dict[str, object]:
    project = (
        os.getenv("RX_BIGQUERY_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or ""
    ).strip() or None
    raw = (os.getenv("RX_BIGQUERY_SERVICE_ACCOUNT_JSON") or "").strip()
    if raw:
        try:
            info = json.loads(raw)
            email = str(info.get("client_email") or "").strip() or None
            return {
                "source": "RX_BIGQUERY_SERVICE_ACCOUNT_JSON",
                "credential_type": str(info.get("type") or "service_account_json"),
                "project": str(info.get("project_id") or project or "") or None,
                "service_account_email": email,
                "local_signer_material_present": bool(info.get("private_key") and email),
            }
        except Exception as exc:
            return {
                "source": "RX_BIGQUERY_SERVICE_ACCOUNT_JSON",
                "credential_type": "invalid_json",
                "project": project,
                "error": type(exc).__name__,
            }
    try:
        import google.auth

        credentials, adc_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        email = (
            getattr(credentials, "service_account_email", None)
            or getattr(credentials, "signer_email", None)
        )
        return {
            "source": "application_default_credentials",
            "credential_type": f"{type(credentials).__module__}.{type(credentials).__name__}",
            "project": adc_project or project,
            "service_account_email": str(email).strip() if email else None,
            "local_signer_material_present": bool(
                getattr(credentials, "sign_bytes", None)
                or getattr(credentials, "signer", None)
            ),
        }
    except Exception as exc:
        return {
            "source": "application_default_credentials",
            "credential_type": "unavailable",
            "project": project,
            "error": f"{type(exc).__name__}:{str(exc)[:160]}",
        }


AUDIT = _safe_audit()
print("RX_V48_PMTILES_AUTH_AUDIT=" + json.dumps(AUDIT, sort_keys=True), flush=True)

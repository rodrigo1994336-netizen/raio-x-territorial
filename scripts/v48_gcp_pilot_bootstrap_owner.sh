#!/usr/bin/env bash
set -euo pipefail

# OWNER/ADMIN-ONLY bootstrap for the V48 ES pilot.
# It intentionally grants no Cloud Billing/Budget role and no direct Compute
# instance-admin role to the GitHub orchestrator.

PROJECT="metodo-afp-plataforma"
REGION="southamerica-east1"
BUCKET="raio-x-territorial-car-metodo-afp-plataforma"
ORCHESTRATOR_SA="raio-x-bigquery@${PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA_NAME="rx-v48-vector-worker"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

if [[ "${GOOGLE_CLOUD_PROJECT:-${CLOUDSDK_CORE_PROJECT:-}}" != "${PROJECT}" ]]; then
  gcloud config set project "${PROJECT}" >/dev/null
fi

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n1)"
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "RX_V48_BOOTSTRAP=FAIL_NO_ACTIVE_ADMIN_IDENTITY" >&2
  exit 2
fi

echo "RX_V48_BOOTSTRAP_ACTIVE_ACCOUNT=${ACTIVE_ACCOUNT}"
echo "RX_V48_BOOTSTRAP_PROJECT=${PROJECT}"
echo "RX_V48_BOOTSTRAP_REGION=${REGION}"
echo "RX_V48_BOOTSTRAP_SCOPE=ES_ONLY"
echo "RX_V48_BOOTSTRAP_BUDGET_ROLE_GRANTS=NONE"

# APIs needed by Batch + worker. Enabling APIs is an owner/admin action and is
# kept outside the long-lived service-account privileges.
gcloud services enable \
  batch.googleapis.com \
  compute.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT}"

# Dedicated runtime identity, no user-managed key.
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --project="${PROJECT}" \
    --display-name="Raio-X V48 vector pilot worker"
fi

# Dedicated single-region bucket. Uniform IAM + PAP prevents accidental public
# exposure. The browser pilot will use an authenticated/signed validation path.
if ! gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access \
    --public-access-prevention
else
  ACTUAL_LOCATION="$(gcloud storage buckets describe "gs://${BUCKET}" --format='value(location)')"
  if [[ "${ACTUAL_LOCATION,,}" != "${REGION,,}" ]]; then
    echo "RX_V48_BOOTSTRAP=FAIL_BUCKET_LOCATION_${ACTUAL_LOCATION}" >&2
    exit 3
  fi
  gcloud storage buckets update "gs://${BUCKET}" \
    --uniform-bucket-level-access \
    --public-access-prevention >/dev/null
fi

# GitHub service account = orchestrator only. It can submit/read/delete Batch
# jobs, consume enabled services, act as the dedicated runtime identity, and
# read pilot objects for validation. It cannot create Compute VMs directly.
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${ORCHESTRATOR_SA}" \
  --role="roles/batch.jobsEditor" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${ORCHESTRATOR_SA}" \
  --role="roles/serviceusage.serviceUsageConsumer" \
  --condition=None >/dev/null

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${ORCHESTRATOR_SA}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${ORCHESTRATOR_SA}" \
  --role="roles/storage.objectViewer" >/dev/null

# Runtime worker = query public SICAR through jobs billed to this project,
# report Batch state/logs, and write immutable pilot artifacts only to the
# dedicated bucket. No project-wide Storage role is granted.
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/batch.agentReporter" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/bigquery.jobUser" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/logging.logWriter" \
  --condition=None >/dev/null

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectUser" >/dev/null

# Assertions: owner-side evidence only. No national resource is created here.
gcloud storage buckets describe "gs://${BUCKET}" \
  --format='value(name,location,storageClass,uniformBucketLevelAccess,publicAccessPrevention)'
gcloud iam service-accounts describe "${RUNTIME_SA}" \
  --project="${PROJECT}" \
  --format='value(email,disabled)'

echo "RX_V48_BOOTSTRAP_RUNTIME_SA=${RUNTIME_SA}"
echo "RX_V48_BOOTSTRAP_BUCKET=${BUCKET}"
echo "RX_V48_BOOTSTRAP_ORCHESTRATOR=${ORCHESTRATOR_SA}"
echo "RX_V48_BOOTSTRAP_NATIONAL_GENERATION=DISABLED_BY_REPO_CONTRACT"
echo "RX_V48_BOOTSTRAP=PASS"

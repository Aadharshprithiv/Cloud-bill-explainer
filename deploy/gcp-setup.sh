#!/usr/bin/env bash
# One-time Google Cloud setup so GitHub Actions can deploy this app to Cloud Run.
# Run it in Cloud Shell (or anywhere gcloud is logged in as a project owner):
#
#   bash deploy/gcp-setup.sh YOUR_PROJECT_ID [REGION]
#
# It enables the APIs, creates a deploy service account, and lets this GitHub
# repo sign in as it through Workload Identity Federation (no JSON keys).
# At the end it prints the three GitHub variables to add.
set -euo pipefail

PROJECT_ID="${1:?usage: bash deploy/gcp-setup.sh PROJECT_ID [REGION]}"
REGION="${2:-us-central1}"
REPO="Aadharshprithiv/Cloud-bill-explainer"
SA_NAME="github-deployer"
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
POOL_ID="github"
PROVIDER_ID="github"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com secretmanager.googleapis.com

gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_NAME" --display-name="GitHub Actions deployer"

for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin \
            roles/storage.admin roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
done

# Source deploys build with the default compute service account.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/run.builder --condition=None >/dev/null

gcloud iam workload-identity-pools describe "$POOL_ID" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL_ID" --location=global --display-name="GitHub"

gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
    --location=global --workload-identity-pool="$POOL_ID" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --location=global --workload-identity-pool="$POOL_ID" --display-name="GitHub" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='$REPO'"

POOL_NAME="$(gcloud iam workload-identity-pools describe "$POOL_ID" --location=global --format='value(name)')"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL_NAME/attribute.repository/$REPO" >/dev/null

PROVIDER_NAME="$(gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --location=global --workload-identity-pool="$POOL_ID" --format='value(name)')"

cat <<DONE

Done. In GitHub, open the repo's Settings > Secrets and variables > Actions > Variables
and add these repository variables:

  GCP_PROJECT_ID                  $PROJECT_ID
  GCP_REGION                      $REGION
  GCP_WORKLOAD_IDENTITY_PROVIDER  $PROVIDER_NAME
  GCP_SERVICE_ACCOUNT             $SA

Then run the "Deploy to Cloud Run" workflow from the Actions tab (or push to main).
DONE

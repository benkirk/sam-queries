#!/usr/bin/env bash
# Creates k8s Secrets from ../.env for local helm testing (bypasses ExternalSecrets/OpenBao).
# Run once per namespace before `helm install`, or re-run to update credentials.
#
# Usage: bash helm/local-secrets.sh [namespace]
#   namespace defaults to "default"
#
# Requires: kubectl pointed at Docker Desktop k8s cluster
set -euo pipefail

NAMESPACE=${1:-default}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

[[ -f "$ENV_FILE" ]] || { echo "ERROR: .env not found at $ENV_FILE" >&2; exit 1; }

# Load variables from .env without polluting the current shell permanently
set -a; source "$ENV_FILE"; set +a

echo "Creating k8s secrets in namespace: $NAMESPACE"

# STATUS DB credentials → samuel-db-credentials
kubectl create secret generic samuel-db-credentials \
  --namespace="$NAMESPACE" \
  --from-literal=username="${STATUS_DB_USERNAME:-root}" \
  --from-literal=password="${STATUS_DB_PASSWORD:-root}" \
  --dry-run=client -o yaml | kubectl apply -f -

# SAM DB credentials → samuel-sam-db-credentials
kubectl create secret generic samuel-sam-db-credentials \
  --namespace="$NAMESPACE" \
  --from-literal=username="${SAM_DB_USERNAME}" \
  --from-literal=password="${SAM_DB_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# JupyterHub API token → samuel-jh-credentials
kubectl create secret generic samuel-jh-credentials \
  --namespace="$NAMESPACE" \
  --from-literal=token="${JUPYTERHUB_API_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Done. Secrets created/updated in namespace '$NAMESPACE'."

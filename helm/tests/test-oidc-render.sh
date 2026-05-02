#!/usr/bin/env bash
# Helm template render assertions for OIDC SSO + ExternalSecret wiring.
#
# Asserts:
#   1. values.yaml renders the expected OIDC env vars and ExternalSecret CRD.
#   2. values-local.yaml does NOT render the OIDC ExternalSecret (local dev
#      uses DISABLE_AUTH=1 + stub provider; OpenBao is unavailable locally).
#
# Usage:
#   bash helm/tests/test-oidc-render.sh
#
# Exit codes:
#   0  all assertions passed
#   1  one or more assertions failed (specific failure logged to stderr)
#
# Requires: helm v3+ in PATH.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${SCRIPT_DIR}/.."
RELEASE_NAME="samuel"

red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

assert_contains() {
  local haystack="$1" needle="$2" msg="$3"
  if ! printf '%s' "$haystack" | grep -qF -- "$needle"; then
    red "FAIL: $msg"
    red "  expected to find: $needle"
    return 1
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" msg="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    red "FAIL: $msg"
    red "  unexpectedly found: $needle"
    return 1
  fi
}

if ! command -v helm >/dev/null 2>&1; then
  red "FAIL: helm not found in PATH (needed for template rendering)"
  exit 1
fi

# ---------------------------------------------------------------------------
# Production render (values.yaml only)
# ---------------------------------------------------------------------------

prod_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" -f "$CHART_DIR/values.yaml")

assert_contains "$prod_out" "name: AUTH_PROVIDER"               "values.yaml: AUTH_PROVIDER env not rendered"
assert_contains "$prod_out" "name: OIDC_REDIRECT_URI"           "values.yaml: OIDC_REDIRECT_URI env not rendered"
assert_contains "$prod_out" "name: OIDC_USERNAME_CLAIM"         "values.yaml: OIDC_USERNAME_CLAIM env not rendered"
assert_contains "$prod_out" "name: OIDC_SCOPES"                 "values.yaml: OIDC_SCOPES env not rendered"
assert_contains "$prod_out" "name: FLASK_CONFIG"                "values.yaml: FLASK_CONFIG env not rendered"

assert_contains "$prod_out" "samuel.k8s.ucar.edu/auth/oidc/callback" \
  "values.yaml: OIDC_REDIRECT_URI value missing or wrong host"

assert_contains "$prod_out" "name: OIDC_CLIENT_ID"      "values.yaml: OIDC_CLIENT_ID secretKeyRef not rendered"
assert_contains "$prod_out" "name: OIDC_CLIENT_SECRET"  "values.yaml: OIDC_CLIENT_SECRET secretKeyRef not rendered"
assert_contains "$prod_out" "name: OIDC_ISSUER"         "values.yaml: OIDC_ISSUER secretKeyRef not rendered"
assert_contains "$prod_out" "name: FLASK_SECRET_KEY"    "values.yaml: FLASK_SECRET_KEY secretKeyRef not rendered"

assert_contains "$prod_out" "samuel-oidc-credentials"   "values.yaml: oidc Secret name not referenced"
assert_contains "$prod_out" "kind: ExternalSecret"      "values.yaml: ExternalSecret CRD not rendered"
assert_contains "$prod_out" "samuel-oidc-credentials-esos" "values.yaml: oidc ExternalSecret CRD not rendered"
assert_contains "$prod_out" "csg/sam-oidc"              "values.yaml: oidc OpenBao secretPath not rendered"

# Critical security assertion: ensure the dev-only insecure key is NOT in prod render.
assert_not_contains "$prod_out" "dev-only-insecure-key" \
  "values.yaml: insecure FLASK_SECRET_KEY leaked into production render (S1)"
# In prod render, FLASK_SECRET_KEY must come from secretKeyRef, never a literal value.
assert_not_contains "$prod_out" 'value: "dev-only-insecure-key-change-for-production"' \
  "values.yaml: insecure FLASK_SECRET_KEY value present"

# ---------------------------------------------------------------------------
# Local dev render (values.yaml + values-local.yaml)
# ---------------------------------------------------------------------------

local_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" \
  -f "$CHART_DIR/values.yaml" \
  -f "$CHART_DIR/values-local.yaml")

# Local must NOT render the OIDC ExternalSecret (no OpenBao locally).
assert_not_contains "$local_out" "samuel-oidc-credentials-esos" \
  "values-local.yaml: OIDC ExternalSecret rendered in local dev (oidcCredentials.enabled should be false)"

# Local must NOT inject OIDC_CLIENT_ID/SECRET/ISSUER from a non-existent secret.
assert_not_contains "$local_out" "name: OIDC_CLIENT_ID" \
  "values-local.yaml: OIDC_CLIENT_ID secretKeyRef rendered in local dev"
assert_not_contains "$local_out" "name: OIDC_CLIENT_SECRET" \
  "values-local.yaml: OIDC_CLIENT_SECRET secretKeyRef rendered in local dev"

# Local must use stub auth provider (defense-in-depth alongside DISABLE_AUTH=1).
assert_contains "$local_out" 'value: "stub"' \
  "values-local.yaml: AUTH_PROVIDER not overridden to stub for local dev"

green "OK: helm renders match expectations"

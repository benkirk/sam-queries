#!/usr/bin/env bash
# Render assertions for the samuel-dev overlay (values.yaml + values-dev.yaml).
#
# Asserts, per manifest (-s), that dev is:
#   1. authenticated (production config, OIDC, no auth bypass)
#   2. mute (mail off or redirected; XRAS levers off; no prod XRAS key)
#   3. on its own data (sam_dev / system_status_dev on BOTH manifests, its own
#      OpenBao paths, its own API key)
#   4. disjoint from prod (no shared object name, label, host or TLS secret)
# then proves each check can fail by re-rendering with one prod value at a time.
#
# Usage:
#   bash helm/tests/test-dev-render.sh
#
# Exit codes:
#   0  all assertions passed
#   1  one or more assertions failed (specific failure logged to stderr)
#
# Requires: helm v3+ in PATH.

set -euo pipefail

# shellcheck source=lib/assert.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/assert.sh"

DEV_HOST="samuel-dev.k8s.ucar.edu"
DEV_URL="https://${DEV_HOST}"
OVERLAY="$CHART_DIR/values-dev.yaml"

prod_hash=$(grep -E '^[[:space:]]+API_KEYS_COLLECTOR:' "$CHART_DIR/values.yaml" | sed 's/.*: *//' | tr -d '"')
[[ -n "$prod_hash" ]] || { red "FATAL: could not read API_KEYS_COLLECTOR out of values.yaml"; exit 1; }

# env_value <manifest> <NAME>: the literal `value:` of one env entry ("" if absent).
env_value() {
  awk -v k="$2" '$1=="-" && $2=="name:" && $3==k { getline; if ($1=="value:") { gsub(/"/, "", $2); print $2 }; exit }' <<<"$1"
}
# env_names <manifest> <regex>: sorted env names matching the regex.
env_names() {
  grep -E "^[[:space:]]+- name: ${2}" <<<"$1" | sed -E 's/.*name: //' | sort -u
}
# env_value_names <manifest> <regex>: like env_names, literal `value:` entries only.
env_value_names() {
  awk -v re="^${2}" '$1=="-" && $2=="name:" && $3 ~ re { n=$3; getline; if ($1=="value:") print n }' <<<"$1" | sort -u
}
# objects <manifest>: one "Kind name" per rendered object.
objects() {
  awk '/^kind:/ { kind=$2 } /^  name:/ && kind { print kind, $2; kind="" }' <<<"$1" | sort
}
same_on_both() {   # <deploy> <cron> <NAME> <expected|"">: present on both with one value
  local d c
  d=$(env_value "$1" "$3"); c=$(env_value "$2" "$3")
  [[ -n "$d" && "$d" == "$c" ]] || { red "FAIL: $3 must render identically on both manifests (deploy='$d' cron='$c')"; return 1; }
  [[ -z "$4" || "$d" == "$4" ]] || { red "FAIL: $3 must be '$4' on dev, got '$d'"; return 1; }
}

# Every assertion below is a plain command under `set -e`, so the first failure
# ends the calling (sub)shell non-zero. Do not wrap a call in `if`/`!`/`||`:
# that context switches errexit off for the whole body.
check_dev() {
  local whole deploy cron netpol prod
  whole=$(render dev "$@")
  deploy=$(render dev -s templates/deployment.yaml "$@")
  cron=$(render dev -s templates/cronjob-tasks.yaml "$@")
  netpol=$(render dev -s templates/redis-networkpolicy.yaml "$@")
  prod=$(render prod)

  # --- 1. authenticated ------------------------------------------------------
  [[ "$(env_value "$deploy" FLASK_CONFIG)" == "production" ]] || { red "FAIL: FLASK_CONFIG must be production (the auth interlock)"; return 1; }
  [[ "$(env_value "$deploy" AUTH_PROVIDER)" == "oidc" ]] || { red "FAIL: AUTH_PROVIDER must be oidc"; return 1; }
  [[ "$(env_value "$deploy" DISABLE_AUTH)" == "0" ]] || { red "FAIL: DISABLE_AUTH must be 0 on a routable host"; return 1; }
  # The anonymous /register form is live on dev (its test bed) and dark in prod.
  [[ "$(env_value "$deploy" ACCOUNT_REGISTRATION_ENABLED)" == "1" ]] || { red "FAIL: ACCOUNT_REGISTRATION_ENABLED must be 1 on dev"; return 1; }
  # The project Invitations tab is live on dev and dark in prod (initial prod
  # capability is the XRAS-mirrored Accounts queue only).
  [[ "$(env_value "$deploy" ACCOUNT_INVITATIONS_ENABLED)" == "1" ]] || { red "FAIL: ACCOUNT_INVITATIONS_ENABLED must be 1 on dev"; return 1; }
  # The signed-in preview of /register runs under a low site-wide POST ceiling.
  grep -A1 'name: RATELIMIT_REGISTER_GLOBAL' <<<"$deploy" | grep -q 'value: "5 per hour; 20 per day"' \
    || { red "FAIL: dev must pin RATELIMIT_REGISTER_GLOBAL low for the registration preview"; return 1; }
  assert_not_contains "$deploy" "name: OIDC_REDIRECT_URI" "OIDC_REDIRECT_URI must stay unset so the callback follows the request host"
  assert_not_contains "$whole" "auth/oidc/callback" "no hard-coded OIDC callback URL"

  # --- 2. mute ---------------------------------------------------------------
  local m
  for m in "$deploy" "$cron"; do
    if [[ "$(env_value "$m" NOTIFY_ENABLED)" != "0" && -z "$(env_value "$m" NOTIFY_REDIRECT_TO)" ]]; then
      red "FAIL: mail must be off (NOTIFY_ENABLED=0) or redirected (NOTIFY_REDIRECT_TO) on both manifests"; return 1
    fi
  done
  same_on_both "$deploy" "$cron" XRAS_OUTGOING_ENABLED "0"
  [[ "$(env_value "$deploy" XRAS_WRITE_ENABLED)" == "0" ]] || { red "FAIL: XRAS_WRITE_ENABLED must be 0 on dev"; return 1; }
  [[ "$(env_value "$deploy" XRAS_ACTIONS_CAPTURE_ONLY)" == "1" ]] || { red "FAIL: XRAS_ACTIONS_CAPTURE_ONLY must be 1 on dev"; return 1; }
  assert_not_contains "$whole" "name: XRAS_API_KEY" "dev must not hold the XRAS API key"
  assert_not_contains "$whole" "xras-api-credentials" "dev must not sync the XRAS key from OpenBao"

  # --- 3. its own data -------------------------------------------------------
  local dev_hash
  dev_hash=$(env_value "$deploy" API_KEYS_COLLECTOR)
  [[ -n "$dev_hash" ]] || { red "FAIL: dev needs its own API_KEYS_COLLECTOR"; return 1; }
  [[ "$dev_hash" != "$prod_hash" ]] || { red "FAIL: dev must not accept the production collector key"; return 1; }
  same_on_both "$deploy" "$cron" SAM_API_BASE "$DEV_URL"

  local switch
  same_on_both "$deploy" "$cron" SAM_TASKS_DISABLED ""
  switch=$(env_value "$cron" SAM_TASKS_DISABLED)
  local t
  for t in expiration_notices xras_notices; do
    grep -q "$t" <<<"$switch" || { red "FAIL: $t (mails PIs) must be in SAM_TASKS_DISABLED on dev"; return 1; }
  done
  if [[ "$(env_value "$cron" XRAS_OUTGOING_ENABLED)" != "1" ]]; then
    grep -q xras_sweep <<<"$switch" || { red "FAIL: xras_sweep must be disabled while XRAS_OUTGOING_ENABLED is off"; return 1; }
  fi

  # Load-test target: the three traffic tiers are raised, the login tier is not.
  [[ -n "$(env_value "$deploy" RATELIMIT_M2M)" ]] || { red "FAIL: dev must raise RATELIMIT_M2M (load-test target)"; return 1; }
  [[ "$(env_value "$deploy" RATELIMIT_AUTHED)" == "$(env_value "$deploy" RATELIMIT_M2M)" ]] || { red "FAIL: RATELIMIT_AUTHED and RATELIMIT_M2M must be raised together"; return 1; }
  assert_not_contains "$deploy" "name: RATELIMIT_AUTH_LOGIN" "the login tier stays at the prod default on dev"

  assert_contains "$deploy" "replicas: 1" "dev runs one replica"
  assert_not_contains "$whole" "kind: PodDisruptionBudget" "a PDB with minAvailable 1 on 1 replica blocks node drains"

  local k
  for k in SAM_DB_DRIVER SAM_DB_SERVER SAM_DB_PORT SAM_DB_NAME SAM_DB_REQUIRE_SSL STATUS_DB_DRIVER STATUS_DB_SERVER STATUS_DB_NAME; do
    same_on_both "$deploy" "$cron" "$k" ""
  done
  same_on_both "$deploy" "$cron" SAM_DB_NAME "sam_dev"
  same_on_both "$deploy" "$cron" SAM_DB_DRIVER "postgresql"
  same_on_both "$deploy" "$cron" STATUS_DB_NAME "system_status_dev"
  # Set-equality catches a future key the CronJob's exclusion list swallows.
  local d_keys c_keys
  d_keys=$(env_names "$deploy" '(SAM_DB|STATUS_DB)_'); c_keys=$(env_names "$cron" '(SAM_DB|STATUS_DB)_')
  [[ "$d_keys" == "$c_keys" ]] || { red "FAIL: SAM_DB_*/STATUS_DB_* differ between manifests:"; diff <(echo "$d_keys") <(echo "$c_keys") >&2 || true; return 1; }

  assert_contains "$deploy" "name: samuel-dev-sam-db-credentials" "webapp reads the dev SAM credentials Secret"
  assert_contains "$cron"   "name: samuel-dev-sam-db-credentials" "tasks read the dev SAM credentials Secret"
  assert_contains "$whole" "key: csg/sam-dev-pg"   "SAM credentials come from csg/sam-dev-pg"
  assert_contains "$whole" "key: csg/sam-dev-oidc" "OIDC credentials come from csg/sam-dev-oidc"
  assert_contains "$whole" "key: csg/jh-api-token" "the JupyterHub token is inherited from prod by decision"
  assert_not_contains "$whole" "csg/sam-writeuser" "dev must never sync the production SAM write credential"
  assert_not_contains "$whole" "csg/sam-oidc"      "dev must never sync the production OIDC registration"

  assert_contains "$deploy" "redis://samuel-dev-redis." "webapp uses the dev Redis"
  assert_contains "$cron"   "redis://samuel-dev-redis." "tasks use the dev Redis"
  assert_contains "$netpol" "name: samuel-dev-redis-allow-webapp" "dev Redis has its own NetworkPolicy"
  assert_contains "$netpol" "app: samuel-dev-tasks" "the dev task pods reach the dev Redis"
  grep -qE '^[[:space:]]+app: samuel-dev$' <<<"$netpol" || { red "FAIL: the dev webapp pods reach the dev Redis"; return 1; }

  local refs
  refs=$(grep -cE '^[[:space:]]+image: ghcr\.io/.*/webapp:' <<<"$whole" || true)
  [[ "$refs" -eq 2 ]] || { red "FAIL: expected exactly 2 webapp image refs in the dev render, got $refs"; return 1; }
  if grep -qE '^[[:space:]]*image:' "$OVERLAY"; then
    red "FAIL: values-dev.yaml must not carry an image: line (CI pins values.yaml only)"; return 1
  fi

  # --- 4. disjoint from prod -------------------------------------------------
  local shared
  shared=$(comm -12 <(objects "$prod") <(objects "$whole"))
  [[ -z "$shared" ]] || { red "FAIL: dev and prod render the same object(s):"; red "$shared"; return 1; }
  local bad
  bad=$(objects "$whole" | awk '$2 !~ /^samuel-dev/' || true)
  [[ -z "$bad" ]] || { red "FAIL: every dev object must be named samuel-dev*:"; red "$bad"; return 1; }
  shared=$(comm -12 <(grep -E '^\s+app: ' <<<"$prod" | sort -u) <(grep -E '^\s+app: ' <<<"$whole" | sort -u))
  [[ -z "$shared" ]] || { red "FAIL: dev and prod share an app label: $shared"; return 1; }
  grep -qE '^[[:space:]]+group: samuel$' <<<"$whole" && { red "FAIL: dev must not carry group: samuel"; return 1; }
  local hosts
  hosts=$(grep -E '^\s+- host: ' <<<"$whole" | sed -E 's/.*host: //' | tr -d '"' | sort -u)
  [[ "$hosts" == "$DEV_HOST" ]] || { red "FAIL: dev ingress hosts must be exactly $DEV_HOST, got: $hosts"; return 1; }
  shared=$(comm -12 <(grep -E '^\s+- host: ' <<<"$prod" | sort -u) <(grep -E '^\s+- host: ' <<<"$whole" | sort -u))
  [[ -z "$shared" ]] || { red "FAIL: dev and prod share an ingress host: $shared"; return 1; }
  shared=$(comm -12 <(grep -E 'secretName: ' <<<"$prod" | sort -u) <(grep -E 'secretName: ' <<<"$whole" | sort -u))
  [[ -z "$shared" ]] || { red "FAIL: dev and prod share a TLS secret: $shared"; return 1; }
}

# --- the positive path -------------------------------------------------------
check_dev

# --- prod proxy: the forward loop stays a no-op for prod ---------------------
prod_cron=$(render prod -s templates/cronjob-tasks.yaml)
prod_keys=$(env_value_names "$prod_cron" '(SAM_DB|STATUS_DB)_' | tr '\n' ' ')
[[ "$prod_keys" == "SAM_DB_DRIVER SAM_DB_REQUIRE_SSL SAM_DB_SERVER STATUS_DB_DRIVER STATUS_DB_SERVER " ]] || {
  red "FAIL: the prod CronJob must carry exactly the five original DB keys, got: $prod_keys"; exit 1; }
prod_deploy=$(render prod -s templates/deployment.yaml)
[[ -z "$(env_value_names "$prod_deploy" 'RATELIMIT_(AUTHED|M2M|ANON|AUTH_LOGIN)')" ]] || {
  red "FAIL: prod must not carry a RATELIMIT_ tier override (only the dev overlay raises them)"; exit 1; }
[[ "$(env_value "$prod_deploy" ACCOUNT_REGISTRATION_ENABLED)" == "0" ]] || {
  red "FAIL: the anonymous /register form must ship dark in prod (ACCOUNT_REGISTRATION_ENABLED=0)"; exit 1; }
[[ "$(env_value "$prod_deploy" ACCOUNT_INVITATIONS_ENABLED)" == "0" ]] || {
  red "FAIL: the project Invitations tab must ship dark in prod (ACCOUNT_INVITATIONS_ENABLED=0)"; exit 1; }

# --- the negative loop: each prod value must be refused ----------------------
# `set +e` around a `( set -e; ... )` subshell keeps errexit live inside it;
# an `if ( ... )` would switch errexit off for the whole body.
expect_reject() {
  set +e
  ( set -e; check_dev "$@" ) >/dev/null 2>&1
  local rc=$?
  set -e
  [[ $rc -ne 0 ]] || { red "FAIL: the dev checks accepted: $*"; exit 1; }
}
expect_reject --set webapp.env.DISABLE_AUTH=1
expect_reject --set webapp.env.ACCOUNT_REGISTRATION_ENABLED=0
expect_reject --set webapp.env.ACCOUNT_INVITATIONS_ENABLED=0
expect_reject --set webapp.env.FLASK_CONFIG=development
expect_reject --set webapp.env.AUTH_PROVIDER=stub
expect_reject --set webapp.env.NOTIFY_ENABLED=1
expect_reject --set webapp.env.XRAS_OUTGOING_ENABLED=1
expect_reject --set webapp.env.XRAS_WRITE_ENABLED=1
expect_reject --set webapp.env.XRAS_ACTIONS_CAPTURE_ONLY=0
expect_reject --set webapp.xrasApiCredentials.enabled=true
expect_reject --set webapp.env.SAM_DB_NAME=sam
expect_reject --set webapp.env.SAM_DB_NAME=null
expect_reject --set webapp.env.STATUS_DB_NAME=system_status
expect_reject --set webapp.samDbCredentials.secretPath=csg/sam-writeuser
expect_reject --set webapp.oidcCredentials.secretPath=csg/sam-oidc
expect_reject --set webapp.name=samuel
expect_reject --set webapp.group=samuel
expect_reject --set cache.name=samuel-redis
expect_reject --set tasks.name=samuel-tasks
expect_reject --set webapp.tls.secretName=incommon-cert-samuel
expect_reject --set webapp.tls.fqdn=samuel.k8s.ucar.edu
expect_reject --set tasks.env.SAM_TASKS_DISABLED=xras_notices
expect_reject --set podDisruptionBudget.enabled=true
expect_reject --set-string "webapp.env.API_KEYS_COLLECTOR=${prod_hash}"

green "OK: samuel-dev renders authenticated, mute, on its own data, disjoint from prod (24 rejections proven)"

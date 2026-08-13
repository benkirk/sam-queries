#!/usr/bin/env bash
# Helm template render assertions for the scheduled-task CronJob.
#
# Asserts:
#   1. values.yaml renders a CronJob with the fields that encode a decision
#      (concurrencyPolicy, backoffLimit, activeDeadlineSeconds, timeZone).
#   2. It reuses webapp.container.image — the ONE thing here that would fail
#      silently and stay broken. See the image-pinning block below.
#   3. It inherits the Deployment's hardening (non-root, no SA token).
#   4. It ships kill-switched, so merging cannot delete anything.
#   5. values-local.yaml does NOT render it (nothing should silently DELETE
#      local data on Docker Desktop).
#
# Usage:
#   bash helm/tests/test-cronjob-render.sh
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

assert_contains "$prod_out" "kind: CronJob" \
  "production values must render the scheduled-task CronJob"
assert_contains "$prod_out" "name: samuel-tasks" \
  "CronJob name should come from tasks.name"
assert_contains "$prod_out" 'command: ["sam-admin"]' \
  "the CronJob must invoke sam-admin, not a bespoke entrypoint"
assert_contains "$prod_out" '- "--run-due"' \
  "the dispatcher entry point is 'tasks --run-due'"

# --- The image-pinning invariant -------------------------------------------
#
# CI's update-helm job sed-rewrites every line matching
# `image: ghcr.io/<repo>/webapp:` in values.yaml. A tasks-specific image key
# would either be missed (pinned at :main for ever) or silently co-rewritten,
# giving two sources of truth for one image. The CronJob therefore references
# .Values.webapp.container.image, and exactly two rendered manifests should
# carry that image: the Deployment and this CronJob.
webapp_image=$(grep -E '^\s+image: ghcr\.io/.*/webapp:' "$CHART_DIR/values.yaml" \
               | awk '{print $2}')
if [[ -z "$webapp_image" ]]; then
  red "FAIL: could not find webapp.container.image in values.yaml"
  exit 1
fi
assert_contains "$prod_out" "image: ${webapp_image}" \
  "CronJob must reuse webapp.container.image (${webapp_image})"

# Anchored to a real YAML key (indent + `image:`), so a comment that happens
# to quote the pattern is not counted as a manifest reference.
image_refs=$(printf '%s' "$prod_out" \
             | grep -cE '^[[:space:]]+image: ghcr\.io/.*/webapp:' || true)
if [[ "$image_refs" -ne 2 ]]; then
  red "FAIL: expected exactly 2 webapp image refs (Deployment + CronJob), got ${image_refs}"
  red "  A third means someone added a second pinned image line; CI's sed will"
  red "  rewrite all of them or none, and neither is what you want."
  exit 1
fi

# --- Fields that encode a decision -----------------------------------------
assert_contains "$prod_out" "concurrencyPolicy: Forbid" \
  "Forbid is the belt; the ledger is the suspenders"
assert_contains "$prod_out" "backoffLimit: 0" \
  "the next hourly dispatch IS the retry"
assert_contains "$prod_out" "activeDeadlineSeconds: 3000" \
  "a wedged run must not coexist with its successor (50min < 60min interval)"
assert_contains "$prod_out" "startingDeadlineSeconds: 600" \
  "never unset — >100 missed schedules wedges the controller permanently"
assert_contains "$prod_out" 'timeZone: "Etc/UTC"' \
  "the controller must never see a DST gap or fold; Python handles task-level DST"
assert_contains "$prod_out" "restartPolicy: Never" \
  "OnFailure contradicts backoffLimit: 0, and Never preserves the pod for logs"

# --- Inherited hardening ----------------------------------------------------
assert_contains "$prod_out" "automountServiceAccountToken: false" \
  "the dispatcher never talks to the k8s API"
assert_contains "$prod_out" "runAsUser: 1000" \
  "CronJob must inherit the Deployment's non-root securityContext"
assert_contains "$prod_out" "runAsNonRoot: true" \
  "CronJob must inherit runAsNonRoot"

# --- Wiring -----------------------------------------------------------------
assert_contains "$prod_out" "samuel-db-credentials" \
  "STATUS_DB_* must come from the same namespace Secret the Deployment uses"
assert_contains "$prod_out" "csg-postgres.k8s.ucar.edu" \
  "the ledger and the retention target both live on csg-postgres"
assert_contains "$prod_out" "fieldPath: metadata.name" \
  "RUNNER_ID ties a ledger row back to kubectl logs"
assert_contains "$prod_out" 'value: "America/Denver"' \
  "the pod clock stays Mountain, matching the webapp (see SCHEDULED_TASKS § 2.3)"

# --- Ships kill-switched ----------------------------------------------------
#
# This is a deliberate, temporary state: the P5 rollout is 24h of `skipped`
# rows proving creds/DNS/image with zero blast radius, and only THEN a second
# commit clearing the switch. If you are here because this assertion failed
# after that second commit, delete it.
assert_contains "$prod_out" 'name: SAM_TASKS_DISABLED' \
  "the kill switch must be present in the rendered env"
assert_contains "$prod_out" 'value: "cleanup_status_snapshots"' \
  "P4 ships with the destructive task disabled; P5 clears it separately"
assert_contains "$prod_out" 'value: "365"' \
  "STATUS_RETENTION_DAYS must be explicit in GitOps, not implied by a default"

# --- BOTH pods must see the kill switch -------------------------------------
#
# The CronJob OBEYS the switch; the webapp's Admin → Configuration card
# REPORTS it. They are two consumers of ONE declaration in tasks.env.
#
# ⚠️ Asserted per-manifest, and that is the whole point. The assertions above
# run against the *full* render, so the CronJob alone satisfies them — which is
# exactly how this shipped to production with the webapp not carrying the
# variable at all. The card read its own environment, found nothing, and
# rendered a kill-switched dispatcher as perfectly healthy: the precise failure
# that card exists to prevent.
#
# When P5 clears the switch this pair fails too — delete it alongside the block
# above, for the same reason.
deploy_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" \
             -f "$CHART_DIR/values.yaml" -s templates/deployment.yaml)

assert_contains "$deploy_out" 'name: SAM_TASKS_DISABLED' \
  "the webapp Deployment must carry the kill switch too, or the admin card lies"
assert_contains "$deploy_out" 'value: "cleanup_status_snapshots"' \
  "and it must carry the SAME value — one declaration, two consumers"

# ---------------------------------------------------------------------------
# Local dev render (values.yaml + values-local.yaml)
# ---------------------------------------------------------------------------

local_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" \
            -f "$CHART_DIR/values.yaml" -f "$CHART_DIR/values-local.yaml")

assert_not_contains "$local_out" "kind: CronJob" \
  "local dev must not render the CronJob — nothing should silently DELETE local data"
assert_contains "$local_out" "kind: Deployment" \
  "sanity: the local render should still produce the webapp Deployment"

green "OK: CronJob renders as expected (prod enabled + kill-switched, local disabled)"

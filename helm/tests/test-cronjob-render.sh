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
  if ! grep -qF -- "$needle" <<<"$haystack"; then
    red "FAIL: $msg"
    red "  expected to find: $needle"
    return 1
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" msg="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
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
# A deliberate, ongoing state: the chart enables tasks in stages, so the switch
# is expected to stay non-empty for as long as any registered task is awaiting
# review.
#
# ⚠️ Read the expected value OUT of values.yaml rather than pinning a literal.
# The literal version had to be edited in lockstep with every change to the
# list, in two places — and the comment here used to instruct the next reader
# to DELETE these assertions instead, which would have dropped the
# one-declaration-two-consumers guarantee below. Derive it, and neither
# happens.
switch=$(grep -E '^[[:space:]]+SAM_TASKS_DISABLED:' "$CHART_DIR/values.yaml" \
         | sed 's/.*: *//' | tr -d '"')
[[ -n "$switch" ]] || {
  echo "FATAL: could not read SAM_TASKS_DISABLED out of values.yaml" >&2
  exit 1
}

assert_contains "$prod_out" 'name: SAM_TASKS_DISABLED' \
  "the kill switch must be present in the rendered env"
assert_contains "$prod_out" "value: \"${switch}\"" \
  "the CronJob must carry the kill-switch value declared in values.yaml"
assert_contains "$prod_out" 'value: "365"' \
  "STATUS_RETENTION_DAYS must be explicit in GitOps, not implied by a default"

# --- Notifications must reach the CronJob, not just the Deployment ----------
#
# ⚠️ Asserted PER-MANIFEST, and that is load-bearing. `cronjob-tasks.yaml`
# renders `.Values.tasks.env` plus a hand-listed set and NOTHING else — it does
# not inherit `webapp.env`, where NOTIFY_* and MAIL_* live. So a whole-render
# grep passes on the Deployment's copy alone and proves nothing about the pod
# that actually sends the mail.
#
# The failure this catches is silent by construction: NotifyConfig is
# fail-closed, so a CronJob without NOTIFY_ENABLED records every message
# `suppressed`, reports `succeeded` and exits 0. Green Job, no mail, no
# indication. `expiration_notices` also refuses to run mail-disabled at
# runtime; this is the other half of that pair.
cron_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" \
           -f "$CHART_DIR/values.yaml" -s templates/cronjob-tasks.yaml)

assert_contains "$cron_out" 'name: NOTIFY_ENABLED' \
  "the CronJob must carry NOTIFY_ENABLED, or expiration_notices mails nobody, silently"
assert_contains "$cron_out" 'name: NOTIFY_TRANSPORT' \
  "and the transport, or it falls back to the smtp default by accident"
assert_contains "$cron_out" 'name: MAIL_SERVER' \
  "and the relay"
assert_contains "$cron_out" 'name: MAIL_DEFAULT_FROM' \
  "and the envelope sender, which must SPF-pass as sam-admin@ucar.edu"
assert_contains "$cron_out" 'name: SAM_TASKS_EMAIL_MAX' \
  "and the runaway guard"
assert_contains "$cron_out" 'name: SAM_TASKS_SUMMARY_TO' \
  "and the per-run summary recipient"
assert_contains "$cron_out" 'name: SAM_TASKS_XRAS_MAX' \
  "and xras_notices' own runaway guard — it does NOT share SAM_TASKS_EMAIL_MAX, \
because 2500 is ~50x that task's realistic volume"

# ── XRAS outgoing (xras_sweep) ──────────────────────────────────────────────
# Same trap as NOTIFY_*: this manifest renders `.Values.tasks.env` and does NOT
# inherit `.Values.webapp.env`, so every key the sweep reads is cross-referenced
# by hand in cronjob-tasks.yaml. Asserted against THIS manifest (-s) because a
# whole-render grep passes on the Deployment's copy and proves nothing about the
# pod that actually calls XRAS.
# ⚠️ The sweep publishes into the SHARED cache the webapp reads. Without this
# the bucket silently falls back to a per-worker in-process cache, the sweep
# reports success, the pod exits, and the dashboard tab shows "no sweep has
# published yet" forever. Caught on the first production run.
assert_contains "$cron_out" 'name: CACHE_REDIS_URL' \
  "the sweep cannot hand its worklist to the dashboard without the shared Redis"

# ...and reaching it needs more than the URL. Redis is default-deny except from
# the webapp's label; the task pods carry `app: samuel-tasks`, so without their
# own ingress peer they are silently denied and the sweep falls back to a
# per-worker cache that dies with the pod. Both halves, or neither works.
# ⚠️ Comments are STRIPPED before asserting. helm renders YAML comments into
# its output, and the first version of this check matched the explanatory
# comment it had just added to the template rather than the selector — passing
# with the peer deleted. Same class as grepping the whole render and hitting
# the Deployment's copy.
netpol_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" -f "$CHART_DIR/values.yaml" \
             -s templates/redis-networkpolicy.yaml | grep -v '^[[:space:]]*#')
assert_contains "$netpol_out" "app: samuel-tasks" \
  "the task pods need their own Redis ingress peer, not just the webapp's"

assert_contains "$cron_out" 'name: XRAS_OUTGOING_ENABLED' \
  "the sweep's master lever must reach the CronJob, not just the Deployment"
assert_contains "$cron_out" 'name: XRAS_API_BASE' \
  "and the API base URL"
assert_contains "$cron_out" 'name: XRAS_ALLOCATIONS_PROCESS' \
  "and the allocations process header"
assert_contains "$cron_out" 'name: XRAS_API_USER' \
  "and the required XA-USER header"
assert_contains "$cron_out" 'name: XRAS_API_KEY' \
  "and the key itself, via secretKeyRef — the sweep cannot enumerate without it"
assert_contains "$cron_out" 'name: samuel-xras-api-credentials' \
  "which must name the Secret the ExternalSecret materialises"
assert_contains "$cron_out" 'name: SAM_TASKS_XRAS_SWEEP_MAX_PAGES' \
  "and the sweep's page budget"
assert_contains "$cron_out" 'name: SAM_TASKS_XRAS_SWEEP_MAX_PEOPLE' \
  "and its person-refresh budget"
assert_contains "$cron_out" 'name: SAM_TASKS_XRAS_SWEEP_WINDOW_DAYS' \
  "and the window WITHOUT which the sweep reports a census, not a queue"
assert_contains "$cron_out" 'name: SAM_TASKS_XRAS_SWEEP_STATUS' \
  "and the request-status filter"

# Fail-closed, and pinned: the sweep ships switched off at BOTH levers — its
# name in SAM_TASKS_DISABLED, and XRAS_OUTGOING_ENABLED "0". Derived from
# values.yaml rather than hardcoded, so flipping either is a deliberate edit
# here as well as there.
outgoing=$(grep -E '^\s+XRAS_OUTGOING_ENABLED:' "$CHART_DIR/values.yaml" | awk '{print $2}' | tr -d '"')
assert_contains "$cron_out" "value: \"${outgoing}\"" \
  "XRAS_OUTGOING_ENABLED must render the value values.yaml declares"

# ⚠️ The sweep and the lever are ONE decision. The task skips while the lever
# is off, and the Feed-B dashboard tab renders only what the task publishes —
# so a chart with the task enabled and the lever off yields a permanently
# empty tab and a ledger full of `skipped`, with nothing failing to say so.
if ! grep -q 'xras_sweep' <<<"$switch"; then
  if [[ "$outgoing" != "1" ]]; then
    red "FAIL: xras_sweep is enabled but XRAS_OUTGOING_ENABLED is \"${outgoing}\""
    red "  The task would skip every run and the Feed-B tab would never fill."
    exit 1
  fi
fi

# The values must MATCH the Deployment's — cross-referenced, not duplicated.
notify_enabled=$(grep -E '^\s+NOTIFY_ENABLED:' "$CHART_DIR/values.yaml" \
                 | awk '{print $2}' | tr -d '"')
assert_contains "$cron_out" "value: \"${notify_enabled}\"" \
  "the CronJob's NOTIFY_ENABLED must be webapp.env's, not a second literal"

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
# Value derived from values.yaml, same as the CronJob assertion above: what is
# being proved is that the two manifests AGREE, not what the list happens to
# say today.
#
# One caveat if the list is ever emptied: deployment.yaml guards the key with
# `{{- with .SAM_TASKS_DISABLED }}`, and an empty string is falsy, so the
# webapp renders no variable at all. That is correct behavior — the card then
# reports nothing disabled — but this pair would need to become conditional.
deploy_out=$(helm template "$RELEASE_NAME" "$CHART_DIR" \
             -f "$CHART_DIR/values.yaml" -s templates/deployment.yaml)

assert_contains "$deploy_out" 'name: SAM_TASKS_DISABLED' \
  "the webapp Deployment must carry the kill switch too, or the admin card lies"
assert_contains "$deploy_out" "value: \"${switch}\"" \
  "and it must carry the SAME value — one declaration, two consumers"

# The Deployment needs the key too — the dashboard card enriches from it. The
# two manifests share no env anchor, so this is a second hand-written copy and
# must be asserted separately.
assert_contains "$deploy_out" 'name: XRAS_API_KEY' \
  "the webapp needs the XRAS key for the account-creation card's person detail"
assert_contains "$deploy_out" 'name: XRAS_OUTGOING_ENABLED' \
  "and the same fail-closed lever"

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

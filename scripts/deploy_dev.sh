#!/bin/bash
# deploy_dev.sh — TEMPORARY laptop deploy of the samuel-dev release on nwc1.
#
# Phase 1 of docs/plans/K8S_DEV_ENVIRONMENT.md: until the platform team adds
# Argo CD Application `sam-query-dev`, this runs `helm upgrade --install` from
# a laptop against the tree CI pinned on origin/cirrus-dev. It self-retires:
# once the Deployment carries an Argo tracking annotation it refuses to run.
# Retire it for good then: delete the helm release Secrets
# (kubectl -n sam-queries delete secret -l name=samuel-dev,owner=helm), drop
# `make deploy-dev`, and delete this file.
#
# Deploys the cirrus-dev TREE, never the working tree, and refuses to touch the
# cluster unless the render is unmistakably the dev overlay (no prod object
# name, host or OpenBao path; dev databases on both manifests; mail off;
# XRAS capture-only).
#
# Usage:
#   scripts/deploy_dev.sh [--render-only] [--no-color]
#
# Options:
#       --render-only   Fetch, unpack, render and check; do not touch the cluster
#       --no-color      Disable ANSI color
#   -h, --help          Show this help
#
# Exit codes: 0 deployed (or render-only passed) / 1 precondition failed /
# 2 render refused.
#
# DEPLOY_DEV_SOURCE_REF overrides the source ref for --render-only tests ONLY;
# a real deploy always uses origin/cirrus-dev.

set -euo pipefail

_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/common.sh
source "${_LIBDIR}/common.sh"

RELEASE="samuel-dev"
NAMESPACE="sam-queries"
CONTEXT="nwc1"
SOURCE_REF="origin/cirrus-dev"
DEV_HOST="samuel-dev.k8s.ucar.edu"
RENDER_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --render-only) RENDER_ONLY=1; shift;;
        --no-color)    USE_COLOR=0; shift;;
        -h|--help)     usage_from_header "$0"; exit 0;;
        *) echo "Unknown option: $1" >&2; exit 1;;
    esac
done
setup_colors

refuse() { echo -e "  ${RED}✘ REFUSED${NC} — $*" >&2; exit 2; }
precondition() { echo -e "  ${RED}✘${NC} $*" >&2; exit 1; }

if [[ -n "${DEPLOY_DEV_SOURCE_REF:-}" ]]; then
    [[ $RENDER_ONLY -eq 1 ]] || precondition "DEPLOY_DEV_SOURCE_REF is honored only with --render-only"
    SOURCE_REF="$DEPLOY_DEV_SOURCE_REF"
fi

# --- 1. preconditions --------------------------------------------------------
for c in kubectl helm git tar; do command -v "$c" >/dev/null 2>&1 || precondition "$c not found in PATH"; done
CUR_CTX=$(kubectl config current-context 2>/dev/null || true)
[[ "$CUR_CTX" == "$CONTEXT" ]] || precondition "kubectl context is '${CUR_CTX:-<none>}', expected '$CONTEXT'"
info "kubectl context $CONTEXT, namespace $NAMESPACE, release $RELEASE"

if [[ "$SOURCE_REF" == "origin/cirrus-dev" ]]; then
    git fetch --quiet origin cirrus-dev || precondition "cannot fetch origin/cirrus-dev (has CI pinned it yet?)"
fi
git rev-parse --verify --quiet "$SOURCE_REF^{commit}" >/dev/null || precondition "no such ref: $SOURCE_REF"
info "source: $SOURCE_REF = $(git log -1 --format='%h %s' "$SOURCE_REF")"

# --- 2. self-retire once Argo owns the objects --------------------------------
TRACKING=$(kubectl -n "$NAMESPACE" get deploy "$RELEASE" \
             -o jsonpath='{.metadata.annotations.argocd\.argoproj\.io/tracking-id}' 2>/dev/null || true)
[[ -z "$TRACKING" ]] || precondition "deploy/$RELEASE is Argo-managed ($TRACKING); retire this script (see header)"

# --- 3. unpack the pinned tree ------------------------------------------------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git archive "$SOURCE_REF" helm | tar -x -C "$TMP"
CHART="$TMP/helm"
[[ -f "$CHART/values-dev.yaml" ]] || refuse "$SOURCE_REF carries no helm/values-dev.yaml"
grep -qE '^\s+name: samuel-dev$' "$CHART/values-dev.yaml" || refuse "values-dev.yaml does not name samuel-dev"
PIN=$(grep -E '^\s+image: ghcr\.io/.*/webapp:sha-[0-9a-f]{7}' "$CHART/values.yaml" | awk '{print $2}' || true)
[[ -n "$PIN" ]] || refuse "values.yaml on $SOURCE_REF is not pinned to a sha- image (CI pins it; is this the cirrus-dev tree?)"
info "image: $PIN"

# --- 4. render and refuse before touching the cluster ------------------------
render() { helm template "$RELEASE" "$CHART" -f "$CHART/values.yaml" -f "$CHART/values-dev.yaml" -n "$NAMESPACE" "$@" | grep -v '^[[:space:]]*#'; }
WHOLE=$(render)
DEPLOY=$(render -s templates/deployment.yaml)
CRON=$(render -s templates/cronjob-tasks.yaml)
for needle in 'name: samuel$' 'name: samuel-redis$' 'name: samuel-tasks$' 'app: samuel$' \
              'samuel\.k8s\.ucar\.edu' 'sam\.hpc\.ucar\.edu' 'csg/sam-oidc' 'csg/sam-writeuser' \
              'incommon-cert-samuel$'; do
    ! grep -qE "$needle" <<<"$WHOLE" || refuse "render contains a production value: $needle"
done
for m in "$DEPLOY" "$CRON"; do
    grep -qE '^\s+value: "sam_dev"$'           <<<"$m" || refuse "a manifest does not pin SAM_DB_NAME=sam_dev"
    grep -qE '^\s+value: "system_status_dev"$' <<<"$m" || refuse "a manifest does not pin STATUS_DB_NAME=system_status_dev"
done
grep -A1 'name: NOTIFY_ENABLED$' <<<"$DEPLOY" | grep -q 'value: "0"' || refuse "NOTIFY_ENABLED is not \"0\""
grep -A1 'name: XRAS_ACTIONS_CAPTURE_ONLY$' <<<"$DEPLOY" | grep -q 'value: "1"' || refuse "XRAS_ACTIONS_CAPTURE_ONLY is not \"1\""
grep -qE "host: \"?$DEV_HOST\"?" <<<"$WHOLE" || refuse "ingress does not serve $DEV_HOST"
# The tree's own render test is the full ruleset; the checks above are the
# short list this script enforces even if that test is missing or edited.
if [[ -f "$CHART/tests/test-dev-render.sh" ]]; then
    bash "$CHART/tests/test-dev-render.sh" >/dev/null 2>&1 || refuse "$SOURCE_REF's own helm/tests/test-dev-render.sh fails"
fi
echo -e "  ${GREEN}✔${NC} render is the dev overlay ($(grep -c '^kind:' <<<"$WHOLE") objects, $PIN)"

if [[ $RENDER_ONLY -eq 1 ]]; then
    info "--render-only: not touching the cluster"
    exit 0
fi

# --- 5. deploy ---------------------------------------------------------------
helm upgrade --install "$RELEASE" "$CHART" \
    -f "$CHART/values.yaml" -f "$CHART/values-dev.yaml" \
    -n "$NAMESPACE" --kube-context "$CONTEXT" --atomic --timeout 10m
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status "deploy/$RELEASE" --timeout=5m
echo -e "  ${GREEN}✔${NC} $RELEASE deployed from $SOURCE_REF ($PIN)"

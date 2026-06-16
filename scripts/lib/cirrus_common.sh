# shellcheck shell=bash
#
# cirrus_common.sh — Kubernetes / cirrus layer shared by the SAM cluster
# scripts (cirrus_healthcheck.sh, cirrus_weblog_audit.sh).
#
# Sits ON TOP of common.sh (sourced automatically here) and adds the bits
# that know about the 'samuel' release on the nwc1 cluster:
#
#   - baked-in release/object names + defaults (NAMESPACE/RELEASE/CONTEXT)
#   - build_kctl            populate KCTL / KCTL_NS command arrays
#   - handle_common_arg     parse the shared -n/-r/--context/--no-color/-v/-h
#                           flags; return 1 for flags the caller owns
#   - human_bytes / to_cores / to_bytes / seconds_since
#                           K8s resource-unit + RFC3339 conversions
#
# Source from a script in scripts/ with:
#
#   _LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
#   # shellcheck source=lib/cirrus_common.sh
#   source "${_LIBDIR}/cirrus_common.sh"

_CIRRUS_COMMON_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${_CIRRUS_COMMON_DIR}/common.sh"

# --- release / object names (match helm/templates/*.yaml) -------------------
# If you rename objects in the chart, update these in lockstep. Resource
# limits are always read live from the pod spec, never hard-coded here.
NAMESPACE="${NAMESPACE:-sam-queries}"
RELEASE="${RELEASE:-samuel}"
CONTEXT="${CONTEXT:-}"

WEBAPP_NAME="samuel"
WEBAPP_PORT=5050
REDIS_NAME="samuel-redis"
REDIS_PORT=6379
INGRESS_HOST="samuel.k8s.ucar.edu"
TLS_SECRET="incommon-cert-samuel"
HEALTH_PATH="/api/v1/health/ready"

# --------------------------------------------------------------------------
# build_kctl
#
# Populate the KCTL and KCTL_NS arrays from the current CONTEXT / NAMESPACE.
# Call AFTER argument parsing (so --context / -n are in effect):
#
#   "${KCTL[@]}"    get nodes          # cluster-scoped
#   "${KCTL_NS[@]}" get pods           # namespace-scoped
# --------------------------------------------------------------------------
build_kctl() {
    KCTL=(kubectl)
    [[ -n "$CONTEXT" ]] && KCTL+=(--context "$CONTEXT")
    KCTL_NS=("${KCTL[@]}" -n "$NAMESPACE")
}

# --------------------------------------------------------------------------
# handle_common_arg "$@"
#
# Process one leading argument if it is a shared flag. On a match, sets
# _CONSUMED to the number of tokens used (1 or 2) and returns 0; on a flag the
# caller owns, returns 1 (caller's own case handles it). Usage:
#
#   while [[ $# -gt 0 ]]; do
#       if handle_common_arg "$@"; then shift "$_CONSUMED"; continue; fi
#       case "$1" in
#           --since) SINCE="$2"; shift 2;;
#           *) echo "Unknown option: $1" >&2; exit 2;;
#       esac
#   done
# --------------------------------------------------------------------------
handle_common_arg() {
    _CONSUMED=0
    case "$1" in
        -n|--namespace) NAMESPACE="$2"; _CONSUMED=2;;
        -r|--release)   RELEASE="$2";   _CONSUMED=2;;
        --context)      CONTEXT="$2";   _CONSUMED=2;;
        --no-color)     USE_COLOR=0;    _CONSUMED=1;;
        -v|--verbose)   VERBOSE=1;      _CONSUMED=1;;
        -h|--help)      usage_from_header "$0"; exit 0;;
        *) return 1;;
    esac
    return 0
}

# --- K8s resource-unit + timestamp conversions -----------------------------

human_bytes() {
    awk -v b="$1" 'BEGIN{
        split("B KB MB GB TB PB",u);
        i=1; while (b>=1024 && i<6){ b/=1024; i++ }
        printf "%.1f%s", b, u[i]
    }'
}

# Strip K8s resource units to a plain number.
# CPU: 16 -> 16 cores, 250m -> 0.25, 100000000n -> 0.1
to_cores() {
    local v="$1"
    case "$v" in
        *n) awk -v x="${v%n}" 'BEGIN{printf "%.3f", x/1e9}';;
        *u) awk -v x="${v%u}" 'BEGIN{printf "%.3f", x/1e6}';;
        *m) awk -v x="${v%m}" 'BEGIN{printf "%.3f", x/1000}';;
        *)  awk -v x="$v"     'BEGIN{printf "%.3f", x+0}';;
    esac
}
# Mem: 128Gi -> 128*1024^3, 4096Mi -> 4096*1024^2, 4096M -> 4096*1e6
to_bytes() {
    local v="$1"
    case "$v" in
        *Ki) awk -v x="${v%Ki}" 'BEGIN{printf "%.0f", x*1024}';;
        *Mi) awk -v x="${v%Mi}" 'BEGIN{printf "%.0f", x*1024*1024}';;
        *Gi) awk -v x="${v%Gi}" 'BEGIN{printf "%.0f", x*1024*1024*1024}';;
        *Ti) awk -v x="${v%Ti}" 'BEGIN{printf "%.0f", x*1024*1024*1024*1024}';;
        *K)  awk -v x="${v%K}"  'BEGIN{printf "%.0f", x*1000}';;
        *M)  awk -v x="${v%M}"  'BEGIN{printf "%.0f", x*1000000}';;
        *G)  awk -v x="${v%G}"  'BEGIN{printf "%.0f", x*1000000000}';;
        *T)  awk -v x="${v%T}"  'BEGIN{printf "%.0f", x*1000000000000}';;
        *)   awk -v x="$v"      'BEGIN{printf "%.0f", x+0}';;
    esac
}

# Convert RFC3339 timestamp to seconds-ago via portable date(1).
# Echoes a single integer in seconds; non-zero exit if unparseable.
seconds_since() {
    awk -v d="$1" 'BEGIN{
        cmd="date -u +%s"; cmd | getline now; close(cmd);
        cmd="date -u -d \"" d "\" +%s 2>/dev/null || date -u -j -f %Y-%m-%dT%H:%M:%SZ \"" d "\" +%s 2>/dev/null"
        cmd | getline t; close(cmd);
        if (t=="" || t==0) exit 1;
        printf "%.0f", (now-t)
    }'
}

# shellcheck shell=bash
#
# cirrus_common.sh — Kubernetes / cirrus layer shared by the SAM cluster
# scripts (cirrus_healthcheck.sh, cirrus_watch.sh, cirrus_weblog_audit.sh).
#
# Sits ON TOP of common.sh (sourced automatically here) and adds the bits
# that know about the 'samuel' (prod, namespace sam-queries) and 'samuel-dev'
# (dev, namespace sam-queries-dev, Argo app sam-query-dev) releases on nwc1:
#
#   - cirrus_set_env        namespace + release/object names per SAM_ENV
#                           (prod | dev), applied at source time and again by --env
#   - build_kctl            populate KCTL / KCTL_NS command arrays
#   - handle_common_arg     parse the shared --env/-n/-r/--context/--no-color/-v/-h
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

# --- release / object names (match helm/values*.yaml) -----------------------
# One row per environment; if you rename objects in the chart, update the row
# in lockstep. Resource limits are always read live from the pod spec, never
# hard-coded here.
# An explicit namespace (env var or -n) wins over the per-env default.
_NAMESPACE_EXPLICIT="${NAMESPACE:+1}"
CONTEXT="${CONTEXT:-}"
WEBAPP_PORT=5050
REDIS_PORT=6379
# The dispatcher wakes hourly (helm tasks.schedule "7 * * * *"), so anything
# past one interval plus slack means it has stopped being scheduled.
TASKS_MAX_SILENCE_S=4200
HEALTH_PATH="/api/v1/health/ready"

# cirrus_set_env <prod|dev>
#
# INGRESS_HOST is the platform-primary name (helm webapp.tls.fqdn) and the CN of
# the issued cert; INGRESS_HOSTS is every name the ingress answers for (fqdn +
# extraHosts), all on the one multi-SAN TLS_SECRET. The task pods carry
# `app: <tasks.name>` — NOT `app.kubernetes.io/component=tasks`, which matches
# nothing and reads as "the dispatcher never fired". XRAS_ES_EXPECTED says
# whether the chart syncs the XRAS API key for this env (values-dev.yaml turns
# it off). DEFAULT_WATCH_DB_HOST empty = no XRAS/db-load reads (dev SAM is
# Postgres and XRAS never posts to dev).
cirrus_set_env() {
    SAM_ENV="$1"
    case "$SAM_ENV" in
        prod)
            ENV_NAMESPACE="sam-queries"
            RELEASE="samuel"
            WEBAPP_NAME="samuel"
            REDIS_NAME="samuel-redis"
            TASKS_NAME="samuel-tasks"
            INGRESS_HOST="samuel.k8s.ucar.edu"
            INGRESS_HOSTS=("samuel.k8s.ucar.edu" "sam.hpc.ucar.edu")
            TLS_SECRET="incommon-cert-samuel"
            XRAS_ES_EXPECTED=1
            DEFAULT_WATCH_DB_HOST="sam-sql.ucar.edu"
            ;;
        dev)
            ENV_NAMESPACE="sam-queries-dev"
            RELEASE="samuel-dev"
            WEBAPP_NAME="samuel-dev"
            REDIS_NAME="samuel-dev-redis"
            TASKS_NAME="samuel-dev-tasks"
            INGRESS_HOST="samuel-dev.k8s.ucar.edu"
            INGRESS_HOSTS=("samuel-dev.k8s.ucar.edu")
            TLS_SECRET="incommon-cert-samuel-dev"
            XRAS_ES_EXPECTED=0
            DEFAULT_WATCH_DB_HOST=""
            ;;
        *) echo "cirrus_common.sh: unknown SAM_ENV '$SAM_ENV' (prod|dev)" >&2; exit 2;;
    esac
    TASKS_SELECTOR="app=${TASKS_NAME}"
    [[ -n "$_NAMESPACE_EXPLICIT" ]] || NAMESPACE="$ENV_NAMESPACE"
}
cirrus_set_env "${SAM_ENV:-prod}"

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
        # Re-applies the whole name table, so put --env before -r/--ingress-host.
        --env)          cirrus_set_env "$2"; _CONSUMED=2;;
        -n|--namespace) NAMESPACE="$2"; _NAMESPACE_EXPLICIT=1; _CONSUMED=2;;
        -r|--release)   RELEASE="$2";   _CONSUMED=2;;
        --context)      CONTEXT="$2";   _CONSUMED=2;;
        # Narrow edge checks to ONE host (both the canonical name and the
        # iteration list), e.g. to probe an alias in isolation.
        --ingress-host) INGRESS_HOST="$2"; INGRESS_HOSTS=("$2"); _CONSUMED=2;;
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

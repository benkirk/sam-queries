#!/bin/bash
# cirrus_redis_purge.sh — deploy-time cache flush for the samuel Redis on nwc1.
#
# The FIRST mutating script in scripts/ (cirrus_healthcheck.sh and
# cirrus_weblog_audit.sh are deliberately read-only), so it is guarded:
# the default run is a read-only DRY-RUN that surveys the keyspace and
# prints what WOULD be removed; nothing is deleted without --yes.
#
# Use case: cache-namespace cutovers and deploy-window flushes (the CSP
# rollout set the pattern). The app rebuilds every cache on demand by
# design, so a flush costs only warm-up time. Example: the retirement of
# the shared 'usage:' RedisTTLAdapter prefix (docs/plans/
# REDIS_CACHE_PREFIXES.md) orphans all pre-cutover usage:* keys — flush
# them at deploy instead of letting them sit until TTL (up to 8 days).
#
# Scope: Redis DB 0 only — the app's cache keyspace. Rate-limiter data
# lives in DB 1 and is NEVER touched by this script.
#
# Deletion is performed server-side via a Lua SCAN+DEL (or FLUSHDB): the
# TTL-adapter keys are prefix + raw pickle bytes, which do not survive a
# round-trip through `redis-cli --scan | xargs redis-cli del`.
#
# Usage:
#   scripts/cirrus_redis_purge.sh [options]
#
# Options:
#   --yes                 Actually delete (default is dry-run)
#   --pattern GLOB        Targeted SCAN+DEL of matching keys instead of
#                         FLUSHDB (e.g. --pattern 'usage:*')
#   -n, --namespace NS    Namespace the release lives in   (default: sam-queries)
#   -r, --release    REL  Helm release name                (default: samuel)
#       --context    CTX  kubectl context to target        (default: current)
#       --no-color        Disable ANSI color
#   -v, --verbose         Extra detail
#   -h, --help            Show this help

set -euo pipefail

_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/cirrus_common.sh
source "${_LIBDIR}/cirrus_common.sh"

CONFIRM=0
PATTERN=""

while [[ $# -gt 0 ]]; do
    if handle_common_arg "$@"; then shift "$_CONSUMED"; continue; fi
    case "$1" in
        --yes)     CONFIRM=1;    shift;;
        --pattern) PATTERN="$2"; shift 2;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done

setup_colors
build_kctl
require_cmd kubectl

# Cache DB only; the rate limiter's DB 1 is out of bounds by design.
REDIS_DB=0

# Known keyspaces, for the survey display only (deletion never depends on
# this list). Mirrors flask_adapter._FOREIGN_PREFIXES + the flask-cache and
# legacy namespaces.
SURVEY_PREFIXES=(
    'flask_cache_*'
    'chart:*'
    'allocation_usage:*'
    'fs_scans:*'
    'fs_scans_filtered:*'
    'jobs:*'
    'jobs_recent:*'
    'usage:*'          # legacy (pre name-derived prefixes) — orphans after cutover
)

# Server-side Lua so binary (pickle-suffixed) keys never round-trip through
# the shell. COUNT-only and DEL variants share the SCAN loop shape.
LUA_COUNT='local c="0" local n=0 repeat local r=redis.call("SCAN",c,"MATCH",ARGV[1],"COUNT",500) c=r[1] n=n+#r[2] until c=="0" return n'
LUA_DEL='local c="0" local n=0 repeat local r=redis.call("SCAN",c,"MATCH",ARGV[1],"COUNT",500) c=r[1] for _,k in ipairs(r[2]) do redis.call("DEL",k) n=n+1 end until c=="0" return n'

rcli() {
    "${KCTL_NS[@]}" exec "$REDIS_POD" -- redis-cli -p "$REDIS_PORT" -n "$REDIS_DB" "$@"
}

section "Target"
CUR_CTX=$("${KCTL[@]}" config current-context 2>/dev/null || echo "<none>")
info "context=${CUR_CTX}  namespace=${NAMESPACE}  db=${REDIS_DB}"

REDIS_POD=$("${KCTL_NS[@]}" get pod -l "app=$REDIS_NAME" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
[[ -n "$REDIS_POD" ]] || die "no Redis pod found via selector app=$REDIS_NAME in namespace $NAMESPACE"
info "pod=${REDIS_POD}"

PONG=$(rcli ping 2>&1 | tr -d '\r') || die "redis-cli ping failed: $PONG"
[[ "$PONG" == "PONG" ]] || die "Redis returned '$PONG' instead of PONG"

section "Keyspace survey (read-only)"
DBSIZE=$(rcli dbsize | tr -d '\r')
say "  DBSIZE (db ${REDIS_DB}): ${BOLD}${DBSIZE}${NC} keys"
accounted=0
for glob in "${SURVEY_PREFIXES[@]}"; do
    n=$(rcli eval "$LUA_COUNT" 0 "$glob" | tr -d '\r')
    accounted=$((accounted + n))
    [[ "$n" -gt 0 || $VERBOSE -eq 1 ]] && say "    $(printf '%-22s' "$glob") ${n}"
done
say "    $(printf '%-22s' '<unmatched>') $((DBSIZE - accounted))"

section "Action"
if [[ -n "$PATTERN" ]]; then
    would=$(rcli eval "$LUA_COUNT" 0 "$PATTERN" | tr -d '\r')
    if [[ $CONFIRM -eq 0 ]]; then
        note "DRY-RUN: would SCAN+DEL ${would} key(s) matching '${PATTERN}' — re-run with --yes to execute"
        exit 0
    fi
    deleted=$(rcli eval "$LUA_DEL" 0 "$PATTERN" | tr -d '\r')
    ok=$(rcli dbsize | tr -d '\r')
    say "  deleted ${BOLD}${deleted}${NC} key(s) matching '${PATTERN}'; DBSIZE now ${ok}"
else
    if [[ $CONFIRM -eq 0 ]]; then
        note "DRY-RUN: would FLUSHDB (all ${DBSIZE} keys in db ${REDIS_DB}) — re-run with --yes to execute"
        exit 0
    fi
    rcli flushdb >/dev/null
    ok=$(rcli dbsize | tr -d '\r')
    say "  FLUSHDB done; DBSIZE now ${ok} (caches rebuild on demand)"
fi

#!/bin/bash
# cirrus_logs.sh — read the durable log archive of the sam-queries release on
# nwc1: every samuel pod's stdout, kept after the pod is gone (a deploy, a
# finished task Job). Read-only: one `kubectl exec` into the archiver pod, which
# runs `python -m logarchive cat` (design: docs/plans/implemented/DURABLE_LOGS.md).
#
# Output is what `kubectl logs` prints, merged across pods in time order, so the
# awk parsers in cirrus_watch.sh and cirrus_weblog_audit.sh read it unchanged.
#
# Usage:
#   scripts/cirrus_logs.sh [options] [-- grep-args]
#
# Options:
#       --since WHEN   Start of the window: 48h, 7d, or an RFC3339 time (default: 1h)
#       --until WHEN   End of the window, same forms                  (default: now)
#       --app   WHICH  webapp | tasks | all                           (default: webapp)
#       --timestamps   Keep each line's leading UTC timestamp
#       --env     ENV  Which release: prod | dev            (default: $SAM_ENV or prod)
#   -n, --namespace NS Namespace the release lives in       (default: per --env)
#       --context CTX  kubectl context to target            (default: current)
#   -h, --help         Show this help
#   --                 Pass everything after it to grep; the exit status is grep's
#
# Examples:
#   scripts/cirrus_logs.sh --env dev --since 7d -- -F rate_limit_exceeded
#   scripts/cirrus_logs.sh --app tasks --since 3d --timestamps
#
# When the archiver is not deployed (helm logArchiver.enabled=false) it says so
# on stderr and exits 0. Exit 2: kubectl could not reach the namespace.

set -euo pipefail

_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/cirrus_common.sh
source "${_LIBDIR}/cirrus_common.sh"

SINCE="1h"
UNTIL=""
APP="webapp"
TIMESTAMPS=0
GREP_ARGS=()

while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--" ]]; then shift; GREP_ARGS=("$@"); break; fi
    if handle_common_arg "$@"; then shift "$_CONSUMED"; continue; fi
    case "$1" in
        --since)      SINCE="$2"; shift 2;;
        --until)      UNTIL="$2"; shift 2;;
        --app)        APP="$2";   shift 2;;
        --timestamps) TIMESTAMPS=1; shift;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done

build_kctl

case "$APP" in
    webapp) APPS=(--app "$WEBAPP_NAME");;
    tasks)  APPS=(--app "$TASKS_NAME");;
    all)    APPS=(--app "$WEBAPP_NAME" --app "$TASKS_NAME");;
    *) echo "--app must be webapp, tasks or all (got '$APP')" >&2; exit 2;;
esac

# The StatefulSet has one replica, so its pod has a fixed name.
POD="${LOGS_NAME}-0"
if ! ERR=$("${KCTL_NS[@]}" --request-timeout=10s get pod "$POD" -o name 2>&1 >/dev/null); then
    if grep -q NotFound <<<"$ERR"; then
        echo "cirrus_logs.sh: no archiver pod '$POD' in $NAMESPACE (logArchiver.enabled=false?)." >&2
        echo "  Live pods only: kubectl -n $NAMESPACE logs -l app=$WEBAPP_NAME --since=$SINCE --tail=-1 --all-containers=true" >&2
        exit 0
    fi
    echo "cirrus_logs.sh: cannot read $NAMESPACE (VPN/RBAC?): $ERR" >&2
    exit 2
fi

CMD=("${KCTL_NS[@]}" exec "$POD" -- python -m logarchive cat "${APPS[@]}" --since "$SINCE")
[[ -n "$UNTIL" ]] && CMD+=(--until "$UNTIL")
[[ "$TIMESTAMPS" -eq 1 ]] && CMD+=(--timestamps)

if [[ ${#GREP_ARGS[@]} -gt 0 ]]; then
    "${CMD[@]}" | grep "${GREP_ARGS[@]}"
else
    "${CMD[@]}"
fi

#!/bin/bash
# Comp charge summaries from the job_history DB into SAM.
# Usage: accounting-comp.sh [sam-admin accounting args]   (default: --last 2d)
# Fallback ladder, as the host cron did it: plain -> --create-queues -> --skip-errors.
case "${NCAR_HOST}" in
    casper|derecho) ;;
    *) echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}" >&2; exit 2 ;;
esac
[[ $# -eq 0 ]] && set -- --last 2d
TIMEFORMAT='(%3R seconds elapsed)'
export PYTHONWARNINGS="${PYTHONWARNINGS},ignore::RuntimeWarning:importlib._bootstrap" COLUMNS=1024

base=(sam-admin accounting --machine "${NCAR_HOST}" "$@" --comp --verbose)
for extra in "" "--create-queues" "--create-queues --skip-errors"; do
    echo "# ${base[*]} ${extra}"
    # shellcheck disable=SC2086
    time "${base[@]}" ${extra} && exit 0
done
echo "All fallbacks failed"
exit 2

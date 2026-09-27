#!/bin/bash
# One pass of the status collectors (POST to STATUS_API_URL) for this host.
# Usage: collectors.sh [--dry-run|--json-only ...]  (passed to each collector)
# Same per-collector timeout and naive-UTC clock as collectors/run_collectors.sh.
export TZ=UTC
case "${NCAR_HOST}" in
    casper)  collectors=(derecho casper jupyterhub) ;;
    derecho) collectors=(derecho) ;;
    *) echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}" >&2; exit 2 ;;
esac
log_dir="${NHD_LOGS:-.}/collectors"
mkdir -p "${log_dir}"
rc=0
for c in "${collectors[@]}"; do
    t0=$(date +%s)
    # stdout duplicates the collector's own log file; stderr keeps pre-logging crashes.
    if timeout 1m python3 /code/collectors/"${c}"/collector.py --log-file="${log_dir}/${c}.log" "$@" \
            > /dev/null 2>> "${log_dir}/${c}.log"; then
        echo "  ok   ${c} ($(( $(date +%s) - t0 ))s)"
    else
        s=$?; rc=2
        echo "  FAIL ${c} exit=${s} ($(( $(date +%s) - t0 ))s; see ${log_dir}/${c}.log)"
    fi
done
exit ${rc}

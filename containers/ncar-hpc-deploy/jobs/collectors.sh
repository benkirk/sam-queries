#!/bin/bash
# One pass of the status collectors (POST to STATUS_API_URL) for this host.
# Usage: collectors.sh [--strict] [--dry-run|--json-only ...]  (rest passed to each collector)
# --strict fails a collector that logged any ERROR: a collector whose every ssh fails
# still exits 0, so exit status alone cannot gate an image.
# Same per-collector timeout and naive-UTC clock as collectors/run_collectors.sh.
export TZ=UTC
case "${NCAR_HOST}" in
    casper)  collectors=(derecho casper jupyterhub) ;;
    derecho) collectors=(derecho) ;;
    *) echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}" >&2; exit 2 ;;
esac
strict=0
[[ "$1" == --strict ]] && { strict=1; shift; }
log_dir="${NHD_LOGS:-.}/collectors"
mkdir -p "${log_dir}"
rc=0
for c in "${collectors[@]}"; do
    t0=$(date +%s)
    log="${log_dir}/${c}.log"
    lines_before=$(cat "${log}" 2>/dev/null | wc -l)
    # stdout duplicates the collector's own log file; stderr keeps pre-logging crashes.
    timeout 1m python3 /code/collectors/"${c}"/collector.py --log-file="${log}" "$@" > /dev/null 2>> "${log}"
    s=$?
    if (( s == 0 && strict )) && tail -n +"$((lines_before + 1))" "${log}" | grep -q ' ERROR '; then
        s=3
    fi
    if (( s == 0 )); then
        echo "  ok   ${c} ($(( $(date +%s) - t0 ))s)"
    else
        rc=2
        echo "  FAIL ${c} exit=${s} ($(( $(date +%s) - t0 ))s; see ${log})"
    fi
done
exit ${rc}

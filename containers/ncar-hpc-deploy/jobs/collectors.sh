#!/bin/bash
# Parse this host's collector spools and POST to STATUS_API_URL.
# The host already scraped them (ncar-hpc-deploy prejob_collectors, bash only):
# the container has no ssh identity and no PBS client.
# Usage: collectors.sh [--strict] [--dry-run|--json-only ...]  (passed to each collector)
# Env: NHD_COLLECTORS (systems for this host), NHD_SPOOL (spool root).
export TZ=UTC   # collectors write naive-UTC timestamps, as collectors/run_collectors.sh does
[[ -n "${NHD_COLLECTORS}" && -n "${NHD_SPOOL}" ]] \
    || { echo "ERROR: NHD_COLLECTORS/NHD_SPOOL unset; run via ncar-hpc-deploy" >&2; exit 2; }
log_dir="${NHD_LOGS:-.}/collectors"
mkdir -p "${log_dir}"
rc=0
for c in ${NHD_COLLECTORS}; do
    t0=$(date +%s)
    log="${log_dir}/${c}.log"
    # stdout duplicates the collector's own log file; stderr keeps pre-logging crashes.
    timeout 1m python3 /code/collectors/"${c}"/collector.py --spool "${NHD_SPOOL}/${c}" \
        --log-file="${log}" "$@" > /dev/null 2>> "${log}"
    s=$?
    if (( s == 0 )); then
        echo "  ok   ${c} ($(( $(date +%s) - t0 ))s)"
    else
        rc=2
        echo "  FAIL ${c} exit=${s} ($(( $(date +%s) - t0 ))s; see ${log})"
    fi
done
exit ${rc}

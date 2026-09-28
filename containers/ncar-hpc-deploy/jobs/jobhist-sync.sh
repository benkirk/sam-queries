#!/bin/bash
# PBS accounting logs -> job_history DB (hpc-usage-queries jobhist-sync).
# Usage: jobhist-sync.sh rapid|daily|weekly [extra jobhist-sync args, e.g. --dry-run]
case "${NCAR_HOST}" in
    casper)  log_path=/ssg/pbs/casper/accounting ;;
    derecho) log_path=/ncar/pbs/accounting ;;
    *) echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}" >&2; exit 2 ;;
esac
mode="${1:-rapid}"; shift
case "${mode}" in
    rapid)  sync_args=(--verbose --today --incremental) ;;
    daily)  sync_args=(--last 2d --upsert) ;;
    weekly) sync_args=(--last 14d --upsert) ;;
    *) echo "ERROR: mode must be rapid|daily|weekly, not '${mode}'" >&2; exit 2 ;;
esac
[[ -d "${log_path}" ]] || { echo "ERROR: ${log_path} not visible in the container" >&2; exit 2; }
TIMEFORMAT='(%3R seconds elapsed)'
time jobhist-sync -m "${NCAR_HOST}" -l "${log_path}" "${sync_args[@]}" "$@"

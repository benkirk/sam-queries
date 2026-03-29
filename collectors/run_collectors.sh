#!/usr/bin/env bash
#
# Loop-runner for HPC collectors, aligned to 5-minute intervals
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTERVAL=300    # 5 minutes
TIMEOUT="1m"    # timeout for each collector
LOG_DIR="${SCRIPT_DIR}/logs"

mkdir -p "$LOG_DIR"

# Parse arguments
RUN_DERECHO=0
RUN_CASPER=0
RUN_JUPYTERHUB=0
RUN_ONCE=0

usage() {
    echo "Usage: $(basename "$0") [--derecho] [--casper] [--jupyterhub] [--once]"
    echo ""
    echo "  --derecho      Run the Derecho collector"
    echo "  --casper       Run the Casper collector"
    echo "  --jupyterhub   Run the JupyterHub collector"
    echo "  --once         Run selected collectors once instead of looping"
    echo ""
    echo "  Default (no flags): run all collectors in a loop"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --derecho)    RUN_DERECHO=1 ;;
        --casper)     RUN_CASPER=1 ;;
        --jupyterhub) RUN_JUPYTERHUB=1 ;;
        --once)       RUN_ONCE=1 ;;
        --help|-h)    usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
    shift
done

# Default: run all collectors if none specified
if [[ $RUN_DERECHO -eq 0 && $RUN_CASPER -eq 0 && $RUN_JUPYTERHUB -eq 0 ]]; then
    RUN_DERECHO=1
    RUN_CASPER=1
    RUN_JUPYTERHUB=1
fi

# Build collector list dynamically
COLLECTORS=()
[[ $RUN_DERECHO    -eq 1 ]] && COLLECTORS+=("Derecho:derecho/collector.py")
[[ $RUN_CASPER     -eq 1 ]] && COLLECTORS+=("Casper:casper/collector.py")
[[ $RUN_JUPYTERHUB -eq 1 ]] && COLLECTORS+=("JupyterHub:jupyterhub/collector.py")

cat <<EOF
============================================================
HPC Status Collectors - Starting
Interval: ${INTERVAL}s ($(($INTERVAL / 60)) minutes)
Timeout: ${TIMEOUT}
Log directory: $LOG_DIR
============================================================

EOF

while true; do
    loop_start=$(date +%s)
    next_run=$(( loop_start + INTERVAL ))

    echo "==== Collection started at $(date) ===="

    # Collector loop
    for entry in "${COLLECTORS[@]}"; do
        IFS=":" read -r NAME COLLECTOR <<< "$entry"
        LOGFILE="${LOG_DIR}/${NAME}.log"

        echo "  - ${NAME}..."

        t0=$(date +%s)
        if timeout "${TIMEOUT}" "./${COLLECTOR}" --log-file="${LOGFILE}" > /dev/null 2>&1; then
            elapsed=$(( $(date +%s) - t0 ))
            echo "    ✓ ${NAME} completed in ${elapsed}s"
        else
            status=$?
            elapsed=$(( $(date +%s) - t0 ))
            if [[ $status -eq 124 ]]; then
                echo "    ✗ ${NAME} TIMED OUT in ${elapsed}s"
            else
                echo "    ✗ ${NAME} failed in ${elapsed}s (see ${LOGFILE})"
            fi
        fi
    done

    echo ""

    # Exit after one pass if --once was requested
    if [[ $RUN_ONCE -eq 1 ]]; then
        break
    fi

    # Sleep remainder of interval
    now=$(date +%s)
    sleep_time=$(( next_run - now ))

    if (( sleep_time > 0 )); then
        echo "  Sleeping for ${sleep_time}s until next interval..."
        sleep "${sleep_time}"
    else
        echo "  Collectors overran the interval — starting next cycle immediately."
    fi

    echo ""
done

#!/usr/bin/env bash
#
# Loop-runner for HPC collectors, aligned to 5-minute intervals
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTERVAL=300    # 5 minutes
TIMEOUT="2m"    # timeout for each collector
LOG_DIR="${SCRIPT_DIR}/logs"

mkdir -p "$LOG_DIR"

# Define collectors as "name:relative_path"
COLLECTORS=(
    "Derecho:derecho/collector.py"
    "Casper:casper/collector.py"
    "JupyterHub:jupyterhub/collector.py"
)

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

        if timeout "${TIMEOUT}" "./${COLLECTOR}" --log-file="${LOGFILE}" > /dev/null 2>&1; then
            echo "    ✓ ${NAME} completed"
        else
            status=$?
            if [[ $status -eq 124 ]]; then
                echo "    ✗ ${NAME} TIMED OUT after ${TIMEOUT}"
            else
                echo "    ✗ ${NAME} failed (see ${LOGFILE})"
            fi
        fi
    done

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

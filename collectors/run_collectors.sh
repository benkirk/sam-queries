#!/bin/bash
#
# Simple while-loop runner for HPC collectors
# Runs Derecho and Casper collectors every 5 minutes
#

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Configuration
INTERVAL=300  # 5 minutes in seconds
LOG_DIR="${SCRIPT_DIR}/logs"

# Create log directory
mkdir -p "$LOG_DIR"

echo "============================================================"
echo "HPC Status Collectors - Starting"
echo "Interval: ${INTERVAL}s ($(($INTERVAL / 60)) minutes)"
echo "Log directory: $LOG_DIR"
echo "============================================================"
echo ""

# Trap Ctrl+C
trap 'echo ""; echo "Shutting down collectors..."; exit 0' INT TERM

# Main collection loop
while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] Running collectors..."

    # Run Derecho collector
    echo "  - Derecho..."
    if ./derecho/collector.py --log-file="$LOG_DIR/derecho.log" > /dev/null 2>&1; then
        echo "    ✓ Derecho completed"
    else
        echo "    ✗ Derecho failed (see $LOG_DIR/derecho.log)"
    fi

    # Run Casper collector
    echo "  - Casper..."
    if ./casper/collector.py --log-file="$LOG_DIR/casper.log" > /dev/null 2>&1; then
        echo "    ✓ Casper completed"
    else
        echo "    ✗ Casper failed (see $LOG_DIR/casper.log)"
    fi

    echo "  Done! Sleeping for ${INTERVAL}s..."
    echo ""

    sleep "$INTERVAL"
done

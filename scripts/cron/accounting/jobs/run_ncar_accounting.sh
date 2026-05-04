#!/bin/bash
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

unset machine
unset log_path

case "${NCAR_HOST}" in
    "casper")
        machine="${NCAR_HOST}"
        ;;
    "derecho")
        machine="${NCAR_HOST}"
        ;;
    *)
        echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}"
        exit 1
        ;;
esac

# CLI passthrough: use "--last 2d" default when no args given, otherwise pass args directly
if [ $# -eq 0 ]; then
    accounting_args="--last 2d"
else
    accounting_args="$*"
fi

# time \
#     1>${machine}-dryrun.log \
#     2>${machine}-dryrun.err \
#     sam-admin accounting --machine ${machine} ${accounting_args} --comp --verbose --dry-run

# first try - clean, no args
{ time \
    1>${machine}-accounting.log \
    2>${machine}-accounting.err \
    sam-admin accounting --machine ${machine} ${accounting_args} --comp --verbose; } 2>&1 && exit 0

# if we get here, something above failed.  Try again creating queues
{ time \
    1>${machine}-accounting-create-queues.log \
    2>${machine}-accounting-create-queues.err \
    sam-admin accounting --machine ${machine} ${accounting_args} --comp --verbose --create-queues; } 2>&1 && exit 0

# if we get here, something above still failed.  Try again skipping errors
{ time \
    1>${machine}-accounting-skip-errors.log \
    2>${machine}-accounting-skip-errors.err \
    sam-admin accounting --machine ${machine} ${accounting_args} --comp --verbose --create-queues --skip-errors; } 2>&1 && exit 0

echo "All fallbacks failed"
exit 1

#!/bin/bash

#----------------------------------------------------------------------------
# Determine the directory containing this script, compatible with bash and zsh
if [ -n "${BASH_SOURCE[0]}" ]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION}" ]; then
  SCRIPT_PATH="${(%):-%x}"
else
  echo "Unknown shell!"
fi
SCRIPT_DIR=$(realpath $(dirname ${SCRIPT_PATH}))
TOP_DIR=$(git rev-parse --show-toplevel)
#----------------------------------------------------------------------------

sep="#----------------------------------------------------------------------------"
TIMEFORMAT='(%3R seconds elapsed)'

source ${TOP_DIR}/etc/config_env.sh
export PYTHONWARNINGS="${PYTHONWARNINGS},ignore::RuntimeWarning:importlib._bootstrap"

#which python3
#which jobhist-sync

# Source backend-specific env file (overrides any .env settings)
env_file="${SCRIPT_DIR}/.env"
set -a
source "${env_file}" && echo "Loaded .env from ${env_file}" || { echo "Could not source ${env_file}!"; exit 1; }
set +a
cat <<EOF
${sep}
# $(date)
# (${env_file})
# SAM_DB_SERVER=${SAM_DB_SERVER}
# SAM_DB_USERNAME=${SAM_DB_USERNAME}
${sep}

EOF

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

# time \
#     1>${machine}-dryrun.log \
#     2>${machine}-dryrun.err \
#     sam-admin accounting --machine ${machine} --last 2d --comp --verbose --dry-run

# first try - clean, no args
time \
    1>${machine}-accounting.log \
    2>${machine}-accounting.err \
    sam-admin accounting --machine ${machine} --last 2d --comp --verbose && exit 0

# if we get here, something above failed.  Try again creating queues
time \
    1>${machine}-accounting-create-queues.log \
    2>${machine}-accounting-create-queues.err \
    sam-admin accounting --machine ${machine} --last 2d --comp --verbose --create-queues && exit 0

# if we get here, something above still failed.  Try again skipping errors
time \
    1>${machine}-accounting-skip-errors.log \
    2>${machine}-accounting-skip-errors.err \
    sam-admin accounting --machine ${machine} --last 2d --comp --verbose --create-queues --skip-errors && exit 0

echo "All fallbacks failed"
exit 1

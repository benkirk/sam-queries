#!/bin/bash
# Usage: run_ncar_accounting.sh --resource <Resource> --user-usage <file> [extra sam-admin args...]
# Runs sam-admin accounting --disk with env setup, passing all args through.

if [ -n "${BASH_SOURCE[0]}" ]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION}" ]; then
  SCRIPT_PATH="${(%):-%x}"
else
  echo "Unknown shell!"; exit 1
fi
SCRIPT_DIR=$(realpath $(dirname ${SCRIPT_PATH}))
TOP_DIR=$(git rev-parse --show-toplevel)

sep="#----------------------------------------------------------------------------"
TIMEFORMAT='(%3R seconds elapsed)'

source ${TOP_DIR}/etc/config_env.sh
export PYTHONWARNINGS="${PYTHONWARNINGS},ignore::RuntimeWarning:importlib._bootstrap"
export COLUMNS=1024

env_file="${SCRIPT_DIR}/../.env"
set -a
source "${env_file}" || { echo "Could not source ${env_file}!"; exit 1; }
set +a

cat <<EOF
${sep}
# $(date)
# (${env_file})
# SAM_DB_SERVER=${SAM_DB_SERVER}
# SAM_DB_USERNAME=${SAM_DB_USERNAME}
# args: $@
${sep}

EOF

time sam-admin accounting --disk "$@" --verbose --skip-errors && exit 0

echo "completed with errors."
exit 0

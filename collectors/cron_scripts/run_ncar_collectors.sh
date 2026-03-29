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

case "${NCAR_HOST}" in
    "casper")
        machine="${NCAR_HOST}"
        args="--casper --jupyterhub --once"
        ;;
    "derecho")
        machine="${NCAR_HOST}"
        args="--derecho --once"
        ;;
    *)
        echo "ERROR: unhandled NCAR_HOST=${NCAR_HOST}"
        exit 1
        ;;
esac

time \
    ${TOP_DIR}/collectors/run_collectors.sh ${args}

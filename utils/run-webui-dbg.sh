#!/usr/bin/env bash

#----------------------------------------------------------------------------
# Determine the directory containing this script, compatible with bash and zsh
if [ -n "${BASH_SOURCE[0]}" ]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION}" ]; then
  SCRIPT_PATH="${(%):-%x}"
else
  echo "Unknown shell!"
fi
SCRIPTDIR="$(cd "$(dirname "$(realpath "${SCRIPT_PATH}")")" >/dev/null 2>&1 && pwd)"
#----------------------------------------------------------------------------
source ${SCRIPTDIR}/../etc/config_env.sh

exe=$(realpath ${SCRIPTDIR}/../src/webapp/run.py)

DISABLE_AUTH=1 \
  DEV_AUTO_LOGIN_USER=benkirk \
  FLASK_DEBUG=1 \
  python3 ${exe}

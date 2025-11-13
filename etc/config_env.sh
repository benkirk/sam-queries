#----------------------------------------------------------------------------
# Determine the directory containing this script, compatible with bash and zsh
if [ -n "${BASH_SOURCE[0]}" ]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION}" ]; then
  SCRIPT_PATH="${(%):-%x}"
else
  echo "Unknown shell!"
fi
CONFDIR="$(cd "$(dirname "$(realpath "${SCRIPT_PATH}")")" >/dev/null 2>&1 && pwd)"
#----------------------------------------------------------------------------

ROOT_DIR=$(realpath ${CONFDIR}/..)

ETC_DIR=${ROOT_DIR}/etc
LOG_DIR=${ROOT_DIR}/logs
ENV_NAME=conda-env
ENV_DIR=${ROOT_DIR}/${ENV_NAME}

module try-load conda > /dev/null 2>&1
conda --version > /dev/null 2>&1 || {
    echo "Cannot locate conda?"
    exit 1
}

make --silent -C ${ROOT_DIR} ${ENV_NAME}

conda activate ${ENV_DIR}

# fully specify a new PYTHONPATH
export PYTHONPATH="${ROOT_DIR}/python:"

source ${ROOT_DIR}/.env || { echo "no top-level .env found!"; exit 1; }

# prevent leakage
unset SAM_DB_PASSWORD TEST_SAM_DB_PASSWORD LOCAL_SAM_DB_PASSWORD

#----------------------------------------------------------------------------
# environment
CONFDIR="$( cd "$( dirname "$( realpath ${BASH_SOURCE[0]} )" )" >/dev/null 2>&1 && pwd )"
#----------------------------------------------------------------------------

#ROOT_DIR=$(git rev-parse --show-toplevel)
ROOT_DIR=$(realpath ${CONFDIR}/..)

ETC_DIR=${ROOT_DIR}/etc
LOG_DIR=${ROOT_DIR}/logs
ENV_NAME=conda-env
ENV_DIR=${ROOT_DIR}/${ENV_NAME}

module try-load conda >/dev/null 2>&1
conda --version >/dev/null 2>&1 || { echo "Cannot locate conda?"; exit 1; }

make --silent -C ${ROOT_DIR} ${ENV_NAME}

conda activate ${ENV_DIR}

PYTHONPATH=${ROOT_DIR}/python:${PYTHONPATH}

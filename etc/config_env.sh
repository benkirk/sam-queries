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
#export PYTHONPATH="${ROOT_DIR}/src:"

# walk up directotry tree, looking for .env file
dir="$(realpath ${ROOT_DIR})"

while true; do
    if [[ -f "$dir/.env" ]]; then
        # shellcheck source=/dev/null
        source "$dir/.env" || { echo "Found .env but could not source it!"; exit 1; }
        echo "Loaded .env from $dir"
        break
    fi

    # Stop at the filesystem root
    if [[ "$dir" == "/" ]]; then
        echo "No .env found in any parent directory!"
        exit 1
    fi

    # Go up one directory
    dir=$(dirname "$dir")
done

# prevent leakage
unset SAM_DB_PASSWORD TEST_SAM_DB_PASSWORD LOCAL_SAM_DB_PASSWORD

#!/bin/bash
# Shared prologue for cron accounting scripts.
# Callers: source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"
# On return:
#   - conda env active, ../.env loaded, banner printed
#   - SCRIPT_DIR, TOP_DIR, sep, TIMEFORMAT set
#   - PYTHONWARNINGS, COLUMNS exported

# Caller's script path (not this file's). All callers use #!/bin/bash,
# and cron never sources from zsh, so BASH_SOURCE is sufficient.
SCRIPT_DIR=$(realpath "$(dirname "${BASH_SOURCE[1]}")")
TOP_DIR=$(git rev-parse --show-toplevel)

sep="#----------------------------------------------------------------------------"
TIMEFORMAT='(%3R seconds elapsed)'

source "${TOP_DIR}/etc/config_env.sh"
export PYTHONWARNINGS="${PYTHONWARNINGS},ignore::RuntimeWarning:importlib._bootstrap"
export COLUMNS=1024

env_file="${SCRIPT_DIR}/../.env"
set -a
source "${env_file}" || { echo "Could not source ${env_file}!" >&2; exit 1; }
set +a

cat <<EOF
${sep}
# $(date)
# (${env_file})
# SAM_DB_SERVER=${SAM_DB_SERVER}
# SAM_DB_USERNAME=${SAM_DB_USERNAME}
${sep}

EOF

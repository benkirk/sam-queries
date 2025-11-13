#!/usr/bin/env bash --login

#----------------------------------------------------------------------------
# environment
SCRIPTDIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" > /dev/null 2>&1 && pwd)"
#----------------------------------------------------------------------------

top_dir=$(git rev-parse --show-toplevel 2>&1 || $(realpath ${SCRIPTDIR}/..))

source ${top_dir}/etc/config_env.sh

cat << EOF
#--------------------------------------------------------------------------------
$(date)
top_dir=${top_dir}
CONDA_PREFIX=${CONDA_PREFIX}
PYTHONPATH=${PYTHONPATH}
SAM_DB_USERNAME=${SAM_DB_USERNAME}
SAM_DB_SERVER=${SAM_DB_SERVER}
#--------------------------------------------------------------------------------
EOF

# # Check detault
# mysql -u $SAM_DB_USERNAME -h $SAM_DB_SERVER

# check local (duplicated, subsetted) DB
mysql -u $LOCAL_SAM_DB_USERNAME -h $LOCAL_SAM_DB_SERVER -proot --table sam << EOF || {
SHOW tables;
SELECT * FROM users LIMIT 3;
EOF
    echo -e "\n\nCannot connect to LOCAL_SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}\nMaybe ./containers/sam-sql-dev/docker_start.sh"
    exit 1
}

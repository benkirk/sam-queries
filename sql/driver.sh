#!/bin/bash

ml conda
conda activate /glade/work/benkirk/conda-envs/sam_sql
which mysql
source .env

./gen_context.sh

mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
SHOW FULL TABLES WHERE Table_Type LIKE 'VIEW';
EOF

# set -e
# set -o noglob
#     mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
# show tables;
# EOF

# for table in \
#         account \
#         account_user \
#         allocation \
#         allocation_type \
#         contract \
#         email_address \
#         project \
#         project_code \
#         project_directory \
#         project_organization \
#         users \
#     ; do

#     sql_cmd="DESC ${table}; SELECT COUNT(*) AS row_count FROM ${table}"
#     echo ${sql_cmd}

#     mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
#     ${sql_cmd}
# EOF
# done


# for table in \
#     panel \
#     facility \
#     ; do

#     sql_cmd="DESC ${table}; ; SELECT COUNT(*) AS row_count FROM ${table}; SELECT * FROM ${table} LIMIT 20;"
#     echo ${sql_cmd}

#     mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
#     ${sql_cmd}
# EOF
# done

#queries/list_common_tables.sql

#!/bin/bash

ml conda
conda activate /glade/work/benkirk/conda-envs/sam_sql
which mysql
source .env


set -e
set -o noglob
    mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
show tables;
EOF

for table in \
        account \
        account_user \
        allocation \
        allocation_type \
        contract \
        email_address \
        panel \
        project \
        project_code \
        project_directory \
        project_organization \
        users \
    ; do

    sql_cmd="DESC ${table};"
    echo ${sql_cmd}

    mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
    ${sql_cmd}
EOF
done


for table in \
    facility \
    ; do

    sql_cmd="DESC ${table}; SELECT * FROM ${table} LIMIT 10;"
    echo ${sql_cmd}

    mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
    ${sql_cmd}
EOF
done

#queries/list_common_tables.sql

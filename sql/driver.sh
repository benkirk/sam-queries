#!/bin/bash

ml conda
conda activate /glade/work/benkirk/conda-envs/sam_sql
which mysql
source .env

./gen_context.sh

mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
SHOW FULL TABLES WHERE Table_Type LIKE 'VIEW';
EOF


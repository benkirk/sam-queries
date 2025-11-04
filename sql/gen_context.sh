#!/bin/bash

ml conda
conda activate /glade/work/benkirk/conda-envs/sam_sql
which mysql >&2
source .env

set -e
set -o noglob

echo "SAM MySQL tables (for context):"
sql_cmd="SHOW tables;"
echo && echo ${sql_cmd}
mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
${sql_cmd}
EOF

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
#
#     sql_cmd="DESC ${table};"
#     echo && echo ${sql_cmd}
#     mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
#     ${sql_cmd}
# EOF
# done
#
#
# for table in \
#        panel \
#        facility \
#        allocation_transaction \
#        dav_charge_summary \
#        hpc_charge_summary \
#        comp_job \
#        comp_charge_summary \
#        comp_activity \
#        comp_activity_charge \
#     ; do
#
#     sql_cmd="DESC ${table}; SELECT * FROM ${table} LIMIT 5;"
#     echo && echo ${sql_cmd}
#     mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
#     ${sql_cmd}
# EOF
# done


for table in \
    academic_status \
    access_branch \
    access_branch_resource \
    account \
    account_user \
    adhoc_group \
    adhoc_group_tag \
    adhoc_system_account_entry \
    allocation \
    allocation_transaction \
    allocation_type \
    archive_activity \
    archive_charge \
    archive_charge_summary \
    archive_charge_summary_status \
    archive_cos \
    area_of_interest \
    area_of_interest_group \
    charge_adjustment \
    charge_adjustment_type \
    comp_activity \
    comp_activity_charge \
    comp_charge_summary \
    comp_charge_summary_status \
    comp_job \
    contract \
    contract_source \
    country \
    dataset_activity \
    dav_activity \
    dav_charge \
    dav_charge_summary \
    dav_charge_summary_status \
    dav_cos \
    default_project \
    disk_activity \
    disk_charge \
    disk_charge_summary \
    disk_charge_summary_status \
    disk_cos \
    disk_resource_root_directory \
    email_address \
    facility \
    facility_resource \
    factor \
    formula \
    fos_aoi \
    hpc_activity \
    hpc_charge \
    hpc_charge_summary \
    hpc_charge_summary_status \
    hpc_cos \
    institution \
    institution_type \
    login_type \
    machine \
    machine_factor \
    manual_task \
    mnemonic_code \
    nsf_program \
    organization \
    panel \
    panel_session \
    phone \
    phone_type \
    product \
    project \
    project_code \
    project_contract \
    project_directory \
    project_number \
    project_organization \
    queue \
    queue_factor \
    resource_shell \
    resource_type \
    resources \
    responsible_party \
    role \
    role_user \
    schema_version \
    stage_hpc_job \
    state_prov \
    synchronizer \
    tables_dictionary \
    temp_joey_expired_project \
    user_alias \
    user_institution \
    user_organization \
    user_resource_home \
    user_resource_shell \
    users \
    wallclock_exemption \
    ; do

    sql_cmd="DESC ${table};"
    echo && echo ${sql_cmd}
    mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
    ${sql_cmd}
EOF
    sql_cmd="SELECT * FROM ${table} LIMIT 3;"
    echo && echo ${sql_cmd}
    mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
    ${sql_cmd}
EOF
done


#mysql -u ${SAM_DB_USERNAME} -h ${SAM_DB_SERVER} sam -t <<EOF
#SELECT * FROM hpc_charge_summary ORDER BY hpc_charge_summary_id LIMIT 10;
#EOF

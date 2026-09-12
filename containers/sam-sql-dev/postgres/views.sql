-- Postgres port of SAM's 7 MySQL views (source: the git-ignored dump/views.sql
-- that `make clone` writes). Applied by load_postgres.py after the data, in
-- dependency order. camelCase columns are quoted because the ORM view models
-- declare them that way and Postgres folds unquoted identifiers to lower case.
-- Statements are split on ";\n", so keep one blank line between them.

CREATE OR REPLACE VIEW comp_activity_charge AS
SELECT cs.unix_uid, cs.username, cj.projcode, cj.job_id, cj.job_name,
       cs.queue AS queue_name, cs.machine, ca.start_time, ca.end_time, ca.submit_time,
       ca.unix_user_time, ca.unix_system_time,
       ca.start_time - ca.submit_time AS queue_wait_time,
       ca.num_nodes_used, cj.cos, cj.exit_status, cj.interactive, ca.processing_status,
       ca.error_comment, ca.activity_date, ca.load_date, ca.num_cores_used, ca.external_charge,
       cj.job_idx, ca.util_idx, ca.wall_time, ca.core_hours, ca.charge, ca.charge_date
FROM comp_activity ca
JOIN comp_job cj ON cj.era_part_key = ca.era_part_key AND cj.job_id = ca.job_id
     AND cj.job_idx = ca.job_idx AND cj.machine = ca.machine AND cj.submit_time = ca.submit_time
JOIN comp_charge_summary cs ON cs.charge_summary_id = ca.charge_summary_id;

CREATE OR REPLACE VIEW xras_action AS
SELECT al.allocation_id AS "allocationId", p.projcode AS "projectId",
       CASE altr.transaction_type
            WHEN 'NEW' THEN 'New' WHEN 'TRANSFER' THEN 'Transfer'
            WHEN 'SUPPLEMENT' THEN 'Supplemental' WHEN 'ADVANCE' THEN 'Advance'
            WHEN 'EXTENSION' THEN 'Extension' WHEN 'ADJUSTMENT' THEN 'Adjustment' END AS "actionType",
       altr.transaction_amount AS amount, altr.alloc_end_date AS "endDate",
       altr.creation_time AS "dateApplied"
FROM project p
JOIN account ac ON p.project_id = ac.project_id
JOIN allocation al ON ac.account_id = al.account_id
JOIN allocation_type alty ON p.allocation_type_id = alty.allocation_type_id
JOIN allocation_transaction altr ON altr.allocation_id = al.allocation_id
ORDER BY altr.creation_time;

CREATE OR REPLACE VIEW xras_hpc_allocation_amount AS
SELECT al.allocation_id, al.amount AS allocated,
       COALESCE(SUM(hcs.charges), 0) AS used,
       al.amount - COALESCE(SUM(hcs.charges), 0) AS remaining
FROM allocation al
JOIN account ac ON al.account_id = ac.account_id
JOIN resources r ON ac.resource_id = r.resource_id
JOIN resource_type rt ON rt.resource_type_id = r.resource_type_id
LEFT JOIN hpc_charge_summary hcs ON hcs.account_id = ac.account_id
     AND CAST(hcs.activity_date AS date) >= CAST(al.start_date AS date)
     AND CAST(hcs.activity_date AS date) <= CAST(al.end_date AS date)
WHERE rt.resource_type = 'HPC'
GROUP BY al.allocation_id;

CREATE OR REPLACE VIEW xras_allocation AS
SELECT al.allocation_id AS "allocationId", p.projcode AS "projectId",
       al.start_date AS "allocationBeginDate", al.end_date AS "allocationEndDate",
       al.amount AS "allocatedAmount", xhpc_aa.remaining AS "remainingAmount",
       xrrk.resource_repository_key AS "resourceRepositoryKey"
FROM project p
JOIN account ac ON p.project_id = ac.project_id
LEFT JOIN xras_resource_repository_key_resource xrrk ON ac.resource_id = xrrk.resource_id
JOIN allocation al ON ac.account_id = al.account_id
LEFT JOIN xras_hpc_allocation_amount xhpc_aa ON al.allocation_id = xhpc_aa.allocation_id
ORDER BY al.start_date DESC;

CREATE OR REPLACE VIEW xras_request AS
SELECT MIN(CAST(al.start_date AS date)) AS "requestBeginDate",
       CAST(al.end_date AS date) AS "requestEndDate",
       STRING_AGG(al.allocation_id::text, ',') AS "allocationIds",
       alt.allocation_type AS "allocationType", p.title AS "projectTitle",
       p.projcode AS "projectId", p.area_of_interest_id AS "xrasFosTypeId"
FROM project p
JOIN account ac ON p.project_id = ac.project_id
JOIN allocation al ON ac.account_id = al.account_id
JOIN allocation_type alt ON p.allocation_type_id = alt.allocation_type_id
GROUP BY p.project_id, p.projcode, p.title, p.area_of_interest_id, alt.allocation_type,
         CAST(al.end_date AS date)
ORDER BY CAST(al.end_date AS date);

CREATE OR REPLACE VIEW xras_role AS
SELECT p.projcode AS "projectId", u.username, 'AllocationManager' AS role
FROM users u JOIN project p ON u.user_id = p.project_admin_user_id
UNION ALL
SELECT p.projcode, u.username, 'Pi'
FROM users u JOIN project p ON u.user_id = p.project_lead_user_id;

-- MySQL's any_value(if(min(rank) is not null, phone, NULL)) picked an arbitrary
-- phone once any ranked type existed; this picks the best-ranked one.
CREATE OR REPLACE VIEW xras_user AS
SELECT u.username,
       COALESCE(u.nickname, u.first_name) AS "firstName",
       u.middle_name AS "middleName", u.last_name AS "lastName",
       (ARRAY_AGG(p.phone_number ORDER BY CASE pt.phone_type
                     WHEN 'Ucar Office' THEN 0 WHEN 'External Office' THEN 1 WHEN 'Cell' THEN 2
                     WHEN 'Other' THEN 3 WHEN 'Home' THEN 4 WHEN 'Fax' THEN 5 END)
          FILTER (WHERE pt.phone_type IN ('Ucar Office', 'External Office', 'Cell', 'Other', 'Home', 'Fax')))[1]
         AS phone,
       COALESCE(MIN(i.name), 'UCAR/NCAR:' || MIN(o.acronym)) AS organization,
       COALESCE(MIN(ea1.email_address), MIN(ea2.email_address),
                MIN(ea3.email_address), MIN(ea4.email_address)) AS email,
       ac.description AS "academicStatus"
FROM users u
LEFT JOIN phone p ON u.user_id = p.user_id
LEFT JOIN phone_type pt ON p.ext_phone_type_id = pt.ext_phone_type_id
LEFT JOIN user_organization uo ON u.user_id = uo.user_id
     AND uo.start_date <= LOCALTIMESTAMP AND (uo.end_date >= LOCALTIMESTAMP OR uo.end_date IS NULL)
LEFT JOIN organization o ON uo.organization_id = o.organization_id
LEFT JOIN user_institution ui ON u.user_id = ui.user_id
     AND ui.start_date <= LOCALTIMESTAMP AND (ui.end_date >= LOCALTIMESTAMP OR ui.end_date IS NULL)
LEFT JOIN institution i ON ui.institution_id = i.institution_id
LEFT JOIN email_address ea1 ON u.user_id = ea1.user_id AND ea1.is_primary
     AND ea1.email_address NOT LIKE '%ucar.edu%'
LEFT JOIN email_address ea2 ON u.user_id = ea2.user_id AND ea2.email_address NOT LIKE '%ucar.edu%'
LEFT JOIN email_address ea3 ON u.user_id = ea3.user_id AND ea3.is_primary
LEFT JOIN email_address ea4 ON u.user_id = ea4.user_id AND NOT ea4.is_primary
LEFT JOIN academic_status ac ON u.academic_status_id = ac.academic_status_id
WHERE u.login_type_id = 1
GROUP BY u.user_id, ac.description;

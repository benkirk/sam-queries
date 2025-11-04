SET @start_date = '2013-01-01';

-- Machine names with usage statistics
SELECT
    machine,
    MIN(activity_date) as first_seen,
    MAX(activity_date) as last_seen,
    COUNT(*) as total_records,
    COUNT(DISTINCT username) as unique_users,
    COUNT(DISTINCT projcode) as unique_projects,
    ROUND(SUM(charges)) as total_charges,
    CASE
        WHEN MAX(activity_date) >= DATE_SUB(CURDATE(), INTERVAL 90 DAY) THEN 'Active'
        ELSE 'Inactive'
    END as status
FROM (
    SELECT machine, activity_date, username, projcode, charges
    FROM hpc_charge_summary

    UNION ALL

    SELECT machine, activity_date, username, projcode, charges
    FROM dav_charge_summary

    UNION ALL

    SELECT machine, activity_date, username, projcode, charges
    FROM comp_charge_summary
) all_data
GROUP BY machine
ORDER BY last_seen DESC, machine;


-- Quarterly trends combining hpc_charge_summary and comp_charge_summary (with institutions)
SELECT
    CONCAT(YEAR(activity_date), ' Q', QUARTER(activity_date)) AS quarter_label,
    COUNT(DISTINCT username) as unique_users,
    COUNT(DISTINCT institution_id) as unique_institutions,
    COUNT(DISTINCT projcode) as unique_projects,
    SUM(num_jobs) as total_jobs
FROM (
    -- Historical data from hpc_charge_summary (older table)
    SELECT
        hcs.activity_date,
        hcs.username,
        hcs.projcode,
        hcs.num_jobs,
        ui.institution_id
    FROM hpc_charge_summary hcs
    LEFT JOIN user_institution ui ON hcs.user_id = ui.user_id
    WHERE hcs.activity_date >= @start_date

    UNION ALL

    -- Historical data from dav_charge_summary (older table)
    SELECT
        dcs.activity_date,
        dcs.username,
        dcs.projcode,
        dcs.num_jobs,
        ui.institution_id
    FROM dav_charge_summary dcs
    LEFT JOIN user_institution ui ON dcs.user_id = ui.user_id
    WHERE dcs.activity_date >= @start_date

    UNION ALL

    -- Current data from comp_charge_summary (newer table)
    SELECT
        ccs.activity_date,
        ccs.username,
        ccs.projcode,
        ccs.num_jobs,
        ui.institution_id
    FROM comp_charge_summary ccs
    LEFT JOIN user_institution ui ON ccs.user_id = ui.user_id
    WHERE ccs.activity_date >= @start_date
) combined_data
GROUP BY YEAR(activity_date), QUARTER(activity_date)
ORDER BY YEAR(activity_date), QUARTER(activity_date);

-- quarterly trends by facility (excluding NULL facility_name)
SELECT
    CONCAT(YEAR(combined_data.activity_date), ' Q', QUARTER(combined_data.activity_date)) AS quarter_label,
    f.facility_name,
    COUNT(DISTINCT combined_data.username) as unique_users,
    COUNT(DISTINCT ui.institution_id) as unique_institutions,
    COUNT(DISTINCT combined_data.projcode) as unique_projects,
    SUM(combined_data.num_jobs) as total_jobs
FROM (
    SELECT
        hcs.activity_date,
        hcs.username,
        hcs.projcode,
        hcs.facility_name,
        hcs.user_id,
        hcs.num_jobs
    FROM hpc_charge_summary hcs
    WHERE hcs.activity_date >= @start_date
        AND hcs.facility_name IS NOT NULL

    UNION ALL

    SELECT
        dcs.activity_date,
        dcs.username,
        dcs.projcode,
        dcs.facility_name,
        dcs.user_id,
        dcs.num_jobs
    FROM dav_charge_summary dcs
    WHERE dcs.activity_date >= @start_date
        AND dcs.facility_name IS NOT NULL

    UNION ALL

    SELECT
        ccs.activity_date,
        ccs.username,
        ccs.projcode,
        ccs.facility_name,
        ccs.user_id,
        ccs.num_jobs
    FROM comp_charge_summary ccs
    WHERE ccs.activity_date >= @start_date
        AND ccs.facility_name IS NOT NULL
) combined_data
JOIN facility f ON combined_data.facility_name = f.facility_name
LEFT JOIN user_institution ui ON combined_data.user_id = ui.user_id
GROUP BY YEAR(combined_data.activity_date), QUARTER(combined_data.activity_date), f.facility_id
ORDER BY YEAR(combined_data.activity_date), QUARTER(combined_data.activity_date), f.facility_name;

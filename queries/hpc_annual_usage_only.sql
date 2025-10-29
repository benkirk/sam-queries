-- Annual trends combining hpc_charge_summary and comp_charge_summary (with institutions)
SELECT
    YEAR(activity_date) as year,
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
    WHERE hcs.activity_date >= '2013-01-01'

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
    WHERE dcs.activity_date >= '2013-01-01'

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
    WHERE ccs.activity_date >= '2013-01-01'
) combined_data
GROUP BY YEAR(activity_date)
ORDER BY year;

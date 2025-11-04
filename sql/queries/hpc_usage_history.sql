-- define starting date
SET @start_date = '2010-10-01';

SELECT
    CASE
        WHEN MONTH(fy.activity_date) >= 10 THEN YEAR(fy.activity_date) + 1
        ELSE YEAR(fy.activity_date)
    END AS fiscal_year,
    COUNT(DISTINCT fy.username) AS users,
    COUNT(DISTINCT fy.institution_id) AS institutions,
    COUNT(DISTINCT fy.projcode) AS projects,
    SUM(fy.num_jobs) AS jobs,
    SUM(fy.core_hours) / 3.4 AS core_hours,
    SUM(fy.charges) / 3.4 AS charges
FROM (
    -- HPC
    SELECT
        hcs.username,
        ui.institution_id,
        hcs.projcode,
        hcs.num_jobs,
        (hcs.core_hours * mf.factor_value) AS core_hours,
        (hcs.charges * mf.factor_value) AS charges,
        hcs.activity_date
    FROM hpc_charge_summary hcs
    LEFT JOIN user_institution ui ON ui.user_id = hcs.user_id
    JOIN machine m ON LOWER(hcs.machine) = LOWER(m.`name`)
    JOIN machine_factor mf ON mf.machine_id = m.machine_id
    WHERE hcs.activity_date >= @start_date
      AND hcs.activity_date <= CURDATE()  -- up to today

    UNION ALL

    -- COMP
    SELECT
        ccs.username,
        ui.institution_id,
        ccs.projcode,
        ccs.num_jobs,
        (ccs.core_hours * mf.factor_value) AS core_hours,
        (ccs.charges * mf.factor_value) AS charges,
        ccs.activity_date
    FROM comp_charge_summary ccs
    LEFT JOIN user_institution ui ON ui.user_id = ccs.user_id
    JOIN machine m ON LOWER(ccs.machine) = LOWER(m.`name`)
    JOIN machine_factor mf ON mf.machine_id = m.machine_id
    WHERE ccs.activity_date >= @start_date
      AND ccs.activity_date <= CURDATE()

    UNION ALL

    -- DAV
    SELECT
        dav.username,
        ui.institution_id,
        dav.projcode,
        dav.num_jobs,
        dav.core_hours,
        dav.charges,
        dav.activity_date
    FROM dav_charge_summary dav
    LEFT JOIN user_institution ui ON ui.user_id = dav.user_id
    WHERE dav.activity_date >= @start_date
      AND dav.activity_date <= CURDATE()
) fy
GROUP BY fiscal_year
ORDER BY fiscal_year;

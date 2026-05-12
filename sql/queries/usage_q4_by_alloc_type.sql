-- Q4: projects and core hours by allocation_type x facility.
-- Answers: project counts of different allocation_types per facility;
--          core hours of different allocation_types per facility.
--
-- Variables: @start_date, @end_date, @period_grouping ('quarterly'|'annual'|'lump')

SET @start_date      = COALESCE(@start_date,      '2013-01-01');
SET @end_date        = COALESCE(@end_date,        CURDATE());
SET @period_grouping = COALESCE(@period_grouping, 'lump');

WITH all_charges AS (
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM hpc_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND facility_name IS NOT NULL

    UNION ALL
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM dav_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND facility_name IS NOT NULL

    UNION ALL
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND facility_name IS NOT NULL
)
SELECT
    CASE @period_grouping
        WHEN 'quarterly' THEN CONCAT(YEAR(ac.activity_date), ' Q', QUARTER(ac.activity_date))
        WHEN 'annual'    THEN CAST(YEAR(ac.activity_date) AS CHAR)
        ELSE 'ALL'
    END                              AS period,
    f.facility_name                  AS facility_name,
    alt.allocation_type              AS allocation_type,
    COUNT(DISTINCT ac.projcode)      AS unique_projects,
    COUNT(DISTINCT ac.username)      AS unique_users,
    SUM(ac.num_jobs)                 AS total_jobs,
    ROUND(SUM(ac.core_hours))        AS total_core_hours,
    ROUND(SUM(ac.charges))           AS total_charges
FROM all_charges ac
JOIN facility f             ON f.facility_name = ac.facility_name
JOIN project p              ON p.projcode = ac.projcode
JOIN allocation_type alt    ON alt.allocation_type_id = p.allocation_type_id
GROUP BY period, f.facility_id, f.facility_name, alt.allocation_type_id, alt.allocation_type
ORDER BY period, f.facility_name, total_core_hours DESC;

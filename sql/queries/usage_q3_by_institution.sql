-- Q3: core hours by institution (entity), with institution_type and facility.
-- Group: period x facility x institution_type x institution.
--
-- Variables: @start_date, @end_date, @period_grouping ('quarterly'|'annual'|'lump')
--
-- NOTE: when a user is affiliated with N institutions in user_institution, the
-- LEFT JOIN multiplies their rows by N, so their hours are attributed to EACH
-- affiliated institution. This is the conventional NCAR view (matches
-- hpc_usage_totals.sql) but means SUM(core_hours) here does not sum back to the
-- Q1 grand total.

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
    END                                       AS period,
    f.facility_name                           AS facility_name,
    COALESCE(it.type, '(unknown)')            AS institution_type,
    COALESCE(i.name, '(unknown)')             AS institution_name,
    COUNT(DISTINCT ac.username)               AS unique_users,
    COUNT(DISTINCT ac.projcode)               AS unique_projects,
    SUM(ac.num_jobs)                          AS total_jobs,
    ROUND(SUM(ac.core_hours))                 AS total_core_hours,
    ROUND(SUM(ac.charges))                    AS total_charges
FROM all_charges ac
JOIN facility f               ON f.facility_name = ac.facility_name
LEFT JOIN user_institution ui ON ui.user_id = ac.user_id
LEFT JOIN institution i       ON i.institution_id = ui.institution_id
LEFT JOIN institution_type it ON it.institution_type_id = i.institution_type_id
GROUP BY period, f.facility_id, f.facility_name, it.type, i.institution_id, i.name
ORDER BY period, f.facility_name, institution_type, total_core_hours DESC;

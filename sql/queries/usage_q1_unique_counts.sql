-- Q1: unique counts (users / projects / institutions) and totals per period.
-- UNION of hpc_charge_summary + dav_charge_summary + comp_charge_summary.
--
-- Variables (driver overrides; defaults below for standalone use):
--   @start_date       Inclusive lower bound on activity_date
--   @end_date         Inclusive upper bound on activity_date
--   @period_grouping  'quarterly' | 'annual' | 'lump'
--
-- Standalone use:
--   mysql -h sam-sql.ucar.edu sam < usage_q1_unique_counts.sql

SET @start_date      = COALESCE(@start_date,      '2013-01-01');
SET @end_date        = COALESCE(@end_date,        CURDATE());
SET @period_grouping = COALESCE(@period_grouping, 'lump');

WITH all_charges AS (
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM hpc_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date

    UNION ALL
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM dav_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date

    UNION ALL
    SELECT activity_date, username, user_id, projcode, facility_name,
           num_jobs, core_hours, charges
    FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
),
period_data AS (
    SELECT
        CASE @period_grouping
            WHEN 'quarterly' THEN CONCAT(YEAR(activity_date), ' Q', QUARTER(activity_date))
            WHEN 'annual'    THEN CAST(YEAR(activity_date) AS CHAR)
            ELSE 'ALL'
        END AS period,
        username, user_id, projcode, num_jobs, core_hours, charges
    FROM all_charges
),
-- Institution count is computed separately to avoid SUM inflation when a user
-- has multiple rows in user_institution.
period_inst AS (
    SELECT pd.period, COUNT(DISTINCT ui.institution_id) AS unique_institutions
    FROM period_data pd
    LEFT JOIN user_institution ui ON ui.user_id = pd.user_id
    GROUP BY pd.period
)
SELECT
    pd.period                              AS period,
    COUNT(DISTINCT pd.username)            AS unique_users,
    COUNT(DISTINCT pd.projcode)            AS unique_projects,
    pi.unique_institutions                 AS unique_institutions,
    SUM(pd.num_jobs)                       AS total_jobs,
    ROUND(SUM(pd.core_hours))              AS total_core_hours,
    ROUND(SUM(pd.charges))                 AS total_charges
FROM period_data pd
LEFT JOIN period_inst pi USING (period)
GROUP BY pd.period, pi.unique_institutions
ORDER BY pd.period;

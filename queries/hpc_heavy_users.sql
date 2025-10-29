SET @start_date = '2013-01-01';

-- Top 5 users with most jobs per year
WITH annual_user_jobs AS (
    SELECT
        YEAR(activity_date) AS activity_year,
        username,
        user_id,
        SUM(num_jobs) AS total_jobs,
        COUNT(DISTINCT projcode) AS projects_used,
        ROUND(SUM(charges)) AS total_charges
    FROM (
        SELECT hcs.activity_date, hcs.username, hcs.user_id, hcs.num_jobs, hcs.projcode, hcs.charges
        FROM hpc_charge_summary hcs
        WHERE hcs.activity_date >= @start_date

        UNION ALL

        SELECT dcs.activity_date, dcs.username, dcs.user_id, dcs.num_jobs, dcs.projcode, dcs.charges
        FROM dav_charge_summary dcs
        WHERE dcs.activity_date >= @start_date

        UNION ALL

        SELECT ccs.activity_date, ccs.username, ccs.user_id, ccs.num_jobs, ccs.projcode, ccs.charges
        FROM comp_charge_summary ccs
        WHERE ccs.activity_date >= @start_date
    ) combined_data
    GROUP BY YEAR(activity_date), username, user_id
),
ranked_users AS (
    SELECT
        activity_year,
        username,
        total_jobs,
        ROW_NUMBER() OVER (PARTITION BY activity_year ORDER BY total_jobs DESC) AS user_rank
    FROM annual_user_jobs
)
SELECT
    ru.activity_year,
    ru.user_rank,
    ru.username,
    ru.total_jobs
FROM ranked_users ru
WHERE ru.user_rank <= 5
ORDER BY ru.activity_year, ru.user_rank;

-- Q6: per-project per-machine compute totals over the period.
-- The combiner pivots `machine` into Derecho/Casper columns.
--
-- Variables: @start_date, @end_date

SET @start_date = COALESCE(@start_date, '2013-01-01');
SET @end_date   = COALESCE(@end_date,   CURDATE());

-- Annual report is comp_charge_summary only.
SELECT
    projcode                       AS projcode,
    LOWER(machine)                 AS machine,
    SUM(num_jobs)                  AS total_jobs,
    ROUND(SUM(core_hours), 2)      AS total_core_hours,
    ROUND(SUM(charges), 2)         AS total_charges
FROM comp_charge_summary
WHERE activity_date BETWEEN @start_date AND @end_date
  AND projcode IS NOT NULL
GROUP BY projcode, LOWER(machine)
ORDER BY projcode, machine;

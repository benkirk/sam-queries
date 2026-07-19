-- Q6: per-project per-machine compute totals over the period.
-- Unions the modern comp_charge_summary (Derecho/Casper era) with the legacy
-- hpc_charge_summary (Cheyenne, Yellowstone, and other retired systems) so
-- historical windows attribute compute to the machine that actually ran it.
--
-- De-dup rule: `casper` is the only machine that appears in BOTH tables (a brief
-- overlap in early 2020), so machines present in comp_charge_summary are taken
-- ONLY from comp; every other (legacy) machine comes from hpc. This avoids
-- double-counting without hardcoding machine names.
--
-- The combiner pivots `machine` into Derecho/Casper columns; other machines
-- (e.g. cheyenne) surface as their own resource rows in the NSF funding block.
--
-- Variables: @start_date, @end_date

SET @start_date = COALESCE(@start_date, '2013-01-01');
SET @end_date   = COALESCE(@end_date,   CURDATE());

SELECT
    projcode                       AS projcode,
    LOWER(machine)                 AS machine,
    SUM(num_jobs)                  AS total_jobs,
    ROUND(SUM(core_hours), 2)      AS total_core_hours,
    ROUND(SUM(charges), 2)         AS total_charges
FROM (
    SELECT projcode, machine, num_jobs, core_hours, charges
    FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND projcode IS NOT NULL

    UNION ALL

    SELECT projcode, machine, num_jobs, core_hours, charges
    FROM hpc_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND projcode IS NOT NULL
      AND LOWER(machine) NOT IN (SELECT DISTINCT LOWER(machine) FROM comp_charge_summary)
) u
GROUP BY projcode, LOWER(machine)
ORDER BY projcode, machine;

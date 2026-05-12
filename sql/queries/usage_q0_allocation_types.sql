-- Q0: distinct allocation_type values seen for projects active in the period.
-- Run this first; use the output to populate allocation_type_buckets.csv.
--
-- Variables: @start_date, @end_date  (no period grouping needed)

SET @start_date = COALESCE(@start_date, '2013-01-01');
SET @end_date   = COALESCE(@end_date,   CURDATE());

WITH all_charges AS (
    SELECT DISTINCT projcode, facility_name
    FROM hpc_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date

    UNION
    SELECT DISTINCT projcode, facility_name
    FROM dav_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date

    UNION
    SELECT DISTINCT projcode, facility_name
    FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
)
SELECT
    COALESCE(alt.allocation_type, '(none)')          AS allocation_type,
    COUNT(DISTINCT p.projcode)                       AS n_projects,
    GROUP_CONCAT(DISTINCT ac.facility_name ORDER BY ac.facility_name SEPARATOR '|') AS facilities,
    SUBSTRING(GROUP_CONCAT(DISTINCT p.projcode ORDER BY p.projcode SEPARATOR ','), 1, 200)
                                                     AS sample_projcodes
FROM all_charges ac
JOIN project p              ON p.projcode = ac.projcode
LEFT JOIN allocation_type alt ON alt.allocation_type_id = p.allocation_type_id
GROUP BY alt.allocation_type
ORDER BY n_projects DESC, allocation_type;

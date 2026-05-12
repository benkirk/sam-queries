-- Q7: per-project per-resource disk totals over the period.
-- Combiner picks the row for the Campaign Store resource as Campaign TB-yrs.
--
-- disk_charge_summary has no resource_name column directly; it carries
-- facility_name. The "Campaign Store" identity is conventional: an
-- (account_id -> resources.resources_name) join would be more precise, but
-- the simple aggregation per (projcode, facility_name) covers the cases that
-- the annual report needs ("Campaign Store" is a single-facility resource
-- in practice).
--
-- Variables: @start_date, @end_date

SET @start_date = COALESCE(@start_date, '2013-01-01');
SET @end_date   = COALESCE(@end_date,   CURDATE());

SELECT
    dcs.projcode                            AS projcode,
    COALESCE(r.resource_name, '(unknown)')  AS resource_name,
    ROUND(SUM(dcs.terabyte_years), 4)       AS total_terabyte_years,
    ROUND(SUM(dcs.charges), 2)              AS total_charges
FROM disk_charge_summary dcs
LEFT JOIN account a   ON a.account_id  = dcs.account_id
LEFT JOIN resources r ON r.resource_id = a.resource_id
WHERE dcs.activity_date BETWEEN @start_date AND @end_date
  AND dcs.projcode IS NOT NULL
GROUP BY dcs.projcode, r.resource_name
ORDER BY dcs.projcode, resource_name;

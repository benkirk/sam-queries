-- University NSF funding profile, per calendar year 2015-2026.
-- One row per (year, project, NSF contract) for every project that held a live
-- allocation overlapping that year (the allocation arm of usage_q5's "active" test).
-- Facility = the project's allocation type's panel facility; the rollup keeps UNIV (or all),
-- drops NCAR's cooperative agreements, dedupes award numbers within a year and sums
-- estimated_total_amt / award years from api.nsf.gov (sql/queries/nsf_awards.py).
WITH RECURSIVE yrs AS (SELECT 2015 AS cy UNION ALL SELECT cy + 1 FROM yrs WHERE cy < 2026),
held AS (
    SELECT DISTINCT y.cy, p.project_id, p.projcode
    FROM yrs y
    JOIN allocation a ON a.deleted = 0
         AND a.start_date <= CONCAT(y.cy, '-12-31')
         AND (a.end_date IS NULL OR a.end_date >= CONCAT(y.cy, '-01-01'))
    JOIN account ac ON ac.account_id = a.account_id
    JOIN project p  ON p.project_id = ac.project_id
)
SELECT h.cy, h.projcode, f.facility_name AS facility, alt.allocation_type,
       c.contract_number, csrc.contract_source, c.title AS contract_title
FROM held h
JOIN project p                 ON p.project_id = h.project_id
LEFT JOIN allocation_type alt  ON alt.allocation_type_id = p.allocation_type_id
LEFT JOIN panel pa             ON pa.panel_id = alt.panel_id
LEFT JOIN facility f           ON f.facility_id = pa.facility_id
JOIN project_contract pc       ON pc.project_id = p.project_id
JOIN contract c                ON c.contract_id = pc.contract_id
JOIN contract_source csrc      ON csrc.contract_source_id = c.contract_source_id
WHERE csrc.contract_source = 'NSF';

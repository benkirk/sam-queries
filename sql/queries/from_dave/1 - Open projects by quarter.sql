-- NOTE: the "users" value is correct for each row (quarter, facility, alloc type), but summing across the rows
-- will result in inflated user counts, due to the same user appearing in different quarters and multiple projects.
-- Based on comparing different queries, the margin of error for summing values within a quarter is
--    0% for project counts
--    2% for project leads (across facilities only) and 7% (across facilities and alloc types)
--    8% for lead orgs & 206% for lead orgs
--   20% for users and 33% for users 
-- Summing across quarters should not be done in any situation as the results would be meaningless.

WITH RECURSIVE quarter_series AS (
    SELECT 
        DATE('2017-01-01') AS quarter_start,
        CONCAT(YEAR(DATE('2017-01-01')), ' Q', QUARTER(DATE('2017-01-01'))) AS quarter_label
    UNION ALL
    SELECT 
        DATE_ADD(quarter_start, INTERVAL 3 MONTH),
        CONCAT(YEAR(DATE_ADD(quarter_start, INTERVAL 3 MONTH)), ' Q', QUARTER(DATE_ADD(quarter_start, INTERVAL 3 MONTH)))
    FROM quarter_series
    WHERE quarter_start < '2025-06-30'
), 
users_insts AS (
	select u.user_id, u.username, IF(ui.institution_id, i.name, 'NCAR/UCAR') as inst
	from users u
	left join user_institution ui on ui.user_id = u.user_id 
				and ui.start_date <= NOW() and (ui.end_date >= NOW() or ui.end_date is null)
	left join institution i on i.institution_id = ui.institution_id
	left join user_organization uo on uo.user_id = u.user_id 
				and uo.start_date <= NOW() and (uo.end_date >= NOW() or uo.end_date is null)
	left join organization o on o.organization_id = uo.organization_id
)
SELECT
    qs.quarter_label,
    f.facility_name,
--    alt.allocation_type,
    COUNT(DISTINCT p.project_id) AS open_projects,
    COUNT(DISTINCT p.project_lead_user_id) AS project_leads,
    COUNT(DISTINCT uio.inst) AS lead_insts,
    COUNT(DISTINCT au.user_id) AS users

FROM quarter_series qs
JOIN allocation al 
  ON al.start_date < DATE_ADD(qs.quarter_start, INTERVAL 3 MONTH)
 AND al.end_date >= qs.quarter_start
JOIN account ac ON ac.account_id = al.account_id
JOIN project p ON p.project_id = ac.project_id
JOIN allocation_type alt ON alt.allocation_type_id = p.allocation_type_id
JOIN panel pn ON pn.panel_id = alt.panel_id
JOIN facility f ON f.facility_id = pn.facility_id
LEFT JOIN users_insts uio ON uio.user_id = p.project_lead_user_id
LEFT JOIN account_user au ON au.account_id = ac.account_id
    AND au.start_date < DATE_ADD(qs.quarter_start, INTERVAL 3 MONTH)
    AND (au.end_date >= qs.quarter_start OR au.end_date IS NULL)

GROUP BY qs.quarter_label, f.facility_name -- , alt.allocation_type
ORDER BY qs.quarter_label;
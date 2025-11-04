-- NOTE: Summing values across quarters should never be done. Values would be meaningless.
-- Summing values across rows will inflate the true values due to users and leads potentially appearing 
-- in different facilities or or projects of different types.
-- For most accurate values, modify the query to collapse the group by columns.


select CONCAT(YEAR(ccs.activity_date), ' Q', QUARTER(ccs.activity_date)) AS quarter_label
	, ccs.facility_name
-- 	, alt.allocation_type
	, count(distinct ccs.projcode) as projects
	, count(distinct p.project_lead_user_id) as leads
	, count(distinct ccs.username) as users
	, count(distinct ui.institution_id) as institutions
	, count(distinct uo.organization_id) as organizations
	, sum(ccs.num_jobs) as num_jobs
	, round(sum(ccs.core_hours)) as core_hours
	, round(sum(ccs.charges)) as charges

from comp_charge_summary ccs
left join user_institution ui on ui.user_id = ccs.user_id
		AND ui.start_date = (
						select max(uix.start_date)
						from user_institution uix
						where uix.user_id = ui.user_id
						)
left join user_organization uo on uo.user_id = ccs.user_id
		AND uo.start_date = (
						select max(uox.start_date)
						from user_organization uox
						where uox.user_id = uo.user_id
						)
join project p on p.projcode = ccs.projcode
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id

where ccs.activity_date between '2023-06-01' and '2025-09-30'
and lower(ccs.machine) like 'derecho%'
-- and ccs.projcode not in ('UTAM0017', 'UTAM0019')
	group by 1, 2 -- , 3
	order by 1
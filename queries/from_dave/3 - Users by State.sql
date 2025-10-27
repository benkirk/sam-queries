
select c.name as country
	, s.name as "state"
	, count(distinct ccs.username) as users
	, count(distinct ui.institution_id) as institutions
	, count(distinct uo.organization_id) as ncar_orgs
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
join institution i on i.institution_id = ui.institution_id
left join state_prov s on s.ext_state_prov_id = i.state_prov_id
left join country c on c.ext_country_id = s.ext_country_id

where ccs.activity_date between '2023-06-01' and '2025-09-30'
-- and lower(ccs.machine) like 'derecho%'
-- and ccs.projcode not in ('UTAM0017', 'UTAM0019')
	group by 1, 2 -- , 3
	order by 1
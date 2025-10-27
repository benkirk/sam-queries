select CONCAT(YEAR(ccs.activity_date), '-', LPAD(MONTH(ccs.activity_date), 2, '0')) AS month_label
	, ccs.facility_name
	, alt.allocation_type
	, sum(ccs.num_jobs) as num_jobs
	, round(sum(ccs.core_hours)) as core_hours
	, round(sum(ccs.charges)) as charges

from comp_charge_summary ccs
left join user_institution ui on ui.user_id = ccs.user_id
join project p on p.projcode = ccs.projcode
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id


where ccs.activity_date between '2021-10-01' and '2025-09-30'
  and lower(ccs.machine) like 'derecho'
	group by 1, 2, 3
	order by 1
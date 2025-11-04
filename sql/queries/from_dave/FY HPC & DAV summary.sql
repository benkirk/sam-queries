
select count(distinct fy.username) as users
	, count(distinct fy.institution_id) as institutions
	, count(distinct fy.projcode) as projects
	, sum(fy.num_jobs) as jobs
	, sum(fy.core_hours)/3.4 as core_hours
	, sum(fy.charges)/3.4 as charges
from 
 (
-- 	select hcs.username
--   , ui.institution_id
-- 	, hcs.projcode
-- 	, sum(hcs.num_jobs) as num_jobs
-- 	, sum(hcs.core_hours * mf.factor_value) as core_hours
-- 	, sum(hcs.charges * mf.factor_value) as charges
-- from hpc_charge_summary hcs
-- left join user_institution ui on ui.user_id = hcs.user_id
-- join machine m on lower(hcs.machine) = lower(m.`name`)
-- join machine_factor mf on mf.machine_id = m.machine_id
-- where hcs.activity_date between '2024-10-01' and '2025-09-30'
-- and hcs.machine = 'cheyenne'
-- and ui.start_date = 
-- 		(select max(uix.start_date) 
-- 		from user_institution uix 
-- 		where uix.user_id = ui.user_id
-- 		-- 	and (ui.end_date is NULL or ui.end_date >= '2023-10-01')
-- 				)
-- group by 1,2,3
-- 
-- UNION
-- 
	select ccs.username
  , ui.institution_id
	, ccs.projcode
	, sum(ccs.num_jobs) as num_jobs
	, sum(ccs.core_hours * mf.factor_value) as core_hours
	, sum(ccs.charges * mf.factor_value) as charges
from comp_charge_summary ccs
left join user_institution ui on ui.user_id = ccs.user_id
join machine m on lower(ccs.machine) = lower(m.`name`)
join machine_factor mf on mf.machine_id = m.machine_id
where ccs.activity_date between '2024-10-01' and '2025-09-30'
-- and ccs.machine = 'derecho'
 and ui.start_date = 
		(select max(uix.start_date) 
		from user_institution uix 
		where uix.user_id = ui.user_id
-- 		and (ui.end_date is NULL or ui.end_date >= '2023-10-01')
		)
group by 1,2,3


UNION

select dav.username
	, ui.institution_id
	, dav.projcode
	, sum(dav.num_jobs) as num_jobs
	, sum(dav.core_hours) as core_hours
	, sum(dav.charges) as charges

from dav_charge_summary dav
join user_institution ui on ui.user_id = dav.user_id

where dav.activity_date between '2024-10-01' and '2025-09-30'
group by 1, 2, 3 
	) fy
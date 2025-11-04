select
 hcs.machine, 
	 count(distinct hcs.username) as users
-- , count(distinct ui.institution_id) as institutions
	, count(distinct hcs.projcode) as projects
	, sum(hcs.num_jobs) as num_jobs
	, sum(hcs.core_hours) as core_hours
	, sum(hcs.charges) as charges

from hpc_charge_summary hcs
-- left join user_institution ui on ui.user_id = hcs.user_id

where hcs.activity_date between '2023-10-01' and '2024-09-30'
-- and hcs.machine in ( 'cheyenne', 'Yellowstone')

 group by 1
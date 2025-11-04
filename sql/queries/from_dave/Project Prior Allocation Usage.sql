select p.projcode
-- 	, hc.username
	, r.resource_name as "Resource"
	, date(al.start_date) as "Start", date(al.end_date) as "End", round(al.amount) as "Allocation"
	, round(sum(hc.charges)) as Charges
 	, round(al.amount - sum(hc.charges)) as Remaining
	, sum(hc.num_jobs) as Jobs
 	, count(distinct hc.user_id) as Users
from users u
join account_user au on au.user_id = u.user_id
join account ac on ac.account_id = au.account_id
join allocation al on al.account_id = ac.account_id
join hpc_charge_summary hc on hc.user_id = u.user_id and hc.account_id = ac.account_id
join resources r on ac.resource_id = r.resource_id
join project p on p.project_id = ac.project_id
where
 p.projcode = 'UDKE0016'
and hc.activity_date between al.start_date and al.end_date
-- and r.resource_name = 'Yellowstone'
group by 1, 2, 3, 4, 5 -- , 6
order by 4 desc, 7 desc
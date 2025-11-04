select  date(activity_date) as "Date"
-- 	, CASE 
-- 		WHEN (end_time - start_time) < 990 THEN "Short job"
-- 		ELSE "Long job"
-- 	  END as job_type
	, sum(num_jobs) as jobs
	, round(sum(core_hours)) as core_hours
-- 	, avg(num_nodes_used) as avg_size
	, count(distinct username) as "Users"
	, count(distinct projcode) as "Projects"
from comp_charge_summary
where  activity_date between '2023-10-01' and '2024-09-30'
and  machine = 'derecho'
group by 1 
order by 1

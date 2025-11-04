SELECT DATE_FORMAT(ha.activity_date,'%Y-%m') as 'Month'
 	, ha.queue_name
-- 	, ha.num_nodes_used as 'Job_nodes'
	, sum((ha.end_time - ha.start_time) * ha.num_nodes_used * 16)/3600 as Core_Hrs_Used
	, (sum(ha.queue_wait_time) / count(ha.hpc_activity_id)) / 3600 as Average_Wait_Hrs
	, count(ha.hpc_activity_id) as Jobs

from hpc_activity ha
where ha.machine = 'cheyenne'
and ha.queue_name in ('regular', 'economy', 'premium')
and ha.num_nodes_used > 1
group by 1, 2
order by 1, 2
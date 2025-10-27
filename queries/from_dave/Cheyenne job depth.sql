select U.Depth, 
	   sum(U.Jobs) as Jobs,
	   sum(U.Core_Hrs) as Core_Hrs_Used
from (select power(2,ceil(log(2,j.num_nodes_used))) as Depth,
		count(j.job_id) as Jobs,
		sum((j.end_time - j.start_time) * j.num_nodes_used * 36)/3600 as Core_Hrs
	  from hpc_activity j

	  where FROM_UNIXTIME(j.end_time) between '2017-01-01' and '2021-12-01'
	  and (j.end_time - j.start_time) > 0
	  and j.machine = 'cheyenne'
	  group by 1) as U
group by U.Depth
order by 1

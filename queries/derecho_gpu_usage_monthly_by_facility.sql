select ccs.facility_name, alt.allocation_type, ccs.projcode, ca.num_nodes_used, ca.num_cores_used,
	count(distinct ccs.username) as users,
	count(ca.job_id) as jobs,
	sum(ca.wall_time/60) as hours,
	sum(ccs.charges) as gpu_hours
from comp_activity ca
join comp_charge_summary ccs on ccs.charge_summary_id = ca.charge_summary_id
join project pr on pr.projcode = ccs.projcode
join allocation_type alt on alt.allocation_type_id = pr.allocation_type_id
where ca.machine = 'derecho-gpu'
and ca.processor_type = 'gpu'
and ca.activity_date between '2021-10-01' and '2025-10-31'
group by 1, 2, 3
order by 1, 2

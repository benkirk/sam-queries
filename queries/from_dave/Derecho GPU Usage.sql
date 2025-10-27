select ccs.facility_name, alt.allocation_type, ccs.projcode, ca.num_nodes_used, ca.num_cores_used,
	count(distinct ccs.username) as "Users",
	count(ca.job_id) as "Jobs",
	sum(ca.wall_time/60) as "Hours",
	sum(ccs.charges) as "GPU-Hours"
from comp_activity ca
join comp_charge_summary ccs on ccs.charge_summary_id = ca.charge_summary_id
join project pr on pr.projcode = ccs.projcode
join allocation_type alt on alt.allocation_type_id = pr.allocation_type_id
where ca.machine = 'derecho-gpu'
and ca.processor_type = 'gpu'
and ca.activity_date between '2023-10-01' and '2024-09-30'
group by 1, 2, 3
order by 1, 2
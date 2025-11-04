select round(sum(hc.charges)) as "Charges"
	, round(sum(hc.core_hours)) as "Core_Hours"
	, round(sum(hc.num_jobs)) as "Jobs"
from comp_charge_summary hc
join project p on p.projcode = hc.projcode
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
join panel pn on pn.panel_id = alt.panel_id
join facility f on f.facility_id = pn.facility_id
where hc.machine like 'derecho%'
and hc.activity_date between '2023-10-01' and '2024-10-01'
and f.facility_name = 'UNIV'
-- group by 1
order by 1, 2
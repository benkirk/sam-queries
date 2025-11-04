select date_format(hcs.activity_date, '%Y/%m/1') as "Month"
     , f.facility_name as "Facility"
	 , r.resource_name as "Resource"
 --  , p.projcode
   , round(sum(hcs.core_hours),0) as "Core-hours"
   , round(sum(hcs.charges),0) as "Charges"
	, count(distinct hcs.username) as "Users"
from project p
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
join panel pn on pn.panel_id = alt.panel_id
join facility f on pn.facility_id = f.facility_id
join account ac on ac.project_id = p.project_id
join resources r on r.resource_id = ac.resource_id
join hpc_charge_summary hcs on hcs.account_id = ac.account_id
where r.resource_name in ('Cheyenne')
and hcs.activity_date between '2017-10-01' and '2025-12-31'
-- p.active = 1
-- and f.facility_name != 'ASD'
-- and alt.allocation_type ='NSC'
group by 1, 2

UNION

select date_format(hcs.activity_date, '%Y/%m/1') as "Month"
     , f.facility_name as "Facility"
	 , r.resource_name as "Resource"
 --  , p.projcode
   , round(sum(hcs.core_hours),0) as "Core-hours"
   , round(sum(hcs.charges),0) as "Charges"
	, count(distinct hcs.username) as "Users"
from project p
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
join panel pn on pn.panel_id = alt.panel_id
join facility f on pn.facility_id = f.facility_id
join account ac on ac.project_id = p.project_id
join resources r on r.resource_id = ac.resource_id
join comp_charge_summary hcs on hcs.account_id = ac.account_id
where r.resource_name like 'Derecho%'
and hcs.activity_date between '2017-10-01' and '2025-12-31'
-- p.active = 1
-- and f.facility_name != 'ASD'
-- and alt.allocation_type ='NSC'
group by 1, 2

order by 1, 2



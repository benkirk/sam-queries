-- Core-hours allocated, used by Institution, as of [DATE]
-- Institution, Allocated, Used, # Projects, # Users

select i.name as Institution
	, s.code as 'State'
-- 	, p.projcode
	, round(sum(allocs.allocation),0) as Total_Allocation
 	, round(sum(ych.charges),0) as Total_Charges
	, count(distinct p.projcode) as Projects
 	, sum(proj_users.users) as Total_Users

from project p
join account a on a.project_id = p.project_id
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
join panel pn on pn.panel_id = alt.panel_id
join facility f on pn.facility_id = f.facility_id
join user_institution ui on ui.user_id = p.project_lead_user_id
join institution i on i.institution_id = ui.institution_id
left join state_prov s on s.ext_state_prov_id = i.state_prov_id
left join country c on c.ext_country_id = s.ext_country_id
join (select al.account_id, sum(al.amount) as allocation
		from allocation al 
		join account ac on ac.account_id = al.account_id
		join resources r on ac.resource_id = r.resource_id
		where r.resource_name in ('Derecho','Derecho GPU', 'Cheyenne', 'Yellowstone', 'Geyser_Caldera','CMIP Analysis Platform')
		and al.end_date > '2023-10-01'
		and al.start_date <= '2025-09-30'
		group by 1) as allocs
	on allocs.account_id = a.account_id
left join (select ac3.account_id, count(distinct au3.user_id) as users
				from account ac3
				join account_user au3 on au3.account_id = ac3.account_id
				join resources r3 on r3.resource_id = ac3.resource_id
				where r3.resource_name in ('Derecho','Derecho GPU', 'Cheyenne','Yellowstone','Geyser_Caldera','Casper')
				and (au3.end_date > '2023-10-01' or au3.end_date is NULL)
				and au3.start_date <= '2025-09-30'
				group by 1) as proj_users
	on proj_users.account_id = a.account_id
left join (select hcs.account_id, sum(hcs.charges) as charges
				 from hpc_charge_summary hcs
				 join account ac2 on ac2.account_id = hcs.account_id
				 join resources r2 on ac2.resource_id = r2.resource_id
				 where r2.resource_name in ('Derecho','Derecho GPU', 'Cheyenne','Yellowstone')
				 and hcs.activity_date between '2023-10-01' and '2025-09-30'
				 group by 1) as ych
	on ych.account_id = a.account_id

where f.facility_name IN ('UNIV','WNA')
and p.active = TRUE
and c.code = 'US'
group by 1, 2
order by 1
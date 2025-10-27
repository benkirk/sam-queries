select nsf_awd.division as "NSF Division"
	, count(distinct nsf_awd.projcode) as "Projects"
  , sum(hpc.gaus)/3.4 as "Cheyenne CHs"
  , sum(hpc2.gaus)/3.4 as "Derecho CHs"
	, sum(dav.gaus)/3.4 as "DAV core-hours"
	, sum(campaign.tb_yrs) as "Campaign TB-yrs"
	, sum(glade.tb_yrs) as "GLADE TB-yrs"
from 
(select distinct p.projcode, substring(ct.contract_number,1,locate('-',ct.contract_number)-1) as division
	from account ac
	join project p on p.project_id = ac.project_id
	join project_contract pc on pc.project_id = p.project_id
	join contract ct on ct.contract_id = pc.contract_id
	join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
	join allocation al on al.account_id = ac.account_id
	join panel pn on pn.panel_id = alt.panel_id
	join facility f on pn.facility_id = f.facility_id
	where -- ct.start_date <= '2022-09-30' and ct.end_date > '2021-10-01'
	  ct.contract_source_id = 1 -- NSF awards
    and al.start_date <= '2025-09-30' and al.end_date >= '2024-10-01'
	and (f.facility_name = 'UNIV' or pn.panel_name = 'ASD-CHAP')
	) as nsf_awd

left outer join (select hpcx.projcode, sum(hpcx.gaus) as gaus
	  from (select hc.projcode, hc.machine, round(sum(hc.core_hours) * mf.factor_value,2) as gaus
		from hpc_charge_summary hc
		join machine m on lower(hc.machine) = lower(m.name)
		join machine_factor mf on mf.machine_id = m.machine_id
		where hc.activity_date between '2024-10-01' and '2025-09-30'
		group by 1, 2) as hpcx
	  group by 1) as hpc
		on hpc.projcode = nsf_awd.projcode
left outer join (select hpcy.projcode, sum(hpcy.gaus) as gaus
 from (select ccs.projcode, ccs.machine, round(sum(ccs.core_hours) * mf.factor_value,2) as gaus
from comp_charge_summary ccs
join machine m on lower(ccs.machine) = lower(m.`name`)
join machine_factor mf on mf.machine_id = m.machine_id
where ccs.activity_date between '2024-10-01' and '2025-09-30'
and m.name like 'derecho%'
group by 1, 2) as hpcy
 group by 1) as hpc2
on hpc2.projcode = nsf_awd.projcode
left outer join (select davx.projcode, sum(davx.gaus) as gaus
	from (select dc.projcode, dc.machine, round(sum(dc.core_hours) * mf.factor_value,2) as gaus
      from comp_charge_summary dc 
	  join machine m on lower(dc.machine) = lower(m.name)
	  join machine_factor mf on mf.machine_id = m.machine_id
      where dc.activity_date between '2024-10-01' and '2025-09-30'
			and m.name like 'Casper%'
	  group by 1, 2) as davx
	group by 1) as dav
		on upper(dav.projcode) = upper(nsf_awd.projcode)
left outer join (select p2.projcode
		-- , round(sum(da.file_size_total)/power(1000,3),2) as tb
		, sum(dc.charge) as tb_yrs
      from disk_activity da
	  join disk_charge dc on dc.disk_activity_id = da.disk_activity_id
		join account ac on ac.account_id = dc.account_id
		join resources r on r.resource_id = ac.resource_id
    join project_directory pd on pd.directory_name = da.directory_name
	  join project p2 on pd.project_id = p2.project_id
      where da.activity_date between '2024-10-01' and '2025-09-30'
			and r.resource_name like 'GLADE%'
	  group by 1) as glade
		on glade.projcode = nsf_awd.projcode
left outer join (select p2.projcode
		-- , round(sum(da.file_size_total)/power(1000,3),2) as tb
		, sum(dc.charge) as tb_yrs
      from disk_activity da
	  join disk_charge dc on dc.disk_activity_id = da.disk_activity_id
		join account ac on ac.account_id = dc.account_id
		join resources r on r.resource_id = ac.resource_id
    join project_directory pd on pd.directory_name = da.directory_name
	  join project p2 on pd.project_id = p2.project_id
      where da.activity_date between '2024-10-01' and '2025-09-30'
			and r.resource_name like 'Campaign%'
	  group by 1) as campaign
		on campaign.projcode = nsf_awd.projcode
-- left outer join (select aa.projcode, aa.archive_resource
-- 	  , sum(ach.charge) as tb_yrs
--       from archive_activity aa
-- 	  join archive_charge ach on ach.archive_activity_id = aa.archive_activity_id
--       where aa.type_act = 'S'
--       and aa.activity_date between '2020-10-01' and '2021-09-30'
-- 	  group by 1) as hpss
-- 		on hpss.projcode = nsf_awd.projcode

group by 1
order by 3 desc
 
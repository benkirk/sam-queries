select pn.panel_name, alt.allocation_type
	, count(distinct p.projcode) as "Projects"
  , sum(hpc.gaus)/3.40 as "Cheyenne CHs"
  , sum(hpc2.gaus)/3.40 as "Derecho CHs"
	, round(sum(dav.gaus),2)/3.40 as "DAV CH"
	, round(sum(campaign.tb_yrs),2) as "Campaign TB-yrs"
	, round(sum(glade.tb_yrs),2) as "GLADE TB-yrs"

from account ac
join project p on p.project_id = ac.project_id
-- join resources r on r.resource_id = ac.resource_id
join allocation al on al.account_id = ac.account_id
join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
join panel pn on pn.panel_id = alt.panel_id
join facility f on pn.facility_id = f.facility_id
left outer join (select hc.projcode, hc.machine, round(sum(hc.core_hours) * mf.factor_value,2) as gaus
      from hpc_charge_summary hc
	  join machine m on lower(hc.machine) = lower(m.`name`)
	  join machine_factor mf on mf.machine_id = m.machine_id
      where hc.activity_date between '2024-10-01' and '2025-09-30'
	  group by 1,2) as hpc
		on hpc.projcode = p.projcode
left outer join (select ccs.projcode, ccs.machine, round(sum(ccs.core_hours) * mf.factor_value,2) as gaus
from comp_charge_summary ccs
join machine m on lower(ccs.machine) = lower(m.`name`)
join machine_factor mf on mf.machine_id = m.machine_id
where ccs.activity_date between '2024-10-01' and '2025-09-30'
and m.name like 'derecho%'
group by 1, 2) as hpc2
on hpc2.projcode = p.projcode
left outer join (select dc.projcode, dc.machine, round(sum(dc.charges),2) as gaus
      from comp_charge_summary dc
	  join machine m on lower(dc.machine) = lower(m.`name`)
	  join machine_factor mf on mf.machine_id = m.machine_id
      where dc.activity_date between '2024-10-01' and '2025-09-30'
			and m.name like 'Casper%'
	  group by 1,2) as dav
		on upper(dav.projcode) = upper(p.projcode)
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
		on glade.projcode = p.projcode
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
		on campaign.projcode = p.projcode
-- left outer join (select aa.projcode, aa.archive_resource
-- 		-- , round(sum(aa.bytes)/power(1000,4),2) as tb
-- 	  , sum(ach.charge) as tb_yrs
--       from archive_activity aa
-- 	  join archive_charge ach on ach.archive_activity_id = aa.archive_activity_id
--       where aa.type_act = 'S'
--       and aa.activity_date between '2021-10-01' and '2022-09-30'
-- 	  group by 1) as hpss
-- 		on hpss.projcode = p.projcode

where al.start_date <= '2025-09-30' and al.end_date >= '2024-10-01'
and f.facility_name = 'UNIV'
and p.projcode != 'CESM0002'
-- and alt.allocation_type in ('Data')
group by 1, 2
order by 4 desc

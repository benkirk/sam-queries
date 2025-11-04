select x.facility_name, x.panel_name 
	, coalesce(x.email, concat(x.username, '@ucar.edu')) as email
	, x.name, x.username, x.institution
	, group_concat(x.project order by x.project separator '; ') as projects
	, coalesce(sum(x.hpc_charges),0) as hpc
	, coalesce(sum(x.hpc2_charges),0) as hpc2
	, coalesce(sum(x.dav_charges),0) as dav
	, coalesce(sum(x.disk_tb),0) as disk_tb
-- 	, coalesce(sum(x.hpss_tb),0) as hpss_tb
from
(select distinct u.username as 'username'
	, concat(u.first_name, ' ', u.last_name) as 'name'
	, (select e.email_address from email_address e
		where e.is_primary is FALSE
		and u.user_id = e.user_id
		limit 1) as 'email'
	, coalesce(i.name,'NCAR') as 'institution'
	, p.projcode as 'project'
   , pn.panel_name
   , hpc.machine as HPC
   , hpc.charges as "hpc_charges"
	 , comp.machine as HPC2
	 , comp.charges as "hpc2_charges"
   , dav.machine as DAV
   , dav.charges as "dav_charges"
   , disk.tb as "disk_tb"
--   , hpss.tb as "hpss_tb"
	, f.facility_name
from project p
join account ac on ac.project_id = p.project_id
join allocation al on al.account_id = ac.account_id
left outer join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
left outer join panel pn on pn.panel_id = alt.panel_id
left outer join facility f on pn.facility_id = f.facility_id
left outer join users u on u.user_id = p.project_lead_user_id
left outer join (select ui.user_id, ui.institution_id
				 from user_institution ui
				 where ui.start_date < NOW()
				 and (ui.end_date > NOW() or ui.end_date is NULL)) uix
	on uix.user_id = u.user_id
left outer join institution i on i.institution_id = uix.institution_id
left outer join (select ha.projcode, ha.machine, round(sum(ha.charges),2) as charges
      from hpc_charge_summary ha
      where ha.activity_date between '2023-10-01' and '2025-10-01'
	  group by 1) as hpc
		on hpc.projcode = p.projcode
left outer join (select ccs.projcode, ccs.machine, round(sum(ccs.charges),2) as charges
      from comp_charge_summary ccs
      where ccs.activity_date between '2023-10-01' and '2025-10-01'
	  group by 1) as comp
		on comp.projcode = p.projcode
left outer join (select da.projcode, da.machine, round(sum(da.charges),2) as charges
      from dav_charge_summary da
      where da.activity_date between '2023-10-01' and '2025-10-01'
	  group by 1) as dav
		on dav.projcode = p.projcode
left outer join (select p2.projcode, round(sum(da.bytes)/power(1000,4),2) as tb
      from disk_activity da
      inner join project_directory pd on pd.directory_name = da.directory_name
	  inner join project p2 on pd.project_id = p2.project_id
      where da.activity_date = (select max(activity_date)
                                from disk_activity
							    where activity_date between '2023-10-01' and '2025-10-01')
	  group by 1) as disk
		on disk.projcode = p.projcode
-- left outer join (select aa.projcode, aa.archive_resource, round(sum(aa.bytes)/power(1000,4),2) as tb
--       from archive_activity aa
--       where type_act = 'S'
--       and aa.activity_date = (select max(activity_date)
--                               from archive_activity
-- 							  where type_act = 'S'
-- 							  and activity_date between '2022-10-01' and '2024-10-01')
-- 	  group by 1) as hpss
-- 		on hpss.projcode = p.projcode

-- one of:  ASD, CISL, CSL, NCAR, UNIV, WNA, XSEDE
where f.facility_name not in ('CISL','NCAR')
  and pn.panel_name != 'ASD-NCAR'
--  and p.active = 1
and p.parent_id is NULL
-- and ui.start_date < NOW() and (ui.end_date is NULL or ui.end_date >= NOW())
and al.start_date < '2025-10-01'
and al.end_date >= '2023-10-01') x
group by 1,2,3,4,5,6
order by 1

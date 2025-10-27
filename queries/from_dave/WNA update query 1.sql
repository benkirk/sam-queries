select p.projcode, pn.panel_name, u.last_name
, derecho.charges as Derecho_charges, derecho.core_hours as Derecho_core_hours
, derecho_gpu.charges as DerGPU_charges, derecho_gpu.gpu_hours as Derecho_GPU_hours
, cheyenne.charges as Cheyenne_charges, cheyenne.core_hours as Cheyenne_core_hours
, dav.charges as DAV_charges, hpss.charges as HPSS_charges
from project p
join users u on u.user_id = p.project_lead_user_id
join user_institution ui on ui.user_id = p.project_lead_user_id
join institution i on i.institution_id = ui.institution_id
join account ac on ac.project_id = p.project_id
join allocation al on al.account_id = ac.account_id
left outer join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
left outer join panel pn on pn.panel_id = alt.panel_id
left outer join facility f on pn.facility_id = f.facility_id
left outer JOIN (select px.projcode, round(sum(ccs.charges),2) as charges, round(sum(ccs.core_hours),2) as core_hours
      from comp_charge_summary ccs
	  join account acx on acx.account_id = ccs.account_id
	  join project px on px.project_id = acx.project_id
      where ccs.activity_date between '2022-10-01' and '2024-09-30'
			and machine like 'derecho'
	  group by 1) as derecho
		on derecho.projcode = p.projcode
left outer JOIN (select px.projcode, round(sum(ccs2.charges),2) as charges, round(sum(ccs2.core_hours),2) as gpu_hours
      from comp_charge_summary ccs2
	  join account acx on acx.account_id = ccs2.account_id
	  join project px on px.project_id = acx.project_id
      where ccs2.activity_date between '2022-10-01' and '2024-09-30'
			and machine like 'derecho-gpu'
	  group by 1) as derecho_gpu
		on derecho_gpu.projcode = p.projcode
left outer JOIN (select px.projcode, round(sum(hc.charges),2) as charges, round(sum(hc.core_hours),2) as core_hours
      from hpc_charge_summary hc
	  join account acx on acx.account_id = hc.account_id
	  join project px on px.project_id = acx.project_id
      where hc.activity_date between '2022-10-01' and '2024-09-30'
	  group by 1) as cheyenne
		on cheyenne.projcode = p.projcode
left outer JOIN (select px.projcode, round(sum(dc.charges),2) as charges, round(sum(dc.core_hours),2) as core_hours
      from dav_charge_summary dc
	  join account acx on acx.account_id = dc.account_id
	  join project px on px.project_id = acx.project_id
      where dc.activity_date between '2022-10-01' and '2024-09-30'
	  group by 1) as dav
		on dav.projcode = p.projcode
left outer join (select px.projcode, round(sum(ac.charge),2) as charges
      from archive_charge ac
	  join account acx on acx.account_id = ac.account_id
	  join project px on px.project_id = acx.project_id
      where ac.charge_date between '2022-10-01' and '2024-09-30'
	  group by 1) as hpss
		on hpss.projcode = p.projcode
where  f.facility_name = 'WNA'
-- i.name like 'Cary%'
and al.end_date > '2022-10-01'
and al.start_date <= '2024-09-30'
group by 1
order by 2,4 desc
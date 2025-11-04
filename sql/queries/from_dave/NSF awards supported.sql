select c.contract_number as "Grant"
	, date(c.start_date) as "Start", date(c.end_date) as "End"
	, p.projcode as "Project", p.active
 	, round(sum(hpc.charges),0) as hpc_charges
 	, round(sum(dav.charges),0) as dav_charges
-- 	, round(sum(hpss.charges),2) as hpss_charges
from contract c
join contract_source cs on cs.contract_source_id = c.contract_source_id
join project_contract pc on pc.contract_id = c.contract_id
join project p on p.project_id = pc.project_id
join account ac on ac.project_id = p.project_id
left join (select hcs.account_id, round(sum(hcs.charges),2) as charges
         from hpc_charge_summary hcs
         join account ac2 on ac2.account_id = hcs.account_id
         join allocation al2 on al2.account_id = ac2.account_id
        where hcs.activity_date between '2016-10-01' and '2023-12-01'
		  and al2.end_date  >= '2016-10-01'
          and al2.start_date < '2023-12-01'
		group by 1) as hpc
 	  on hpc.account_id = ac.account_id
left join (select dc.account_id, round(sum(dc.charges),2) as charges
         from dav_charge_summary dc
         join account ac3 on ac3.account_id = dc.account_id
         join allocation al3 on al3.account_id = ac3.account_id
        where dc.activity_date between '2016-10-01' and '2023-12-01'
		  and al3.end_date  >= '2016-10-01'
          and al3.start_date < '2023-12-01'
		group by 1) as dav
 	  on dav.account_id = ac.account_id
where cs.contract_source = 'NSF'
and c.end_date >= '2016-10-01'
and c.start_date < '2023-12-01'
-- and (p.active = 1 or p.inactivate_time between '2016-10-01' and '2020-10-01')
group by 1, 2, 3, 4
order by 6 desc


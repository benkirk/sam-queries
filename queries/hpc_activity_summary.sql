-- derecho
select CONCAT(YEAR(ccs.activity_date), ' ', 'Q', QUARTER(ccs.activity_date)) AS quarter_label
	, count(distinct ccs.username) as users
  , count(distinct ui.institution_id) as institutions
	, count(distinct ccs.projcode) as projects
	, sum(ccs.num_jobs) as num_jobs
	, round(sum(ccs.core_hours)) as core_hours
	, round(sum(ccs.charges)) as charges

from comp_charge_summary ccs
left join user_institution ui on ui.user_id = ccs.user_id

where ccs.activity_date between '2017-10-01' and '2025-09-30'
  and lower(ccs.machine) like 'derecho%'
	group by quarter_label;

-- casper
select CONCAT(YEAR(ccs.activity_date), ' ', 'Q', QUARTER(ccs.activity_date)) AS quarter_label
	, count(distinct ccs.username) as users
  , count(distinct ui.institution_id) as institutions
	, count(distinct ccs.projcode) as projects
	, sum(ccs.num_jobs) as num_jobs
	, round(sum(ccs.core_hours)) as core_hours
	, round(sum(ccs.charges)) as charges

from comp_charge_summary ccs
left join user_institution ui on ui.user_id = ccs.user_id

where ccs.activity_date between '2017-10-01' and '2025-09-30'
  and lower(ccs.machine) like 'casper%'
	group by quarter_label;

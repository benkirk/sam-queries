WITH weekly_usage as (
	select date_format(activity_date, '%Y/%m/%d') as "date"
	, CASE
		WHEN directory_name like '/glade/collections%' THEN '/glade/collections'
		WHEN directory_name like '/glade/p/work%' THEN '/glade/work'
		WHEN directory_name like '/glade/p/%' THEN '/glade/project'
		WHEN directory_name like '/glade/scratch%' THEN '/glade/scratch'
		WHEN directory_name like '/glade/u%' THEN '/glade/u'
		WHEN directory_name like '/glade/work' THEN '/glade/work'
		WHEN directory_name like '/glade2/collections%' THEN '/glade2/collections'
		WHEN directory_name like '/glade2/h2%' THEN '/glade2/h2'
		WHEN directory_name like '/glade2/scratch2' THEN '/glade2/2scratch2'
		WHEN directory_name like '/gpfs/csfs1%' THEN 'campaign_storage'
		WHEN directory_name like '/quasar%' THEN 'quasar'
		WHEN directory_name like '/stratus%' THEN 'stratus'
 		ELSE directory_name
	  END as File_space
	, count(distinct username) as Users
	, sum(number_of_files) as Files
	, sum(bytes)/power(1000,4) as TB
 	, count(disk_activity_id) as Records
from disk_activity 
where activity_date >= '2017-01-01'
  and directory_name not like '/glade2%'
-- and projcode not like 'a%'
-- and projcode = 'nral0017'
group by 1, 2
)
select date_format(w.date, '%Y/%m/1') as "Month"
	, w.File_space
	, max(w.Users) as users
	, max(w.Files) as files
	, max(w.TB) as TB
from weekly_usage w
group by 1, 2
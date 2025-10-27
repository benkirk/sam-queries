select t1.activity_date as "DATE"
	 , t1.archive_resource as "RESOURCE"
	 , sum(t1.charges) as "Charges"
     , sum(t1.files) as "FILES"
     , sum(t1.gbytes) as "GB"
     , sum(t1.gbytes_dup) as "GB_Dup"
     , count(distinct(t1.unix_uid)) as "USERS_CHARGED"
	, sum(t1.stars) as Records_Stored
from (select aa.archive_resource
             , aa.activity_date
             , aa.unix_uid
             , sum(aa.number_of_files) as files
             , sum(aa.bytes) / ( 1000 * 1000 * 1000) as gbytes
             , sum(aa.bytes * cos.number_of_copies) / ( 1000 * 1000 * 1000) as gbytes_dup
             , sum(arch.charge) as charges
			 , count(aa.archive_activity_id) as stars
        from archive_activity aa
		inner join archive_cos cos on aa.archive_cos_id = cos.archive_cos_id
		left join archive_charge arch on arch.archive_activity_id = aa.archive_activity_id
        where aa.type_act = 'S'
-- 		and aa.processing_status is NULL
        group by archive_resource, activity_date, unix_uid
        ) t1
group by t1.archive_resource, t1.activity_date
order by 1 desc,2,3,4
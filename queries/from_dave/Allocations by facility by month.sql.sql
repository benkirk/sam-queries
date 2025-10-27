select f.facility_name, r.resource_name
	, date_format(txn.creation_time, '%m/1/%Y') as "Month"
 		, count(txn.transaction_type) as Num_txns
		, sum(coalesce(txn.requested_amount,0)) as Requested
		, sum(txn.transaction_amount) as Awarded
		from account ac
		join allocation al on al.account_id = ac.account_id
		join allocation_transaction txn on txn.allocation_id = al.allocation_id
		join resources r on r.resource_id = ac.resource_id
		join project p on p.project_id = ac.project_id
		join allocation_type alt on alt.allocation_type_id = p.allocation_type_id
		join panel pn on pn.panel_id = alt.panel_id
		join facility f on f.facility_id = pn.facility_id
		where r.resource_name in ('CMIP Analysis Platform') and
		 txn.creation_time between '2012-10-01' and '2024-09-30'
		and txn.transaction_type = 'NEW'
 		and f.facility_name = 'UNIV'
		group by 1, 2, 3
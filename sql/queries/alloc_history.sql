SET @start_date = '2013-01-01';

-- Summary of allocation transactions by type
SELECT
    transaction_type,
    COUNT(*) as transaction_count,
    COUNT(DISTINCT allocation_id) as unique_allocations,
    COUNT(DISTINCT user_id) as unique_users,
    SUM(transaction_amount) as total_transaction_amount,
    AVG(transaction_amount) as avg_transaction_amount,
    MIN(creation_time) as first_transaction,
    MAX(creation_time) as last_transaction
FROM allocation_transaction
GROUP BY transaction_type
ORDER BY transaction_count DESC;


-- -- Monthly allocation transaction trends
-- SELECT
--     DATE_FORMAT(at.creation_time, '%Y-%m') as month,
--     SUM(CASE WHEN at.transaction_type = 'NEW' THEN 1 ELSE 0 END) as new_allocations,
--     SUM(CASE WHEN at.transaction_type = 'EXTENSION' THEN 1 ELSE 0 END) as extensions,
--     SUM(CASE WHEN at.transaction_type = 'SUPPLEMENT' THEN 1 ELSE 0 END) as supplements,
--     ROUND(SUM(at.transaction_amount)) as total_amount
-- FROM allocation_transaction at
-- WHERE at.creation_time >= @start_date
-- GROUP BY DATE_FORMAT(at.creation_time, '%Y-%m')
-- ORDER BY month;

-- Quarterly allocation transaction trends
SELECT
    CONCAT(YEAR(at.creation_time), ' Q', QUARTER(at.creation_time)) as quarter,
    SUM(CASE WHEN at.transaction_type = 'NEW' THEN 1 ELSE 0 END) as new_allocations,
    SUM(CASE WHEN at.transaction_type = 'EXTENSION' THEN 1 ELSE 0 END) as extensions,
    SUM(CASE WHEN at.transaction_type = 'SUPPLEMENT' THEN 1 ELSE 0 END) as supplements,
    ROUND(SUM(at.transaction_amount)) as total_amount
FROM allocation_transaction at
WHERE at.creation_time >= @start_date
GROUP BY YEAR(at.creation_time), QUARTER(at.creation_time)
ORDER BY YEAR(at.creation_time), QUARTER(at.creation_time);

-- Annual allocation transaction trends
SELECT
    YEAR(at.creation_time) as year,
    SUM(CASE WHEN at.transaction_type = 'NEW' THEN 1 ELSE 0 END) as new_allocations,
    SUM(CASE WHEN at.transaction_type = 'EXTENSION' THEN 1 ELSE 0 END) as extensions,
    SUM(CASE WHEN at.transaction_type = 'SUPPLEMENT' THEN 1 ELSE 0 END) as supplements,
    ROUND(SUM(at.transaction_amount)) as total_amount
FROM allocation_transaction at
WHERE at.creation_time >= @start_date
GROUP BY YEAR(at.creation_time)
ORDER BY year;

-- -- Complete allocation history for a project
-- SELECT
--     p.projcode,
--     al.allocation_id,
--     at.transaction_type,
--     at.requested_amount,
--     at.transaction_amount,
--     DATE(at.alloc_start_date) as start_date,
--     DATE(at.alloc_end_date) as end_date,
--     DATEDIFF(at.alloc_end_date, at.alloc_start_date) as duration_days,
--     CONCAT(u.first_name, ' ', u.last_name) as processed_by,
--     DATE(at.creation_time) as transaction_date,
--     at.auth_at_panel_mtg,
--     at.transaction_comment
-- FROM allocation_transaction at
-- JOIN allocation al ON at.allocation_id = al.allocation_id
-- JOIN account ac ON al.account_id = ac.account_id
-- JOIN project p ON ac.project_id = p.project_id
-- LEFT JOIN users u ON at.user_id = u.user_id
-- WHERE p.projcode = 'SCSG0001'  -- Replace with actual project code
-- ORDER BY at.creation_time;

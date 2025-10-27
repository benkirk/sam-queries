-- Active projects with their directories
SELECT
    p.projcode,
    pd.directory_name,
    DATE(pd.start_date) as directory_start,
    DATE(pd.end_date) as directory_end,
    CASE
        WHEN pd.end_date IS NULL THEN 'Active'
        WHEN pd.end_date >= CURDATE() THEN 'Active'
        ELSE 'Expired'
    END as directory_status,
    DATE(MAX(al.end_date)) as allocation_end_date,
    p.title
FROM project p
LEFT JOIN project_directory pd ON p.project_id = pd.project_id
LEFT JOIN account ac ON p.project_id = ac.project_id AND ac.deleted = 0
LEFT JOIN allocation al ON ac.account_id = al.account_id AND al.deleted = 0
WHERE p.active = 1
GROUP BY p.project_id, pd.project_directory_id
ORDER BY p.projcode, pd.start_date DESC;

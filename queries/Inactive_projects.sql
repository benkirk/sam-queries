-- Query returns a list of projects that have expired within the month that is 3 months prior to when the script is ran.
-- Query returns a list of projects that have expired within the month that is 3 months prior to when the script is ran.
SELECT
    p.projcode AS project_code,
    c.contract_number,
    alty.allocation_type,
    DATE(MAX(al.end_date)) AS latest_end_date, -- Cast to DATE to ensure time component is present
    u.last_name AS project_lead_last_name,
    pd.directory_name
FROM
    allocation al
INNER JOIN
    account ac ON al.account_id = ac.account_id
INNER JOIN
    project p ON ac.project_id = p.project_id
INNER JOIN
    users u ON p.project_lead_user_id = u.user_id
INNER JOIN
    allocation_type alty ON p.allocation_type_id = alty.allocation_type_id
LEFT JOIN
    project_contract pcn ON p.project_id = pcn.project_id
LEFT JOIN
    contract c ON pcn.contract_id = c.contract_id
LEFT JOIN
    project_directory pd ON p.project_id = pd.project_id
WHERE
    al.deleted = 0
    AND ac.deleted = 0
    AND p.active = 1
GROUP BY
    p.project_id,
    p.projcode,
    c.contract_number,
    alty.allocation_type,
    u.last_name,
    pd.directory_name
HAVING
    latest_end_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 3 MONTH), '%Y-%m-01')
    AND
    latest_end_date <= LAST_DAY(DATE_SUB(CURDATE(), INTERVAL 3 MONTH))
ORDER BY
    latest_end_date DESC,
    allocation_type DESC;

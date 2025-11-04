-- Allocations by quarter, allocation_type, and facility
SELECT
    CONCAT(YEAR(a.creation_time), ' Q', QUARTER(a.creation_time)) AS quarter_label,
    f.facility_name,
    at.allocation_type,
    COUNT(*) AS new_allocations,
    ROUND(SUM(a.amount)) AS total_amount
FROM allocation a
JOIN account ac ON a.account_id = ac.account_id
JOIN project p ON ac.project_id = p.project_id
JOIN allocation_type at ON p.allocation_type_id = at.allocation_type_id
JOIN panel pa ON at.panel_id = pa.panel_id
JOIN facility f ON pa.facility_id = f.facility_id
WHERE a.creation_time >= DATE_SUB(CURDATE(), INTERVAL 10 YEAR)
    AND a.creation_time <= CURDATE()
    AND a.deleted = 0
GROUP BY YEAR(a.creation_time), QUARTER(a.creation_time), f.facility_id, at.allocation_type_id
ORDER BY YEAR(a.creation_time), QUARTER(a.creation_time), f.facility_name, at.allocation_type;

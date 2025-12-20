WITH RankedAllocations AS (
    SELECT
        p.projcode,
        p.title AS project_title,
        CONCAT(u.first_name, ' ', u.last_name) AS pi_name,
        a.amount AS allocation_size,
        i.name AS institution_name,
        c.contract_number AS grant_award,
        s.name AS state,
        f.facility_name,
        r.resource_name,
        p.abstract,
        a.start_date,
        ROW_NUMBER() OVER (
            PARTITION BY p.projcode, r.resource_name
            ORDER BY a.start_date DESC, a.allocation_id DESC
        ) as rn
    FROM allocation a
    JOIN account ac ON a.account_id = ac.account_id
    JOIN resources r ON ac.resource_id = r.resource_id
    JOIN project p ON ac.project_id = p.project_id
    JOIN users u ON p.project_lead_user_id = u.user_id
    JOIN user_institution ui ON u.user_id = ui.user_id
    JOIN institution i ON ui.institution_id = i.institution_id
    JOIN state_prov s ON i.state_prov_id = s.ext_state_prov_id
    JOIN facility_resource fr ON r.resource_id = fr.resource_id
    JOIN facility f ON fr.facility_id = f.facility_id
    LEFT JOIN project_contract pc ON p.project_id = pc.project_id
    LEFT JOIN contract c ON pc.contract_id = c.contract_id
    WHERE
        r.resource_name LIKE '%Casper%'
        AND f.facility_name IN ('UNIV', 'WNA')
        AND a.start_date >= '2023-01-01'
        AND s.name IN ('Kansas', 'Maine', 'Mississippi', 'North Carolina', 'Wyoming', 'Alaska', 'Texas')
        AND (ui.end_date IS NULL OR ui.end_date >= a.start_date)
)
SELECT
    state,
    institution_name,
    projcode,
    pi_name,
    project_title,
    MAX(CASE WHEN resource_name = 'Casper' THEN allocation_size ELSE NULL END) AS derecho_allocation,
    MAX(CASE WHEN resource_name = 'Casper GPU' THEN allocation_size ELSE NULL END) AS derecho_gpu_allocation,
    grant_award,
    facility_name,
    abstract
FROM RankedAllocations
WHERE rn = 1
GROUP BY
    state,
    institution_name,
    projcode,
    pi_name,
    project_title,
    grant_award,
    facility_name,
    abstract
ORDER BY state, institution_name, projcode;

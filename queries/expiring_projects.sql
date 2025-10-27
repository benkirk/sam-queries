-- UNIV/WNA only
SELECT
    p.projcode,

    -- Lead info
    CONCAT(lead_u.first_name, ' ', lead_u.last_name) AS lead_name,
    lead_email.email_address AS lead_email,

    -- Admin info (if any)
    CONCAT(admin_u.first_name, ' ', admin_u.last_name) AS admin_name,
    admin_email.email_address AS admin_email,

    -- Allocation info
    a.amount AS allocation_amount,
    DATE_FORMAT(a.start_date, '%Y-%m-%d') AS start_date, -- date only, ISO format
    DATE_FORMAT(a.end_date, '%Y-%m-%d') AS end_date,     -- date only, ISO format
    DATEDIFF(a.end_date, CURDATE()) AS days_left,        -- days until allocation expires

    -- Facility info
    f.facility_name AS community,

    -- CONCAT(LEFT(p.title, 40), IF(CHAR_LENGTH(p.title) > 40, '...', '')) AS project_title  -- truncated w/ ellipsis
    p.title

FROM allocation AS a
JOIN account AS ac
    ON a.account_id = ac.account_id
JOIN project AS p
    ON ac.project_id = p.project_id

-- Project lead (required)
JOIN users AS lead_u
    ON p.project_lead_user_id = lead_u.user_id
LEFT JOIN email_address AS lead_email
    ON lead_email.user_id = lead_u.user_id
    AND lead_email.is_primary = 1

-- Project admin (optional)
LEFT JOIN users AS admin_u
    ON p.project_admin_user_id = admin_u.user_id
LEFT JOIN email_address AS admin_email
    ON admin_email.user_id = admin_u.user_id
    AND admin_email.is_primary = 1

-- Facility chain
LEFT JOIN allocation_type AS at
    ON p.allocation_type_id = at.allocation_type_id
LEFT JOIN panel AS pa
    ON at.panel_id = pa.panel_id
LEFT JOIN facility AS f
    ON pa.facility_id = f.facility_id

WHERE
    p.active = 1
    AND a.deleted = 0
    AND a.end_date IS NOT NULL
    AND a.end_date BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 60 DAY)
    AND a.allocation_id = (
        SELECT MAX(a2.allocation_id)
        FROM allocation AS a2
        JOIN account AS ac2 ON a2.account_id = ac2.account_id
        WHERE ac2.project_id = p.project_id
          AND a2.deleted = 0
    )
    AND f.facility_name IN ('UNIV', 'WNA')
ORDER BY a.end_date ASC;

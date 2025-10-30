-- Query returns a list of projects set to end on a specified date.
-- The list includes project type, user details, project lead, and project directory.

WITH ProjectDetails AS (
    -- Calculates the latest end date and gathers primary project info.
    SELECT
        p.projcode AS project,
        p.project_id,
        MAX(al.end_date) AS latest_end_date,
        p.project_lead_user_id,
        ac.account_id,
        alt.allocation_type
    FROM
        allocation al
    INNER JOIN
        account ac ON al.account_id = ac.account_id
    INNER JOIN
        project p ON p.project_id = ac.project_id
    INNER JOIN
        allocation_type alt ON alt.allocation_type_id = p.allocation_type_id
    INNER JOIN
        panel pn ON pn.panel_id = alt.panel_id
    INNER JOIN
        facility f ON pn.facility_id = f.facility_id
    -- Exclude deleted accounts, allocations, and inactive projects.
    -- Only include projects under the UNIV and WNA facilities.
    WHERE
        ac.deleted = 0
        AND al.deleted = 0
        AND p.active = 1
        AND f.facility_name IN ('UNIV', 'WNA')
    GROUP BY
        p.project_id, alt.allocation_type, p.project_lead_user_id, ac.account_id, p.projcode
)
SELECT DISTINCT
    pd.project,
    pd.allocation_type AS type,
    CAST(pd.latest_end_date AS DATE) AS end_date,
    u.username,
    u.first_name,
    u.last_name,
    ea.email_address,
    l.last_name AS lead_last_name,
    pdir.directory_name
FROM
    ProjectDetails pd
INNER JOIN
    account_user au ON au.account_id = pd.account_id
INNER JOIN
    users u ON u.user_id = au.user_id
INNER JOIN
    email_address ea ON ea.user_id = u.user_id AND ea.is_primary = TRUE
LEFT JOIN
    users l ON l.user_id = pd.project_lead_user_id
LEFT JOIN
    project_directory pdir ON pdir.project_id = pd.project_id
WHERE
-- Only include projects within given date range.
    CAST(pd.latest_end_date AS DATE) BETWEEN '2025-11-01' AND '2025-11-30'
    -- Only include users active users.
    AND (au.end_date IS NULL OR CAST(au.end_date AS DATE) >= CURDATE())
    AND u.active = 1
ORDER BY
    pd.project,
    pd.allocation_type,
    pd.latest_end_date DESC;

-- Q5: one row per (project, contract) for every project active in the period.
-- Projects with no contract appear once with NULLs in contract columns.
--
-- Notes:
--   * NSF division code is the contract_number prefix before the first hyphen
--     (e.g., "AGS-0830068" -> "AGS"). Mapping division -> directorate is done
--     by the combiner via nsf_directorate_map.csv (not in the DB).
--   * Lab/Division attribution: project has no organization_id, so we infer
--     the NCAR lab from the project lead's current organization affiliation
--     (user_organization with NULL or future end_date).
--
-- Variables: @start_date, @end_date

SET @start_date = COALESCE(@start_date, '2013-01-01');
SET @end_date   = COALESCE(@end_date,   CURDATE());

WITH active_projcodes AS (
    -- A project is "active in the period" if EITHER:
    --   (1) it has any comp_charge_summary row in the period, OR
    --   (2) it has at least one non-deleted allocation whose date range
    --       overlaps the period (start <= @end AND (end IS NULL OR end >= @start)).
    -- Including (2) captures projects that hold allocations but happened to do
    -- no compute work in the window — the sample annual report counts them.
    SELECT DISTINCT projcode FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date

    UNION

    SELECT DISTINCT p.projcode
    FROM allocation a
    JOIN account ac ON ac.account_id = a.account_id
    JOIN project p  ON p.project_id = ac.project_id
    WHERE a.deleted = 0
      AND a.start_date <= @end_date
      AND (a.end_date IS NULL OR a.end_date >= @start_date)
),
-- Per-project facility list as seen by comp_charge_summary (primary source).
proj_fac_comp AS (
    SELECT projcode,
           GROUP_CONCAT(DISTINCT facility_name ORDER BY facility_name SEPARATOR '|') AS facility_names
    FROM comp_charge_summary
    WHERE activity_date BETWEEN @start_date AND @end_date
      AND facility_name IS NOT NULL
    GROUP BY projcode
),
-- Fallback: facility per project derived via account -> resource ->
-- facility_resource -> facility. Used when a project has an allocation but
-- no comp_charge_summary activity in the period.
proj_fac_alloc AS (
    SELECT p.projcode,
           GROUP_CONCAT(DISTINCT f.facility_name ORDER BY f.facility_name SEPARATOR '|') AS facility_names
    FROM allocation a
    JOIN account ac      ON ac.account_id = a.account_id
    JOIN project p       ON p.project_id  = ac.project_id
    JOIN facility_resource fr ON fr.resource_id = ac.resource_id
    JOIN facility f      ON f.facility_id = fr.facility_id
    WHERE a.deleted = 0
      AND a.start_date <= @end_date
      AND (a.end_date IS NULL OR a.end_date >= @start_date)
    GROUP BY p.projcode
),
-- Pick the most recent (or current) organization affiliation per user.
-- Prefer current (end_date NULL or in future); else the latest by start_date.
lead_org AS (
    SELECT uo.user_id,
           uo.organization_id
    FROM   user_organization uo
    JOIN   (
        SELECT user_id, MAX(start_date) AS max_start
        FROM   user_organization
        WHERE  end_date IS NULL OR end_date > NOW()
        GROUP BY user_id
    ) cur ON cur.user_id = uo.user_id AND cur.max_start = uo.start_date
),
-- Walk up parent_org_id until we hit a Lab-level org (level_code = '0300'),
-- which is what the annual report's "Lab/Division" column uses.
org_lab AS (
    -- Recursive CTE: start from every org, climb until we find level 0300.
    WITH RECURSIVE walk AS (
        SELECT organization_id AS start_id,
               organization_id AS cur_id,
               parent_org_id,
               acronym,
               level_code
        FROM organization
        UNION ALL
        SELECT w.start_id,
               o.organization_id,
               o.parent_org_id,
               o.acronym,
               o.level_code
        FROM walk w
        JOIN organization o ON o.organization_id = w.parent_org_id
        WHERE w.level_code <> '0300' AND w.level_code <> '0200' AND w.level_code <> '0100'
    )
    SELECT start_id AS organization_id, acronym AS lab_acronym
    FROM walk
    WHERE level_code = '0300'
)
SELECT
    p.projcode                                                AS projcode,
    p.title                                                   AS project_title,
    -- Facility list: prefer comp_charge_summary (actual usage in period);
    -- fall back to allocation-derived facility (account -> resource ->
    -- facility_resource -> facility) for allocated-but-unused projects.
    COALESCE(pfc.facility_names, pfa.facility_names)          AS facility_names,
    COALESCE(alt.allocation_type, '(none)')                   AS allocation_type,
    o.acronym                                                 AS lead_org_acronym,
    o.name                                                    AS lead_org_name,
    COALESCE(ol.lab_acronym, o.acronym)                       AS lab_acronym,
    c.contract_id                                             AS contract_id,
    c.contract_number                                         AS contract_number,
    csrc.contract_source                                      AS contract_source,
    np.nsf_program_name                                       AS nsf_program_name,
    CASE
        WHEN c.contract_number IS NULL THEN NULL
        WHEN INSTR(c.contract_number, '-') = 0 THEN c.contract_number
        ELSE SUBSTRING_INDEX(c.contract_number, '-', 1)
    END                                                       AS nsf_division_code
FROM project p
JOIN  active_projcodes ap        ON ap.projcode = p.projcode
LEFT JOIN proj_fac_comp pfc      ON pfc.projcode = p.projcode
LEFT JOIN proj_fac_alloc pfa     ON pfa.projcode = p.projcode
LEFT JOIN allocation_type alt    ON alt.allocation_type_id = p.allocation_type_id
LEFT JOIN lead_org lo            ON lo.user_id = p.project_lead_user_id
LEFT JOIN organization o         ON o.organization_id = lo.organization_id
LEFT JOIN org_lab ol             ON ol.organization_id = lo.organization_id
LEFT JOIN project_contract pc    ON pc.project_id = p.project_id
LEFT JOIN contract c             ON c.contract_id = pc.contract_id
LEFT JOIN contract_source csrc   ON csrc.contract_source_id = c.contract_source_id
LEFT JOIN nsf_program np         ON np.nsf_program_id = c.nsf_program_id
ORDER BY p.projcode, c.contract_number;

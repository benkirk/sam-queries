-- restore_derecho_facility_resource.sql
--
-- Restore the Derecho / Derecho GPU facility_resource rows deleted on 2026-10-01
-- with a NULL fair_share_percentage (= "use the facility default" on both stacks).
--
-- Cause: SAMuel's Admin > Resources "Unset" DELETEs the row. Legacy SAM reads the
-- row as facility-on-resource membership (DefaultFacilitiesForResourceQuery walks
-- resource.getFacilityResources()), so with no rows /fairShareTree/v3/Derecho
-- serves "facilities": []. A NULL row falls back to facility.fair_share_percentage
-- in both legacy (FacilityResourceDTOFacilityFacade) and SAMuel (COALESCE).
--
-- Portable MySQL / Postgres; resolves by name, idempotent (NOT EXISTS).
-- After COMMIT, rebuild legacy's in-memory trees (else next :30 Quartz slot):
--   PUT https://sam.ucar.edu/api/protected/admin/ssg/fairShareTree/v3/Derecho
--   PUT https://sam.ucar.edu/api/protected/admin/ssg/fairShareTree/v3/Derecho%20GPU

-- STEP 0 -- pre-check: expect 0 rows (any row here is left untouched by STEP 1).
SELECT r.resource_name, f.facility_name, fr.facility_resource_id, fr.fair_share_percentage
FROM facility_resource fr
JOIN facility f  ON f.facility_id = fr.facility_id
JOIN resources r ON r.resource_id = fr.resource_id
WHERE r.resource_name IN ('Derecho', 'Derecho GPU')
ORDER BY r.resource_name, f.facility_name;

-- Postgres (sam_dev) only, if STEP 1 raises a duplicate key on facility_resource_id:
-- SELECT setval(pg_get_serial_sequence('facility_resource', 'facility_resource_id'),
--               (SELECT MAX(facility_resource_id) FROM facility_resource));

START TRANSACTION;

-- STEP 1 -- expect 12 rows inserted (6 facilities x 2 resources).
INSERT INTO facility_resource (facility_id, resource_id, creation_time)
SELECT f.facility_id, r.resource_id, CURRENT_TIMESTAMP
FROM facility f
CROSS JOIN resources r
WHERE f.facility_name IN ('ASD', 'CISL', 'CSL', 'NCAR', 'UNIV', 'WNA')
  AND r.resource_name IN ('Derecho', 'Derecho GPU')
  AND NOT EXISTS (SELECT 1 FROM facility_resource fr
                  WHERE fr.facility_id = f.facility_id
                    AND fr.resource_id = r.resource_id);

-- STEP 2 -- verify: 12 rows, every fair_share_percentage NULL, effective sums 100.
SELECT r.resource_name, f.facility_name, fr.fair_share_percentage AS override_pct,
       COALESCE(fr.fair_share_percentage, f.fair_share_percentage) AS effective_pct
FROM facility_resource fr
JOIN facility f  ON f.facility_id = fr.facility_id
JOIN resources r ON r.resource_id = fr.resource_id
WHERE r.resource_name IN ('Derecho', 'Derecho GPU')
ORDER BY r.resource_name, f.facility_name;

SELECT r.resource_name, COUNT(*) AS n_rows,
       SUM(COALESCE(fr.fair_share_percentage, f.fair_share_percentage)) AS effective_sum
FROM facility_resource fr
JOIN facility f  ON f.facility_id = fr.facility_id
JOIN resources r ON r.resource_id = fr.resource_id
WHERE r.resource_name IN ('Derecho', 'Derecho GPU')
GROUP BY r.resource_name
ORDER BY r.resource_name;

COMMIT;

-- STEP 3 -- confirm the rows persisted (a piped run without COMMIT rolls back silently).
SELECT COUNT(*) AS persisted_rows
FROM facility_resource fr
JOIN resources r ON r.resource_id = fr.resource_id
WHERE r.resource_name IN ('Derecho', 'Derecho GPU')
  AND fr.fair_share_percentage IS NULL;

-- UNDO (restores the 2026-10-01 empty state; legacy tree goes empty again):
-- DELETE fr FROM facility_resource fr JOIN resources r ON r.resource_id = fr.resource_id
--  WHERE r.resource_name IN ('Derecho', 'Derecho GPU') AND fr.fair_share_percentage IS NULL;

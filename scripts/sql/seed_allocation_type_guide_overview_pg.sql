-- ===========================================================================
-- seed_allocation_type_guide_overview_pg.sql — the comparison table's copy
-- (Postgres, sam_dev).
--
--   Apply after alter_allocation_type_guide_overview_pg.sql:
--     psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/seed_allocation_type_guide_overview_pg.sql
--
-- Ben, 2026-11-03 (revised 2026-11-04): drafted from ARC's opportunities page and the allocations
-- docs; edit it on the admin card (Allocations > XRAS > Configuration). Fills
-- a row only while all three columns are NULL, so a re-run never overwrites
-- an edit. External Projects and NSC (staff-only, audience ncar) are drafted from the NCAR Allocations on Derecho document.
-- The two dialects must carry the same rows
-- (tests/unit/models/test_allocation_type_guide.py).
-- ===========================================================================

UPDATE allocation_type_guide SET purpose = 'Modest projects, or early-phase computational campaigns preparing for a Large request',
       eligibility = 'U.S. university researchers with an NSF award',
       requirements = 'An NSF award that explicitly supports the proposed computing'
 WHERE xras_allocation_type_id = 500024
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Dissertations, early-career research, or groundwork toward a funded proposal',
       eligibility = 'Graduate students, postdocs, and faculty with no prior NCAR allocation at U.S. universities, once per career stage',
       requirements = 'A letter from an advisor or department head'
 WHERE xras_allocation_type_id = 500847
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'A university course or workshop',
       eligibility = 'Faculty and instructors at U.S. 2- and 4-year institutions',
       requirements = 'A course description and syllabus (PDF)'
 WHERE xras_allocation_type_id = 500026
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Analyzing NSF NCAR-hosted data on Casper',
       eligibility = 'Researchers at U.S. institutions, with or without a funding award',
       requirements = 'Rationale including which NCAR-hosted data sets you will analyze'
 WHERE xras_allocation_type_id = 500848
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Projects that need more than a Small allocation',
       eligibility = 'U.S. university researchers with an NSF award',
       requirements = 'An NSF award that explicitly supports the proposed computing, and a Request Summary'
 WHERE xras_allocation_type_id = 500023
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Mid-size NCAR work funded by an external award',
       eligibility = 'NCAR staff leading an externally funded award',
       requirements = 'Core-hours budgeted or co-sponsored in the award, with Directorate approval'
 WHERE xras_allocation_type_id = 501276
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Large-scale NCAR projects, above the lead lab''s NSC minimum',
       eligibility = 'NCAR scientists, base-funded or on an external award',
       requirements = 'A proposal with a multi-year computational plan'
 WHERE xras_allocation_type_id = 500088
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

-- Verification: 7 rows carry the copy.
SELECT COUNT(*) AS rows_expect_7 FROM allocation_type_guide WHERE purpose IS NOT NULL;

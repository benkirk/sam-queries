-- ===========================================================================
-- seed_allocation_type_guide_overview_pg.sql — the comparison table's copy
-- (Postgres, sam_dev).
--
--   Apply after alter_allocation_type_guide_overview_pg.sql:
--     psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/seed_allocation_type_guide_overview_pg.sql
--
-- Ben, 2026-11-03: drafted from ARC's opportunities page and the allocations
-- docs; edit it on the admin card (Allocations > XRAS > Configuration). Fills
-- a row only while all three columns are NULL, so a re-run never overwrites
-- an edit. NCAR External Projects and NSC have no public copy and stay NULL.
-- The two dialects must carry the same rows
-- (tests/unit/models/test_allocation_type_guide.py).
-- ===========================================================================

UPDATE allocation_type_guide SET purpose = 'Small projects, or first runs before a Large request',
       eligibility = 'U.S. university researchers with an NSF award',
       requirements = 'An NSF award that supports the computing'
 WHERE xras_allocation_type_id = 500024
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Dissertations, early-career research, seed work',
       eligibility = 'Graduate students, postdocs and new faculty at U.S. universities',
       requirements = 'A letter from an advisor or department head'
 WHERE xras_allocation_type_id = 500847
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'A university course or workshop',
       eligibility = 'Faculty and instructors at U.S. 2- and 4-year institutions',
       requirements = 'Course dates and a class roster'
 WHERE xras_allocation_type_id = 500026
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Analyzing NSF NCAR-hosted data on Casper',
       eligibility = 'Earth system scientists at U.S. institutions, any funding',
       requirements = 'The data sets you will use'
 WHERE xras_allocation_type_id = 500848
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

UPDATE allocation_type_guide SET purpose = 'Projects beyond what Small offers',
       eligibility = 'U.S. university researchers with an NSF award',
       requirements = 'An NSF award and a Request Summary; panel review twice a year'
 WHERE xras_allocation_type_id = 500023
   AND purpose IS NULL AND eligibility IS NULL AND requirements IS NULL;

-- Verification: 5 rows carry the copy.
SELECT COUNT(*) AS rows_expect_5 FROM allocation_type_guide WHERE purpose IS NOT NULL;

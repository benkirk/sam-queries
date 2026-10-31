-- ===========================================================================
-- seed_allocation_type_guide_pg.sql — the first guide rows (Postgres, sam_dev).
--
--   Apply after create_allocation_type_guide_pg.sql:
--     psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/seed_allocation_type_guide_pg.sql
--
-- Ben, 2026-10-31: SAM owns this copy, modeled on ARC's opportunities table
-- (frequency, funding eligibility; the limits come from
-- allocation_type_resource_limit). A NULL summary falls back to XRAS's own
-- description. Inserts only absent types, so a re-run never overwrites an
-- edit made on the admin card. The two dialects must carry the same rows
-- (tests/unit/models/test_allocation_type_guide.py).
-- ===========================================================================

INSERT INTO allocation_type_guide
  (xras_allocation_type_id, allocation_type, title, display_order, audience, cadence,
   funding, summary, notes, active, created_by, creation_time, modified_time)
SELECT v.xras_allocation_type_id, v.allocation_type, v.title, v.display_order, v.audience,
       v.cadence, v.funding, v.summary, v.notes, TRUE, 'seed', TIMESTAMP '2026-10-31 00:00:00', TIMESTAMP '2026-10-31 00:00:00'
  FROM (
         SELECT 500024, 'Small'               , 'Small'                 , 10, 'university', 'Continuous'              , 'NSF award required'       , 'For university researchers whose work is supported by an NSF award. The award is looked up and confirmed on the form.', 'Requesting Derecho and Derecho GPU together reduces each limit proportionally. A supplement may add up to the same limit again.'
    UNION ALL SELECT 500847, 'Small (No NSF award)', 'Exploratory'           , 20, 'university', 'Continuous'              , 'No external funding award', 'For university researchers and graduate students starting work that has no external funding award.', 'Requesting Derecho and Derecho GPU together reduces each limit proportionally. A supplement may add up to the same limit again.'
    UNION ALL SELECT 500026, 'Classroom'           , 'Classroom'             , 30, 'university', 'Continuous'              , 'No external funding award', 'For a university course or workshop: the instructor requests time for the class.', 'Requesting Derecho and Derecho GPU together reduces each limit proportionally.'
    UNION ALL SELECT 500848, 'Data'                , 'Data Analysis'         , 40, 'university', 'Continuous'              , 'Any funding source'       , 'For data analysis and visualization on Casper, from any funding source. No Derecho time.', NULL
    UNION ALL SELECT 500023, 'CHAP'                , 'Large'                 , 50, 'university', 'Semi-annual panel review', 'NSF award required'       , 'For large university projects supported by an NSF award, reviewed twice a year by the CISL HPC Allocation Panel. No upper limit, subject to availability.', NULL
    UNION ALL SELECT 501276, 'External Project'    , 'NCAR External Projects', 60, NULL        , NULL                      , NULL                       , NULL, NULL
    UNION ALL SELECT 500088, 'NSC'                 , 'NSC'                   , 70, 'ncar'      , NULL                      , NULL                       , NULL, NULL
  ) AS v (xras_allocation_type_id, allocation_type, title, display_order, audience, cadence,
          funding, summary, notes)
ON CONFLICT (xras_allocation_type_id) DO NOTHING;

-- Verification: 7 rows.
SELECT COUNT(*) AS rows_expect_7 FROM allocation_type_guide;

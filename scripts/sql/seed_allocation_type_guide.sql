-- ===========================================================================
-- seed_allocation_type_guide.sql — the first guide rows (MySQL).
--
--   Apply after create_allocation_type_guide.sql:
--     mysql -u <hpc-writer> -h <host> -p sam < scripts/sql/seed_allocation_type_guide.sql
--
-- Ben, 2026-10-31: SAM owns this copy, modeled on ARC's opportunities table
-- (frequency, funding eligibility; the limits come from
-- allocation_type_resource_limit). A NULL summary falls back to XRAS's own
-- description. Inserts only absent types, so a re-run never overwrites an
-- edit made on the admin card. The comparison-table copy is
-- seed_allocation_type_guide_overview.sql. The two dialects must carry the same rows
-- (tests/unit/models/test_allocation_type_guide.py).
-- ===========================================================================

INSERT IGNORE INTO allocation_type_guide
  (xras_allocation_type_id, allocation_type, title, display_order, audience, cadence,
   funding, summary, notes, active, created_by, creation_time, modified_time)
SELECT v.xras_allocation_type_id, v.allocation_type, v.title, v.display_order, v.audience,
       v.cadence, v.funding, v.summary, v.notes, 1, 'seed', '2026-10-31 00:00:00', '2026-10-31 00:00:00'
  FROM (
         SELECT 500024, 'Small'               , 'Small'                 , 10, 'university', 'Continuous'              , 'NSF award required'       , 'For university researchers whose work is supported by an NSF award.', 'Derecho and Derecho GPU are weighed together: a large request for one draws closer review of the other. You may later ask for a supplement of up to the same amount.'
    UNION ALL SELECT 500847, 'Small (No NSF award)', 'Exploratory'           , 20, 'university', 'Continuous'              , 'No external funding award', 'For university researchers starting out, with no external funding award. One Exploratory allocation per career stage: graduate student, postdoc, and faculty who have not held an NCAR allocation as faculty.', 'Derecho and Derecho GPU are weighed together: a large request for one draws closer review of the other. You may later ask for a supplement of up to the same amount.'
    UNION ALL SELECT 500026, 'Classroom'           , 'Classroom'             , 30, 'university', 'Continuous'              , 'No external funding award', 'An instructor requests the time on behalf of a course or workshop.', 'Derecho and Derecho GPU are weighed together: a large request for one draws closer review of the other.'
    UNION ALL SELECT 500848, 'Data'                , 'Data Analysis'         , 40, 'university', 'Continuous'              , 'Any funding source'       , 'Modest resources for analysis and visualization on Casper, whatever your funding. Does not include Derecho.', NULL
    UNION ALL SELECT 500023, 'CHAP'                , 'Large'                 , 50, 'university', 'Semi-annual panel review', 'NSF award required'       , 'For large university projects supported by an NSF award, reviewed twice a year by the CISL HPC Allocation Panel.', NULL
    UNION ALL SELECT 501276, 'External Project'    , 'NCAR External Projects', 60, 'ncar'      , 'Continuous'              , 'Budgeted or co-sponsored by the award', 'For NCAR-led projects on external awards, NSF or non-NSF, whose computing was budgeted or co-sponsored in the proposal. Needs at NSC scale go through NSC.', NULL
    UNION ALL SELECT 500088, 'NSC'                 , 'NSC'                   , 70, 'ncar'      , 'Semi-annual panel review', NULL                       , 'For large-scale NCAR projects reviewed twice a year by a panel of NCAR scientists; the minimum size depends on the lead lab.', NULL
  ) AS v (xras_allocation_type_id, allocation_type, title, display_order, audience, cadence,
          funding, summary, notes);

-- Verification: 7 rows.
SELECT COUNT(*) AS rows_expect_7 FROM allocation_type_guide;

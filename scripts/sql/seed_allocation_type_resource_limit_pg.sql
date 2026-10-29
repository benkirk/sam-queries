-- ===========================================================================
-- seed_allocation_type_resource_limit_pg.sql — the first limits (Postgres, sam_dev).
--
--   Apply after create_allocation_type_resource_limit_pg.sql:
--     psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/seed_allocation_type_resource_limit_pg.sql
--
-- Ben, 2026-10-29: defaults are the median award active projects of the type
-- hold; ceilings are ARC's published caps where they exist, else about p90;
-- CHAP and NSC get a high ceiling and no default; Data Analysis Casper is 1/1
-- (a yes, not a quota). Measured values: XRAS_SUBMISSION_SPRINT1.md § 4.
-- Both "Small" rows take the same limits. Inserts only absent pairs, so a
-- re-run never overwrites an edit made on the admin card. The _pg twin must
-- carry the same rows (tests/unit/models/test_allocation_type_limits.py).
-- ===========================================================================

INSERT INTO allocation_type_resource_limit
  (allocation_type_id, resource_id, min_amount, default_amount, max_amount, active,
   created_by, creation_time, modified_time)
SELECT at.allocation_type_id, r.resource_id, NULL, v.default_amount, v.max_amount, TRUE,
       'seed', TIMESTAMP '2026-10-29 00:00:00', TIMESTAMP '2026-10-29 00:00:00'
  FROM (
         SELECT 'Small'               , 'Derecho'       ,    500000,    1000000
    UNION ALL SELECT 'Small'               , 'Derecho GPU'   ,      1500,       2500
    UNION ALL SELECT 'Small'               , 'Casper'        ,      5000,      20000
    UNION ALL SELECT 'Small'               , 'Casper GPU'    ,      1500,       6000
    UNION ALL SELECT 'Small'               , 'Campaign_Store',         5,         20
    UNION ALL SELECT 'Small (No NSF award)', 'Derecho'       ,    500000,     500000
    UNION ALL SELECT 'Small (No NSF award)', 'Derecho GPU'   ,      1500,       1500
    UNION ALL SELECT 'Small (No NSF award)', 'Casper'        ,      5000,      10000
    UNION ALL SELECT 'Small (No NSF award)', 'Casper GPU'    ,      1000,       5000
    UNION ALL SELECT 'Small (No NSF award)', 'Campaign_Store',         5,         10
    UNION ALL SELECT 'Classroom'           , 'Derecho'       ,    400000,     500000
    UNION ALL SELECT 'Classroom'           , 'Derecho GPU'   ,      1500,       1500
    UNION ALL SELECT 'Classroom'           , 'Casper'        ,      5000,      10000
    UNION ALL SELECT 'Classroom'           , 'Casper GPU'    ,      1500,       4500
    UNION ALL SELECT 'Classroom'           , 'Campaign_Store',         2,          5
    UNION ALL SELECT 'Data'                , 'Casper'        ,         1,          1
    UNION ALL SELECT 'Data'                , 'Casper GPU'    ,         1,          1
    UNION ALL SELECT 'Data'                , 'Campaign_Store',         5,         20
    UNION ALL SELECT 'External Project'    , 'Derecho'       ,   2000000,    4000000
    UNION ALL SELECT 'External Project'    , 'Derecho GPU'   ,      5000,      50000
    UNION ALL SELECT 'External Project'    , 'Casper'        ,     10000,     100000
    UNION ALL SELECT 'External Project'    , 'Casper GPU'    ,       500,      20000
    UNION ALL SELECT 'External Project'    , 'Campaign_Store',        15,         50
    UNION ALL SELECT 'CHAP'                , 'Derecho'       ,      NULL,  500000000
    UNION ALL SELECT 'CHAP'                , 'Derecho GPU'   ,      NULL,    1000000
    UNION ALL SELECT 'CHAP'                , 'Casper'        ,      NULL,    1000000
    UNION ALL SELECT 'CHAP'                , 'Casper GPU'    ,      NULL,      50000
    UNION ALL SELECT 'CHAP'                , 'Campaign_Store',      NULL,       2000
    UNION ALL SELECT 'NSC'                 , 'Derecho'       ,      NULL,  200000000
    UNION ALL SELECT 'NSC'                 , 'Derecho GPU'   ,      NULL,     500000
    UNION ALL SELECT 'NSC'                 , 'Casper'        ,      NULL,     200000
    UNION ALL SELECT 'NSC'                 , 'Casper GPU'    ,      NULL,      20000
    UNION ALL SELECT 'NSC'                 , 'Campaign_Store',      NULL,       2000
  ) AS v (allocation_type, resource_name, default_amount, max_amount)
  JOIN allocation_type at ON at.allocation_type = v.allocation_type AND at.active
  JOIN resources r ON r.resource_name = v.resource_name
ON CONFLICT (allocation_type_id, resource_id) DO NOTHING;

-- Verification: 38 rows (both "Small" types; 33 pairs).
SELECT COUNT(*) AS rows_expect_38 FROM allocation_type_resource_limit;

-- ===========================================================================
-- Restore project memberships lost to erroneous user-account closures
--
--   Generated : 2026-07-24
--   Target    : prod `sam` (sam-sql.ucar.edu).  Requires MySQL 8.0+ (CTEs).
--   Upstream  : NCAR/sam-ldap-syncd  (tracking issue filed alongside this file)
--
-- ---------------------------------------------------------------------------
-- THE PROBLEM
-- ---------------------------------------------------------------------------
-- An upstream process -- the LDAP/IdMS-IGA sync, NOT any code in sam-queries;
-- nothing in this repo bulk-end-dates `account_user` -- closes a user account
-- by end-dating EVERY one of that user's `account_user` rows in a single
-- instant.  When the closure is later reversed, only `users.active` /
-- `users.locked` are restored.  The memberships are not.  The user is left as
-- an open account with zero project access: they can log in and see nothing.
--
-- Found while repairing `jpereira`, whose 7 memberships were closed at
-- 2026-07-17 00:14:54 and whose user record was reopened at
-- 2026-07-22 06:48:50.  She was repaired by hand and is NOT in this script.
--
-- This is recurring, not a one-off IGA-cutover accident.  As of 2026-07-24:
--
--     July 2026 (IGA cutover)         12 users    105 rows    27 projects
--     Jan-Jun 2026 (same signature)   39 users    224 rows    57 projects
--     -------------------------------------------------------------------
--     Total                           51 users    329 rows
--
-- Several affected users are actively computing: `marialrocha` charged jobs on
-- 2026-07-24 with 4 orphaned rows; `rpfernan`, `lli226`, `jsachnik`, `vstock`,
-- `cstan` and `melgin` all charged on 07-23/07-24.
--
-- ---------------------------------------------------------------------------
-- THE REPAIR
-- ---------------------------------------------------------------------------
-- Clear `end_date` on the erroneously-closed rows (set it back to NULL).  This
-- preserves the original `start_date`, so membership history is continuous --
-- as if the close never happened.  Every row closed at the instant is restored,
-- including accounts on retired resources (Cheyenne, GLADE fs1, Yellowstone)
-- and projects with no live allocation: a partial restore would leave the user
-- in the "partial access" state the project member card flags with a warning.
--
-- ---------------------------------------------------------------------------
-- DETECTION RULE  (all four must hold)
-- ---------------------------------------------------------------------------
--   1. Whole-account close -- the row's end_date instant `t` closed EVERY
--      membership the user had live at `t`.  A user-level close, not a
--      project-level removal.
--   2. User account is open today -- users.active = 1 AND users.locked = 0.
--   3. Reopened after the close -- users.modified_time > t.  Evidence the
--      closure was reversed at the user level but never at the membership
--      level.
--   4. Not already restored -- no live row exists for that (user_id,
--      account_id) today.  Prevents duplicate live memberships for users
--      already partially repaired (rpfernan 8 of 18, lli226 6 of 9,
--      marialrocha 3 of 7).
--
-- Two false-positive modes the rule deliberately excludes, both present in the
-- data:
--
--   * Single-project users.  For someone on one project, "all memberships
--     ended at once" is trivially true of any ordinary removal.  Rule 3 kills
--     this: the whole 2026-06-03 11:49:12 event (91 users, one project each)
--     has users.modified_time at 2026-01-08 or NULL -- before the close.
--
--   * Resource retirements.  2026-07-21 15:07:59 ended 360 rows for 180 users,
--     but only on P19010000's Cheyenne / GLADE fs1 accounts -- a decommission,
--     and a partial close, so rule 1 rejects it.  Likewise SCSG0002 at
--     2026-07-20 09:24:27.
--
-- ---------------------------------------------------------------------------
-- NAMED EXCEPTION: yifanc
-- ---------------------------------------------------------------------------
-- `yifanc` (closed 2026-07-23 11:08:35, 25 rows across 5 projects) fails rule 3
-- -- users.modified_time equals the close instant exactly, so there is no
-- reopen to point at.  Charge history settles it: she ran jobs on 4 of the 5
-- closed projects within 90 days, including on the day of the close.
--
--     P93300041   6 rows   Casper through 2026-07-23 (close day), 11 days
--     P48500028   8 rows   Casper through 2026-07-15, 7 days
--     UCUB0117    5 rows   Derecho through 2026-07-12, 3,142 core-hours
--     UCUB0089    3 rows   Casper to 2026-06-08; Campaign_Store to 2026-07-18
--     UBUF0002    3 rows   no activity in 90 days -- but she is its LEAD
--
-- She is unioned in below as an explicit named exception -- NOT by loosening
-- rule 3.  UBUF0002 is included: she is the project lead, it was closed by the
-- same erroneous instant, and a quiet 90 days on one project is not evidence of
-- departure from someone who charged four others that week.  (Her UEDV0001
-- Campaign_Store charges are residue from a separate 2024 removal -- out of
-- scope, left alone.)
--
-- ---------------------------------------------------------------------------
-- SIDE EFFECT WORTH KNOWING: LEADS AND ADMINS LOST THEIR OWN MEMBERSHIPS
-- ---------------------------------------------------------------------------
-- 6 of the 12 users in the July window lead or administer one of the affected
-- projects, so the closure violated the lead-and-admin-are-always-members
-- invariant that Account._seed_members maintains:
--
--     afolorunsho  UBAY0004 (lead)     jinmuluo   UCOR0067 (lead)
--     collazo      UGAT0014 (lead)     nmariscal  UUWS0003 (lead)
--     collazo      UGAT0015 (lead)     yifanc     UBUF0002 (lead)
--     huangkai     UNEB0016 (admin)
--
-- Note this masks the symptom: User.active_projects() unions led_projects and
-- admin_projects, so a lead with zero membership rows still reports 1 active
-- project in the UI and CLI -- they look fine while having no resource access
-- at all.  The project member card also suppresses its partial-access warning
-- for the lead by design, so nothing flags them.  Do not use "shows at least
-- one project" as evidence a user is healthy.
--
-- ---------------------------------------------------------------------------
-- DELIBERATELY NOT REPAIRED
-- ---------------------------------------------------------------------------
-- Whole-account closes on open accounts with zero live rows, but no evidence
-- of a reopen and no charge history at all.  Reviewed and held back:
--
--     hojungy   2026-07-02 11:30:52   4 rows (NERP0001)   user row last
--                                     touched 2026-04-07, never reopened
--     egerber   2026-06-16 10:29:12   3 rows              same
--     xumin     2026-06-23 13:32:40   1 row               same
--
-- ---------------------------------------------------------------------------
-- HOW TO RUN
-- ---------------------------------------------------------------------------
--   Section 1  REPORT   -- read-only.  Review this before anything else.
--   Section 2  PIN      -- read-only.  Emits the account_user_id work list and
--                          the ready-to-run rollback statements.  SAVE BOTH.
--   Section 3  REPAIR   -- commented out.  Uncomment only after review.
--   Section 4  VERIFY   -- read-only.
--
-- Sections 1, 2 and 4 are safe to run repeatedly.  Section 3 is the only
-- statement that writes to `account_user`.
--
-- Connecting to prod:
--
--     mysql -h sam-sql.ucar.edu -u <writer-user> -p'<pass>' sam
--
-- Use inline -p, NOT the MYSQL_PWD environment variable: ~/.my.cnf exists on
-- the operator workstation and MySQL option files take precedence over the
-- environment, so MYSQL_PWD is silently ignored and the connection is refused.
--
-- Grants on that account are SELECT, INSERT, UPDATE, DELETE on `sam`.* --
-- there is no CREATE privilege, so this script uses NO DDL.  The work list is
-- pinned by pasting an explicit id list into @IDS rather than by materialising
-- a backup table, and the rollback is a set of generated UPDATE statements
-- saved to a file before anything is written.
-- ===========================================================================


-- ===========================================================================
-- SECTION 0 -- parameters
-- ===========================================================================
-- Repair window.  '2026-07-01' = the IGA-cutover cohort only.
--                 '2026-01-01' = the full back-fill (see counts above).
SET @SINCE = '2026-07-01';

-- Pin "now" so every section agrees even if the run spans a minute boundary.
SET @ASOF = NOW();


-- ===========================================================================
-- SECTION 1 -- REPORT (read-only)
-- ===========================================================================

-- 1a. One row per membership to be restored.
WITH ev AS (
    SELECT au.user_id, au.end_date AS t, COUNT(*) AS ended
      FROM account_user au
     WHERE au.end_date >= @SINCE
       AND au.end_date <  @ASOF
     GROUP BY au.user_id, au.end_date
),
whole AS (                                          -- rule 1
    SELECT ev.user_id, ev.t
      FROM ev
     WHERE ev.ended = (
               SELECT COUNT(*)
                 FROM account_user x
                WHERE x.user_id     =  ev.user_id
                  AND x.start_date  <= ev.t
                  AND (x.end_date IS NULL OR x.end_date >= ev.t))
),
target AS (
    SELECT au.account_user_id, au.user_id, au.account_id, w.t AS closed_at
      FROM whole w
      JOIN users        u  ON u.user_id  = w.user_id
      JOIN account_user au ON au.user_id = w.user_id AND au.end_date = w.t
     WHERE u.active = 1 AND u.locked = 0             -- rule 2
       AND u.modified_time > w.t                     -- rule 3
       AND NOT EXISTS (                              -- rule 4
               SELECT 1 FROM account_user l
                WHERE l.user_id    = au.user_id
                  AND l.account_id = au.account_id
                  AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
exception_rows AS (                                  -- named exception: yifanc
    SELECT au.account_user_id, au.user_id, au.account_id, au.end_date AS closed_at
      FROM account_user au
      JOIN users u ON u.user_id = au.user_id
     WHERE u.username  = 'yifanc'
       AND au.end_date = '2026-07-23 11:08:35'
       AND au.end_date >= @SINCE
       AND NOT EXISTS (                              -- rule 4 still applies
               SELECT 1 FROM account_user l
                WHERE l.user_id    = au.user_id
                  AND l.account_id = au.account_id
                  AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
candidate AS (
    SELECT * FROM target
    UNION
    SELECT * FROM exception_rows
)
SELECT u.username,
       c.closed_at,
       u.modified_time                                       AS reopened_at,
       p.projcode,
       r.resource_name,
       au.start_date                                         AS original_start,
       EXISTS(SELECT 1 FROM allocation al
               WHERE al.account_id  = a.account_id
                 AND al.start_date <= @ASOF
                 AND (al.end_date IS NULL OR al.end_date >= @ASOF))
                                                             AS on_live_alloc,
       (SELECT MAX(cs.activity_date) FROM comp_charge_summary cs
         WHERE cs.user_id = u.user_id)                       AS last_charge,
       c.account_user_id
  FROM candidate c
  JOIN account_user au ON au.account_user_id = c.account_user_id
  JOIN users        u  ON u.user_id          = c.user_id
  JOIN account      a  ON a.account_id       = c.account_id
  JOIN project      p  ON p.project_id       = a.project_id
  JOIN resources    r  ON r.resource_id      = a.resource_id
 ORDER BY c.closed_at, u.username, p.projcode, r.resource_name;


-- 1b. Per-user roll-up -- the review table.
WITH ev AS (
    SELECT au.user_id, au.end_date AS t, COUNT(*) AS ended
      FROM account_user au
     WHERE au.end_date >= @SINCE AND au.end_date < @ASOF
     GROUP BY au.user_id, au.end_date
),
whole AS (
    SELECT ev.user_id, ev.t FROM ev
     WHERE ev.ended = (SELECT COUNT(*) FROM account_user x
                        WHERE x.user_id = ev.user_id
                          AND x.start_date <= ev.t
                          AND (x.end_date IS NULL OR x.end_date >= ev.t))
),
target AS (
    SELECT au.account_user_id, au.user_id, au.account_id, w.t AS closed_at
      FROM whole w
      JOIN users u ON u.user_id = w.user_id
      JOIN account_user au ON au.user_id = w.user_id AND au.end_date = w.t
     WHERE u.active = 1 AND u.locked = 0
       AND u.modified_time > w.t
       AND NOT EXISTS (SELECT 1 FROM account_user l
                        WHERE l.user_id = au.user_id AND l.account_id = au.account_id
                          AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
exception_rows AS (
    SELECT au.account_user_id, au.user_id, au.account_id, au.end_date AS closed_at
      FROM account_user au JOIN users u ON u.user_id = au.user_id
     WHERE u.username = 'yifanc' AND au.end_date = '2026-07-23 11:08:35'
       AND au.end_date >= @SINCE
       AND NOT EXISTS (SELECT 1 FROM account_user l
                        WHERE l.user_id = au.user_id AND l.account_id = au.account_id
                          AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
candidate AS (SELECT * FROM target UNION SELECT * FROM exception_rows)
SELECT u.username,
       c.closed_at,
       u.modified_time                          AS reopened_at,
       COUNT(*)                                 AS rows_to_restore,
       COUNT(DISTINCT a.project_id)             AS projects,
       SUM(EXISTS(SELECT 1 FROM allocation al
                   WHERE al.account_id  = a.account_id
                     AND al.start_date <= @ASOF
                     AND (al.end_date IS NULL OR al.end_date >= @ASOF)))
                                                AS on_live_alloc,
       GROUP_CONCAT(DISTINCT p.projcode ORDER BY p.projcode SEPARATOR ' ')
                                                AS projcodes,
       (SELECT MAX(cs.activity_date) FROM comp_charge_summary cs
         WHERE cs.user_id = u.user_id)          AS last_charge
  FROM candidate c
  JOIN users   u ON u.user_id    = c.user_id
  JOIN account a ON a.account_id = c.account_id
  JOIN project p ON p.project_id = a.project_id
 GROUP BY u.user_id, c.closed_at
 ORDER BY c.closed_at;


-- ===========================================================================
-- SECTION 2 -- PIN + ROLLBACK PREP  (read-only; no DDL, nothing is written)
-- ===========================================================================
-- Pinning the work list as a literal id list freezes the decision: a later data
-- change cannot silently widen the repair between review and apply.  Section 3
-- and Section 4 both read @IDS, so the reviewed set lives in exactly one place.

-- 2a. Emit the work list, then paste it into the SET @IDS below.
WITH ev AS (
    SELECT au.user_id, au.end_date AS t, COUNT(*) AS ended
      FROM account_user au
     WHERE au.end_date >= @SINCE AND au.end_date < @ASOF
     GROUP BY au.user_id, au.end_date
),
whole AS (
    SELECT ev.user_id, ev.t FROM ev
     WHERE ev.ended = (SELECT COUNT(*) FROM account_user x
                        WHERE x.user_id = ev.user_id
                          AND x.start_date <= ev.t
                          AND (x.end_date IS NULL OR x.end_date >= ev.t))
),
target AS (
    SELECT au.account_user_id, au.user_id, au.account_id, w.t AS closed_at
      FROM whole w
      JOIN users u ON u.user_id = w.user_id
      JOIN account_user au ON au.user_id = w.user_id AND au.end_date = w.t
     WHERE u.active = 1 AND u.locked = 0
       AND u.modified_time > w.t
       AND NOT EXISTS (SELECT 1 FROM account_user l
                        WHERE l.user_id = au.user_id AND l.account_id = au.account_id
                          AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
exception_rows AS (
    SELECT au.account_user_id, au.user_id, au.account_id, au.end_date AS closed_at
      FROM account_user au JOIN users u ON u.user_id = au.user_id
     WHERE u.username = 'yifanc' AND au.end_date = '2026-07-23 11:08:35'
       AND au.end_date >= @SINCE
       AND NOT EXISTS (SELECT 1 FROM account_user l
                        WHERE l.user_id = au.user_id AND l.account_id = au.account_id
                          AND (l.end_date IS NULL OR l.end_date >= @ASOF))
),
candidate AS (SELECT * FROM target UNION SELECT * FROM exception_rows)
SELECT COUNT(*)                AS rows_pinned,
       COUNT(DISTINCT user_id) AS users_pinned,
       GROUP_CONCAT(account_user_id ORDER BY account_user_id) AS id_list
  FROM candidate;

-- 2b. Paste the id_list from 2a here.  Everything below this line operates on
--     exactly these rows and nothing else.
SET @IDS = '';      -- <<< PASTE HERE, e.g. '129160,129162,135768,...'

-- 2c. Sanity-check the pasted list against what 2a reported.  These counts MUST
--     match rows_pinned / users_pinned above before going any further.
SELECT COUNT(*)                AS rows_pinned,
       COUNT(DISTINCT user_id) AS users_pinned,
       MIN(end_date)           AS earliest_close,
       MAX(end_date)           AS latest_close
  FROM account_user
 WHERE FIND_IN_SET(account_user_id, @IDS);

-- 2d. Generate the rollback statements NOW, before anything is written, and
--     save the output to a file.  Every row a user lost shares one close
--     instant, so this is one statement per closure event (~12 for the July
--     window, ~51 for the full back-fill).
--
--     e.g.  mysql -h sam-sql.ucar.edu -u <writer-user> -p'<pass>' sam -N -B \
--             < this-section > rollback-20260724.sql
SELECT CONCAT('UPDATE account_user SET end_date = ''', end_date,
              ''' WHERE account_user_id IN (',
              GROUP_CONCAT(account_user_id ORDER BY account_user_id), ');')
                                        AS rollback_sql
  FROM account_user
 WHERE FIND_IN_SET(account_user_id, @IDS)
 GROUP BY end_date
 ORDER BY end_date;


-- ===========================================================================
-- SECTION 3 -- REPAIR  ** COMMENTED OUT -- UNCOMMENT ONLY AFTER REVIEW **
-- ===========================================================================
-- Drives off @IDS, not off a re-run of the detection rule, so what was
-- approved in Sections 1 and 2 is exactly what gets written.
--
-- PRECONDITION: the rollback statements from 2d are saved to a file.
-- Check that ROW_COUNT() equals `rows_pinned` from Section 2 BEFORE committing.
--
-- START TRANSACTION;
--
-- UPDATE account_user
--    SET end_date = NULL
--  WHERE FIND_IN_SET(account_user_id, @IDS);
--
-- SELECT ROW_COUNT() AS rows_updated;      -- must equal rows_pinned
--
-- COMMIT;      -- or ROLLBACK; if the count does not match


-- ===========================================================================
-- SECTION 4 -- VERIFY (read-only)
-- ===========================================================================
-- Requires @IDS from Section 2b to still be set in this session.

-- 4a. Every pinned row is now live.  Expect zero rows.
SELECT au.account_user_id, u.username, au.end_date
  FROM account_user au
  JOIN users u ON u.user_id = au.user_id
 WHERE FIND_IN_SET(au.account_user_id, @IDS)
   AND au.end_date IS NOT NULL;

-- 4b. No repaired user is left in a "partial access" state -- for every
--     project they now belong to, every account with a live allocation has a
--     live membership row.  Expect zero rows.  (This mirrors what
--     Project.get_members_access_status() reports on the member card.)
SELECT DISTINCT u.username, p.projcode, r.resource_name AS missing_resource
  FROM (SELECT DISTINCT user_id FROM account_user
         WHERE FIND_IN_SET(account_user_id, @IDS)) fixed
  JOIN users        u   ON u.user_id     = fixed.user_id
  JOIN account_user mine ON mine.user_id = u.user_id
                        AND (mine.end_date IS NULL OR mine.end_date >= NOW())
  JOIN account      ma  ON ma.account_id = mine.account_id
  JOIN project      p   ON p.project_id  = ma.project_id
  JOIN account      a   ON a.project_id  = p.project_id AND a.deleted = 0
  JOIN resources    r   ON r.resource_id = a.resource_id
 WHERE EXISTS (SELECT 1 FROM allocation al
                WHERE al.account_id  = a.account_id
                  AND al.start_date <= NOW()
                  AND (al.end_date IS NULL OR al.end_date >= NOW()))
   AND NOT EXISTS (SELECT 1 FROM account_user l
                    WHERE l.user_id    = u.user_id
                      AND l.account_id = a.account_id
                      AND (l.end_date IS NULL OR l.end_date >= NOW()))
 ORDER BY u.username, p.projcode, r.resource_name;

-- 4c. ROLLBACK -- run the statements saved from Section 2d, verbatim, inside a
--     transaction.  They restore the exact prior end_date values.  Nothing
--     needs to be reconstructed: 2d captured the pre-image before the write.
--
--     START TRANSACTION;
--     SOURCE rollback-20260724.sql;
--     COMMIT;
--
-- 4d. Follow-up once verified: the affected users' project cards should show no
--     partial-access warning, and
--
--         sam-search user <name> --list-projects
--
--     should report Membership = Active for every project.

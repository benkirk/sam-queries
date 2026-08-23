# Runbook — "my projects are missing"

A user was deactivated, later reactivated, and now reports that their projects
are gone. This is a **known, recurring defect** in the LDAP/IdMS-IGA sync, not
a SAM bug. This runbook takes you from the ticket to a verified fix.

Background, blast radius, and the July 2026 bulk repair:
[`RESTORED-2026-07-24.md`](RESTORED-2026-07-24.md) ·
[`2026-07-restore-orphaned-memberships.sql`](2026-07-restore-orphaned-memberships.sql)
· upstream tracking issue **NCAR/sam-ldap-syncd#3**.

---

## The failure, in one paragraph

Closing a user account end-dates **every** one of that user's `account_user`
rows in a single instant. Reopening the account restores only `users.active`
and `users.locked` — **nothing restores the memberships**. The user is left as
an open, unlocked account with zero project access. As of 2026-07-24 there were
**39 more users / 224 rows** carrying this signature from January–June 2026 that
were deliberately left alone; expect them to surface one at a time as people
come back and complain.

---

## Before you start

```bash
mysql -h sam-sql.ucar.edu -u <writer-user> -p'<pass>' sam
```

Two things that will waste your time otherwise:

- **Use inline `-p`, not `MYSQL_PWD`.** `~/.my.cnf` exists on the operator
  workstation and MySQL option files take precedence over the environment
  variable, so `MYSQL_PWD` is silently ignored and the connection is refused
  with a confusing `Access denied`.
- **The writer account has no DDL.** Grants are `SELECT, INSERT, UPDATE, DELETE` on
  `sam.*`. No `CREATE TABLE`, so no scratch/backup tables — capture rollback
  state as generated SQL text instead (Step 3 does this).

---

## Step 1 — Triage (60 seconds)

Set the username once; both queries read it.

```sql
SET @USERNAME = 'jdoe';       -- <<< the reporting user

-- 1a. Account state and what they can currently see.
SELECT u.username, u.active, u.locked,
       u.modified_time AS user_last_touched,
       (SELECT COUNT(*) FROM account_user l
         WHERE l.user_id = u.user_id
           AND (l.end_date IS NULL OR l.end_date >= NOW())) AS live_rows
  FROM users u
 WHERE u.username = @USERNAME;

-- 1b. Closure events in the last ~13 months, newest first.
--     `rows_ended` == `live_just_before` means that instant closed EVERYTHING
--     the user had -- an account-level close, not a project removal.
SELECT au.end_date AS closed_at,
       COUNT(*)    AS rows_ended,
       (SELECT COUNT(*) FROM account_user x
         WHERE x.user_id = au.user_id
           AND x.start_date <= au.end_date
           AND (x.end_date IS NULL OR x.end_date >= au.end_date)) AS live_just_before,
       SUM(NOT EXISTS(SELECT 1 FROM account_user l
                       WHERE l.user_id    = au.user_id
                         AND l.account_id = au.account_id
                         AND (l.end_date IS NULL OR l.end_date >= NOW()))) AS still_orphaned
  FROM account_user au
  JOIN users u ON u.user_id = au.user_id
 WHERE u.username = @USERNAME
   AND au.end_date IS NOT NULL
   AND au.end_date >= DATE_SUB(NOW(), INTERVAL 400 DAY)
 GROUP BY au.user_id, au.end_date
 ORDER BY au.end_date DESC
 LIMIT 10;
```

Rows already repaired have `end_date = NULL` and drop out of 1b entirely — so a
clean 1b after a fix is the expected result, not a missing answer.

## Step 2 — Read the output

| What you see                                                                                               | What it means                                                                                                      | Do                                                      |
|------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| `active=0` or `locked=1`                                                                                   | The account is still closed. Memberships are the *symptom*, not the problem.                                       | Get the account reopened first, then re-triage.         |
| `rows_ended == live_just_before`, `still_orphaned > 0`, and `user_last_touched` **later than** `closed_at` | **The known defect.** Closed, reopened, memberships never restored.                                                | Step 3.                                                 |
| `rows_ended < live_just_before`                                                                            | An ordinary project removal or a resource retirement — only part of their access ended.                            | Not this defect. Treat as a normal membership question. |
| `rows_ended == live_just_before` but `user_last_touched` is **older than** `closed_at`                     | Whole-account close with no evidence of a reopen. Could be a real departure whose `active` flag was never flipped. | **Do not auto-restore.** Step 5.                        |
| `live_rows > 0` but the user still says projects are missing                                               | Partial access — they hold some accounts and not others.                                                           | Use the ⚠ grant on the project member card (Step 4B).   |

### Two traps

- **A Lead or Admin looks healthy when they are not.**
  `User.active_projects()` unions `led_projects` and `admin_projects`, so a lead
  with **zero** membership rows still shows an active project in the UI and
  CLI — while having no resource access at all. The project member card also
  suppresses its partial-access ⚠ for the lead by design. 7 of the 27 pairs in
  the July repair were Lead/Admin. **Never** conclude "they're fine" from the
  project count; use `live_rows` from 1a.

- **`rows_ended == live_just_before` is trivially true for single-project
  users.** Someone who only ever had one project satisfies it on any ordinary
  removal. That is why the "reopened after the close" check matters — it is
  what separates the defect from a routine removal.

## Step 3 — Repair

Restores the exact rows that closure took, clearing `end_date` back to `NULL`
so the original `start_date` is preserved and membership history stays
continuous. Substitute the `closed_at` value from 1b.

```sql
SET @USERNAME  = 'jdoe';
SET @CLOSED_AT = '2026-07-17 00:07:36';    -- <<< from query 1b

-- 3a. Review exactly what will change. Read this before going further.
SELECT au.account_user_id, p.projcode, r.resource_name,
       au.start_date, au.end_date,
       CASE WHEN p.project_lead_user_id  = u.user_id THEN 'Lead'
            WHEN p.project_admin_user_id = u.user_id THEN 'Admin'
            ELSE 'Member' END AS role
  FROM account_user au
  JOIN users     u ON u.user_id     = au.user_id
  JOIN account   a ON a.account_id  = au.account_id
  JOIN project   p ON p.project_id  = a.project_id
  JOIN resources r ON r.resource_id = a.resource_id
 WHERE u.username  = @USERNAME
   AND au.end_date = @CLOSED_AT
   AND NOT EXISTS (SELECT 1 FROM account_user l          -- skip any already restored
                    WHERE l.user_id    = au.user_id
                      AND l.account_id = au.account_id
                      AND (l.end_date IS NULL OR l.end_date >= NOW()))
 ORDER BY p.projcode, r.resource_name;

-- 3b. Save the rollback BEFORE writing. Copy the output to a file.
SELECT CONCAT('UPDATE account_user SET end_date = ''', @CLOSED_AT,
              ''' WHERE account_user_id IN (',
              GROUP_CONCAT(au.account_user_id ORDER BY au.account_user_id), ');')
                                                        AS rollback_sql
  FROM account_user au
  JOIN users u ON u.user_id = au.user_id
 WHERE u.username  = @USERNAME
   AND au.end_date = @CLOSED_AT
   AND NOT EXISTS (SELECT 1 FROM account_user l
                    WHERE l.user_id    = au.user_id
                      AND l.account_id = au.account_id
                      AND (l.end_date IS NULL OR l.end_date >= NOW()));

-- 3c. Apply. Confirm rows_updated matches the row count from 3a before COMMIT.
START TRANSACTION;

UPDATE account_user au
  JOIN users u ON u.user_id = au.user_id
   SET au.end_date = NULL
 WHERE u.username  = @USERNAME
   AND au.end_date = @CLOSED_AT;

SELECT ROW_COUNT() AS rows_updated;

COMMIT;      -- or ROLLBACK; if the count does not match 3a
```

If 1b shows **more than one** qualifying whole-close instant, repeat Step 3 per
instant. Do not widen the `WHERE` to "all closed rows" — that would resurrect
legitimate removals.

## Step 4 — When SQL is not the right tool

**4A. One project, and the user should simply be re-added.**
Use *Project → Members → Add Member* in the web UI. Since #368 this correctly
creates a fresh open row even when a stale end-dated row exists. On anything
before #368 it is a silent no-op, which is how the index case went unnoticed. Leaves a visible gap
in membership history — fine for a genuine re-join, wrong for undoing an
erroneous close.

**4B. The user has *some* access but is missing resources.**
Open the project member card. Members with gaps show a muted-yellow ⚠ next to
their name; click it to grant every resource they are missing in one action
(#369). Faster than SQL and fully audited. Note again that the ⚠ is suppressed
for the project lead.

**Choosing:** undoing an erroneous close → Step 3 (preserves history). A real
re-join, or filling gaps → Step 4.

## Step 5 — When not to repair

If the close has **no evidence of a reopen** (`user_last_touched` older than
`closed_at`) and the user has no recent charge history, stop and confirm with
the requester that the person is genuinely returning. Three users were
deliberately held back on exactly this basis during the July repair —
`hojungy`, `egerber`, `xumin`.

Useful supporting evidence:

```sql
SELECT p.projcode, r.resource_name,
       MIN(cs.activity_date) AS first_day, MAX(cs.activity_date) AS last_day,
       COUNT(*) AS charge_days, ROUND(SUM(cs.charges),1) AS charges
  FROM comp_charge_summary cs
  JOIN users     u ON u.user_id     = cs.user_id
  JOIN account   a ON a.account_id  = cs.account_id
  JOIN project   p ON p.project_id  = a.project_id
  JOIN resources r ON r.resource_id = a.resource_id
 WHERE u.username = @USERNAME
   AND cs.activity_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
 GROUP BY p.projcode, r.resource_name
 ORDER BY last_day DESC;
```

Jobs charged at or after the close instant is decisive — that person did not
depart. It is what settled `yifanc`'s case, whose closure otherwise looked
ambiguous because it set `modified_time` without clearing `active`.

## Step 6 — Verify

1. Re-run **1a**: `live_rows` should now be non-zero and `active=1, locked=0`.
2. Re-run **1b**: the repaired instant should be gone from the list.
3. No residual partial access — expect **zero rows**:

```sql
SELECT DISTINCT p.projcode, r.resource_name AS still_missing
  FROM users u
  JOIN account_user mine ON mine.user_id = u.user_id
                        AND (mine.end_date IS NULL OR mine.end_date >= NOW())
  JOIN account   ma ON ma.account_id  = mine.account_id
  JOIN project   p  ON p.project_id   = ma.project_id
  JOIN account   a  ON a.project_id   = p.project_id AND a.deleted = 0
  JOIN resources r  ON r.resource_id  = a.resource_id
 WHERE u.username = @USERNAME
   AND EXISTS (SELECT 1 FROM allocation al
                WHERE al.account_id  = a.account_id
                  AND al.start_date <= NOW()
                  AND (al.end_date IS NULL OR al.end_date >= NOW()))
   AND NOT EXISTS (SELECT 1 FROM account_user l
                    WHERE l.user_id    = u.user_id
                      AND l.account_id = a.account_id
                      AND (l.end_date IS NULL OR l.end_date >= NOW()))
 ORDER BY p.projcode, r.resource_name;
```

4. Application layer:

```bash
sam-search user <username> --list-projects       # Membership = Active on every row
```

5. Load the project member card and confirm no ⚠ remains.
6. Reply to the ticket. Ask the user to confirm — group membership propagates to
   the HPC systems on the next sync, not instantly.

## Step 7 — Log it

Add a line to [`RESTORED-2026-07-24.md`](RESTORED-2026-07-24.md), or start a new
`RESTORED-<date>.md` if this is a fresh batch: username, project(s), rows,
close instant, and why you judged it the defect. Comment on
**NCAR/sam-ldap-syncd#3** with the new occurrence — recurrence data is what will
justify the upstream fix.

---

## Periodic sweep

Rather than waiting for tickets, run the bulk detector to find everyone
currently in this state:

```bash
mysql -h sam-sql.ucar.edu -u <writer-user> -p'<pass>' sam -t \
  < scripts/repair/2026-07-restore-orphaned-memberships.sql
```

Sections 1 and 2 are read-only; section 3 is commented out. Adjust `@SINCE` at
the top of the file — `'2026-01-01'` covers the full known backlog. The
detection rule, its two documented false-positive modes, and the reviewed
exclusions are all in that file's header.

Worth doing after any large IdMS/IGA change, and quarterly otherwise.

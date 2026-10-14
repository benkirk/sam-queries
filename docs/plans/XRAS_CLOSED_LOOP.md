# XRAS closed loop: SAMuel renames requests, then the submission plan under load

**Status: Track 1 built (#819), proven on XRAS-dev, backfilled in production and enabled there
2026-10-14 (#820; § 3.3). Track 2 run in full 2026-10-14 (§ 4).** What
landed and what was decided: § 3.5. The wider goal this is phase A of:
[`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md). Written 2026-10-13 at the end of the session recorded in
[`XRAS_DEV_PROBES.md`](../xras/outgoing/XRAS_DEV_PROBES.md). Two tracks on one test bed:
**Track 1** builds the projcode rename in SAMuel and backfills production; **Track 2**
stress-tests [`XRAS_SUBMISSION.md`](XRAS_SUBMISSION.md) on samuel-dev ↔ XRAS-dev. Track 1
does not wait for the submission UX.

## 1. Why

When a `New` lands, SAM mints a projcode, but XRAS keeps calling the request `NCAR####`
until someone renames it. Until 2026-10-13 we thought XRAS did that on its own schedule.
Steve: XRAS ignores the reply to the accounting-service post, and NCAR staff renamed requests
by hand. The person who did it has left. Every action filed on an unrenamed family arrives
under the `NCAR` number, finds no project, and parks while XRAS reports a successful post
(`XRAS_DEV_PROBES.md` § 4, #7 and #8). The award letter the PI gets at notify shows whatever
number the request has at that moment.

SAM can do the rename: `PUT /v1/requests/<request_id>/attributes?requestNumber=<projcode>`,
under `submit`, as the PI, renamed NCAR4087 → UHSS0002 in place, and an Extension filed
afterwards applied (#12). Ben (2026-10-13): **SAMuel owns the rename**, right after the post,
with a task backstop that also runs a one-time backfill.

**Why XRAS never learns the projcode.** ACCESS's own accounting service
(`https://allocations-api.access-ci.org/xacct/apidoc.html`, read 2026-10-13) keys everything
on XRAS's request number: `GET /v1/users/projects/<u>` lists projects by `grantNumber` (the
XRAS number) with each `requestId`, and every route XRAS calls is number-keyed
(`requests/request/<n>`, `dates/requests/<list>`, `roles/<n>/…`, `users/<n>/…`,
`usage/by_month/<n>/…`). A service with its own identifier reports it back as `projectId` under
`projectIdLabel`; nothing renames anything, and `POST /v1/actions` documents no reply body. The
rename is NCAR's convention, needed because SAM mints its own projcodes. So SAM needs both
models: resolve the NCAR number from its log (§ 4.6 item 2, xacct's way) and rename XRAS to the
projcode (item 3), because PIs see the number and XRAS's other number-keyed calls to SAM
(`/roles`, `/dates/requests`, `/requests/request`) resolve it as a projcode.

Read first: `XRAS_DEV_PROBES.md` (§ 3 write ladder, § 4 timeline, § 5 rewrite, § 7 state left
on demo); `XRAS_SUBMISSION.md` § 4.2 (the three identifiers), § 4.6 items 1–4, § 5;
[`XRAS_REQUEST_TICKETS.md`](XRAS_REQUEST_TICKETS.md) § 7; `.claude/rules/notify-and-tasks.md`.
CLAUDE.md tripwire: `SAM_TASKS_DISABLED` is fail-OPEN, so a new task ships named in
`helm/values.yaml`'s disabled list in the same change.

## 2. The test bed and how to drive it

| Piece | How |
|---|---|
| XRAS-dev API | `https://xras-submit-api-demo.xsede.org/v1`, process `NCAR`, the production `XRAS_API_KEY` from `../.env`. Headers `XA-API-KEY`, `XA-ALLOCATIONS-PROCESS: NCAR`, `XA-CONTEXT` (`submit` for writes and for reads of one's own lines, `report` for `reports/*`), `XA-USER`. `review`/`admin` are 401 |
| Probe helper | recreate in the scratchpad: one call per invocation, base URL pinned to the demo host (assert it), every call appended to a JSONL log with the key never written. Never `import` a script that performs writes at module level: on 2026-10-13 one import re-ran a whole ladder |
| Admin app | `https://xras-admin-demo.xsede.org`. Ben signs in once in the Playwright browser (NCAR RADIUS, 2FA); Claude then drives approve and post with `mcp__playwright__*`. "Repost to Accounting Service" re-sends the request's `New`, not a chosen action |
| samuel-dev data | `psql` on `sam_dev` at `csg-postgres.k8s.ucar.edu`, `SAM_DEV_PG_USER` / `SAM_DEV_PG_PASSWORD`, `sslmode=require`. Every post is a row in `xras_action_log` |
| samuel-dev logs | `kubectl -n sam-queries-dev logs <pod> \| grep webapp.run \| grep /api/xras/`: per-pod only, emptied by every roll. Task pods carry the `xras_sweep` detail (`pending_push_sample`, `account_requests`) |
| Health | `scripts/cirrus_watch.sh --context nwc1 --env dev` (the `watch-dev` skill) |

Ground rules, from what went right and wrong on 2026-10-13:

- One attempt per write, then re-read. A 200 proves nothing (`XRAS_WRITE_PROBES.md` § 4.4).
- Write only to our own requests: `benkirk` is PI, or the placeholder `samprobe-20261013`.
  Demo copies of real PIs' requests are post-only, never written through the API.
- **No notify on demo.** Where demo's mail goes is unknown. If a test cannot proceed without
  a notify, notify only a request whose whole roster is Ben.
- Request numbers past the 2026-03-24 fork are demo-native and name different requests in
  production. Never carry a demo number into a production read.
- Production database reads need Ben's say-so in the session (auto mode refused one).

State to start from (details in `XRAS_DEV_PROBES.md` § 8): **UHSS0002** = request 1439543,
renamed, Extension 372866 applied; NCAR4086 → UCSG0004 (1439308, kyledavis's, not renamed);
NCAR4088 (1439539, refused 422 for its roster); sam_dev `xras_action_log` #1–#12.

## 3. Track 1: SAMuel renames the request

### 3.1 The pieces

| Piece | Where | Notes |
|---|---|---|
| Client verb | `XrasAdminClient.rename_request(request_id, *, old_number, new_number, xa_user)` in `src/sam/integration/xras_api/admin_client.py` | beside `update_request_attributes`, whose verify re-reads by the old number and cannot be reused. Verify: `reports/request_numbers/<new>` contains `request_id`, `<old>` does not |
| Audited op | `rename_request(...)` in `src/sam/manage/xras_remediation.py` through `_editor_op` | the audit row is committed before the write. `_editor_op` patches the sweep's card entry keyed by `request_number`, so the patch moves the entry to the new key |
| Immediate trigger | `src/webapp/api/xras/actions.py`, the `processed` arm | only when `service == 'add'` and `request_number != projcode`; impersonate the payload's PI |
| Backstop task | a sibling of `xras_sweep` in `src/scheduling/tasks/`, or a step in it | every `processed` `New` whose `request_number` differs from `projcode_result` and whose XRAS line still answers the old number |
| Resolution | `select_service` in `src/sam/xras/dispatch.py`; `live_pending_numbers` in `src/sam/queries/xras_requests.py` | `XRAS_SUBMISSION.md` § 4.6 item 2 (three rules), the park-reason text, the sweep's pending set |

### 3.2 Decisions the build has to make

1. **Before or after the reply.** XRAS waits on our answer to the post, and Steve has
   reported connect timeouts. Decided: after the reply, from `response.call_on_close`
   (§ 3.5); the task covers a failure.
2. **Lifting the write lever into the CronJob.** `XRAS_WRITE_ENABLED` is webapp-only by
   design: `helm/values.yaml` (the lever block and the live-task list),
   `helm/templates/cronjob-tasks.yaml`, and the gate
   `tests/unit/gates/test_xras_admin_client.py` ("No scheduled task may write to XRAS") pin
   it. `XRAS_INGEST_IMPROVEMENTS.md` § 5 records the exception. Change the three together, and
   keep the task disabled on production until the backfill is approved.
3. **Edge cases.** A family with several lines under the `NCAR` number renames every line. A
   `New` routed to `update` already carries the projcode: skip. `failed` and `manual` rows have
   no projcode: skip. A PI missing from the line falls back to the Allocation Manager, else the
   task reports it.
4. **XRAS's other number-keyed calls.** `POST|DELETE /api/xras/v1/roles/<n>/…`,
   `dates/requests/<list>` and `requests/request/<n>` resolve `<n>` as a projcode
   (`src/webapp/api/xras/`), so before a rename they miss an unrenamed family. Traffic is near
   zero (legacy: `requests/request` once in 30 days, `POST /roles` never), so the rename covers
   it; decide whether those routes also get § 4.6 item 2's log lookup.

### 3.3 Production backfill

The `xras_rename` task with its window widened (Ben, 2026-10-13: no separate CLI). The runner
skips a task named in `SAM_TASKS_DISABLED` even under `--force`, so each run clears it:

```bash
# 1. dry run: reads only, needs no write lever; detail lists each line and its state
SAM_TASKS_XRAS_RENAME_SINCE=2026-08-24 SAM_TASKS_DISABLED= \
  sam-admin tasks --run xras_rename --force --dry-run --format json
# 2. Ben approves the list; Steve hears before the first production rename
# 3. apply: the same, without --dry-run and with XRAS_WRITE_ENABLED=1 for that run only
```

**Applied 2026-10-14 18:03Z** from a workstation: the dry run listed exactly the five
below, and the apply verified all five (`xras_remediation_event` 13-17, `created_by`
`task:xras_rename`, each read back from XRAS); the other 68 read `already_renamed`. Ben judged
five lines too small to need a heads-up to Steve.

Enabling the task on production is then a values change: drop `xras_rename` from
`SAM_TASKS_DISABLED`, add `XRAS_WRITE_ENABLED` to production's `tasks.env`, and flip
`XRAS_RENAME_ENABLED` to `"1"` for the route's after-reply rename. The gates that pin each
value (`test_xras_admin_client.py`, `test_task_xras_rename.py`, `test-dev-render.sh`) change
with it.

A dry-run report first: every production `processed` `New` since 2026-08-24 whose XRAS line
still answers its placeholder. Ben approves the list, then apply. Tell Steve before the first
production rename: PIs see the number in ARC, and the award letter for anything notified
later will carry the projcode.

**Census, 2026-10-14** (read-only: every `Approved` line in production XRAS against prod
`xras_action_log`). 4,170 approved lines; 23 live ones still numbered `NCAR*`:

| Group | Lines | Backfill |
|---|---|---|
| Posted to SAMuel, never renamed | NCAR4293 → NRAL0055, NCAR4212 → NRAL0056, NCAR4300 → NHAO0037 (posted 09-09), NCAR4354 → UHSS0001 (09-15), NCAR4432 → UWHO0029 (10-13) | **the five the dry run should list** |
| Posted to SAMuel, already renamed by hand | 68 of the 73 processed News since the repoint, including NCAR4326 → UUSL0051 and NCAR4401 → UCGD0010 | none: one read each, `already_renamed` |
| Approved, no projcode | NCAR4281, 4340, 4395 (refused, 422); NCAR4389, 4391, 4398 (never posted) | none: pending pushes, renamed after they land |
| Legacy era (2015-2022), no SAMuel log row | NCAR0099, 0116, 0868, 0910, 2169-2174, 2178, 2179, 2181 | none: every title matches only inactive SAM projects (most expired 2018-2024), and the matches are ambiguous (five requests and five projects share one title). Left alone |

NCAR4401 was posted on 10-06 and is renamed: NUSD still renames by hand in the admin app
(Summary → Request Number (Edit), `XRAS_DEV_PROBES.md` § 6), after activating the project in
SAM and copying its projcode (Travis Fair, 2026-10-14). He agreed to SAM taking the step over;
tell NUSD when `XRAS_RENAME_ENABLED` goes on, and that the reloaded request page and XRAS's
search show the projcode while the old number stops resolving. A window earlier than 2026-08-24 selects nothing more: the task reads
only SAMuel's log, whose processed rows start at the repoint.

### 3.4 Proving it on the loop

**Done 2026-10-14** (`XRAS_DEV_PROBES.md` § 6): steps 1-5 on NCAR4089 → UHSS0003, the route's
rename 1.77 s after the reply, the task's on NCAR4086 and on the renewal sibling.

1. Submit a benkirk-only `New` via the API; approve and post it through Playwright.
2. See the rename land before any notify, and the old number stop resolving.
3. File an Extension via the API, approve, post: `processed` under the projcode.
4. Disable the immediate path, repeat 1–3, and see the task do the rename.
5. Rename onto a live family: the second line of a family still under `NCAR####` (a
   Renewal filed before the rename). A `New` routed to `update` is not this case: it already
   carries the projcode, so nothing is renamed. Submit and approve a benkirk-only `New`
   (`NCARn`), spawn a renewal line on it (`POST …/requests/<rid>/renew`) **before** posting,
   then post the `New`: the route renames line 1 to `P`, and the `xras_rename` task's sibling
   pass renames the renewal line onto the now-live family `P`. Read
   `reports/request_numbers/P` and the card's primary line. Proven 2026-10-14: UHSS0003 holds
   both lines, `NCAR4089` answers nothing, and the primary line stays the New
   (`XRAS_DEV_PROBES.md` § 6).
6. Unit tests: the client verb against a fake transport; the trigger's conditions; the task's
   selection; the gate changes.

### 3.5 What landed (branch `xras-closed-loop`)

| Piece | Where |
|---|---|
| By-id read, the one key a rename does not move (probed: 1439543 answers `UHSS0002`) | `XrasApiClient.get_request_by_id` (`reports/requests/<rid>`) |
| Client verb, verified by id | `XrasAdminClient.rename_request` |
| Audited op: pre-read by id (already renamed = no write; a third number = left alone), PI else Allocation Manager, card row moved to the new number, overrides rekeyed | `sam.manage.xras_remediation.rename_request`, `rename_plan`; `XrasRequestOverride.rekey` |
| The one task-writable operation | `TASK_WRITABLE_OPERATIONS = ('rename_request',)` in `sam/integration/xras.py` |
| Immediate path, **after** the reply (decision 1) | `_rename_after_reply` in `webapp/api/xras/actions.py`, via `response.call_on_close`; logs status and ms. Behind `XRAS_RENAME_ENABLED` (review F1): `"1"` on dev, `"0"` in production, pinned by the gate |
| Backstop task and backfill | `src/scheduling/tasks/xras_rename.py`; `rename_candidates` in `sam/queries/xras_actions.py` |
| Log resolution, rules 2 and 3 (decision 4: the number-keyed routes `/roles`, `/dates/requests`, `/requests/request` do not get it; the rename covers their near-zero traffic) | `resolve_project` in `sam/xras/dispatch.py`; `logged_projcode` |
| Sweep pending set | `live_pending_numbers` subtracts `pushed_request_numbers` |
| Lever (decision 2) | the task: dev `tasks.env` only; production disables it by name and carries no lever. The route: `XRAS_RENAME_ENABLED` |

Every processed post whose number differs from its projcode is renamed, not only a `New`: an
action resolved by rules 2 and 3 names a line that still needs it, and the pre-read makes a
repeat free. The identity census behind this (nothing else needed re-keying first) is
`UNPLANNED_CITY_LEDGER.md` entry 26.

## 4. Track 2: stress-test the submission plan

Each scenario: what to file, what to click, what SAM should record. Findings go into
`XRAS_DEV_PROBES.md`; design changes into `XRAS_SUBMISSION.md`.

| # | Scenario | Tests |
|---|---|---|
| 1 | Supplement and Extension on a renamed family as a multi-step flow, with a failure mid-flow (abort = soft-delete the action) and a resume | § 4.3, phase 1 |
| 2 | `/renew` on UHSS0002 → approve → post → the `update` handler; what `/renew` copies; the Progress Report rule | phase 1b |
| 3 | Operator outcomes in the admin app: Hold Off, Return for Corrections, Reject | the state mapping, § 4.2 |
| 4 | Withdraw after submit; resubmit (lands `Submitted` on XRAS-dev, § 6.1) | § 3.1 |
| 5 | The SAM lead absent from the XRAS line: refused with a reason, never worked around | § 4.4 |
| 6 | A request authored by `samprobe-20261013` → post → expected 422 → merge on arrival (demo only; a merge deletes the placeholder) → repost | phase 3 |
| 7 | A `New` posted for an existing projcode (`update`, the NRAL0051 double-apply); two posts of one action in quick succession (the in-flight guard) | § 4.6 item 1 |
| 8 | **Does a request leave the admin queue without a notify?** Look at UHSS0002 and UCSG0004 (posted, never notified) first. Notify only if the answer needs it, on a Ben-only request, and record where the mail goes | Ben's question, 2026-10-13 |

Out of scope on the loop: the form (§ 4.1, unprototyped) and limits (§ 4.5, SAM-side only).

**Run 2026-10-14** (`XRAS_DEV_PROBES.md` § 6.1): 1-5, 7 and 8 on UHSS0003 and NCAR4090; 6 on
NCAR4091 → UHSS0004 (post 422, merge in SAMuel, repost 200, renamed 0.7 s after the reply).
Nothing found needs a change to #819. For the design:
a resubmit lands `Submitted`, not `Under Review`; Hold Off is a `states[]` entry, not a status,
and survives a return and a withdraw; a request leaves the admin queue at the post, without a
notify; a Supplement filed after a Renewal posts lands on the renewal's future window (legacy's
`getLatestAllocation`, `XRAS_INGEST_IMPROVEMENTS.md` candidate).

**Resume here (fresh session).** Nothing open: each phase of `XRAS_POST_AUTOMATION.md` gets its
scenarios on this bed as it lands. The placeholder `samprobe-20261013` is gone (scenario 6); a
placeholder scenario needs a fresh person, named `<x>-user-<y>` so SAM classes it as one.

- **Bed:** samuel-dev on the branch image (check `kubectl -n sam-queries-dev get pods` shows the
  expected `sha-`; a staging merge re-pins `cirrus-dev` to staging, and
  `gh workflow run "Publish Images and CIRRUS Deploy" --ref <branch>` restores it). State left
  on XRAS-dev: `XRAS_DEV_PROBES.md` § 8.
- **Writes:** a scratchpad helper as in § 2 (one call per invocation, demo host asserted, JSONL
  log, no key written). The New recipe that validates on Exploratory: create, `roles/PI`,
  attributes (title, abstract, keywords, `isSupportedByGrants=false`), Derecho resource, a
  Requested date range, fos 500003, the EUA bulk PUT (attribute 539638, `attributeValue: null`),
  an Advisor Letter (`cupsfilter` PDF, JSON base64), validate, submit. An Extension also needs
  `userComments`.
- **Clicks:** Ben signs in to `xras-admin-demo` (2FA); approve on `/actions/<aid>/show_process`
  ("Approve the requested dates", "Fill Award With Requested", Save & Approve), then Post.
- **Rules:** benkirk-only requests; Notify only when Ben is the sole recipient; never carry a demo
  number into a production read; production reads need the session's say-so.

## 5. Questions for Ben

- Decided 2026-10-13: the rename runs after the reply (§ 3.5).
- The backfill: one approval of the census list (§ 3.3).
- Scenario 6's merge on demo: approved and run 2026-10-14, into benkirk.

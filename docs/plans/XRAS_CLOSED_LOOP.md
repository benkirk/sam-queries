# XRAS closed loop: SAMuel renames requests, then the submission plan under load

**Status: handoff, not started.** Written 2026-10-13 at the end of the session recorded in
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

Read first: `XRAS_DEV_PROBES.md` (§ 3 write ladder, § 4 timeline, § 5 rewrite, § 7 state left
on demo); `XRAS_SUBMISSION.md` § 4.2 (the three identifiers), § 4.6 items 1–4, § 5;
[`XRAS_REQUEST_TICKETS.md`](XRAS_REQUEST_TICKETS.md) § 6; `.claude/rules/notify-and-tasks.md`.
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

State to start from (details in `XRAS_DEV_PROBES.md` § 7): **UHSS0002** = request 1439543,
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
   reported connect timeouts. The webapp has no after-response hook today. Measure on demo: a
   synchronous rename before replying (adds one PUT plus two reads), against a post-response
   hook. Whichever is chosen, the task covers a failure.
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

### 3.3 Production backfill

A dry-run report first: every production `processed` `New` since 2026-08-24 whose XRAS line
still answers its placeholder. Candidates from award letters and the sweep, to confirm in the
report: NCAR4212, NCAR4293, NCAR4300, NCAR4326, NCAR4354 (UHSS0001), NCAR4401. Ben approves the list, then apply. Tell Steve before the first
production rename: PIs see the number in ARC, and the award letter for anything notified
later will carry the projcode.

### 3.4 Proving it on the loop

1. Submit a benkirk-only `New` via the API; approve and post it through Playwright.
2. See the rename land before any notify, and the old number stop resolving.
3. File an Extension via the API, approve, post: `processed` under the projcode.
4. Disable the immediate path, repeat 1–3, and see the task do the rename.
5. Unit tests: the client verb against a fake transport; the trigger's conditions; the task's
   selection; the gate changes.

## 4. Track 2: stress-test the submission plan

Each scenario: what to file, what to click, what SAM should record. Findings go into
`XRAS_DEV_PROBES.md`; design changes into `XRAS_SUBMISSION.md`.

| # | Scenario | Tests |
|---|---|---|
| 1 | Supplement and Extension on a renamed family as a multi-step flow, with a failure mid-flow (abort = soft-delete the action) and a resume | § 4.3, phase 1 |
| 2 | `/renew` on UHSS0002 → approve → post → the `update` handler; what `/renew` copies; the Progress Report rule | phase 1b |
| 3 | Operator outcomes in the admin app: Hold Off, Return for Corrections, Reject | the state mapping, § 4.2 |
| 4 | Withdraw after submit; resubmit lands `Under Review` | § 3.1 |
| 5 | The SAM lead absent from the XRAS line: refused with a reason, never worked around | § 4.4 |
| 6 | A request authored by `samprobe-20261013` → post → expected 422 → merge on arrival (demo only; a merge deletes the placeholder) → repost | phase 3 |
| 7 | A `New` posted for an existing projcode (`update`, the NRAL0051 double-apply); two posts of one action in quick succession (the in-flight guard) | § 4.6 item 1 |
| 8 | **Does a request leave the admin queue without a notify?** Look at UHSS0002 and UCSG0004 (posted, never notified) first. Notify only if the answer needs it, on a Ben-only request, and record where the mail goes | Ben's question, 2026-10-13 |

Out of scope on the loop: the form (§ 4.1, unprototyped) and limits (§ 4.5, SAM-side only).

## 5. Questions for Ben

- After measuring on demo: the rename before or after the reply.
- Whether the backfill needs per-request sign-off or one approval of the list.
- Whether Steve hears about the rename before or after the demo proof.
- Whether scenario 6's merge on demo is wanted.

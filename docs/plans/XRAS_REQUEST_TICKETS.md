# One NUSD ticket per approved XRAS request

**Status: design, not built.** Input to a later PR. The detection it rests on shipped
with the sweep changes of 2026-10-13 (branch `xras-sweep-open-ended-window`). The table
it needs is designed in `XRAS_SUBMISSION.md` § 4.7, not here.

## 1. Goal

When XRAS approves a request, SAM opens one ticket with NUSD (Jira Service Management
at `ithelp.ucar.edu`, the RC project) before XRAS pushes the request to SAM. The ticket
says what completing the request needs. When that changes, SAM adds an internal comment.
When the push lands, SAM resolves the ticket. A request needing nothing reads "ready to
post". Today such a request is posted by an operator; later SAM could post it itself, and
such a ticket would then never be filed.

Today the sweep files one `account_request` row per person and **no ticket** for those
rows (`sam/notify/kinds.py`, "XRAS-sweep rows never ticket"). NUSD works from the
Accounts queue and the weekly digest. A ticket per request replaces that for XRAS-origin
rows: one conversation per award instead of one row per person.

## 2. Trigger

A request is **live-pending** when all of these hold:

- it is Approved and not deleted;
- it passes `overlaps_window` (an open-ended request is aged by
  `OPEN_ENDED_MAX_AGE_DAYS`);
- it has no SAM project, **and** no push has landed for it (§ 6).

The sweep sees one within 15 minutes in business hours and within the hour otherwise.
The light pass reads the newest `status=Approved` pages, because `active=true` omits
every request with no `endDate`.

**First seen approved** is the trigger instant. XRAS records no approval time
(`reference_xras_no_approval_timestamp`), so SAM must persist the first time it saw the
request approved. That is the one fact this feature needs that no table holds today.
`account_request.creation_time` stands in for it per person, not per request.

## 3. The checklist

These are built per request from what the sweep already computes:

| Item | Source | Notes |
|---|---|---|
| People to create | `classify_accounts` rows with remedy `create` (absent, incl. ARC placeholders) | per roster person, with role |
| People to reactivate | remedy `reactivate` (inactive SAM user) | |
| Placeholders to merge | remedy `merge` (`stamp_merge_targets`) | an operator step in XRAS, not NUSD's |
| Push blockers | preflight verdicts per action: `status`, `gaps`, `messages` (`_run_preflights` `by_number`) | built on the **daily** pass only |
| Unmapped opportunity | `audit_opportunity_mapping` against the request's `opportunity_id` | |

"Ready to post" means the list is empty.

**The digest must not flap.** Preflight verdicts are built on the 04:00 full pass and are
absent on light runs (`index_entries=None`). A digest that includes blockers must read
them from the published index (`load_requests_index()`) on a light run. Otherwise every
light run posts "blockers cleared" and every full run posts them back.

## 4. Comments

Post an internal comment (`comment(key, text, internal=True)`) only when the checklist
digest changes. Ledger it in `notification_log` as `xras_ticket:<request_id>:<digest>`
(`dedup_key` is String(128)), so a repeat is suppressed by the machinery that already
suppresses mail. **Caveat:** A -> B -> A does not post the second A. Accept that, or fold
a sequence number into the key. A public comment would move the request to "Customer
wait" (see `reference_ithelp_jira_api`), so every comment stays internal.

## 5. Filing from a task

Keeping the Jira levers out of the task CronJob was a deployment simplification, not a
constraint. Lifting it is deliberate, in one change:

- **Lift the filer, do not copy it.** `webapp/register/handoff_mail.py` holds a complete
  find-before-create routine:
  - the ledger check (`account_ticket:<id>`);
  - `provider.find(handle)`;
  - a `queued` row, then `create`;
  - `_filed_anyway` for a create that timed out;
  - `_link_ticket` on its own session.

  Move it into a Flask-free `sam/integration/tickets/filing.py` taking the provider, the
  ledger, a session factory and `(entity_type, entity_id, handle, kind)`.
- **Rename what stays.** The web wrapper keeps the `flask.g` provider cache and outage
  switch, the receipt mail and the after-commit call sites. Rename it
  `webapp/register/handoff.py`: the name `handoff_mail` predates the API filing and now
  misleads. Its importers are `register/blueprint.py`, `register/invite.py`,
  `dashboards/project_invites.py`, `dashboards/event_lifecycle.py` and
  `dashboards/admin/account_requests_routes.py`, plus four test files, two rule files and
  three docs.
- **A new kind.** Add `KINDS` entry `xras_request` (`tickets/base.py`), with its own
  `JIRA_XRAS_REQUEST_*` settings. The CronJob lists `JIRA_*` keys by hand
  (`helm/templates/cronjob-tasks.yaml`), so each needs explicit wiring. The handle is
  something like `SAM-XR-<request_id>`; `jira.py`'s exact-match filter needs its prefix.
- **Provider gaps.**
  - Add `transition(key, ...)` (resolve; Jira 761 needs a `resolution`) as an optional
    method with a safe default, like `comment`.
  - `learn` covers only `account_request` / `account_ticket`
    (`tickets/queries.py`). A mail-fallback ticket of the new kind is never learned, so
    either extend `learn` or give the task filer no mail fallback.
- **The gates and prose that say a task cannot file.** Update them together:
  - `helm/templates/cronjob-tasks.yaml` (the comment above the hand-listed `JIRA_*`
    keys) and `helm/tests/test-cronjob-render.sh`;
  - `tests/unit/gates/test_helm_jira_levers.py` (`TestTheCronJobReadsOnly`) and
    `helm/values.yaml` (the Jira block);
  - `scheduling/tasks/account_requests_reconcile.py` ("never a tracker write") and
    `tickets/registry.py` / `learn.py` ("reads only");
  - `docs/plans/implemented/TICKET_PROVIDER.md` (§§ 5–9) and
    `.claude/rules/notify-and-tasks.md`;
  - `sam/notify/kinds.py` and `implemented/ACCOUNT_REGISTRATION.md` ("sweep rows never
    ticket").

  Leave the outbound safety pins alone: `test_outbound_guards.py`, `tests/conftest.py`
  and `helm/tests/test-dev-render.sh`.
- **Where it runs.** A step in `xras_sweep` after the queue upsert, or a sibling task
  reading the sweep's snapshot. A sibling keeps the sweep's failure modes separate and can
  ship in `SAM_TASKS_DISABLED`; that is the recommendation.

## 6. Resolving: "the push landed" is not `requestNumber == projcode`

XRAS rewrites a pushed New's `requestNumber` to the projcode **on its own clock**: hours
for `UPUR0036`, a day and counting for `NCAR4212` -> `NRAL0056`, and still `NCAR4354`
weeks after it was pushed as `UHSS0001` (`XRAS_SUBMISSION.md` § 4.2).

Until the rewrite, the sweep's set difference reads a landed push as still pending. The
four requests the light pass called pending on 2026-10-13 (NCAR4212/4293/4300/4354) fit
that shape. So:

- **Key the ticket on the `request_id` of the line whose approved New or Renewal
  triggered it, and store that id.** A family holds several lines, each with its own id.
  At the trigger this is `primary_line`, the line the Remediations card shows and every
  write targets. Later, a newer action on another line moves `primary_line`, so a key
  re-read from it would file a second ticket. The stored id is stable across the rewrite
  (§ 4.2's table, which keys its record on the created line the same way); the
  `request_number` is carried and updated when observed.
- **Resolve from `xras_action_log`.** A processed row for the request (its
  `request_number` as pushed, `projcode_result`) closes the checklist. The same test
  belongs in `live_pending_numbers` (`sam/queries/xras_requests.py`). Today that is a
  set difference against `project.projcode` only, so a pushed-but-not-rewritten request
  stays pending on the Remediations card too.

## 7. Rollout

1. **Shadow.** The task computes and persists checklists and records "would file" in the
   ledger detail, shipped in `SAM_TASKS_DISABLED` on prod and enabled on samuel-dev.
2. **File on dev** against the RC project with a label and immediate cancel, the way
   RC-40275/40276 were smoked.
3. **Prod** with the lever.

**Experiments on samuel-dev against XRAS dev** (Ben drives approve and push):

- **Before the push.** The request's `endDate` and its `active=true` visibility after
  approval.
- **After the push.** When the `requestNumber` rewrite happens, and whether `requestId`
  is unchanged.
- **The action log.** What `xras_action_log` holds for the push (`request_number`,
  `projcode_result`).

## 8. Open questions for Ben

- **Ticket type and audience.** Request type 20 "Add a user", or a new one? Reporter
  `rc-sam-bot`; on behalf of the PI?
- **Account rows.** Do `account_request` rows link to the request record, by a column or
  derived from the roster? A person on two requests is one row today.
- **Comment cadence.** Is there a floor on comments, for example at most one per day per
  ticket?
- **Drop-out.** When a request drops out (deleted, aged out), does the request lifecycle
  dismiss its open sweep rows and cancel its ticket? Neither happens today: the rows stay
  open until an operator acts, and a ticket filed for a request XRAS then deleted would
  stay open at NUSD with nothing left to do.

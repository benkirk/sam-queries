# XRAS submission sprint 1: the build plan

**Status: starts 2026-10-29. Issue #871; one PR to `staging`, commits grouped per phase.** Lands
phases 0, 1, 1b and 2 of [`XRAS_SUBMISSION.md`](XRAS_SUBMISSION.md): a lead files an Extension,
Supplement, Renewal or New request from a My Requests tab on SAMuel's user dashboard. Sprint 2
(#872) adds the registration-linked New. The decisions are `XRAS_SUBMISSION.md` § 3.5; the probe
record behind them is § 9. Every UI step loads the
`wire-dashboard-feature` skill first.

## 1. Commit map

| # | Commit | Phase |
|---|---|---|
| 0a | write client: `_write(json=)`; verbs `create_request`, `delete_action`, `upload_document`, `set_opportunity_attributes`, `add_grant`, `update_grant`, `set_fos`; reads `required_fields`, `required_documents_status`, `get_draft`; `renew_request(opportunity_id=)`, `add_action(user_comments=)`. `vocabulary.py`: `ACTION_TYPES`, `REQUEST_TYPES` ids. Levers `XRAS_SUBMIT_ENABLED` (dev "1", prod "0") and `XRAS_SUBMIT_ACTION_TYPES`, with their manifest pins | 0 |
| 0b | `XrasSubmission` and `AllocationTypeResourceLimit` models, DDL in two dialects, seed, `_BOOTSTRAP_TABLES`, LFS blob (once, last); flow layer `sam/manage/xras_submission.py`; routing `sam/xras/routing.py`; limits CrudSpec card | 0 |
| 1a | My Requests tab: page, status readout, per-project action picker, own modal shell, project-card link | 1 |
| 1b | Extension and Supplement forms through the flow; EULA on every action; abort and resume; admin Add-action modal re-pointed | 1 |
| 1c | Renewal: `/renew`, abstract, dates, amounts, grants, document; document helper; rule-book reads | 1b |
| 2a | New for a logged-in user: describe → request → review → submit; pickers; cancel = `delete_request`; New dark on prod through `XRAS_SUBMIT_ACTION_TYPES` | 2 |
| 2b | the New handler's consult point and the `applied` stamp | 2 |

Each commit regenerates the route-map snapshot it changes and lands its own tests; doc edits
ride the commit that makes them true.

## 2. The pieces

### Flow layer (`sam/manage/xras_submission.py`)

```python
ActionRequest(action_type, request_number, request_id, opportunity_id, title, abstract,
              keywords, user_comments, resources, dates, grants, fos_type_id, documents,
              eua_attribute_id, admin_username, sam=SamRefs(project_id, facility_id,
              mnemonic_code_id, allocation_type_id, contract_id))   # frozen; the row's payload
request_action(session_factory, draft, *, actor, xa_user, eula, submit=True, resume_id=None,
               door='pi', client=None) -> SubmissionOutcome
resume_submission(session_factory, submission_id, *, actor, submit=True, documents=(),
                  door='pi', client=None) -> SubmissionOutcome
abort_submission(session_factory, submission_id, *, actor, client=None)
submit_armed(action_type=None)
```

| Step | Ops through `_editor_op` (`xras_remediation.py`) | Row state after |
|---|---|---|
| open | `XrasSubmission.create`, committed on its own session **before any write** | `draft` |
| mint | Extension and Supplement: `add_action(user_comments=)`. Renewal: `renew_request(opportunity_id=)`, then the new line's first action from the family read. New: `create_request`, then `add_role(PI, xa_user)` (the creator is only the Allocation Manager) and the optional admin role | `created` |
| populate | each only when the draft carries it: `update_attributes`, `set_fos`, `update_resource_amount` per resource, `set_action_dates` (Extension: end date alone), `add_grant` per grant, `set_opportunity_attributes` (the EUA echo, while an opportunity carries the set), `upload_document` per document | `populated` |
| submit | `submit_action(preflight=True)`, which validates as the PI first | `submitted` |
| abort | `delete_action` on an existing line; `delete_request` for a New draft | `aborted` |

- New op names, all within `operation`'s 24 characters: `create_request delete_action
  upload_document set_opp_attributes add_grant update_grant set_fos`.
- A rejected, errored or unverified step stops the flow, stamps `last_error` with XRAS's
  `errors[]` whole, and leaves the state where it was; **no automatic abort**. Resume reloads the
  payload and skips every step whose event is `verified`. A second open row on the same
  `(request_id, action_type)` for the same actor is refused.
- A resume never re-sends a write that adds a row in XRAS (`create_request renew_request
  add_action add_role set_action_dates add_grant upload_document`) once its event is anything
  but `verified` or `rejected`: it may have landed, and a second Extension action is the
  UNVR0027 duplicate. The row stops with that reason; the remedy is abort. A document's bytes
  never reach the payload, so a resume asks for the file again.
- One `logger.info` per step: submission id, step, op, request and action ids, status.
- PI door (`door='pi'`): `xa_user = actor`, and the lever with the type armed. Admin door
  (`door='admin'`): `xa_user = resolve_pi(roster)`, `created_by = operator`, the write lever
  alone. Abort needs only the write lever.
- `_editor_op` on an unnumbered draft passes `refresh=lambda: True`: nothing is in the index to
  patch. The submission verbs verify through `get_draft` (`GET /v1/requests/<rid>` as the
  role-holder, 200 on XRAS-dev and production), which needs no number: a create by its new id
  reading back with its action, a deleted draft by that read answering 401, a deleted action by
  `isDeleted: true`.
- The EULA is required on every action: `request_action(eula=(sha, accepted_at))` stamps the row.

### `XrasSubmission` (`sam/integration/xras_submission.py`)

| Group | Columns |
|---|---|
| Keys | `xras_submission_id`; `action_id` unique, nullable; `request_id` indexed; `request_number`; `project_id` |
| What | `action_type`, `request_type`, `opportunity_id` |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id`, `registration_id` |
| Agreement | `eula_sha`, `eula_accepted_at` |
| Lifecycle | `state` (bare `String(16)`, validated in code against `draft created populated submitted aborted applied`, no Enum: two dialects), `created_by`, `xa_user`, `creation_time`, `modified_time`, `submitted_time`, `applied_time`, `aborted_time`, `last_error` (Text) |
| Evidence | `payload` (the draft), `xras_snapshot` (the create or renew response), `step_events` (step → `xras_remediation_event` id) |

`create()`, `advance(state)`, `record_step()`, `set_keys()`, `mark_applied(project_id)`;
module-level `submission_by_action_id`, `new_submissions_by_request_id`, `open_submission_for`,
`step_statuses`, `documents_to_reattach`. Index names never match a table name (Postgres shares the
namespace). `anonymize_sam_db.py` purges the table.

### `AllocationTypeResourceLimit` (`sam/accounting/allocation_type_limits.py`)

`allocation_type_id`, `resource_id`, `min_amount`, `default_amount`, `max_amount`, `active`,
`created_by`, timestamps; unique on the pair. `create()`/`update()` validate
`0 ≤ min ≤ default ≤ max`; `lookup_resource_limit(session, *, allocation_type_name,
resource_name)` and `limits_for_allocation_type(session, name)` take names and resolve ids at
runtime, mapping every row of a duplicated name (the local table has two "Small" rows, 3 and 8,
and two "Education"). The card is a `CrudSpec` under Admin → Configuration (`SYSTEM_ADMIN` to
edit, `VIEW_SYSTEM_CONFIG` to read).

### Routing (`sam/xras/routing.py`, Flask-free)

`XRAS_REQUEST_ROUTES` keyed by allocation-type **name** (`xras` for CHAP, Small, Small (No NSF
award), Data, Classroom, NSC, External Project; `email:wrap@uwyo.edu` for WRAP; `staff` for
Divisional, Staff, the reserves); an unlisted name routes `staff` with its reason.
`request_routing(session, project, family, username, *, armed) → RoutingDecision` carries one
reason vocabulary: lever off, policy, no live family, not on the roster, action already pending.
The gate stays the family read plus the roster (§ 4.4 of the design); the opportunity read's
per-user `availableActionRequests` and the line's `allowedActions` (both 200 as the PI on
production, 2026-10-28) feed the picker and the resource lists.

### The tab and the forms (`webapp/dashboards/user/requests_routes.py`)

| Route | Decorator | Does |
|---|---|---|
| `GET /user/requests` | `login_required` | the page: the user's requests with status, then the per-project pickers |
| `GET /user/requests/<projcode>/actions` | + `require_project_permission(EDIT_PROJECTS)` | the lazy picker for one project |
| `GET/POST …/<projcode>/extension`, `…/supplement`, `…/renewal` | the same | one `HtmxFormHandler` subclass per type, `stage=review` then `stage=submit` |
| `GET/POST /user/requests/new` | `login_required` | the New form, stages describe → request → review → submit |
| `GET /user/requests/new/opportunity` | `login_required` | page one's reveal for the chosen opportunity |
| `POST /user/requests/<id>/abort`, `…/resume` | owner or `MANAGE_XRAS` | the stopped-row controls |
| `GET /user/requests/htmx/mnemonic-suggest`, `…/award-lookup` | `login_required` | the New form's pickers |

- The page reads `sam/queries/xras_my_requests.py`: `my_requests` (SAM's rows, then the index
  lines the user is on, each through one `Readout`: the `xras_action_log` row once posted, else
  XRAS's action status) and `led_projects`. The page makes no XRAS call; a project's picker reads
  the family, then `XrasAdminClient.available_action_requests(xa_user)` (the opportunity rules as
  the user), and `action_offers` in the routing module turns the decision into one offer per
  action. Renewal is offered only where that read names the line, with its opportunity;
  Extension and Supplement follow the gate alone, since the line's `allowedActions` is advisory.
  `?projcode=` (the card's "Request time" link) opens that project's picker on load.
- The Extension and Supplement forms are one `HtmxFormHandler` (`_RequestForm`): each stage
  re-reads the family and re-runs the routing, so nothing about the gate rides in the browser.
  Review runs `preflight_draft` (`sam/xras/submission_preflight.py`) and disables Submit on a
  `failed` verdict; a submit without the tick, with a stale `eula_sha` or with a failed verdict
  returns to the review. Comments are required on both, a SAM choice (XRAS requires them on an
  Extension only). The Supplement's resources are the line's own; its limits map through the
  catalog key to SAM's resource. An impersonating session cannot file. The terms open at
  `/user/requests/agreement` (the `/register` blueprint is unmounted on production). Resume and
  Discard answer `HX-Refresh`.
- The Renewal (1c) opens from XRAS's offer for the line (`available_action_requests` carries
  its opportunity, allocation type, award period, named resources and the required End User
  Agreement attribute, which the draft echoes with a null value). The rule book
  (`sam/integration/xras_api/rule_book.py`: `required_fields` plus `/types/documents`, memoised
  a day, `RULE_BOOK_FALLBACK` by type name) names the documents; each required one is a PDF
  upload on the review, checked by `document_refusal`. The form pre-fills the abstract from the
  line (`/renew` does not copy it) and the window from the day after the current end for the
  award period. A rule book that wants grants asks for one on the form (the New's grant step).
- Lines SAM mints (New, Renewal) are drafts: their populate and submit steps verify by id
  (`XrasAdminClient.reading_draft`, a `get_draft` re-read as the PI) instead of the reports
  family by number.
- The admin Add-action files Extension and Supplement through `request_action(door='admin',
  submit=False)` and asks the same agreement tick; Transfer, Adjustment, New and Renewal keep
  the editor's direct `add_action`.
- Schemas in `sam/schemas/forms/xras_submission.py`, composed from the caps and validators of
  `forms/xras_remediation.py`; `XrasAgreementMixin` on all four.
- The review step runs `preflight_draft` (`sam/xras/submission_preflight.py`), which builds the
  reports-shaped action with null ids and calls `preflight_action`; for a New that is the real
  resolution ladder (roster, mnemonic, allocation type, contracts) run while the submitter is
  present. The Extension review names the window it reaches and floors the date on
  `latest_allocation`; Supplement and New amounts are re-checked against the limits table.
- Grants are lookup-then-confirm: the award number goes to `resolve_award('NSF', …)`;
  `AwardRecord` fills title, begin and end date, PI name and the program officer (`monitor`),
  and the PI types what NSF lacks. NSF only (`fundingAgencyId` 1): USAspending carries no program
  officer, so any other award is typed. The rule book's grant fields (`GRANT_FIELDS`, External
  without officer or title) are required by the handler; the draft sets `isSupportedByGrants`.
- The New (2a) re-reads the user's opportunities (`new_request_opportunities`: `canSubmitNewRequest`,
  `resourceIdsAvailableForNewRequest`) at every stage and writes nothing before submit, so its
  Cancel is the modal's. Page one asks the opportunity, then title, abstract, keywords, field of
  science (`/types/fos`, selectable rows) and an optional project admin (a live SAM user with an
  XRAS person); the opportunity reveals its facts, the code letters and the grant. The letters
  come from the New handler's own ladder (`new_request_sam_state`); where it misses, the PI picks
  a code, which the handler takes from the row (2b) and the review's verdict takes in place of
  the ladder's miss. Page two is the window (the first of next month for the award period) and the
  amounts; the review adds the document and the agreement. A stopped New is discarded with
  `delete_request`.
- Documents: PDF magic always; `SUBMISSION_DOCUMENT_MAX_BYTES` and `SUBMISSION_DOCUMENT_MAX_PAGES`
  are dark unless set; the rule book's `hardPageLimit` and document type are shown beside the
  upload; XRAS's own 400 is the backstop.
- The EULA: `eula_html()` one click away, a checkbox beside the submit, `eula_sha()` hidden in
  the form and checked in `clean()`.
- Own modal shell `xrasRequestModal` on the page; every fragment that targets its body is pinned
  in `HTMX_FRAGMENT_SHELL_DEPS`. The admin page's `xrasModal` keeps its operator copy.
- The admin Add-action modal (`_XrasAddActionHandler`) calls `request_action(…, submit=False)`
  with the operator door, so the editor's bare action gains a row and the same audit trail.

### The incoming consult point (commit 2b)

`NewHandler.assemble` starts by looking up the New row SAM filed on the action's `requestId`
(`new_submissions_by_request_id`, one read memoized for a whole validate-only worklist). `resolve_mnemonic_code(preset=)` takes the row's `mnemonic_code_id` when it
is still active, after an operator override and before the ladder; the pinned error order is
untouched. Only the code is preset: the allocation type, contracts and field of science come
from the push, which carries the opportunity and grants the form sent, and an allocation-type
preset would split from `auth_at_panel_meeting`, which re-derives the pair. `ActionHandler.run`
calls `stamp_applied()` after `execute()`, inside the one transaction, for every handler: the
row with this `actionId` (from `created`, `populated` or `submitted`) reads `applied`, with
the project a New created. The review runs before the row exists, so a code the PI chose
stands in for the ladder's miss in its verdict.

## 3. Levers

| Lever | Read by | dev | prod |
|---|---|---|---|
| `XRAS_SUBMIT_ENABLED` | `XrasApiConfig.submit_enabled`, `xras_submit_configured()`; webapp only, never `tasks.env` | "1" | "0" |
| `XRAS_SUBMIT_ACTION_TYPES` | `XrasApiConfig.submit_action_types` (`parse_submit_action_types`), then the routing | `all` | `Extension,Supplement`, shipped with the lever off so the first flip cannot expose New or Renewal |
| `SUBMISSION_DOCUMENT_MAX_BYTES`, `SUBMISSION_DOCUMENT_MAX_PAGES` | the document helper | unset | unset |

The admin door runs under `XRAS_WRITE_ENABLED` alone: the Remediations card's Add-action opens
`xras_submission` rows on production with the submit lever off. On such a row the PI's Resume
and Discard need `XRAS_SUBMIT_ENABLED` with the type armed (the page hides them otherwise), and a
resume honors the `submit` the row was opened with, so it never submits what the operator
left unsubmitted.

Pins: `tests/unit/gates/test_xras_admin_client.py` (prod off and narrowed, no `tasks.env` and no CronJob names it, dev on),
`helm/tests/test-dev-render.sh`, `tests/conftest.py`.

## 4. DDL, spoon-fed

samuel-dev runs on the CNPG Postgres `sam_dev`; production, the local clone and the test
container are MySQL. `load_postgres.py` streams every ORM table from the local MySQL clone, so
the MySQL script must land there before any `make refresh-dev`, and `sam_dev` takes the
Postgres script between refreshes.

| Table | MySQL (local 3306, mysql-test 3307, production) | Postgres (`sam_dev`) |
|---|---|---|
| `xras_submission` | `scripts/sql/create_xras_submission.sql` | `scripts/sql/create_xras_submission_pg.sql` |
| `allocation_type_resource_limit` | `scripts/sql/create_allocation_type_resource_limit.sql`, then `scripts/sql/seed_allocation_type_resource_limit.sql` | the same two with `_pg` |

Per roll:
1. `mysql -u root -h 127.0.0.1 -proot sam < scripts/sql/create_….sql` on the local clone, the
   same on `-P 3307`; `make -C containers/sam-sql-dev regen-lfs-blob` once, last before the PR;
   `pytest tests/integration/test_schema_validation.py` and `make pytest-pg`.
2. `psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/create_…_pg.sql` (Ben), or a
   `make refresh-dev`, which rebuilds the schema from the ORM.
3. Production MySQL at convergence (Ben): `mysql -u <hpc-writer> -h <host> -p sam < …`.

House shape (`create_external_ticket.sql`, `create_account_request.sql`): no foreign keys,
utf8mb4 on prose and echoed columns, app-clock timestamps, NO DROP, verification SELECTs at the
end. The seed is an `INSERT … SELECT` joining `allocation_type` (active rows, both "Small" rows
alike) and `resources` by name.

**The seed values** (Ben, 2026-10-28: defaults are a nice median of what active projects of
that type hold, limits a multiple above, the peer-reviewed panels get a high ceiling and no
default). Read from the 2026-10 snapshot: every non-deleted top-level allocation live today on
an active project, grouped by the project's allocation type and the account's resource, with
the first transaction as the initial award:

| SAM type (opportunity) | Resource | n | median | p90 | first award | **default / max** |
|---|---|---|---|---|---|---|
| Small (Small) | Derecho | 274 | 707,777 | 2,000,000 | 500,000 | **500,000 / 1,000,000** (published cap) |
| | Derecho GPU | 108 | 2,500 | 5,000 | 2,500 | **1,500 / 2,500** (published cap) |
| | Casper | 259 | 5,000 | 20,000 | 5,000 | **5,000 / 20,000** |
| | Casper GPU | 77 | 1,500 | 6,000 | 1,500 | **1,500 / 6,000** |
| | Campaign_Store (TB) | 142 | 5 | 15 | 5 | **5 / 20** |
| Small (No NSF award) (Exploratory) | Derecho | 431 | 500,000 | 1,000,000 | 500,000 | **500,000 / 500,000** (published cap) |
| | Derecho GPU | 144 | 1,500 | 1,500 | 1,500 | **1,500 / 1,500** (published cap) |
| | Casper | 423 | 5,000 | 10,000 | 5,000 | **5,000 / 10,000** |
| | Casper GPU | 89 | 1,000 | 5,000 | 1,000 | **1,000 / 5,000** |
| | Campaign_Store | 116 | 5 | 10 | 5 | **5 / 10** |
| Classroom (Classroom) | Derecho | 53 | 400,000 | 500,000 | 400,000 | **400,000 / 500,000** (published cap) |
| | Derecho GPU | 15 | 1,500 | 5,000 | 1,500 | **1,500 / 1,500** (published cap) |
| | Casper | 56 | 5,000 | 10,000 | 5,000 | **5,000 / 10,000** |
| | Casper GPU | 13 | 1,500 | 4,500 | 1,500 | **1,500 / 4,500** |
| | Campaign_Store | 3 | 2 | 4 | 2 | **2 / 5** |
| Data (Data Analysis) | Casper | 145 | 10,000 | 25,000 | 5,000 | **1 / 1** (a yes, not a quota; the operator sets the SAM amount) |
| | Casper GPU | 27 | 1,500 | 3,000 | 1,500 | **1 / 1** |
| | Campaign_Store | 39 | 5 | 10 | 5 | **5 / 20** |
| External Project (External) | Derecho | 15 | 2,000,000 | 3,900,000 | 2,000,000 | **2,000,000 / 4,000,000** |
| | Derecho GPU | 3 | 5,000 | 37,000 | 5,000 | **5,000 / 50,000** |
| | Casper | 13 | 10,000 | 90,000 | 10,000 | **10,000 / 100,000** |
| | Casper GPU | 3 | 500 | 16,100 | 500 | **500 / 20,000** |
| | Campaign_Store | 4 | 15 | 24 | 15 | **15 / 50** |
| CHAP (Large) | Derecho | 188 | 8,353,935 | 30,000,000 | 4,000,000 | **— / 500,000,000** |
| | Derecho GPU | 34 | 10,000 | 185,000 | 9,800 | **— / 1,000,000** |
| | Casper | 189 | 11,000 | 50,000 | 10,000 | **— / 1,000,000** |
| | Casper GPU | 35 | 1,500 | 5,000 | 1,000 | **— / 50,000** |
| | Campaign_Store | 172 | 50 | 170 | 30 | **— / 2,000** |
| NSC (NSC) | Derecho | 32 | 20,944,000 | 52,602,000 | 20,444,000 | **— / 200,000,000** |
| | Derecho GPU | 3 | 142,300 | 198,412 | 142,000 | **— / 500,000** |
| | Casper | 29 | 10,000 | 50,000 | 10,000 | **— / 200,000** |
| | Casper GPU | 2 | 5,500 | 9,100 | 5,500 | **— / 20,000** |
| | Campaign_Store | — | — | — | — | **— / 2,000** |

WRAP, Divisional and Staff route by email or to staff (§ 4.4 of the design) and get no rows. A
pair with no row means "no limit, says so". Ben reviews the bold column on Thursday; the
numbers are a proposal from the data, not policy.

## 5. Items left for Thursday

1. Done 2026-10-29: Ben approved the seed as proposed (§ 4): medians from the snapshot as defaults, published caps or a multiple as the limit, no defaults for CHAP and NSC.
2. Done 2026-10-28: the production read as benkirk (§ 9); the fixtures
   `tests/fixtures/xras/outgoing/create_request_*.json`.
3. Before the phase-2 proof: open a Large and an NSC opportunity in `xras-admin-demo`.

## 6. The dev proof (samuel-dev ↔ XRAS-dev)

The branch rolls by `gh workflow run "Publish Images and CIRRUS Deploy" --ref <branch>`; Ben
approves and posts in a 2FA Playwright session; every request is benkirk's.

| Phase | Do | Check in `sam_dev` | Check in the demo admin app |
|---|---|---|---|
| 0 | roll, `watch-dev` | the Configuration card shows `submit_enabled: true`; both tables and the seed rows | — |
| 1 | UHSS0003 (1439571) Extension from My Requests; UHSS0002 (1439543) Supplement; the admin Add-action on UHSS0004; one deliberate Discard | rows `submitted`, `created`, `aborted`; one verified `xras_remediation_event` per step; after the post: `xras_action_log` processed, the allocation moved, the row `applied`; the tab's badges | the action under the family, `Submitted`, dates, comments and amount as typed; the discarded action `isDeleted` |
| 1b | UHSS0003 Renewal with a rendered PDF | the row carries `eula_sha`; the document step verified; a new window after the post | a third line on the family; "Requirement Met" |
| 2 | New under Exploratory 532221 with an Advisor Letter; New under Small 532220 with a looked-up NSF award | `submitted` with `requestNumber` null → after the post `applied`, `project_id` (UHSS0007), `projcode_result`; `xras_rename` renames; the mnemonic came from the row, not the ladder; the Small validate passes without typing the officer fields | the drafts listed; approve and post; the rename lands |

Acceptance for #871: a lead files an Extension on UHSS0002 from samuel-dev, Ben approves and
posts it on xras-admin-demo, `sam_dev` applies it, and the My Requests row reads applied.

## 7. Gates that will trip, and what resolves them

Manifest pins (the 0a tests, `test-dev-render.sh`); `test_modal_shell_contract` (pin the
fragments on `#xrasRequestModalBody`); `test_route_map_parity` (regen per commit);
`test_schema_validation` with `_BOOTSTRAP_TABLES` and the blob; the op-name length test in
`test_xras_remediation_event.py`; `test_docs` (status lines, cited paths); `test_template_csp_lint`
(no inline script in the EULA reveal); `test_dbbrowse_redact` (`payload` is prose, list it
`_KNOWN_SAFE` if flagged); the perf baseline for `/user/requests` (one query over the led and
administered projects, never a per-project loop); `test_layer_imports` (the manage module
imports from `sam.integration`, never `sam.queries.__init__`).

## 8. Risks inside the one PR

- The New form is the unprototyped half: it ships last (2a), and `XRAS_SUBMIT_ACTION_TYPES`
  keeps it dark on production after the lever flips.
- The LFS blob conflicts with any concurrent regen: regenerate once, last.
- The editor verbs the flow reuses (attributes, resources, dates, roles, submit) verify through
  the reports family by number; on a line SAM minted the flow scopes them to the by-id read
  (`reading_draft`, 1c). On a New every step after the create reads by id, the PI role
  included: its roster read by number has no number to use (2a).
- `require_project_permission(EDIT_PROJECTS)` admits stewards by design (§ 4.4).

## 9. Probe record (2026-10-27)

Run on XRAS-dev before the sprint, with the helper discipline of `XRAS_DEV_PROBES.md` § 2 (one call
per write, verify by re-read, JSONL log in the session scratchpad). Every write was benkirk's
and was soft-deleted afterwards.

**Reads.**

| Probe | Answer |
|---|---|
| `required_fields` for New 500019, all seven allocation types | `title` and `abstract` everywhere. Grants on Small 500024 and Large 500023 (grantNumber, fundingAgencyId, piName, title, beginDate, endDate, programOfficerName, programOfficerEmail) and External 501276 (no officer, no title). Data Analysis 500848: Renewal title and abstract, Supplement none, Extension `endDate` + `userComments`. External: Renewal title and abstract, Supplement none, **Extension none** |
| `/types/documents` | `documentTypes[]` with `allocationActionTypes[].actionTypes[]{hardPageLimit, isRequired, allowMany}`: Main_Doc 20 (Large, NSC; New and Renewal; 10 pages), Supp_Info 21 (Educational New), Advisor_Letter 22 (Exploratory New and Renewal), Progress_Report 24 (Small, Educational Renewal); Final_Report 23 and Final Review 429 bind nothing |
| `required_documents_status` | on an Extension `[]`; on a New or Renewal with the letter uploaded "Requirement Met" |
| `GET /v1/opportunities` as benkirk | five open on the demo (Classroom 532223, External 533276, Data Analysis 532222, Small 532220, Exploratory 532221; no Large or NSC). Each carries `rules{canSubmitNewRequest, noAvailableActionsReason, resourceIdsAvailableForNewRequest, gracePeriod, availableActionRequests[]}` and `defaultAllocationAwardPeriod: 12`. **`availableActionRequests` is per XA-USER**: benkirk sees Renewal on UHSS0002, UHSS0004, UHSS0005 and UHSS0006 under Exploratory; `arcguest` sees none. `resources[].numbers[]` holds Default Amount (Cheyenne, 2017) and Dollar Value (Derecho) only, never a maximum |
| `GET /v1/requests/1439571` as benkirk | `rules{allowedOperations: [], allowedActions: ["Supplement","Transfer"], allowedActionsRes[], existingActions[]}`. After XRAS accepted an Extension add on the same line, `allowedActions` still read the same two: **advisory**, not a gate |
| `/types/all` | keys `actionTypes allcationDateTypes attributeSetRelationTypes attributeSetTypes documentTypes fosTypes grantTypes opportunityStateTypes permissionTypes personStatusTypes requestStatusTypes requestTypes resourceNumberTypes resourceTypes roleTypes unitTypes`; `grantTypes` is empty; `fosTypes` has 39 selectable rows |
| apidoc (`api.xras.org/apidoc/1.0/requests/*.html`) | `POST …/grants` and `PUT …/grants/<gid>` take params `fundingAgencyId grantNumber piName title beginDate endDate awardedAmount awardedUnits percentageAward programOfficerName programOfficerEmail isPending subAwardNumber primaryFosTypeId comments` → `{grantId}`; `POST …/documents` is JSON `{documentType, filename, title, document}` (or multipart); `DELETE …/actions/<aid>` takes nothing; `PUT …/fos/<id>` needs `isPrimary`; `POST /v1/people/<username>` takes name, email, phone, `isReconciled` |

**Writes.**

| Probe | Answer |
|---|---|
| `POST /v1/requests?opportunityId=532221&requestType=New` | 200: 1439703, action 373017 `New Incomplete`, `requestNumber` null, benkirk the only role; the response's `rules{}` is `allowedOperations ["Edit","Delete"]` and `existingActions` only — **no resource list**. Small (532220) likewise: 1439704 / 373018 |
| Documents on 373017 (JSON base64) | a 5 MB rendered PDF 200 in 2.3 s (304037); a **15 MB PDF 200 in 4.7 s** (304038); `probe.txt` **400 "document is not a PDF file"**. A `Main_Doc` was accepted on an Exploratory action, so the type is not checked against the rule book at upload |
| `POST /requests/1439571/actions?actionType=Extension&userComments=…` | 200 → 373019 with the comments stored (re-read) |
| `POST …/actions/373019/allocation_dates?endDate=2029-12-31` | 200 → 241159 `{Requested, beginDate: null, endDate: 2029-12-31}` |
| Small New fully populated (1439705 / 373020: PI role 537888, attributes with `isSupportedByGrants=true`, fos 500003 primary, Derecho 530902 amount 1000, dates 2027, EUA 539637 `null`) + `POST …/grants?grantNumber=2345678&fundingAgencyId=1` | grant 174568 stored with every other field null; **validate failed** with six errors: "Supporting Grant PI Name / Title / Program Officer Name / Program Officer Email / Begin Date / End Date is required" |
| `PUT …/grants/174568` with all six | 200; **validate successful** |
| `DELETE /requests/1439703`, `/1439704`, `/1439705` | 200; each then answers **401 by id** (the verify signal for a deleted draft). `DELETE …/actions/373019` → 200, re-read `isDeleted: true` |

**Production read, 2026-10-28** (Ben's say-so, GET only, as benkirk): `GET /v1/opportunities`
lists the same five University and External opportunities with per-user rules (UHSS0001 offers
Renewal; Small's `resourceIdsAvailableForNewRequest` has seven ids, Data Analysis four, none
with Derecho); `GET /v1/requests/1449367` answers 200 with `allowedActions ["Supplement",
"Extension","Transfer"]` and per-action resource ids. The 401 in the privilege register was for
an identity with no role on the line.

What this settles for the design: the New form's resource list comes from the opportunity's
rules, read before any write; an Extension is two writes before validate; a grant must carry
the six fields, which `AwardRecord` (`sam/integration/awards/base.py`) supplies for an NSF award
(`monitor` is the program officer) and USAspending does not; upload size needs no SAM-side cap
to work, and a non-PDF is refused by XRAS with a usable message; a Large or NSC New cannot be
proven on the demo until an opportunity of each is opened there.

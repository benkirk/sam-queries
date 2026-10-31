# XRAS submission sprint 2: the build plan

**Status: planned 2026-10-31; issue #872. The guide table's DDL is applied locally (3306, 3307,
the test Postgres); `sam_dev` and production wait on Ben (§ 4).** Builds phase 3 of
[`XRAS_SUBMISSION.md`](XRAS_SUBMISSION.md): someone with no SAM account files a New from SAMuel,
and the New form gains an optional roster. Sprint 1's build plan,
[`XRAS_SUBMISSION_SPRINT1.md`](XRAS_SUBMISSION_SPRINT1.md), is the shape this one follows. Every UI
step loads the `wire-dashboard-feature` skill first.

The case: ARC's untracked placeholder PIs are 55% of `New` handoff failures (`XRAS_SUBMISSION.md`
§ 1). SAM mints the placeholder itself, names it on the registration row that will merge it, and
merges it when the account arrives, so no placeholder SAM creates can be orphaned.

## 0. Decisions (Ben, 2026-10-31)

| Decision | |
|---|---|
| **The request page lays out the allocation types before anything starts** | A bare "Opportunity" dropdown says nothing to a first-time user. The page shows one card per type, modeled on ARC's opportunities table (`arc.ucar.edu/xras_submit/opportunities`: initial and supplement HPC limit, frequency, funding eligibility) |
| **SAM owns the copy; the gaps are a table** | Computed per type: limits (`allocation_type_resource_limit`), grants and documents with page limits (the rule book), award period and open state (XRAS). Not held anywhere: who the type is for, funding eligibility, review cadence, the rules nothing enforces (Derecho and Derecho GPU together reduce each limit proportionally; a supplement may add up to the same limit again). Those are `allocation_type_guide` (§ 4), edited on an admin card |
| **Types → verify → form, as one flow** | The identity questions are asked inside the request flow with the same schema and manage function as `/register`, not by sending the visitor through the `/register` pages |
| **Accounts only for approved projects** | A verified request-registration files no NUSD ticket. The ticket goes when the approved request is posted and refused for its placeholder PI (§ 2, step 3a). Later, out of scope: approval triggers automated account creation, with identity vetting by an outside service (visual compliance) and NUSD receiving the validated result and the agreement record. Step 3a is the one hook that swap replaces |
| **The logged-in New keeps its dropdown for now** | The overview is one shared piece with an eligibility hook; the logged-in door adopts it once a filter exists (NCAR staff need not see Exploratory) |
| **Sequencing** | Branch `xras-submission-sprint2` from `origin/staging`, rebased after Ben's phase-1/UX sweep merges. `make -C containers/sam-sql-dev everything-coherent` runs **after** this sprint, so sprint 1's dev rows (UHSS0002-0006) stay as samples |

Defaults, open to review at the first commit: the visitor New has **no admin picker** (the PI
names an admin once the project exists; `XRAS_SUBMISSION.md` § 5 forbids the logged-in user
search); roster entries take XRAS's **User** role (`USER_ROLE_TYPE_ID`; NCAR has no co-PI); people
without accounts are invited **at activation**, never before, so an unapproved New sends no mail.

## 1. What exists, and what the visitor door must replace

`/register` already gives the identity half: the person fields (`RegisterForm`), the agreement
gate, the code-or-link verify (`webapp/register/tokens.py`), the rate tiers, the honeypot and
the human check. It ends at a dead-end page, and its verify files the NUSD ticket at once.

The logged-in New (`_NewRequestForm`, `webapp/dashboards/user/requests_routes.py`) assumes a
SAM user at every step:

| Seam | Logged-in | Visitor |
|---|---|---|
| opportunities | `new_request_opportunities(current_user)`: per-user `rules{}` | the report context (probe 2: the same `rules{}`) |
| affiliation, code letters | `best_institution(pi)` feeds the ladder | the typed Institution matched to a live institution; else the letters picker |
| admin | the user search (`admin-suggest`) | none: the endpoint exposes the directory |
| preflight | `preflight_draft` as the PI | the same, ignoring "PI not in database" (`sam/xras/errors.py`) for this door only |
| terms link | `/user/requests/agreement` (login) | a public twin |
| submit | `request_action(xa_user=current_user)` | `create_person`, then `request_action(xa_user=placeholder, door='visitor')` with `registration_id` |
| ownership | `created_by` or `xa_user` equals the username | a signed token bound to the registration |

The XRAS side is half built: `merge_placeholder` (`sam/manage/xras_remediation.py`) folds a
placeholder into a real user and closes its `account_request` rows, but there is no
`create_person`, `reconcile_account_requests` has no `submission` branch, and the merge leaves
`xras_submission` alone. `xras_submission` has no `xras_username` column: `xa_user` holds the
placeholder. `registration_id` exists, unused since sprint 1.

## 2. Commit map

| # | Commit | What |
|---|---|---|
| D | docs | this plan; `XRAS_SUBMISSION.md` § 2, § 4.7, § 5 |
| P | probes (done 2026-10-31) | § 7. Settled: (1) the person fields all stick, but a second `POST` on a taken name **overwrites** it, so `create_person` refuses unless the name reads 404 first; (2) the report context returns the same `rules{}` as a user, so the visitor pages read opportunities there and **the placeholder is minted at submit**; (3) `add_role(User)` works for a real person, and for an unknown username XRAS **mints a bare reconciled person**, so a roster entry reaches XRAS only once `get_person` resolves it; (4) the merge moving PI and Allocation Manager was measured 2026-10-14 (§ 6.1, scenario 6) and again here. Large and NSC are now open on the demo |
| 0a | write client, levers | `create_person`, refusing a name that already resolves (probe 1), verified by re-read, op `create_person`; the placeholder namer `samr<registration_id>-user-<hex6>` (`PLACEHOLDER_USERNAME_RE` classes it; accepted by XRAS, probe 1); `add_role` refuses a username `get_person` cannot resolve (probe 3); `XRAS_SUBMIT_ANONYMOUS_ENABLED` (dev "1", prod "0", webapp only) with its manifest pins. The visitor door needs it, `ACCOUNT_REGISTRATION_ENABLED` and New armed |
| 0b | the guide | `AllocationTypeGuide` (§ 4); a CrudSpec card beside Request Limits; `allocation_type_overview(session, opportunities, *, eligible=None)` composing the guide row, the limits, the rule book's grants and documents, and the open opportunity. A type with no open opportunity (Large between calls) still shows, without a Start button |
| 1a | the public page | an anonymous blueprint (`/request/`) under the visitor lever, on the register blueprint's anonymous rate tiers; one `allocation_type_cards` macro; added to `DASHBOARD_BLUEPRINTS` |
| 1b | about you | `RegisterForm` and `register_request` with `purpose='submission'`, the human check, the agreement, the verify inside the flow. `AccountRequest`: a self-made `submission` may wait for its `xras_username`; `attach_placeholder()`. Verify files no ticket for this purpose. A signed draft token (a new salt, bound to `verified_at`) carries the flow, the resume link and the status page |
| 1c | the visitor New | first an identity seam lifted out of `_NewRequestForm`, so the visitor class overrides it instead of copying `gate`, `clean` and `draft` (`SWEEP_32_HANDOFF.md` § 2); then the overrides of § 1; token-gated twins of mnemonic-suggest and award-lookup; a receipt mail with the status link; one open visitor New per registration |
| 1d | status | the visitor's page: the account request's state and the submission's readout (`sam/queries/xras_my_requests.py`); Resume and Discard on a stopped row |
| 2a | roster | "People on the project" on page one, both doors: `Name <email>` lines, parsed by the event roster's `parse_roster`, at most 20. Matched by email server-side (`sam_merge_targets`); the PI, the admin and a visitor's own address drop out. An active SAM user XRAS knows gets `username` (an XRAS User role: `_mint_steps` adds `role:User:<u>`, `new_request_line` lists it for the pre-check); everyone else rides `ActionRequest.roster` without one (`draft.invitees`). An outage reading XRAS makes an invitee, never a role. The logged-in review says which is which; a visitor's never does (`roster_private`). A SAM user whose XRAS username differs would fail the post ("Username %s is missing"), hence the role only through `get_person(sam username)` |
| 2b | invitations | after activation, automatic (`_activate_after_reply`) and manual (the card's Activate): `invite_submission_roster` (`sam/manage/xras_activation.py`) makes each of the project's New submissions' invitees `invite_user(..., sponsor=lead)`; `invite_activated_roster` (`webapp/register/invite_mail.py`) mails the queued rows their links when `ACCOUNT_INVITATIONS_ENABLED` mounts them. A known address joins at once and `DUPLICATE` keeps a second call quiet; a failure is logged, never the activation's |
| 3a | ticket at approval | the inbound post's 422 "PI not in database", for a SAM placeholder whose row carries `registration_id`, files that registration's NUSD ticket (`send_ticket`, keyed `account_ticket:<id>`); the request ticket names `SAM-AR-<id>` |
| 3b | merge on arrival | a `submission` branch in `reconcile_account_requests`: on fulfillment, `merge_placeholder`, which also re-keys `xras_submission.xa_user` and `user_id`; a comment on the request ticket asks for the repost (XRAS never retries a post); Pending Users links the placeholder to its registration. Production cannot reach it while the visitor lever is off: no registration-linked rows exist |
| C | carried | the `TestTheTicketKey` flake |

Each commit regenerates the route-map snapshot it changes and lands its own tests.

## 3. Levers

| Lever | Read by | dev | prod |
|---|---|---|---|
| `XRAS_SUBMIT_ANONYMOUS_ENABLED` | the `/request/` blueprint's mount and every visitor stage | "1" | "0" |
| `ACCOUNT_REGISTRATION_ENABLED` | the same, and `/register` | "1" | "0", until client-IP forwarding (`implemented/ACCOUNT_REGISTRATION.md` § 6.1, item 2) |
| `ACCOUNT_REGISTRATION_LOGIN_REQUIRED` | `/register` and `/request` | "0" (Ben, 2026-10-31: both open to visitors on dev; Turnstile on, mail off, so an operator's Verify confirms) | unset, so on |
| `XRAS_SUBMIT_ENABLED`, `XRAS_SUBMIT_ACTION_TYPES` | as in sprint 1; New must be armed | "1", `all` | "0", `Extension,Supplement` |

## 4. DDL, spoon-fed

One table, `allocation_type_guide`: one row per XRAS allocation type.

| Column | |
|---|---|
| `xras_allocation_type_id` | unique; `allocationTypeInfo.allocationTypeId` (500024 Small, 500847 Exploratory, 500026 Classroom, 500848 Data Analysis, 500023 Large, 501276 External, 500088 NSC) |
| `allocation_type` | the SAM type **name** whose limits the card shows, resolved at read time |
| `title`, `summary`, `funding`, `cadence`, `notes` | the card's prose, utf8mb4; a null summary falls back to XRAS's `allocationTypeInfo.description` |
| `audience` | `university`, `ncar`, or null for everyone; `ncar` keeps a type off the public page (NSC) |
| `display_order`, `active`, `created_by`, `creation_time`, `modified_time` | house shape |

| | MySQL | Postgres (`sam_dev`) |
|---|---|---|
| create | `scripts/sql/create_allocation_type_guide.sql` | `scripts/sql/create_allocation_type_guide_pg.sql` |
| seed (7 rows, from ARC's table) | `scripts/sql/seed_allocation_type_guide.sql` | `scripts/sql/seed_allocation_type_guide_pg.sql` |

Applied 2026-10-31: local 3306, mysql-test 3307, the test Postgres (5434; the seed re-run is a
no-op). Still to do:

1. `sam_dev` (Ben): `psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/create_allocation_type_guide_pg.sql`, then the `_pg` seed; each script ends with its expected counts.
2. `make -C containers/sam-sql-dev regen-lfs-blob` once, last before the PR.
3. Production MySQL (Ben), before the visitor lever flips.

Pinned by `tests/integration/test_schema_validation.py` (`_BOOTSTRAP_TABLES`) and
`tests/unit/models/test_allocation_type_guide.py` (the two seeds carry the same rows).

## 5. The dev proof (samuel-dev ↔ XRAS-dev)

1. Register `smokeNNNN` through `/request/`; file an Exploratory New with an Advisor Letter and a one-person roster. No NUSD ticket at verify.
2. Ben approves and posts: 422, the registration's account ticket, and the request ticket naming `SAM-AR-<id>`.
3. Fulfill the account on `sam_dev`; the reconcile merges the placeholder and re-keys the row.
4. Repost: 200, the rename, the activation; the roster invitee gets the invitation.
5. The real user's My Requests shows the request; the `account_request` row reads merged.

Then `everything-coherent` (Ben).

## 6. Gates that will trip

`test_route_map_parity` (the new blueprint in `DASHBOARD_BLUEPRINTS`); `test_modal_shell_contract`;
`test_template_csp_lint`; `test_schema_validation` with the blob; the op-name length test; the
manifest pins in `test_xras_admin_client.py` and `test-dev-render.sh`; the perf baseline for the
public page (one read per type list, never a per-type loop); `test_layer_imports`;
`test_outbound_guards` (the receipt and invitation mail through the fail-closed mailer).

## 7. Probe record (XRAS-dev, 2026-10-31)

One call per write, verify by re-read, logged as JSONL in the session scratchpad
(step P of § 2). Every write benkirk-attributed or the probe placeholder's.

| Probe | Observed |
|---|---|
| `POST /v1/people/samr9001-user-p1031a` with `firstName middleName lastName email phone organization academicStatus residenceCountry isReconciled=false` | 200 with the person object; every field reads back by `GET /v1/people/<u>`, `isReconciled: false` |
| the same `POST` again with other names and email | **200, and it overwrites** the names and the email (the omitted fields stay). A create on a taken name silently rewrites someone else's person, so the verb checks for a 404 first |
| `GET /v1/opportunities` as the fresh placeholder, in the report context, and as benkirk | the same seven open opportunities with the same `rules{}` in all three: `canSubmitNewRequest` true and the same `resourceIdsAvailableForNewRequest` everywhere. The visitor door reads them in the report context, before any placeholder exists. **Large (534893) and NCAR Strategic Computing (534960) are now open on the demo.** XRAS's `allocationTypeInfo.description` texts are stale (Cheyenne, 400,000 core-hours) |
| create (1439870, Exploratory) → `roles/PI/<self>` → `roles/User/benkirk`, as the placeholder | all 200 and verified by the by-id read; one placeholder may author a line and add a real person as User |
| `roles/User/nobody1031-probe` (an unknown username, no person params) | **200, and XRAS mints a bare person**: every field null, `isReconciled: true`. A roster entry without a resolved XRAS person must never reach this route |
| cleanup | 1439870 deleted (401 by id); `samr9001-user-p1031a` and `nobody1031-probe` merged into benkirk (both 404 after) |

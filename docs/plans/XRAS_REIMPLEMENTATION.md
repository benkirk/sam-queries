# XRAS Integration Reimplementation (Python) — Final Plan

## Context

Legacy SAM (Java/Tomcat) is the site-side server for NCAR's XRAS allocation
integration: the XRAS broker **pushes** allocation decisions to SAM
(`POST /api/xras/v1/actions`) and **pulls** identity/request data from SAM
(`GET /api/xras/v1/people*`, `/requests/*`). This is the last major legacy
surface not yet ported to Python SAM. Goal: a **drop-in replacement** —
same URLs, same auth headers, same wire formats — with **full automation** of
all six action types, **fixed error codes** (400/422 instead of blanket 500s),
and a **DB-backed audit trail + admin dashboard** replacing the email-only /
paste-JSON-replay legacy workflow.

Exploration sources: `legacy_sam/doc/apis/*.md` (30-day log audit),
legacy Java under `legacy_sam/src/main/java/edu/ucar/cisl/sam/xras/`,
`legacy_sam/src/main/resources/{spring,hibernate/xras,json/xras,db/migration}`,
public docs at api.xras.org, and the current Python codebase.

## Key facts from exploration

### Traffic (30-day audit) — what must work
| Endpoint | Hits | Notes |
|---|---|---|
| `GET /api/xras/v1/people/{username}` | 3,547 | 1,341 404s are normal broker "search-before-create" |
| `POST /api/xras/v1/actions` | 158 | 27% 500-rate today (validation surfaced as 500) |
| `GET /api/xras/v1/people` | 30 | full identity dump |
| `GET /api/xras/v1/requests/request/{requestNumber}` | 3 | request/balance lookup |

Stale (0 hits): `requests/user/{u}`, `requests/role/{r}/{u}`, `dates/requests/{list}`,
`POST /roles/...`. Decision: port the three stale **reads** cheaply (same assembly
machinery, keeps drop-in complete); **skip** the stale roles POST.

### Direction & runtime shape
- SAM is **purely a server**. Zero outbound calls to xras.org anywhere in legacy.
  No client, no XRAS credentials. Only outbound side-effect is SMTP to admins.
- **No background services** — everything synchronous request-driven (no Quartz in
  the XRAS path). So no `sam-admin xras` daemon is needed; CLI is optional tooling only.

### Wire contract (must match exactly)
- Paths: `/api/xras/v1/...`.
- Auth: headers `XA-REQUESTER`/`XA-API-KEY` → Basic auth (synthesized only when both
  present and no Authorization header; XA headers then stripped) → verified against
  the **same `api_credentials` table** (bcrypt) requiring role `XRAS`
  (role ⋈ role_api_credentials). 401 body: pretty `{"message":null,"result":null}`,
  **no WWW-Authenticate header**.
- `GET /people` → bare array of `{username, firstName, middleName, lastName,
  organization, academicStatus, phone, email}` (null fields omitted).
  `GET /people/{u}` → bare object; 404 → `{"message":"username=<u> not found","result":null}`.
- `POST /actions` success → `{"message":"OK","result":null}`; manual-fallback also 200.
- `GET /requests/request/{n}` → `{"message":null,"result":{"projectIdLabel":null,
  "masters":[{requestNumber, requests:[{requestType("New"=earliest per project else
  "Renewal"), requestBeginDate/EndDate, allocationType, projectTitle, projectId,
  fos:[{xrasFosTypeId, isPrimary:true}], allocations:[{dates, allocatedAmount(STRING
  "%.1f"), remainingAmount(STRING, HPC-only, omitted if null), resourceRepositoryKey,
  actions:[{orderApplied(1-based), actionType, amount(STRING), endDate, dateApplied}]}]}]}]}}`.
  Unknown requestNumber → 200 with empty masters.
- Dates `yyyy-MM-dd` strings everywhere EXCEPT `dates/requests` (epoch-millis — keep).
- XrasAction inbound schema (all optional; absent strings → `""`; unknown fields ignored):
  actionId, actionType, actionBeginDate/EndDate, requestId, requestNumber(trim→projcode),
  requestType, requestAbstract, requestTitle, requestShortTitle, opportunityId/Type/Name,
  allocationType, awardDate, awardPeriod,
  resources[{actionResourceId, resourceRepositoryKey, awardedAmount(STRING), comments}],
  roles[{requestPeopleRoleId, roleType, username, beginDate, endDate,
  isAccountToBeCreated(forgiving bool: null→false, int→!=0, y/n/t/f strings),
  person{firstName, middleName, lastName, email, phone, organization, academicStatus, isReconciled}}],
  fos[{fosTypeId(STRING), fosNum, fosName, fosAbbr, isPrimary}], panels[...], grants[...].
- PI = first role roleType=="PI" whose date window brackets actionBeginDate;
  same for "ALLOCATION_MANAGER".

### Action-processing semantics (legacy selector, first match wins)
| actionType | condition | behavior |
|---|---|---|
| New | projcode !exists | Create project: title/abstract/lead=PI/admin=AM, allocation_type via extractor chain, AOI from primary fosNum, org from lead, NONEXEMPT, generate projcode (facility+mnemonic+seq), allocate GID; add contracts from grants (trailing ≥6-digit core number matched against existing contracts; skip blank/"N/A"); per resource: create allocation (repoKey→resource, start clamped ≥ commission date, end-of-day end); add all valid role users to accounts; finally **set project inactive** — human review activates it (success email = trigger) |
| New/Renewal | project exists | Update fields (active=true); contracts; per-resource: create alloc if none overlapping; extend if end grows (error if shrinks); undo AUTO/DEFAULT canned allocation (compensating adjust "UNDO AUTO/DEFAULT"); then supplement (>0) or adjust (<0). comments=="AUTO_DEFAULT_ALLOCATION_TRANSACTION" → extension only |
| Supplement | project exists | Per resource: create alloc if none (start=today, end=latest contract/alloc end), else supplement if >0; ≤0 ignored (log warn) |
| Adjust | project exists | Like Supplement; legacy silently drops negatives — we log-warn instead |
| Transfer | project exists | 1 negative source + ≥1 positive dests, same project, Σ==0, source clamped to available |
| Extension | project exists | Ignores payload resources; extend latest allocation of every active account to actionEndDate; error if it would shrink any |
| other (Renewal w/o project, Advance, …) | — | Manual fallback: persist + email, return 200 |

Extractors (New/Update only):
- **AllocationType**: ~11 ordered text-matching strategies on opportunityName/title/
  allocationType → (panel, type) → allocation_type_id. (ACCESS Discover/Explore, NSC,
  External, CSL, Large, SmallNonNSF, SmallNSF, Classroom, Data, ASD-UNIV, ASD-NCAR.)
- **Mnemonic**: "NCAR "-prefixed opportunity → PI org parentage (lab level); external PI →
  institution; internal → organization. Python already has
  `MnemonicCode.resolve_for_institution/organization` (`src/sam/core/organizations.py:455,475`).
- **AreaOfInterest**: primary fosNum → area_of_interest by id or name.
- `/people` org fixup: `UCAR/NCAR:<acronym>` → parentage walk ("NCAR/<acr>";
  {NCAR→NCAR, UCAR→UCAR, UCP→"UCAR Community Programs"}, no-parent→UCAR).

### Error-semantics changes (deliberate, user-approved)
- Malformed JSON → **400** (legacy 500). Validation failures → **422** with structured
  error list (legacy 500). Keep: 200 on manual-fallback, 200+empty on unknown request,
  404 people-not-found. Confirm with the XRAS/ACCESS side that the broker treats
  4xx like 5xx (i.e. logs/flags) before cutover.
- Every inbound action persisted regardless of outcome (see xras_action_log).

### Known legacy bug to design around
`XRAS_SAM_POSTING_BUGREPORT.txt` (2026-07-20): NPE in identity sync rolls back user
creation → queued XRAS actions fail ("PI not in database" / mnemonic failures).
Port lesson: action processing must tolerate/report missing users cleanly (422 +
persisted for replay), and mnemonic resolution failures must be reviewable, not fatal-opaque.

## What already exists in Python SAM (reuse, don't rebuild)

- **ORM**: `XrasResourceRepositoryKeyResource` (`src/sam/integration/xras.py`);
  all 6 views in `src/sam/integration/xras_views.py` (camelCase matches wire contract).
  View smoke tests in `tests/integration/test_views.py::TestXrasViews`.
  (`xras_request` view has a known GROUP BY/ONLY_FULL_GROUP_BY issue — fix the view
  or bypass it, see Phase 2.)
- **API recipe**: PR #346 SSG endpoints `src/webapp/api/v1/queue.py`,
  `wallclock_exemption.py` — blueprint + `register_error_handlers(bp)` +
  `sam.queries.*` + `@login_or_token_required` + cache/refresh. Blueprints registered
  in `src/webapp/run.py` (~line 40 imports, ~line 402 register).
- **Auth**: `login_or_token_required` (`src/webapp/utils/api_auth.py`), backed by
  `api_credentials` (PR #347). Roles already captured in `g.api_key_roles` but NOT
  enforced — XRAS needs enforcement (new small decorator).
- **Manage ops** (`src/sam/manage/`): `management_transaction`, `create_allocation`,
  `update_allocation`, `log_allocation_transaction`, `exchange_allocations`,
  `extend_project_allocations`, `renew_project_allocations`, `add_user_to_project`,
  `change_project_admin`; `_resolve_*` helpers in `summaries.py`.
  Replay invariant: all allocation mutations via `log_allocation_transaction`.
- **Projcode/mnemonic**: `next_projcode(session, facility_id, mnemonic_code_id)`
  (`src/sam/projects/projects.py:1516`), `Project.create` classmethod,
  `MnemonicCode` resolution + `create` (`src/sam/core/organizations.py`).
- **GID**: `GidAllocation.allocate_next_gid` exists on the **unmerged `gid_allocation`
  branch** (PR #263, gated on seeding prod `gid_allocation`). Soft prerequisite for the
  New handler; until merged/seeded, New leaves `unix_gid` NULL for assignment during
  the manual-activation review (projects arrive inactive anyway).
- **Email**: find existing notification/email utility used by webapp (or add a minimal
  one); templates become Jinja2 ports of `xras-post-action-{new-success,new-failure,manual}.vm`.
  Config: recipients list (legacy `xras.actionpost.recipients` = hdt@ucar.edu in prod).
- **Test fixtures**: real sample payloads in `legacy_sam/src/main/resources/json/xras/`
  and `src/test/resources/xras/`; legacy tomcat logs in `legacy_sam/sam-tomcat_logs/`.

## Implementation plan

### Phase 1 — Auth + read endpoints (`/api/xras/v1` blueprint)
Files: `src/webapp/api/xras/__init__.py` (+ `people.py`, `requests.py`, `actions.py` as it grows)
or single `src/webapp/api/v1/xras.py` to start; register in `src/webapp/run.py` with
`url_prefix='/api/xras/v1'`.
1. **XA-header shim**: `before_request` on the blueprint (or tiny WSGI-level translation
   in the decorator): if both `XA-REQUESTER` and `XA-API-KEY` present and no
   `Authorization`, synthesize Basic; always ignore XA headers afterward.
2. **Role-enforcing decorator**: `xras_api_required` wrapping the token path of
   `login_or_token_required` and requiring `'XRAS' in g.api_key_roles` (also allow
   admin session users for dashboard-triggered replays). Legacy-shaped 401
   (no WWW-Authenticate, `{"message":null,"result":null}`) via blueprint error handler.
3. **People**: query `XrasUserView` (optionally by username) + org-name fixup helper
   (`sam/queries/xras.py`: parentage walk per legacy `UCAROrgNameQuery`). Bare-object/
   bare-array responses, NON_NULL omission, wrapper-shaped 404.
4. **Requests**: `sam/queries/xras.py` assembly reproducing `RequestFactory`:
   read `xras_request`/`xras_allocation`/`xras_action` views, group into
   masters/requests/allocations/actions, derive "New"/"Renewal" (earliest begin per
   project), format amounts `"%.1f"` as strings. Also port cheap stale reads:
   `requests/user/{u}`, `requests/role/{r}/{u}` (role map pi|co_pi|allocation_manager;
   co_pi returns empty), `dates/requests/{list}` (epoch-millis).
   Fix `xras_request` view GROUP BY issue (adjust view SQL in local + prod DB, or
   assemble from base tables instead of the view — prefer fixing the view; schema
   validation tests will pin it).

### Phase 2 — Action ingestion + audit trail
1. **New table `xras_action_log`**: id, received_time, remote_actor, action_type,
   request_number, raw_payload (JSON/mediumtext), status
   (processed|manual|failed|replayed), error_messages (JSON), projcode_result,
   processed_time, processed_by. DDL in `sql/`, ORM model in
   `src/sam/integration/xras.py`, schema-validation test, factory, obfuscated-snapshot
   regen note (payloads contain PII — scrub in snapshot Makefile rule).
2. **Input schema**: `src/sam/schemas/forms/xras.py` — `XrasActionSchema` (nested
   Resource/Role/Person/Fos/Grant/Panel schemas) with legacy tolerances: unknown=EXCLUDE,
   missing strings → `""`, forgiving bool for `isAccountToBeCreated`, `yyyy-MM-dd`
   validation. Export from `forms/__init__.py`.
3. **`POST /v1/actions` route**: parse (400 on malformed JSON) → persist log row →
   dispatch to processor → 200 `{"message":"OK"}` / 200 manual-fallback / 422 validation
   (structured errors, also stored + emailed) / 500 unexpected. Email notifications
   (Jinja2 ports of the 3 Velocity templates, raw JSON attached, recipients from config,
   disable flag for dev/test).

### Phase 3 — Action processors (`src/sam/manage/xras.py` or `src/sam/xras/`)
1. **Selector** mirroring legacy order (New→Update→Supplement→Adjust→Transfer→Extension→manual).
2. **Extractor chain** for allocation type: data-driven list of (predicate, panel, type)
   rules resolved to allocation_type_id at runtime (per memory: pair names, not IDs).
3. **Handlers**, all inside `management_transaction`, reusing manage ops:
   - New: `Project.create` + `next_projcode` + mnemonic/AOI extractors +
     contract linking + `create_allocation` per resource + `add_user_to_project` +
     set inactive. GID via `GidAllocation` when available, else NULL.
   - Update: overlap/extend/supplement/adjust logic incl. AUTO/DEFAULT undo.
   - Supplement/Adjust: thin wrappers over `update_allocation`/`create_allocation`
     (negative-adjust: warn + record, don't silently drop).
   - Transfer: `exchange_allocations` (verify semantics match: same-project,
     clamped source, multi-destination loop).
   - Extension: `extend_project_allocations` across active accounts.
4. Every handler returns structured errors → 422 path; every mutation logged via
   `log_allocation_transaction` (replay invariant).

### Phase 4 — Admin dashboard + CLI + replay
1. Dashboard tab (Integrations → XRAS): list `xras_action_log` with status filters;
   detail view (pretty payload, errors); actions: **replay** (re-dispatch stored JSON),
   **activate project** (link to existing project admin), paste-JSON manual post
   (parity with legacy XRASPostBean). Routes follow §8/§9 rules
   (`require_permission`, form schemas).
2. `sam-admin xras` (new `src/cli/xras/` domain): `--list-pending`, `--replay <id>`,
   `--validate-mapping` (xras_resource_repository_key_resource sanity), DB-direct
   pattern like user/project commands.

### Phase 5 — Parity verification + cutover
1. **Golden tests**: replay legacy sample payloads (`legacy_sam/src/main/resources/json/xras/`,
   `src/test/resources/xras/`) through the new endpoint in tests; assert DB effects
   (project fields, allocation amounts, transactions) and response bodies.
2. **Read parity script** (`scripts/` or `utils/`): fetch `people`,
   `requests/request/{n}` from legacy and new for a sample set; diff normalized JSON
   (like the PR #346 SSG parity runs).
3. Cutover: create/verify `api_credentials` row + XRAS role mapping for the broker,
   coordinate proxy switch of `/api/xras/*` to the Python app, watch `xras_action_log`
   + email for the first live actions. Legacy stays available for rollback.

## Verification
- `pytest` — new suites: `tests/api/test_xras_api.py` (auth shim incl. XA headers,
  people/requests wire shapes, actions status codes), `tests/unit/test_xras_actions.py`
  (each handler against factories; golden payloads), schema-validation for
  `xras_action_log`; existing `TestXrasViews` extended for the fixed `xras_request` view.
- Manual: `docker compose up webdev --watch`, then curl with XA headers:
  `curl -H 'XA-REQUESTER: xras' -H 'XA-API-KEY: ...' localhost:5050/api/xras/v1/people/benkirk`;
  post a sample action JSON and review it in the new dashboard tab; replay from
  dashboard and CLI.
- Parity script diff vs legacy prod (read endpoints) before cutover.

## Open items / risks
- **PR #263 (`gid_allocation`)** must merge + prod pool seeded for full New-handler
  parity; interim: NULL GID + manual assignment at activation.
- `xras_request` view GROUP BY fix touches the prod DB (view redefinition) — small,
  but coordinate like other view changes.
- Confirm with XRAS/ACCESS operators that 400/422 responses are acceptable replacements
  for legacy 500s before cutover (broker retry behavior unknown).
- Transfer semantics: verify `exchange_allocations` matches legacy clamping/multi-dest
  behavior during Phase 3 (adjust or add a dedicated helper if not).
- Email delivery from the k8s webapp (SMTP relay config) — legacy sends from
  sweg-notify@ucar.edu; needs an SMTP path in the new deployment.

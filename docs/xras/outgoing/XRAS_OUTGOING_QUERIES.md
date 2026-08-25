# XRAS "outgoing" queries — an account-creation worklist

**Status:** **implemented as built**, 2026-08-20, on `probing_xras`. The design
below stands as written except where § 0 records a deviation.
**Probed:** 2026-08-19 against production
`https://api.xras.org` (two rounds: the original survey, then a follow-up after
the full API documentation was located), cross-checked against the XRAS-admin
web app.

This is a handoff document. It is written so an implementation session that has
never seen the original conversations can start cold. It records what the XRAS
API *can* and *cannot* do — including every dead end, with enough detail that
nobody re-probes a path that is already closed — and the design decisions
already made with the operator, so the implementation session builds rather
than re-litigates.

> **Direction of travel.** Everything in `docs/xras/incoming/` is XRAS → SAM
> (they push actions, they pull our GETs). This document is the opposite
> direction: **SAM calling out to XRAS**. There is no such code in the repo
> today — `api.xras.org` is zero hits outside this document.

---

## 0. As built — deviations and findings

Seven commits. Everything in §§ 7-8.1 shipped; §§ 7.6 (the notes table) and
8.2 stay deferred as planned. What follows is only what the implementation
learned that this document did not already say.

### The plan was wrong about one fixture

§ 10 named `new_ncar4214_ok.json` as the pre-flight case whose
`placeholder34-user-00034` should classify **absent**. It does not, and
should not: that role carries `endDate: 2026-07-28` against an action
beginning `2026-07-30`, so the roster's date window correctly excludes it —
the role is over and the handoff does not need the account.
`new_ncar4227_failed.json` carries an in-window placeholder and is the test
case instead. A second test pins the exclusion itself, so the window rule
cannot silently loosen.

### Tier-III measured, against the unscrubbed corpus + the dev database

41/41 payloads parsed, 72 distinct real usernames, **9 worklist rows: 4
absent, 5 inactive**. Two numbers matter:

- An existence-only predicate would have found 4 of 9. The `inactive` class
  is the majority of real work, not an edge case.
- **All 5 usernames carrying `isAccountToBeCreated: true` were existing,
  *active* SAM accounts** — the flag was 100% stale in this corpus. Using it
  as the predicate would have produced 5 false positives and found none of
  the 9 real cases. § 5's trap, confirmed on production data.

Nothing derived from that run is committed; it is recorded here as counts
only.

### The live probe confirmed the API shape

`scripts/xras/probe_outgoing.py` against production: 13 resources, all
carrying `resourceRepositoryKey`, reconciling **13/13** against
`xras_resource_repository_key_resource` with zero unmapped and zero dangling
(§ 6). `reports/requests` paginates cleanly — 3 pages of 10, 30 distinct
rows, zero overlap. Role entries are `{person, roles[]}` with the inner
`role` key, person inline carrying `isReconciled` and `residenceCountry`.
Across 64 sampled role entries the only values were `PI`, `Allocation
Manager` and `User` — **no co-PI**, as § 3.4 predicted from
`/v1/types/roles`.

Of 43 role-people in 25 Approved requests, **7 were `isReconciled: false`**.
The worklist has real content waiting.

### Three small deviations from § 7

1. **`iter_request_pages()` was added underneath `iter_requests()`.** The
   sweep must distinguish "the data ran out" from "I hit `max_pages`", and a
   generator flattened to individual requests cannot report that. A silent
   cap would read as full coverage in the ledger detail.
2. **The roster is the union of `roster_usernames` and both
   `role_candidates` lists**, not the roster alone. `role_candidates` applies
   a looser begin-date rule (legacy defect 3), so a PI can resolve while
   absent from the roster — and a missing PI still fails the handoff.
3. **Config follows `sam/notify/config.py`**, i.e. `XrasApiConfig` with
   `from_environment()` reading Flask-config-then-environment, rather than the
   `from_env(env=None)` sketched in § 7.1. Same behavior, the repo's idiom,
   and it brings a secret-free `summary()` along for the configuration card.

### Still open

The `productionEndDate` question (§ 13.1) is now sharper: the probe shows
**NSF NCAR Derecho and Derecho-GPU both carrying `productionEndDate:
2026-05-12`**, three months in the past, alongside Cheyenne (2023-12-31) and
Yellowstone (2017-12-31) which are genuinely retired. Nothing in SAM reads
that field, so nothing breaks either way — but it is worth asking XRAS
whether it is stale data or a telegraphed retirement.

---

## 1. Why

**Goal: a dashboard card listing the user accounts that must be created (or
reactivated) in SAM before an XRAS handoff can succeed** — who, why, with
detail. Account creation is a manual process. This complements the XRAS
action-log card rather than replacing it.

This targets the largest known failure mode. `src/sam/xras/handlers/new.py:24-27`
records the measured causes of the legacy 70% failure rate:

> an unresolvable mnemonic (24%, a frozen `user_organization` table),
> **unreconciled ARC placeholder identities (55%)**, and resource keys with no
> mapping row.

`scripts/xras/scrub_payload.py:32-34` independently states that
`<name>-user-<token>` identities "are 55% of production failures". So the
worklist addresses, by the repo's own measurements, the single biggest cause of
XRAS handoff failure.

**Timing context.** PR #457 landed the capture-only receive path; the next step
disables capture-only (live dispatch) and ACCESS is repointed at SAM, at which
point `xras_action_log` begins filling (it is at 0 rows until then).
**Consequence for this design:** actions in the log may be `received` (under
capture-only) *or* `processed` / `failed` / `manual` (under live dispatch), so
the worklist derivation must be regime-proof — it classifies against the
*current* state of `users`, never against the action's `status`.

### 1.1 Scope decisions, already settled with the operator

These were decided on 2026-08-19; the implementation session should not reopen
them without new information:

1. **Step 0 — the API key moves into OpenBao first** (§ 11). It currently sits
   in cleartext in the operator's home directory and on `crlogin` hosts.
2. **Read-only card first — no new table in this PR.** The worklist derives
   entirely from existing data; the operator-notes/dismissal table
   (`xras_account_event`, § 7.6) is an immediate follow-up PR.
3. **Cheap win (a) only** — the two-sided `--validate-mapping` (§ 8.1) rides
   along. Cheap win (b), the `opportunityId` → allocation-type map (§ 8.2), is
   **deferred**: assess after the first triage week under live dispatch.
4. **The `xras_sweep` scheduled task ships in this PR, switched off** — its
   name added to `SAM_TASKS_DISABLED` in the same change (the fail-open trap;
   see the `xras_notices` precedent).
5. One PR vs `staging`, as an ordered commit series (§ 12).

---

## 2. Which API — read this first

Two different services are easy to confuse.

| | |
|---|---|
| **XRAS Rules Service** — `xras-rules-service-demo.xsede.org/apidoc/` | A separate, mostly-POST engine (notifications, validate, required_documents, clone rules). Paths are `/api/v1/...`. **Our key does not open it.** Not useful here. |
| **XRAS Allocations API** — `https://api.xras.org/v1/...` | ✅ **This is the one.** |

The two share endpoint *names* (`opportunities`, `requests`), which is exactly
why they get confused. Note the path prefix differs: `/api/v1/` for the rules
service, `/v1/` for the allocations API. `api.xras.org/api/v1/...` 404s.

### 2.1 The documentation — and where it hides

`https://api.xras.org/` is a two-iframe page. The left frame's **"API"** link
targets `https://api.xras.org/apidoc` — **which 404s when fetched directly**,
and that decoy is why the first survey concluded no endpoint documentation
existed. The real page is
**`https://api.xras.org/apidoc.html`** ("ALLOCATIONS API 1.0"), with static
per-endpoint detail pages under `https://api.xras.org/apidoc/1.0/...`. Those
pages carry parameter tables and example responses and are fetchable with plain
curl. Also useful: `api.xras.org/api/overview` and
`api.xras.org/api/request_headers`.

### 2.2 Authentication

```
XA-API-KEY:             <key>          # see § 11 — never in this repo
XA-ALLOCATIONS-PROCESS: NCAR
XA-CONTEXT:             submit | report        ✅ 200
                        review | admin         ❌ 401 (key not provisioned)
XA-USER:                <username>     # required header; scopes /v1/requests only
```

Omitting `XA-CONTEXT` gives `400 {"message":"XA-CONTEXT header missing"}`.

⚠️ **`submit` and `report` are NOT the same surface.** The first survey
concluded they were identical, but that held only for the endpoints it knew
about. The **Reports family (§ 3.2) answers only under `XA-CONTEXT: report`**
— the same paths 401 under `submit`. Everything else this project reads
(`/v1/people`, `/v1/resources`, `/v1/requests`, `/v1/types/*`,
`/v1/search/people`) works under `report` as well. **Design consequence: the
client hardcodes `XA-CONTEXT: report`** — one context, read-only semantics, no
knob.

`XA-USER` is a required header on every call, but outside `/v1/requests` it
scopes nothing — the reports endpoints return process-wide data regardless of
its value. The existing operator scripts use `arcguest`; the client takes it
from `XRAS_API_USER` (default `arcguest`). Per-user impersonation is **not
needed anywhere in this design** (the enumeration made it obsolete, § 3.2).

Server is Rails behind Apache/Passenger. A Rails HTML 404 page means "no such
route"; a JSON `{"message":..., "result":null}` means the route exists and the
request was refused or found nothing. Every JSON response wraps its payload in
that `{message, result}` envelope — the client unwraps it centrally.

---

## 3. The readable surface

### 3.1 Per-user and global endpoints (work under `submit` and `report`)

| Endpoint | Scope | Returns |
|---|---|---|
| `GET /v1/people/:username` | global directory | firstName, middleName, lastName, email, phone, organization, academicStatus, **residenceCountry**, **isReconciled**, orcid, hasOrcidToken |
| `GET /v1/search/people?q=...` | global directory | the same person shape, matched on name/username fragments |
| `GET /v1/requests` | **`XA-USER`-scoped** | every request that user is PI / CoPI / Allocation Manager on, in full |
| `GET /v1/requests/:requestId` | `XA-USER`-scoped | one request; **401** if the user has no role on it |
| `GET /v1/opportunities` · `/:id` · `/list/:ids` | global | open opportunities; the id forms also resolve **historic/Terminating** ones |
| `GET /v1/resources` · `/v1/resources/:id` | global | 13 resources, **including `resourceRepositoryKey`** |
| `GET /v1/panels` · `/v1/panels/:id` | global | 5 panels **with member rosters** |
| `GET /v1/types/roles`, `/v1/types/actions`, `/v1/types/request_status`, ... `/v1/types/all` | global | the process's own vocabularies (§ 3.4) |
| `GET /v1/permissions/:username` | global | XRAS permission grants for a person |

**Unknown username** gives a clean `404 {"message":"username=X not found"}`.

### 3.2 The Reports family — `XA-CONTEXT: report` only ⭐

This is the finding that reshapes the whole design. The first survey probed
`/v1/reports` bare (404, § 4.7) and closed the path; the real routes live
*under* it and were located via `apidoc.html`. All verified 200 with our
existing key under `report` context, 401 under `submit`:

| Endpoint | Returns |
|---|---|
| `GET /v1/reports/requests` ⭐ | **Every request in the NCAR process, unscoped.** Paginated: `?limit=N&prevMinRequestId=M` (descending `requestId`; strictly-less-than, so pass the smallest id seen to get the next page). Filters: `?status=` (one of `Submitted, Approved, Rejected, Incomplete, Under Review`) or `?active=true|false` (Approved with end date in future/past — mutually exclusive with `status`). Optional `fosTypeId`, `piOrganizationId`. |
| `GET /v1/reports/request_numbers/:requestNumber` ⭐ | **Look up by request number — i.e. by projcode.** Same response as `/v1/requests/:requestId` minus the `rules` object. Optional `?status=active|inactive`. |
| `GET /v1/reports/allocations` | All allocations (`?status=active` → ~7.9 MB for NCAR today): actionId, actionType, begin/end dates, requestNumber, PI name/institution/**username**, opportunity, per-action `resources[]` and `requestedResources[]`. |
| `GET /v1/reports/username/:username` | Roles and panels for one person: requests grouped by role name. |
| `GET /v1/reports/opportunity_requests/:opportunityId` | Requests for one opportunity. |
| `GET /v1/reports/requests/:requestId` | One request via the reports path. |
| `GET /v1/reports/fos/:fosId` | Requests by field of science. |

**The `reports/requests` rows are complete**: verified top-level keys include
`roles[]` — each with the **full person object inline** (all `/v1/people`
fields, including `isReconciled` and `residenceCountry`) — plus full
`actions[]` (resources with Requested/Recommended/Approved amounts, dates,
states, documents), `fos`, `grants`, `conflicts`, `publications`.

Three consequences, each load-bearing:

1. **Enumeration exists today, on our key.** No per-identity polling, no
   roster crawling, no impersonation, and no "please provision an admin
   credential" ask — the original Phase-2 request to XRAS is moot.
2. **Dropped-push detection is nearly free**: diff the Approved set's
   `requestNumber` against `project.projcode` (§ 6) wholesale.
3. **The worklist's hardest population — a brand-new PI on a solo New request,
   connected to nobody SAM knows — is reachable *before* the push**, with
   their person detail already inline.

### 3.2a `reports/username/:username` payload shape — probed 2026-08-22

Wired for the **XRAS User** modal. Probed live against a PI (`janebaldwin`) and
an Allocation Manager (`Karpus`); a stuck-placeholder (`*-user-*`) **404s** here
(and at `/v1/people` and `/v1/permissions`) once merged away, so the modal
degrades on `None`. `/v1/permissions/:username` returned an **empty list** for
both real users — carries nothing worth rendering, so it is not wired.

```
{ panels: [...],                      ← reviewer panel memberships (unused; our
  requestRoles: [                       people are PIs / Managers, not reviewers)
    { roleName: "Project Lead",       ← XRAS display vocabulary (Project Lead /
      requests: [                       Allocation Manager / User) — matches
        { requestNumber: "UCIR0072",    ROLE_TYPES[].display
          requestId: 1446007,          (also spelled requestID)
          requestTitle, actionType, allocationType,
          opportunity, opportunityId,
          beginDate, endDate, updateDate,
          pi, piUsername, piInstitution, coPis[],
          fos, fosTypeId,
          requestedResources[], resources[] },
        ... ] },
    ... ] }
```

⚠️ **No `requestStatus`** in this feed — the panel keys each request to the
Request modal by `requestNumber` (the projcode) for the live status instead.

### 3.3 `/v1/requests` payload shape

(One request from `reports/requests` / `reports/request_numbers` is this same
shape minus `rules`.)

```
rules:
  allowedOperations[], allowedActions[]          ← what this PI may do NEXT
  allowedActionsRes[{actionType, availableResourceIds[]}]
  existingActions[{actionId, actionType, reviewsViewable}]
requestId, requestType, requestStatus, requestNumber, opportunityId,
opportunity_name, title, shortTitle, abstract, submitDate, isDeleted,
isSupportedByGrants, grantTypeId, publicURL
actions[]:
  actionId, actionType, actionStatus, entryDate, userComments, adminComments,
  collaborators, returnedForCorrections, finalReview, finalReviews[], states[],
  resources[{resourceId, resourceName, resourceUnits, amount,
             type: Requested | Recommended | Approved, comments}]
  documents[{documentId, documentType, title, filename, size}]
  allocationDates[{allocationDateId, allocationDateType, beginDate, endDate}]
  opportunityAttributes[], resourceAttributes[]
roles[]:
  person{username, firstName, middleName, lastName, email, phone, organization,
         academicStatus, residenceCountry, isReconciled, orcid, hasOrcidToken}
  roles[{roleId, role, roleTypeId, beginDate, endDate, isAccountToBeCreated}]
fos[{fosTypeId, fosNum, isPrimary}], grants[],
conflicts[{conflictId, conflictType, conflictPerson}], publications[]
```

### 3.4 Vocabularies — verified against the live process

| Field | Values |
|---|---|
| `requestStatus` | Submitted, Approved, Rejected, Incomplete, Under Review (the filter's full enum; Approved/Rejected/Incomplete observed) |
| `requestType` | New, Renewal |
| `actionType` | New, Renewal, Supplement, Extension, Transfer, Adjustment, Date Adjustment |
| `actionStatus` | Approved, Declined, Incomplete — and, at the action level, **Submitted** and **Under Review** (probed 2026-08-23: 4 each among the newest 455 actions; an action awaiting review on an Approved request carries its own status) |
| action `states[]` | "Conflicts Verified", "Reviewers Assigned" |
| resource `type` | Requested / Recommended / Approved — the full award trail |
| `allocationDateType` | Requested, Approved |

**The co-PI question is settled.** `GET /v1/types/roles` for the NCAR process
returns **exactly three role types**:

```json
[{"roleTypeId": 13, "roleType": "PI",                 "displayRoleType": "Project Lead"},
 {"roleTypeId": 14, "roleType": "Allocation Manager", "displayRoleType": "Project Admin"},
 {"roleTypeId": 19, "roleType": "User",               "displayRoleType": "User"}]
```

**There is no co-PI role type in the NCAR process, so one can never appear on
the wire.** This closes the open risk carried in
`docs/xras/incoming/XRAS_REIMPLEMENTATION.md` and explains why zero co-PIs
exist across 101 role entries in 41 captured fixtures and all live sampling.
(The generic XRAS product vocabulary — visible in the apidoc example — does
define `CoPI` with roleTypeId 1; those generic ids are per-process, which is
why NCAR's are 13/14/19.) The hedging comments at
`src/sam/schemas/forms/xras.py:204-208`, the `!=` role match at
`src/sam/xras/roster.py:233`, and the stress scenarios' `Co-PI`/`CoPi`
variants can all be annotated with this answer (comment-only changes).

⚠️ A request still being drafted has `requestNumber: null` and
`requestStatus: "Incomplete"` — in-flight work is visible before it has a
number. Also note: inbound-wire role strings are `PI` / `Allocation Manager`
(spaced), while SAM's own outbound GET side uses `Pi` / `CoPi` /
`AllocationManager` (`src/webapp/api/xras/requests.py:33-37`), where the
`CoPi` branch is structurally always empty — now provably so.

---

## 4. Closed paths — do not re-probe

Every row was tested. Entries marked **[submit-only]** were originally tested
under `XA-CONTEXT: submit` before the Reports family was known; their
conclusions were re-checked under `report` where it mattered.

### 4.1 `review` / `admin` contexts are refused at the auth layer

`401` with `X-Runtime: 0.003` — rejected before any lookup. A property of
**the API key**, not of the header or the user. (Not needed: everything this
design requires works under `report`.)

### 4.2 `XA-PERMISSIONS` does nothing

Tested against a real Approved request the impersonated user had no role on,
in all four contexts, with each of: `Administrator`, `XRAS - Admin read
permission`, `XRAS - Review impersonator`, `Resource Provider User`,
`Allocations Process Manager`, and a comma-joined pair. **All 401.** The
permission strings are real (`GET /v1/permissions/:username` lists them); the
header is inert.

### 4.3 `/v1/requests` is strictly role-scoped; its path key is `requestId`

| Probe | Result |
|---|---|
| `GET /v1/requests/<requestNumber>` (as its own PI or anyone) | **401** — `requestNumber` is not a valid key on *this* route. Use `GET /v1/reports/request_numbers/<n>` instead (§ 3.2). |
| `?requestNumber=...`, `?opportunityId=...`, `?all=true` on `/v1/requests` | silently ignored — the `XA-USER`-scoped list is returned |
| `/v1/requests/number/<n>`, `/v1/requests/request/<n>` | 404 |
| Impersonating genuine XRAS administrators via `XA-USER` | only *their own* role-scoped requests; a foreign Approved request still 401s |

Scanning the numeric `requestId` space through `/v1/requests/:id` yields only
401s without a role. The reports family answers every *read* question, so the
route matters for one thing only: its `rules{}` block, the authoritative
legal-moves answer. Measured 2026-08-24 (read-only, PI identity, both contexts):

| XA-USER | context | `GET /v1/requests/<rid>` |
|---|---|---|
| `arcguest` | submit or report | 401 |
| the request's PI | submit or report | 200, `rules` present |

UCUB0089 (Approved New, as `kmussel`): `allowedActions: ["Transfer","Supplement"]`
with per-type `availableResourceIds`, every existing action `allowedOperations: []`.
UMIT0073 (Submitted Renewal, as `shuangw`): the action carries
`allowedOperations: ["Edit","Delete"]`, and `GET .../actions/<aid>/validate`
answers `{"validation": "successful", "errors": []}`. No key or config lever is
involved — the XA-USER is per call and the write paths already resolve the PI.
Wiring this as an offer overlay is **retired unless a named trigger appears**
(`docs/plans/XRAS_DATA_MODEL_UPLIFT.md` § B4); offers are derived from swept
state, which every modal re-checks live, and an illegal move fails loud.

### 4.4 People endpoints that do not exist

`GET /v1/people/<personId>`, `/v1/people/<email>`, `/v1/people/unreconciled`
and bare `/v1/people` all 404. `/v1/people/:username` is username-keyed —
**but `/v1/search/people?q=` exists** (§ 3.1) and matches on names, so people
are findable without knowing a username. Whether search matches *placeholder*
identities' display names is **untested** — a nice-to-know, not load-bearing
(placeholders arrive with usernames via both feeds).

### 4.5 Routes that do not exist (Rails 404) **[submit-only]**

`/v1/actions`, `/v1/allocations`, `/v1/awards`, `/v1/reviews`, `/v1/grants`,
`/v1/organizations`, `/v1/allocation_types`, `/v1/fields_of_science`,
`/v1/publications`, `/v1/users`, `/v1/roles` (as a GET), `/v1/requests/:id/actions`,
`/v1/people/:u/requests`, `/v2/*`. (`/v1/reports` *bare* also 404s — the
family lives one segment deeper, § 3.2. Several of these have proper
equivalents there: `/v1/reports/allocations`, `/v1/types/fos`, etc.)

### 4.6 `/v1/projects` is another service entirely

Returns a **Tomcat** 404 (everything else here is Rails) under both contexts
and with a valid `XA-USER`. It is documented in the generic apidoc (an
ACCESS-style per-user project/resource state view) but is not routed for our
process. Confirmed dead for NCAR; do not re-probe.

### 4.7 The write surface — why GET-only must be structural

The documented API is far more write-capable than the first survey knew, and
**our submit-context key holds at least some of it** (it is the credential the
ARC submission UI uses). Documented POSTs/PUTs/DELETEs include: create/delete
requests, submit/withdraw actions, add/remove roles and users
(`/v1/roles/<requestNumber>/Users`), **merge one person into another**
(`/v1/people/:u/merge/:new`), update resources, set ORCID data. None of that
may ever be reachable from SAM code. The client therefore has **no generic
verb method at all** — its only transport primitive is an internal `_get`
(§ 7.1), and a test pins that no post/put/patch/delete callable exists.

---

## 5. The placeholder finding

The XRAS-admin "Recent submissions" queue shows Approved requests whose Project
Lead is badged **"Unreconciled user"** — a researcher with no site account.
That is precisely the population the worklist is for.

**Every unreconciled person has an ARC placeholder username** of the shape
`<name>-user-<token>` (the admin UI shows it only on the person's own page,
not on the request summary). That username resolves fully:

```
GET /v1/people/<name>-user-<token>
{ "username": "...", "firstName": "...", "lastName": "...", "email": "...",
  "academicStatus": "Graduate Student", "residenceCountry": "United States",
  "organization": "...", "isReconciled": false, "orcid": null }
```

Three consequences:

1. **It is the account-creation detail sheet.** Name, email, organization,
   academic status, and **`residenceCountry`** — the last of which the inbound
   payload does **not** carry and account creation needs.
2. **`isReconciled` is the closure signal.** Re-poll the same username; when
   it flips, the worklist item closes itself with nobody updating SAM by hand.
3. The placeholder username is **already on the inbound wire**: fixture
   `tests/fixtures/xras/actions/new_ncar4214_ok.json` carries a role with
   `"username": "placeholder34-user-00034"` and `"isAccountToBeCreated": true`.

And via the reports family (§ 3.2), the same person object — placeholder
username, `isReconciled: false`, and all — arrives **inline in every request's
roster**, so the enumeration feed needs no separate person fetch at all.

### Two traps

**⚠️ `isAccountToBeCreated` is stale — never use it as the predicate.**
Live data shows an active, present SAM user carrying
`isAccountToBeCreated: true` on a PI role. XRAS sets the flag when the role is
created and never clears it. Surface it as a hint column only.

**⚠️ The predicate is SAM-side and has two classes.** Of 19 live roster
usernames checked against `users`, all 19 existed — but five had `active = 0`.
*Absent* and *inactive* block the handoff identically and need different
remedies (create vs reactivate). A predicate that only checks existence misses
a quarter of the real cases.

---

## 6. Join keys — both verified

**`resourceRepositoryKey` ↔ `xras_resource_repository_key_resource.resource_repository_key`.**
Reconciled live against the local SAM DB: **13/13 exact, zero unmapped, zero
dangling.** The API also exposes `productionBeginDate` / `productionEndDate`
per resource — and at probe time the two current flagship compute resources
carried a `productionEndDate` roughly three months in the past, which nothing
in SAM would notice. Whether that is XRAS staleness or a telegraphed
retirement is an open question worth asking (§ 13).

**`requestNumber` == `project.projcode`.** Verified both directions: an
Approved request's number exists as a SAM project; a **Rejected** request's
number (`NCAR3116`, the SAM author's own test request) does **not**, because
no project is ever created for a rejected request. This is exactly the
existence test `dispatch.select_service()` uses to tell a "New that is really
an update" from a genuine New — and it is what makes wholesale dropped-push
detection a set difference.

---

## 7. The design

### 7.0 Shape of the whole

Two feeds produce normalized roster records; one classifier turns them into
the worklist; one card renders it; one scheduled task runs the enumeration.

```
Feed A  xras_action_log.raw_payload  ──┐
        (inbound pushes, at push time) │   normalized          classify vs
                                       ├── RosterRecord ──►  users table ──► worklist rows
Feed B  GET /v1/reports/requests       │   (feed-neutral)    absent/inactive   │
        (enumeration, ahead of push)  ──┘                                      ▼
                                                              card (Feed A now; Feed B
        GET /v1/people/:username ── enrichment + isReconciled closure          via task detail,
                                    (Feed A only; Feed B carries it inline)    table later)
```

The feed-agnostic seam — a `RosterRecord` dataclass that both feeds construct
— is the one decision that keeps every downstream piece (classifier,
enrichment, card, CLI, eventual notes table) single-sourced.

### 7.1 The client — `src/sam/integration/xras_api/`

Follow `src/sam/integration/awards/` (the repo's outbound-client template —
same transport semantics, cache idiom, and test patterns), minus its
multi-provider registry (there is exactly one XRAS API; a registry would be
ceremony). Modules: `base.py` (exceptions), `config.py`, `client.py`,
`people.py` (cached wrappers), `cache.py`.

```python
class XrasApiClient:
    def __init__(self, *, api_key, base_url, allocations_process,
                 api_user='arcguest', timeout=10, max_retries=3): ...
    @classmethod
    def from_env(cls, env=None): ...      # raises XrasApiNotConfigured

    def get_person(self, username) -> Optional[dict]
    def get_resources(self) -> Optional[list]
    def get_request_by_number(self, request_number) -> Optional[list]
    def iter_requests(self, *, status='Approved', page_size=50,
                      max_pages=None) -> Iterator[dict]
    def search_people(self, q) -> Optional[list]
```

- **Transport semantics copied from `AwardHttpClient` verbatim**: one
  persistent `requests.Session`; explicit `timeout=` (10 s — this can run
  inside an htmx round-trip); 404 → `None`; other 4xx → raise immediately (a
  client error is deterministic, never retried); 5xx → warn + `2**attempt`
  backoff; non-JSON or exhausted retries → `XrasSourceUnavailable`. The
  three-outcome model (**found / not-found / unreachable**) is preserved all
  the way to CLI exit codes, as `AwardSourceUnavailable` does.
- `XrasApiNotConfigured(XrasSourceUnavailable)` — an unconfigured client
  degrades identically to an unreachable one at every call site.
- **GET-only, structural** (§ 4.7): `_get` is the only transport primitive.
- Headers: `XA-API-KEY` / `XA-ALLOCATIONS-PROCESS` / `XA-CONTEXT: report`
  (hardcoded, § 2.2) / `XA-USER` (static, from config). Every call logged at
  INFO. The `{message, result}` envelope unwrapped centrally.
- **Config** via `os.getenv`, read per call, never at import: `XRAS_API_KEY`,
  `XRAS_API_BASE`, `XRAS_ALLOCATIONS_PROCESS`, `XRAS_API_USER`, and the master
  lever **`XRAS_OUTGOING_ENABLED` (default off, fail-closed)** in the style of
  `XRAS_ACTIONS_CAPTURE_ONLY`. Two layers: `xras_api_configured()` is the
  cheap predicate callers branch on; `from_env()` raising is the backstop.
- **`cache.py`** — `BucketedTTLCache('xras_api', 'xras_api', ...)` with
  buckets `xras_people` (TTL 4 h — `isReconciled` is a closure signal and must
  not go stale) and `xras_resources` (TTL 1 d — a 13-row catalog). Registered
  by adding the module to `_BUCKETED_CACHE_MODULES`
  (`src/webapp/caching/__init__.py:48-53`) — that one line buys
  `sam-admin cache --refresh --category`, the Admin card row, `stats()` and
  `clear()`. Plus the hand-maintained `click.Choice` at
  `src/cli/cmds/admin.py:590-591`. Cache successes *and* definite negatives;
  never cache an `XrasSourceUnavailable` (the raise propagates before the
  store). Bucket names are global Redis prefixes — keep the `xras_` prefix.
- No Click / Flask / rich / kubernetes imports anywhere under `src/sam/` or
  `src/scheduling/` (AST-gated by existing tests).

### 7.2 The worklist query — `src/sam/queries/xras_accounts.py`

**Not exported from `sam/queries/__init__.py`** (that file imports its
submodules eagerly; the unexported precedent is `expiration_notices` /
`xras_notices`).

```python
WORKLIST_STATUSES = ('received', 'failed', 'manual')
PLACEHOLDER_USERNAME_RE = re.compile(r'^\S+-user-\S+$')

@dataclass(frozen=True)
class ActionRef:            # provenance — feed-neutral
    action_log_id: Optional[int]      # None for a Feed-B record
    request_number, action_type, status, received_time
    would_succeed: Optional[bool]     # validate_only verdict; None = not run
    reject_messages: Tuple[str, ...]  # verbatim, display-only — NEVER parsed

@dataclass(frozen=True)
class RosterRecord:         # one action's roster, already normalized
    ref: ActionRef
    usernames: Tuple[str, ...]
    roles_by_username: Mapping[str, Tuple[str, ...]]   # 'PI'/'Allocation Manager'/'User'
    account_flag: Mapping[str, bool]                   # isAccountToBeCreated — hint only
    person_by_username: Mapping[str, dict]             # inline person detail (Feed B)

def records_from_action_log(session, *, statuses=WORKLIST_STATUSES,
                            since=None, until=None) -> List[RosterRecord]   # Feed A
def records_from_report_requests(payloads) -> List[RosterRecord]            # Feed B
def classify_accounts(session, records) -> List[dict]                       # the core
def get_account_worklist(session, *, statuses=..., since=None, until=None)  # A ∘ classify
def enrich_worklist(rows, *, person_lookup=None, max_lookups=25) -> dict
```

- **Feed A** parses `raw_payload` with `json.loads` +
  `XrasActionSchema().load()` (`sam.schemas.forms` — the same path the webapp's
  `_parse_action` takes, with no webapp import), then extracts the roster with
  the structured helpers in `src/sam/xras/roster.py` (`roster_usernames`,
  `role_candidates`, `normalize_username`) — **never by parsing the
  byte-pinned error strings in `errors.py`**, which are a wire contract, not
  an interface. For actions in `WORKLIST_STATUSES` it additionally runs
  `dispatch_action(session, action, validate_only=True)`
  (`src/sam/xras/dispatch.py`; the validate path structurally cannot write —
  `management_transaction` only opens in `handlers/base.py` after the seam),
  catching `XrasActionRejected` to fill `would_succeed` / `reject_messages`.
  The verdict is *provenance*, not the classifier — it also catches non-account
  failures (mnemonic, resource key), which the card shows as "action would
  fail for other reasons".
- **Feed B** maps the outgoing wire shape — `roles[].person.username` +
  `roles[].roles[].role` — and carries the inline person dict, so Feed-B rows
  never need a `/v1/people` call.
- **Classification is a current-state check** against `users`: no row →
  `absent` (remedy: create); row failing `User.is_active` → `inactive`
  (remedy: reactivate); active rows are dropped. It never keys off the
  action's `status` — regime-proof across the capture-only → live-dispatch
  transition (§ 1). The placeholder flag comes from the username shape.
- **Row shape** (username-keyed, grouped across all actions naming the
  username): `username, classification, placeholder, roles (PI→AM→User
  union), is_account_to_be_created (hint only), actions (newest first),
  latest_action_log_id, first_seen, last_seen`, and — post-enrichment —
  `person` (PII subset) + `is_reconciled`. `latest_action_log_id` is
  deliberately the future notes-table FK target (§ 7.6).
- **Enrichment is separate and injected** (`person_lookup` defaults to the
  cached `xras_api.people.get_person`): the query layer stays fully
  offline-capable; one `XrasSourceUnavailable` marks the batch
  `unavailable=True` and leaves `person=None` rather than raising, so the card
  degrades to counts/usernames instead of 500ing. `max_lookups` bounds a
  cold-cache render.

### 7.3 The dashboard card — read-only this PR

A fragment beside the existing XRAS surfaces in
`src/webapp/dashboards/allocations/xras/card_routes.py` (which already serves
`/xras`, `/xras_fragment`, `/xras_pending_fragment`), template under
`templates/dashboards/allocations/partials/`.

- One route `/xras_accounts_fragment`: `@login_required` +
  `@require_permission(Permission.VIEW_XRAS)`, embedded in `xras.html` with a
  lazy `hx-get`.
- **PII is gated route-level**, following `xras_pending_fragment`
  (in `xras/card_routes.py`) and the Notifications precedent (counts at one
  level, rows naming people higher): person columns (name, email,
  organization, academicStatus, residenceCountry) are assembled **only** when
  the viewer holds `MANAGE_XRAS`; a `VIEW_XRAS` response carries username,
  classification, placeholder badge, roles, naming actions, the
  `is_reconciled` boolean, and counts — never the person dict.
- **The unconfigured state is first-class** (it is what staging shows): the
  Feed-A worklist renders fully without the API; a muted note marks person
  detail and reconciliation as unavailable. The **empty state is designed,
  not broken** — production is 0 rows until ACCESS repoints.
- Columns: username · why (absent/inactive + placeholder badge) · roles ·
  which request/projcode and action needs them · `isReconciled` · person
  detail (permission-gated). Classification facet chips with self-exclusion
  (the house pattern). `sam.fmt` filters for all formatting. Route-map parity
  snapshot regenerated in the same commit as the route.
- No dead UI: notes and dismissal ship with the table (§ 7.6), not before.

### 7.4 The `xras_sweep` scheduled task — enumerate-and-diff, shipped disabled

`src/scheduling/tasks/xras_sweep.py`, `Daily(hour=3, minute=30,
tz='America/Denver')`, `needs=('sam',)`, `expected_runtime=timedelta(minutes=20)`
(lease `3 × 20 min = 3600 s` > the CronJob's `activeDeadlineSeconds: 3000` —
the drift test pattern from `test_task_xras_notices.py` applies). Template:
`src/scheduling/tasks/xras_notices.py` — deferred `sam.*` imports, pure env
readers, `to_local_naive(ctx.occurrence, ...)`.

Per run (persists nothing but `TaskResult.detail` — there is no table yet):

1. `xras_api_configured()` false → a visible **skip** detail, not a raise
   (the ledger row is the record; this is the shipped state).
2. **Enumerate**: `iter_requests(status='Approved', page_size=200,
   max_pages=...)` — page cap from `SAM_TASKS_XRAS_SWEEP_MAX_PAGES` (default
   25 → 5,000 requests; junk/zero refused, per the `xras_email_max` idiom),
   with a client-side recency filter on action dates to bound work.
3. **Dropped/pending-push detection**: diff `requestNumber` against
   `project.projcode` → `pending_push` count + capped list in detail.
4. **Classify**: `records_from_report_requests` → `classify_accounts` →
   absent/inactive/placeholder counts + capped username lists in detail.
5. **Warm the closure signal**: fresh `/v1/people` fetch for each current
   Feed-A worklist username → `closures` count (and a warm cache for the
   card's morning renders).

`detail` always carries: pages, requests_seen, pending_push, worklist counts,
placeholders, people_refreshed, closures, budget_exhausted,
unavailable_errors — "0 findings, succeeded" must be distinguishable from "did
not look".

**What was deliberately dropped from an earlier draft of this design**: the
per-identity `/v1/requests` polling sweep (measured at 0.84 s × 1,518 seed
identities nightly) and its transitive roster crawl. The reports enumeration
(§ 3.2) reaches strictly more people — including wholly-new solo PIs the crawl
could never connect to — in a handful of paginated calls. Do not resurrect the
crawl.

⚠️ **Ship-disabled mechanics** (fail-open trap): the task's name goes into
`SAM_TASKS_DISABLED` in `helm/values.yaml` **in the same commit** that
registers it, plus the values.yaml-grep test (`test_it_ships_switched_off`
pattern), the `docs/README-k8s.md` mentions (3 places), and the module added
to `tests/unit/test_task_ledger.py`'s subprocess import matrix.

### 7.5 CLI — `sam-admin xras` grows three things

Extending the existing mode-dispatch in `src/cli/xras/commands.py`
(builders/display split per the awards pattern; exit codes 0/1/2/130):

- `--accounts [--enrich]` — the worklist as a table / JSON envelope
  (`kind: xras_accounts`). `--enrich` requires configuration (else exit 2).
  An empty worklist exits 0 — a successful report, not a miss.
- `--person USERNAME` — direct `/v1/people` probe: found → 0, 404 → 1,
  unavailable/unconfigured → 2 (`kind: xras_person`).
- `--validate-mapping` becomes two-sided automatically (§ 8.1).

### 7.6 Schema — none now, one table next

**The worklist itself needs no storage.** It derives from
`xras_action_log.raw_payload`, `users`, the pre-flight verdict, and
`/v1/people` (cached). This follows `XrasActivationEvent`'s own rule: *state
is DERIVED, never stored*.

**Operator notes and dismissals do need storage, and `XrasActivationEvent`
cannot carry them** — its `project_id` is NOT NULL and project-scoped, while
the account worklist is **username-keyed** and for a New request *the project
does not exist yet*. The follow-up PR adds `xras_account_event`, mirroring the
existing table deliberately: append-only; `username varchar(35)` in place of
`project_id`; `event_type` from a small tuple; `comment`; `created_by
varchar(35)`; `xras_action_log_id` as provenance only. Derive current state by
timestamp comparison against the latest action naming that username — **copy
the current action-keyed supersedes idiom in
`src/sam/queries/xras_activation.py` (`_activation_state`, the `supersedes`
comparison), not the older project-keyed rule quoted in the
`XrasActivationEvent` docstring** (the card was re-keyed since that was
written).

DDL context: `docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md` records the
2026-08-10 `CREATE/ALTER/INDEX/REFERENCES` grant; the applied-DDL precedent is
`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2. There is deliberately no
`DROP` — get the DDL right the first time. SAM has no migrations (Alembic
covers only `system_status`): table by hand, ORM to match.

---

## 8. The cheap win riding along (and the one deferred)

### 8.1 Two-sided `--validate-mapping` — in this PR

`audit_resource_mapping` (`src/sam/queries/xras_actions.py:652`, body
:678-713) reads two local tables and nothing else; its own docstring concedes
it has no list of the keys XRAS will actually send. So the failure that
genuinely breaks an award — XRAS sends a `resourceRepositoryKey` SAM has no
row for — is invisible to the command and surfaces only at runtime as
`No resource found in SAM corresponding to key %s` (`errors.py:201`, raised
from `handlers/_fields.py:148`). `GET /v1/resources` is that missing list.

Design: `audit_resource_mapping(session, *, xras_keys=None)` — an optional
injected iterable, so the function keeps zero network knowledge; new report
keys `xras_only_keys` and `live_checked`. The CLI auto-detects: it fetches the
live list iff `xras_api_configured()`, and degrades to the one-sided report
(byte-compatible with today) when unconfigured or unavailable. Exit 1 when
`dangling_keys` **or** `xras_only_keys` is non-empty. Self-verifying: must
report 13/13 against today's catalog (§ 6).

### 8.2 `opportunityId` → allocation type — ✅ **BUILT 2026-08-20**

**Superseded by [`XRAS_OPPORTUNITY_ALLOCATION_TYPE.md`](XRAS_OPPORTUNITY_ALLOCATION_TYPE.md)**,
which carries the design, the corrections the build found, and — as of
2026-08-20 — the `--validate-opportunities` CLI, which is no longer deferred
(§ 8.6 there).

The deferral reasoning below is kept because it is what the decision was
reversed *against*: the criterion was "assess after the first triage week under
live dispatch", and the reversal turns on that criterion being unable to produce
evidence — triage week is 100% University traffic, so it reports zero collisions
right up until the day WNA onboards.

Assess **after the first triage week under live dispatch**. The case is
recorded so it isn't lost: SAM derives allocation type with an 11-strategy
free-text ladder (`src/sam/xras/extractors.py`) of which only 5 strategies are
exercised by all 41 production payloads; `opportunityId` is on the inbound
wire in 41/41 fixtures and read by nothing; `/v1/opportunities/:id` resolves
historic and Terminating opportunities, so an unknown id can be fetched and
cached rather than hardcoded. Constraints when built: key on `opportunityId`
(never the wire `allocationType` string — different vocabulary from SAM's, and
non-unique); keep the two-column `(panel, allocation_type)` join; keep the
ladder as fallback. Note the strategy chain is pure/sessionless by design — an
id lookup needing cache or DB belongs in `resolve_allocation_type` or a
session-taking pre-step, not inside a strategy.

---

## 9. What no longer needs asking

An earlier draft of this document had a "Phase 2" that asked XRAS for an
enumeration credential. **That ask is moot** — `XA-CONTEXT: report` on the
existing key already enumerates the process (§ 3.2). Of the four things Phase
2 was for: mirroring the Approved queue ✅ (`reports/requests`), dropped-push
detection ✅ (§ 7.4), an unreconciled-people view ✅ (inline in every roster,
plus `search/people`), review/panel state — `states[]` is visible per action;
the review queue itself remains admin-only and remains not-needed.

The only remaining external conversations with XRAS are operational, § 13.

---

## 10. Verification

- **Unit tests with canned fixtures; no live calls in CI.** Mirror
  `tests/unit/test_award_providers.py`: payloads as module dict constants with
  invented identities, transport tests via a mocked `session.request` +
  no-op sleep, the three-outcome model, an "outage is never memoised" case,
  and the cache-reset autouse fixture (`reset_for_tests()` +
  `delenv CACHE_REDIS_URL`).
- **Pre-flight correctness**: the worklist reproduces the expected
  classifications against `tests/fixtures/xras/actions/` — the
  `placeholder34-user-00034` entry classified **absent** (and
  placeholder-flagged), a factory-made inactive user classified **inactive**.
- **Predicate regression**: `isAccountToBeCreated: true` on an existing,
  active SAM user must **not** appear on the worklist (the § 5 trap).
- **Feed-agnostic proof**: a canned `reports/requests` payload through
  `records_from_report_requests` reaches the same classifier and classifies
  identically.
- **Two-sided mapping audit** self-verifies 13/13 with injected keys; a
  synthetic extra key is reported and flips the exit code.
- **Task tests**: the `values.yaml` ships-switched-off grep, the
  lease-vs-`activeDeadlineSeconds` drift test, registration, page-cap reader
  matrix, skip-when-unconfigured, enumerate+diff+classify with a stub client.
- **Route-map parity**: `ROUTE_MAP_REGEN=1`, snapshot committed in the same
  commit as the new route.
- **Helm**: per-manifest assertions (`-s templates/cronjob-tasks.yaml`) for
  the new `tasks.env` keys and the secret injection — a whole-render grep
  passes on the Deployment's copy and proves nothing.
- **Live opt-in probe script** `scripts/xras/probe_outgoing.py`, gated on
  `XRAS_API_KEY` — **skips** (exit 0 + message) when absent, mirroring
  `utils/parity/`'s `_resolve_xras_credentials()` behavior.
- **End-to-end**: `docker compose up webdev --watch`, seed via
  `scripts/xras/seed_dev_actions.py`, confirm the card lists a seeded
  placeholder identity as *absent*, and that the person columns appear only
  for a `MANAGE_XRAS` viewer.

### The Tier-III test bed — `~/xras_payloads_raw/`

**Only unscrubbed payloads can validate the core predicate, and this is
structural.** The anonymizer rewrites every username to `user_<hex>` (or
`placeholder<NN>-user-<NNNNN>`), so for the in-tree fixtures "no `users` row"
is trivially true for **all** of them and proves nothing. Distinguishing a
genuinely-unknown user from an artifact of scrubbing requires real usernames
posted against the cloned development database.

That corpus exists outside the tree, deliberately uncommitted: **41 payloads**
(8 top-level plus 33 under `incoming_2026-08-11/`), spanning `New` ×13,
`Extension` ×7, `Supplement` ×7, `Date Adjustment` ×4, `Adjustment` ×2 — with
6 role entries carrying `isAccountToBeCreated=true` and ~4 distinct ARC
placeholder identities.

Procedure: POST a payload naming an unknown user to the local
`/api/xras/v1/actions` (`scripts/xras/seed_dev_actions.py` does the posting,
reading `SAM_XRAS_USER` / `SAM_XRAS_PASS`), then confirm the worklist
classifies that username **absent** rather than **inactive**, and that
`/v1/people` enriches it.

⚠️ **These payloads are unscrubbed and name real people.** They stay outside
the tree. Nothing derived from them — usernames, emails, or counts tied to
identities — may enter a commit, a test fixture, or a docstring. Any
regression test that must *persist* is built from the scrubbed corpus; the raw
run is a one-time manual verification recorded only as pass/fail.

---

## 11. Safety and secrets

- **Read-only, GET-only**, enforced structurally in the client (§ 4.7, § 7.1)
  — not by convention. The same key can create requests, merge people, and
  modify roles.
- **Step 0 — the key moves into OpenBao before anything deploys.** Follow the
  `jupyterhubCredentials` token pattern exactly: store at `csg/xras-api-key`,
  property `token`, readable by the `csg-ro` SecretStore; chart side is a
  `webapp.xrasApiCredentials` block (same fields as `jupyterhubCredentials`,
  `helm/values.yaml:378-383`) rendering an ExternalSecret and injecting
  `XRAS_API_KEY` via `secretKeyRef` into **both** `deployment.yaml` and
  `cronjob-tasks.yaml` (they share no env anchor — this is the same
  cross-referencing trap `NOTIFY_*` hit). After cutover, retire the cleartext
  copies in the operator's home directory and on `crlogin`; a key **rotation**
  request to XRAS is worth raising, since the key has lived in cleartext.
- **The key never enters this repo** — not in `.env` files with values, not in
  fixtures, not in test constants, not in probe output pasted into docs.
- **Fail-closed lever**: `XRAS_OUTGOING_ENABLED` defaults off everywhere;
  `helm/values.yaml` pins it `"0"` visibly in both env maps. With the lever
  off, the card renders its unconfigured state, the sweep records a skip, and
  the CLI degrades — nothing raises for lack of a key.
- **Do not confuse `XRAS_API_KEY` with `SAM_XRAS_USER` / `SAM_XRAS_PASS`** —
  the latter are credentials for calling *SAM* (`.env.example:107-108`), a
  production **write** credential in the inbound direction.
- **The worklist names real people, emails, organizations and
  `residenceCountry`.** PII is gated route-level on `MANAGE_XRAS` (§ 7.3);
  a `VIEW_XRAS` response never carries it.
- **The local dev database may hold unobfuscated production data.** Nothing
  derived from a local query goes into a commit.

---

## 12. Implementation plan — one PR vs `staging`

**Step 0 (operator, pre-deploy prerequisite):** key into OpenBao per § 11.
The PR does not block on it, but the chart's `xrasApiCredentials.enabled: true`
must not reach the cirrus deploy before the secret exists.

Commit series, each self-testing:

| # | Commit | Contents |
|---|---|---|
| 1 | `sam/integration/xras_api: GET-only client for the XRAS Allocations API` | § 7.1 package + tests + `probe_outgoing.py`; `_BUCKETED_CACHE_MODULES` + cache-category `click.Choice` registrations |
| 2 | `sam/queries/xras_accounts: derive the account-creation worklist` | § 7.2 query module + both feed builders + classifier + enrichment, tests incl. the predicate regression |
| 3 | `allocations dashboard: account-creation worklist card (read-only)` | § 7.3 route + template + PII gating tests + route-map snapshot regen |
| 4 | `sam-admin xras: accounts worklist, person lookup, two-sided --validate-mapping` | § 7.5 CLI modes + § 8.1 audit change + tests |
| 5 | `scheduling: xras_sweep enumerate-and-diff nightly (ships disabled)` | § 7.4 task + registration + `SAM_TASKS_DISABLED` + README-k8s + tests (same commit — fail-open trap) |
| 6 | `env/helm: XRAS outgoing API configuration, fail-closed, key via ExternalSecret` | `.env.example` block (JUPYTERHUB_* style); values.yaml env in both maps; `xrasApiCredentials` + ExternalSecret + secretKeyRef in both manifests; per-manifest render assertions |
| 7 | `docs: file XRAS outgoing under docs/xras/outgoing/` | `git mv` this document to `docs/xras/outgoing/`, status → implemented-as-built; co-PI annotation comments (`sam/schemas/forms/xras.py`, `sam/xras/roster.py`, `XRAS_REIMPLEMENTATION.md` open-risks); `docs/xras/README.md` index; deferred register (notes table, § 8.2, sweep enablement, key rotation) |

PR notes: `--base staging`; the card renders legitimately **empty in
production until ACCESS repoints** — say so in the description; no skip-ci
tokens anywhere in title/body/commits.

Verification commands (the operator runs pytest):

```
pytest tests/unit/test_xras_api_client.py tests/unit/test_xras_accounts_query.py \
       tests/unit/test_xras_accounts_card.py tests/unit/test_task_xras_sweep.py
pytest tests/unit/test_task_ledger.py tests/unit/test_admin_tasks_cli.py
ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py
pytest tests/unit
bash helm/tests/test-cronjob-render.sh
helm template samuel helm -f helm/values.yaml -s templates/cronjob-tasks.yaml | grep XRAS
helm template samuel helm -f helm/values.yaml -s templates/deployment.yaml   | grep XRAS
helm template samuel helm -f helm/values.yaml -s templates/external_secret.yaml
XRAS_OUTGOING_ENABLED=1 XRAS_API_KEY=… python scripts/xras/probe_outgoing.py
```

---

## 13. Open questions

1. **Are the two flagship compute resources really production-ended?** The API
   reports a `productionEndDate` months in the past for both (§ 6). Confirm
   with XRAS whether that is stale data or a telegraphed retirement.
2. **Key rotation** (§ 11) — worth requesting once OpenBao holds it.
3. **Sweep tuning** — page cap and recency window are defaults to revisit with
   real post-cutover volumes; the enumeration is cheap enough that this is a
   dial, not a design question.
4. **Does `search/people` match placeholder identities?** Untested,
   nice-to-know only (§ 4.4).
5. ~~Co-PI role type~~ — settled (§ 3.4). ~~Enumeration credential~~ — moot
   (§ 9). ~~Read-only card vs notes table~~ — decided (§ 1.1).

---

## 14. Deferred register — what this PR did NOT build

Each was a deliberate decision, with the reason recorded so it can be revisited
on evidence rather than re-litigated from scratch.

| Deferred | Why, and what would change it |
|---|---|
| **`xras_account_event`** (§ 7.6) — operator notes and dismissal | Needs a table, and `XrasActivationEvent` cannot carry it: `project_id` is NOT NULL and project-scoped, while this worklist is username-keyed and a New request has no project yet. Shipping the buttons first would be dead UI. **Immediate follow-up PR.** |
| **`opportunityId` → allocation type** (§ 8.2) | Assess after the first triage week under live dispatch. The 11-strategy ladder works; only 5 strategies are exercised by 41 payloads, so the case for replacing it is real but unmeasured. |
| **Enabling `xras_sweep`** | Ships named in `SAM_TASKS_DISABLED`. Enable after the card has been reviewed against real post-cutover data — the task's value is proportional to how full `xras_action_log` is, and it is empty until ACCESS repoints. |
| **`XRAS_OUTGOING_ENABLED: "1"`** | Ships `"0"`. Flipping it turns on person enrichment in the card and the two-sided half of `--validate-mapping`. Independent of the sweep's own switch. |
| **Key rotation** | The key lived in cleartext in a home directory and on `crlogin` hosts before OpenBao. Worth requesting from XRAS now that `csg/xras-api-key` is authoritative; retire the cleartext copies either way. |
| **`search/people` against placeholders** (§ 13.4) | Untested, nice-to-know. Placeholders arrive with usernames on both feeds, so nothing depends on it. |

---

## 15. Reference — related documents

| Document | Why it matters here |
|---|---|
| `docs/xras/README.md` | Index; the `incoming/` lifecycle convention this document's `outgoing/` destination mirrors |
| `docs/xras/incoming/XRAS_REIMPLEMENTATION.md` | The inbound wire contract, action semantics, the allocation-type extractor, and the open-risks list (co-PI now answerable) |
| `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` | § 2 has the applied DDL sequence — the precedent for the follow-up table (§ 7.6) |
| `docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md` | The 2026-08-10 DDL grant that makes the follow-up table cheap |
| `docs/xras/incoming/implemented/XRAS_SPRINT_B.md` | The operator surface and activation worklist — the closest existing analogue to the proposed card |
| `src/sam/integration/awards/` | The outbound-client template: client + cache, transport semantics, test patterns |
| `src/sam/xras/dispatch.py` | `select_service()` and the `validate_only` seam Feed A is built on |
| `src/sam/xras/roster.py` | The structured roster helpers the classifier uses instead of parsing error strings |
| `src/sam/queries/xras_activation.py` | The action-keyed derived-state idiom the follow-up notes table will copy |
| `src/scheduling/tasks/xras_notices.py` | The shipped-disabled task recipe `xras_sweep` follows |

# XRAS "outgoing" queries — an account-creation worklist

**Status:** research complete, design drafted, **not implemented**.
**Branch:** `probing_xras`. **Probed:** 2026-08-19 against production
`https://api.xras.org`, cross-checked against the XRAS-admin web app.

This is a handoff document. It is written so an implementation session that has
never seen the original conversation can start cold. It records what the XRAS
API *can* and *cannot* do — including every dead end, with the command that
closed it, so nobody spends a second session re-probing a path that is already
known to fail.

> **Direction of travel.** Everything in `docs/xras/incoming/` is XRAS → SAM
> (they push actions, they pull our GETs). This document is the opposite
> direction: **SAM calling out to XRAS**. There is no such code in the repo
> today — `api.xras.org` is zero hits.

---

## 1. Why

**Goal: a dashboard listing the user accounts that must be created (or
reactivated) in SAM before an XRAS handoff can succeed** — who, why, with notes.
Account creation is a manual process. This complements the XRAS action-log card
rather than replacing it.

This targets the largest known failure mode. `src/sam/xras/handlers/new.py:24-27`
records the measured causes of the legacy 70% failure rate:

> an unresolvable mnemonic (24%, a frozen `user_organization` table),
> **unreconciled ARC placeholder identities (55%)**, and resource keys with no
> mapping row.

`scripts/xras/scrub_payload.py:32-34` independently states that
`<name>-user-<token>` identities "are 55% of production failures". So the
worklist addresses, by the repo's own measurements, the single biggest cause of
XRAS handoff failure.

**Timing context.** PR #457 lands the capture-only receive path; ACCESS is
repointed at SAM shortly after, at which point `xras_action_log` begins filling
(it is at 0 rows until then). Capture-only is an *advantage* here: it means
every action can be pre-flighted **before** it is ever processed.

---

## 2. Which API — read this first

Two different services are easy to confuse.

| | |
|---|---|
| **XRAS Rules Service** — `xras-rules-service-demo.xsede.org/apidoc/` | A separate, mostly-POST engine (notifications, validate, required_documents, clone rules). Paths are `/api/v1/...`. **Our key does not open it.** Not useful here. |
| **XRAS Allocations API** — `https://api.xras.org/v1/...` | ✅ **This is the one.** Live docs: `api.xras.org/api/overview`, `api.xras.org/api/request_headers`, `api.xras.org/api/data_models` (that last renders empty). `api.xras.org/apidoc` 404s. |

The two share endpoint *names* (`opportunities`, `requests`), which is exactly
why they get confused. Note the path prefix differs: `/api/v1/` for the rules
service, `/v1/` for the allocations API. `api.xras.org/api/v1/...` 404s.

### Authentication

```
XA-API-KEY:             <key>          # see § 10 — not in this repo
XA-ALLOCATIONS-PROCESS: NCAR
XA-CONTEXT:             submit | report        ✅ 200
                        review | admin         ❌ 401 (key not provisioned)
XA-USER:                <username>     # scopes every per-user response
XA-PERMISSIONS:         <optional>     # accepted; changes nothing (see § 4)
```

Omitting `XA-CONTEXT` gives `400 {"message":"XA-CONTEXT header missing"}`.
The documented context vocabulary is `submit`, `report`, `review`, `admin`;
our key holds the first two. `submit` and `report` expose an **identical**
surface — `report` unlocks nothing extra.

Server is Rails behind Apache/Passenger. A Rails HTML 404 page means "no such
route"; a JSON `{"message":..., "result":null}` means the route exists and the
request was refused or found nothing. That distinction is useful when probing.

---

## 3. The readable surface — seven endpoints

| Endpoint | Scope | Returns |
|---|---|---|
| `GET /v1/people/:username` | **global directory** | firstName, middleName, lastName, email, phone, organization, academicStatus, **residenceCountry**, **isReconciled**, orcid, hasOrcidToken |
| `GET /v1/requests` | **`XA-USER`-scoped** | every request that user is PI / CoPI / Allocation Manager on, in full |
| `GET /v1/requests/:requestId` | `XA-USER`-scoped | one request; **401** if the user has no role on it |
| `GET /v1/opportunities` | global | currently-open opportunities, full detail |
| `GET /v1/opportunities/:id` | global | one opportunity — **including historic/Terminating ones** |
| `GET /v1/opportunities/list/:id,:id,...` | global | batch form of the above |
| `GET /v1/resources` · `/v1/resources/:id` | global | 13 resources, **including `resourceRepositoryKey`** |
| `GET /v1/panels` · `/v1/panels/:id` | global | 5 panels **with member rosters** |

**Unknown username** gives a clean `404 {"message":"username=X not found"}`.

### `/v1/requests` payload shape

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

### Vocabularies observed in live NCAR data

| Field | Values seen |
|---|---|
| `requestStatus` | Approved, Rejected, Incomplete |
| `requestType` | New, Renewal |
| `actionType` | New, Renewal, Supplement, Extension, Transfer, Adjustment, Date Adjustment |
| `actionStatus` | Approved, Declined, Incomplete |
| action `states[]` | "Conflicts Verified", "Reviewers Assigned" |
| resource `type` | **Requested / Recommended / Approved** — the full award trail |
| `allocationDateType` | Requested, Approved |
| **`roleTypeId`** | **13 = PI, 14 = Allocation Manager, 19 = User** |

⚠️ A request still being drafted has `requestNumber: null` and
`requestStatus: "Incomplete"` — in-flight work is visible before it has a number.

### Role types — and the CoPI question

`docs/xras/incoming/XRAS_REIMPLEMENTATION.md` carries "co-PI roleType still
unknown" as an open risk, and `src/sam/schemas/forms/xras.py:204-208` explains
the field is not enum-validated because no co-PI has ever appeared in a sampled
payload. Stress scenarios hedge across `Co-PI` / `CoPi` / `Co-Investigator`.

Two findings:

- **The outgoing API carries a numeric `roleTypeId` (13/14/19); the inbound wire
  carries none at all** — only a `roleType` *string*, matched with a plain `!=`
  at `src/sam/xras/roster.py:233`. `grep -rn roleTypeId src tests` returns
  nothing.
- **Still zero co-PIs** — none across 101 role entries in the 41 captured
  fixtures, and none in any live request sampled. Consistent with NCAR's process
  using PI / Allocation Manager / User only, which is exactly the roster SAM
  models.

Note the two vocabularies must not be conflated: inbound uses `PI` /
`Allocation Manager` (spaced), while SAM's own outbound GET side uses `Pi` /
`CoPi` / `AllocationManager` (`src/webapp/api/xras/requests.py:33-37`), where the
`CoPi` branch is structurally always empty (`src/sam/queries/xras_access.py:260-276`).

---

## 4. Negative results — closed paths, do not re-probe

Every row below was tested. The point of this section is to stop a future
session rediscovering them.

### 4.1 There is no enumeration

No endpoint lists requests, people, or actions across the process. `/v1/requests`
is always and only `XA-USER`-scoped.

### 4.2 `XA-PERMISSIONS` does nothing

Tested against a real Approved request the impersonated user had no role on, in
all four contexts, with each of: `Administrator`, `XRAS - Admin read permission`,
`XRAS - Review impersonator`, `Resource Provider User`,
`Allocations Process Manager`, and a comma-joined pair. **All 401.**
(The permission strings are real — they are listed on a person's admin page.)

### 4.3 `review` / `admin` contexts are refused at the auth layer

`401` with `X-Runtime: 0.003` — rejected before any lookup. This is a property
of **the API key**, not of the header or the user.

### 4.4 Probing by project code / request number does not work

This is the most tempting idea and it fails for three independent reasons:

| Probe | Result |
|---|---|
| `GET /v1/requests/<requestNumber>` **as its own PI** | **401** — `requestNumber` is not a valid path key at all. The numeric `GET /v1/requests/<requestId>` for the same request returns 200. |
| `GET /v1/requests/<requestNumber>` as a non-member | 401 |
| `?requestNumber=...`, `?opportunityId=...`, `?all=true` | **silently ignored** — the unfiltered list is returned |
| `/v1/requests/number/<n>`, `/v1/requests/request/<n>` | 404 |

Scanning the **numeric** `requestId` space is equally futile: ids are dense and
guessable, but reading one still requires a role, so a scan yields only 401s.

### 4.5 Admin identities gain no breadth

Impersonating genuine XRAS administrators (members of the admin panel) returns
only *their own* role-scoped requests — counts of 1, 2 and 6 for the three
tested. Adding `XA-PERMISSIONS: Administrator` changes nothing, and a foreign
Approved request still 401s for them.

**This is an important design input:** the admin web app's queue view
demonstrably does **not** come through this API. See § 8 for how that reshapes
the ask.

### 4.6 Unreconciled people cannot be addressed by id or email

`GET /v1/people/<personId>`, `/v1/people/<email>`, `/v1/people/unreconciled`
and `/v1/people` all 404. `/v1/people/:username` is username-keyed, full stop.

**But see § 5 — this does not mean unreconciled people are unreachable.**

### 4.7 Endpoints that do not exist

`/v1/actions`, `/v1/allocations`, `/v1/awards`, `/v1/reviews`, `/v1/reports`,
`/v1/grants`, `/v1/organizations`, `/v1/allocation_types`,
`/v1/fields_of_science`, `/v1/publications`, `/v1/users`, `/v1/roles`,
`/v1/requests/:id/actions`, `/v1/people/:u/requests`, `/v2/*` — all 404.

Curiosity note: `/v1/projects` returns a **Tomcat** 404 rather than the Rails
one, so some other service is routed at that path. It is not ours and did not
respond usefully to anything tried.

---

## 5. The finding that makes the worklist possible

The XRAS-admin "Recent submissions" queue shows Approved requests whose Project
Lead is badged **"Unreconciled user"** — a researcher with no site account. That
is precisely the population the worklist is for, and at first it looked
unreachable (§ 4.6).

**It is reachable.** Every unreconciled person still has an **ARC placeholder
username** of the shape `<name>-user-<token>`. The admin UI shows it only on the
person's own page, not on the request summary — which is what made it look
absent. That username resolves fully:

```
GET /v1/people/<name>-user-<token>
{ "username": "...", "firstName": "...", "lastName": "...", "email": "...",
  "academicStatus": "Graduate Student", "residenceCountry": "United States",
  "organization": "...", "isReconciled": false, "orcid": null }
```

Three consequences, each load-bearing:

1. **It is the account-creation detail sheet.** Name, email, organization,
   academic status, and **`residenceCountry`** — the last of which the inbound
   payload does **not** carry and account creation needs.
2. **`isReconciled` is the closure signal.** Re-poll the same username; when it
   flips, the item closes itself with nobody updating SAM by hand.
3. **Impersonating the placeholder reads the request in full** — so approved
   amounts and dates can be verified even for a handoff SAM never received.

And the placeholder username is **already on the inbound wire**: fixture
`tests/fixtures/xras/actions/new_ncar4214_ok.json` carries a role with
`"username": "placeholder34-user-00034"` and `"isAccountToBeCreated": true`.
The push names these people; the API describes them.

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
carried a `productionEndDate` roughly three months in the past, which nothing in
SAM would notice. Whether that is XRAS staleness or a telegraphed retirement is
an open question worth asking.

**`requestNumber` == `project.projcode`.** Verified both directions: an Approved
request's number exists as a SAM project; a **Rejected** request's number
(`NCAR3116`, which is the SAM author's own test request) does **not**, because no
project is ever created for a rejected request. This is exactly the existence
test `dispatch.select_service()` uses to tell a "New that is really an update"
from a genuine New.

---

## 7. Phase 1 — buildable now, no new key

### 7.1 The central question, answered

> *Can we see Approved requests before XRAS pushes them?*

**Yes — for any identity SAM already knows.** The ceiling is **discovery**, not
**reading**. `XA-USER` may be set to any username we know, so any request that
user has a role on is fully readable.

Verified end-to-end: the admin queue showed an Approved Extension on an existing
SAM project. Polling that project's known lead through `/v1/requests` returned
the same action — its `actionId`, `entryDate`, approved end date and full roster
— with no push and no admin key. All six extension-type Approved rows in the
sampled queue mapped to SAM projects with known leads.

### 7.2 The three populations

Checking the Approved queue's project leads against SAM:

| Class | Visible in Phase 1? | Remedy |
|---|---|---|
| **Known + active** in SAM | ✅ by polling | usually none — may just need adding to a project |
| **Known but `active = 0`** | ✅ by polling | **reactivate** |
| **Absent entirely** — ARC placeholder identity | ❌ push-only until Phase 2 | **create** |

Roughly a quarter of the Approved queue is Extensions/Transfers on existing
projects (fully visible today). The rest are New requests spread across all
three classes. **The third class is the residual gap — and it is the
55%-of-failures population.** Phase 1 catches it *at push time, before
processing*; Phase 2 (§ 8) would catch it *before the push*.

### 7.3 Feed A — pre-flight over captured actions

**Reuse what exists; build nothing.**
`dispatch_action(session, action, validate_only=True)`
(`src/sam/xras/dispatch.py:222-263`) runs the handler's assemble-and-check half
and returns **before `management_transaction` opens**.
`src/webapp/api/xras/recheck.py:195-202` already drives it.

Because production runs **capture-only**, running this across captured actions
pre-flights every action *before* it is ever processed — literally "detect users
that need creating before the task can succeed".

It yields exactly five error strings from `src/sam/xras/errors.py`, and those
five **are** the worklist, with the remedy already distinguished:

| `errors.py` | Meaning | Remedy |
|---|---|---|
| `pi_not_in_database` :143 | PI has no `users` row | create |
| `pi_not_active` :149 | PI row inactive | reactivate |
| `manager_not_in_database` :154 | Allocation Manager absent | create |
| `manager_not_active` :160 | Allocation Manager inactive | reactivate |
| `username_missing` :185 / `username_inactive` :190 | roster member | create / reactivate |

### 7.4 Feed B — known-identity polling, with a roster crawl

A scheduled sweep calling `GET /v1/requests` per known identity, recording
Approved and in-flight actions. Two things it buys beyond Feed A:

- **Pre-push visibility** for extensions/transfers on existing projects and for
  New requests from people SAM already knows.
- **Discovery of unknown people through known ones.** A known PI's roster names
  their collaborators, *including placeholder usernames SAM has never seen*.

**Roster crawl.** Every newly-seen username is itself pollable, so the sweep
should be transitive: poll seeds → harvest usernames from their rosters → poll
the new ones → repeat to a fixed point, bounded by a depth cap and a per-run
budget. This widens coverage well past the seed set with no enumeration.

Its limit, stated plainly: the crawl reaches people who **share a request with
someone we know**. A wholly-new PI submitting a solo New request connects to
nothing SAM knows, so no crawl reaches them. Push-only until Phase 2.

**Measured cost.** `GET /v1/requests` ≈ **0.84 s, ~22 KB** per identity
(3 consecutive runs: 0.844 / 0.826 / 0.837 s).

| Seed tier | Count (local snapshot) | Serial | ~8-way |
|---|---:|---:|---:|
| Active project leads + admins | 1,518 | ~21 min | ~3 min |
| All active, unlocked users | 6,310 | ~88 min | ~11 min |
| All non-deleted users | 28,344 | ~6.6 h | ~50 min |

Nightly over the first tier is the right default. Hit rate is low — of five real
project leads sampled, **three returned zero requests** — so a full sweep is
mostly empty and the crawl matters more than the seed breadth.

### 7.5 Enrichment and closure

For each worklist username, `GET /v1/people/:username` supplies the detail
sheet. Re-poll to close on `isReconciled`. Cache **hours, not days** — this is a
closure signal and must not go stale.

### 7.6 New code

`src/sam/integration/xras_api/` — follow `src/sam/integration/awards/` exactly.
It is the closest template in the repo and already a sibling of
`src/sam/integration/xras.py`.

- **`client.py`** — one persistent `requests.Session`; explicit `timeout=` on
  every call; `2 ** attempt` backoff; **404 → return `None`**; other 4xx →
  raise immediately (a client error is deterministic, never retried); 5xx →
  warn + retry; exhausted → raise `XrasSourceUnavailable`. Preserve the
  three-outcome model (**found / not-found / unreachable**) all the way to CLI
  exit codes, as `AwardSourceUnavailable` does.
- **GET-only allowlist, enforced structurally.** The API exposes POSTs
  (`/v1/resources/update`, the notification endpoints, `new_request_number`)
  that must never be reachable from SAM. Do not rely on convention.
- **`cache.py`** — `BucketedTTLCache`, registered by adding the module to
  `_BUCKETED_CACHE_MODULES` in `src/webapp/caching/__init__.py:48-53`. That one
  line gives `sam-admin cache --refresh --category`, the Admin Configuration
  card row, `stats()` and `clear()` for free. Cache successes *and* definite
  negatives; never cache an `XrasSourceUnavailable`.
- **Config** via `os.getenv` — `XRAS_API_KEY`, `XRAS_API_BASE`,
  `XRAS_ALLOCATIONS_PROCESS` — declared in `.env.example` and
  `helm/values.yaml`, **fail-closed** when unset. No Click / Flask / rich /
  kubernetes imports anywhere under `src/sam/` or `src/scheduling/`.

`src/sam/queries/xras_accounts.py` — the worklist query: group pre-flight
failures by username, classify (absent / inactive / placeholder-shaped), join
the most recent `xras_action_log` row for provenance.

⚠️ **Do not export the new query module from `sam/queries/__init__.py`** if it
imports `sam.notify` — that file imports its submodules eagerly. See the
existing trap documented for `expiration_notices` / `xras_notices`.

**The sweep is a scheduled task** under `src/scheduling/tasks/`.
⚠️ `SAM_TASKS_DISABLED` is **fail-open**: registering a task puts it live on the
next hourly wake unless its name is added to `helm/values.yaml` **in the same
change**. Ship it switched off, as `xras_notices` was, and add the
`values.yaml` grep assertion the existing task tests use.

### 7.7 Schema — one new table, not zero

**The worklist itself needs no storage.** It derives entirely from
`xras_action_log.raw_payload` (roles and usernames), `users` (existence and
activity), the pre-flight (the reason), and `/v1/people` (the detail, cached).
This follows `XrasActivationEvent`'s own rule: *state is DERIVED, never stored*.

**Operator notes and dismissals do need storage, and `XrasActivationEvent`
cannot carry them.** Its key is

```python
project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
```

— project-scoped and NOT NULL. The account worklist is **username-keyed**, and
for a New request *the project does not exist yet*. That is the entire case.
So reuse the **pattern**, not the table.

Proposed `xras_account_event`, mirroring the existing table deliberately:
append-only (a new row, never an edit of an existing one); `username`
`varchar(35)` (`users.username` width) in place of `project_id`; `event_type`
from a small tuple; `comment`; `created_by` `varchar(35)` ("the human who
clicked"); `xras_action_log_id` as **provenance only**. Derive current state by
timestamp comparison against the latest action naming that username — exactly as
the activation card does. That gets re-open-on-new-information and anti-spam for
free, and avoids a `done` boolean that would be wrong the moment the same person
appears on a second request.

⚠️ **Adding a table is no longer a DBA round-trip.**
`docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md` records
`GRANT CREATE, ALTER, INDEX, REFERENCES ON sam.* TO 'hpc-writer'@'%'` granted
**2026-08-10**, with the applied sequence and verification queries in
`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2 as precedent. There is
deliberately **no `DROP`** — so get the DDL right the first time. SAM has no
migrations (Alembic covers only `system_status`): the table is created by hand
and the ORM model written to match.

**Faster option:** ship the card **read-only** first — the worklist derives with
*zero* schema change — and land the notes table as an immediate follow-up. That
takes DDL off the critical path without changing any query or classifier work.

### 7.8 The dashboard

A card beside the existing XRAS surfaces in
`src/webapp/dashboards/allocations/blueprint.py` (which already serves `/xras`,
`/xras_fragment`, `/xras_pending_fragment`), with templates under
`src/webapp/templates/dashboards/allocations/`.

Columns: username · why (the five classes of § 7.3) · person detail from
`/v1/people` · which request / projcode and action needs them · XRAS
`isReconciled` · notes.

Follow the house rules in `CLAUDE.md`: **§9** for the form schema
(`sam.schemas.forms`) and the smallest handler tier that fits, **§8** for the
access decorator on any project-scoped route, **§10** for any active-only
toggle (`read_active_only`), and `sam.fmt` filters for all formatting.

### 7.9 Two cheap wins worth folding in

**(a) Make `--validate-mapping` two-sided.** `audit_resource_mapping`
(`src/sam/queries/xras_actions.py:652`, body at :678-713) reads two local tables and nothing
else; its own docstring concedes it has no list of the keys XRAS will actually
send. So the failure that genuinely breaks an award — XRAS sends a
`resourceRepositoryKey` SAM has no row for — is **invisible** to the command and
surfaces only at runtime as `No resource found in SAM corresponding to key %s`
(`src/sam/xras/errors.py:201`, raised from `src/sam/xras/handlers/_fields.py:148`).
`GET /v1/resources` **is** that missing list. One global GET closes the gap.

**(b) `opportunityId` → allocation type.** SAM derives allocation type with a
~676-line, 11-strategy chain (`src/sam/xras/extractors.py`). Exactly one arm is
a keyed lookup; the rest are string matching on operator-authored free text
(a regex on `requestTitle` for `CSL`, case-sensitive `contains` of
`'no NSF award'` / `'unsponsored'`, literal `startswith` prefixes). Only 5 of
the 11 strategies are exercised by all 41 production payloads.

Meanwhile **`opportunityId` is already on the inbound wire in 41/41 fixtures**
(declared at `src/sam/schemas/forms/xras.py:381`) and
`grep -rn opportunityId src tests --include=*.py` returns **one** hit — that
declaration. Nothing reads it.

The outgoing API closes the loop because `/v1/opportunities/:id` and
`/list/:ids` **resolve historic and `Terminating` opportunities**, not just
currently-open ones — verified for every opportunityId in the fixture corpus.
That defeats the objection that would otherwise sink a static table: the `Large`
window mints a **new opportunityId every semester**, so a hardcoded map rots
twice a year, but an unknown id can simply be fetched and cached.

Design constraints for this one:

- **Key the map on `opportunityId`, not on the wire `allocationType` string.**
  XRAS's vocabulary (`Small`, `Large`, `Educational`, `Exploratory`,
  `Data Analysis`, `NCAR External Projects`) is a *different* vocabulary from
  SAM's `allocation_type` table, and it does not disambiguate — two different
  opportunities both report `Small`, while `Exploratory` must land on SAM's
  `Small (No NSF award)`.
- **Keep the existing two-column `(panel, allocation_type)` join**
  (`extractors.py:346-350`): `allocation_type.allocation_type` is not unique
  (`Small` exists under two panels, `Education` under two more).
- **Leave the 11-strategy ladder as the fallback** for an unresolvable id.

---

## 8. Phase 2 — if a broader credential is granted

**Frame the ask as a capability, not a flag.** The obvious request — "provision
our key for `XA-CONTEXT: admin`" — is probably *insufficient on its own*,
because genuine XRAS administrators get no extra breadth through this API
(§ 4.5). The admin web app's queue view comes from somewhere else.

Better: **"a credential that lets SAM enumerate NCAR's Approved requests — the
same set the Recent Submissions queue shows."** Let XRAS choose the mechanism
(an admin-context key, a reporting role, a service identity, or a webhook).

What it would add, in value order:

1. **Mirror the whole Approved queue.** The worklist stops depending on the push
   *or* on SAM already knowing the person — closing the third population
   (brand-new placeholder identities) **ahead of** the push rather than at it.
2. **Dropped-push detection.** Today a push that never arrives is invisible
   until somebody notices a missing project. With enumeration, XRAS's Approved
   set diffs against `projcode` wholesale (§ 6).
3. **An unreconciled-people report** — the admin app has one; there is no API
   equivalent.
4. **Review / panel state** — `states[]` exposes "Conflicts Verified" and
   "Reviewers Assigned" per action, but the review queue itself is admin-only.

**Phase 1 is not throwaway.** Phase 2 changes only the **feed**. The classifier,
the five error classes, the person enrichment, the closure signal, the notes
table and the card are all unchanged. **Design the worklist query to accept a
set of `(action, roster)` records from either source** — that one decision is
what makes Phase 2 additive.

---

## 9. Verification

- **Unit tests with recorded fixtures; no live calls in CI.** Mirror
  `tests/unit/test_award_*` and the `AwardSourceUnavailable` three-outcome model.
- **Pre-flight correctness**: the worklist must reproduce the five `errors.py`
  strings against `tests/fixtures/xras/actions/`, with the
  `placeholder34-user-00034` entry classified *absent* and a known-inactive
  username classified *inactive*.
- **Predicate regression**: a case where `isAccountToBeCreated` is true but the
  SAM user exists and is active must **not** appear on the worklist. This is the
  live counterexample of § 5 and the whole reason that flag is not the predicate.
- **§ 7.9(a) self-verifies**: it must report 13/13 against today's catalog.
- **Live opt-in probe script** under `scripts/xras/`, gated on `XRAS_API_KEY`
  being set, that **skips** rather than fails when absent — mirroring
  `utils/parity/`'s `_resolve_xras_credentials()` behaviour.
- **Route-map parity**: regen with `ROUTE_MAP_REGEN=1` and commit the snapshot
  diff in the same change as the new dashboard routes.
- **Helm**: assert the new task's name appears in `SAM_TASKS_DISABLED`
  **per-manifest** (`-s templates/cronjob-tasks.yaml`) — a whole-render grep
  passes on the Deployment's copy and proves nothing.
- **End-to-end**: `docker compose up webdev --watch`, seed via
  `scripts/xras/seed_dev_actions.py`, confirm the card lists a seeded
  placeholder identity and that adding a note writes an event row.

### The Tier-III test bed — `~/xras_payloads_raw/`

**Only unscrubbed payloads can validate the core predicate, and this is
structural.** The anonymizer rewrites every username to `user_<hex>` (or
`placeholder<NN>-user-<NNNNN>`), so for the in-tree fixtures "no `users` row" is
trivially true for **all** of them and proves nothing. Distinguishing a
genuinely-unknown user from an artifact of scrubbing requires real usernames
posted against the cloned development database.

That corpus already exists outside the tree, deliberately uncommitted:
**41 payloads** (8 top-level plus 33 under `incoming_2026-08-11/`), spanning
`New` ×13, `Extension` ×7, `Supplement` ×7, `Date Adjustment` ×4,
`Adjustment` ×2 — and it carries the cases that matter: **6 role entries with
`isAccountToBeCreated=true` and ~4 distinct ARC placeholder identities**.
Locally, `xras_action_log` and `xras_activation_event` both exist at 0 rows, so
the path is ready.

Procedure: POST a payload naming an unknown user to the local
`/api/xras/v1/actions` (`scripts/xras/seed_dev_actions.py` does the posting,
reading `SAM_XRAS_USER` / `SAM_XRAS_PASS`), then confirm the pre-flight
classifies that username **absent** rather than **inactive**, that `/v1/people`
enriches it, and that an operator note writes an event row.

⚠️ **These payloads are unscrubbed and name real people.** They stay outside the
tree. Nothing derived from them — usernames, emails, or counts tied to
identities — may enter a commit, a test fixture, or a docstring. Any regression
test that must *persist* is built from the scrubbed corpus; the raw run is a
one-time manual verification recorded only as pass/fail.

---

## 10. Safety and secrets

- **Read-only, GET-only**, enforced in the client (§ 7.6), not by convention.
- **`XA-USER` impersonation is how per-user reads work.** Every polled username
  must originate in SAM or in a received payload, must be logged, and the whole
  feature sits behind a fail-closed flag in the style of the existing
  `XRAS_ACTIONS_CAPTURE_ONLY` / `XRAS_ACTIONS_ENABLED` levers.
- **The API key must not enter this repo.** It currently sits in cleartext in a
  shell script in the operator's home directory. Move it to the `SAM_keys`
  credential store and a k8s secret. Note it is a *submit*-context key — the same
  credential that can `POST /v1/resources/update`, which is precisely why the
  client's GET-only allowlist matters.
- **Do not confuse it with `SAM_XRAS_USER` / `SAM_XRAS_PASS`**, which are
  credentials for calling *SAM* (`.env.example:107-108`) and are a production
  **write** credential in the inbound direction.
- **The worklist names real people, emails, organizations and
  `residenceCountry`.** Gate the card on an existing permission — follow the
  Notifications pattern, where counts are visible at one level and rows naming
  real addresses require `SYSTEM_ADMIN`.
- **The local dev database may hold unobfuscated production data.** Nothing
  derived from a local query goes into a commit.

---

## 11. Open questions for the implementation session

1. **Will XRAS grant an enumeration credential?** (§ 8.) Ask early — it does not
   block Phase 1 but it determines how much of Phase 2 is worth designing now.
2. **Are the two flagship compute resources really production-ended?** The API
   reports a `productionEndDate` months in the past for both (§ 6). Confirm with
   XRAS whether that is stale data or a telegraphed retirement.
3. **What seed tier and crawl depth?** § 7.4 gives measured costs; the right
   default is probably nightly over leads+admins with a shallow crawl, but that
   should be tuned once real hit rates are known post-cutover.
4. **Does the co-PI role type ever appear?** Still zero across all fixtures and
   all live sampling. The outgoing API is the one place that could settle the
   spelling definitively, since it carries a numeric `roleTypeId`.
5. **Read-only card first, or wait for the notes table?** (§ 7.7.)

---

## 12. Reference — related documents

| Document | Why it matters here |
|---|---|
| `docs/xras/README.md` | Index; explains the `incoming/` vs `incoming/implemented/` lifecycle convention this document's eventual sibling would follow |
| `docs/xras/incoming/XRAS_REIMPLEMENTATION.md` | The inbound wire contract, action semantics, the allocation-type extractor's design, and the open-risks list (incl. the co-PI question) |
| `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` | § 2 has the applied DDL sequence and verification queries — the precedent for § 7.7 |
| `docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md` | Records the 2026-08-10 DDL grant that makes a new table cheap |
| `docs/xras/incoming/implemented/XRAS_SPRINT_B.md` | The operator surface, activation worklist and `sam-admin xras` — the closest existing analogue to the proposed card |
| `src/sam/integration/awards/` | The outbound-client template to copy: client + cache + registry + base, with a CLI wrapper |
| `src/webapp/api/xras/actions.py` | The inbound handler; `_record`/`_finish` and the audit-row width guards |
| `src/sam/xras/dispatch.py` | `select_service()` and the `validate_only` seam Phase 1 is built on |

# XRAS Integration Reimplementation (Python)

## Context

Legacy SAM (Java/Tomcat, deployed build **2.0.3**) is NCAR's site-side server for the XRAS
allocation integration. The XRAS broker at https://admin-ncar.xras.org/ **pushes** allocation
decisions to SAM (`POST /api/xras/v1/actions`) and **pulls** identity and request data from it
(`GET /api/xras/v1/people*`, `/requests/*`). It is one of the remaining major legacy surfaces not
yet ported.

The port is a **drop-in replacement** — same URLs, same auth headers, same response bytes — with two
deliberate improvements: **structured error responses** (422 carrying the real validation messages,
instead of a 500 carrying an opaque timestamp), and a **DB-backed audit trail with an admin UI**,
replacing a workflow whose only record is an email and whose only replay mechanism is pasting JSON
into a form.

**Provenance.** Every number here is measured, not inferred. Sources: 30 days of access, application
and XRAS-action logs on `sam-tomcat.ucar.edu` (**2026-07-07 → 2026-08-05**; retention is a hard 30
days with no archives), read-only queries against the production database (`sam-sql.ucar.edu`), live
credentialed probes of the running endpoint, and the deployed source at tag `2.0.3`.

**Which legacy checkout to read.** `~/codes/sam` is on `project_structure_AI_docs`
(`git describe` → `2.0.3-16-g8c07316f9`). **No checkout switch is needed:** `git diff 2.0.3..HEAD`
over `src/main/java/edu/ucar/cisl/sam/xras`, `src/main/resources/spring` and
`src/main/resources/hibernate/xras` is **empty** — the XRAS code, security config and named queries
in the working tree are byte-identical to the deployed tag.

---

## Status

| Phase | State | Notes |
|---|---|---|
| **0** — Prerequisites | **partly done** | credential ✅, role enforcement ✅, `VIEW_XRAS` + `MANAGE_XRAS` ✅ (Sprint B). `xras_action_log` and `xras_activation_event` exist in **dev and CI only** — the prod DDL is one DBA ticket, now unblocked. SMTP still open |
| **1** — Read endpoints (6 GETs) | ✅ **done** | PR #424; 94% of traffic |
| **2** — Action ingestion + audit trail | ✅ **done, capture-only** | `xras_action_log` + `XrasActionSchema` + `POST /actions` shipped behind `XRAS_ACTIONS_CAPTURE_ONLY`; see [`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md). Dispatch is Phase 3 |
| **3** — Handlers | ✅ **done** | **Sprint C** — see [`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md). All six services registered: Extension, Supplement, Adjustment, New, Update; Transfer is a *registered* handler that parks with a reason. Behind `XRAS_ACTIONS_CAPTURE_ONLY`, which flips **only at cutover** |
| **4** — XRAS as the 4th Allocations tab | ✅ **done** | **Sprint B** — see [`XRAS_SPRINT_B.md`](XRAS_SPRINT_B.md). Tab, replay, `sam-admin xras`, the activation worklist; and it settled `xras_activation_event` so one DBA ticket carries both tables |
| **5** — Parity and cutover | **partly done** | `--api xras` harness ✅, and `sam-admin xras --validate-mapping` ✅ (run it **before** parity — closing a mapping gap moves GET response bytes). **Next gate: run parity against the deployed port** — cutover step 1, needs only a deploy to `samuel.k8s` |

### Sprint map

Listed in execution order. **Sprint A was scoped as ingestion *and* handlers; the
handlers slipped out of it** — the payload harvest turned out to be the sprint, and
shipping capture-only was what made the harvest possible at all. Sprint C picks them
up, which pushes SMTP to D.

| Sprint | Contents | State |
|---|---|---|
| **A** — Action ingestion | Phase 2: `xras_action_log`, ORM, `XrasActionSchema`, `POST /actions` in capture mode | ✅ shipped — [`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md) |
| **B** — Operator surface | Phase 4: the 4th Allocations tab, `sam-admin xras`, replay, `VIEW_XRAS`/`MANAGE_XRAS`, the activation worklist | ✅ shipped — [`XRAS_SPRINT_B.md`](XRAS_SPRINT_B.md) |
| **C** — Handlers | Phase 3: the dispatcher and all six handler paths, and the replay-and-diff oracle that verifies them | ✅ shipped — [`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md). Suite 4,708 → **5,213** |
| **C.1a** — Handler refactor | The `ActionHandler` base class the six handlers should have shared. Six bugs the duplication produced, one of them live | ✅ shipped — [`XRAS_HANDLER_REFACTOR.md`](XRAS_HANDLER_REFACTOR.md) § *Deviations*. Suite 5,213 → **5,223** |
| **C.1b** — Stress + schema | Stress the handlers with the **audit row** as the assertion target, then decide the remaining `xras_action_log` columns | ☐ next — [`XRAS_STRESS_AND_SCHEMA.md`](XRAS_STRESS_AND_SCHEMA.md). ⚠️ Has the DBA-ticket clock on it |
| **D** — SMTP | Phase 0.2: lift `EmailNotificationService` into `src/sam/notifications/` | ☐ **deferrable** — see below |

**C.1a is done, so C.1b inherits the easier half of it.** The stress harness now has
**one** `management_transaction` patch point instead of five, and
`tests/unit/test_xras_transaction_seam.py` keeps it that way — which matters because a
missed patch site in a harness that writes to the shared database fails *silently*.
C.1b is now the only thing between here and the DBA ticket.

**What is left before cutover is not code.** Four gates, in order: (1) the DBA ticket for
both tables; (2) `--validate-mapping` clean, *then* `--api xras` against the deployed
host; (3) the 400/422 contract confirmed with `allocations@access-ci.org`; (4) XRAS
repoints its base URL — which is the cutover, and the moment
`XRAS_ACTIONS_CAPTURE_ONLY` flips to `0`.

Two things run on external lead time and are **not** gated on any sprint. Start them
in parallel, not after:

- **The DBA ticket** for `xras_action_log` + `xras_activation_event` (one ticket, both
  init scripts — `containers/sam-sql-dev/initdb.d/zz-90-*.sql` and `zz-91-*.sql`), plus
  the manual run on staging, whose `init-rds.sh` has no initdb hook. It is unblocked:
  Sprint B's definition-of-done condition for filing it is met. Nothing can be captured
  or cut over until it lands, and it is the only item here with a third party's
  schedule attached.

  ⚠️ **Landing it does not, by itself, start capturing anything.** XRAS posts to
  legacy's URL; nothing reaches this endpoint until XRAS repoints, and that repoint
  *is* the cutover. An early ticket buys lead time, not payloads. Growing the corpus
  ahead of cutover would need a dual-post arrangement, which is **ruled out** — see
  § 6 Phase 5.5.
- **Confirming the 400/422 contract change with `allocations@access-ci.org`** (§ 2.5).
  Broker retry behaviour on 4xx is unknown, and it is the riskiest open unknown on the
  cutover path. It is an email, not code.

Three sequencing points that are easy to get wrong:

- **The GET cutover is independent of the POST work.** Phase 1 is finished; cutover steps 1–3 need
  only a deploy to `samuel.k8s` and a green `--api xras` run. That moves 94% of the traffic off
  legacy while Sprint C is still being written — do not queue it behind the handlers.
- **Harvest real payloads first, not last.** Phase 5.1 lists it late, but it was the input to two
  Sprint A deliverables: `XrasActionSchema` (seven nested schemas, the most likely thing to be
  wrong) and the New handler (21% of posts at 30% success, against a repo that then held exactly
  **one** sample payload). Borne out — the harvest *was* Sprint A, and it corrected about twenty
  points of the inferred contract. The corpus is now 8 and nothing is sample-blocked, but the
  principle still applies to what remains unsampled (co-PI `roleType`, `Transfer`, `Renewal`,
  `Advance`), and production capture is now the cheapest way to get it.
- **SMTP can genuinely be deferred.** XRAS projects arrive `active = 0` and the success email is
  the human activation trigger — but **legacy keeps sending those emails until `POST /actions`
  cuts over**, which is cutover step 4, after Sprint C. No notification gap opens before then.
  The requirement is only that *some* path exists before step 4, and Sprint B's pending-activation
  card is it.

---

## 1. What production actually does

### 1.1 Traffic — small, narrow, single-sourced

| Endpoint | 30d hits | Status split | Latency p50 / p95 | Body size |
|---|---:|---|---|---|
| `GET /api/xras/v1/people/{username}` | 3,058 | 2,128×200 / 930×404 | 95 ms / 107 ms | 87–274 B |
| `POST /api/xras/v1/actions` | 175 | 108×200 / 67×500 | 383 ms / 1,130 ms | 41 B ok, 112 B error |
| `GET /api/xras/v1/people?` | 30 | 30×200 | 1,123 ms / 1,272 ms | **3.81 MB** |
| `GET /api/xras/v1/requests/request/{n}` | 1 | 1×200 | 7,679 ms | — |
| every other mapped endpoint | **0** | — | — | — |

- **One caller, ever:** `18.223.62.77` (AWS us-east-2), User-Agent `Ruby` (bare `Net::HTTP`
  default), 100% of requests. Cutover is a single-party conversation, and an IP allowlist would be
  complete access control for this surface.
- **Peak burst is 24 requests/minute** (~0.4 rps). The throughput bar is trivial; the latency bar is
  not (§4.2).
- `GET /people` is a **nightly cron at 03:00:5x MDT, 30 days out of 30**. Everything else follows a
  weekday/weekend human pattern (100–290/day vs 7–32/day).
- The roster URL is literally `GET /api/xras/v1/people?` — a bare trailing `?`.

> **Analysis trap.** `grep -i xras` over the access logs is dominated by **187,382** requests to
> `/api/protected/amie/v1/task/AMIE/XRAS-*/create_project` on 2026-07-08–09 — an AMIE polling loop
> stuck on 13 tasks. That is 57× the entire real XRAS volume and is unrelated. Filter on
> `/api/xras/`.

### 1.2 Action mix

`actionType` is **never written to any log** — it exists only as a Velocity variable in the
notification emails. The distribution below was recovered by correlating the 109 success lines in
`sam-xras-actions.log` against `allocation_transaction.creation_time` (±60 s) and
`project.creation_time`:

| Effective action | posts | share | DB effect |
|---|---:|---:|---|
| **Extension** (existing project) | 65 | **60%** | 213 `EXTENSION` rows — avg **3.3 allocations per post** |
| **New** (project created) | 23 | 21% | 63 `NEW` rows — avg 2.7 allocations |
| **Supplement** (existing project) | 16 | 15% | 24 `SUPPLEMENT` rows |
| Update adding an allocation to an existing project | 3 | 3% | `NEW` ×2, `NEW`+`SUPPLEMENT` ×1 |
| Successful post that mutated nothing | 2 | 2% | 0 rows |
| **Transfer** | **0** | — | — |
| **Adjustment** | **0** | — | — (none *in this window* — one was observed 2026-08-05, § 1.4) |

### 1.3 Failure is concentrated in one code path

Of 67 failures, 66 come from validators and extractors that **only run on the New/Renewal path**.
The exception is one `Action end date before existing allocation end date for Derecho GPU`.

| Path | successes | failures | success rate |
|---|---:|---:|---:|
| **New / Renewal** | 28 | **66** | **30%** |
| Extension | 65 | 1 | 98.5% |
| Supplement | 16 | 0 | 100% |

Only six distinct causes exist in 30 days:

| Cause | count | share |
|---|---:|---:|
| `PI <name>-user-<token> is not in database` — unreconciled ARC (https://arc.ucar.edu/) placeholder identities | 37 | 55% |
| `Could not determine Mnemonic code for internal PI via organization` | 16 | 24% |
| `PI <user> is not an active user` | 6 | 9% |
| `Cannot find contract for grant number "<n>"` | 4 | 6% |
| `Username <user> is missing` | 4 | 6% |
| `Action end date before existing allocation end date for <resource>` | 1 | 1.5% |

Five identities account for 39 of the 67; `kquagraine-user-89o84` alone accounts for 17. This is not
a broad reliability problem — it is a small set of unmapped identities reposted by hand, repeatedly,
because XRAS does not auto-retry and the 500 body tells the operator nothing.

**Planning consequence:** the highest-volume handler is nearly perfect and the hardest one carries
all the pain. Build `Extension` first to establish the pipeline on the easy path, then invest in
`New`.

### 1.4 The manual-fallback path fires rarely — but it does fire

`ManualFallbackActionPostService` is reachable *only* via `catch (BadRequestException)` — i.e.
`ProjectActionServiceSelector` finding no serviceable, which is what an `Adjustment` or `Advance`
actionType produces. (The selector's guard string is `"Adjust"`, not `"Adjustment"` — **legacy
defect 4**, § 9 — and there is no `"Advance"` serviceable at all.) It logs only at `LOG.debug()`,
which is suppressed, so it cannot be grepped; detected instead by comparing access-log 200s
against `EmailingActionPostService` INFO lines per day. **Δ = 0 on every day with coverage — zero
invocations in the measured 30-day window.**

⚠️ **Corrected 2026-08-07: that is a sampling artifact, not the true rate.** On 2026-08-05 this
path fired for an `Adjustment` on UWIS0064, forwarded by Travis Fair and now committed as
`tests/fixtures/xras/actions/adjustment_uwis0064_manual.json`. Its subject line
(`"New XRAS post action (Adjustment request for UWIS0064)"`) is `formatSubject` in this very
class, so the provenance is unambiguous. Two consequences:

- The measured window simply contained no Adjustment. The rate is low, not zero, and **§ 1.2's
  `Adjust: 0` row means "none in those 30 days"**, not "never happens".
- The harvest query in `XRAS_SPRINT_A.md` § 3b missed this path for the same reason: it matched
  only the three `EmailingActionPostService` subjects. Manual-fallback payloads carry a fourth,
  and they are the *only* record of the action types SAM does not service.

Its structural consequence matters more than its usage: on that path the broker receives
`200 {"message":"OK"}` for an action SAM silently deferred to a human. **The legacy 200/500 split
does not distinguish "processed" from "quietly parked."** Ours must.

---

## 2. Wire contract

### 2.1 Endpoints

`web.xml` maps a dedicated `DispatcherServlet` (`xrasRestApi`, context
`classpath:spring/xras-rest-context.xml`) at **`/api/xras/*`**, plus a dedicated
`XrasAuthenticationFilter` on the same pattern. All five controllers are `@RestController` extending
a shared `XrasController`. Effective path = `/api/xras` + the `@RequestMapping` value; there is no
bare `/v1/*` surface.

| # | Method | Path | Response type |
|---|---|---|---|
| 1 | GET | `/api/xras/v1/people` | `List<PersonDTO>` — **bare array, not wrapped** |
| 2 | GET | `/api/xras/v1/people/{username}` | `PersonDTO` — **bare object, not wrapped** |
| 3 | GET | `/api/xras/v1/requests/request/{requestNumber}` | `ResponseWrapper{result: AccountingRequestResponse}` |
| 4 | GET | `/api/xras/v1/requests/user/{username}` | same |
| 5 | GET | `/api/xras/v1/requests/role/{role}/{username}` | same |
| 6 | GET | `/api/xras/v1/dates/requests/{requestNumbers}` | `ResponseWrapper{result: List<RequestDatesDTO>}` |
| 7 | POST | `/api/xras/v1/roles/{requestNumber}/{role}/{username}` | empty body, 200 |
| 8 | POST | `/api/xras/v1/actions` | `{"message":"OK","result":null}` |

- **#1 and #2 are the only endpoints without the `{message, result}` envelope.** Any client
  description assuming a uniform wrapper is wrong for the highest-traffic endpoint on the surface.
- #5's `{role}` segment is a lowercase snake_case key: `pi→Pi`, `co_pi→CoPi`,
  `allocation_manager→AllocationManager`. An unrecognised role throws `IllegalArgumentException` →
  500. The role check runs **before** username validation.
- #7 accepts **only** `pi` (`equalsIgnoreCase`, so `PI`/`Pi` also match); anything else is
  `NotFoundException` → 404. **It is not a roster endpoint** — it calls
  `RoleService.setLeadUserRole(requestNumber, username)`, i.e. it *reassigns the project lead*.
  There is no XRAS endpoint for adding a co-PI or an ordinary member. Rosters arrive whole, in the
  `roles[]` array of `POST /actions` — see §3.5. (And the ACCESS spec's `DELETE /v1/roles/…` is
  unimplemented, so revocations never reach SAM at all.)
- #8 takes `@RequestBody String actionJson` and calls `new ObjectMapper().readValue(...)` itself — a
  second, unconfigured mapper. Parse failure → `RuntimeException` → 500.
- The ACCESS/XRAS spec documents `POST /v1/actions/<actionId>/<requestId>/<actionType>`, but **all
  175 real posts go to bare `/api/xras/v1/actions`**, the only form SAM maps. If the broker is ever
  corrected to match its own docs, every post 404s — **map both forms defensively.**
- Spec endpoints SAM does not implement, and which are out of scope here: `GET /test_auth`,
  `GET /v1/usage/by_month/…`, `DELETE /v1/roles/…`, and the `/v1/users/…` family.

### 2.2 Auth

**Header translation** — `XrasAuthenticationFilter`:

1. If **neither** `XA-REQUESTER` nor `XA-API-KEY` is present → pass through untouched.
2. Otherwise wrap the request in a case-insensitive mutable header map.
3. If `Authorization` is **absent** *and* **both** XA headers are present → set
   `Authorization: Basic base64(requester + ":" + apikey)` (UTF-8, non-chunked).
4. **Unconditionally remove both XA headers.** Supplying only one ⇒ headers stripped, no
   `Authorization` synthesized ⇒ 401.
5. An explicit `Authorization` header always wins.

**Authorization** — the `/api/xras/**` chain is stateless, CSRF disabled, security headers disabled,
`requires-channel="https"`, `use-expressions="false"`, `access="ROLE_XRAS"` (plain `RoleVoter`, so
the authority string is literally `ROLE_XRAS`).

**Credential store** — `api_credentials` (`username`, `password` bcrypt, `enabled`) joined via
`role_api_credentials` → `role`. Production holds `XRAS` (id 2, enabled, `ROLE_XRAS`), a disabled
`XRAS_OLD` (id 1), and `samuel` (id 14, ours — §Phase 0.4). `api_credentials.username` is
`varchar(11)`.

**401** — byte-exact, verified with `od -c`:

```
Content-Type: application/json;charset=UTF-8
Content-Length: 41
(no WWW-Authenticate header — deliberate, per XrasAuthenticationEntryPoint's javadoc)

{\n  "message" : null,\n  "result" : null\n}
```

Note the **space before the colon** (Jackson's `DefaultPrettyPrinter`) — and note that this is the
*only* pretty-printed body on the surface; every 200 and the `/people/{u}` 404 are compact.

**Unmapped paths and 403** both depend on whether auth succeeded first, because the filter and
security chain run *before* routing. Measured at `/api/xras/v1/test_auth`:

| Condition | Legacy response |
|---|---|
| no credentials, or wrong ones | **401**, the 41 B JSON above |
| valid `ROLE_XRAS`, unmapped path | **404**, 431 B of Tomcat HTML |
| valid credentials without `ROLE_XRAS`, any path | **403**, the same 431 B of Tomcat HTML |

The HTML comes from Spring's default `AccessDeniedHandlerImpl` calling `sendError()` while `web.xml`
declares no `<error-page>`. **Do not reproduce it** (§7 divergences 1 and 5) — no real client has
ever received it on a mapped path.

### 2.3 Response shapes

**`PersonDTO`** (endpoints 1–2), in Java field-declaration order, which is the JSON key order:
`username, firstName, middleName, lastName, organization, academicStatus, phone, email` — all
strings. Note this differs from the SQL alias order, where `phone` and `organization` are swapped.

**Roster row order is `users.user_id` ascending.** The `identityServicePersons` named query has **no
`ORDER BY`**, so legacy's order is a MySQL artifact of `GROUP BY`. Confirmed two ways: production's
first eight usernames (`bruceb robted fulker rodi kubo mbetsill clw remmel`) are the first eight the
local dev DB emits, and both match `ORDER BY user_id` — **not** `ORDER BY username`. Our port states
it explicitly, which reproduces the observed bytes and makes the order deterministic rather than
incidental.

**`GET /people/{u}` 404**: `{"message":"username=<u> not found","result":null}`, whose length is a
closed form — **`bytes = len(username) + 47`**. Exact at username lengths 2, 5, 11, 15 and 25 (49,
52, 58, 62, 72 bytes); the literal is 21 + n + 26.

**There are two different 404 bodies, with different wording.** `/people/{u}` misses emit
`username=<u> not found`; `/requests/user/{u}` and `/requests/role/{r}/{u}` also validate the
username (`RequestServiceController.validateUser`) but emit `User <u> not found`.

**`GET /requests/request/{n}`** → `ResponseWrapper{message: null, result: {...}}`. `{requestNumber}`
**is the projcode** — matching `projcode = trimToNull(requestNumber)` on the POST side (§2.4). The
`xras_request` view has no `requestNumber` column at all; its identifying column is
`projectId varchar(30)`, and `requestsByProjectCode` keys on it. Do not build a separate
request-number lookup.

```
projectIdLabel : null                                  # emitted, always null
masters[]      : { requestNumber, requests[] }         # an ARRAY — getMasters() returns .values()
  requests[]   : { requestType,                        # "New" | "Renewal" — see the rule below
                   requestBeginDate, requestEndDate,   # "yyyy-MM-dd" strings
                   allocationType, projectTitle, projectId,
                   xrasActionIds,                      # never emitted
                   fos[]         : { xrasFosTypeId, isPrimary: true },   # always exactly one
                   allocations[] : { actionType,       # never emitted
                                     allocationBeginDate, allocationEndDate,
                                     allocatedAmount,  # STRING, "%.1f"
                                     remainingAmount,  # STRING, "%.1f"; omitted when null
                                     resourceRepositoryKey,  # INT; omitted when null
                                     actions[] : { orderApplied,   # 1-based, assignment order
                                                   actionType,
                                                   amount,       # STRING "%.1f"; omitted when null
                                                   endDate,      # omitted when null
                                                   dateApplied } } }
```

#### Null handling is per-DTO, not a global setting

`xras-rest-context.xml` is `<mvc:annotation-driven/>` and nothing else — no
`<mvc:message-converters>`, no `ObjectMapper` bean. So the mapper is Spring's stock build, whose
inclusion is **`ALWAYS`**, and `NON_NULL` is applied **per class** by
`@JsonSerialize(include=NON_NULL)`:

| Class | Annotation | Behaviour |
|---|---|---|
| `ResponseWrapper` | none | **emits** `message: null` |
| `AccountingRequestResponse` | none | **emits** `projectIdLabel: null` (nothing ever assigns it) |
| `RequestMaster`, `FieldOfScience` | none | emit (no field is ever null in practice) |
| `RequestDatesDTO` | none | **emits** — `requestEndDate` can legitimately be null |
| `PersonDTO`, `Request`, `Allocation`, `Action` | `NON_NULL` | **omit the key entirely** |

**A port that applies one global "drop nulls" pass is wrong in both directions.** The empty string is
**emitted**, not omitted — one roster email proves `"" ≠ null`.

Field presence, measured across the full captured corpus (134 request, 555 allocation and 1,109
action objects from all seven `requests/*` probes):

| Field | Present | Note |
|---|---:|---|
| `xrasActionIds` | **0 / 134** | never set by `RequestFactory` |
| `allocations[].actionType` | **0 / 555** | as are `xrasActionId` / `xrasActionResourceId` on both `Allocation` and `Action` |
| `resourceRepositoryKey` | **376 / 555** (68%) | omitted when null — this *is* the unmapped-resource gap (§4.1) surfacing on the wire |
| `remainingAmount` | **243 / 555** (44%) | HPC-only |
| `actions[].amount` | **811 / 1109** (73%) | see the `actionType` correlation below |
| `actions[].endDate` | **867 / 1109** (78%) | same |
| every other field above | 100% | always emitted |

The two optional `actions[]` fields track `actionType`, because the underlying
`allocation_transaction` columns are populated per transaction kind:

| `actionType` | n | `amount` | `endDate` |
|---|---:|---|---|
| `New` | 554 | ✓ | ✓ |
| `Extension` | 301 | ✗ (298/301) | ✓ |
| `Supplemental` | 174 | ✓ | ✗ |
| `Adjustment` | 78 | ✓ | ✗ (67/78) |
| `Transfer` | 2 | ✓ | mixed |

Emitting them unconditionally breaks parity on **most** request responses — `Extension` alone is 27%
of all action objects.

#### `requestType` — "New" vs "Renewal"

`HibernateAccountingDao.setRequestTypes()` stamps every DTO `Renewal`, then per `projectId` keeps the
row with the smallest `requestBeginDate` using a **strict**
`earliest.getRequestBeginDate().after(dto.getRequestBeginDate())` comparison. A tie therefore leaves
the incumbent, so the **first row in result-set order wins**, and that order is `xras_request`'s
`ORDER BY al.end_date` ascending. In one sentence:

> label everything `Renewal`; then, iterating in `end_date` ASC, the first row achieving
> `min(requestBeginDate)` becomes `New`.

Verified on both tie cases in the corpus (`UALB0006`, three requests sharing 2014-10-16; `NRAL0032`,
two sharing 2022-05-02) and on all 20 observed masters — exactly one `New` each.

#### Array ordering

`RequestFactory` preserves result-set order into `LinkedHashMap`s, so three of the four arrays are
data-derived and one is not:

| Array | Order | Source |
|---|---|---|
| `requests[]` within a master | `allocation.end_date` **ASC** | `xras_request` view's `ORDER BY` |
| `allocations[]` within a request | `allocation.start_date` **DESC** | `xras_allocation` view's `ORDER BY` |
| `actions[]` within an allocation | `allocation_transaction.creation_time` **ASC**, `orderApplied` = 1..n | `xras_action` view's `ORDER BY` |
| **`masters[]`** | Java **`HashMap` bucket order** over the projcode keys | `AccountingRequestResponse.masters` is a `HashMap`; `getMasters()` returns `.values()` |

Two consequences:

- **`ORDER BY al.end_date` on `xras_request` is load-bearing.** It sets the `requests[]` array order
  (ascending in 13 of 13 observed masters) *and* the `New`/`Renewal` tie-break. Removing it — which
  is the obvious way to satisfy `ONLY_FULL_GROUP_BY` (§4.2) — would silently reorder every
  `requests/*` response and move the `New` label.
- **Neither `masters[]` nor a tied `allocations[]` is reproducible from the data**, so both are
  documented divergences (§7, #3 and #6) rather than parity targets.

#### Dates

`yyyy-MM-dd` strings everywhere except `dates/requests`, whose DTO holds a raw `java.util.Date` with
no date module configured on the mapper ⇒ **epoch-millis integers**, at **server-local (Denver)
midnight**. The element key is the projcode under the name `requestNumber`:

```json
{"message":null,"result":[{"requestNumber":"UALB0006",
                           "requestBeginDate":1413439200000,
                           "requestEndDate":1853906400000}]}
```

Legacy's `split(",")` on the path segment does **not** trim, so `"A, B"` looks up `" B"` and silently
misses.

### 2.4 Inbound `POST /actions` payload

All POJOs carry `@JsonIgnoreProperties(ignoreUnknown = true)`; absent strings default to `""`,
absent lists to empty lists, absent boxed numerics to `null`.

```
actionId(Integer) actionType actionBeginDate actionEndDate requestId(Integer)
requestNumber requestType requestAbstract requestTitle requestShortTitle
opportunityId(Integer) opportunityType opportunityName allocationType awardDate awardPeriod
resources[] : actionResourceId(Integer) resourceRepositoryKey(Integer) awardedAmount comments
roles[]     : requestPeopleRoleId(Integer) roleType username beginDate endDate
              isAccountToBeCreated(forgiving bool) person{}
  person    : firstName middleName lastName email phone organization academicStatus isReconciled
fos[]       : fosTypeId fosNum fosName fosAbbr isPrimary        # all STRING except isPrimary
panels[]    : type name abbr isPrimary
grants[]    : fundingAgency grantNumber programOfficerName programOfficerEmail piName title
              beginDate endDate awardedAmount awardedUnits percentageAward subAwardNumber
              primaryFos{} isPending
```

- `projcode` = `StringUtils.trimToNull(requestNumber)`.
- **PI** = the first role with `roleType == "PI"` whose date window brackets `actionBeginDate`; the
  same rule finds `"Allocation Manager"`.
- `ForgivingBooleanDeserializer` applies to **exactly one field**, `roles[].isAccountToBeCreated`:
  `null→false`, integer→`!= 0`, `t/true/y/yes`→true, `f/false/n/no/""`→false, anything else errors.
  All other booleans use Jackson defaults.
- **`isReconciled` and `isAccountToBeCreated` are inert** — parsed into the POJO and never read by
  any business logic. A role meaning "provision this new person" therefore fails regardless of
  intent.
- Real payloads send `fos[].fosTypeId` and `awardPeriod` as **numbers** into **string** fields;
  Jackson coerces, and our schema must accept both.

**Sample payloads.** The repo holds exactly one:
`2.0.3:src/test/resources/xras/rest/request/createActionGood.json` (3,593 B). The two JSON *schema*
files under `src/main/resources/json/xras/` reference a Java package that no longer exists,
`Action.json` is missing `grants` entirely and models `fos[]` with two fields — **they are not
contract.**

Real payloads exist **only** as `XRAS_post_action.json` attachments in `hdt@ucar.edu`, which is
the sole value of `xras.actionpost.recipients` (`2.0.3:app/env/sam.complete.properties:29`).
`sweg-notify@ucar.edu` does **not** hold them: it is `sam.errormail.to`, the logback
`SMTPAppender` (`app/env/logback.xml:6-14`), which mails buffered log *events* — stack traces,
no attachment. And `actionJson` is never logged at any level, so no log-level change recovers a
body either. Both the success and the failure mail carry the attachment
(`EmailingActionPostService.sendSuccessEmail` / `sendErrorEmail`), so hdt holds ~108 successes
plus 67 failures.

**Four real payloads have since been harvested** (New and Extension, one success and one failure
each) and live scrubbed in `tests/fixtures/xras/actions/`. They correct roughly twenty points of
the shape inferred from the POJOs — see [`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md) § *Track 0*, which
is authoritative, and `tests/unit/test_xras_actions.py`, which enforces it.

### 2.5 Status codes

Legacy handling is six `@ExceptionHandler` methods on `XrasController`; there is no
`@ControllerAdvice`.

| Condition | Legacy | Ours |
|---|---|---|
| `/people/{u}` miss | 404 `{"message":"username=x not found","result":null}` | unchanged |
| Unknown request number | 200, empty `masters` (62 B) | unchanged |
| Success | 200 `{"message":"OK","result":null}` | unchanged |
| Unrecognised `{role}` segment | 500, opaque timestamp body | **400** with a real message |
| Malformed JSON body | 500 | **400** |
| Validation failure | 500, `{"message":"Unhandled SAM exception processing XRAS request (timestamp <epoch-ms>)","result":null}` | **422** with the structured error list |
| Unhandled action type | 200 + email, no trace | **200 + audit row marked `manual` + email** |
| Missing/bad credentials | 401 (41 B JSON) | unchanged |
| Valid credentials, wrong role | 403 (Tomcat HTML) | 403 JSON envelope |

**Why the 422 matters.** `ActionProcessingException.getErrorMessages()` holds the ordered list of
real validation messages, but the exception matches no typed handler, so it lands in the catch-all
and **the messages are dropped from the HTTP response** — they survive only in the log and the
failure email. XRAS admins read the response body directly in their "Accounting Service Posts"
panel, so today they see `Unhandled SAM exception … (timestamp 1785384269504)` where they could see
`PI kquagraine-user-89o84 is not in database` and self-service the fix.

Confirm the 4xx change with `allocations@access-ci.org` before the `POST /actions` cutover step —
broker retry behaviour on 4xx is unknown.

---

## 3. Action-processing semantics

### 3.1 Selector — first match wins

```java
selector.setServiceables(
        addProjectActionService(),        // 1
        updateProjectActionService(),     // 2
        supplementProjectActionService(), // 3
        adjustProjectActionService(),     // 4
        transferAllocationActionService(),// 5
        extendProjectActionService());    // 6
```

| # | Guard | Behaviour |
|---|---|---|
| 1 | `actionType == "New"` && project **not** exists | Create project: title/abstract, lead=PI, admin=AM, allocation type via extractor chain, AOI from primary `fosNum`, org from lead, non-exempt, generated projcode, allocated GID; contracts from `grants[]`; per resource create an allocation (start clamped ≥ resource commission date, end-of-day end); add **every** `roles[]` entry to the accounts, regardless of `roleType` (§3.5); **finally set the project inactive** — a human activates it, and the success email is the trigger |
| 2 | `actionType ∈ {"New","Renewal"}` && project exists | Update fields (`active=true`); contracts; per resource: create allocation if none overlapping, extend if the end grows (**error** if it shrinks), undo an AUTO/DEFAULT canned allocation via a compensating `UNDO AUTO/DEFAULT` adjustment, then supplement (`>0`) or adjust (`<0`). `comments == "AUTO_DEFAULT_ALLOCATION_TRANSACTION"` ⇒ extension only |
| 3 | `actionType == "Supplement"` && project exists | Per resource: create allocation if none (start today, end = latest contract/allocation end), else supplement when `>0`; `≤0` ignored with a warning |
| 4 | `actionType == "Adjust"` && project exists | As Supplement; legacy silently drops negatives. ⚠️ **Unreachable in legacy** — XRAS sends `"Adjustment"`, so this row has never executed (defect 4, § 9). SAM accepts both spellings |
| 5 | `actionType == "Transfer"` && project exists | 1 negative source + ≥1 positive destinations, same project, Σ = 0, source clamped to available |
| 6 | `actionType == "Extension"` && project exists | **Ignores payload resources**; extends the latest allocation of **every active account** to `actionEndDate`; **errors** if that would shrink any |
| — | no match | `BadRequestException` → swallowed → manual-fallback email → **200** |

Assembly does **not** short-circuit: errors accumulate into an ordered `LinkedHashSet` on
`ProcessingAction` via `observer.report(...)`, then `throwExceptionIfErrors()` raises once with the
full list. Reproduce this — reporting every problem in one response is what lets an operator fix a
request in a single pass instead of five.

### 3.2 Allocation-type extractor — 11 ordered strategies, first non-null wins

Resolves to a **`(panel, allocation_type)` pair**, then `findByPanelAndType`. Never resolve by name
alone: production `allocation_type` contains `Small` twice (panels `UNIV USS` and `UW`) and
`Education` twice.

| # | Strategy | Match rule (case-sensitive unless noted) | Result `(panel, type)` |
|---|---|---|---|
| 1 | ACCESS | if `allocationType != null` → exact enum lookup (may return null and fall through); else lowercase `opportunityName` contains `discover` → Discover; contains `explore` or equals `staff allocations` → Explore | `("ACCESS","Discover ACCESS")` / `("ACCESS","Explore ACCESS")` |
| 2 | NSC | `opportunityName.startsWith("NCAR - NSC Allocation Request")` | `("NCAR-ARP","NSC")` |
| 3 | External | regex `(.* )?External( .*)?` matches **any** of `requestTitle`, `opportunityName`, `allocationType` | `("External Projects","External Project")` |
| 4 | CSL | regex `\s*CSL(\|[\W].*)` matches `requestTitle` only | `("CSLAP","CSL")` |
| 5 | Large | `allocationType == "Large"` or `opportunityName` contains `Large Allocation` | `("CHAP","CHAP")` |
| 6 | SmallNonNSF | `opportunityName` contains `no NSF award` \| `unsponsored` \| `Exploratory Allocation` | `("UNIV USS","Small (No NSF award)")` |
| 7 | SmallNSF | contains `w/ NSF` \| `with NSF` \| `Small Allocation` | `("UNIV USS","Small")` |
| 8 | Classroom | contains `Classroom/Training` \| `Classroom Allocation` | `("UNIV USS","Classroom")` |
| 9 | DataAnalysis | contains `Data Analysis Allocation` | `("UNIV USS","Data")` |
| 10 | ASD-UNIV | lowercase `opportunityName.startsWith("univ - asd opportunity")` | `("ASD-CHAP","ASD-UNIV")` |
| 11 | ASD-NCAR | lowercase `opportunityName.startsWith("ncar - asd opportunity")` | `("ASD-NCAR","ASD-NCAR")` |

All null ⇒ `AttributeExtractionException("Unable to determine allocation type from action data")`.

Production frequency of the resulting types (automated project creations, last 12 months) — order
test coverage by this:

| Type | n | | Type | n |
|---|---:|---|---|---:|
| `Small (No NSF award)` | 146 | | **`CHAP`** | **30** |
| `Small` | 87 | | `NSC` | 16 |
| `Data` | 79 | | `Discover ACCESS` | 15 |
| `Classroom` | 52 | | `Explore ACCESS` | 10 |
| | | | `External Project` | 4 |

A second use of the same resolution: `getAuthAtPanelMeeting()` returns `true` iff the resolved type
is `CSL` or `CHAP`.

### 3.3 Other extractors

- **Mnemonic** — `opportunityName.startsWith("NCAR ")` → organization parentage at lab level
  (parentage size 0 → null; ≤3 → element 0; else element `len-3`); else external PI → institution
  (exact `findOneByDescription("<name>, <city>")`); else internal → organization (fuzzy
  `code LIKE '%name%' OR description LIKE '%name%'` with `code` a `varchar(3)` — **broken: 150 of
  171 active orgs match nothing**). `XrasAction.getMnemonicCode()` is hard-coded `null`, so the
  extractor always runs.
- **Area of interest** — primary `fosNum` (first `isPrimary` entry, else first entry) →
  `Integer.decode()` → `findOne(id)`, falling back to `findOne(name)` on `NumberFormatException`.
- **Contract number** — regex `^(.*[^0-9])?([0-9]{6,})[^0-9]*$`, group 2, then
  `contract_number LIKE '%<core>'` with `uniqueResult()`. Grants with a blank number are skipped.
- **`/people` org fixup** — `UCAR/NCAR:<acronym>` → look up the organization by acronym, then: exact
  map `{NCAR→NCAR, UCAR→UCAR, UCP→"UCAR Community Programs"}`, else walk `parent_org_id` — a parent
  of `NCAR` yields `NCAR/<acronym>`, a mapped parent acronym yields its name, a null parent yields
  `UCAR`. An **unknown acronym yields `null`**, which then drops the key (PersonDTO is `NON_NULL`).

### 3.4 Error strings the handlers must be able to produce

> ⚠️ **SUPERSEDED — do not implement from this list.** Sprint A flagged it as needing a
> pass against the Java source; that pass is done, and this list turned out to be wrong
> or incomplete in **seven** places: a double space dropped, `Missing begin/end date`
> is really two strings, and four strings are missing entirely
> (`Could not convert begin|end date for allocation(s)`,
> `Transfer requires one source resource (negative amount)`,
> `No FieldOfScience (fos) objects`, `No AllocationType for SelectionParms{…}`). The
> single end-date row is also two different validators on two different handler paths.
>
> The verified table, with every emitter at `file:line`, is
> [`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md) § *The error vocabulary*; it is implemented in
> `src/sam/xras/errors.py` and pinned byte-for-byte by `tests/unit/test_xras_errors.py`.
> Kept below as the historical record of what was inferred before the source was read.

`Missing title` · `Missing pi role` · `PI %s is not in database` · `PI %s is not an active user: ` ·
`Allocation Manager %s is not in database: ` · `Allocation Manager %s is not active ` ·
`Username %s is missing` · `Username %s is inactive` ·
`No resource found in SAM corresponding to name %s` · `No resource found in SAM corresponding to key %s` ·
`Awarded amount missing` · `Could not convert awarded amount "…" to float` ·
`Missing begin/end date for allocation(s)` · `Action end date before existing allocation end date for %s` ·
`All contract and allocation end dates are null or past for project [%s]` ·
`Transfer supports only one source (negative amount)` · `Transfer requires at least one destination resource (positive amount)` ·
`Transfer source project:resource (%s:%s) has no allocation` ·
`Transfer destination credit (%f) exceeds source allowed debit (%f)` ·
`Cannot find contract for grant number "%s" ("%s")` ·
`Could not determine Mnemonic code for {internal PI via organization | external PI via institution}` ·
`Could not produce affiliation data for PI %s` · `AreaOfInterest (FOS) id is not in database: %s`

### 3.5 How the project roster is built — `roles[]` is roleType-agnostic

The full membership of a new project arrives in **one place**: the `roles[]` array of
`POST /actions`. Endpoint #7 plays no part (§2.1). Two *different* readings of that same array run,
and confusing them is the easiest way to get this wrong:

| Reading | Method | Filter | Result |
|---|---|---|---|
| **Role assignment** | `getPiUsername()` / `getAllocationManagerUsername()` | `roleType` **must** equal `PI` or `Allocation Manager`, plus a date window | project **lead** / project **admin** |
| **Roster** | `getUsernames()` | **`roleType` is never examined** — date window only | **every** entry becomes a project member |

`ActionRoleName` contains exactly two constants, `PI` and `ALLOCATION_MANAGER`. So a `Co-PI`, a
`User`, or any unrecognised `roleType` is invisible to *role assignment* but is **still added to the
project** — which is how XRAS delivers a lead + admin + N ordinary members in a single New action,
as production does today.

`AddUserToProjectActionCommandsFactory.create()` then fans the roster out **per resource** — one
`AddUserToProjectCommand` for each entry in `resources[]`, each carrying every username. Invalid
members are reported but do **not** abort: `reportInvalidUsernames()` emits `Username %s is missing`
(no such user) or `Username %s is inactive` for each, and these accumulate through the observer like
every other assembly error (§3.1).

⚠️ **The two date filters are not the same, and the difference is a latent legacy bug.**

```java
// roster — getUsernames()
if (roleBeginDate.compareTo(actionDate) > 0) continue;              // strictly excluded

// role assignment — getUsernameByRoleType()
if (roleBeginDate > actionDate && currDate <= roleBeginDate && currDate <= actionDate)
    continue;                                                        // excluded only if ALSO future
```

A role whose `beginDate` is after `actionBeginDate` **but has since started** (`currDate >
roleBeginDate`) is accepted as PI or Allocation Manager yet is **excluded from the roster** — so
legacy makes that person the project lead without giving them an account on any resource. Reporting
it as a warning is better than either silently copying it or silently fixing it (§9).

Both filters compare dates with **lexicographic `String.compareTo`**, which is correct only for
zero-padded ISO-8601 — one of the open questions Phase 5.1 resolves from real payloads.

---

## 4. Data and performance constraints

### 4.1 Production data facts

| Fact | Measured | Consequence |
|---|---|---|
| `xras_user` has no active/deleted filter (only `login_type_id = 1`) | 28,253 rows, **22,039 inactive** | `/people` publishes every user who ever existed — reproduced bug-for-bug (§7) |
| `organization` null rate in `xras_user` | **79%** (22,311 rows) | downstream of the frozen `user_organization` |
| rows needing the `UCAR/NCAR:` fixup | 1,760 | port the parentage walk faithfully (§3.3) |
| **`user_organization` is frozen** | no rows created since 2026-07-09; **4,563** active users have no current org; **2,092** rows point at a dangling `organization_id = 0` | root cause of 24% of failures. Out of scope to fix, but the port must report it as a reviewable 422, not an opaque 500 |
| **Contract suffix collisions are live** | 3 cores collide today: `1049089` (`1049089` \| `PLR-1049089`), `1744587` (`OPP-` \| `PLR-`), `2146709` (`2146709` \| `AGS-2146709`) | legacy's `LIKE '%core'` + `uniqueResult()` guarantees `NonUniqueResultException` → 500 for any grant citing these. Resolve deterministically: exact match, then unique suffix, else report |
| `allocation_type` has duplicate names | `Small` ×2, `Education` ×2 | resolve by `(panel, type)` |
| `xras_resource_repository_key_resource` | **13 rows**, 6 of them pointing at decommissioned kit (Yellowstone, Janus, Geyser_Caldera, HPSS, GLADE fs1, Cheyenne) | **11 active SAM resources have no mapping row**: Boreas, Destor, GLADE user, GLADE work, Gust, Gust GPU, hpc, hpc-dev, HPC_Futures_Lab, Laramie, Quasar. An award citing one fails with `No resource found in SAM corresponding to key %s` — see §9 |
| `fos_aoi` (FOS → AreaOfInterest) | exists in prod, **18 rows** | ⚠️ **"Prefer `fos_aoi`" was wrong and is retired** (Sprint C). The two id spaces are disjoint: `fos_aoi.fos_id` holds 5-digit AMIE/XSEDE codes (`10202`, `10501`, …) while XRAS sends `1`–`40`, which is the `area_of_interest` **primary key** space. Legacy's `findOne(fosInt)` is a PK lookup and it is correct — every corpus payload's primary `fosNum` equals the `area_of_interest_id` its real project carries, and the `fosName` XRAS sends is SAM's `area_of_interest` string verbatim. Routing through `fos_aoi` would file every XRAS project under the wrong research area, silently. `FosAoi` is the AMIE path; leave it alone |
| **GID allocation is live in legacy** | pool `99000–99999`, `nextGid = 99025`; `modified_time` matches the 2026-08-05 09:58:49 XRAS post to the second | legacy allocates GIDs locally for XRAS projects (since 2026-07-16, `UMIT0083` = 99001). **`project.unix_gid` is NULL for 0 of 5,795 rows** — never leave it NULL |
| XRAS-created projects arrive `active = 0` | 21 of 23 have since been activated by hand | by design (`InactivateNewProject`); the success email is the human trigger |
| XRAS allocation transactions | `user_id IS NULL`; comment `XrasAction Extension Request` (current) / `XRAS Extension Request` (pre-2025-10) | the actor convention to preserve — see Phase 3 |
| Production `sql_mode` | `STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION` — **no `ONLY_FULL_GROUP_BY`** | views that work in prod can fail in the dev/test DB, which enable it |
| Email recipients | `xras.actionpost.recipients=hdt@ucar.edu` (`2.0.3:app/env/sam.complete.properties:29`) | the deployed `var/sam.complete.properties` is 0600 and unreadable |
| Feature flag | `XRAS_POST_ACTION=true` (`/tomcat/tomcat-sam/var/features.properties`) | — |

### 4.2 The XRAS views are not usable as-is

Measured server-side against production with `SHOW PROFILES`:

| Query | Duration |
|---|---:|
| `SELECT COUNT(*) FROM xras_user` (28,253 rows) | 0.96 s |
| **`xras_user WHERE username = 'benkirk'`** | **0.91 s** |
| `SELECT COUNT(*) FROM xras_allocation` | 7.66 s |
| **`xras_allocation WHERE projectId = 'UUSL0047'`** | **6.41 s** |
| `xras_request WHERE projectId = 'UUSL0047'` | 0.0007 s |
| `xras_action WHERE projectId = 'UUSL0047'` | 0.0007 s |

**Port the Hibernate named queries against base tables, not the ORM's view models.** Three
independent reasons:

1. **`xras_user` does not push down a username predicate.** Its `GROUP BY u.user_id` materialises all
   28k rows for every single-user lookup. Legacy is far faster end-to-end (95 ms p50) because
   `IdentityServiceImpl` uses the named query `identityServicePersons`
   (`2.0.3:src/main/resources/hibernate/xras/namedQuery.xml:7-60`), **not the view**. Measured on the
   local dev DB:

   | Query | Duration |
   |---|---:|
   | `SELECT * FROM xras_user WHERE username='benkirk'` | **0.409 s** |
   | the identical SQL with the predicate applied *inside* the grouped query | **0.0007 s** |

   a **560×** difference, from one predicate moving across a `GROUP BY`.

2. **The named query and the view are not equivalent.** This is easy to miss, because the ORM exposes
   the *view*, so the path of least resistance is the wrong one:

   | | `identityServicePersons` (what legacy serves) | `xras_user` view |
   |---|---|---|
   | username predicate | built in: `(:username IS NULL OR username = :username)` | none — callers filter on top |
   | `email` | `ANY_VALUE(COALESCE(ea1, ea2, ea3, ea4))` — a **per-row** coalesce over the four join aliases | `COALESCE(MIN(ea1), MIN(ea2), MIN(ea3), MIN(ea4))` — a **per-tier** coalesce |
   | `GROUP BY` | `u.username, firstName, u.middle_name, u.last_name, ac.description` | `u.user_id` |

   For a user with several addresses the two email expressions can select **different values**.
   Porting the view would ship a silent data divergence that byte parity catches only if that user
   happens to be in the sample.

3. **`xras_allocation` costs 6–8 s regardless of filter**, because `xras_hpc_allocation_amount`
   aggregates `hpc_charge_summary` across *all* allocations before joining. This is why the single
   `requests/request/{n}` call in the 30-day corpus took 7.7 s. Scoping that aggregate to the
   requested projects is the single largest win available, and it changes no output bytes.

**`xras_request` additionally fails under `ONLY_FULL_GROUP_BY`** (error 1055), which the dev and CI
databases enable and production does not. The SELECT list is safe — `GROUP BY p.projcode` is
functionally determining via the `project_projcode_uk` unique index. The sole offender is
`ORDER BY al.end_date`, which names a different expression from the `GROUP BY`'s
`cast(al.end_date as date)`. **Do not "fix" the view by dropping that `ORDER BY`** — it is
load-bearing (§2.3). Ordering by the grouping expression itself is both legal and equivalent, which
is what the port does. Repairing the view proper is a deferred follow-up (§6, Phase 1 deferred).

### 4.3 Shared database ⇒ a reversible cutover, but no A/B

`.env.example:14` sets `PROD_SAM_DB_SERVER=sam-sql.ucar.edu`: **both applications read and write the
same production database.** Two consequences pull in opposite directions, and both matter:

- **Cutover is reversible with no data migration.** Whichever stack serves a request, it reads and
  writes the same rows, so pointing traffic back at legacy leaves nothing stranded.
- **A live A/B parity run is impossible.** Running both stacks against the same action would apply
  it *twice*. So the write path can only be verified by replaying a corpus against a test database
  and diffing the result (§ 6 Phase 5 item 6) — never by comparing two live stacks, the way the
  GET surface is compared.

⚠️ This section previously concluded that a **per-endpoint proxy cutover** was available, on the
grounds that `sam.ucar.edu` (128.117.225.232) is fronted by `prod-staticweb14/15.ucar.edu`, which
can split on path prefix. **That is not the mechanism being used.** The new stack is a separate
host — `sam.hpc.ucar.edu` — and cutover is XRAS repointing one base URL, which moves every
endpoint at once. See § 6 Phase 5 item 5.

### 4.4 Parity oracles

**Access-log oracle — 30 days of real legacy output, no credential required.** `%b` and `%D` are
recorded, so we have the byte count of every response legacy served in the window:

| Oracle | Size | Strength |
|---|---|---|
| `GET /people/{u}` 200s | **385 usernames with a single stable byte count** over 30 days (29 more have two — their DB rows changed mid-window) | strong: any null-omission, org-fixup or missing-field bug shifts the count |
| `GET /people/{u}` 404s | **563 distinct usernames**; size is the closed form `len(username) + 47` | total; assert the formula, no fixture needed |
| `GET /people` roster | **30 nightly points**, 3,807,879 → 3,839,790 B, rising ~1.3 KB/day | strong single-number regression check |
| `GET /requests/request/{n}` | 1 point, ever | negligible |
| `requests/user`, `requests/role`, `dates/requests` | **0** points | none |

Corpus: 3,268 request records, 413 distinct 200-path usernames, 563 404-path usernames.

**Credentialed oracle.** Four of the six GET endpoints had no production oracle at all — the decisive
reason to provision a credential. Byte-exact comparison is also the only way to catch a
length-preserving bug: swapped `firstName`/`lastName`, wrong field order, `"%.1f"` drift.

Measured with `samuel` against `https://sam.ucar.edu` from a workstation (timings include network
RTT):

| Endpoint | Status | Bytes | Elapsed |
|---|---:|---:|---:|
| `/people/{u}` (`benkirk`) | 200 | 170 | 0.50 s |
| `/people/{u}` (miss) | 404 | `len(u) + 47` | 0.45 s |
| `/people` roster | 200 | **3,837,666** | 2.03 s |
| `/requests/request/{n}` | 200 | 899 – 8,768 | **6.1 – 7.3 s** |
| `/requests/request/` unknown | 200 | 62 | 0.35 s |
| `/requests/user/{u}` | 200 | 89,863 | 6.13 s |
| `/requests/role/pi/{u}` | 200 | 89,049 | 6.21 s |
| `/requests/role/co_pi/{u}` | 200 | 62 (empty) | 0.40 s |
| `/requests/role/allocation_manager/{u}` | 200 | 31,110 | 6.10 s |
| `/requests/role/{bogus}/{u}` | **500** | 101 | 0.39 s |
| `/dates/requests/{n}` | 200 | 120 / 213 | 0.4 s |

**The `requests/*` family costs 6–7 s per call** — direct confirmation of §4.2's finding. Any port
that reads those views naively inherits it; the ~1 call/month traffic is the only reason it has
never mattered.

**Roster corroboration** (28,259 people, one call): `organization` present on **21.0%** — the exact
complement of §4.1's measured 79% null rate. Field order is a strict subsequence of the `PersonDTO`
declaration order in **all 28,259** records, across 19 distinct observed key orders — so a fixed
field order plus null-dropping reproduces the bytes. 1,753 rows carry the `UCAR/NCAR:` fixup and
**zero** raw `UCAR/NCAR:` strings survive, so the fixup is applied unconditionally.

**Credential facts**, verified against production:

- `api_credentials`: `api_credentials_id` int unsigned **auto_increment**; `username`
  **varchar(11)** UNIQUE (`idx_api_credentials_uniq`); `password` char(64) holding a **60-char
  `$2a$` bcrypt**; `enabled` tinyint.
- `role_api_credentials`: auto_increment PK, UNIQUE `(role_id, api_credentials_id)`, FKs to both.
  `ROLE_XRAS` is `role_id = 10` **in production** — an environment-specific auto-increment, so
  resolve it by name, never by literal.
- **No credential caching.** `<security:jdbc-user-service>` declares no `cache-ref`, so `JdbcDaoImpl`
  uses `NullUserCache`: **a new row is live on the very next request, no Tomcat restart.** The Python
  side caches for `API_KEYS_DB_TTL` (default 60 s).
- The provider is the second `<security:authentication-provider>` in
  `2.0.3:src/main/resources/spring/auth-saml-config.xml:36-50` (mirrored in
  `auth-backdoor-config.xml:34-48`), with `<security:password-encoder hash="bcrypt"/>` at `:48`:
  ```sql
  -- users-by-username-query
  SELECT username, password, enabled FROM api_credentials WHERE username=?
  -- authorities-by-username-query  (returns role.name verbatim, no ROLE_ prefix added)
  SELECT a.username, r.name FROM role r
    JOIN role_api_credentials ra ON r.role_id = ra.role_id
    JOIN api_credentials a ON a.api_credentials_id = ra.api_credentials_id
   WHERE a.username=?
  ```
  `enabled` must be truthy or `JdbcDaoImpl` rejects the account before the password is checked.
- Both `$2a$` and `$2b$` verify — the deployed stack is Spring Security **5.8.12**
  (`2.0.3:pom.xml:61`), whose `BCryptPasswordEncoder.matches()` accepts `$2(a|y|b)?$`. Prefer `$2a$`
  for `api_credentials` rows purely for consistency with the existing rows;
  `scripts/gen_api_key.py --prefix {2a,2b}` selects it, and `--sql` prints the provisioning
  statements with the hash substituted and both PKs resolved at runtime.

---

## 5. Building blocks in Python SAM

Reuse these; do not rebuild.

**ORM / views** — `XrasResourceRepositoryKeyResource` (`src/sam/integration/xras.py:9`); six view
models in `src/sam/integration/xras_views.py`, exported from `src/sam/__init__.py`. Smoke tests at
`tests/integration/test_views.py`. ⚠️ Per §4.2 the *view models* are not the right source for the
read endpoints; `XrasResourceRepositoryKeyResource` (a real table) is.

**API recipe** — `src/webapp/api/v1/queue.py`, `wallclock_exemption.py`: module docstring naming the
legacy endpoint, `bp = Blueprint(...)`, `@bp.route` → auth decorator → `@cache.cached(...)`, query
logic in `sam/queries/*`. Registered in `src/webapp/run.py`. These are **legacy-compat blueprints**
under `CLAUDE.md` — "DO NOT REFACTOR, response bytes must not change". The XRAS blueprint joins that
class and its module docstring says so.

⚠️ `register_error_handlers` (`src/webapp/api/helpers.py`) emits `{'error': …}` and registers no 422
or 500 handler, so XRAS uses blueprint-local handlers instead. Likewise `_auth_challenge`
(`api_auth.py:48`) emits `{'error': …}` **with** `WWW-Authenticate`, which the legacy XRAS 401
deliberately omits.

**Manage ops** — `management_transaction` (`src/sam/manage/transaction.py:12`),
`log_allocation_transaction` (`src/sam/manage/allocations.py:69`), `create_allocation` (`:197`),
`update_allocation` (`:271`), `exchange_allocations` (`:416`),
`extend_project_allocations` (`src/sam/manage/extend.py:40`),
`renew_project_allocations` (`src/sam/manage/renew.py:260`),
`add_user_to_project` (`src/sam/manage/__init__.py:53`), `change_project_admin` (`:179`).
⚠️ `create_allocation`, `exchange_allocations`, `extend_project_allocations` and
`renew_project_allocations` are **not** re-exported from `sam.manage` — import from the submodules.

Two of these do not mean what an XRAS handler needs:

- `exchange_allocations` is strictly **1 source → 1 destination**, requires the **same resource**
  (not the same project), and **raises** when `amount > source.amount` rather than clamping. Legacy
  Transfer allows multiple destinations and clamps.
- `extend_project_allocations` is **project-tree-scoped** (not per active account) and **silently
  skips** shrinks, open-ended and inheriting allocations. Legacy Extension errors on a shrink.

**Projects / lookups** — `Project.create` (`src/sam/projects/projects.py:233`),
`next_projcode` (`:1698`), `GidAllocation.allocate_next_gid` (`src/sam/core/groups.py:292`),
`MnemonicCode.build_lookup/resolve_for_institution/resolve_for_organization`
(`src/sam/core/organizations.py:445/461/481`), `Contract.existing_by_number`
(`src/sam/projects/contracts.py:249` — bulk exact-match, the right primitive for grant resolution),
`ProjectContract.create` (`:468`), `FosAoi` (`src/sam/projects/areas.py:175`), `AllocationType`
(`src/sam/accounting/allocations.py:483`).

**A working reference for the New handler already exists:**
`src/webapp/dashboards/admin/projects_routes.py:600-687` performs, inside one
`management_transaction`, exactly the sequence the New handler needs —
`next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` →
`ProjectContract.create` → `ProjectOrganization.create`. **Port against that, not from scratch.**

**Allocations dashboard** — blueprint `allocations_dashboard`, `url_prefix='/allocations'`
(`src/webapp/dashboards/allocations/blueprint.py:46`). Unlike the admin dashboard it has **no
sub-route modules** — all 1,132 lines are in one `blueprint.py`. Three tabs today: Projects,
Transactions, Adjustments. The tab strip is the shared `page_tabs` macro driven by a literal list in
`templates/dashboards/allocations/base_allocations.html:21-25`; tabs are real routed `<a href>`s. A
**parallel** nav registry lives at `src/webapp/utils/nav.py:145-159`, maintained separately.

**Email** — there is none in the webapp: zero `MAIL_*` / `flask_mail` / `smtplib` hits under
`src/webapp/` or `src/sam/`. The only mailer is `src/cli/notifications/email.py` (stdlib `smtplib` +
Jinja2, with a hardcoded `Bcc: benkirk@ucar.edu` at `:127,:138`).

**Testing** — `tests/factories/security.py:make_api_credentials(..., roles=())` builds `Role` +
`RoleApiCredentials`. ⚠️ A factory row is **invisible to an HTTP request**: routes read
Flask-SQLAlchemy's `db.session` on a separate connection that only sees committed rows. For
auth tests, monkeypatch `api_auth._get_db_api_keys` instead — see
`tests/api/test_api_credentials_auth.py`. Adding an ORM model to `src/sam/__init__.py`
**auto-registers a Flask-Admin view**.

---

## 6. Implementation

Phases are ordered by **production volume × failure rate**.

### Phase 0 — Prerequisites

1. ⚠️ **Create `xras_action_log`, dev first, production later.** Dev and CI ✅ (a tracked,
   self-retiring `initdb.d` script rather than the by-hand `CREATE TABLE` this sequence
   assumed); **production still open, and it is the one item on external lead time.**
   The database is the schema source of
   truth and the ORM follows it — but that does **not** mean production must be first, and it
   *cannot* be: the prod writer account holds `SELECT, INSERT, UPDATE, DELETE` and **no DDL**
   (`scripts/repair/RUNBOOK-missing-projects.md:36-38`), so a `CREATE TABLE` there is a DBA request
   with its own lead time. Sequencing Phase 2 behind that ticket buys nothing, because the table's
   shape is the thing under design.

   Sequence: agree the DDL → **`CREATE TABLE` by hand on the local dev DB** → add the model to
   `src/sam/integration/xras.py`, export from `src/sam/__init__.py` → add a
   `tests/integration/test_schema_validation.py` case → **then** raise the prod DDL request and
   backfill → add a PII scrubbing rule to `containers/sam-sql-dev/anonymize_sam_db.py` → regenerate
   `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz` so CI has the table.

   ⚠️ The scrubbing rule must land **before** the next snapshot regeneration — `raw_payload` carries
   PII — and regenerating that blob has its own blast radius on fixture-dependent tests. (The rule
   is written: `purge_xras_action_log` in `anonymize_sam_db.py` **purges** rather than scrubs, since
   a verbatim POST body cannot be safely obfuscated field-by-field.)

   ⚠️ **The ticket carries two tables, not one.** Sprint B added `xras_activation_event`
   (`zz-91-xras_activation_event.sql`) and deliberately settled its DDL before filing, because a
   second request costs another round of the same lead time. Staging needs both run by hand once —
   `infrastructure/scripts/init-rds.sh` restores the raw `.xz` with no initdb hook.

2. ☐ **SMTP from the k8s webapp.** Lift `EmailNotificationService` into `src/sam/notifications/`,
   drop the hardcoded `Bcc`, and give the webapp `MAIL_*` config — or accept DB-only audit for v1 and
   add email later. This is a move plus a config wire-up, not a build: `src/config.py:32,37` already
   defines `MAIL_SERVER` (default `ndir.ucar.edu`) and `MAIL_DEFAULT_FROM` (default
   `sam-admin@ucar.edu`), with `.env` populating both, and `src/cli/notifications/email.py` is stdlib
   `smtplib` + Jinja2 with no Flask coupling. Legacy sends ~3 emails per action (`XrasActionLogger`
   lacks `additivity="false"`, so every event also reaches the root `SMTPAppender`); **we send one.**

3. ✅ **Role enforcement — `login_or_token_required(roles=, deny=)`.** Two keyword-only, defaulted,
   purely additive parameters on the shared decorator:

   ```python
   def login_or_token_required(permission=None, *, roles=None, deny=None):
       """
       roles: iterable of API-key role names (api_credentials → role_api_credentials).
              The token caller must hold at least one. When given, the SESSION path is
              closed — a browser session has no API-key roles by definition.
       deny:  optional callable(status, message) -> response, for blueprints whose error
              bodies are part of a fixed legacy wire contract. Defaults to today's exact
              JSON shapes, so all existing call sites are byte-unchanged.
       """
   ```

   `roles` closes an authz gap the module had been advertising: `g.api_key_roles` was populated but
   never enforced, and all 20 existing call sites pass only the positional `permission`, so the new
   parameters are invisible to them. `deny` exists because XRAS's denial *bodies* are contract — its
   401 is a byte-exact 41-byte literal with `charset=UTF-8` and **no** `WWW-Authenticate`, where
   `_auth_challenge` emits `{'error': …}` *with* that header. (Making the decorator `abort()` so
   blueprint error handlers render the body would change the 401 bytes for the existing legacy-compat
   blueprints, which `CLAUDE.md` forbids.)

   XRAS then needs no auth logic of its own, only an alias:
   ```python
   xras_api_required = partial(login_or_token_required,
                               roles=('ROLE_XRAS',), deny=_xras_deny)
   ```

   `permission` still does not apply to token callers — unchanged and out of scope; `roles` is the
   token-path analogue, not a fix for that.

4. ✅ **XRAS credential — username `samuel`** (`api_credentials_id = 14`, `ROLE_XRAS`, enabled,
   `$2a$12$`, provisioned 2026-08-05). One row in each table. This is the credential the Python app
   authenticates with at cutover, and the parity harness's credential in the meantime. Generate it
   and the SQL together:
   ```bash
   python scripts/gen_api_key.py --username samuel --rounds 12 --prefix 2a --sql
   ```
   which emits, with the hash substituted and both PKs resolved at runtime:
   ```sql
   START TRANSACTION;
   INSERT INTO api_credentials (username, password, enabled)
   VALUES ('samuel', '<$2a$12$… 60 chars>', 1);
   INSERT INTO role_api_credentials (role_id, api_credentials_id)
     SELECT r.role_id, ac.api_credentials_id FROM role r, api_credentials ac
      WHERE r.name = 'ROLE_XRAS' AND ac.username = 'samuel';
   -- verify one row, correct role, enabled — then COMMIT (else ROLLBACK)
   SELECT ac.api_credentials_id, ac.username, ac.enabled, r.name FROM api_credentials ac
     JOIN role_api_credentials rac USING (api_credentials_id)
     JOIN role r USING (role_id) WHERE ac.username = 'samuel';
   COMMIT;
   ```
   Requires a **writer** account: the `.env` `PROD_SAM_DB_*` credential is `hpc-reader`
   (`SELECT, SHOW VIEW` only). Pass the password inline with `-p`; `~/.my.cnf` overrides `MYSQL_PWD`.
   No restart needed. Verify against `https://sam.ucar.edu` — expect **200**, not 403.

   The same row is seeded in the local dev DB (a dev seed, not a migration — the next snapshot
   restore wipes it). Rollback is two `DELETE`s, child row first.

   ⚠️ **`ROLE_XRAS` also permits `POST /actions`** — the security chain makes no method distinction.
   Treat the secret as a production *write* credential, and keep the `.env` holding it at mode 600.

5. ✅ **Add `Permission.MANAGE_XRAS`** — shipped in Sprint B, as a **pair**: `VIEW_XRAS` (the page,
   table, filters and error lists; swept into `ALL_VIEW`) and `MANAGE_XRAS` (the raw payload panel,
   replay, and every worklist action; auto-granted to nobody, so added explicitly to
   `_ALLOCATION_ADMIN`). The split is on *what the data is*, not read-vs-write, and neither is in
   `USER_FACILITY_PERMISSIONS` — an XRAS action is not facility-scopable. The original note:
   - Add `MANAGE_XRAS = "manage_xras"` to the `Permission` enum's "System administration" block,
     alongside `MANAGE_ROLES` / `MANAGE_SYSTEM_STATUS`.
   - ⚠️ **It is auto-granted to nobody.** `ALL_VIEW`/`ALL_EDIT`/`ALL_CREATE`/`ALL_DELETE` are built by
     `_perms_with_action('view'|'edit'|'create'|'delete')`, and `manage_` matches none of them. Add it
     **explicitly** to `_ALLOCATION_ADMIN` (used by both the `nusd` and `csg` bundles), or the tab's
     actions are invisible to everyone except `SYSTEM_ADMIN` holders and `USER_PERMISSION_OVERRIDES`
     entries.
   - Update `tests/unit/test_rbac.py` and any bundle-membership assertions.

### Phase 1 — Read endpoints ✅ DONE

94% of traffic, zero write risk. All six GETs, on branch `xras_reimplementation` (PR #424).

**As built**

| Where | What |
|---|---|
| `src/sam/queries/xras_access.py` | the five named queries, ported to base tables; the `UCAR/NCAR:` org fixup |
| `src/webapp/api/xras/__init__.py` | blueprint, XA-header shim, `xras_api_required`, error handlers |
| `src/webapp/api/xras/serialize.py` | the wire format — the single place bytes are decided |
| `src/webapp/api/xras/people.py` | endpoints 1–2 |
| `src/webapp/api/xras/requests.py` | endpoints 3–6 (the `RequestFactory` port) |
| `tests/api/test_xras_access.py` | 64 tests |

**Serialization.** `jsonify` cannot be used: Flask 3.1.3's `DefaultJSONProvider` sorts keys
alphabetically, appends a trailing `\n`, and picks separators from `app.debug` — so
`DevelopmentConfig` and `ProductionConfig` emit different bytes from the same call. One helper owns
the format (`json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=False)`), and
the envelope is a **flag** on it, so `/people`'s bare shape is `envelope=False` rather than a
separate code path — which makes re-standardising the outliers later a one-argument change. Raw
UTF-8, not `\uXXXX`: the roster carries 78 non-ASCII bytes and zero escapes. Null omission is
applied **per DTO**, never globally (§2.3).

**Query design.** One builder with an optional username predicate serves both `/people` endpoints, so
the roster and the single lookup share a rendering path by construction. `remainingAmount` is a
project-scoped re-implementation of `xras_hpc_allocation_amount` rather than a read of the view.
Every `ORDER BY` carries a primary-key tiebreaker, because legacy's are not total orders (§7 #6).

**Verified** against the captured production corpus: per-master structure identical **6/6** and
**5/5** for `requests/user` and `requests/role/pi` — every key name, key order and value type at
every nesting depth — and `requestType` agrees on **54/54, 53/53 and 16/16** requests. `co_pi`,
unknown-request and both `dates/requests` bodies match production's byte counts exactly. The only
structural difference is the declared `masters[]` ordering divergence.

**Latency**, against legacy's measured production numbers:

| Endpoint | This port | Legacy |
|---|---:|---:|
| `/people/{u}` warm | **3.4–4.5 ms** | 95 ms p50 |
| `/people` roster (~3.84 MB) | **637 ms** | 1,123 ms p50 |
| `/requests/request/{n}` | **29 ms** | 6,100–7,300 ms |
| `/requests/user/{u}` | **18 ms** | 6,130 ms |
| `/dates/requests/{n}` | **2 ms** | ~400 ms |

**Deferred out of Phase 1: repairing the `xras_request` view.** Phase 1 ports the named queries
against base tables, so `ONLY_FULL_GROUP_BY` never bites us, and the `ORDER BY` a naive fix would
remove is load-bearing (§2.3). Doing it properly means three unrelated lead-times: a **DBA request**
on production (the writer account has no DDL), plus a CI-snapshot regeneration before
`tests/integration/test_views.py:95-111` can be un-skipped — with the blast radius that carries on
fixture-dependent tests. Three lead-times to un-skip a test guarding code we do not use. Tracked as
NRIT P2-63.

### Phase 2 — Action ingestion + audit trail ✅ DONE (capture-only)

> **Sprint A.** The as-built record — the measured wire contract, the DDL, how the new table
> reaches dev *and* CI without regenerating the LFS snapshot, and the capture-first running
> order — is in [`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md). The original handoff is retired to
> [`implemented/XRAS_ACTION_INGESTION.md`](implemented/XRAS_ACTION_INGESTION.md). What follows
> is the contract summary.

1. **`xras_action_log`**: `id`, `received_time`, `remote_actor`, `action_type`, `request_number`,
   `raw_payload`, `status` (`processed|manual|failed|replayed`), `error_messages`, `projcode_result`,
   `processed_time`, `processed_by`. Payloads carry PII — the Phase 0 scrubbing rule must land before
   any snapshot regeneration.
2. **`src/sam/schemas/forms/xras.py`** — `XrasActionSchema` plus nested
   Resource/Role/Person/Fos/Panel/Grant schemas with the §2.4 tolerances: `unknown=EXCLUDE`, absent
   strings → `""`, number-into-string coercion, and the forgiving boolean for `isAccountToBeCreated`
   only. Export from `forms/__init__.py`.
3. **`POST /v1/actions`** — parse (400 on malformed JSON) → **persist the log row before dispatch** →
   dispatch → 200 / 422 with the real error list / 500. Every inbound action is persisted regardless
   of outcome; that is what makes replay possible.

### Phase 3 — Handlers, in production-frequency order ☐

> **Sprint C** — the cold-start handoff is [`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md); this section is
> the contract it implements. The order is easy-path-first
> (Extension → Supplement → Adjustment → Update → **New last**) so the pipeline is proven before
> the 30%-success path is attempted. The eight harvested payloads cover **New at both outcomes,
> Extension at both outcomes, Supplement ×2 and Adjustment ×1**, so no handler is sample-blocked.
> Two caveats on that order: "Update" is `New`/`Renewal` against an existing project, so it and
> New are one dispatch decision rather than two; and Adjustment has no known-good production
> outcome to compare against, because legacy has never once serviced one (defect 4, § 9).

All inside `management_transaction`; every allocation mutation through `log_allocation_transaction`.

**The actor question, settled.** Legacy writes `allocation_transaction.user_id = NULL` for XRAS.
`log_allocation_transaction` (`src/sam/manage/allocations.py:69`) declares `user_id: int`
positionally, which looks like a blocker — but the column is **nullable**
(`src/sam/accounting/allocations.py:232`) and nothing in the body validates or dereferences it, so
passing `None` writes `NULL` today and matches legacy exactly. Widen the hint to `Optional[int]`,
document that `None` means an integration actor, and settle it before the first handler: every
handler and every parity diff against legacy rows depends on it.

⚠️ `management_transaction` does **no** implicit audit logging — it is commit-on-success /
rollback-on-error and nothing else. Audit rows exist because manage functions *explicitly* call
`log_allocation_transaction`; the context manager only makes the write and its audit row atomic.

1. **Extension (60% of posts, 98.5% success)** — build first, on the easy path. Extend the latest
   allocation of every **active account** to `actionEndDate`, erroring if that would shrink any;
   payload resources are ignored. `extend_project_allocations` is tree-scoped and skips shrinks
   silently (§5), so add an account-scoped variant or a strict mode. Expect ~3.3 allocations per
   action. Use comment `XrasAction Extension Request`.
2. **New (21% of posts, 30% success)** — the failure hot spot. Port against
   `projects_routes.py:600-687`: allocation-type extractor → mnemonic → AOI →
   `next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` → contracts →
   `create_allocation` per resource (start clamped ≥ commission date, end-of-day end) →
   `add_user_to_project` (**after** accounts exist — it raises otherwise) → set `active=False`.
   - Allocation type: transcribe §3.2 verbatim into a data-driven rule table resolving to
     `(panel, type)` pairs.
   - Mnemonic: reuse `MnemonicCode.resolve_for_institution/organization`. Surface failures as
     structured 422 errors, never an opaque 500.
   - Contracts: use `Contract.existing_by_number` with an explicit policy for the three known
     ambiguous cores (§4.1). Legacy hard-fails where AMIE parks a human task — treat an unresolvable
     grant as a **reviewable warning**, not a fatal error.
3. **Supplement (15%, 100% success)** — create the allocation if none exists (start today, end =
   latest contract/allocation end), else supplement when `> 0`; log-warn on `≤ 0` rather than
   dropping silently.
4. **Update path (New/Renewal on an existing project, 3%)** — field updates, contracts, per-resource
   create/extend/supplement/adjust, and the `AUTO_DEFAULT_ALLOCATION_TRANSACTION` undo kludge
   (compensating `UNDO AUTO/DEFAULT` adjustment; 33 such rows in the last two years). Must tolerate
   the **no-op case** — 2 of 109 successful posts changed nothing.
5. **Adjust** — a Supplement variant reusing the same primitives, so the marginal cost is small and it
   closes a spec obligation. Log-warn and record negatives rather than dropping them.
6. **Transfer** — route to the manual-fallback path with an explicit audit row and email (§7). Its
   semantics, for whenever it is built: 1 negative source + ≥1 positive destinations, same project,
   Σ = 0, source clamped to available.

### Phase 4 — XRAS as the 4th Allocations tab ✅ DONE

> **Sprint B.** The as-built record — 12 routes, the replay interlock, the `VIEW_XRAS`/`MANAGE_XRAS`
> split, `xras_activation_event` and the pending-activation worklist, and 11 numbered deviations
> from this section — is [`XRAS_SPRINT_B.md`](XRAS_SPRINT_B.md). What follows is the design it was
> built from, kept because its "six places a 4th tab touches" list is the reusable part.

Ship the free Flask-Admin view first as a stopgap. Then the real surface: a **4th tab on the
Allocations dashboard**, beside Transactions and Adjustments — an XRAS action *is* an allocation
transaction, and those tabs display the very rows it produces.

**Copy the Transactions tab wholesale.** Same shape (append-only audit rows, filter bar, sortable
paginated table, click-row-for-detail modal), and it will be the direct neighbour:

| Concern | Follow | At |
|---|---|---|
| page route | `transactions()` | `src/webapp/dashboards/allocations/blueprint.py:284-292` |
| shared page context | `_audit_page_context()` | `blueprint.py:254-281` |
| filter/sort/page parsing | `_parse_audit_filters(args, sort_whitelist)` | `blueprint.py:719-784` |
| lazy fragment route | `transactions_fragment()` | `blueprint.py:788-813` |
| detail route | `transaction_details()` | `blueprint.py:856-883` |
| filter bar / pagination / sort | `audit_filters`, `pagination`, `sort_link` macros | `templates/dashboards/fragments/` |
| shared detail modal shell | `partials/audit_details_modal.html` | included by both audit tabs |
| permission-gated write action | Adjustments' "Create" button + POST handler | `adjustments.html:8-18`, `blueprint.py:1088-1091` |

**Six places a 4th tab touches, none optional:**

1. **Tab strip** — add an entry to `page_tabs([...])` in
   `templates/dashboards/allocations/base_allocations.html:21-25`, and update that file's docstring
   at lines 2-3, which enumerates the pages.
2. **Nav registry** — the `'allocations'` entry's `items` tuple in `src/webapp/utils/nav.py:145-159`.
   ⚠️ This list is maintained separately from the tab strip, so **both files need the entry** — the
   easy one to miss. `tests/unit/test_nav.py:44-50` fails if the endpoint isn't a real route.
3. **Routes** — page + `*_fragment` + `*_details` in `allocations/blueprint.py`. Page and read
   fragments gated `@login_required` + `@require_permission_any_facility(Permission.VIEW_PROJECTS)`,
   matching the sibling tabs; **replay** and **activate-project** gated
   `@require_permission(Permission.MANAGE_XRAS)`. Facility-scope queries with
   `apply_facility_scope(...)` and `abort(403)` on out-of-scope detail rows, as `transaction_details`
   does at `blueprint.py:874-877`.
4. **Templates** — `templates/dashboards/allocations/xras.html` extending `base_allocations.html`,
   plus `partials/xras_table.html` and `partials/xras_details_modal.html` (pretty-printed payload,
   error list, status badge).
5. **Route-map snapshot** — regenerate `tests/unit/snapshots/dashboard_route_map.json` with
   `ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`.
6. **Modal-contract fixtures** — `tests/unit/test_modal_shell_contract.py`: add `/allocations/xras` to
   `PAGES_WITH_PROJECT_MODAL` (`:302-304`), and `xras_table.html` to `HTMX_FRAGMENT_SHELL_DEPS`
   (`:220-227`) since it opens `#auditDetailsModal`/`#projectDetailsModal`. Optionally extend the e2e
   page lists (`e2e/test_console_sweep.py:86-90`, `e2e/test_dark_mode.py`).

Then `sam-admin xras` (`--list-pending`, `--replay <id>`, `--validate-mapping`) following the
three-module domain pattern in `src/cli/README.md:137-168`.

### Phase 5 — Parity and cutover

1. ⚠️ **Harvest real payloads** from the `hdt@ucar.edu` mailbox (**not** `sweg-notify`, see §2.4).
   **Eight** are in hand, scrubbed into `tests/fixtures/xras/actions/`: New ×3 (two success, one
   failure), Extension ×2 (one of each), Supplement ×2, Adjustment ×1. All three original open
   questions are **closed**: the `roleType` on stale placeholder entries is `'PI'`/`'User'` (full
   vocabulary `'PI'` / `'Allocation Manager'` / `'User'`, space separated, and *not* the
   `Pi`/`CoPi`/`AllocationManager` keys of endpoint #5); `isReconciled` is always `true` —
   including for the very identity SAM cannot find, so it must stay inert; dates are zero-padded
   ISO-8601 **date-only**, so lexicographic `String.compareTo` is safe.

   Corrected by the second batch (2026-08-07):

   - `isAccountToBeCreated` is **not** always `false` — UWIS0071 carries a `true`.
   - There is **no `actionType` of "Update"**. Legacy's vocabulary is `New, Extension, Supplement,
     Transfer, Renewal, Adjustment, Advance` (`action/domain/model/Action.java:6`), and "Update"
     is a handler selected by `(New | Renewal) && project exists`. **Supplement and Update both
     have samples now**, so neither handler is gated.
   - `allocationType` has a wider vocabulary than sampled (`Exploratory`, `Data Analysis` on top
     of `Small`/`Large`/`Educational`) and matches no `allocation_type` row in SAM. It is inert on
     this path — legacy reads it only on the GET side — so this is a trap, not a blocker.

   Still open, and the reason this is ⚠️ rather than ✅: **no co-PI has appeared** in any of the
   eight, so its spelling is still unknown, and `Transfer` / `Renewal` / `Advance` have zero
   samples. One bulk forward from hdt closes all four — ask for successes as well as failures,
   and include the manual-fallback subject (§ 1.4).

2. ✅ **GET parity harness — `--api xras`.** `XrasClient` in `clients.py` (base-URL parameterised, so
   the same class serves both stacks), `compare_xras` in `comparators.py`, plus the import block, a
   `_fetch_xras` and its dispatch branch, and **both** argparse lists in `check_legacy_apis.py` — the
   `choices` tuple and the `all` expansion tuple are separate. Env `SAM_XRAS_USER` / `SAM_XRAS_PASS`,
   optional: without them `--api all` skips xras with a message, since the `SAM_LEGACY_*` account
   cannot reach `/api/xras/*`.

   ⚠️ `_BaseClient._get` calls `resp.json()` and discards the raw bytes; byte comparison uses a
   `_get_raw` returning `resp.content`. The tolerance primitives in `helpers.py` are the wrong tool
   for the same reason — this is the strictest comparator in the harness, because XRAS has no
   DB-mirror lag (§4.3).

   The sample is bootstrapped from legacy's own output rather than hardcoded: the roster supplies a
   username and that user's requests supply projcodes. Because 22k of the roster's 28k entries are
   inactive and the roster is in user_id order, entry zero is an ancient account with nothing
   attached — the search walks newest-first for a user who has projects, and `--xras-user` overrides
   it. Budget ~2 minutes per run.

   | Endpoint | Comparison |
   |---|---|
   | `GET /people` | full-body byte equality; report size delta first, then first differing offset |
   | `GET /people/{u}` | byte equality on a live sample |
   | `GET /people/{u}` 404 | the closed form `len(username) + 47` **and** body equality |
   | `GET /requests/request/{n}` | a project sample spanning New/Renewal and HPC/non-HPC, so `remainingAmount` presence *and* omission are exercised |
   | `requests/user`, `requests/role` | byte-exact per master, order-insensitive across masters (§7 #3) and across tied allocations (§7 #6) |
   | `dates/requests` | byte equality |

3. ☐ **Zero-credential regression checks** (§4.4) — the 404 closed form, the roster byte count
   (~3.84 MB ±0.2%, +1.3 KB/day), and the 385 stable single-lookup sizes. Cheap, and they keep
   working after legacy is decommissioned.

4. ☐ **Golden corpus as pytest fixtures** — *only if the live harness proves insufficient* (§7).
   Capture real legacy bytes, then run names/emails/phones through the rules in
   `containers/sam-sql-dev/anonymize_sam_db.py` before committing. ⚠️ Scrubbing must be
   **length-preserving** wherever possible, or fixture byte counts stop matching the access-log
   oracle; where it can't be, store the pre-scrub count as a separate assertion.

5. ☐ **Cutover — one repoint, not a staged proxy split.**

   ⚠️ **This section previously described a per-endpoint cutover driven by a path-prefix split on
   `prod-staticweb14/15.ucar.edu`. That is not the mechanism.** The two stacks are separate hosts —
   legacy at `sam.ucar.edu`, this app at `sam.hpc.ucar.edu` (a CNAME to `samuel.k8s.ucar.edu`,
   same ingress and cert) — and cutover is **XRAS changing one base URL**. That has three
   consequences the staged model got wrong:

   - **The six GETs and `POST /actions` move together.** There is no step ordering to choose;
     XRAS holds one base URL, not seven.
   - **Rollback is not unilateral.** It is another repoint, which means another round-trip with
     ACCESS rather than a proxy flip we control. Budget for that when deciding what "ready" means.
   - **There is no observation window.** Nothing about the new stack is exercised by production
     traffic until *all* of it is. Pre-cutover verification is therefore the only verification,
     which is why the replay-and-diff oracle (item 6) is a deliverable rather than a nicety.

   Dual-post — XRAS posting to both stacks, ours in capture mode — would have restored an
   observation window and grown the payload corpus. **It is ruled out**; do not re-propose it.

   **The ordered prerequisite chain.** All of it precedes the single repoint:

   | # | Prerequisite | Lead time |
   |---|---|---|
   | 1 | `xras_action_log` + `xras_activation_event` in production, and run by hand on staging | **external** — DBA ticket, unblocked, file it now |
   | 2 | The 400/422 contract confirmed with `allocations@access-ci.org` — broker retry behaviour on 4xx is unknown | **external** — an email, start it in the same week |
   | 3 | Phase 3 handlers, all six paths | Sprint C |
   | 4 | **Per-type enablement.** `XRAS_ACTIONS_CAPTURE_ONLY` is a global boolean set in neither `helm/values.yaml` nor `compose.yaml`, so production runs on the code default. Under a single repoint this is not a rollout mechanism — it is the **triage lever** that lets one misbehaving action type be parked on the manual path by config, without a redeploy | Sprint C, small |
   | 5 | The replay-and-diff oracle (item 6) — there is no other way to verify a write path | Sprint C |
   | 6 | `sam-admin xras --validate-mapping` run — the 11 unmapped active resources (§ 9) | Sprint C, small |
   | 7 | *Some* notification path for the `active = 0` activation trigger | ✅ Sprint B's card |
   | 8 | `--api xras` against the **deployed** `sam.hpc.ucar.edu`, using our own `samuel` credential | after deploy, before the repoint |

   Note what is **not** on that list: SMTP. Legacy keeps mailing until the repoint, and Sprint B's
   pending-activation card is the accepted substitute trigger.

   Sequence: Sprint C merged → deployed to `samuel.k8s` → DBA applies both tables → item 8's
   parity run → item 2's confirmation → **XRAS repoints** → a triage week with the operator page
   and `sam-admin xras --summary` as the watch surface.

   The § 4.4 rollback signals still apply as *health* signals during that week — the ~30% 404
   baseline, the ~3.84 MB ±0.2% roster size, and any `xras_action_log` status the 30-day legacy
   corpus never produced — they simply no longer gate a per-endpoint advance.

6. ☐ **The POST-side oracle — replay the corpus and diff DB state.** There is no parity harness for
   writes and there cannot be a live A/B one: both stacks share one production database (§4.3), so
   "run both and compare" would apply every action twice. The only viable check is to replay the
   corpus against a test DB and diff the resulting rows against what legacy did for the same action
   — the §1.2 action-mix correlation is the oracle, and UFSU0023 and NCAR4232 have known-correct
   legacy outcomes with exact error strings to diff a 422 against. `utils/parity/` is GET-only today
   (`XrasClient` has no `post_action`). Build this **with** the first handler, not after it.

---

## 7. Design decisions

- **`xras_action_log` lives in the production `sam` schema**, created out-of-band with the ORM
  following. The audit trail is the core value of this project and belongs next to the data it
  describes, where it can be joined and FK'd — and it earns a Flask-Admin view for free.
- **`GET /people` stays bug-for-bug**, inactive users included. XRAS's identity matching may depend on
  resolving historical usernames, and a 404 where a 200 used to be is a change we cannot observe from
  our side. A filter is a separate conversation with ACCESS; the roster byte-diff is the guard
  meanwhile.
- **`Adjust` is implemented; `Transfer` is deferred** to the manual-fallback path with an audit row.
  Transfer has zero traffic and `exchange_allocations` doesn't fit its semantics, so parity needs new
  allocation machinery rather than an adapter. The audit log tells us the moment traffic appears.
- **`Permission.MANAGE_XRAS`** gates replay and activate-project rather than reusing
  `EDIT_ALLOCATIONS`, so it can be granted to whoever fields XRAS failures independently of general
  allocation editing.
- **The repo ships no golden byte corpus.** Repo tests assert the *rules* (field order, the per-DTO
  null policy, `"%.1f"`, epoch-millis, both 404 forms, the closed-form length) against factory data;
  real bytes are compared live by the parity harness, where the data is real by construction and
  nothing needs scrubbing. The captured corpus is 28,259 real names, emails and phone numbers, so a
  committed corpus would have to be scrubbed length-preservingly first — worth doing only if the live
  harness proves insufficient.

### One credential, both stacks

**Both applications read the same `sam.api_credentials` table** — legacy Java via
`<security:jdbc-user-service>` (§2.2), the Python webapp via `ApiCredentials.as_api_key_map`
(`src/sam/security/roles.py:91`) behind `API_KEYS_DB_ENABLED`, default on. **A single INSERT makes
one secret valid against both stacks simultaneously**, which is exactly what a byte-for-byte
comparator wants: same credential, two base URLs, no possibility that a difference in what the two
can *see* is mistaken for a difference in what they *render*.

Accepted risk: `samuel` also permits `POST /actions`, the same exposure the existing `XRAS` account
carries.

⚠️ **Never set `API_KEYS_SAMUEL`.** `_verify_api_key` checks `current_app.config['API_KEYS']` first
and never falls through to the DB on a hit, *and* config-sourced identities are returned with
`'roles': []` unconditionally (`api_auth.py:112`) — config carries no role assignments. So defining
that variable would not merely shadow the DB row: it would make the `ROLE_XRAS` assertion **fail
closed**, and every XRAS request would 403 while legacy kept serving the same credential happily.
Today only `API_KEYS_COLLECTOR` is configured (`helm/values.yaml:253`). Pinned by a test.

### Byte parity is pursued broadly, not irrationally

Reproduce byte-exactly everything that is *contract* — field presence, field order, value formatting,
and array order that comes from SQL. Diverge deliberately where legacy emits a **server-side failure
artifact**, or where its ordering is an artifact of a **JDK data structure** rather than of the data.
Six divergences, each recorded so re-standardising later is a local edit:

| # | Legacy | Ours | Why |
|---|---|---|---|
| 1 | 403 → **431 B of Tomcat HTML** | JSON envelope, correct status | A servlet-container artifact of a missing `<error-page>`; no client has received it on a mapped path |
| 2 | `requests/role/{bogus}` → **500** with the opaque timestamp body | **400** carrying a real `message` | `IllegalArgumentException` falling into the catch-all — a client error answered with a server error. Zero traffic. Same reasoning as the 422 decision (§2.5) |
| 3 | `masters[]` in Java **`HashMap` bucket order** | sorted by projcode | See below |
| 4 | roster order *incidental* (no `ORDER BY`) | explicit `ORDER BY u.user_id` | Reproduces observed output **and** makes it deterministic — strictly better than legacy |
| 5 | unmapped path → **401** unauthenticated, **404** (431 B Tomcat HTML) authenticated | Flask's own 404 in both cases | Legacy 401s because the filter runs *before* routing; Flask routes first, so a blueprint `errorhandler(404)` never sees a routing miss. Reproducing it means a catch-all that turns every typo into a 401 — worse to debug, for a case no client exercises |
| 6 | `allocations[]` order under a **`start_date` tie** is arbitrary | primary-key tiebreaker | See below |

**On #3 —** the order *is* reproducible: emulating `String.hashCode()` plus `HashMap`'s
spread-and-bucket walk matched all three captured multi-master responses exactly, including one where
the observed order is not insertion order. Rejected anyway. It is ~15 lines of JDK emulation that
becomes **untestable above 12 masters** (where `HashMap` resizes and the bucket walk changes) without
new probes, and it buys byte-parity only on `requests/user` and `requests/role` — both **zero hits in
30 days**. `requests/request/{n}` always has exactly one master and is byte-exact either way.

**On #6 —** `xras_allocation`'s `ORDER BY al.start_date DESC` is **not a total order**: SCSG0001 alone
has 11 allocations sharing a start date, so MySQL may return tied rows in any order. **Legacy's own
responses are therefore not guaranteed byte-stable**, and ours were not either until a primary-key
tiebreaker was added — the symptom was two identical requests returning different bytes. Measured
against production for SCSG0001: of 15 request groups, the **6 with no tie match our deterministic
order exactly**, and the 9 with a tie match neither ascending nor descending `allocation_id`.

For both, the parity comparator sorts the affected array on **both** sides before comparing, so
content is still checked byte-exact while an order neither side can be held to is ignored.

---

## 8. Verification

- **`pytest`**
  - `tests/api/test_xras_access.py` — the XA-header shim including the one-header case; the
    byte-exact 41 B 401 (space before the colon, no `WWW-Authenticate`); `ROLE_XRAS` enforcement and
    the closed session path; bare-array and bare-object shapes; the per-DTO null policy; field order;
    `"%.1f"`; epoch-millis at Denver midnight; both 404 bodies and the closed-form length;
    byte-stability across repeated calls. **Phase 2+ adds** action status codes 200/400/422.
  - `tests/unit/test_xras_actions.py` (Phase 3) — each handler against factories, plus golden
    payloads.
  - `tests/integration/test_schema_validation.py` (Phase 2) — the new `xras_action_log` table.
  - `tests/unit/test_nav.py test_route_map_parity.py test_modal_shell_contract.py test_rbac.py`
    (Phase 4) — all four pin fixtures that a new tab or permission invalidates.
- **Live parity** — `python utils/parity/check_legacy_apis.py --api xras` (UCAR VPN,
  `SAM_XRAS_USER`/`SAM_XRAS_PASS`). Exit 0 across all six GET endpoints.
- **Manual** — `docker compose up webdev --watch`, then
  `curl -H 'XA-REQUESTER: samuel' -H 'XA-API-KEY: …' localhost:5050/api/xras/v1/people/benkirk`;
  from Phase 2, post a sample action and replay it from both the dashboard and the CLI.
- **Latency budget**, from measured legacy: `/people/{u}` ≤ 100 ms p50; roster ≤ 1.2 s;
  `POST /actions` ≤ 400 ms p50 (legacy's tail is inflated by synchronous SMTP, so we should beat it).

---

## 9. Open risks

- **`user_organization` is frozen** (nothing since 2026-07-09; 4,563 active users with no current
  organization), causing 24% of legacy's XRAS failures. Fixing it is outside this project, but the
  port must surface it as a reviewable 422 — otherwise we ship the same invisible failure with better
  plumbing.
- **The 400/422 error-contract change needs confirmation from `allocations@access-ci.org`** before
  cutover step 4. Broker retry behaviour on 4xx is unknown.
- **11 active SAM resources are unmapped** in `xras_resource_repository_key_resource` (§4.1). The
  question that actually matters is which of those gaps XRAS can exercise — a SAM resource with no
  mapping row only fails if XRAS holds a repository key for it, which is knowable from
  `xras_allocation`'s historical keys plus an ACCESS-side conversation. Add
  `sam-admin xras --validate-mapping` and run it before cutover. Note this also moves bytes:
  `resourceRepositoryKey` is omitted when unmapped (§2.3), so closing a gap legitimately changes a
  `requests/*` response.
- **Four legacy defects worth not reproducing.**

  1. `XrasAction.getUsernameByRoleType()` returns the first matching role and ignores duplicates —
     the ACCESS docs state a request must have exactly one PI, so we should reject rather than
     pick-first (a mis-ordered array could otherwise mint a project under the wrong human).
     **Now measured, not hypothetical:** `new_uwis0071_existing_ok.json` carries *two* `PI` roles
     for one human whose institution changed mid-request — one on a closed window
     (`2026-07-27`..`2026-08-04`, `organization: 'UNIVERSITY OF WISCONSIN AT MADISON'`) and one
     open (`2026-08-05`.., `organization: 'NCAR/EDECD'`). Since `organization` is the mnemonic
     extractor's input, the two choices resolve to *different facilities*. Rejecting outright is
     wrong here — this is legitimate traffic — so the rule is **filter on the date window**, and
     reject only if that still leaves more than one.
  2. Organization 158 "UCAR Community Programs" matches two mnemonic codes (`CAR`, `UCP`), which
     throws for any PI in that organization.
  3. The roster and role-assignment readings of `roles[]` apply **different begin-date filters**
     (§3.5), so a PI whose role starts after the action begin date but before today becomes
     project lead with no account on any resource.
  4. **`AdjustProjectActionService` is dead code.** Its `isServiceable` tests
     `getActionType().equals("Adjust")`, but XRAS sends `"Adjustment"` (measured — see
     `adjustment_uwis0064_manual.json`, and legacy's own vocabulary comment agrees). The two never
     match, so no Adjustment has ever been serviced; every one falls through to the manual mailer
     (§ 1.4). **Deliberate divergence:** nothing has shipped on the SAM side, so
     `sam.queries.xras_actions.XRAS_ACTION_TYPE_ALIASES` maps `Adjust → Adjustment` and both
     spellings select the same handler and the same filter bucket. `xras_action_log.action_type`
     still stores what arrived verbatim, so the audit trail records the wire, not our
     normalisation. Blast radius is small and one-directional: an action type that previously did
     nothing but email a human would begin to be serviced once the Adjustment handler lands, which
     is the point — but it means **the Adjustment handler is the one to review hardest**, since it
     has no known-good production outcome to compare against.

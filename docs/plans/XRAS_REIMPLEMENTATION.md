# XRAS Integration Reimplementation (Python) — Plan

## Context

Legacy SAM (Java/Tomcat, deployed build **2.0.3**) is the site-side server for NCAR's XRAS
allocation integration. The XRAS broker at ACCESS **pushes** allocation decisions to SAM
(`POST /api/xras/v1/actions`) and **pulls** identity and request data from SAM
(`GET /api/xras/v1/people*`, `/requests/*`). This is the last major legacy surface not yet ported
to Python SAM.

Goal: a **drop-in replacement** — same URLs, same auth headers, same wire bytes — with the failure
modes that make the current system expensive to operate fixed: **structured error responses**
(422 with the real validation messages instead of a blanket 500 carrying an opaque timestamp), and a
**DB-backed audit trail + admin dashboard** replacing the email-only, paste-JSON-to-replay workflow.

### Evidence base

An earlier revision of this plan was written from static source reading alone. It has since been
**verified and corrected on `sam-tomcat.ucar.edu`**, the live legacy host, with:

- 30 days of access, application and XRAS-action logs (**2026-07-07 → 2026-08-05**; nothing older
  exists — there are no archives),
- read-only queries against the production database (`sam-sql.ucar.edu`, `hpc-reader`),
- live HTTP probes of the running endpoint,
- the deployed source at tag `2.0.3` (**not** the working tree, which is divergent).

Everything below is measured unless explicitly marked as inference. Where the earlier revision was
wrong, the correction is called out so the change is auditable.

---

## 1. The production system, measured

### 1.1 Traffic is small, narrow, and single-sourced

| Endpoint | 30d hits | Status split | Latency (p50 / p95) | Body size |
|---|---:|---|---|---|
| `GET /api/xras/v1/people/{username}` | 3,058 | 2,128×200 / 930×404 | 95 ms / 107 ms | 87–274 B |
| `POST /api/xras/v1/actions` | 175 | 108×200 / **67×500** | 383 ms / 1,130 ms | 41 B ok, 112 B error |
| `GET /api/xras/v1/people?` | 30 | 30×200 | 1,123 ms / 1,272 ms | **3.81 MB** |
| `GET /api/xras/v1/requests/request/{n}` | **1** | 1×200 | 7,679 ms | — |
| every other mapped endpoint | **0** | — | — | — |

- **One caller, ever.** 100% of requests come from `18.223.62.77` (AWS us-east-2) with User-Agent
  `Ruby` (bare `Net::HTTP` default). There is no long tail. Cutover is a single-party conversation,
  and an IP allowlist would be complete access control for this surface.
- **Peak burst is 24 requests/minute** (~0.4 rps), on 2026-07-23. The throughput bar is trivial.
- `GET /people` is a **nightly cron at 03:00:5x MDT, 30 days out of 30**. Everything else follows a
  weekday/weekend human pattern (100–290/day vs 7–32/day).
- The roster URL is literally `GET /api/xras/v1/people?` — a bare trailing `?`.

> **Correction.** The earlier revision cited 3,547 / 158 / 30 / 3 hits from a different window and a
> 27% POST failure rate. The measured failure rate is **38%**, and `requests/request/{n}` sees
> **one** call per month, not three.

> **Analysis trap.** `grep -i xras` over the access logs is dominated by **187,382** requests to
> `/api/protected/amie/v1/task/AMIE/XRAS-*/create_project` on 2026-07-08–09 — an AMIE polling loop
> stuck on 13 `create_project` tasks. That is 57× the entire real XRAS volume and is unrelated.
> Filter on `/api/xras/`.

### 1.2 Production action-type mix — recovered from the database

`actionType` is **never written to any log** (verified: zero occurrences across `sam.log`,
`sam-xras-actions.log`, `catalina.out`; it exists only as a Velocity variable in the notification
emails). The distribution was recovered by correlating the 109 success lines in
`sam-xras-actions.log` against `allocation_transaction.creation_time` (±60 s) and
`project.creation_time`:

| Effective action | posts | share | DB effect |
|---|---:|---:|---|
| **Extension** (existing project) | 65 | **60%** | 213 `EXTENSION` rows — avg **3.3 allocations per post** |
| **New** (project created) | 23 | 21% | 63 `NEW` rows — avg 2.7 allocations |
| **Supplement** (existing project) | 16 | 15% | 24 `SUPPLEMENT` rows |
| Update creating an allocation on an existing project | 3 | 3% | `NEW` ×2, `NEW`+`SUPPLEMENT` ×1 |
| **Successful post that mutated nothing** | 2 | 2% | 0 rows |
| **Transfer** | **0** | — | — |
| **Adjust** | **0** | — | — |

### 1.3 Failure is concentrated in exactly one code path

Of the 67 failures, 66 are raised by validators and extractors that **only run on the
New/Renewal path**. The single exception is `Action end date before existing allocation end date
for Derecho GPU` (the Extension handler).

| Path | successes | failures | success rate |
|---|---:|---:|---:|
| **New / Renewal** | 28 | **66** | **30%** |
| Extension | 65 | 1 | 98.5% |
| Supplement | 16 | 0 | 100% |

Distinct failure causes over 30 days — only six exist:

| Cause | count | share |
|---|---:|---:|
| `PI <name>-user-<token> is not in database` (unreconciled ACCESS placeholder identities) | 37 | 55% |
| `Could not determine Mnemonic code for internal PI via organization` | 16 | 24% |
| `PI <user> is not an active user` | 6 | 9% |
| `Cannot find contract for grant number "<n>"` | 4 | 6% |
| `Username <user> is missing` | 4 | 6% |
| `Action end date before existing allocation end date for <resource>` | 1 | 1.5% |

Five identities account for 39 of the 67 failures; `kquagraine-user-89o84` alone accounts for 17.
**This is not a broad reliability problem** — it is a small set of unmapped identities reposted by
hand, repeatedly, because XRAS does not auto-retry and the 500 body tells the operator nothing.

**Planning consequence:** the highest-volume handler is nearly perfect and the lowest-volume-but-
hardest one carries all the pain. Build `Extension` first to establish the pipeline on the easy
path, then invest in `New`.

### 1.4 The manual-fallback path is dead code

`ManualFallbackActionPostService` logs only at `LOG.debug()`, which is suppressed
(`XrasActionLogger` is pinned at INFO in the *active* config), so grepping for it proves nothing.
Detected indirectly instead: access-log `POST /actions` 200s versus `EmailingActionPostService`
INFO lines, per day. **Δ = 0 on every day with access-log coverage** (108 vs 109; the −1 falls on
2026-07-06, which predates access-log retention). A fallback invocation would appear as a 200 with
no INFO line. **Zero invocations in 30 days.**

It is reachable *only* via `catch (BadRequestException)` — i.e. `ProjectActionServiceSelector`
finding no serviceable, which is what an `Adjustment` or `Advance` actionType would produce. Note
the selector's guard string is `"Adjust"`, not `"Adjustment"`, and there is no `"Advance"`
serviceable at all.

> **Structural consequence.** On that path the broker still receives `200 {"message":"OK"}` for an
> action SAM silently deferred to a human. **The legacy 200/500 split does not distinguish
> "processed" from "quietly parked".** The new implementation must.

---

## 2. Wire contract

### 2.1 Endpoint inventory (deployed 2.0.3)

`web.xml` maps a dedicated `DispatcherServlet` (`xrasRestApi`, context
`classpath:spring/xras-rest-context.xml`) at **`/api/xras/*`**, plus a dedicated
`XrasAuthenticationFilter` on the same pattern. All five controllers are `@RestController` and
extend a shared `XrasController`. Effective path = `/api/xras` + the `@RequestMapping` value; there
is no bare `/v1/*` surface.

| # | Method | Path | Response type |
|---|---|---|---|
| 1 | GET | `/api/xras/v1/people` | `List<PersonDTO>` — **bare array, not wrapped** |
| 2 | GET | `/api/xras/v1/people/{username}` | `PersonDTO` — **bare object, not wrapped** |
| 3 | GET | `/api/xras/v1/requests/request/{requestNumber}` | `ResponseWrapper{result: AccountingRequestResponse}` |
| 4 | GET | `/api/xras/v1/requests/user/{username}` | same |
| 5 | GET | `/api/xras/v1/requests/role/{role}/{username}` | same |
| 6 | GET | `/api/xras/v1/dates/requests/{requestNumbers}` | `ResponseWrapper{result: List<RequestDatesDTO>}` |
| 7 | POST | `/api/xras/v1/roles/{requestNumber}/{role}/{username}` | empty body, 200 |
| 8 | POST | `/api/xras/v1/actions` | `ResponseWrapper("OK")` → `{"message":"OK","result":null}` |

Notes that matter for the port:

- **#1 and #2 are the only endpoints without the `{message, result}` envelope.** Any client
  description assuming a uniform wrapper is wrong for the highest-traffic endpoint on the surface.
- #5's `{role}` path segment is the lowercase snake_case key, mapped
  `pi→Pi`, `co_pi→CoPi`, `allocation_manager→AllocationManager`. An unrecognised role throws
  `IllegalArgumentException` → **HTTP 500**, not 400.
- #7 accepts **only** `pi`; anything else is `NotFoundException` → 404.
- #8 does not let Spring bind the body. It takes `@RequestBody String actionJson` and calls
  `new ObjectMapper().readValue(actionJson, XrasAction.class)` itself — a second, unconfigured
  mapper. Parse failure → `RuntimeException` → 500.
- The upstream ACCESS spec documents `POST /v1/actions/<actionId>/<requestId>/<actionType>`, but
  **all 175 real posts go to bare `/api/xras/v1/actions`**, the only form SAM maps. If the broker is
  ever "corrected" to match its own docs, every post 404s. Worth mapping both forms defensively.
- Endpoints the ACCESS spec defines that SAM **does not implement**: `GET /test_auth`,
  `GET /v1/usage/by_month/…`, `DELETE /v1/roles/…` (so role *revocations* never reach SAM),
  and the `/v1/users/…` family. These are known gaps, not part of this port.

### 2.2 Auth

**Header translation** — `XrasAuthenticationFilter`, exact behaviour:

1. If **neither** `XA-REQUESTER` nor `XA-API-KEY` is present → pass through untouched.
2. Otherwise wrap the request in a case-insensitive mutable header map.
3. If `Authorization` is **absent** *and* **both** XA headers are present → set
   `Authorization: Basic base64(requester + ":" + apikey)` (UTF-8, non-chunked).
4. **Unconditionally remove both XA headers.** Supplying only one ⇒ headers stripped, no
   `Authorization` synthesized ⇒ 401.
5. An explicit `Authorization` header always wins.

**Authorization** — Spring Security chain for `/api/xras/**`: stateless, CSRF disabled, security
headers disabled, `requires-channel="https"`, `use-expressions="false"`, `access="ROLE_XRAS"`
(plain `RoleVoter`, so the authority string is literally `ROLE_XRAS`).

**Credential store** — `api_credentials` (`username`, `password` bcrypt, `enabled`) joined through
`role_api_credentials` → `role`. Verified in production: row **`XRAS`** (id 2, `enabled=1`,
`ROLE_XRAS`); a disabled `XRAS_OLD` (id 1) also exists. Note `api_credentials.username` is
`varchar(11)`.

> **Correction.** The earlier revision said role `XRAS`. The literal authority is **`ROLE_XRAS`**.

**401 response — byte-exact, verified live with `od -c`:**

```
Content-Type: application/json;charset=UTF-8
Content-Length: 41
(no WWW-Authenticate header — deliberate, see XrasAuthenticationEntryPoint javadoc)

{\n  "message" : null,\n  "result" : null\n}
```

Note the **space before the colon** — Jackson's `DefaultPrettyPrinter`. This body is returned for
**unmapped paths too** (`/api/xras/v1/test_auth` → 401, not 404), because the filter and security
chain run before routing.

**403** (valid credentials, missing `ROLE_XRAS`) falls through to Spring's default
`AccessDeniedHandlerImpl` → `sendError(403)` → the **container's HTML page**, not JSON. Reproducing
this is not worth it; return the JSON envelope with 403 and note the divergence.

### 2.3 Response shapes

**`PersonDTO`** (endpoints 1–2): `username, firstName, middleName, lastName, organization,
academicStatus, phone, email` — all strings.

**Null fields are omitted.** Source reading is ambiguous here: the POJOs carry the Jackson-1.x-era
`@JsonSerialize(include = JsonSerialize.Inclusion.NON_NULL)`, which jackson-databind ≥2.9 no longer
honors, and `pom.xml` at 2.0.3 pins **2.16.0** — which would mean nulls *are* emitted. Settled
empirically against the nightly roster's actual byte count:

| Hypothesis | Predicted | Observed 2026-08-05 |
|---|---:|---:|
| nulls **omitted** | 3,845,112 B | **3,839,790 B** ✅ (0.14% off) |
| nulls emitted | 5,219,474 B | 36% off |

The −5 KB residual is explained by `IdentityServiceImpl.fixInternalOrg()` shortening
`UCAR/NCAR:<acronym>` strings. **Omit nulls in the Python port**, and use roster byte size as the
parity check.

**`GET /people/{u}` 404**: `{"message":"username=<u> not found","result":null}`.

**`GET /requests/request/{n}`** → `ResponseWrapper{message: null, result: {...}}`:

```
projectIdLabel : null
masters[]      : { requestNumber, requests[] }          # serialized as an ARRAY (getMasters() returns .values())
  requests[]   : { requestType,                          # "New" for earliest begin per project, else "Renewal"
                   requestBeginDate, requestEndDate,     # "yyyy-MM-dd" strings
                   allocationType, projectTitle, projectId,
                   xrasActionIds,                        # never populated → null
                   fos[]         : { xrasFosTypeId, isPrimary: true },
                   allocations[] : { actionType,         # never populated → null
                                     allocationBeginDate, allocationEndDate,
                                     allocatedAmount,    # STRING, "%.1f"
                                     remainingAmount,    # STRING, "%.1f"; HPC-only, omitted when null
                                     resourceRepositoryKey,
                                     actions[] : { orderApplied,   # 1-based, assignment order
                                                   actionType, amount,   # STRING "%.1f"
                                                   endDate, dateApplied } } }
```

Unknown request number → **200 with empty `masters`**. `RequestFactory` throws
`IllegalStateException` (→ 500) when the `allocationIds` CSV on `xras_request` fails to reconcile
against `xras_allocation`/`xras_action`, and `RequestDTO.getXrasFosTypeId()` NPEs on a null column.

**Dates are `yyyy-MM-dd` strings everywhere except `dates/requests`**, which returns
`java.util.Date` with no date module configured ⇒ **epoch-millis integers**. Preserve that quirk.

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
fos[]       : fosTypeId fosNum fosName fosAbbr isPrimary          # all STRING except isPrimary
panels[]    : type name abbr isPrimary
grants[]    : fundingAgency grantNumber programOfficerName programOfficerEmail piName title
              beginDate endDate awardedAmount awardedUnits percentageAward subAwardNumber
              primaryFos{} isPending
```

- `projcode` = `StringUtils.trimToNull(requestNumber)`.
- **PI** = the first role with `roleType == "PI"` whose date window brackets `actionBeginDate`;
  same rule for `"Allocation Manager"`.
- `ForgivingBooleanDeserializer` is applied to **exactly one field**, `roles[].isAccountToBeCreated`:
  `null→false`, integer→`!= 0`, `t/true/y/yes`→true, `f/false/n/no/""`→false, anything else is an
  error. All other booleans use Jackson defaults.
- **`isReconciled` and `isAccountToBeCreated` are inert** — parsed into the POJO and never read by
  any business logic (verified by grep across `src/main/java`). A role meaning "provision this new
  person" therefore produces a hard failure regardless of intent.
- Real payloads send `fos[].fosTypeId` and `awardPeriod` as **numbers** into **string** fields;
  Jackson coerces. The Python schema must accept both.

**Sample payloads.** Only one real payload exists in the repo:
`2.0.3:src/test/resources/xras/rest/request/createActionGood.json` (3,593 B). The two JSON *schema*
files under `src/main/resources/json/xras/` are **stale** — they reference a Java package that no
longer exists, and `Action.json` is missing `grants` entirely and models `fos[]` with only two
fields. **Do not treat them as contract.**

### 2.5 Error mapping — legacy, and what we change

Legacy has no `@ControllerAdvice`; all handling is six `@ExceptionHandler` methods on
`XrasController`:

| Exception | Legacy status | Legacy body |
|---|---|---|
| `IdentityNotFoundException` | 404 | `{"message":"username=x not found","result":null}` |
| `NotFoundException` | 404 | `{"message":"…","result":null}` |
| `BadRequestException` | 400 | `{"message":"…","result":null}` |
| `BadStateException` | 403 | `{"message":"…","result":null}` |
| `XrasException` | 500 | `{"message":"…","result":null}` |
| **anything else** (incl. `ActionProcessingException`) | **500** | `{"message":"Unhandled SAM exception processing XRAS request (timestamp <epoch-ms>)","result":null}` |

**The critical defect:** `ActionProcessingException.getErrorMessages()` holds the ordered list of
real validation messages — and matches no typed handler, so it lands in the catch-all and **the
messages are discarded from the HTTP response**. They go only to the log and the failure email.
XRAS admins see the response body directly in their "Accounting Service Posts" panel, so today they
read `Unhandled SAM exception … (timestamp 1785384269504)` where they could be reading
`PI kquagraine-user-89o84 is not in database` and self-servicing.

**Deliberate divergences in the Python port:**

| Condition | Legacy | New |
|---|---|---|
| Malformed JSON body | 500 | **400** |
| Validation failure (`ActionProcessingException` equivalent) | 500, opaque | **422** with the structured error list |
| Unhandled action type | 200 + email, silently | **200 + audit row marked `manual` + email** |
| Success | 200 `{"message":"OK","result":null}` | unchanged |
| Unknown request number | 200, empty `masters` | unchanged |
| `/people/{u}` miss | 404 | unchanged |

Everything else stays byte-identical. The 4xx change should be confirmed with
`allocations@access-ci.org` before the `POST /actions` cutover step — broker retry behaviour on 4xx
is unknown, and this is the whole point of the change.

---

## 3. Action-processing semantics (legacy, to be reproduced)

### 3.1 Selector — first match wins, in this order

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
| 1 | `actionType == "New"` && project **not** exists | Create project: title/abstract, lead=PI, admin=AM, allocation type via extractor chain, AOI from primary `fosNum`, org from lead, non-exempt, generated projcode, allocated GID; contracts from `grants[]`; per resource create an allocation (start clamped ≥ resource commission date, end-of-day end); add all valid role users to accounts; **finally set the project inactive** — a human activates it, and the success email is the trigger |
| 2 | `actionType ∈ {"New","Renewal"}` && project exists | Update fields (`active=true`); contracts; per resource: create allocation if none overlapping, extend if the end grows (**error** if it shrinks), undo an AUTO/DEFAULT canned allocation via a compensating `UNDO AUTO/DEFAULT` adjustment, then supplement (`>0`) or adjust (`<0`). `comments == "AUTO_DEFAULT_ALLOCATION_TRANSACTION"` ⇒ extension only |
| 3 | `actionType == "Supplement"` && project exists | Per resource: create allocation if none (start today, end = latest contract/allocation end), else supplement when `>0`; `≤0` ignored with a warning |
| 4 | `actionType == "Adjust"` && project exists | As Supplement; legacy silently drops negatives |
| 5 | `actionType == "Transfer"` && project exists | 1 negative source + ≥1 positive destinations, same project, Σ = 0, source clamped to available |
| 6 | `actionType == "Extension"` && project exists | **Ignores payload resources**; extends the latest allocation of **every active account** to `actionEndDate`; **errors** if that would shrink any |
| — | no match | `BadRequestException` → swallowed → manual-fallback email → **200** |

Assembly does **not** short-circuit: errors accumulate into an ordered `LinkedHashSet` on
`ProcessingAction` via `observer.report(...)`, then `throwExceptionIfErrors()` raises once with the
full list. Reproduce this — reporting all problems in one response is what lets an operator fix a
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

**Production frequency of the resulting types** (automated project creations, last 12 months) —
use this to order test coverage:

| Type | n | | Type | n |
|---|---:|---|---|---:|
| `Small (No NSF award)` | 146 | | **`CHAP`** | **30** |
| `Small` | 87 | | `NSC` | 16 |
| `Data` | 79 | | `Discover ACCESS` | 15 |
| `Classroom` | 52 | | `Explore ACCESS` | 10 |
| | | | `External Project` | 4 |

> **Correction.** The earlier revision's extractor summary omitted **CHAP** (the `Large` strategy),
> which is the 5th most common type in production.

A second use of the same resolution: `getAuthAtPanelMeeting()` returns `true` iff the resolved type
is `CSL` or `CHAP`.

### 3.3 Other extractors

- **Mnemonic** — `opportunityName.startsWith("NCAR ")` → organization parentage at lab level
  (parentage size 0 → null; ≤3 → element 0; else element `len-3`); else external PI → institution
  (exact `findOneByDescription("<name>, <city>")`); else internal → organization (fuzzy
  `code LIKE '%name%' OR description LIKE '%name%'` with `code` a `varchar(3)` — **broken; 150 of
  171 active orgs match nothing**). `XrasAction.getMnemonicCode()` is hard-coded `null`, so the
  extractor always runs.
- **Area of interest** — primary `fosNum` (first `isPrimary` entry, else first entry) →
  `Integer.decode()` → `findOne(id)`, falling back to `findOne(name)` on `NumberFormatException`.
- **Contract number** — regex `^(.*[^0-9])?([0-9]{6,})[^0-9]*$`, group 2, then
  `contract_number LIKE '%<core>'` with `uniqueResult()`. Grants with a blank number are skipped.
- **`/people` org fixup** — `UCAR/NCAR:<acronym>` → parentage walk producing `NCAR/<acr>`, with
  `{NCAR→NCAR, UCAR→UCAR, UCP→"UCAR Community Programs"}` and no-parent→`UCAR`.

### 3.4 Error strings the handlers must be able to produce

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

---

## 4. Production data constraints

| Fact | Measured | Consequence for the port |
|---|---|---|
| `xras_user` has **no active/deleted filter** (only `login_type_id = 1`) | 28,253 rows, **22,039 inactive** | `/people` publishes every user who ever existed — kept bug-for-bug (§7 D2) |
| `organization` null rate in `xras_user` | **79%** (22,311 rows) | consequence of the frozen `user_organization` |
| rows needing the `UCAR/NCAR:` fixup | 1,760 | port `UCAROrgNameQuery` faithfully |
| **`user_organization` is frozen** | no rows created since **2026-07-09**; **4,563** active users have no current org; **2,092** rows point at `organization_id = 0` (dangling FK) | root cause of the 16 mnemonic failures. Out of scope to fix, but the port must report it as a reviewable 422, not an opaque 500 |
| **Contract suffix collisions are live** | 3 cores collide **today**: `1049089` (`1049089` \| `PLR-1049089`), `1744587` (`OPP-` \| `PLR-`), `2146709` (`2146709` \| `AGS-2146709`) | legacy's `LIKE '%core'` + `uniqueResult()` ⇒ guaranteed `NonUniqueResultException` ⇒ 500 for any grant citing these. Resolve deterministically: exact match first, then unique suffix, else report |
| `allocation_type` has duplicate names | `Small` ×2, `Education` ×2 | resolve by `(panel, type)` — matches `CLAUDE.md:819` |
| `xras_resource_repository_key_resource` | **13 rows** | maps Derecho, Derecho GPU, Casper, Casper GPU, Campaign_Store, HPSS, CMIP AP + decommissioned kit. **Unmapped:** `GLADE user`, `GLADE work`, `Destor`, `Boreas`, `Gust`, `Gust GPU` — an award on any of these fails with `No resource found in SAM corresponding to key %s` |
| `fos_aoi` (FOS → AreaOfInterest) | exists in prod, **18 rows** | already modelled as `FosAoi` (`src/sam/projects/areas.py:175`) and referenced by no query module. Legacy does **not** use it — it decodes `fosNum` as an id. Prefer `fos_aoi` in the port, falling back to legacy behaviour |
| **GID allocation is live in legacy** | pool `99000–99999`, `nextGid = 99025`; `modified_time` matches the 2026-08-05 09:58:49 XRAS post to the second | legacy 2.0.3 allocates GIDs locally for XRAS projects (since 2026-07-16, `UMIT0083` = 99001). **`project.unix_gid` is NULL for 0 of 5,795 rows.** Never leave it NULL |
| XRAS-created projects arrive `active = 0` | 21 of 23 have since been activated by hand | confirmed by design (`InactivateNewProject`); the success email is the human trigger |
| XRAS allocation transactions | `user_id IS NULL`; comment `XrasAction Extension Request` (current) / `XRAS Extension Request` (pre-2025-10) | the actor convention to preserve — but see §6 Phase 3 |
| `xras_request` fails under `ONLY_FULL_GROUP_BY` | prod `sql_mode` omits it, so the view works there (9,489 rows) | the offending clause is **`ORDER BY al.end_date`** alone — the SELECT list is safe via the `project_projcode_uk` unique index. Verified: removing the `ORDER BY` returns 9,489 rows under `sql_mode='ONLY_FULL_GROUP_BY'` |
| Email recipients | `xras.actionpost.recipients=hdt@ucar.edu` (`2.0.3:app/env/sam.complete.properties:29`) | the deployed `/tomcat/tomcat-sam/var/sam.complete.properties` is 0600 and unreadable |
| Feature flag | `XRAS_POST_ACTION=true` (`/tomcat/tomcat-sam/var/features.properties`) | — |

### 4.1 Two performance landmines in the XRAS views

Measured server-side against production with `SHOW PROFILES`:

| Query | Duration |
|---|---:|
| `SELECT COUNT(*) FROM xras_user` (28,253 rows) | 0.96 s |
| **`xras_user WHERE username = 'benkirk'`** | **0.91 s** |
| `SELECT COUNT(*) FROM xras_allocation` | 7.66 s |
| **`xras_allocation WHERE projectId = 'UUSL0047'`** | **6.41 s** |
| `xras_request WHERE projectId = 'UUSL0047'` | 0.0007 s |
| `xras_action WHERE projectId = 'UUSL0047'` | 0.0007 s |

- **`xras_user` does not push down a username predicate.** Its `GROUP BY u.user_id` forces
  materialization of all 28,253 rows for every single-user lookup. Legacy is far faster end-to-end
  (95 ms p50) because `IdentityServiceImpl` uses the named query `identityServicePersons`
  (`2.0.3:src/main/resources/hibernate/xras/namedQuery.xml`), **not the view**.
  ⇒ **`GET /people/{username}` must query base tables with the filter applied.** Using
  `XrasUserView` — as the earlier revision proposed — would be a ~10× latency regression.
- **`xras_allocation` costs 6–8 s regardless of filter**, because `xras_hpc_allocation_amount`
  aggregates `hpc_charge_summary` across *all* allocations before joining. This is why the one
  `requests/request/{n}` call in 30 days took 7.7 s. ⇒ compute `remainingAmount` scoped to the
  requested project.

### 4.2 Shared database ⇒ incremental cutover

`.env.example:14` sets `PROD_SAM_DB_SERVER=sam-sql.ucar.edu`: **both applications read and write the
same production database.** A per-endpoint proxy cutover is therefore safe and reversible, with no
data divergence between steps. `sam.ucar.edu` (128.117.225.232) is fronted by
`prod-staticweb14/15.ucar.edu`, which can split on path prefix.

---

## 5. What already exists in Python SAM

Reuse these; do not rebuild. Line references verified against the current checkout.

**ORM / views** — `XrasResourceRepositoryKeyResource` (`src/sam/integration/xras.py:9`); six view
models in `src/sam/integration/xras_views.py` (`XrasUserView:23`, `XrasRoleView:50`,
`XrasActionView:72`, `XrasAllocationView:97`, `XrasHpcAllocationAmountView:122`,
`XrasRequestView:144`), exported from `src/sam/__init__.py:186-192`. Smoke tests at
`tests/integration/test_views.py:31-111` — the two `xras_request` tests currently `pytest.skip`
inside a bare `except Exception`, which must be removed once the view is fixed.

**API recipe** — `src/webapp/api/v1/queue.py`, `wallclock_exemption.py`: module docstring naming the
legacy endpoint, `bp = Blueprint(...)` immediately followed by `register_error_handlers(bp)`,
`@bp.route` → `@login_or_token_required(...)` → `@cache.cached(...)`, query logic in
`sam/queries/*`, and a `POST /refresh` with `@csrf.exempt`. Registered in `src/webapp/run.py`
(imports `:29-41`, API register block `:405-417`).

**Auth** — `login_or_token_required` (`src/webapp/utils/api_auth.py:169`), `ApiCredentials`
(`src/sam/security/roles.py:65`) with `as_api_key_map` (`:91`) already resolving role **names**;
`g.api_key_roles` populated at `api_auth.py:129`. `tests/factories/security.py:make_api_credentials(..., roles=())`
already builds `Role` + `RoleApiCredentials` — no new factory needed.

**Manage ops** — `management_transaction` (`src/sam/manage/transaction.py:12`),
`log_allocation_transaction` (`src/sam/manage/allocations.py:69`), `create_allocation` (`:197`),
`update_allocation` (`:271`), `exchange_allocations` (`:416`),
`extend_project_allocations` (`src/sam/manage/extend.py:40`),
`renew_project_allocations` (`src/sam/manage/renew.py:260`),
`add_user_to_project` (`src/sam/manage/__init__.py:53`), `change_project_admin` (`:179`).

**Projects / lookups** — `Project.create` (`src/sam/projects/projects.py:233`),
`next_projcode` (`:1698`), `GidAllocation.allocate_next_gid` (`src/sam/core/groups.py:292`),
`MnemonicCode.build_lookup/resolve_for_institution/resolve_for_organization`
(`src/sam/core/organizations.py:445/461/481`),
`Contract.existing_by_number` (`src/sam/projects/contracts.py:249`),
`ProjectContract.create` (`:468`), `FosAoi` (`src/sam/projects/areas.py:175`),
`AllocationType` (`src/sam/accounting/allocations.py:483`).

**A working reference for the New handler already exists**:
`src/webapp/dashboards/admin/projects_routes.py:600-687` performs, inside one
`management_transaction`, exactly the sequence the New handler needs —
`next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` →
`ProjectContract.create` → `ProjectOrganization.create`. **Port against that, not from scratch.**

### 5.1 Corrections to the earlier revision of this plan

| Earlier claim | Reality |
|---|---|
| GID lives on the unmerged `gid_allocation` branch; New leaves `unix_gid` NULL | **Wrong.** `allocate_next_gid` is on `main` and already used by the admin create-project flow; the prod pool is seeded and in active use. `origin/gid_allocation` is stale. A NULL GID would violate a de-facto invariant (0 of 5,795 projects) |
| Transfer → `exchange_allocations` | **Semantics differ.** It is strictly 1 source → 1 destination, requires the **same resource** (not same project), and **raises** when `amount > source.amount` rather than clamping |
| Extension → `extend_project_allocations`, "error if it would shrink any" | It is **project-tree-scoped** (not per active account) and **silently skips** shrinks, open-ended and inheriting allocations |
| `xras_action_log` DDL "in `sql/`" | `sql/` contains no DDL at all; `CLAUDE.md:38,808` say the SAM schema is not modified from the repo. Resolved in §7 D1 |
| "find the existing webapp email utility" | **None exists.** Zero `MAIL_*`/`flask_mail`/`smtplib` hits under `src/webapp/` or `src/sam/`. The only mailer is `src/cli/notifications/email.py` (stdlib `smtplib` + Jinja2, hardcoded `Bcc: benkirk@ucar.edu` at `:127,:138`) |
| legacy 401 shape via `register_error_handlers` | Incompatible: `src/webapp/api/helpers.py:16-42` emits `{'error': …}` and `_auth_challenge` (`api_auth.py:48`) adds `WWW-Authenticate`. There is also **no 422 or 500 handler**. XRAS needs blueprint-local handlers |
| enforce role `XRAS` | Role name is `ROLE_XRAS`; enforcement exists **nowhere** today (`g.api_key_roles` has no consumers); `api_credentials.username` is `String(11)` |
| `projects.py:1516`, `organizations.py:455,475`, `run.py:~402` | Now `:1698`, `:461,481`, `:405-417` |
| manage ops importable from `sam.manage` | `create_allocation`, `exchange_allocations`, `extend_project_allocations`, `renew_project_allocations` are **not** re-exported — import from the submodules |

Also newly relevant: the **legacy-compat blueprint policy** (`CLAUDE.md:199-203`) — "DO NOT REFACTOR,
response bytes must not change". The XRAS blueprint joins that class and should say so in its module
docstring. Adding an ORM model to `src/sam/__init__.py` **auto-registers a Flask-Admin view**, which
gives a zero-cost first cut of the audit UI. `tests/unit/test_route_map_parity.py` pins dashboard
routes to a snapshot — regenerate with `ROUTE_MAP_REGEN=1` when the admin tab lands.

---

## 6. Implementation plan

Ordered by **production volume × failure rate**, which is the main structural change from the
earlier revision.

### Phase 0 — Prerequisites (these gate everything)

1. **Create `xras_action_log` in the production `sam` schema, out-of-band.** A deliberate one-time
   exception to `CLAUDE.md:808` — the DDL goes through the normal DBA path and *then* the ORM
   follows it, which is exactly the rule the repo states. Sequence: agree the DDL → create in prod →
   add the model to `src/sam/integration/xras.py`, export from `src/sam/__init__.py` → add a
   `tests/integration/test_schema_validation.py` case → add a PII scrubbing rule to
   `containers/sam-sql-dev/anonymize_sam_db.py` → regenerate
   `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz` so the test DB has the table.
2. **SMTP from the k8s webapp.** Nothing exists. Either lift `EmailNotificationService` into
   `src/sam/notifications/` (dropping the hardcoded `Bcc`) and give the webapp `MAIL_*` config, or
   accept DB-only audit for v1. Legacy sends ~3 emails per action — `XrasActionLogger` lacks
   `additivity="false"`, so every event also reaches the root `SMTPAppender` at
   `sweg-notify@ucar.edu`. **The new system should send one.**
3. **Role enforcement.** Add an XRAS-local `xras_api_required` wrapping the token path of
   `login_or_token_required` and asserting `'ROLE_XRAS' in g.api_key_roles`. Do **not** change
   `login_or_token_required`'s existing behaviour — other consumers depend on it.

### Phase 1 — Read endpoints (94% of traffic, zero write risk)

New package `src/webapp/api/xras/` (`__init__.py`, `people.py`, `requests.py`), registered in
`src/webapp/run.py` with `url_prefix='/api/xras/v1'`. Module docstring declares legacy-compat
status. Blueprint-local error handlers reproduce the byte-exact legacy bodies (§2.2, §2.5).

1. **XA-header shim** — blueprint `before_request` implementing §2.2 exactly, including the
   "only one header ⇒ no synthesis, headers still stripped" case.
2. **`GET /people/{username}` and `GET /people`** — highest volume, build first.
   - Query **base tables** in a new `src/sam/queries/xras.py`, porting the `identityServicePersons`
     named query — **not** `XrasUserView` (§4.1).
   - Bare object / bare array, no envelope. Omit null fields. 404 body
     `{"message":"username=<u> not found","result":null}`.
   - Keep the `login_type_id = 1` filter and **no** active/deleted filter (§7 D2).
   - Org fixup: `UCAR/NCAR:<acronym>` → parentage walk.
   - The 3.8 MB roster must not be materialized twice; budget ≤ 1.1 s.
3. **`GET /requests/request/{n}`** — port `RequestFactory` assembly. Read `xras_request` (after the
   fix) and `xras_action`; compute `remainingAmount` with a **project-scoped** query rather than
   touching `xras_allocation`/`xras_hpc_allocation_amount` wholesale (§4.1). `requestType` = "New"
   for the earliest begin date per project, else "Renewal". Amounts are `"%.1f"` **strings**.
4. **Spec-obligation reads** — `requests/user/{u}`, `requests/role/{r}/{u}` (lowercase
   `pi`/`co_pi`/`allocation_manager`; `co_pi` returns empty), `dates/requests/{list}`
   (**epoch-millis**). Zero traffic, but they are contract obligations, not dead code.
5. **Fix the `xras_request` view** — drop `ORDER BY al.end_date`; un-skip
   `tests/integration/test_views.py:95-111` and delete the bare `except`.

### Phase 2 — Action ingestion + audit trail

1. **`xras_action_log`**: `id`, `received_time`, `remote_actor`, `action_type`, `request_number`,
   `raw_payload`, `status` (`processed|manual|failed|replayed`), `error_messages`,
   `projcode_result`, `processed_time`, `processed_by`. Payloads carry PII — the scrubbing rule from
   Phase 0 must land before any snapshot regeneration.
2. **`src/sam/schemas/forms/xras.py`** — `XrasActionSchema` plus nested
   Resource/Role/Person/Fos/Panel/Grant schemas, with the legacy tolerances of §2.4:
   `unknown=EXCLUDE`, absent strings → `""`, number-into-string coercion, and the forgiving boolean
   for `isAccountToBeCreated` only. Export from `forms/__init__.py`.
3. **`POST /v1/actions`** — parse (400 on malformed JSON) → **persist the log row before dispatch**
   → dispatch → 200 `{"message":"OK","result":null}` / 422 with the real error list / 500. Every
   inbound action is persisted regardless of outcome, which is what makes replay possible.

### Phase 3 — Handlers, in production-frequency order

All inside `management_transaction`; every allocation mutation through
`log_allocation_transaction`.

**Solve the actor problem first.** Legacy writes `allocation_transaction.user_id = NULL` for XRAS;
`log_allocation_transaction` requires a `user_id`. Either permit `None` for integration actors or
mint a service user — decide once, because it affects every handler and every parity diff against
legacy rows.

1. **Extension (60% of posts, 98.5% success)** — build first, on the easy path. Legacy extends the
   latest allocation of every **active account** to `actionEndDate` and **errors** if that would
   shrink any; payload resources are ignored. `extend_project_allocations` is tree-scoped and
   silently skips shrinks, so it is **not a drop-in** — add an account-scoped variant or a strict
   mode. Expect ~3.3 allocations touched per action. Use comment `XrasAction Extension Request`.
2. **New (21% of posts, 30% success)** — the failure hot spot. Port against
   `projects_routes.py:600-687`: allocation-type extractor → mnemonic → AOI →
   `next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` → contracts →
   `create_allocation` per resource (start clamped ≥ commission date, end-of-day end) →
   `add_user_to_project` (**after** accounts exist — it raises otherwise) → set `active=False`.
   - Allocation type: transcribe §3.2 verbatim into a data-driven rule table resolving to
     `(panel, type)` pairs.
   - Mnemonic: reuse `MnemonicCode.resolve_for_institution/organization`. Surface failures as
     structured 422 errors, never an opaque 500.
   - Contracts: use `Contract.existing_by_number` with an explicit policy for the 3 known ambiguous
     cores (§4). Legacy hard-fails here where AMIE parks a human task — treat an unresolvable grant
     as a **reviewable warning**, not a fatal error.
3. **Supplement (15%, 100% success)** — create the allocation if none exists (start today, end =
   latest contract/allocation end), else supplement when `> 0`; log-warn on `≤ 0` rather than
   legacy's silent drop.
4. **Update path (New/Renewal on an existing project, 3%)** — field updates, contracts, per-resource
   create/extend/supplement/adjust, and the `AUTO_DEFAULT_ALLOCATION_TRANSACTION` undo kludge
   (compensating `UNDO AUTO/DEFAULT` adjustment; 33 such rows exist in the last 2 years). Must
   tolerate the **no-op case** — 2 of 109 successful posts changed nothing.
5. **Adjust** — implement. It is a Supplement variant reusing the same primitives, so the marginal
   cost is small and it closes a spec obligation. Legacy silently drops negatives; we log-warn and
   record them.
6. **Transfer — defer** (§7 D3). Route to the manual-fallback path with an explicit audit row and
   email. Legacy semantics, recorded so the deferred work is fully specified: 1 negative source +
   ≥1 positive destinations, same project, Σ = 0, source clamped to available.

### Phase 4 — Admin surface

Ship the free Flask-Admin view first (a side effect of registering the model). Then an
Integrations → XRAS tab modelled on the contracts pages
(`src/webapp/dashboards/admin/contracts_routes.py` + `crud.py`'s `CrudSpec`): list with status
filters, detail view (pretty payload + errors), **replay**, **activate project**, and a paste-JSON
manual post for parity with legacy's `XRASPostBean`. Regenerate the route-map snapshot.
Then `sam-admin xras` (`--list-pending`, `--replay <id>`, `--validate-mapping`) following the
three-module domain pattern in `src/cli/README.md:137-168`.

### Phase 5 — Parity and staged cutover

1. **Golden tests.** The repo has exactly one real payload (§2.4). Richer real payloads exist only
   as `XRAS_post_action.json` attachments in the `hdt@ucar.edu` / `sweg-notify@ucar.edu` mailboxes —
   legacy emails the raw body on **every** action, success or failure. **Harvesting a handful of
   those is the single cheapest way to de-risk Phase 3** and should happen before the handlers are
   written.
2. **Read parity script.** Diff normalized JSON, legacy vs new, for `/people` (all 28,253 rows),
   `/people/{u}` over the 378 usernames that resolved and the 536 that 404'd in the last 30 days,
   and `requests/request/{n}` over a project sample.
3. **Staged, per-endpoint cutover** (enabled by §4.2):

   | Step | Move | Why here | Rollback signal |
   |---|---|---|---|
   | 1 | `GET /people/{username}` | 94% of traffic, read-only, cheap | 404 rate departs from the ~30% baseline; p50 > 100 ms |
   | 2 | `GET /people` (roster) | one call/day at 03:00 — a full day of observation per attempt | roster size departs from ~3.84 MB ±0.2% |
   | 3 | `GET /requests/*` | ~1 call/month; near-zero blast radius | any 500 |
   | 4 | `POST /actions` | last: the only writing surface | `xras_action_log` shows a status the 30-day legacy corpus never produced |

   Provision the `ROLE_XRAS` `api_credentials` row for the new app before step 1 (username column is
   `varchar(11)`; legacy uses `XRAS`). Legacy stays hot at every step — a rollback is a proxy
   change, not a data migration.

---

## 7. Decisions taken

- **D1 — `xras_action_log` lives in the production `sam` schema**, DDL created out-of-band, ORM
  follows. Recorded as a deliberate exception to the repo's no-schema-changes rule, with the
  rationale that the audit trail is the core value of this project and belongs next to the data it
  describes (it also earns a free Flask-Admin view and can be FK'd and joined).
- **D2 — `GET /people` stays bug-for-bug**, inactive users included. XRAS's identity matching may
  depend on resolving historical usernames, and a 404 where a 200 used to be is a change we cannot
  observe from our side. A filter is a separate, later conversation with ACCESS; the roster
  byte-diff is the parity guard in the meantime.
- **D3 — `Adjust` implemented, `Transfer` deferred** to the manual-fallback path with an audit row.
  Transfer has zero traffic and `exchange_allocations` doesn't fit its semantics, so parity needs
  new allocation machinery rather than an adapter. The audit log tells us the moment traffic
  appears.
- **D4 — staged per-endpoint cutover**, `/people/{u}` → roster → `/requests/*` → `POST /actions`.

---

## 8. Verification

- **`pytest`**
  - `tests/api/test_xras_api.py` — XA-header shim including the one-header case; byte-exact 401
    (including the space before the colon and the absent `WWW-Authenticate`); bare-array and
    bare-object shapes; null omission; 404 body; action status codes 200/400/422.
  - `tests/unit/test_xras_actions.py` — each handler against factories, plus golden payloads.
  - `tests/integration/test_schema_validation.py` — the new `xras_action_log` table.
  - `tests/integration/test_views.py::TestXrasViews` — with the `xras_request` skip and its bare
    `except` removed.
- **Manual** — `docker compose up webdev --watch`, then
  `curl -H 'XA-REQUESTER: XRAS' -H 'XA-API-KEY: …' localhost:5050/api/xras/v1/people/benkirk`;
  post a sample action; replay it from both the dashboard and the CLI.
- **Roster byte-diff against legacy before cutover.** The null-omission and org-fixup rules make
  total size a sensitive single-number regression check — expect ~3.84 MB ±0.2%.
- **Latency budget**, from measured legacy: `/people/{u}` ≤ 100 ms p50; roster ≤ 1.2 s;
  `POST /actions` ≤ 400 ms p50 (legacy's tail is inflated by synchronous SMTP, so the new one should
  beat it).

---

## 9. Residual risks

- **The payload corpus is thin.** One real sample exists in the repo. Harvesting real payloads from
  the notification mailbox (Phase 5.1) is also the only way to settle three questions left open by
  earlier triage: the `roleType` carried by stale placeholder entries; whether `isReconciled` and
  `isAccountToBeCreated` are populated in practice; and the actual `beginDate`/`endDate` format —
  legacy compares them with lexicographic `String.compareTo`, which is correct **only** for
  zero-padded ISO-8601.
- **`user_organization` is still frozen** (nothing since 2026-07-09; 4,563 active users with no
  current organization). It causes 24% of legacy's XRAS failures and is outside this project's
  scope, but the port must surface it as a reviewable 422 — otherwise we ship the same invisible
  failure with better plumbing.
- **The 400/422 error-contract change needs confirmation from `allocations@access-ci.org`** before
  cutover step 4. Broker retry behaviour on 4xx is unknown.
- **Two legacy defects worth not reproducing.** `XrasAction.getUsernameByRoleType()` returns the
  first matching role and ignores duplicates — the ACCESS docs state a request must have exactly one
  PI, so SAM should reject rather than pick-first (a mis-ordered array could otherwise mint a
  project under the wrong human). And organization 158 "UCAR Community Programs" matches two
  mnemonic codes (`CAR`, `UCP`), which throws for any PI in that organization.
- **Six XRAS-relevant resources are unmapped** in `xras_resource_repository_key_resource`
  (§4). Add a `sam-admin xras --validate-mapping` check and run it before cutover.

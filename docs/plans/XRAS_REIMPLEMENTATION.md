# XRAS Integration Reimplementation (Python)

## Context

Legacy SAM (Java/Tomcat, deployed build **2.0.3**) is NCAR's site-side
server for the XRAS allocation integration. The XRAS broker at
https://admin-ncar.xras.org/ **pushes** allocation decisions to SAM
(`POST /api/xras/v1/actions`) and **pulls** identity and request data
from it (`GET /api/xras/v1/people*`, `/requests/*`). It is one of the remaining
major legacy surfaces not yet ported.

The port is a **drop-in replacement** — same URLs, same auth headers, same response bytes — with two
deliberate improvements: **structured error responses** (422 carrying the real validation messages,
instead of a 500 carrying an opaque timestamp), and a **DB-backed audit trail with an admin UI**,
replacing a workflow whose only record is an email and whose only replay mechanism is pasting JSON
into a form.

**Provenance.** Every number here is measured, not inferred. Sources: 30 days of access,
application and XRAS-action logs on `sam-tomcat.ucar.edu` (**2026-07-07 → 2026-08-05**; retention is
a hard 30 days with no archives), read-only queries against the production database
(`sam-sql.ucar.edu`), live HTTP probes of the running endpoint, and the deployed source at tag
`2.0.3`.

**Which legacy checkout to read.** `~/codes/sam` is currently on `project_structure_AI_docs`
(`git describe` → `2.0.3-16-g8c07316f9`). **No checkout switch is needed:**
`git diff 2.0.3..HEAD` over `src/main/java/edu/ucar/cisl/sam/xras`,
`src/main/resources/spring` and `src/main/resources/hibernate/xras` is **empty** — the XRAS code,
security config and named queries in the working tree are byte-identical to the deployed tag. The
16 commits since `2.0.3` are documentation and investigation only.

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
| **Adjust** | **0** | — | — |

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

### 1.4 The manual-fallback path never fires

`ManualFallbackActionPostService` is reachable *only* via `catch (BadRequestException)` — i.e.
`ProjectActionServiceSelector` finding no serviceable, which is what an `Adjustment` or `Advance`
actionType would produce. (Note the selector's guard string is `"Adjust"`, not `"Adjustment"`, and
there is no `"Advance"` serviceable at all.) It logs only at `LOG.debug()`, which is suppressed, so
it cannot be grepped; detected instead by comparing access-log 200s against
`EmailingActionPostService` INFO lines per day. **Δ = 0 on every day with coverage — zero
invocations in 30 days.**

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
  500, not 400.
- #7 accepts **only** `pi` (`equalsIgnoreCase`, so `PI`/`Pi` also match); anything else is
  `NotFoundException` → 404. **It is not a roster endpoint** — it calls
  `RoleService.setLeadUserRole(requestNumber, username)`, i.e. it *reassigns the project lead*.
  There is no XRAS endpoint for adding a co-PI or an ordinary member. Rosters arrive whole, in
  the `roles[]` array of `POST /actions` — see §3.5. (And the ACCESS spec's
  `DELETE /v1/roles/…` is unimplemented, so revocations never reach SAM at all.)
- #8 takes `@RequestBody String actionJson` and calls `new ObjectMapper().readValue(...)` itself — a
  second, unconfigured mapper. Parse failure → `RuntimeException` → 500.
- The ACCESS/XRAS spec documents `POST /v1/actions/<actionId>/<requestId>/<actionType>`, but **all 175
  real posts go to bare `/api/xras/v1/actions`**, the only form SAM maps. If the broker is ever
  corrected to match its own docs, every post 404s — **map both forms defensively.**
- Spec endpoints SAM does not implement, and which are out of scope here: `GET /test_auth`,
  `GET /v1/usage/by_month/…`, `DELETE /v1/roles/…` (so role *revocations* never reach SAM), and the
  `/v1/users/…` family.

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
`role_api_credentials` → `role`. Production holds `XRAS` (id 2, enabled, `ROLE_XRAS`) and a disabled
`XRAS_OLD` (id 1). `api_credentials.username` is `varchar(11)`.

**401** — byte-exact, verified with `od -c`:

```
Content-Type: application/json;charset=UTF-8
Content-Length: 41
(no WWW-Authenticate header — deliberate, per XrasAuthenticationEntryPoint's javadoc)

{\n  "message" : null,\n  "result" : null\n}
```

Note the **space before the colon** (Jackson's `DefaultPrettyPrinter`) — and note that this is the
*only* pretty-printed body on the surface; every 200 and the `/people/{u}` 404 are compact.

This body is returned for **unmapped paths too** — but only while unauthenticated. Measured
2026-08-05 at `/api/xras/v1/test_auth`:

| Credentials | Response |
|---|---|
| none, or wrong | **401**, the 41 B JSON above — the filter and security chain run before routing |
| valid `ROLE_XRAS` | **404, 431 B of Tomcat HTML** — auth succeeds, then routing fails, and `web.xml` declares no `<error-page>` |

So the same 431-byte Tomcat error page that §2.2 describes for the 403 case also serves 404s. An
earlier draft stated flatly that unmapped paths return "401, not 404"; that holds only for the
unauthenticated case, which is the only one the access logs could show.

**403** — valid credentials without `ROLE_XRAS` produce **431 bytes of Tomcat HTML**, not JSON:
Spring's default `AccessDeniedHandlerImpl` calls `sendError(403)` and `web.xml` declares no
`<error-page>`. Verified live across `/people`, `/people/{u}`, `/requests/request/{n}` and
`/dates/requests/{n}`. **Do not reproduce it** — return the JSON envelope with 403; no real client
depends on an HTML body it has never received.

### 2.3 Response shapes

**`PersonDTO`** (endpoints 1–2): `username, firstName, middleName, lastName, organization,
academicStatus, phone, email` — all strings.

**Null fields are omitted.** Confirmed by predicting the nightly roster's byte count from the
database both ways: nulls-omitted predicts 3,845,112 B against an observed 3,839,790 B (0.14%,
the residual explained by `IdentityServiceImpl.fixInternalOrg()` shortening `UCAR/NCAR:<acronym>`
strings); nulls-emitted predicts 5,219,474 B, off by 36%.

**Roster row order is `users.user_id` ascending.** The `identityServicePersons` named
query has **no `ORDER BY`**, so legacy's order is a MySQL artifact of `GROUP BY`.
Confirmed two ways: production's first eight usernames
(`bruceb robted fulker rodi kubo mbetsill clw remmel`) are byte-for-byte the first eight
the local dev DB emits through the view, and both match `ORDER BY user_id` — **not**
`ORDER BY username`. Our port should state it explicitly, which reproduces the observed
3.8 MB and makes it deterministic rather than incidental.

**There are two different 404 bodies, with different wording.** `/people/{u}` misses emit
`username=<u> not found`; `/requests/user/{u}` and `/requests/role/{r}/{u}` also validate
the username (`RequestServiceController.validateUser`) but emit `User <u> not found`.
The role check runs **before** user validation, so an unrecognised `{role}` 500s even for
a non-existent user.

**`GET /people/{u}` 404**: `{"message":"username=<u> not found","result":null}`, and its length is a
closed form — **`bytes = len(username) + 47`**.

> ⚠️ **Corrected 2026-08-05.** An earlier draft said `+ 58`, inferred from 981 access-log samples.
> Measured directly against production with the `samuel` credential, the constant is **47**: exact
> at username lengths 2, 5, 11, 15 and 25 (49, 52, 58, 62, 72 bytes). Counting the literal
> `{"message":"username=` + `<u>` + ` not found","result":null}` gives 21 + n + 26 = n + 47, which
> agrees. The `+58` figure is wrong by 11 and must not be used as a parity assertion.

**`GET /requests/request/{n}`** → `ResponseWrapper{message: null, result: {...}}`. Note that
`{requestNumber}` **is the projcode** — matching `projcode = trimToNull(requestNumber)` on the
POST side (§2.4). The `xras_request` view has no `requestNumber` column at all; its identifying
column is `projectId varchar(30)`, and `requestsByProjectCode`
(`2.0.3:src/main/resources/hibernate/xras/namedQuery.xml:89`) keys on it. Do not build a separate
request-number lookup.

```
projectIdLabel : null
masters[]      : { requestNumber, requests[] }        # an ARRAY — getMasters() returns .values()
  requests[]   : { requestType,                        # "New" for earliest begin per project, else "Renewal"
                   requestBeginDate, requestEndDate,   # "yyyy-MM-dd" strings
                   allocationType, projectTitle, projectId,
                   xrasActionIds,                      # ALWAYS ABSENT — never emitted (see below)
                   fos[]         : { xrasFosTypeId, isPrimary: true },
                   allocations[] : { actionType,       # ALWAYS ABSENT — never emitted
                                     allocationBeginDate, allocationEndDate,
                                     allocatedAmount,  # STRING, "%.1f"
                                     remainingAmount,  # STRING, "%.1f"; omitted when null
                                     resourceRepositoryKey,  # INT; omitted when null
                                     actions[] : { orderApplied,   # 1-based, assignment order
                                                   actionType,
                                                   amount,       # STRING "%.1f"; OMITTED when null
                                                   endDate,      # OMITTED when null
                                                   dateApplied } } }
```

**Why the omission is per-DTO and not a global setting.** `xras-rest-context.xml` is
`<mvc:annotation-driven/>` and nothing else — no `<mvc:message-converters>`, no
`ObjectMapper` bean. So the mapper is Spring's stock build, whose inclusion is
**`ALWAYS`**, and `NON_NULL` is applied **per class** by
`@JsonSerialize(include=NON_NULL)`:

| Class | Annotation | Behaviour |
|---|---|---|
| `ResponseWrapper` | none | **emits** `message: null` |
| `AccountingRequestResponse` | none | **emits** `projectIdLabel: null` (nothing ever assigns it) |
| `RequestMaster`, `FieldOfScience` | none | emit (no field is ever null in practice) |
| `RequestDatesDTO` | none | **emits** — `requestEndDate` can legitimately be null |
| `PersonDTO`, `Request`, `Allocation`, `Action` | `NON_NULL` | **omit** |

A port that applies one global "drop nulls" pass is wrong in both directions. Note also
that the empty string is **emitted**, not omitted — one roster email proves `"" ≠ null`.

**Null omission applies to this response too, not just `PersonDTO`** — measured 2026-08-05 across
65 request objects and 246 allocation objects from five live responses:

Re-measured 2026-08-06 across the **full** captured corpus — 134 request, 555 allocation
and 1,109 action objects from all seven `requests/*` probes:

| Field | Present | Consequence |
|---|---:|---|
| `xrasActionIds` | **0 / 134** | never emit the key. A Python impl writing `"xrasActionIds": null` breaks byte parity on every response |
| `allocations[].actionType` | **0 / 555** | same (also `xrasActionId`, `xrasActionResourceId` on both `Allocation` and `Action` — `RequestFactory` never sets them) |
| `resourceRepositoryKey` | **376 / 555** (68%) | ⚠️ an earlier draft listed this as always-present. It is omitted whenever null — see §4.1, this *is* the unmapped-resource gap surfacing on the wire |
| `remainingAmount` | **243 / 555** (44%) | as documented: HPC-only, omitted when null |
| **`actions[].amount`** | **811 / 1109** (73%) | ⚠️ **an earlier draft listed this as always-present** |
| **`actions[].endDate`** | **867 / 1109** (78%) | ⚠️ same |
| every other field above | 100% | always emitted |

> ⚠️ **Corrected 2026-08-06.** This table previously ended "every other field above —
> 100% — always emitted", which is wrong for the two `actions[]` fields. Their presence
> tracks `actionType`, because the underlying `allocation_transaction` columns are
> populated per transaction kind:
>
> | `actionType` | n | `amount` | `endDate` |
> |---|---:|---|---|
> | `New` | 554 | ✓ | ✓ |
> | `Extension` | 301 | ✗ (298/301) | ✓ |
> | `Supplemental` | 174 | ✓ | ✗ |
> | `Adjustment` | 78 | ✓ | ✗ (67/78) |
> | `Transfer` | 2 | ✓ | mixed |
>
> Emitting them unconditionally breaks parity on **most** request responses — `Extension`
> alone is 27% of all action objects.

Unknown request number → **200 with empty `masters`** — confirmed live:
`{"message":null,"result":{"projectIdLabel":null,"masters":[]}}`, 62 B. `RequestFactory` throws
`IllegalStateException` (→ 500) when the `allocationIds` CSV on `xras_request` fails to reconcile
against `xras_allocation`/`xras_action`, and `RequestDTO.getXrasFosTypeId()` NPEs on a null column.

**`requestType` is fully deterministic — and the rule is now known exactly.**
`HibernateAccountingDao.setRequestTypes()` stamps every DTO `Renewal`, then per
`projectId` keeps the row with the smallest `requestBeginDate` using a **strict**
`earliest.getRequestBeginDate().after(dto.getRequestBeginDate())` comparison. A tie
therefore leaves the incumbent, so **the first row in result-set order wins**, and that
order is `xras_request`'s `ORDER BY al.end_date` **ascending**. In one sentence:

> label everything `Renewal`; then, iterating in `end_date` ASC, the first row achieving
> `min(requestBeginDate)` becomes `New`.

Verified against both documented tie cases (`UALB0006`, three requests sharing
2014-10-16; `NRAL0032`, two sharing 2022-05-02) and against all 20 masters in the
captured corpus — exactly one `New` each.

**Array ordering is data-derived in three places and a JDK artifact in the fourth.**
`RequestFactory` preserves result-set order into `LinkedHashMap`s, so:

| Array | Order | Source |
|---|---|---|
| `requests[]` within a master | `allocation.end_date` **ASC** | `xras_request` view's `ORDER BY` |
| `allocations[]` within a request | `allocation.start_date` **DESC** | `xras_allocation` view's `ORDER BY` |
| `actions[]` within an allocation | `allocation_transaction.creation_time` **ASC**, `orderApplied` = 1..n | `xras_action` view's `ORDER BY` |
| **`masters[]`** | **Java `HashMap` bucket order over the projcode keys** | `AccountingRequestResponse.masters` is a `HashMap`; `getMasters()` returns `.values()` |

The first three are reproduced byte-exactly. The fourth is **not** — see §7.

⚠️ **Consequence for §4.2's proposed view fix:** `ORDER BY al.end_date` on `xras_request`
is **load-bearing**. It sets both the `requests[]` array order (confirmed ascending in
13 of 13 observed masters) and the `New`/`Renewal` tie-break. Dropping it, as Phase 1
item 5 originally proposed, would silently reorder every `requests/*` response and move
the `New` label.

**Dates are `yyyy-MM-dd` strings everywhere except `dates/requests`**, which returns
`java.util.Date` with no date module configured ⇒ **epoch-millis integers**. Preserve the quirk —
**confirmed live 2026-08-05**, and the element key is the projcode under the name `requestNumber`:

```json
{"message":null,"result":[{"requestNumber":"UALB0006",
                           "requestBeginDate":1413439200000,
                           "requestEndDate":1853906400000}]}
```

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
contract.** Richer real payloads exist only as `XRAS_post_action.json` attachments in the
`hdt@ucar.edu` / `sweg-notify@ucar.edu` mailboxes; legacy emails the raw body on every action.

### 2.5 Status codes

Legacy handling is six `@ExceptionHandler` methods on `XrasController`; there is no
`@ControllerAdvice`.

| Condition | Legacy | Ours |
|---|---|---|
| `/people/{u}` miss | 404 `{"message":"username=x not found","result":null}` | unchanged |
| Unknown request number | 200, empty `masters` | unchanged |
| Success | 200 `{"message":"OK","result":null}` | unchanged |
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
| 4 | `actionType == "Adjust"` && project exists | As Supplement; legacy silently drops negatives |
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

### 3.5 How the project roster is built — `roles[]` is roleType-agnostic

The full membership of a new project arrives in **one place**: the `roles[]` array of
`POST /actions`. Endpoint #7 plays no part (§2.1). Two *different* readings of that same array
run, and confusing them is the easiest way to get this wrong:

| Reading | Method | Filter | Result |
|---|---|---|---|
| **Role assignment** | `getPiUsername()` / `getAllocationManagerUsername()` | `roleType` **must** equal `PI` or `Allocation Manager`, plus a date window | project **lead** / project **admin** |
| **Roster** | `getUsernames()` | **`roleType` is never examined** — date window only | **every** entry becomes a project member |

`ActionRoleName` (`2.0.3:…/action/domain/model/ActionRoleName.java`) contains exactly two
constants, `PI` and `ALLOCATION_MANAGER`. So a `Co-PI`, a `User`, or any unrecognised
`roleType` is invisible to *role assignment* but is **still added to the project** — which is
how XRAS delivers a lead + admin + N ordinary members in a single New action, as production
does today.

`AddUserToProjectActionCommandsFactory.create()` then fans the roster out **per resource** —
one `AddUserToProjectCommand` for each entry in `resources[]`, each carrying every username.
Invalid members are reported but do **not** abort: `reportInvalidUsernames()` emits
`Username %s is missing` (no such user) or `Username %s is inactive` for each, and these
accumulate through the observer like every other assembly error (§3.1).

⚠️ **The two date filters are not the same, and the difference is a latent legacy bug.**

```java
// roster — getUsernames()
if (roleBeginDate.compareTo(actionDate) > 0) continue;              // strictly excluded

// role assignment — getUsernameByRoleType()
if (roleBeginDate > actionDate && currDate <= roleBeginDate && currDate <= actionDate)
    continue;                                                        // excluded only if ALSO future
```

A role whose `beginDate` is after `actionBeginDate` **but has since started** (`currDate >
roleBeginDate`) is accepted as PI or Allocation Manager yet is **excluded from the roster** —
so legacy makes that person the project lead without giving them an account on any resource.
Decide deliberately whether to reproduce this; reporting it as a warning is probably better
than either silently copying it or silently fixing it.

Both filters compare dates with **lexicographic `String.compareTo`**, which is correct only for
zero-padded ISO-8601 — one of the open questions Phase 5.1 resolves from real payloads.

---

## 4. Data and performance constraints

### 4.1 Production data facts

| Fact | Measured | Consequence |
|---|---|---|
| `xras_user` has no active/deleted filter (only `login_type_id = 1`) | 28,253 rows, **22,039 inactive** | `/people` publishes every user who ever existed — reproduced bug-for-bug (§7) |
| `organization` null rate in `xras_user` | **79%** (22,311 rows) | downstream of the frozen `user_organization` |
| rows needing the `UCAR/NCAR:` fixup | 1,760 | port `UCAROrgNameQuery` faithfully |
| **`user_organization` is frozen** | no rows created since 2026-07-09; **4,563** active users have no current org; **2,092** rows point at a dangling `organization_id = 0` | root cause of 24% of failures. Out of scope to fix, but the port must report it as a reviewable 422, not an opaque 500 |
| **Contract suffix collisions are live** | 3 cores collide today: `1049089` (`1049089` \| `PLR-1049089`), `1744587` (`OPP-` \| `PLR-`), `2146709` (`2146709` \| `AGS-2146709`) | legacy's `LIKE '%core'` + `uniqueResult()` guarantees `NonUniqueResultException` → 500 for any grant citing these. Resolve deterministically: exact match, then unique suffix, else report |
| `allocation_type` has duplicate names | `Small` ×2, `Education` ×2 | resolve by `(panel, type)` — matches `CLAUDE.md:819` |
| `xras_resource_repository_key_resource` | **13 rows** | maps Derecho, Derecho GPU, Casper, Casper GPU, Campaign_Store, HPSS, CMIP AP + decommissioned kit. **Unmapped:** `GLADE user`, `GLADE work`, `Destor`, `Boreas`, `Gust`, `Gust GPU` — an award on any of these fails with `No resource found in SAM corresponding to key %s` |
| `fos_aoi` (FOS → AreaOfInterest) | exists in prod, **18 rows** | modelled as `FosAoi` (`src/sam/projects/areas.py:175`), referenced by no query module. Legacy does not use it — it decodes `fosNum` as an id. Prefer `fos_aoi`, falling back to legacy behaviour |
| **GID allocation is live in legacy** | pool `99000–99999`, `nextGid = 99025`; `modified_time` matches the 2026-08-05 09:58:49 XRAS post to the second | legacy allocates GIDs locally for XRAS projects (since 2026-07-16, `UMIT0083` = 99001). **`project.unix_gid` is NULL for 0 of 5,795 rows** — never leave it NULL |
| XRAS-created projects arrive `active = 0` | 21 of 23 have since been activated by hand | by design (`InactivateNewProject`); the success email is the human trigger |
| XRAS allocation transactions | `user_id IS NULL`; comment `XrasAction Extension Request` (current) / `XRAS Extension Request` (pre-2025-10) | the actor convention to preserve — see Phase 3 |
| Production `sql_mode` | `STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION` — **no `ONLY_FULL_GROUP_BY`** | views that work in prod can fail in the dev/test DB; test with `SET SESSION sql_mode='ONLY_FULL_GROUP_BY'` before trusting one |
| Email recipients | `xras.actionpost.recipients=hdt@ucar.edu` (`2.0.3:app/env/sam.complete.properties:29`) | the deployed `var/sam.complete.properties` is 0600 and unreadable |
| Feature flag | `XRAS_POST_ACTION=true` (`/tomcat/tomcat-sam/var/features.properties`) | — |

### 4.2 The XRAS views are not all usable as-is

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
  (`2.0.3:src/main/resources/hibernate/xras/namedQuery.xml:7-60`), **not the view**. ⇒ **`GET
  /people/{username}` must query base tables with the filter applied**; using `XrasUserView` would
  be a ~10× latency regression.

  Measured on the local dev DB 2026-08-06, which quantifies exactly what the pushdown buys:

  | Query | Duration |
  |---|---:|
  | `SELECT * FROM xras_user WHERE username='benkirk'` | **0.409 s** |
  | the identical SQL with the predicate applied *inside* the grouped query | **0.0007 s** |

  a **560×** difference, from one predicate moving across a `GROUP BY`.

- ⚠️ **The named query and the view are NOT equivalent — port the named query.** This is
  easy to miss because the ORM exposes the *view*, so the path of least resistance is the
  wrong one. Two substantive differences:

  | | `identityServicePersons` (what legacy serves) | `xras_user` view |
  |---|---|---|
  | username predicate | built in: `(:username IS NULL OR username = :username)` | none — callers filter on top |
  | `email` | `ANY_VALUE(COALESCE(ea1, ea2, ea3, ea4))` — a **per-row** coalesce over the four join aliases | `COALESCE(MIN(ea1), MIN(ea2), MIN(ea3), MIN(ea4))` — a **per-tier** coalesce |
  | `GROUP BY` | `u.username, firstName, u.middle_name, u.last_name, ac.description` | `u.user_id` |

  For a user with several addresses the two email expressions can select **different
  values**. Porting the view would therefore ship a silent data divergence that byte
  parity would catch only if that user happened to be in the sample.
- **`xras_allocation` costs 6–8 s regardless of filter**, because `xras_hpc_allocation_amount`
  aggregates `hpc_charge_summary` across *all* allocations before joining. This is why the single
  `requests/request/{n}` call took 7.7 s. ⇒ compute `remainingAmount` scoped to the requested
  project.
- **`xras_request` fails under `ONLY_FULL_GROUP_BY`** (error 1055). The SELECT list is safe —
  `GROUP BY p.projcode` is functionally determining via the `project_projcode_uk` unique index. The
  sole offender is **`ORDER BY al.end_date`**, which names a different expression from the
  `GROUP BY`'s `cast(al.end_date as date)`. Removing it returns all 9,489 rows under strict mode.
  **Reproduced on the local dev DB**, whose default `sql_mode` *does* include `ONLY_FULL_GROUP_BY`:
  a bare `SELECT ... FROM xras_request GROUP BY projectId` errors, and the same statement after
  `SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,NO_ENGINE_SUBSTITUTION'`
  returns 9,489 rows. So this is not a latent prod-only risk — it fails today, locally, for anyone
  who touches the view.
  Fixing the view also un-skips `tests/integration/test_views.py:95-111`, whose bare
  `except Exception` currently masks the failure (NRIT P2-63).

### 4.3 Shared database ⇒ incremental cutover

`.env.example:14` sets `PROD_SAM_DB_SERVER=sam-sql.ucar.edu`: **both applications read and write the
same production database.** A per-endpoint proxy cutover is therefore safe and reversible, with no
data divergence between steps. `sam.ucar.edu` (128.117.225.232) is fronted by
`prod-staticweb14/15.ucar.edu`, which can split on path prefix.

### 4.4 Parity oracles

**Access-log oracle — 30 days of real legacy output, no credential required.** `%b` and `%D` are
recorded, so we have the byte count of every response legacy served in the window:

| Oracle | Size | Strength |
|---|---|---|
| `GET /people/{u}` 200s | **385 usernames with a single stable byte count** over 30 days (29 more have two — their DB rows changed mid-window) | strong: any null-omission, org-fixup or missing-field bug shifts the count |
| `GET /people/{u}` 404s | **563 distinct usernames**; size is the closed form **`len(username) + 47`** (§2.3 — *not* `+58`, which this table previously carried) | total; assert the formula, no fixture needed |
| `GET /people` roster | **30 nightly points**, 3,807,879 → 3,839,790 B, rising ~1.3 KB/day | strong single-number regression check |
| `GET /requests/request/{n}` | 1 point, ever | negligible |
| `requests/user`, `requests/role`, `dates/requests` | **0** points | none |

Corpus: 3,268 request records, 413 distinct 200-path usernames, 563 404-path usernames.

**Credentialed oracle.** Four of the six GET endpoints had no production oracle at all — the
decisive reason to provision a credential. Byte-exact comparison is also the only way to catch a
length-preserving bug: swapped `firstName`/`lastName`, wrong field order, `"%.1f"` drift.

**Credential provisioned 2026-08-05** — `samuel`, `api_credentials_id = 14`, `ROLE_XRAS`, enabled,
`$2a$12$` (§Phase 0.4). All six GET endpoints have now been observed. First-pass measurements,
`https://sam.ucar.edu`, from a workstation (so timings include network RTT):

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

Confirmed against the plan as written: epoch-millis on `dates/requests`; `co_pi` empty; unknown
request number → 200 with empty `masters`; unrecognised role → 500 carrying exactly the opaque
`Unhandled SAM exception … (timestamp 1785978747040)` body that §2.5 replaces with a 422;
`"%.1f"` amount strings; `requests/user` and `requests/role` share the `requests/request` envelope.

Corrected by measurement: the 404 constant (§2.3), the omitted `xrasActionIds` /
`actionType` / `resourceRepositoryKey` keys (§2.3), and unmapped-path behaviour when
authenticated (§2.2).

**The `requests/*` family costs 6–7 s per call** — direct confirmation of §4.2's finding that
`xras_allocation` aggregates `hpc_charge_summary` across all allocations before filtering. Any
port that reads those views naively inherits this; the ~1 call/month traffic is the only reason
it has never mattered.

**Roster corroboration** (28,259 people, one call): `organization` present on **21.0%** — the exact
complement of §4.1's measured 79% null rate. Field order is a strict subsequence of
`username, firstName, middleName, lastName, organization, academicStatus, phone, email` in
**all 28,259** records, across 19 distinct observed key orders — so a fixed field order plus
null-dropping reproduces the bytes. 1,753 rows carry the `UCAR/NCAR:` fixup and **zero** raw
`UCAR/NCAR:` strings survive in the output, so `fixInternalOrg()` is applied unconditionally.

Provisioning facts, verified against production:

- `api_credentials`: `api_credentials_id` int unsigned **auto_increment**; `username`
  **varchar(11)** UNIQUE (`idx_api_credentials_uniq`); `password` char(64) holding a **60-char
  `$2a$` bcrypt**; `enabled` tinyint. Max id is 12.
- `role_api_credentials`: auto_increment PK, UNIQUE `(role_id, api_credentials_id)`, FKs to both.
  **`ROLE_XRAS` is `role_id = 10`.**
- **No credential caching.** `<security:jdbc-user-service>` declares no `cache-ref` and no
  `user-cache`/`UserCache` appears in any security config, so `JdbcDaoImpl` uses `NullUserCache`:
  **a new row is live on the very next request, no Tomcat restart.** The Python side caches for
  `API_KEYS_DB_TTL` (default 60 s).
- The exact provider is the second `<security:authentication-provider>` in
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
- `scripts/gen_api_key.py` emitted the library default **`$2b$`** and now takes
  `--prefix {2a,2b}` (default `2b`), plus `--sql` to print the provisioning statements with the
  hash already substituted and both PKs resolved at runtime. Prefer `$2a$` for
  `api_credentials` rows: it is what all 12 existing rows use.
  ⚠️ **Correction to an earlier draft of this document:** `$2b$` is *not* actually rejected.
  The deployed stack is Spring Security **5.8.12** (`2.0.3:pom.xml:61`), whose
  `BCryptPasswordEncoder.matches()` accepts `$2(a|y|b)?$`, and `helm/values.yaml:253` already
  ships a working `$2b$` hash to the Python side. `$2a$` is a consistency choice, not a
  correctness requirement — do not treat it as a blocker.

---

## 5. Building blocks in Python SAM

Reuse these; do not rebuild. Line references verified against the current checkout.

**ORM / views** — `XrasResourceRepositoryKeyResource` (`src/sam/integration/xras.py:9`); six view
models in `src/sam/integration/xras_views.py` (`XrasUserView:23`, `XrasRoleView:50`,
`XrasActionView:72`, `XrasAllocationView:97`, `XrasHpcAllocationAmountView:122`,
`XrasRequestView:144`), exported from `src/sam/__init__.py:186-192`. Smoke tests at
`tests/integration/test_views.py:31-111`.

**API recipe** — `src/webapp/api/v1/queue.py`, `wallclock_exemption.py`: module docstring naming the
legacy endpoint, `bp = Blueprint(...)` immediately followed by `register_error_handlers(bp)`,
`@bp.route` → `@login_or_token_required(...)` → `@cache.cached(...)`, query logic in `sam/queries/*`,
and a `POST /refresh` with `@csrf.exempt`. Registered in `src/webapp/run.py` (imports `:29-41`, API
block `:405-417`). These are **legacy-compat blueprints** under `CLAUDE.md:199-203` — "DO NOT
REFACTOR, response bytes must not change". The XRAS blueprint joins that class and its module
docstring should say so.

**Auth** — `login_or_token_required` (`src/webapp/utils/api_auth.py:169`), `ApiCredentials`
(`src/sam/security/roles.py:65`) with `as_api_key_map` (`:91`) already resolving role **names**, and
`g.api_key_roles` populated at `api_auth.py:129` — but **no role enforcement exists anywhere yet**.
The 401 helper `_auth_challenge` (`api_auth.py:48`) emits `{'error': …}` **with**
`WWW-Authenticate`, and `register_error_handlers` (`src/webapp/api/helpers.py:16-42`) has **no 422 or
500 handler** — XRAS therefore needs blueprint-local handlers rather than the shared ones.

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
(`src/sam/core/organizations.py:445/461/481`),
`Contract.existing_by_number` (`src/sam/projects/contracts.py:249` — bulk exact-match, the right
primitive for grant resolution), `ProjectContract.create` (`:468`),
`FosAoi` (`src/sam/projects/areas.py:175`), `AllocationType`
(`src/sam/accounting/allocations.py:483`).

**A working reference for the New handler already exists:**
`src/webapp/dashboards/admin/projects_routes.py:600-687` performs, inside one
`management_transaction`, exactly the sequence the New handler needs —
`next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` →
`ProjectContract.create` → `ProjectOrganization.create`. **Port against that, not from scratch.**

**Parity harness** — `utils/parity/` already compares five legacy Systems Integration APIs against
the deployed Python stack: `check_legacy_apis.py` (CLI: `--api`, `--format json`, exit codes
0/1/2/130), `clients.py` (Basic-Auth session wrappers for both hosts), `comparators.py` (~40 rules
returning `CheckResult`), `helpers.py`, `README.md`. Env: `SAM_LEGACY_USER`/`PASS`,
`SAM_NEW_API_USER`/`PASS`. **XRAS is an `--api xras` extension of this, not a new script.**
⚠️ Its five existing comparators are deliberately *tolerant* (one-directional subset checks, ±5% on
usage, ±1 day on dates) to absorb DB-mirror lag. **XRAS has no such lag (§4.3), so its comparator
demands byte-exact equality** — the strictest check in the harness.

**Allocations dashboard** — blueprint `allocations_dashboard`, `url_prefix='/allocations'`
(`src/webapp/dashboards/allocations/blueprint.py:46`), registered at `src/webapp/run.py:398`. Unlike
the admin dashboard it has **no sub-route modules** — all 1,132 lines are in one `blueprint.py`.
Three tabs today: Projects, Transactions, Adjustments. The tab strip is the shared `page_tabs` macro
(`templates/dashboards/fragments/page_tabs.html:20-36`) driven by a literal list in
`templates/dashboards/allocations/base_allocations.html:21-25`; tabs are real routed `<a href>`s, so
they are URL-addressable and deep-linkable. A **parallel** nav registry lives at
`src/webapp/utils/nav.py:145-159` — `nav.py:9-12` states explicitly that both lists are maintained
separately.

**Email** — there is none in the webapp: zero `MAIL_*` / `flask_mail` / `smtplib` hits under
`src/webapp/` or `src/sam/`. The only mailer is `src/cli/notifications/email.py` (stdlib `smtplib` +
Jinja2, with a hardcoded `Bcc: benkirk@ucar.edu` at `:127,:138`).

**Testing** — `tests/factories/security.py:make_api_credentials(..., roles=())` already builds
`Role` + `RoleApiCredentials`, so the `ROLE_XRAS` auth tests need no new factory. Adding an ORM model
to `src/sam/__init__.py` **auto-registers a Flask-Admin view**.

---

## 6. Implementation

Phases are ordered by **production volume × failure rate**.

### Phase 0 — Prerequisites (these gate everything)

1. **Create `xras_action_log`, dev first, production later.** `CLAUDE.md:38,808` require that the
   database is the schema source of truth and the ORM follows it. That still holds — but it does
   **not** mean production must be first, and production *cannot* be first: the prod writer account
   holds `SELECT, INSERT, UPDATE, DELETE` and **no DDL** (`scripts/repair/RUNBOOK-missing-projects.md:36-38`),
   so a `CREATE TABLE` there is a DBA request with its own lead time. Sequencing Phase 2 behind that
   ticket buys nothing, because the table's shape is the thing under design.

   Sequence: agree the DDL → **`CREATE TABLE` by hand on the local dev DB** → add the model to
   `src/sam/integration/xras.py`, export from `src/sam/__init__.py` → add a
   `tests/integration/test_schema_validation.py` case → **then** raise the prod DDL request and
   backfill → add a PII scrubbing rule to `containers/sam-sql-dev/anonymize_sam_db.py` →
   regenerate `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz` so CI has the table.

   ⚠️ The scrubbing rule must land **before** the next snapshot regeneration — `raw_payload`
   carries PII — and regenerating that blob has its own blast radius on fixture-dependent tests.
2. **SMTP from the k8s webapp.** Lift `EmailNotificationService` into `src/sam/notifications/`,
   drop the hardcoded `Bcc`, and give the webapp `MAIL_*` config — or accept DB-only audit for v1
   and add email later. Smaller than §5 implies: `src/config.py:32,37` already defines
   `MAIL_SERVER` (default `ndir.ucar.edu`) and `MAIL_DEFAULT_FROM` (default `sam-admin@ucar.edu`),
   with `.env` populating both, and `src/cli/notifications/email.py` is stdlib `smtplib` + Jinja2
   with no Flask coupling. This is a move plus a config wire-up, not a build. Legacy sends ~3 emails per action (`XrasActionLogger` lacks
   `additivity="false"`, so every event also reaches the root `SMTPAppender`); **we send one.**
3. **Role enforcement — extend `login_or_token_required`.**

   > ⚠️ **Reversed 2026-08-06.** This step previously read "add an XRAS-local
   > `xras_api_required` … do **not** change `login_or_token_required` itself — other
   > consumers depend on its behaviour." That would have us reimplement the token path in
   > a second place, and the premise does not survive contact with the code:
   >
   > - There are **20 real call sites** (`queue`, `wallclock_exemption`,
   >   `directory_access`, `project_access`, `fstree_access`, `admin`), and every one
   >   passes only the positional `permission`. Keyword-only parameters with defaults are
   >   invisible to all of them.
   > - **`g.api_key_roles` has no enforcement consumer anywhere in `src/`.** The module's
   >   own docstrings call it "captured now but not yet enforced"
   >   (`api_auth.py:124`) and "for a future permission gate, but those roles are NOT yet
   >   enforced here" (`:180`). This *is* that gate. Building it privately for XRAS would
   >   leave the documented TODO open indefinitely.

   Two keyword-only, defaulted, purely additive parameters:

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

   `roles` closes the authz gap. `deny` exists because XRAS's denial *bodies* are
   contract: its 401 is a byte-exact 41-byte pretty-printed literal with
   `charset=UTF-8` and **no** `WWW-Authenticate`, where `_auth_challenge` emits
   `{'error': …}` *with* that header (§2.2). The alternative — switching the decorator to
   `abort()` so blueprint error handlers render it — would change the 401 bytes for the
   existing legacy-compat blueprints, which `CLAUDE.md` forbids.

   XRAS then needs no auth logic of its own, only an alias:
   ```python
   xras_api_required = partial(login_or_token_required,
                               roles=('ROLE_XRAS',), deny=_xras_deny)
   ```

   Note `permission` still does not apply to token callers — unchanged and out of scope;
   `roles` is the token-path analogue, not a fix for that.
4. **Provision the XRAS credential — username `samuel`.** One row in each table. This is the
   credential the Python app authenticates with at cutover, and the parity harness's credential in
   the meantime. Generate it and the SQL together:
   ```bash
   python scripts/gen_api_key.py --username samuel --rounds 12 --prefix 2a --sql
   ```
   which emits, with the hash substituted (both PKs resolved at runtime — never hardcode
   `role_id = 10`, it is an environment-specific auto-increment):
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
   Requires a **writer** account: the `.env` `PROD_SAM_DB_*` credential is `hpc-reader`, whose
   grants are `SELECT, SHOW VIEW ON sam.*` (verified live). Pass the password inline with `-p`;
   `~/.my.cnf` overrides `MYSQL_PWD` (`scripts/repair/RUNBOOK-missing-projects.md:33-36`).

   No restart needed. Verify immediately — expect **200**, not 403:
   ```bash
   curl -s -o /dev/null -w '%{http_code} %{size_download}B\n' \
        -u samuel:"$SAM_XRAS_PASS" https://sam.ucar.edu/api/xras/v1/people/benkirk
   ```
   ⚠️ Use `https://sam.ucar.edu`, **not** `https://128.117.224.130:8443` with a `Host` override —
   that address does not answer from a workstation, and the public name serves `/api/xras/*`
   correctly (a bare unauthenticated GET there returns the byte-exact 41 B 401 of §2.2). This also
   means the parity harness needs no `Host`-header or `verify=False` support, neither of which
   `utils/parity/clients.py` has.

   Seed the **same** row in the local dev DB: `api_credentials` there has 0 rows (the obfuscated
   dump ships none), so the DB-backed key path has never been exercised against webdev. Local
   `role` already carries `ROLE_XRAS`, so the SQL above applies verbatim. It is a dev seed, not a
   migration — the next snapshot restore wipes it.

   Rollback is two `DELETE`s, child row first.
   ⚠️ **`ROLE_XRAS` also permits `POST /actions`** — the security chain makes no method
   distinction. Treat the secret as a production *write* credential, and keep the `.env` holding
   it at mode 600.
5. **Add `Permission.MANAGE_XRAS`** — Phase 4 needs it, but land it here so routes can be written
   against it. All in `src/webapp/utils/rbac.py`:
   - Add `MANAGE_XRAS = "manage_xras"` to the `Permission` enum's "System administration" block,
     alongside `MANAGE_ROLES` / `MANAGE_SYSTEM_STATUS`.
   - ⚠️ **It is auto-granted to nobody.** `ALL_VIEW`/`ALL_EDIT`/`ALL_CREATE`/`ALL_DELETE` are built
     by `_perms_with_action('view'|'edit'|'create'|'delete')`, and `manage_` matches none of them.
     Add it **explicitly** to `_ALLOCATION_ADMIN` (used by both the `nusd` and `csg` bundles), or the
     tab's actions are invisible to everyone except `SYSTEM_ADMIN` holders and
     `USER_PERMISSION_OVERRIDES` entries.
   - Update `tests/unit/test_rbac.py` and any bundle-membership assertions.

### Phase 1 — Read endpoints (94% of traffic, zero write risk) — ✅ IMPLEMENTED

> **Status 2026-08-06.** All six GET endpoints are implemented on branch
> `xras_reimplementation` (PR #424). What shipped, and how it was verified:
>
> | | |
> |---|---|
> | `src/sam/queries/xras_access.py` | the five named queries, ported to base tables |
> | `src/webapp/api/xras/` | `__init__` (blueprint, XA shim, `xras_api_required`, error handlers), `serialize.py`, `people.py`, `requests.py` |
> | `src/webapp/utils/api_auth.py` | `roles=` / `deny=` on `login_or_token_required` (Phase 0.3) |
> | `utils/parity/` | `XrasClient`, `compare_xras`, `--api xras`, `--xras-user` |
> | `tests/api/test_xras_access.py` | 63 tests |
>
> **Structural validation** against the captured production corpus:
> per-master structure identical **6/6** and **5/5** for `requests/user` and
> `requests/role/pi` — every key name, key order and value type at every nesting
> depth — and `requestType` agrees on **54/54, 53/53 and 16/16** requests. The
> only difference is the declared `masters[]` ordering divergence. `co_pi`,
> unknown-request and both `dates/requests` bodies match production's byte
> counts exactly.
>
> **Latency**, against legacy's measured production numbers:
>
> | Endpoint | This port | Legacy |
> |---|---:|---:|
> | `/people/{u}` warm | **3.4–4.5 ms** | 95 ms p50 |
> | `/people` roster (~3.84 MB) | **637 ms** | 1,123 ms p50 |
> | `/requests/request/{n}` | **29 ms** | 6,100–7,300 ms |
> | `/requests/user/{u}` | **18 ms** | 6,130 ms |
> | `/dates/requests/{n}` | **2 ms** | ~400 ms |
>
> The `requests/*` speedup is the project-scoped `remainingAmount` aggregate
> (item 3 below); the output bytes are unchanged.
>
> **Not yet done:** running the parity harness against the *deployed* port —
> that needs `samuel.k8s` to carry this code, and it is the cutover gate. The
> harness itself is verified end-to-end (13/13 checks, all six endpoints,
> against production with both base URLs pointed at legacy).


New package `src/webapp/api/xras/` (`__init__.py`, `people.py`, `requests.py`), registered in
`src/webapp/run.py` with `url_prefix='/api/xras/v1'`. Blueprint-local error handlers reproduce the
byte-exact legacy bodies of §2.2 and §2.5.

**Serialization.** `jsonify` cannot be used here: Flask 3.1.3's `DefaultJSONProvider`
sorts keys alphabetically, appends a trailing `\n`, and picks separators from
`app.debug` — so `DevelopmentConfig` and `ProductionConfig` already emit different bytes
from the same call. One helper owns the wire format
(`json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=False)`), and
the envelope is a flag on it, so `/people`'s bare shape (§2.1) is
`envelope=False` rather than a separate code path. Raw UTF-8, not `\uXXXX`: the roster
carries 78 non-ASCII bytes and zero escapes.

1. **XA-header shim** — blueprint `before_request` implementing §2.2 exactly, including the
   "only one header ⇒ no synthesis, headers still stripped" case.
2. **`GET /people/{username}` and `GET /people`** — highest volume, build first.
   - Query **base tables** in a new `src/sam/queries/xras_access.py` (the `_access` suffix
     matches the legacy-compat siblings `queue_access.py` / `wallclock_exemption_access.py`),
     porting the `identityServicePersons` named query — **not** `XrasUserView`, which is
     both slower *and* semantically different (§4.2).
   - Order explicitly by `users.user_id` (§2.3).
   - Bare object / bare array, no envelope. Omit null fields. 404 body
     `{"message":"username=<u> not found","result":null}`.
   - Keep the `login_type_id = 1` filter and **no** active/deleted filter (§7).
   - Org fixup: `UCAR/NCAR:<acronym>` → parentage walk.
   - The 3.8 MB roster must not be materialized twice; budget ≤ 1.1 s.
3. **`GET /requests/request/{n}`** — port `RequestFactory` assembly. Read `xras_request` (after the
   fix) and `xras_action`; compute `remainingAmount` with a **project-scoped** query rather than
   touching `xras_allocation` / `xras_hpc_allocation_amount` wholesale. `requestType` = "New" for
   the earliest begin date per project, else "Renewal". Amounts are `"%.1f"` **strings**.
4. **Spec-obligation reads** — `requests/user/{u}`, `requests/role/{r}/{u}` (lowercase
   `pi`/`co_pi`/`allocation_manager`; `co_pi` returns empty), `dates/requests/{list}`
   (**epoch-millis**). Zero traffic, but they are contract obligations.
5. **~~Fix the `xras_request` view~~ — deferred out of Phase 1.**

   > ⚠️ **Revised 2026-08-06.** This step read "drop `ORDER BY al.end_date`". That
   > `ORDER BY` is **load-bearing** (§2.3): it sets the `requests[]` array order and the
   > `New`/`Renewal` tie-break, so dropping it would silently change the wire output of
   > every `requests/*` response.
   >
   > It is also unnecessary here. Phase 1 ports the *named queries* against base tables,
   > so `ONLY_FULL_GROUP_BY` never bites us. And fixing it for real means three unrelated
   > lead-times: a **DBA request** on production (the writer account has no DDL), plus a
   > CI-snapshot regeneration before `tests/integration/test_views.py:95-111` can be
   > un-skipped — with the blast radius that carries on fixture-dependent tests.
   >
   > Three lead-times to un-skip a test guarding code we do not use. Tracked as a
   > standalone follow-up (NRIT P2-63), not a Phase 1 gate.

### Phase 2 — Action ingestion + audit trail

1. **`xras_action_log`**: `id`, `received_time`, `remote_actor`, `action_type`, `request_number`,
   `raw_payload`, `status` (`processed|manual|failed|replayed`), `error_messages`,
   `projcode_result`, `processed_time`, `processed_by`. Payloads carry PII — the Phase 0 scrubbing
   rule must land before any snapshot regeneration.
2. **`src/sam/schemas/forms/xras.py`** — `XrasActionSchema` plus nested
   Resource/Role/Person/Fos/Panel/Grant schemas with the §2.4 tolerances: `unknown=EXCLUDE`, absent
   strings → `""`, number-into-string coercion, and the forgiving boolean for
   `isAccountToBeCreated` only. Export from `forms/__init__.py`.
3. **`POST /v1/actions`** — parse (400 on malformed JSON) → **persist the log row before dispatch**
   → dispatch → 200 / 422 with the real error list / 500. Every inbound action is persisted
   regardless of outcome; that is what makes replay possible.

### Phase 3 — Handlers, in production-frequency order

All inside `management_transaction`; every allocation mutation through `log_allocation_transaction`.

**Solve the actor problem first.** Legacy writes `allocation_transaction.user_id = NULL` for XRAS;
`log_allocation_transaction` requires a `user_id`. Either permit `None` for integration actors or
mint a service user — decide once, because it affects every handler and every parity diff against
legacy rows.

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
5. **Adjust** — a Supplement variant reusing the same primitives, so the marginal cost is small and
   it closes a spec obligation. Log-warn and record negatives rather than dropping them.
6. **Transfer** — route to the manual-fallback path with an explicit audit row and email (§7). Its
   semantics, for whenever it is built: 1 negative source + ≥1 positive destinations, same project,
   Σ = 0, source clamped to available.

### Phase 4 — XRAS as the 4th Allocations tab

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
   ⚠️ This list is maintained separately from the tab strip (`nav.py:9-12`), so **both files need the
   entry** — the easy one to miss. `tests/unit/test_nav.py:44-50` fails if the endpoint isn't a real
   route.
3. **Routes** — page + `*_fragment` + `*_details` in `allocations/blueprint.py`. Page and read
   fragments gated `@login_required` +
   `@require_permission_any_facility(Permission.VIEW_PROJECTS)`, matching the sibling tabs;
   **replay** and **activate-project** gated `@require_permission(Permission.MANAGE_XRAS)`.
   Facility-scope queries with `apply_facility_scope(...)` and `abort(403)` on out-of-scope detail
   rows, as `transaction_details` does at `blueprint.py:874-877`.
4. **Templates** — `templates/dashboards/allocations/xras.html` extending `base_allocations.html`,
   plus `partials/xras_table.html` and `partials/xras_details_modal.html` (pretty-printed payload,
   error list, status badge).
5. **Route-map snapshot** — regenerate `tests/unit/snapshots/dashboard_route_map.json` with
   `ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`.
6. **Modal-contract fixtures** — `tests/unit/test_modal_shell_contract.py`: add `/allocations/xras`
   to `PAGES_WITH_PROJECT_MODAL` (`:302-304`; anything extending `base_allocations.html` ships
   `projectDetailsModal`), and `xras_table.html` to `HTMX_FRAGMENT_SHELL_DEPS` (`:220-227`) since it
   opens `#auditDetailsModal`/`#projectDetailsModal`. Optionally extend the e2e page lists
   (`e2e/test_console_sweep.py:86-90`, `e2e/test_dark_mode.py`).

Then `sam-admin xras` (`--list-pending`, `--replay <id>`, `--validate-mapping`) following the
three-module domain pattern in `src/cli/README.md:137-168`.

### Phase 5 — Parity and cutover

1. **Harvest real payloads** from the `hdt@ucar.edu` mailbox before writing Phase 3 handlers. It is
   the cheapest de-risking available, and the only way to settle three open questions: the `roleType`
   carried by stale placeholder entries, whether `isReconciled`/`isAccountToBeCreated` are populated
   in practice, and the actual `beginDate`/`endDate` format — legacy compares them with
   lexicographic `String.compareTo`, correct **only** for zero-padded ISO-8601.
2. **GET parity** — add an `xras` comparator to `utils/parity/comparators.py`, an `XrasClient` to
   `clients.py`, and `--api xras` to `check_legacy_apis.py`. Env `SAM_XRAS_USER` /
   `SAM_XRAS_PASS` (**provisioned 2026-08-05**, documented in `.env.example`); the existing
   `SAM_LEGACY_*` account cannot reach `/api/xras/*`. Assert **byte-exact equality** (§5).
   The five mechanical edits are: an `XrasClient` in `clients.py`; `compare_xras` in
   `comparators.py`; the `comparators` import block in `check_legacy_apis.py`; a
   `_fetch_xras` plus its dispatch branch; and **both** argparse lists — the `choices`
   tuple (`:229`) and the `all` expansion tuple (`:291`) are **separate** and both need
   the new name.
   ⚠️ An earlier draft said these "are enumerated in `utils/parity/README.md`". They are
   not — the README's "five" is the five compared APIs. The list above is reconstructed
   from the source.
   ⚠️ `_BaseClient._get` calls `resp.json()` and discards the raw bytes; a byte-exact
   comparator needs a `_get_raw` returning `resp.content`. The tolerance primitives in
   `helpers.py` (`within_tolerance`, `dates_within_one_day`, `subset_diff`) are the wrong
   tool here for the same reason.

   | Endpoint | Comparison |
   |---|---|
   | `GET /people` | full-body byte equality; report size delta first, then first differing offset |
   | `GET /people/{u}` | the 385 access-log-stable usernames plus a live sample |
   | `GET /people/{u}` 404 | assert the closed form `len(username) + 47` **and** body equality |
   | `GET /requests/request/{n}` | a project sample spanning New/Renewal and HPC/non-HPC, so `remainingAmount` presence *and* omission are exercised |
   | `requests/user`, `requests/role`, `dates/requests` | byte equality on a small sample — their only validation |

3. **Zero-credential regression checks** (§4.4) — the 404 closed form, the roster byte count
   (~3.84 MB ±0.2%, +1.3 KB/day), and the 385 stable single-lookup sizes. Cheap, and they keep
   working after legacy is decommissioned.
4. **Golden corpus as pytest fixtures** — capture real legacy bytes once with the Phase 0
   credential, then run names/emails/phones through the rules in
   `containers/sam-sql-dev/anonymize_sam_db.py` before committing. ⚠️ Scrubbing must be
   **length-preserving** wherever possible, or fixture byte counts stop matching the access-log
   oracle; where it can't be, store the pre-scrub count as a separate assertion.
5. **Staged, per-endpoint cutover** (§4.3):

   | Step | Move | Why here | Rollback signal |
   |---|---|---|---|
   | 1 | `GET /people/{username}` | 94% of traffic, read-only, cheap | 404 rate departs from the ~30% baseline; p50 > 100 ms |
   | 2 | `GET /people` (roster) | one call/day at 03:00 — a full day of observation per attempt | roster size departs from ~3.84 MB ±0.2% |
   | 3 | `GET /requests/*` | ~1 call/month; near-zero blast radius | any 500 |
   | 4 | `POST /actions` | last: the only writing surface | `xras_action_log` shows a status the 30-day legacy corpus never produced |

   Legacy stays hot throughout; a rollback is a proxy change, not a data migration.

---

## 7. Design decisions

- **`xras_action_log` lives in the production `sam` schema**, created out-of-band with the ORM
  following. The audit trail is the core value of this project and belongs next to the data it
  describes, where it can be joined and FK'd — and it earns a Flask-Admin view for free.
- **`GET /people` stays bug-for-bug**, inactive users included. XRAS's identity matching may depend
  on resolving historical usernames, and a 404 where a 200 used to be is a change we cannot observe
  from our side. A filter is a separate conversation with ACCESS; the roster byte-diff is the guard
  meanwhile.
- **`Adjust` is implemented; `Transfer` is deferred** to the manual-fallback path with an audit row.
  Transfer has zero traffic and `exchange_allocations` doesn't fit its semantics, so parity needs
  new allocation machinery rather than an adapter. The audit log tells us the moment traffic appears.
- **One permanent `ROLE_XRAS` credential (`samuel`)** serves both the parity harness and the
  cutover. Accepted risk: it also permits `POST /actions`, the same exposure the existing `XRAS`
  account carries.

  This works because **both applications read the same `sam.api_credentials` table** — legacy Java
  via `<security:jdbc-user-service>` (§2.2), the Python webapp via `ApiCredentials.as_api_key_map`
  (`src/sam/security/roles.py:91`) behind `API_KEYS_DB_ENABLED`, default on. **A single INSERT
  therefore makes one secret valid against both stacks simultaneously**, which is exactly what a
  byte-for-byte comparator wants: same credential, two base URLs, no possibility that a difference
  in what the two can *see* is mistaken for a difference in what they *render*.

  ⚠️ One shadowing hazard: `_verify_api_key` checks `current_app.config['API_KEYS']` **first** and
  never falls through to the DB on a miss (`src/webapp/utils/api_auth.py:110-113`). If anyone ever
  sets `API_KEYS_SAMUEL`, the Python side silently stops consulting the row that legacy is still
  using, and the two stacks diverge on authentication alone. Today only `API_KEYS_COLLECTOR` is
  configured (`helm/values.yaml:253`), so nothing shadows `samuel` — keep it that way.

  ⚠️ **Sharper than that, once `roles=` lands** (Phase 0.3): config-sourced identities are
  returned with **`'roles': []`** (`api_auth.py:112`), unconditionally — config keys have
  no role assignments to read. So setting `API_KEYS_SAMUEL` would not merely shadow the
  DB row, it would make the `ROLE_XRAS` assertion **fail closed**: every XRAS request
  would 403 while legacy kept serving the same credential happily. Worth an explicit test.
- **`Permission.MANAGE_XRAS`** gates replay and activate-project rather than reusing
  `EDIT_ALLOCATIONS`, so it can be granted to whoever fields XRAS failures independently of general
  allocation editing.
- **Golden fixtures are scrubbed** through the existing anonymizer before being committed — the
  repo does not ship real names, emails or phone numbers. **For Phase 1 we do not commit a
  byte corpus at all:** the repo tests assert the *rules* (field order, the per-DTO null
  policy, `"%.1f"`, epoch-millis, both 404 forms) against factory data, and real bytes are
  compared live by the parity harness, where the data is real by construction and nothing
  needs scrubbing. A scrubbed corpus is worth building only if the live harness proves
  insufficient.

- **Byte parity is pursued broadly, not irrationally.** Four deliberate divergences, each
  chosen because legacy is emitting a *failure artifact* or a *JDK implementation detail*
  rather than contract:

  | # | Legacy | Ours | Why |
  |---|---|---|---|
  | 1 | 403 and authenticated-404 → **431 B of Tomcat HTML** | JSON envelope, correct status | A servlet-container artifact of a missing `<error-page>`; no client has received it on a real path |
  | 2 | `requests/role/{bogus}` → **500** with the opaque timestamp body | **400** carrying a real `message` | `IllegalArgumentException` falling into the catch-all — a client error answered with a server error. Zero traffic. Same reasoning as the 422 decision in §2.5 |
  | 3 | `masters[]` in Java **`HashMap` bucket order** | sorted by projcode | See below |
  | 4 | roster order *incidental* (no `ORDER BY`) | explicit `ORDER BY u.user_id` | Reproduces observed output **and** makes it deterministic — strictly better than legacy |
  | 5 | unmapped path under `/api/xras/v1` → **401** (41 B) unauthenticated, **404** (431 B Tomcat HTML) authenticated | Flask's own 404 (207 B HTML) in both cases | Legacy 401s because the filter and security chain run *before* routing. Flask routes first, so a blueprint `errorhandler(404)` never sees a routing miss. Reproducing it means a catch-all route that turns every typo into a 401 — worse to debug, for a case no client exercises. Measured 2026-08-06. |

  On #3: the order was reverse-engineered and **is** reproducible — emulating
  `String.hashCode()` plus `HashMap`'s spread-and-bucket walk matched all three
  multi-master captures exactly, including one where the observed order is not insertion
  order. It was rejected anyway. It is ~15 lines of JDK emulation that becomes
  **untestable above 12 masters** (where `HashMap` resizes and the bucket walk changes)
  without new probes, and it buys byte-parity only on `requests/user` and `requests/role`
  — both **zero hits in 30 days**. `requests/request/{n}` always has exactly one master
  and so is byte-exact either way. The parity comparator is byte-exact *within* each
  master and order-insensitive across them; reversing this decision later is one helper
  plus one comparator line.

---

## 8. Verification

- **`pytest`**
  - `tests/api/test_xras_api.py` — XA-header shim including the one-header case; byte-exact 401
    (space before the colon, no `WWW-Authenticate`); bare-array and bare-object shapes; null
    omission; 404 body and its closed-form length; action status codes 200/400/422.
  - `tests/unit/test_xras_actions.py` — each handler against factories, plus golden payloads.
  - `tests/integration/test_schema_validation.py` — the new `xras_action_log` table.
  - `tests/integration/test_views.py::TestXrasViews` — with the `xras_request` skip and its bare
    `except` removed.
  - `tests/unit/test_nav.py test_route_map_parity.py test_modal_shell_contract.py test_rbac.py` —
    all four pin fixtures that a new tab or permission invalidates.
- **Live parity** — `python utils/parity/check_legacy_apis.py --api xras` (UCAR VPN,
  `SAM_XRAS_USER`/`SAM_XRAS_PASS`). Exit 0 = byte-exact across all six GET endpoints.
- **Manual** — `docker compose up webdev --watch`, then
  `curl -H 'XA-REQUESTER: XRAS' -H 'XA-API-KEY: …' localhost:5050/api/xras/v1/people/benkirk`; post a
  sample action; replay it from both the dashboard and the CLI.
- **Latency budget**, from measured legacy: `/people/{u}` ≤ 100 ms p50; roster ≤ 1.2 s;
  `POST /actions` ≤ 400 ms p50 (legacy's tail is inflated by synchronous SMTP, so we should beat it).

---

## 9. Open risks

- **`user_organization` is frozen** (nothing since 2026-07-09; 4,563 active users with no current
  organization), causing 24% of legacy's XRAS failures. Fixing it is outside this project, but the
  port must surface it as a reviewable 422 — otherwise we ship the same invisible failure with
  better plumbing.
- **The 400/422 error-contract change needs confirmation from `allocations@access-ci.org`** before
  cutover step 4. Broker retry behaviour on 4xx is unknown.
- **Six XRAS-relevant resources are unmapped** in `xras_resource_repository_key_resource` (§4.1). Add
  a `sam-admin xras --validate-mapping` check and run it before cutover.
- **Three legacy defects worth not reproducing.** `XrasAction.getUsernameByRoleType()` returns the
  first matching role and ignores duplicates — the ACCESS docs state a request must have exactly one
  PI, so we should reject rather than pick-first (a mis-ordered array could otherwise mint a project
  under the wrong human). Organization 158 "UCAR Community Programs" matches two mnemonic codes
  (`CAR`, `UCP`), which throws for any PI in that organization. And the roster and role-assignment
  readings of `roles[]` apply **different begin-date filters** (§3.5), so a PI whose role starts
  after the action begin date but before today becomes project lead with no account on any
  resource.

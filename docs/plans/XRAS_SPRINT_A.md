# XRAS Sprint A — revised: real payloads in hand, capture-first ingestion

## Context

`docs/plans/XRAS_ACTION_INGESTION.md` gates two deliverables — `XrasActionSchema` (seven nested
schemas) and the New handler (21% of posts, 30% success) — on harvesting real
`XRAS_post_action.json` payloads, and tells you to pull them from `hdt@ucar.edu` /
`sweg-notify@ucar.edu`. Today's forwarded "mnemonic code" errors turned out to be logger digests
with no payload, which put the whole harvest premise in doubt.

That is now resolved. **Travis Fair (NUSD help desk) forwarded four genuine
`XRAS_post_action.json` attachments** — a 2×2 across the two handlers that matter most:

| | New | Extension |
|---|---|---|
| **failed** | NCAR4232, 4,197 B — unreconciled ARC PI | UFSU0023, 2,839 B — shrink rejected |
| **succeeded** | NCAR4253 → UCIR0072, 7,289 B | UCUB0166, 3,131 B |

They contradict the inferred wire contract in about twenty places, several of which would have
produced a broken schema or a wrong handler.

So this plan does three things: record what the payloads prove, correct the two plan docs, and
reorder the sprint so `POST /actions` ships capture-only first — making the audit log the
harvesting instrument rather than a byproduct.

---

## The mail path, corrected

| Doc claim | Reality |
|---|---|
| `hdt@ucar.edu` **and** `sweg-notify@ucar.edu` hold the payloads | `app/env/sam.complete.properties:29` is `xras.actionpost.recipients=hdt@ucar.edu` — **hdt only**. `sweg-notify` is `sam.errormail.to`, the logback `SMTPAppender` (`app/env/logback.xml:6-14`, subject `SAM logger: %logger`). I pulled one of today's: `multipart/mixed`, single 24.6 KB `text/plain` part of buffered log events plus an `ActionProcessingException` stack trace. No attachment, no `requestNumber`. |
| — | **`actionJson` is never logged at any level.** `ActionServiceController.handleAction` takes it as `@RequestBody String` and hands it to `EmailingActionPostService`, which only ever wraps it in an `EmailAttachment`. No log-level flip recovers a payload; `XrasActionLogger` at DEBUG yields only the rendered Velocity body (actionType, requestNumber, requestTitle, requestAbstract, dates). |
| — | `XRASPostBean` (the operator paste-to-replay tool) persists nothing. |
| — | **Success emails carry the attachment too** — `EmailingActionPostService.sendSuccessEmail` → `sendEmail(..., actionJson)`. So hdt holds ~108 *success* payloads as well as the 67 failures. The success corpus is the Extension/Supplement material. Ask for both. |

Today's mnemonic failures are the already-diagnosed Fischer Identity problem
(`~/codes/sam/XRAS_POSTING_ISSUE_DESCRIPTION.txt`, July 20 — placeholder usernames plus a halted
`sam-ldap-syncd`), not new contract information.

---

## Track 0 — what the four real payloads prove

All verified against the legacy POJOs in
`~/codes/sam/src/main/java/edu/ucar/cisl/sam/xras/rest/accounting/action/`. Every row here is a
correction or a resolved open question, not a restatement.

**All four payloads carry an identical top-level key set** — XRAS never omits a key, it sends the
key with `null`. So `allow_none=True` is the load-bearing tolerance and `load_default` is purely
defensive.

Observed value space across the four, for anyone writing extractor tests:

| | NCAR4232 | NCAR4253 | UFSU0023 | UCUB0166 |
|---|---|---|---|---|
| `actionType` | New | New | Extension | Extension |
| `requestNumber` | NCAR4232 | NCAR4253 | UFSU0023 | UCUB0166 |
| `allocationType` | Educational | Small | Large | Small |
| `opportunityType` | Continuous | Continuous | Terminating | Continuous |
| `resources[]` | 4 | 4 | **0** | **0** |
| `roles[]` | 3 | 2 | 2 | 2 |
| `fos[]` / `panels[]` | 1 / 1 | 2 / 1 | 4 / 2 | 2 / 1 |
| `grants[]` | **0** | 1 | 1 | 1 |
| `opportunityQA[]` | 1 | 1 | 0 | 0 |
| `awardDate` | null | null | 2021-10-26 | null |

### Resolved open questions

1. **Date format.** Zero-padded ISO-8601 `YYYY-MM-DD`, date-only, no time, in every date field
   (`actionBeginDate`, `actionEndDate`, `roles[].beginDate`, `grants[].beginDate/endDate`,
   `awardDate`). Legacy's lexicographic `String.compareTo` comparisons are therefore correct.
   **Open question closed.**
2. **`roleType` vocabulary.** Across 9 roles: `'PI'`, `'Allocation Manager'`, `'User'` —
   **space separated, not camel case.** These are **not** the `Pi` / `CoPi` /
   `AllocationManager` keys that GET endpoint #5's `{role}` segment maps to. Two distinct
   vocabularies; anything that assumes they match breaks. `'User'` is a plain-member role, which
   is why §3.5's "add every `roles[]` entry regardless of `roleType`" is right. Three of the four
   payloads carry exactly PI + Allocation Manager; **no co-PI appears yet**, so whether it is
   `'Co-PI'` or `'CoPi'` is the one vocabulary question the bulk batch still needs to settle.
3. **`isReconciled` is XRAS's reconciliation state, not SAM's.** The unreconciled ARC placeholder
   `gsaha-user-hv1bu` — the identity whose absence *caused* the failure — arrives with
   `isReconciled: true`, as do all 9 observed roles. It has never been observed false. Parse it,
   keep it inert, and do **not** wire it to behaviour: it would be actively misleading.
4. **`isAccountToBeCreated`** is a real `false` boolean in all 9 observed roles — never null,
   never a string, never true. The forgiving coercion stays defensive only.
4b. **`requestNumber` is the projcode for existing-project actions and an `NCAR####` token for
   New.** UFSU0023 / UCUB0166 are projcodes; NCAR4232 / NCAR4253 are XRAS request numbers, and
   NCAR4253 generated projcode UCIR0072. Confirmed by `formatSuccessSubject`, which picks the
   "Existing XRAS project updated" wording precisely when `requestNumber.equals(projcode)`. So
   the selector resolves the project *by treating `requestNumber` as a projcode*, and the audit
   table's `request_number` vs `projcode_result` columns diverge exactly on the New path — which
   is what makes both columns worth having. The doc's `request_number … == projcode` comment is
   only half true.

### Corrections to the schema spec

5. **Absent scalars arrive as JSON `null`, never `""`.** `requestShortTitle`, `requestGrantType`,
   `awardDate`, `resources[].comments`, `roles[].endDate`, `person.middleName`,
   `person.organization`, `grants[].percentageAward`, `grants[].subAwardNumber` are all `null`.
   The Java `= ""` initialisers only ever fire for a *missing key*, and XRAS sends the key.
   So `allow_none=True` is the **norm across the board**, not a per-field exception — this
   inverts the emphasis in the doc's tolerance #2.
6. **`person.organization` can be `null`, and it matters only on the PI.** It is null for the
   UFSU0023 **PI** (which failed) and for the UCUB0166 **Allocation Manager** (which *succeeded*).
   So the mnemonic/affiliation extractors read the **lead's** affiliation only — a sharp,
   testable distinction, and 2 of the 4 payloads exercise both sides of it. This is directly
   implicated in the two top failure causes (`Could not determine Mnemonic code for internal PI
   via organization`, 24%; `Could not produce affiliation data for PI`).
6b. **`person.organization` is free text, not an institution key.** Observed: `'UNIVERSITY OF
   CALIFORNIA AT IRVINE'`, `'Fluid Numerics LLC'`, `'NORTH CAROLINA STATE UNIVERSITY'`, and
   `'North Carolina State University - Incoming Graduate Student'` — inconsistent case, and the
   last one carries an appended role suffix. Matching this against SAM's institution/organization
   tables is inherently fragile, which is a plausible root of the 24% mnemonic failure rate
   (§*Mnemonic* already notes 171 active orgs match nothing). Worth surfacing to Ben as a
   data-quality finding, not just a port detail.
7. **Ints arrive in String-declared fields, confirmed twice.** `awardPeriod` is `12` (int) and
   `fosTypeId` is `500006` (int); both are `private String` in Java. Accept both.
   (`fosNum` is the opposite — an int-looking *string*, `'4'` / `'29'`.)
8. **`awardedAmount` is a float-formatted string**: `'500000.0'`, `'1.0'`, `'5000.0'`,
   `'870857.0'`. Parse to `Decimal`, not `int` — `int('500000.0')` raises.
9. **Three fields exist on the wire that no POJO declares**, so legacy silently discards them
   today: top-level **`requestGrantType`** (null in all four) and **`opportunityQA`** (array of
   `attributeSetId` / `attributeSetRelationType` / `attributeSetName` / `opportunityAttributeId` /
   `attributeName` / `attributeValue` / `answer` — the End User Agreement acknowledgement, present
   on both News and empty on both Extensions), and **`resources[].resourceQA`**. Concrete proof
   that `unknown = EXCLUDE` is mandatory.
9b. **`grants[].awardedAmount` can legitimately be `'0.0'`** (UCUB0166, a GRFP fellowship), and
   **`awardedUnits` is null in 2 of 3 grants** (only UFSU0023 says `'Dollars'`). Do not treat a
   falsy amount as missing. Grant numbers observed: `'EAR-2425607'`, `'OCE-2123632'`,
   `'GRFP-2040434'` — NSF award numbers matched against SAM's `contract` table, which per
   `docs/` is `utf8mb3_bin` and therefore **case-sensitive**; the contract lookup behind
   `Cannot find contract for grant number "<n>"` must use `ilike`, not `LIKE`.
10. `grants[].primaryFos` carries `fosTypeId` / `fosNum` / `fosName` / `fosAbbr` but **no
    `isPrimary`** — it is a different shape from `fos[]` entries. Two FoS schemas, or one with
    `isPrimary` optional.
11. Bodies are **compact** JSON (no whitespace), 2.8–4.2 KB. `TEXT` is ample.

### Corrections that change handler design

12. **`resources: []` on *both* Extensions** — the success as well as the failure, so it is not an
    artifact of the error path. Extension therefore cannot derive targets from the payload: its
    only input is `actionEndDate` against the project's existing allocations. Both News carry 4
    resources. This simplifies Extension considerably and confirms it as the right first handler.
13. **`grants: []` on the Educational New.** NCAR4232 (Classroom/Educational) has none; NCAR4253
    (Small), UFSU0023 (Large) and UCUB0166 (Small) each carry exactly one. The New handler must
    tolerate no grants → no `ProjectContract`, and the
    `Cannot find contract for grant number "<n>"` failure is specific to the grant-bearing path.
14. **The same username appears under multiple roles.** `gsaha-user-hv1bu` is both `PI` and
    `User` with distinct `requestPeopleRoleId`s. Adding every role to the accounts must dedupe.
15. **`panels[].isPrimary` is not index 0.** UFSU0023's primary is the second of two.
16. **`requestType` ≠ `actionType`.** UFSU0023 is `actionType: 'Extension'` on a request whose
    `requestType` is `'New'`. The selector keys on `actionType` only.
17. **AOI comes from `fos[isPrimary].fosNum`, tried as int then as string.**
    `AreaOfInterestExtractor.extract` reads `getPfosNumber()`, does
    `Integer.decode` → `findOne(int)` with a `NumberFormatException` fallback to
    `findOne(String)`. Also yields a fourth error string the doc omits:
    `"No FieldOfScience (fos) objects"` when `fos: []`.
18. **The doc's §1.3 conflates two different end-date validators.** Both strings are real, on
    different handler paths:
    - `ExtendProjectAllocationActionCommandsFactory:42` →
      `"Action end date is before existing allocation end date (%s)"` — the **Extension** path,
      and what UFSU0023 actually returned.
    - `UpdateProjectAllocationActionCommandsFactory:52` →
      `"Action end date before existing allocation end date for " + resourceName` — the
      **Update** path, which is the string §1.3 records.

    §3.4's "exact error strings" needs a pass against the source, since the 422 body is the
    headline deliverable and these strings *are* the contract for XRAS admins. Note also
    `ProjectActionCommandFactoryBase:58` emits `"Allocation Manager %s is not in database: "`
    with a **dangling trailing colon-space** — reproduce or fix deliberately, don't do it by
    accident.
19. **UFSU0023 is the regression test for the Extension shrink divergence.** Its `actionEndDate`
    (2027-09-30) is *before* the existing allocation end (2033-07-31), and legacy errors.
    `extend_project_allocations` silently skips shrinks — this payload is the failing case, in
    hand, today.

---

## Track 1 — correct the plan docs (do first, ~1 hour)

The false mail-path premise and the contract errors above are load-bearing for anyone else
picking this up.

- `docs/plans/XRAS_ACTION_INGESTION.md` §*Day one* item 1 — rewrite around the corrected mail
  path, and replace "pull them" with the Track 3 ask. Fold corrections 1–19 into §*The schema*
  and §*The handlers*.
- `docs/plans/XRAS_REIMPLEMENTATION.md` — fix the `hdt / sweg-notify` phrasing at `:421` (origin
  of the error); update §2.4 with corrections 5–11; add the `opportunityQA` / `requestGrantType` /
  `resourceQA` unknown-field list; split the §1.3 end-date row per correction 18; mark the three
  §3.5 / Phase 5.1 open questions **closed** per 1–4.
- Note `~/codes/sam/src/main/resources/json/xras/Action.json` (the `jsonschema2pojo` source) as a
  **stale** artifact — it targets the dead `presentation.rest.xras` package and lacks
  `person` / `grants` / `requestShortTitle` / `isReconciled`. Evidence, not contract; the real
  payloads supersede it.

---

## Track 2 — the capture slice (the new step 1)

Ships `POST /api/xras/v1/actions` that authenticates, parses, audits and returns 200, **dispatching
nothing**. Deployable alone; makes every later payload self-collecting.

The doc's §*The table* / §*The ORM model* / §*The schema* / §*The route* stand as written. What
changes: dispatch is stubbed, capture is a first-class mode, and the schema is written against
real bytes.

1. **`xras_action_log`** — DDL per §*The table* (`ManualTask`, `src/sam/operational.py:25`, is the
   `remote_actor`/`status`/`raw_payload`/`received_time` precedent). Into dev and CI via the
   tracked post-restore init script: `containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql`
   plus a `COPY` in that image's `Dockerfile` — `mysql` and `mysql-test` build from the same
   image, so one change serves both, and `IF NOT EXISTS` makes it self-retiring. File the DBA DDL
   ticket the same day: the prod writer has no DDL
   (`scripts/repair/RUNBOOK-missing-projects.md:36-38`) and Alembic does not manage `sam`.
2. **ORM model** in `src/sam/integration/xras.py` beside `XrasResourceRepositoryKeyResource`
   (match its `from ..base import *` and banner conventions), exported from `src/sam/__init__.py`'s
   integration block — which auto-registers the Flask-Admin view at
   `/database/default_views/xras_action_log`, the operator surface until Sprint B.
3. **`XrasActionSchema`** in `src/sam/schemas/forms/xras.py`, plain `marshmallow.Schema` with
   `unknown = EXCLUDE`. **Not** `HtmxFormSchema` — its `_strip_empty_strings` pre-load is
   `ImmutableMultiDict`-shaped and shallow, so it will not recurse six nested arrays and it
   strips legitimately-empty strings. Follow `src/sam/schemas/charges.py:91`
   (`BaseChargeSummaryInputSchema`) for the API-JSON-body family, and
   `src/system_status/schemas/status.py:168-172` for the nested-load idiom
   (`fields.Nested(..., many=True, load_default=[])`) — but **not** `load_instance = True`.

   Field sets, from the POJOs plus the real payloads:

   ```
   XrasAction : actionId actionType actionBeginDate actionEndDate requestId requestNumber
                requestType requestAbstract requestTitle requestShortTitle opportunityId
                opportunityType opportunityName allocationType awardDate awardPeriod
                resources[] roles[] fos[] panels[] grants[]
                # on the wire but undeclared, dropped by EXCLUDE:
                #   requestGrantType, opportunityQA[]
   Resource   : actionResourceId resourceRepositoryKey awardedAmount comments
                # undeclared: resourceQA[]
   Role       : requestPeopleRoleId roleType username beginDate endDate
                isAccountToBeCreated person
   Person     : firstName middleName lastName email phone organization academicStatus
                isReconciled
   Fos        : fosTypeId fosNum fosName fosAbbr isPrimary
   Panel      : type name abbr isPrimary
   Grant      : fundingAgency grantNumber programOfficerName programOfficerEmail piName title
                beginDate endDate awardedAmount awardedUnits percentageAward subAwardNumber
                primaryFos isPending      # primaryFos has no isPrimary
   ```

   Tolerances per Track 0: `allow_none=True` broadly; ints accepted in `fosTypeId` /
   `awardPeriod`; `awardedAmount` → `Decimal`; forgiving boolean on
   `roles[].isAccountToBeCreated` only.
4. **The route**, `src/webapp/api/xras/actions.py`, imported at the bottom of
   `src/webapp/api/xras/__init__.py` beside `people` / `requests`:

   ```python
   @bp.route('/actions', methods=['POST'])
   @bp.route('/actions/<int:action_id>/<int:request_id>/<action_type>', methods=['POST'])
   @csrf.exempt
   @xras_api_required()
   def post_action(action_id=None, request_id=None, action_type=None):
       ...
   ```

   `@csrf.exempt` is required (CSRFProtect covers all POSTs; `api/v1/status.py:227-230` is the
   precedent). Use `xras_api_required`, the Phase 1 alias already in `__init__.py` — *not*
   `api_key_required`, whose challenge emits `WWW-Authenticate` and breaks the byte-exact 401.
   Map both URL forms per §2.1. Extend the blueprint-local error handlers in
   `api/xras/__init__.py`; the shared `register_error_handlers` has no 422 or 500.
5. **Capture mode.** `XRAS_ACTIONS_CAPTURE_ONLY` in `src/webapp/config.py`, following the
   `FLASK_ADMIN_ENABLED` idiom at `:50` / `:276`
   (`os.getenv(..., '1').lower() in ('1','true','yes')`) — **default on** in slice 1, flipped off
   per handler as each lands. When on: write the row `status='received'`, skip dispatch, return
   200 `{"message":"OK","result":null}`.
6. **The audit row must outlive a handler rollback.** `management_transaction`
   (`src/sam/manage/transaction.py:31-36`) rolls back the whole session on exception, so the row
   cannot live inside it. Commit it before dispatch on its own session/connection, and test the
   rollback case explicitly — no happy-path test reaches it.

Status codes stay §2.5: 400 on malformed JSON (still writing a row, `action_type=NULL`), 422 with
the ordered error list, 200 on success, 200 + `manual` for an unhandled type.

---

## Track 3 — grow the corpus

### 3a. Fixtures from the four payloads in hand (do now)

Raw copies are in `~/.workspace-mcp/attachments/`:

| Fixture | Raw file |
|---|---|
| `new_ncar4232_failed.json` | `XRAS_post_action_7744e52f.json` |
| `new_ncar4253_ok.json` | `XRAS_post_action_c8ed1e3c.json` |
| `extension_ufsu0023_failed.json` | `XRAS_post_action_ad5c58af.json` |
| `extension_ucub0166_ok.json` | `XRAS_post_action_c8ce5912.json` |

They carry real names, emails, phones (`roles[].person`) and grant officer contacts
(`grants[].programOfficerName/Email`, `piName`). **Keep the raw files out of the repo** — stage
them outside the working tree or in a gitignored path.

- `scripts/xras/scrub_payload.py` — deterministic pseudonyms so a repeated identity stays
  consistent across the corpus. Scrub `person.*` name/email/phone and the three grant contact
  fields. Preserve everything structural: `organization`, `academicStatus`, `fundingAgency`,
  `grantNumber`, dates, amounts, ids, and the null-vs-value pattern, since that pattern *is* the
  contract evidence.
- **Retarget, don't remap.** `containers/sam-sql-dev/anonymization_mappings.json` is keyed
  `user_id → obfuscated`, so it cannot translate a real username forward. Take *shape* from the
  payload and *identity* from the test DB: a `--retarget` mode swapping usernames/projcodes onto
  known snapshot entities (`benkirk` survives the anonymizer — use it) or onto factory-built rows.
- Commit the four scrubbed fixtures to `tests/fixtures/xras/actions/` under the names above, and
  add the `raw_payload` PII rule to `containers/sam-sql-dev/anonymize_sam_db.py` **before** anyone
  next runs `make bootstrap`.

### 3b. Ask Travis for the bulk forward (one email)

Four payloads settle the *shape*; they cannot settle the *distribution*, and three gaps remain
that only volume closes: the co-PI `roleType` spelling (finding 2), a **Supplement** payload
(15% of traffic, zero samples), and an **Update** payload (the `AUTO_DEFAULT_ALLOCATION_TRANSACTION`
undo kludge). Reply to Travis Fair (cc Mea Trahan) asking for a bulk forward from `hdt@ucar.edu`,
30–60 days, successes as well as failures:

```
subject:("Failed to add or update XRAS project" OR "New XRAS project added"
         OR "Existing XRAS project updated") has:attachment
```

That is ~175 payloads. **Supplement and Update are now the handlers gated on this batch** — New
and Extension each have a success and a failure sample in hand, which is enough to build them.

### 3c. Offline synthesizer (bridges the gap, no human in the loop)

- `tests/factories/xras_payloads.py` — pure dict builders on the Layer-2 factory conventions in
  `tests/factories/` (`session` first positional). One baseline builder taking ORM objects, plus
  six thin per-`actionType` variants. **Now validated against real bytes**, so it generates the
  right shape: nulls not empty strings, `'Allocation Manager'` not `'AllocationManager'`,
  float-formatted `awardedAmount`, empty `resources[]` for Extension, empty `grants[]` for
  Educational.
- Ground every field in real data: `requestNumber` ← `project.projcode`;
  `resources[].resourceRepositoryKey` ← real `XrasResourceRepositoryKeyResource` rows;
  `awardedAmount` ← `allocation.amount`; `roles[]` ← project lead/admin + `account_user`;
  `person` ← `User` fields + `primary_email`; `fos[]` ← area-of-interest; `grants[]` ←
  `ProjectContract` → `contract`; dates ← allocation start/end.
- `scripts/xras/synthesize_actions.py` — thin CLI over the same builders that walks the snapshot
  DB and emits a corpus for the §1.2 request numbers (`scripts/ingest_mock_status.py` is the
  nearest precedent).
- **State the limit in the module docstring:** a synthesized corpus encodes our reading of the
  contract, so it cannot falsify it. It validates handlers and gives the nested schemas volume;
  3a and 3b are the evidence about shape.

---

## Then the handlers (order unchanged, gating changed)

Extension (60%, 98.5%) → Supplement (15%) → Adjust → Update → New (21%, 30%) → Transfer to
manual, per the doc. Three changes:

- Each handler flips its slice out of capture mode as it lands, so `POST /actions` stays
  continuously deployable.
- **Extension has both a passing and a failing real payload on day one.** UFSU0023 exercises the
  shrink case `extend_project_allocations` silently skips (correction 19) — the strict/account-
  scoped variant the doc asks for — and UCUB0166 is the success it must not break. With
  `resources: []` on both, the handler's only input is `actionEndDate` (correction 12).
- **New is buildable now, not gated.** NCAR4253 → UCIR0072 is a complete success path (projcode
  generation, one NSF grant, two FoS, mnemonic resolved from an institution) and NCAR4232 is the
  dominant 55% failure mode (unreconciled ARC PI, duplicate username across roles, no grants).
  That covers both ends. **Supplement and Update are the ones now gated on 3b** — zero samples
  each. If the batch slips, ship New/Extension/Adjust and leave those two in capture mode; legacy
  keeps handling them until cutover step 4.

Settle the actor question before the first handler: `log_allocation_transaction`
(`src/sam/manage/allocations.py:69`) declares `user_id: int` but the column is nullable
(`src/sam/accounting/allocations.py:232`) and nothing dereferences it — widen to `Optional[int]`,
document that `None` means an integration actor. Errors **accumulate** into one ordered list
rather than short-circuiting; that list is the 422 body. Never leave `project.unix_gid` NULL.
XRAS-created projects stay `active = 0` on purpose. `management_transaction` does **no** implicit
audit logging — it is six lines of commit/rollback; audit rows exist because manage functions call
`log_allocation_transaction` explicitly.

---

## Files

| Path | Change |
|---|---|
| `docs/plans/XRAS_ACTION_INGESTION.md`, `docs/plans/XRAS_REIMPLEMENTATION.md` | Track 1 corrections |
| `containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql`, `.../Dockerfile` | table into dev + CI |
| `src/sam/integration/xras.py`, `src/sam/__init__.py` | `XrasActionLog` model + export |
| `src/sam/schemas/forms/xras.py`, `forms/__init__.py` | the seven schemas |
| `src/webapp/api/xras/actions.py`, `api/xras/__init__.py` | route + 422/500 handlers |
| `src/webapp/config.py` | `XRAS_ACTIONS_CAPTURE_ONLY` |
| `src/sam/manage/allocations.py` | `user_id: Optional[int]` |
| `scripts/xras/scrub_payload.py`, `scripts/xras/synthesize_actions.py`, `tests/factories/xras_payloads.py` | Track 3 |
| `tests/fixtures/xras/actions/*.json` | four scrubbed real payloads (3a) |
| `containers/sam-sql-dev/anonymize_sam_db.py` | `raw_payload` PII rule |
| `tests/api/test_xras_access.py`, `tests/unit/test_xras_actions.py` | see below |

## Verification

- `pytest tests/integration/test_schema_validation.py` — the table validates honestly, indexes and
  column types included. (`test_all_tables_exist_in_database:213` and
  `test_all_models_have_tables:422` both fail if the model lands without the table, and there is
  no skip mechanism — hence the init script.)
- `pytest tests/unit/test_xras_actions.py` — **`XrasActionSchema` round-trips all four scrubbed
  payloads** (parametrize over the fixture directory) and asserts the Track 0 specifics: dates
  parse as ISO date-only; `roleType` accepts `'Allocation Manager'`; int `fosTypeId` /
  `awardPeriod`; `awardedAmount` → `Decimal('500000.0')` and `Decimal('0.0')`;
  `requestGrantType` / `opportunityQA` / `resourceQA` dropped without error; null
  `person.organization` on a PI *and* on an AM; duplicate username across two roles; empty
  `resources[]`; empty `grants[]`; `panels[]` primary not at index 0. Plus each handler against
  factories and the synthesized corpus.
- Handler-level oracles from the real corpus: UCUB0166 and NCAR4253 must **succeed** (the latter
  minting a projcode, so `request_number != projcode_result`), UFSU0023 must 422 with
  `Action end date is before existing allocation end date (2033-07-31)`, and NCAR4232 must 422
  with its three ordered messages verbatim.
- `pytest tests/api/test_xras_access.py` — extend Phase 1 with the POST surface: `ROLE_XRAS`
  enforced, 400 on malformed JSON, 422 carrying the ordered list, 200 on success, 200 + `manual`
  on an unhandled type, both URL forms, and **an audit row on every one of those paths, including
  the handler-rollback case**.
- Route-map parity: `ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`, commit the diff.
- Full suite green before the PR. The doc's baseline is 4,404 passed / 36 skipped / 1 xfailed —
  re-measure rather than trusting it.
- Local end-to-end — note `make docker-down` has no `-v` (`Makefile:188`), so init scripts won't
  re-run:
  ```bash
  docker compose --profile test down -v && make docker-build && make docker-up
  docker compose up webdev --watch
  curl -u samuel:"$SAM_XRAS_PASS" -H 'Content-Type: application/json' \
       --data @tests/fixtures/xras/actions/new_ncar4253_ok.json \
       localhost:5050/api/xras/v1/actions
  ```
  then confirm the `xras_action_log` row and that `/database/default_views/xras_action_log`
  renders it.
- Once handlers land: replay the corpus and diff resulting DB state against what legacy did for
  the same action — the §1.2 action-mix correlation is the oracle, and UFSU0023 / NCAR4232 have
  known-correct legacy outcomes (both failures, with exact error strings) to diff the 422 against.

## Not in this sprint

Sprint B (4th Allocations tab, `sam-admin xras`, replay UI, `Permission.MANAGE_XRAS`), SMTP
(legacy keeps mailing until cutover step 4, so no gap opens), the GET cutover steps 1–3 (PR #424
is open against `staging` and mergeable; it deploys independently), and the `POST /actions` cutover
itself — which additionally needs the 400/422 error-contract change confirmed with
`allocations@access-ci.org`.

Parked per today's decision: asking XRAS for their own post records, and a capture-only dual-post
arrangement. Nathan Tolbert is out until Mon Aug 9; the thread opened today stays useful for the
cutover conversation. Staging needs the DDL run by hand once —
`infrastructure/scripts/init-rds.sh:14` restores the raw `.xz` with no initdb hook.

**Two things worth surfacing separately, as decisions rather than ports:**

- `opportunityQA` carries the End User Agreement acknowledgement (both New payloads answer "Click
  here to acknowledge and agree to the End User Agreement terms."), present on New actions and
  empty on Extensions, and legacy discards it. If SAM wants a record of EUA consent, the audit
  table captures it for free.
- `person.organization` is free text with inconsistent case and appended role suffixes
  (finding 6b). Matching it against SAM's institution/organization tables is the likely root of
  the 24% mnemonic failure class. A fuzzier or curated mapping would move the New handler's
  success rate more than any code in this sprint.

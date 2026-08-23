# XRAS triage playbook

**What to do when a real action arrives and does not land.** The day-of sequence is
[`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md); the design and every measurement
are in [`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md). This is the week after.

> ## ⚠️ The one fact that shapes every remediation below
>
> **`--recheck` cannot apply anything.** `webapp/api/xras/recheck.py` calls
> `dispatch_action(..., validate_only=True)`, which returns *before*
> `management_transaction` ever opens — the no-write property is structural, not
> configured, and no setting changes it.
>
> So a green re-check means **"this would work if XRAS sent it again"**, never "this has
> been applied". Steven Peckins confirmed on 2026-08-11 that POSTs are triggered by a
> human pushing a button in `xras_admin` and are never retried automatically.
>
> **Every fix therefore ends with a request to ACCESS to re-post the action.** That is
> the loop. Budget for it: a fix is not done when the data is right, it is done when the
> action comes back and lands.

---

## 1 · The daily loop

In the order you will reach for them:

| Surface | What it answers |
|---|---|
| XRAS → **Pending Activations & Notifications** | Everything received, filterable, raw payload behind `MANAGE_XRAS` |
| XRAS → **Accounts Needed** | *Feed A.* The usernames on actions already received that have no usable SAM account. This is § 3.3's fix, as a worklist |
| XRAS → **Pending Requests** | *Feed B.* Approved XRAS requests **not yet pushed** — the same problem *before* the action arrives. Renders the `xras_sweep` snapshot, not a live call |
| `sam-admin xras --summary --last 24h` | Status counts at a glance |
| `sam-admin xras --status failed --last 24h` | The 400s and 422s, with their error lists |
| `sam-admin xras --status manual --last 24h` | What parked, and **why** (`outcome_reason`) |
| `sam-admin xras --show <id> --payload` | One row in full. ⚠️ `raw_payload` is a verbatim POST body and contains PII |
| `sam-admin xras --recheck <id>` | "Would this succeed if posted now?" Applies nothing |
| `sam-admin xras --accounts [--enrich] --last 7d` | Feed A on the CLI. `--enrich` needs `--accounts` and a configured API. **An empty worklist exits 0** — "nobody is blocked" is a successful report |
| `sam-admin xras --person <username>` | One identity from XRAS. Three outcomes on purpose: found `0`, no such username `1`, could-not-ask `2` |
| `sam-admin xras --validate-mapping` | The resource-key map, **both sides** — § 3.1 |
| `sam-admin xras --validate-opportunities` | The `opportunityId` map, both sides — § 3.9, the silent one |
| `sam-admin tasks --history --task xras_sweep --format json` | Whether the sweep is actually feeding the Pending Requests tab — § 3.9 |

Add `--format json` for anything you want to pipe.

⚠️ **`--status unmapped` is not selectable from the CLI.** The `click.Choice` in
`src/cli/cmds/admin.py` predates the status and lists only five of the six in
`XRAS_ACTION_STATUSES`. Those rows *are* counted by `--summary` and *are* filterable on
the dashboard — use one of those. Left unfixed on purpose (see § 6).

### What "normal" looks like — do not chase these

- **~30% of `/people/{username}` lookups 404.** That is the baseline, measured over 30
  days of legacy access logs: historical usernames XRAS still asks about. It is not an
  error rate.
- **The roster response is ~3.84 MB ±0.2%** and takes ~1.1 s. There is no filter and no
  paging; that is the legacy contract.
- **`New` actions succeed ~30% of the time.** Historically, on legacy, for data reasons
  this port did not introduce and cannot fix from code (§ 3, rows 2 and 3).
- **`Date Adjustment` and `Transfer` park.** By design, parity-correct, and now visible.
  A parked row is an outcome, not an incident.
- **A populated *Pending Requests* tab beside an empty *Accounts Needed* tab is correct
  before the repoint.** The two feeds fail empty for opposite reasons, which is why both
  exist. Feed A reads `xras_action_log` — 0 rows until XRAS repoints, a *designed* empty
  state. Feed B reaches `api.xras.org` directly and worked in production on 2026-08-20:
  22 requests, 18 accounts needed. Do not spend day one debugging the empty half.

---

## 2 · Classify the row before you fix anything

`status` × `http_status` × `service` × `outcome_reason` is the whole decision tree. The
three columns C.1b added are what make a row triageable — a NULL `service` means nothing
matched at all, a populated one means a service was selected and then something stopped
it.

| What you see | What it means | Whose problem |
|---|---|---|
| `failed` / `400` | Body was not JSON, or not an object. `action_type` is NULL because we genuinely do not know it | XRAS-side. Reply with the message |
| `failed` / `422` | Parsed fine, validation rejected it. `error_messages` is the ordered list, one per line | § 3 — usually **ours**, and usually a data fix |
| `failed` / `500` | A handler raised. `outcome_reason` carries `handler raised: <ExceptionClass>` | **A bug.** Read the app log for the traceback |
| `manual`, `service` NULL | Nothing matched the actionType — `Date Adjustment`, `Advance`, or something new | Apply by hand, exactly as today |
| `manual`, `service='transfer'` | Deliberately not serviced; zero production traffic, no sampled payload | Apply by hand |
| `manual`, `outcome_reason` names `XRAS_ACTIONS_ENABLED` | **We** parked it, with the triage lever | Ours — did you mean to? |
| `manual`, `outcome_reason` says no handler registered | The handler registry is empty | **A bug.** See § 5 |
| `unmapped` / `404` | XRAS called a path we do not implement. Nothing was applied | One row is worth reading; a *run* of them is a conversation with ACCESS |
| `received`, `processed_time` NULL, past the cutover | A post that arrived while dispatch was off | Ask XRAS to re-post — this cannot be replayed |
| `rechecked` | You ran `--recheck` and it would now succeed | Ask XRAS to re-post |

**`action_id` answers "have I seen this before?"** Three rows sharing one `action_id` are
one action posted three times, not three awards.

---

## 3 · The 422 catalog, ranked by expected frequency

Every string below is built by a named function in `src/sam/xras/errors.py`, which cites
the Java emitter it reproduces. **The typos are deliberate** — a doubled space, a trailing
`: ` with nothing after it — because they are the wire contract legacy established. Do not
tidy them.

### 1. `No resource found in SAM corresponding to key {key}`

The `resourceRepositoryKey` on the award has no row in
`xras_resource_repository_key_resource`.

⚠️ **Ranked first when this was written; demoted since.** The measurement then was "13
mapping rows, 11 active SAM resources have none". Both halves are still true, but they are
different directions and only one of them can cause this 422. #458 measured the direction
that matters, live: **all 13 `resourceRepositoryKey`s XRAS actually offers resolve to a
SAM resource** — zero unmapped, zero dangling. So this cannot fire against today's
catalog. It needs XRAS to start offering a resource we have never seen. The 11 unmapped
SAM resources are unmapped *by design* and are not keys XRAS can cite.

**Fix:** add the mapping row. `sam-admin xras --validate-mapping` now reports **four**
groups, and the fourth is the live one:

| Group | Meaning |
|---|---|
| `unmapped_active` | Active SAM resources with no mapping. Expected — 11 of them, stably. A diagnostic, not a gap |
| `mapped_decommissioned` | Mappings pointing at retired kit. Untidy, not broken |
| `dangling_keys` | A mapping row whose resource does not exist. **Broken** |
| `xras_only_keys` | A key XRAS offers that SAM cannot resolve. **This is the one that breaks an award** |

⚠️ **It exits non-zero on TWO of those** — `dangling_keys` *or* `xras_only_keys`.

⚠️ **A one-sided report is not a clean report.** The XRAS half is auto-detected: with no
`XRAS_OUTGOING_ENABLED=1` + `XRAS_API_KEY` it silently reports the local half, and with an
unreachable API it warns in yellow and does the same. The footer says which you got —
"Local half only …" versus "Every key XRAS offers resolves to a SAM resource." Read it.

> ⚠️ **Adding a mapping row moves GET response bytes.** `resourceRepositoryKey` is
> *omitted* from the `requests/*` payloads when a resource has no row, so closing a gap
> invalidates a previously clean gate-3 parity run. **Re-run it:**
> `python utils/parity/check_legacy_apis.py --api xras --new-base-url https://sam.hpc.ucar.edu`

There is a second wording for the same failure on the roster path — `No resource found in
SAM corresponding to name {resource_name}`. Both can appear for one action.

### 2. `Could not determine Mnemonic code for internal PI via organization`

**24% of legacy's XRAS failures.** The lead's organization has no mnemonic soft link.
SAM matches `mnemonic_code.description` against `organization.name` — or `"Name, City"`
then `"Name"` for institutions — by **exact, casefolded equality**
(`MnemonicCode.build_lookup` / `resolve_for_*` in `sam/core/organizations.py`, reused by
`resolve_mnemonic_code`). **153 of 171 active organizations (89%)** have no such row; 80%
of institutions are in the same state, which gives the external twin, `Could not determine
Mnemonic code for external PI via institution`. (Legacy's match was `code LIKE '%name%'`,
and `errors.py` quotes that census — 150/171; same failure class, different remedy.)

**Fix:** a data fix — a `mnemonic_code` row whose `description` equals the organization
or institution name. Two constraints: `code` and `description` are both unique, so every
new link needs its own unused 3-letter code; and an internal PI's organization comes from
`user_organization`, which is frozen (4,563 active users have no current row) — those PIs
have nothing to link. The admin Institutions card already offers the create modal on a
miss (the warning badge, pre-filled); the Organizations card does not yet. This would move
`New`'s success rate more than any code change available to us.

Two neighbors from the same resolution path:
`Could not produce affiliation data for PI {username}`, and
`Unable to determine allocation type from action data` / `No AllocationType for
SelectionParms{panel='…', type='…'}` (the odd rendering is Java's `toString()`,
reproduced because the operator sees it).

⚠️ **Both of those now mean "the `opportunityId` map missed *and* the ladder declined".**
Since #459 the map is consulted first, and a map **hit** cannot produce the second message
at all — the pair is read back off a real `allocation_type` row, so the join cannot miss.
If you see `No AllocationType for SelectionParms{…}`, the ladder produced it. The map's
own failure is silent and is in § 3.9.

### 3. `PI {username} is not in database` · `Username {username} is missing`

**55% of production `New` failures**, and the largest single cause of handoff failure.
Unreconciled ARC placeholder identities — the username on the award has never been
reconciled to a SAM user.

**Fix: work the *Accounts Needed* tab, or `sam-admin xras --accounts`.** Not "identity
reconciliation" — that is a category, not a loop. #458 made it a worklist, and it is the
highest-value surface on this page:

- **Two classifications, each with its remedy:** `absent` → **Create**, `inactive` →
  **Reactivate**. Account creation is manual; the worklist tells you *who*, *why*, and —
  behind `MANAGE_XRAS` — with what person detail to create them from.
- ⚠️ **`placeholder` is a flag on the row, not a third classification.** A placeholder
  identity is still either absent or inactive. Do not filter on it expecting a bucket.
- **Rows close themselves.** Classification is a check against the *current* `users`
  table, never against action status, so it is regime-proof across the capture-only flip
  and a row leaves the list on the next render once the account exists. There is nothing
  to mark done.
- ⚠️ **`isReconciled` is not a closure signal**, despite reading like one. It means XRAS
  linked the username to a real identity — it says nothing about SAM. The UI calls it
  identified / unidentified, and **unidentified is the harder row**: no detail sheet to
  create the account from.
- ⚠️ **`isAccountToBeCreated` is never the predicate.** XRAS sets it when a role is
  created and never clears it; every username measured carrying the flag was an existing
  active SAM account. It ships as a hint column with a regression test pinning that a
  flagged active user does not appear.
- **Each row carries a `validate_only` pre-flight** (`would_succeed` / `reject_messages`),
  so the card also surfaces **mnemonic and resource-key** rejections on the same rows —
  it is not only about accounts. That overlaps § 3.2 deliberately.

Feed B — the *Pending Requests* tab — is the same question asked **before** the action
arrives, which is the only pre-emptive surface in this document.

Note a blank username renders as `Username  is missing` with a double space; that is the
payload, not a formatting bug.

Same family, and the wording tells you which check tripped:

| Message | Note |
|---|---|
| `Missing pi role` | lowercase "pi", as in the Java |
| `PI {u} is not an active user: ` | ⚠️ trailing colon-space, nothing after it |
| `Allocation Manager {u} is not in database: ` | ⚠️ trailing colon-space |
| `Allocation Manager {u} is not active ` | ⚠️ trailing bare space, no colon |
| `Username {u} is inactive` | |
| `Multiple {role_type} roles are in range for this action: {a, b}` | **Not** a legacy string — legacy picks the first by array order and says nothing |

A **missing Allocation Manager is not an error**; a missing PI is.

### 4. `Cannot find contract for grant number "{grant}" ("{core}")`

⚠️ **The `New` handler LINKS a contract, it does not create one.** A grant number SAM has
never seen is a 422 naming the *contract*, not the grant.

**Fix:** create or link the contract in SAM first, then ask XRAS to re-post.

### 5. `Ambiguous contract for grant number "{grant}" ("{core}"): matches {a, b}`

Also not a legacy string — legacy raises `NonUniqueResultException` and returns an opaque
500. Three colliding pairs are known live in production: `1049089`/`PLR-1049089`,
`OPP-1744587`/`PLR-1744587`, `2146709`/`AGS-2146709`.

**Fix:** disambiguate the contract rows. The message names the candidates precisely so
that the fix is possible without a database session.

### 6. Date and window rejections

| Message | Raised by | Meaning |
|---|---|---|
| `Action end date is before existing allocation end date ({yyyy-MM-dd})` | **Extension** | The award moves an end date backwards |
| `Action end date before existing allocation end date for {resource_name}` | **Update** | Same idea, different path |
| `All contract and allocation end dates are null or past for project [{projcode}]` | Supplement, Adjustment | Supplementing an expired project — extend the contract first |
| `End date of allocation ({end}) must be after commission date of resource({name}).` | New, Update | Award predates the resource. ⚠️ no space before `(` — legacy's `resource(%s)` |
| `Missing {begin\|end} date for allocation(s)` · `Could not convert {begin\|end} date for allocation(s)` | shared | |

⚠️ **The first two are near-twins and must not be unified.** Update's interpolates a
*resource name*; Extension's interpolates a *date*. Which one you are looking at is how
you tell which handler rejected the action.

If the intent really was to move a date backwards, the action type for that is
`Date Adjustment`, which parks — see § 4.

### 7. Amounts

| Message | Note |
|---|---|
| `Awarded amount missing` | |
| `Could not convert awarded amount "{amount}"  to float` | ⚠️ **two spaces** before `to float`, as in the Java |
| `Adjustment of {amount} for {resource} would take the allocation below zero (currently {current})` | **Not** a legacy string — legacy has never run an Adjustment at all |

⚠️ **`awardedAmount` on a Supplement is the INCREMENT, not the new total.** The single
most consequential porting semantic in the whole integration. A Supplement with
`amount <= 0` is dropped silently with a WARN, matching legacy.

⚠️ **Review `adjust` rejections hardest.** Legacy's `AdjustProjectActionService` tests
`equals("Adjust")` against a wire that sends `"Adjustment"`, so it is dead code and has
**never once fired**. Every Adjustment SAM services is the first of its kind, with no
production outcome to diff against. (SAM accepts both spellings; `XRAS_ACTION_TYPE_ALIASES`
folds `Adjust` → `Adjustment`, read-side only. The audit column stores the wire value
verbatim.)

### 8. Miscellaneous

`Missing title` · `No FieldOfScience (fos) objects` · `AreaOfInterest (FOS) id is not in
database: {fos}` · and an oversized body, which answers **422 not 413** on purpose: a
status their panel does not expect would be an unreadable rejection.

> The 422 response always carries the **complete** ordered list. The `error_messages`
> column is cut on message boundaries with a counted tail when a payload overflows the
> 65,535-byte column, and says so in the stored value.

### 9. ⚠️ The two failures that are **not** in this catalog, and not in § 2 either

§ 2's decision tree is `status` × `http_status` × `service` × `outcome_reason`. Neither of
the following appears in any of those four columns. Both arrived with #458/#459, and both
present as *success*.

**9a · A wrong `opportunityId` map row is silent at ingest.**
`select_allocation_type_mapped` (`src/sam/xras/extractors.py`) consults
`xras_opportunity_allocation_type` before the free-text ladder and overrides it, with **no
log line and no ledger entry**. All three miss paths fall through silently too: no
`opportunityId` on the wire, no row for it, or a row whose `allocation_type.panel_id` is
NULL.

So the symptom of a bad row is not a failure — it is a **`processed` action carrying the
wrong projcode series**, because `handlers/new.py` draws the series from
`allocation_type.panel.facility_id`. Projcodes are not undoable. This is the one XRAS
mapping gap that does not shout; every other one 422s.

The check is the **map table**, not the action log:

```bash
sam-admin xras --validate-opportunities        # both sides, if the API is configured
```

It reports mapped / unmapped / dangling, and for the unmapped ones whether the two
independent derivations `xras_sweep` uses **agree**. Exit codes follow
`--validate-mapping`'s discipline: **non-zero only on `dangling_ids`**. An unmapped
opportunity is *not* a failure — with an empty table every opportunity is unmapped and
ingestion is perfectly healthy, because the ladder resolves it exactly as it did before
the table existed.

⚠️ **A withheld row in the `review` bucket is the tool working, not a fault.** Two pairs
sit there permanently by design — XRAS files the unsponsored family under `Educational`,
and gives `NCAR - ASD Opportunity` NSC's own type *and* panel, both of which change the
**facility**. They are `source='manual'` rows a human decided, and the agree-rule will
keep declining to derive them. The bucket matters when something *new* shows up in it —
most likely the first Wyoming opportunity, which is exactly the case the rule exists to
put in front of a person.

**9b · `published: true` is not `publish_backend: 'redis'` by luck.**
The *Pending Requests* tab renders what `xras_sweep` published, not a live call. The task
can succeed while publishing into a per-pod cache that dies with the pod — this happened
in production on 2026-08-20, and the ledger said `published: true` while the tab stayed
empty. `published` is now true **only** for `redis`.

**Empty Pending Requests tab + a `succeeded` ledger row → read `publish_backend` first:**

```bash
sam-admin tasks --history --task xras_sweep --format json | jq '.runs[0].detail'
```

The rest of that `detail`, in the order it is worth reading:

| Key | Says |
|---|---|
| `publish_backend` / `published` | `redis` or the dashboard sees nothing — 9b |
| `skipped` | `XRAS_OUTGOING_ENABLED` is off or there is no key. Success, no work |
| `budget_exhausted` | The **page** cap bit: the enumeration was truncated, so coverage looked complete and was not |
| `map_budget_exhausted` | The **write** cap bit. ⚠️ **Absent on most runs — missing means false** |
| `opportunities_needing_review` | Withheld mappings — 9a. Two are permanent |
| `opportunities_unknown_pair` | XRAS shipped an allocation product the constant does not name. A code review, never a guess |
| `unavailable_errors` | XRAS was unreachable at one of four steps; the run still reports what it got |

---

## 4 · Action types that park, and are supposed to

| Type | Why |
|---|---|
| `Date Adjustment` | No serviceable in legacy either, so parking is parity-correct. Discovered 2026-08-11; 4 of the 41 corpus payloads. The payloads are Extension-shaped but carry an `actionBeginDate` that Extension ignores, and the type most likely exists to move dates in directions Extension rejects. **Whether to service it is a question for ACCESS** |
| `Transfer` | Registered handler that parks with a reason. Zero production traffic, no sampled payload |
| `Advance` | No `select_service` arm. Zero samples |

Since the cutover these answer a **200 with an informative body** rather than a bare
`OK`, so the admin who posted knows a human has it. The status stays 200 because ACCESS
treats anything else as an error.

**Never seen in production, so expect surprises:** `Renewal` (zero samples — and the three
`requestType: 'Renewal'` payloads in the 2026-08-11 forward are *not* the same thing and do
not reach the Renewal arm), `Advance`, and any **co-PI** `roleType`. Membership ignores
`roleType` entirely, so the co-PI spelling cannot break a roster; but role *assignment*
knows only `PI` and `Allocation Manager`, so a co-PI is invisible to it.

---

## 5 · Levers, and what they cost

**Park one action type, no revert** — `helm/values.yaml`:

```yaml
XRAS_ACTIONS_ENABLED: "Extension,Supplement"
```

Narrow it to whatever should keep running. Excluded types take the audited `manual`
path — visible, recorded, applied by a human — rather than being dropped.

- ⚠️ **An unknown token is logged and dropped**, which fails safe in the direction that
  bites: a typo like `Extention` leaves Extension **disabled**. It deliberately does not
  refuse to start — this is the lever reached for at 3am and it must not be able to take
  the app down.
- ⚠️ It keys on **action type**, so disabling `New` disables both the `add` and `update`
  services.

**Stop dispatching entirely** — `XRAS_ACTIONS_CAPTURE_ONLY: "1"`. Everything is still
captured; nothing is applied. ⚠️ But posts arriving while it is on are stranded as
`received` and can only be recovered by asking XRAS to re-post, so this buys safety at
the cost of a round-trip with ACCESS. Prefer the per-type lever.

**Full rollback** is a repoint back to `sam.ucar.edu`, i.e. another round-trip with
ACCESS. Not ours to do alone. Legacy stays running and untouched throughout.

⚠️ **If *every* action suddenly parks with "no handler is registered",** the handler
registry is empty — `sam.xras.handlers` registers by import side effect, and the import in
`webapp/api/xras/actions.py` is what triggers it. It fails quietly, as plausible-looking
`manual` rows rather than as an error.

**What one action wrote:** there is no FK from `xras_action_log` to
`allocation_transaction` — an Extension averages 3.3 rows, so a column was the wrong
shape. Use the correlation query in
[`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md) § *What one action wrote*.

---

## 6 · If a failure class recurs — the sketch, deliberately unbuilt

Nothing below exists, on purpose. Writing remediation tooling for failures that have not
happened is how you end up maintaining the wrong tool. What follows is the *shape* of each
fix and the **trigger** that would justify building it, so that the decision is quick when
the evidence arrives and nobody re-derives it under pressure.

Two rows were overtaken by #458/#459 and are marked so; the rest stand unchanged.

| Trigger | Shape of the fix |
|---|---|
| Resource-key 422s recur across **different** keys | A mapping *writer* on `sam-admin xras` (today it is a hand INSERT). Must print the parity warning — closing a mapping moves GET bytes. ⚠️ **Narrowed:** the *detection* half shipped with #458's two-sided `--validate-mapping`, which exits non-zero when XRAS offers a key SAM cannot resolve. You now learn about this before an award does |
| Mnemonic failures dominate the `New` failure bucket | A bulk organization-mnemonic linker, plus a report of which orgs would unblock the most awards. This is the highest-leverage data fix available |
| Operators repeatedly fix a row, re-check it green, and wait on ACCESS | **A re-apply path.** Explicitly deferred in `recheck.py`; it needs an idempotency key enforced on `action_id` *first*, because 4 of the 6 handlers double-apply — Supplement and Adjustment are additive, and a re-applied successful `New` routes to `update` and supplements the allocation it just created. Do not build the second half before the first |
| You reach for `--status unmapped` and cannot | Derive the CLI `click.Choice` from `XRAS_ACTION_STATUSES` rather than restating it, and give `unmapped` a style in `src/cli/xras/display.py`. One line each; both are restatements of a vocabulary that already exists in one place. ⚠️ Still both unbuilt — and #458 edited a *neighboring* `click.Choice` on the same command without noticing this one |
| Polling the dashboard stops being enough | A digest of `failed` / `manual` / `unmapped` rows. ⚠️ A new entry in `src/scheduling/tasks/` goes live on the next hourly wake unless `SAM_TASKS_DISABLED` names it in the **same** change — the registry is code-side, the list is chart-side, and nothing couples them but the reviewer. `xras_sweep` is **not** this: it digests the *outbound* enumeration and mails nobody. Its arrival did make that warning load-bearing, though — three tasks are live now, not one |

**Removed from this table:** *"a withheld opportunity mapping needs a decision"* — built,
as `sam-admin xras --validate-opportunities` (§ 3.9). It was CLI wiring over a decision
function and a client method that #459 already shipped, and its trigger lands during
triage week by construction.

Two known documentation drifts, harmless and recorded here rather than fixed in a
cutover-week commit: `src/sam/integration/xras.py` documents a `replayed` status the code
never writes (it writes `rechecked`), and the word "replay" survives in RBAC comments for
what is now `recheck.py`.

---

## 7 · Known-open and accepted

These are listed in full at [`XRAS_CUTOVER_RUNBOOK.md`](XRAS_CUTOVER_RUNBOOK.md)
§ *Known-open, and accepted*. The ones most likely to reach you first:

- **11 active resources have no XRAS mapping** — expected. Not every internal resource is
  offered for allocation through XRAS. `--validate-mapping` is a diagnostic, not a gate.
  The direction that *would* break an award — a key XRAS offers with no SAM row — is
  separately checked and was clean 13/13 on 2026-08-20. See § 3.1.
- **Most XRAS opportunities are unmapped, and that is healthy.** The `opportunityId` map
  is additive: an empty table reproduces the free-text ladder exactly. `--validate-
  opportunities` exits non-zero only on a dangling row. See § 3.9.
- **`POST /v1/roles` answers 404/409 where legacy answered 400.** A 409 means "project or
  user is inactive" and the `message` says which. Zero traffic in 58 days of access logs.
- **`unmapped` is not a failure.** It means the broker asked for something we do not
  implement — `DELETE /v1/roles/…` is the documented candidate.
- **SMTP is fail-closed.** If `NOTIFY_ENABLED` is unset, every notice records `suppressed`
  and nothing is delivered, silently. Check Admin → Configuration → Notifications.
- **`xras_notices`, the hourly automatic-notice task, is switched off** through triage
  week — notices go out from the **Notify** button on the Pending Activations card, so a
  human sees each one. Clearing it from `SAM_TASKS_DISABLED` is a follow-up.

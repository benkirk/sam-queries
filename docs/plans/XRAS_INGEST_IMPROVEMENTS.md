# XRAS → SAM ingest — improvement brainstorm

**Status: brainstorm, deliberately unbuilt.** Written 2026-08-23 against issue #433, the
week before XRAS repoints and real payloads start arriving. The incoming API predates two
capabilities that now exist on the outgoing side — the **Remediations card** (merge /
withdraw / re-submit / roster; [`XRAS_REMEDIATIONS.md`](XRAS_REMEDIATIONS.md), built,
unarmed) and the **request editor** (amounts / dates / attributes;
[`../xras/outgoing/REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md)) — and this
page asks what that unlocks for the ingest side, plus what else would help the operators
working triage week.

Companion pages: [`../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md)
(the operator loop this page wants to shorten), [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md)
(the deferred account-queue backlog — not restated here), and the playbook's § 6 sketch
table, which several entries below promote from "shape + trigger" to a fuller design.

**§ 2.1 is promoted to its own build plan:
[`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md).** The summary below is the pointer.

> **Corrections, 2026-08-23** — a research pass against the code overturned several of
> this page's premises; the affected sections are rewritten, and this note says why:
> - Feed B has **no** preflight today (only Feed-A rows run `validate_only`), and the
>   sweep publishes none of the fields a preflight needs — § 2.1 is a new capability, not
>   a generalization, and it covers Extensions and Supplements, not only News.
> - SAM's mnemonic match is **exact casefolded**, not `LIKE` (that is legacy); the
>   institution create-affordance already exists; `mnemonic_code.code` is unique — § 2.2.
> - The broker-retry question was **answered 2026-08-11** (human-pushed, never retried),
>   and `actionId` 388865 proves `action_id` is **not an identity key** — § 2.6 and § 3.
> - `extractors.py` has no logger and `outcome_reason` is never written on `processed`
>   — § 2.4.
> - The Feed-A preflight on the **CLI** path runs with an empty handler registry and
>   reports "would succeed" for everything — § 4, and Phase 0 of the new plan.

---

## 0 · The one constraint, made structural

**XRAS → SAM must work without SAM → XRAS**, degraded but functional. Rather than
re-arguing this per feature, every idea below carries a dependency tier:

| Tier | Depends on | Degraded behaviour required |
|---|---|---|
| **A** | nothing outbound — `xras_action_log`, stored payloads, SAM data | none needed; always works |
| **B** | outgoing **reads** (`XRAS_OUTGOING_ENABLED`, the sweep, live GETs) | honest empty state, never a broken card — the four-empty-states idiom the Remediations card already set |
| **C** | outgoing **writes** (`XRAS_WRITE_ENABLED`, the admin client) | disabled-with-reason controls, never hidden — the lever-off idiom |

The rule: **no tier-A surface may grow a tier-B/C dependency.** The action log, re-check,
and everything in § 3 stay tier A forever; tier-B/C entries decorate them and degrade to
absent. This is already how Feed A vs Feed B fail independently — the tiers just name it.

---

## 1 · Where the remediation panel meets the ingest pipeline

The prompt behind this page: does the ability to edit XRAS requests directly simplify the
replay/resubmit pipeline? **Partly — and it matters where.**

### 1.1 The editor's leverage is pre-push, not post-422

A fact that shapes everything: the editor operates under `XA-CONTEXT: submit` and
therefore edits **only the Requested stage** ([`REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md)
§ 4). After a 422, the data that failed came off the **award**, which our key cannot
touch. So the editor cannot *cure* a failed action — but it can *prevent* the failure:

- **Post-422** (the action already arrived): the fix is SAM-side data (mnemonic,
  contract, mapping, account) or an ACCESS conversation. The editor is not the tool.
- **Pre-push** (the request is Approved but not yet pushed — Feed B): roster fixups,
  placeholder merges, and Requested-stage corrections *before* a human at ACCESS pushes
  the button. This is where withdraw / re-submit / roles / merge shine, and it is the only
  point where SAM can act before the failure exists.

Saying this explicitly in the operator docs is itself worth doing — otherwise triage week
reaches for the editor on a 422 row and finds it edits the wrong stage.

### 1.2 Cross-link the two surfaces, both directions — tier B/C decoration on A

The Remediations card already jumps to the action log ("Show in action log" via
`set-filter-submit`). The reverse link is missing: a failed or parked action-log row whose
`request_number` appears in the sweep's `requests_index` should offer **"Open request…"**
— the existing `xras_request_detail` modal, and from there the whole editor family. Zero
new capability, one lookup against `load_requests_index()` at render, absent when the
index is (tier B). The operator stops re-typing request numbers between tabs.

### 1.3 Triage-playbook-as-code: remedy hints on failed rows — tier A core

Every 422 string is minted by a **named function in `src/sam/xras/errors.py`** — the
catalog in the playbook § 3 is machine-recognizable, not free text. Encode the playbook's
decision tree as a small registry next to the emitters: error family → structured remedy
`(category, whose_problem, fix_surface, needs_repost)`. The action-log detail modal then
renders each stored `error_messages` line annotated:

| Error family | Rendered hint |
|---|---|
| mnemonic (§ 3.2) | "Data fix: organization/institution mnemonic — open the org admin page" |
| PI / username missing (§ 3.3) | "Work the Accounts Needed row for `{username}`" (deep link) |
| contract missing/ambiguous (§ 3.4-5) | "Create/link contract `{grant}`" (contracts surface) |
| resource key (§ 3.1) | "Mapping gap — `sam-admin xras --validate-mapping`; adding a row moves GET bytes (parity note)" |
| dates / amounts (§ 3.6-7) | "Award-side — this needs ACCESS; the editor cannot reach the Approved stage" |
| 400 / handler-raised | "XRAS-side / a bug — see the playbook § 2" |

Matching is by the emitting **family**, tolerant of interpolated values (the emitters can
tag their output, or a regex table keyed to the function names — either way the coupling
lives beside the strings it classifies, so a new emitter without a hint is greppable).
Tier A: hints are text and internal links; the `needs_repost` flag feeds § 1.4. This is
the highest-value/lowest-risk item on this page — it turns the playbook from a document
someone must remember into what the row itself says, during the exact week new operators
are staring at their first 422s.

**How the coupling is built** (decided on research, not yet built): `ActionErrors` stores
plain strings and the emitters are named functions, so the registry lives beside them —
`{emitter: Hint(...)}` — and `classify(message)` is derived by calling each emitter with
sentinel arguments and turning the result into a regex. Wire bytes stay untouched; a test
asserts every emitter round-trips to its own family, so an emitter added without a hint
fails the suite rather than rendering unannotated. One classifier serves both the stored
`error_messages` (Feed A) and the push-readiness verdicts
([`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md)), which are the same strings.

### 1.4 "Ready for re-post" batching — tier A

The playbook's boxed warning is the loop's real cost: *every fix ends with asking ACCESS
to re-post, and a fix is not done until the action comes back and lands.* Until § 3
exists, the round-trip is unavoidable — so batch it instead of dribbling it:

- **Batch re-check**: re-check every `failed` row in the window (CLI first:
  `sam-admin xras --recheck-all --status failed --last 7d`; the dashboard button can
  follow). Each produces the normal audited `rechecked`/`failed` child row — no new
  semantics, just iteration over what exists.
- **The re-post ask as an artifact**: a "ready for re-post" view (and `--format json` /
  markdown export) of rows whose latest re-check is green: `action_id`,
  `request_number`, `action_type`, what was fixed. One consolidated message to ACCESS per
  day instead of N, and a checklist to tick as re-posts land (the arriving re-post writes
  a fresh row with the same `action_id`, so landing is observable).

Two preflights exist and the docs should name which: **SAM-side** re-check (*would SAM's
ingest accept the push?* — `dispatch_action(validate_only=True)`) and **XRAS-side**
`validate` (*would XRAS accept the submission?* — `XrasAdminClient.validate_action`, per
impersonated user, on the re-submit modal). This section and § 2.1 are the SAM-side one.

---

## 2 · Proactive: catch it before the POST arrives

### 2.1 Push-readiness: a SAM-side preflight of every action XRAS has not pushed — tier B

**Promoted to [`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md); this is the pointer.**
The premise this section first had — "the Accounts Needed rows already carry a
`validate_only` preflight; generalize it to Feed B" — was half wrong: only Feed-A rows run
it, Feed-B rows always read "not checked", no synthesizer exists, and the sweep publishes
none of the fields a preflight needs. It is a new capability: synthesize the inbound action
from the `reports/requests` payload (a real translation — resource id → repository key via
`/v1/resources`, nested roles → flat, Approved-stage amounts and dates), run
`dispatch_action(validate_only=True)` inside the sweep while the payloads are in hand, and
publish a verdict **per action** — New, Renewal, Extension, Supplement, Adjustment, on
requests of any status — with `stage` (Approved / Requested) and `push_state`
(seen in the log / applied-inferred / pending / unknown). Renders on the Remediations card
and the Pending Requests tab, with a per-request "Re-check now"; calibrated during triage
week by comparing predicted verdicts with the real 422s as they arrive. Two honesty rules
carry over: the verdict is advisory, and a row that cannot be synthesized says so rather
than guessing green.

Stretch, costing nothing extra: the same board is a thing we can *show* ACCESS ("these
two will bounce if pushed today; give us a day") — publishing the signal back without any
write direction.

### 2.2 The mnemonic unblock report — tier A, a pivot of § 2.1

The playbook calls the organization-mnemonic fix "the highest-leverage data fix available"
(24% of legacy's XRAS failures; 153 of 171 active organizations have no soft link). Four
corrections from the code, each of which changes the tool:

- **The match is exact, not `LIKE`.** SAM resolves `mnemonic_code.description` against
  `organization.name` (or `"Name, City"` then `"Name"` for institutions) by **casefolded
  equality** — `MnemonicCode.build_lookup` / `resolve_for_*`, reused by
  `resolve_mnemonic_code` so the admin create-project form and XRAS cannot drift. The
  `LIKE` is legacy Java. A report that tests `LIKE` over-promises matches SAM will never
  make. (`errors.py` quotes 150/171 — the legacy-`LIKE` census; 153 is SAM's.)
- **Every link mints a code.** `mnemonic_code.code` and `.description` are both uniquely
  indexed, so organizations cannot share a code and there is no FK to "link" — a fix is an
  INSERT of a new 3-letter code whose description equals the name. "Linking 153 orgs" is
  up to 153 new codes. Renaming an org silently breaks its link; the edit form exposes
  `name` with no warning.
- **The write half already exists — for institutions.** The admin Institutions card
  renders a warning badge on a miss that opens the mnemonic-create modal pre-filled with
  the exact match string (`institutions_table.html`, `CREATE_ORG_METADATA`). The
  Organizations card renders `—`. The "linker" is a template change on the org card.
- **A cohort no linker reaches.** `user_organization` is frozen (no rows since
  2026-07-09; 4,563 active users have no current org), and an internal PI's org comes from
  exactly that table. Those PIs fail with nothing to link; the report must show them as
  their own bucket, not under-count them into "orgs to fix".

So the **report** is a group-by over § 2.1's red verdicts whose messages fall in the
mnemonic family: resolve the PI's `_best_institution` / `_best_organization`, rank by how
many pending actions each would unblock, and a separate "no current organization" bucket.
Build § 2.1 first; this is a CLI/card pivot of its output, not a second scan.

### 2.3 The failed/manual/unmapped digest — tier A

Deferred twice already (playbook § 6, [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md)
§ 2) with the trigger "polling the dashboard stops being enough." Real traffic will
likely trip it. When it does: ~80% copy of `expiration_notices` (guards, cap, own-session
ledger), keyed on the **occurrence** with the body carrying the delta, per the account
queue's dedup analysis. ⚠️ Ship it **named in `SAM_TASKS_DISABLED` in the same change**
— that list is fail-open, and this page repeats the warning on purpose.

### 2.4 Un-silence the opportunity map — tier A

Playbook § 3.9a: a wrong `xras_opportunity_allocation_type` row produces a **`processed`
action with the wrong projcode series** — no log line, no ledger entry, and projcodes are
not undoable (it also burns a counter in the wrong series). Three facts from the code
shape the fix:

- `src/sam/xras/extractors.py` has **no logger at all** — "pure and sessionless by
  construction". A log line in `select_allocation_type_mapped` is the module's first, and
  must not disturb that purity beyond logging.
- On a map **hit** the function returns early and never runs the ladder, so "override"
  (map ≠ ladder) is not observable there without *also* running the ladder on the hit
  path. Log **hit / miss** only; the hit-vs-ladder comparison already exists in
  `propose_opportunity_mapping` and `--validate-opportunities`.
- `outcome_reason` is written only on the 500 and `manual` arms — **never on
  `processed`** — and `xras_action_log` has no detail column. Recording "which path
  resolved the type" means either writing `outcome_reason` on the success arm (a new
  semantic for a column documented as "why it parked or failed") or the additive
  `payload_json` the model docstring already sketches.

Recommendation: the log line now; make the resolution **visible pre-push** instead of
attributable post-hoc — `DispatchResult` carries a small `resolved` dict on the
validate_only path (allocation type, panel, facility, mnemonic, map-vs-ladder), and the
push-readiness board ([`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md)) renders "would
mint in series …" before a projcode is burned. A column only if triage week shows a real
mis-series. Folding `--validate-opportunities` into § 2.3's digest still stands.

### 2.5 The account queue's worked state — pointer

[`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) § 1 (`xras_account_event`,
note-and-hide) is the other proactive surface and is already designed; triage week with
two people working the queue is precisely its recorded trigger. Nothing to add here
except: if § 1.3's hints deep-link into the Accounts Needed rows, the queue gets busier,
and the trigger arrives sooner.

### 2.6 Cutover-week hardening: siblings, not duplicates — tier A

- **The retry question is closed — in docs, not yet in code.** Steven Peckins, 2026-08-11
  (`XRAS_CUTOVER_RUNBOOK.md` § 4): *POSTs are not automatically retried. They are
  triggered by a human — a user in xras_admin pushes a button.* So there is no retry loop
  and no reason to answer `500`. The remaining work is a comment at the 422 emitter in
  `webapp/api/xras/actions.py` citing that answer, so the next reader does not re-derive
  it. (Issue #433's body predates the answer; updated 2026-08-23.)
- **Surface siblings, and say "siblings".** `action_id` is stored and indexed for
  detection (`sam/integration/xras.py`) and **rendered nowhere** today. But it is not an
  identity key: `actionId` 388865 arrived twice with different bodies — `NCAR4236` failed,
  `UCHI0020` applied as an update (`XRAS_SPRINT_C.md`). A re-push after a fix is the normal
  loop, so "posted N×" would mislabel the thing the playbook *asks* ACCESS to do. The
  badge reads **"N rows share this actionId"**, and the detail modal lists the siblings —
  `request_number`, status, `received_time`, payload digest — so a re-push after a failure
  is distinguishable from a true double-post. The hazard tell is specifically **≥ 2
  `processed` siblings**. Detection-as-UI, no behavior change, and the measurement § 3
  needs. Naming: `action_id` already means three things across the code (wire id on the
  model, log PK on the dashboard routes and `--show`, outgoing id in `remediation.py`) —
  call the new field `xras_action_id` in UI and CLI.
- **Dropped from this page**: "answer repeats of an already-`failed` `action_id` with the
  same stored verdict without re-dispatching." Under human-pushed re-posts that is the fix
  loop itself, and on 388865 it would have blocked a legitimate apply.

---

## 3 · The re-apply path — the structural fix, sequenced honestly

Not the emphasis of this page, but the brainstorm is incomplete without naming it: § 1.4
batches the ACCESS round-trip; only re-apply **eliminates** it. `recheck.py` already
says what building it requires — *"an idempotency key enforced on `action_id` first,
because 4 of the 6 handlers double-apply"* — and calls the second half "a conversation,
not a flag." This section is that conversation's agenda:

1. **Phase 1 — a double-apply guard on `action_id`, wanted regardless.** A successful
   re-applied `New` routes `(New, exists)` → `update` and supplements the allocation it
   just created; Supplement and Adjustment are additive. ⚠️ But `action_id` is **not an
   identity key**: `actionId` 388865 arrived twice with different bodies — `NCAR4236`
   failed, `UCHI0020` applied as an update — and a re-push after a fix is the *normal*
   loop (§ 1.4). So the guard keys on **a prior `processed` sibling of the same
   `action_id`**, never on "seen before": park as `manual` with a reason naming the earlier
   row and whether the body digest differs, and let a human decide. Value on day one,
   before any re-apply exists — it protects live ingest from a double-click at ACCESS.
   `tests/stress/scenarios.json` pins repeat posts as `processed`; the `repeat_post_*`
   expectations change to `manual` for posts 2..N in the same change. § 2.6's sibling
   badge measures how often it would fire.
2. **Phase 2 — operator-triggered re-apply from the stored bytes.** The verbatim
   `raw_payload` re-enters the *shared* parse ladder and dispatches for real:
   `MANAGE_XRAS`+, audited exactly like re-check (new row, `source_action_id`
   provenance, `processed_by`), offered only on rows whose latest re-check is green.
   Fully **tier A** — the flagship degraded-mode capability: fix the SAM-side data,
   re-apply locally, and the XRAS round-trip disappears for the failure classes that are
   ours (§ 3.2-3.5 of the playbook catalog).
3. **Explicitly not: payload editing.** The stored bytes are the record of what XRAS
   said; an edited-then-applied payload makes SAM diverge from XRAS's award silently.
   SAM-side data is the edit surface; award-side problems go to ACCESS. Truncated
   payloads ("THIS PAYLOAD CANNOT BE REPLAYED") stay re-post-only.

---

## 4 · Paper cuts, recorded so they are not rediscovered

All tier A. The first four are already noted in the playbook § 6 or the account-queue page;
the last three were found in the 2026-08-23 research pass. Listed here because triage week
is when each will be reached for:

| | |
|---|---|
| `--status unmapped` unreachable | derive the `click.Choice` from `XRAS_ACTION_STATUSES`; give `unmapped` a style in `src/cli/xras/display.py` |
| "What did this action write?" | the runbook's correlation query (action → `allocation_transaction` rows in the dispatch window), surfaced on the action detail modal instead of pasted into a SQL prompt |
| `manual` rows have no "done" | a parked row worked by hand leaves no record. Cheapest honest fix: an event-style note ("applied by hand by X", timestamp) on the row — vocabulary shaped like `xras_activation_event`, not a status rewrite |
| `replayed` docstring drift | `sam/integration/xras.py` documents a status the code never writes (`rechecked`); becomes actively confusing the day § 3 adds a real re-apply vocabulary |
| Feed-A preflight on the CLI path | handlers register by import side effect, fired only by `webapp/api/xras/actions.py`; `sam-admin xras --accounts` never imports them, so every dispatch parks and `_validate` maps a parked result to **"would succeed"**. The playbook recommends that command. Fix + test in Phase 0 of [`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md) |
| `manual` ≠ success | the same `_validate` discards the `DispatchResult`; a parked action shows green on the Accounts card too. Carry `preflight_status` on `ActionRef` and derive `would_succeed` from it |
| Feed-B `ActionRef.action_type` | set from `requestType`, which `schemas/forms/xras.py` documents as not the action type; read the action's `actionType` |

---

## 5 · Deliberately not proposed

- **Automatic re-apply / auto-retry of failed actions.** Human-triggered only, ever — the
  same reasoning that keeps `AUTO_NOTICES` fail-closed and scheduled tasks structurally
  unable to write to XRAS.
- **Edit-payload-and-replay** (§ 3.3 above).
- **Servicing `Transfer` / `Date Adjustment`** ahead of ACCESS's answer — parking is
  parity-correct and now visible; that question belongs to them.
- **Any task-side XRAS write.** The invariant is enforced twice (chart + model) and stays.
- **Dual-posting during cutover.** Ruled out in the runbook; not re-proposed.

---

## 6 · Sequencing against triage week

Ranked by (value during triage week) ÷ (risk of building the wrong thing before real
traffic is seen):

1. **§ 1.3 remedy hints + the § 4 preflight fixes** (Phase 0 of
   [`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md)) — turns the playbook into the UI
   before the first real 422, and stops `--accounts` saying "would succeed" for everything.
2. **§ 2.1 push-readiness** — the one surface that acts *before* a push is burned, and it
   covers Extensions and Supplements as well as News. Its verdicts are what § 2.2 pivots.
3. **§ 2.6 sibling badge + the 422-site comment** — cutover-week safety; measures § 3.
4. **§ 1.2 cross-links** — an afternoon, pure navigation; the reverse link also carries the
   predicted verdict once § 2.1 exists.
5. **§ 1.4 batch re-check + re-post artifact** — as soon as the first fixed-and-waiting
   rows accumulate.
6. **§ 2.2 mnemonic report** — a group-by over § 2.1's red verdicts, plus the
   organizations-card badge; first quiet day.
7. **§ 2.3 digest, § 2.5 account queue** — on their recorded triggers, which real traffic
   will either trip or retire.
8. **§ 3 re-apply** — Phase 1 whenever § 2.6 shows real double-applies; Phase 2 after
   triage week's failure mix shows which classes are actually ours to re-apply.

The through-line: triage week is itself the measurement. Most of this page gets cheaper
and better-aimed after a week of real payloads — build the observation surfaces (1.3,
2.1, 2.6, 1.2) first, and let the rest be decided by what they show.
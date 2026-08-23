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

---

## 2 · Proactive: catch it before the POST arrives

### 2.1 A per-request preflight verdict on Pending Requests — tier B

The Accounts Needed rows already carry a `validate_only` preflight
(`would_succeed` / `reject_messages`). Generalize the same idea to **Feed B**: for each
pending request, synthesize the action it will become and preflight it, rendering a
would-it-land verdict with § 1.3's remedy hints attached. The result is a red/green
"push-readiness" board — the 422 catalog applied *before* the 422 exists, while the fix
window is still open and nobody at ACCESS has burned a push on it. Two honesty rules
carry over: the verdict is advisory (the modal's live read is the authority), and a row
that cannot be synthesized says so rather than guessing green.

Stretch, costing nothing extra: the same board is a thing we can *show* ACCESS ("these
two will bounce if pushed today; give us a day") — publishing the signal back without any
write direction.

### 2.2 The mnemonic unblock report, then the linker — tier A

The playbook calls the bulk organization-mnemonic linker "the highest-leverage data fix
available" (mnemonic failures were 24% of legacy's XRAS failures; 153 of 171 active
organizations cannot satisfy the `LIKE` match). Build the **report first**: rank
organizations/institutions by how many known-blocked or preflight-red awards each would
unblock, from stored payloads + Feed B (report is tier A; enrichment tier B). The write
half — a guided linker on the org admin surface — follows only if the report shows
concentration worth automating; if three orgs dominate, three hand edits beat a tool.

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
not undoable. Two cheap tells, neither changing dispatch:

1. **A log line in `select_allocation_type_mapped`** on both override and miss — the
   asymmetry (every other mapping gap 422s; this one is silent) is the bug-shaped part.
2. **Record which path resolved the type** on the action-log row (map-hit vs ladder, in
   or beside `outcome_reason` detail) so a mis-series'd projcode is attributable after
   the fact without re-deriving.

Optionally, fold a scheduled `--validate-opportunities` check into § 2.3's digest so a
dangling row is reported rather than discovered.

### 2.5 The account queue's worked state — pointer

[`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) § 1 (`xras_account_event`,
note-and-hide) is the other proactive surface and is already designed; triage week with
two people working the queue is precisely its recorded trigger. Nothing to add here
except: if § 1.3's hints deep-link into the Accounts Needed rows, the queue gets busier,
and the trigger arrives sooner.

### 2.6 Cutover-week hardening: duplicates and broker retries — tier A

- **Surface duplicate posts.** `action_id` is stored for detection, not prevention
  (`sam/integration/xras.py`), and the stress scenarios pin that a repeat POST
  *processes* — i.e. double-applies. The playbook teaches "three rows sharing one
  `action_id` are one action posted three times"; put that tell on the surface: a
  "posted N×" badge on rows whose `action_id` has siblings, and the sibling list in the
  detail modal. Detection-as-UI, no behaviour change — and the measurement § 3 needs.
- **Close #433's open question in code, not just on the call.** If the broker turns out
  to retry 4xx, one bad payload loops; the issue offers to answer `500` instead. Whatever
  ACCESS answers, record it as a comment at the 422 emitter in `actions.py` and — if
  retries are real — consider answering repeats of an already-`failed` `action_id` with
  the *same* stored verdict without re-dispatching (cheap, idempotent, and the audit
  trail still gains a row).

---

## 3 · The re-apply path — the structural fix, sequenced honestly

Not the emphasis of this page, but the brainstorm is incomplete without naming it: § 1.4
batches the ACCESS round-trip; only re-apply **eliminates** it. `recheck.py` already
says what building it requires — *"an idempotency key enforced on `action_id` first,
because 4 of the 6 handlers double-apply"* — and calls the second half "a conversation,
not a flag." This section is that conversation's agenda:

1. **Phase 1 — idempotency on `action_id`, wanted regardless.** A successful re-applied
   `New` routes `(New, exists)` → `update` and supplements the allocation it just
   created; Supplement and Adjustment are additive. An enforcement point at dispatch
   ("this `action_id` already reached `processed`; park as `manual` with a reason
   instead of applying") protects **live ingest** from broker re-posts and double-clicks
   at ACCESS — value on day one, before any re-apply exists. § 2.6's duplicate badge
   measures how often it would fire.
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

All tier A, all already noted in the playbook § 6 or the account-queue page; listed here
because triage week is when each will be reached for:

| | |
|---|---|
| `--status unmapped` unreachable | derive the `click.Choice` from `XRAS_ACTION_STATUSES`; give `unmapped` a style in `src/cli/xras/display.py` |
| "What did this action write?" | the runbook's correlation query (action → `allocation_transaction` rows in the dispatch window), surfaced on the action detail modal instead of pasted into a SQL prompt |
| `manual` rows have no "done" | a parked row worked by hand leaves no record. Cheapest honest fix: an event-style note ("applied by hand by X", timestamp) on the row — vocabulary shaped like `xras_activation_event`, not a status rewrite |
| `replayed` docstring drift | `sam/integration/xras.py` documents a status the code never writes (`rechecked`); becomes actively confusing the day § 3 adds a real re-apply vocabulary |

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

1. **§ 1.3 remedy hints** — turns the playbook into the UI before the first real 422.
2. **§ 2.6 duplicate badge + retry answer** — cutover-week safety, and it measures § 3.
3. **§ 1.2 cross-links** — an afternoon, pure navigation.
4. **§ 1.4 batch re-check + re-post artifact** — as soon as the first fixed-and-waiting
   rows accumulate.
5. **§ 2.2 mnemonic report** — first quiet day; it decides whether the linker is worth it.
6. **§ 2.1 preflight board, § 2.3 digest, § 2.5 account queue** — on their recorded
   triggers, which real traffic will either trip or retire.
7. **§ 3 re-apply** — Phase 1 whenever § 2.6 shows real duplicates; Phase 2 after triage
   week's failure mix shows which classes are actually ours to re-apply.

The through-line: triage week is itself the measurement. Most of this page gets cheaper
and better-aimed after a week of real payloads — build the observation surfaces (1.3,
2.6, 1.2) first, and let the rest be decided by what they show.

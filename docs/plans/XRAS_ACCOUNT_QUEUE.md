# The XRAS account queue — deferred work

**Status: written down, deliberately unbuilt.** Everything here was scoped during the
2026-08-20 pass that made the worklist legible, and cut from it on purpose so triage week
ships without new tables or new mail. Each item names what it is, why it was deferred, and
what would justify picking it up.

Context first: [`../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md)
— in particular **SAM never creates users**, which is what makes this a queue handed to
another team rather than a control surface.

---

## 1 · `xras_account_event` — making it a *worked* queue

**The blocking item.** The card is read-only. Two people working it cannot see each
other, "asked upstream on the 14th" cannot be recorded, and a row that should not be
worked at all cannot be set aside.

Already designed at `../xras/outgoing/XRAS_OUTGOING_QUERIES.md` § 7.6. The shape:
username-keyed, append-only, `event_type` from a small tuple, `comment`, `created_by`,
`xras_action_log_id` as provenance only.

⚠️ **`XrasActivationEvent` cannot carry it.** Its `project_id` is NOT NULL and
project-scoped; this worklist is username-keyed and a New request *has no project yet*.
That is the whole reason for a second table.

⚠️ **Derive state with the action-keyed supersedes idiom in
`sam/queries/xras_activation.py::_activation_state`** — not the older project-keyed rule
quoted in the `XrasActivationEvent` docstring.

**The property that keeps it small: only dismissal needs storage.** "Done" is already
derived — classification is a check against the current `users` table on every render, so
a row vanishes when the mirror lands and nobody has to close anything. This is a
note-and-hide table, not a ticket system. Suggested vocabulary: `requested` (we asked
upstream, with the date), `comment`, `dismissed`, `restored`. Deliberately **no**
`created` — SAM cannot observe that except by the row disappearing.

DDL applied by hand and recorded in `../xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2,
matching the precedent set by the other XRAS tables; ORM follows the database. Write path
copies the Pending Activations routes (`webapp/dashboards/allocations/blueprint.py`,
the `xras_activate` / `xras_dismiss` / `xras_restore` / `xras_comment` family), which
already carry CSRF, `management_transaction` and the `HX-Trigger` refresh.

**Trigger:** two people working the queue at once, or the first time somebody asks "did
we already ask about this person?"

---

## 2 · A digest, so nobody has to poll a tab

The audience already fields mail — `webapp/utils/rbac.py:258` records that the XRAS
failure mail goes to `hdt@ucar.edu` today. A digest replaces mail with mail, which is the
lowest-friction change for the people receiving it.

~80% of this is copy-paste from `src/scheduling/tasks/expiration_notices.py`: the guards
(`NotificationsDisabled`, `EmailCapExceeded` from `scheduling/tasks/mail_guards.py`), a
per-run `SAM_TASKS_*_MAX` cap, the `_drop_already_notified` pre-filter, the own-session
ledger, `requested_by='task:<name>'`, and `detail` always carrying
selected/suppressed/audience.

⚠️ **The one genuinely open design question is the dedup key**, because a *snapshot*
digest matches neither existing shape. `expiration_notices` keys on something that
changes when the fact changes (the expiration date); `xras_notices` keys on the action id.
A worklist row persists week over week:

- key on the **week** → an unchanged list re-sends every week (probably right for a digest)
- key on the **username** → a person is reported exactly once, ever (almost certainly wrong)

**Recommendation:** key on the **occurrence**, as `task_summary` does, and let the body
carry the delta — *"3 new since last week, 9 still waiting, oldest 37 days"*.
`sam/notify/templates/task_summary.txt` is already the closest body shape.

Also needed: a new `NotificationKind` (`Notifier` hard-raises on an unregistered kind,
`sam/notify/kinds.py:150`) plus `{base}.txt` / `{base}.html`, `facility_aware=False`.

⚠️ **Ship it named in `SAM_TASKS_DISABLED` in the same change** — that list is fail-OPEN,
so a registered task goes live on the next hourly wake unless the chart names it.

**Trigger:** the queue stops being checked daily, or a row is found to have sat for weeks.

---

## 3 · The SAM → XRAS write direction

Three things want it, and they are one capability:

1. **Close abandoned requests.** Requests approved years ago and never pushed still
   surface as people needing accounts (`NCAR0116` and friends, submitted 2015). That is
   upstream data hygiene, not a SAM filter problem — the filters are working.
2. **Buy lead time.** SAM sees these people at *approval*; they are knowable weeks
   earlier, at submission. `SAM_TASKS_XRAS_SWEEP_STATUS` is already a knob.
   ⚠️ Price it before building: 4,088 requests total vs 1,640 in-window, and a one-page
   `status='all'` probe returned 0 rows, so the parameter may not behave as needed.
3. **Publish the signal back**, so a submitter is told at submission time that a named PI
   has no site account — rather than a downstream team discovering it eight weeks later.

⚠️ **All three break a property that is currently structural, not conventional.**
`XrasApiClient` has no verb method other than an internal `_get`, and a test asserts no
`post`/`put`/`patch`/`delete` callable exists on the class — because the same credential
can create requests, modify roles and **merge one person into another**. A write direction
is a **new client with its own credential and its own review**, never a relaxation of this
one.

**Trigger:** ACCESS agreeing to a write scope, or the abandoned-request tail becoming
noisy enough to work.

---

## 4 · Smaller things, recorded so they are not rediscovered

| | |
|---|---|
| **`--status unmapped` is unreachable from the CLI** | The `click.Choice` in `src/cli/cmds/admin.py` lists five of the six in `XRAS_ACTION_STATUSES`, and `_STATUS_STYLE` in `src/cli/xras/display.py` has no entry for it. One line each; both are restatements of a vocabulary that already exists in one place. #458 edited a *neighbouring* Choice on the same command without noticing this one |
| **Two implementations of "the lead must exist and be active"** | `validate_fk_existence` checks existence only — the internal path enforces *active* in the picker's search query, so a forged POST naming an inactive user's id passes server-side. `sam/xras/roster.py` enforces both in code. Worth one shared predicate |
| ⚠️ **Unit tests reach the live XRAS API** | `tests/unit/test_xras_accounts_card.py` was observed making real `GET https://api.xras.org/v1/people/...` calls, because `.env` supplies `XRAS_API_KEY` and the suite inherits it. `tests/conftest.py` blocks `smtplib.SMTP` for exactly this class of reason; the outbound client has no equivalent guard. Not a correctness bug today — the calls 404 harmlessly — but the suite should not depend on a remote host being up, or leak which usernames it tests |
| **`ExporterRegistry` does not exist in this repo** | `CLAUDE.md` and `src/cli/README.md` describe it; it lives only in the peer `hpc-usage-queries`. `Permission.EXPORT_DATA` is declared and enforced nowhere. Any CSV/JSON export off the card is net-new, modelled on `webapp/dashboards/admin/blueprint.py`'s one instance |
| **`compose.yaml` had no `TZ`** | Fixed in the same pass: containers ran UTC while writing naive-Mountain columns, so any age column read as negative. The chart has always set `America/Denver`. Mentioned here because the *data* seeded before the fix still carries UTC stamps |

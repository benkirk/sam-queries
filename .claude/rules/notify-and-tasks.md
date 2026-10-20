---
paths:
  - "src/sam/notify/**/*"
  - "src/scheduling/**/*"
  - "src/sam/queries/expiration_notices.py"
  - "src/webapp/register/handoff_mail.py"
  - "helm/values.yaml"
  - "helm/values-dev.yaml"
  - "helm/templates/cronjob-tasks.yaml"
  - "helm/tests/test-cronjob-render.sh"
  - "tests/unit/notify/**/*"
  - "tests/unit/tasks/**/*"
---

# Notifications and scheduled tasks

Loaded with `sam.notify`, `src/scheduling/` and the task chart wiring. The root `CLAUDE.md` keeps one-line tripwires for the fail-closed and fail-open switches below.

## Notifications — `sam.notify`

One mailer, three consumers: `sam-admin project --upcoming-expirations
--notify`, the webapp's XRAS activation Notify button, and the weekly
`expiration_notices` scheduled task (below). Design and measurements:
`docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`,
`docs/plans/implemented/EXPIRATION_NOTICES.md`.

```python
from sam.notify import Message, Notifier, Recipient
notifier = Notifier(ledger=NotificationLedger(lambda: Session(engine)))
notifier.send(Message(kind='expiration', recipient=Recipient(...), subject=...,
                      dedup_key='expiration:SCSG0001:2026-09-30:pi@x.edu'))
```

⚠️ **Fail-closed.** `NOTIFY_ENABLED` defaults to **false** everywhere; only
`helm/values.yaml` sets it. Every dev container runs an obfuscated production
snapshot, obfuscation does not remove the mail relay, and `ndir.ucar.edu`
relays for the whole `128.117.0.0/16` — a mailer that defaults on can reach
**any address on the internet**. `TestingConfig` also pins
`NOTIFY_TRANSPORT='null'`, and an autouse fixture in `tests/conftest.py`
makes `smtplib.SMTP` raise so no test can open a socket whatever its config.

| | |
|---|---|
| **Suppression** | every `Message` carries a `dedup_key`; `sent`/`redirected` suppress a repeat, `failed`/`suppressed` do not. `--force` overrides. |
| **Stale `queued`** | a `queued` row suppresses only until `NOTIFY_QUEUED_STALE_SECONDS`. Without that horizon one crash suppresses a recipient **permanently** — the counter on the admin card is the same predicate inverted. |
| **The key is pre-redirect** | built from the *intended* recipient, so `NOTIFY_REDIRECT_TO` cannot collapse a staging run onto one key. |
| **Ledger transactions** | `notification_log` commits on its **own** session — mail cannot be un-sent by a rollback. So does the `external_ticket` link written after a filing (`sam/integration/tickets/filing.py::link_ticket`, shared by the webapp and the `xras_request_tickets` task): a filed ticket must survive a later rollback. These two are the §7 exemptions; `xras_activation_event` is the inverse and commits *inside* `management_transaction`. |
| **`preview()` writes no row** | a preview is not an attempt; a stray row would poison the dedup query. |
| **Preview** | every operator send point (XRAS and Project Notify, invite, resend, roster ×2, reject, digest, renew/extend) previews through `webapp/utils/email_preview.py` → `Notifier.preview_delivery()` on `get_notifier(read_only=True)`, building with the send's own builder (`invite_messages`, `plan_renew_allocations`, ...): the same redirect, addressing, `NOTIFY_BCC` (`Transport.envelope_copies`) and overrides as the send, plus "already sent on" from `last_sent_many`. A read-only ledger's `record` raises, so that notifier cannot send. One pane: `dashboards/fragments/email_preview.html` (never a `<form>` or modal toggle; always 200). Spec: `docs/plans/implemented/MAIL_PREVIEW.md`. |
| **Templates** | `src/sam/notify/templates/`, resolved `{base}-{facility}` → `{base}-UNIV` → `{base}`. Text selects the variant and HTML follows it — never resolved independently, or a WNA recipient gets UNIV HTML. |
| **Visibility** | Admin → Configuration → Notifications (`VIEW_SYSTEM_CONFIG`, counts only) → `Details »` (`SYSTEM_ADMIN`, rows name real addresses). |
| **Approver's note** | `adminComments` from the XRAS reports feed (`src/sam/integration/xras_api/comments.py`, keyed by projcode + `actionId`), resolved by the Notify route and the `xras_notices` task and handed to `build_xras_messages(approver_comment=...)`. Fail-open: unconfigured/XRAS down/no match → `None` + one log line, never a withheld mail. `build_xras_messages` builds **one message per action**, as XRAS's award mail was: To the lead (the admin without a lead address), cc the admin when distinct, the note on it; `addressees` names them for the salutation. The dedup key stays on the To address. |
| **Family addressing** | `NOTIFY_<FAMILY>_{CC,BCC,FROM,REPLY_TO}` (family = a `FAMILIES` key in `kinds.py`) is applied by the `Notifier`: cc/bcc **add** to the builder's own (people first, then the env mailbox), From/Reply-To fill only when the builder left them empty. On top, `notification_addressing` rows (scope = family, kind, or `{kind}-{facility}` stem, e.g. `expiration-WNA`) **always add**, read once per `Notifier` through the ledger's session factory, fail-open. Admin → Notifications → **Addressing** (`SYSTEM_ADMIN`) adds/removes rows; deployment defaults are shown read-only. `Message.copies()` is the one redirect-drop rule (transports and ledger read it); what left is recorded in `notification_log.copies` as `cc:a@x;bcc:b@y`. The CronJob forwards every non-empty `NOTIFY_*` by prefix. `NOTIFY_BCC` is the kind-blind global. |
| **Account family** | `account_queue_summary` (the open queue to `NOTIFY_ACCOUNT_QUEUE_TO`, keyed on the day so the Send button and the weekly `account_queue_digest` task cannot both send it) `account_verify` (the public form's link + code; its context carries **nothing the visitor typed**) `account_rejected` (the reject form's checkbox, operator-chosen, keyed on `closed_at` so a reopen can notify again) `account_invite` (the sponsor's checkbox or Resend, keyed on `invite_sent_at`, the stamp its link is signed with; never carries the sponsor's note) `account_request_received` (the invitee's receipt, keyed on `completed_at`; it and the verify mail append the accepted EULA via `_agreement.{txt,html}`, passed in by the webapp because `sam.notify` cannot import it) and `account_ticket` (NUSD's text-only Jira ticket, keyed on the row, From/To `NOTIFY_ACCOUNT_TICKET_{FROM,TO}`; its subject ends in `[SAM-AR-<id>]`, the JQL handle a later lookup needs; with `TICKET_PROVIDER=jira-servicedesk` + `JIRA_WRITE_ENABLED` the same key files through the JSM API instead, mail on any failure, and the hourly reconcile learns keys into `external_ticket` under `JIRA_ENABLED` alone — `docs/plans/implemented/TICKET_PROVIDER.md` § 9). Keep `NOTIFY_ACCOUNT_CC` empty — it would copy every verification mail; a copy on the digest alone is a kind-scoped Addressing row. |
| **Templates / overrides** | Admin → Notifications → **Templates** (`SYSTEM_ADMIN`) edits any of the 34 shipped files (`shipped_template_names()`); a save writes `notification_template_override` (keyed by file name) and the renderer prefers the row on the **next** renderer build, one `SELECT` per `Notifier`. Reset deletes the row. `sam/notify/samples.py` is both the preview input and the variables table; `_email_base.html` (underscore = developer-owned) is never editable. Save **renders** the body against the sample context, because a sandbox refusal is a runtime error. "Preview for" a real project goes through `sam/queries/notification_previews.py` (`build_xras_messages(kind=)` forces the template's kind). Record: `docs/plans/implemented/NOTIFICATION_TEMPLATE_EDITOR.md`. |

**Batch knobs**: `send_many(chunk_size=N)` opens one transport connection per N
*delivered* messages (`None` = one chunk, byte-identical to a plain batch) —
without it a 500-message run holds one SMTP connection throughout, and a relay
that drops it takes every remaining message. `ledger.already_sent_many(keys)` is
the batch suppression check; it shares `_suppression_conditions` with
`already_sent` so the two cannot drift, gated by
`TestTheSingleAndBatchFormsAgree`.

### The `expiration_notices` task

`src/scheduling/tasks/expiration_notices.py`, Monday 09:00 Mountain. The
first task with `needs=('sam',)`.

| | |
|---|---|
| **Window** | from `ctx.occurrence`, converted to the schedule's zone and truncated to **local midnight** — `ctx.occurrence` is naive UTC, `Allocation.end_date` is naive Mountain. |
| **Bands** | `Milestone(label, lo_days, hi_days)` in `sam/queries/expiration_notices.py`, one rung today. The label is **in the dedup key** so enabling 60/30/7 needs no key migration. `band_bounds()` builds the half-open upper edge by hand, because `get_all_expiring_allocations` filters `end_date <=`. |
| **The pre-filter is permanent** | the task drops already-notified messages itself. Left to `Notifier` the ~40 quiet weeks a year would each write hundreds of `suppressed` rows — ~26,000/yr into the table the admin card and the badge read. Deleting it as "redundant" is the regression; the comment says why it isn't. |
| **Guards** | raises if `NOTIFY_ENABLED` is false (else a chart mistake reports `succeeded` and mails nobody) and if the audience exceeds `SAM_TASKS_EMAIL_MAX` (2500). Both fire before any transport. |
| **`detail` always carries** window bounds + selected/suppressed/audience — "0 sent, succeeded" is the *normal* weekly result and must be distinguishable from a query that stopped matching. |
| **Summary mail** | one per run to `SAM_TASKS_SUMMARY_TO`, including on quiet weeks and on the cap trip (before the raise). Never fails the run. |
| **Replay** | `sam-admin tasks --run expiration_notices --force --occurrence 2026-11-23T09:00`. Honored only under `--force`, where the key is `M`-prefixed and cannot claim a real slot. |

### The `xras_notices` task

`src/scheduling/tasks/xras_notices.py`, every quarter hour 08:00–17:00 Mon–Fri Mountain
(`AnyOf` of four `BusinessHourly`; 17:00 is the last slot). Mails the XRAS award notices (phase C): **live in prod** since the
phase C cutover, floored by `SAM_TASKS_XRAS_NOTICES_SINCE` so its first run never repeats
XRAS's own mail. The New's notice is the `/actions` route's, right after activation
(`XRAS_AUTO_NOTIFY`, `sam/manage/xras_activation.py`); this task backstops it. The same floor
gates that notice and the card's `before_cutover` rows (`notices_floor`, `sam/queries/xras_activation.py`),
so `webapp.env` and `tasks.env` carry one value (pinned by `test-dev-render.sh`).

| | |
|---|---|
| **Policy** | `AUTO_NOTICES` — `update`, `extend`, `supplement`, `adjust` with no delay (the next slot), `add` after an hour and **only once the project is active** (a New is *two* writes, `active=True` then the notice). **Fail-closed**: a service not in that tuple is never auto-sent; `transfer` has no kind at all. |
| ⚠️ **Key on `service`, never `action_type`** | a card row badged **New** *can* auto-send: `dispatch.select_service` routes a New whose projcode already exists to `update`. `action_type` is also nullable, aliased (`Adjust`/`Adjustment`), unconstrained, and includes `'Date Adjustment'`, which no service handles. |
| **Threshold** | from `ctx.occurrence` via `to_local_naive`, **not** truncated to midnight (unlike `expiration_notices`): this is a rolling threshold, not a band. `received_time` is naive-Mountain from the app clock. |
| **Window** | `[slot − LOOKBACK, slot]`, 14 days. The lower bound is a blast-radius bound — without it the first run after the switch clears mails every never-notified action in the table. The **upper** bound is determinism: a run reclaimed an hour late must select *its* slot's cohort. |
| **Guards** | `NOTIFY_ENABLED` false raises; audience past `SAM_TASKS_XRAS_MAX` (50, **not** the shared 2500) raises. Both before any transport. |
| **No summary mail** | deliberately, unlike `expiration_notices`: 50 wakes a week, most sending nothing. The ledger row and the admin card are the record. |
| **Writes** | one `XrasActivationEvent(event_type='notified', created_by='task:xras_notices')` per action that actually reached somebody — send first, record second, so the timeline never claims a handoff that did not leave. |
| **Why it cannot double-mail** | button and task both call `build_xras_messages`, so both mint `{kind}:{projcode}:{action_id}:{address}`. The ledger suppresses whichever is second. No locking needed around the card. |
| **Misfire** | the plain 6 h default. A missed slot costs nothing — the window is rolling, so the next slot subsumes it. |

### The `xras_rename` task

`src/scheduling/tasks/xras_rename.py`, hourly :20: renames a request line still under its
`NCAR####` token to its projcode, the backstop to the `/actions` route's after-reply rename.
⚠️ **The one task that writes to XRAS**: `TASK_WRITABLE_OPERATIONS` in
`sam/integration/xras.py` is the only `task:` operation the audit table accepts. Both envs
arm it through `XRAS_WRITE_ENABLED` in `tasks.env` (pinned by `test_xras_admin_client.py`);
removing that key, or naming the task in `SAM_TASKS_DISABLED`, disarms it.
`--dry-run` reads only; `SAM_TASKS_XRAS_RENAME_SINCE` widens the window for the backfill.
The `/actions` route's own rename has a separate lever, `XRAS_RENAME_ENABLED` (`"1"` in both).
Record: `docs/plans/XRAS_CLOSED_LOOP.md` § 3.3, § 3.5. Its after-reply hook also activates a New's
project under `XRAS_AUTO_ACTIVATE` (`"1"` in both; `sam/manage/xras_activation.py`).

### The `xras_request_tickets` task

`src/scheduling/tasks/xras_request_tickets.py`, every 15 min: one NUSD ticket per XRAS New or
Renewal still refused past the grace period (10 min), an internal comment when its blocker list
changes and one when a post applies; NUSD closes it. Keys `xras_ticket:<requestId>:{open,<digest>,applied}`.
⚠️ **The CronJob's one ticket writer**: `TICKET_PROVIDER` and `JIRA_WRITE_ENABLED` reach the task
pod from `tasks.env` alone, so no other task may call `provider_from_environment`
(`test_helm_jira_levers.py`). Prod files to Jira, armed with the floor
`SAM_TASKS_XRAS_TICKETS_SINCE` (a request first refused before it is `predates_floor`, never
opened; `webapp.env` carries the same value so its 422 promises no ticket, state `floored`); dev runs `TICKET_PROVIDER=dry-run` (logs, mints `DRY-<handle>`, outside `PROVIDERS` so
nothing reads it, refused to the webapp) and overrides the write lever and floor off.
`XRAS_REQUEST_TICKETS_ENABLED` off, or `--dry-run`, reports only: the ledger and link rows commit
on their own sessions. The webapp's 422 reads the state with no outbound call
(`sam/queries/xras_request_tickets.py`). Record: `docs/plans/XRAS_REQUEST_TICKETS.md`.

### The `directory_report` task

`src/scheduling/tasks/directory_report.py`, nightly 03:00 Mountain, `needs=('sam',)`: the
read-only sweep of ldap.ucar.edu against SAM (`sam/queries/directory_sweep.py`), counts by
kind and category in `detail`, the headline in `message`; `--dry-run` is the same run. **Ships
named in prod's `SAM_TASKS_DISABLED`**; dev runs it. The CronJob forwards `DIRECTORY_PROVIDER`,
`DIRECTORY_URL`, `DIRECTORY_BASE_DN` and `DIRECTORY_TIMEOUT_SECONDS` by name and never a
`DIRECTORY_APPLY_<KIND>` (asserted per manifest). Unconfigured succeeds with nothing to compare;
an unreadable directory fails the run. Rule: `.claude/rules/directory.md`.

### The account-request tasks

`account_requests_reconcile` (every 15 min, DB-only, **live in prod**: stamps fulfilled
requests, enrolls inside a savepoint so one project without accounts cannot fail the
pass, purges unverified public rows past `SAM_TASKS_ACCOUNT_PURGE_DAYS`) and
`account_queue_digest` (Mon 08:00 MT, one message, reconciles first, `SAM_TASKS_ACCOUNT_MAX`
on the row count, an empty queue sends nothing; **ships named in `SAM_TASKS_DISABLED`**).

⚠️ **`SAM_TASKS_DISABLED` is fail-OPEN.** Registering a task in
`src/scheduling/tasks/` puts it into production **live** on the next hourly
wake unless its name is added to `helm/values.yaml` in the *same change*. The
registry is code-side, the list is chart-side, and nothing couples them but the
reviewer — so `test_task_xras_notices.py` greps `values.yaml` for the name.
`docs/README-k8s.md` enumerates the set in three places.

⚠️ **`NOTIFY_*`/`MAIL_*` must reach the CronJob explicitly.**
`helm/templates/cronjob-tasks.yaml` renders `.Values.tasks.env` plus a
hand-listed set and the `NOTIFY_*` / `SAM_DB_*` / `STATUS_DB_*` prefixes, and
does **not** otherwise inherit `.Values.webapp.env`. A missing
`NOTIFY_ENABLED` there is fail-closed: every message recorded `suppressed`,
`succeeded` reported, exit 0, green Job, no mail, no indication. Guarded twice
— the task's own `config.enabled` check and a **per-manifest**
(`-s templates/cronjob-tasks.yaml`) assertion in
`helm/tests/test-cronjob-render.sh`. A whole-render grep passes on the
Deployment's copy and proves nothing.

⚠️ **`expected_runtime` is a lease knob, not a timeout.** The lease is
`max(3×expected_runtime, 900s)` and **must exceed** the CronJob's
`activeDeadlineSeconds`, or a killed send is reclaimed while it is still
sending and every PI gets a second copy. The task cannot heartbeat
(`TaskContext` exposes no ledger handle). A drift test parses `values.yaml`
and asserts the inequality.

❌ **DON'T** add eager imports to `sam/notify/__init__.py` — `sam/__init__.py`
exports `NotificationLog`, so eager imports there put jinja2 and the
transports into every ORM consumer's import graph.
`tests/unit/gates/test_notify_import_graph.py` is the gate.
❌ **DON'T** re-export anything from `sam/queries/__init__.py` — it is empty on
purpose, so `from sam.queries.x import ...` drags in only `x`'s graph.
`tests/unit/gates/test_layer_imports.py` is the gate.
❌ **DON'T** import Click, Flask, `rich` or `kubernetes` anywhere under
`src/scheduling/` — a task writes to `ctx.logger`, never to stdout, because the
CronJob's stdout is a JSON envelope. The CLI routes logging to stderr as `LEVEL
name: message` (`configure_logging` in `src/cli/core/utils.py`); `kubectl logs`
merges the streams, so `cirrus_healthcheck.sh` strips that shape before
parsing. `test_task_ledger.py` AST-walks the package.

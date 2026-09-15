# HPC account registration — the request portal and the NUSD queue

**Status: design, not built.** A standalone product: the way a person asks for an
NCAR HPC account, the queue the account-creating team works from, and the hook that
lets SAM act the moment the account exists. It is also the identity step that
`XRAS_SUBMISSION.md` phase 3 needs, but nothing here depends on XRAS.

---

## 1. Why

Every HPC account is created upstream by NUSD, in UCAR institutional IT. SAM mirrors
that identity system and never writes `users` — no `User.create()`, no INSERT
anywhere in `src/` (`../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md` § 2). That is the right
division of labor, and it leaves SAM with no intake at all: the only place a "this
person needs an account" fact exists in SAM today is the XRAS **Pending Users** card,
a read-only classifier over handoff rosters with no button, no mail and no export
(`../xras/outgoing/XRAS_OUTGOING_QUERIES.md` § account worklist, `XRAS_ACCOUNT_QUEUE.md`).
An operator reads it and re-types the rows somewhere else.

Three needs share one shape:

| Need | Today |
|---|---|
| A researcher needs an account before anything else can happen | ARC or a help-desk mail; SAM learns of it only if an XRAS handoff names them |
| A project lead brings a cohort — thirty workshop participants, a class — who need accounts on an existing project | Members are added one existing `users` row at a time (`_AddMemberHandler` refuses an unknown username); an XRAS handoff silently skips unknown non-PI members, and thirty `absent` rows appear on Pending Users afterwards |
| A new allocation request from someone without an account (`XRAS_SUBMISSION.md` phase 3) | Not possible from SAM; ARC mints an untracked XRAS placeholder, which is 55% of `New` handoff failures |

One record, one queue, one observer serves all three. NUSD gets a worklist instead of
a card to transcribe; leads get a bulk path that does not exist; the XRAS submission
gets a tracked identity instead of an orphaned placeholder.

## 2. The record

`account_request` — modeled on `XrasRemediationEvent` (no foreign key to the thing
that does not exist yet) and on the Pending Users rule (completion is observed, never
stored). `SessionMixin`; state transitions as methods.

| Group | Columns |
|---|---|
| Identity claim | `email` (lower-cased; the join key SAM already uses to match people — `sam_merge_targets` in `sam/queries/xras_accounts.py` reads `email_address` the same way), `first_name`, `middle_name`, `last_name`, `organization`, `academic_status`, `residence_country`, `orcid`, `phone` — the same field set as an XRAS person (`PERSON_FIELDS`), so a row can feed `POST /v1/people` or an upstream form without re-derivation; `desired_username` nullable (the upstream mints the real one) |
| Intent | `purpose` ∈ `standalone · enrollment · submission`; `project_id` nullable (the project a fulfilled account joins); `sponsor_user_id` nullable (which sponsor enrolled this person, § 2.2); `event_code` nullable (§ 2.1); `xras_username` nullable (the placeholder SAM minted, § 4) |
| Queue state | `state` ∈ `submitted · claimed · requested · rejected · dismissed`; `assignee` (claimed by); `requested_at` (the date SAM told NUSD); `reject_reason`; `comment` |
| Provenance | `created_by` (`self` from the public form, a username for a sponsor or operator, `task:xras_sweep` for rows derived from handoff rosters), `verified_at` (email verification), `creation_time`, `modified_time` |
| Fulfillment | `user_id`, `upid` — stamped when the observer (§ 3.4) finds the mirrored row; `upid` is the upstream's durable person key and outlives a username change |

**Fulfilled is never a stored state.** A request is fulfilled when a `users` row
matching it exists, and that is re-derived on every render exactly as Pending Users
derives `absent` versus `inactive` today. Storing it would let the row disagree with
the mirror. What is stored is only what SAM cannot re-derive: that it asked NUSD, and
when; that somebody dismissed or rejected it, and why; what the requester typed.

Widths follow the XRAS worklist: `email` and usernames at 64, not 35, so an XRAS
placeholder fits. The table is created by hand and the ORM written to match, as
`xras_remediation_event` was (SAM has no migrations outside `system_status`).

### 2.1 Events

`account_request_event` groups requests that belong together — a workshop, a class, a
hackathon, an onboarding wave — because they share a project and a deadline.

| Column | Meaning |
|---|---|
| `event_code` | short, unique, human-typed and human-shared: `WRF-TUTORIAL-2026-10` |
| `name` | what a person sees on the form after entering the code |
| `project_id` | the project every fulfilled account joins |
| `extra_sponsor_user_id` | nullable — one sponsor beyond the project's lead and admin (§ 2.2) |
| `accounts_needed_by` | the creation deadline NUSD works to |
| `opens_at`, `closes_at` | when the code is accepted on the public form |
| `active`, `created_by`, timestamps | `ActiveFlagMixin`; `set()`/`clear()` |

A registration carrying a valid open code inherits `purpose = enrollment`,
`project_id` and the deadline. The queue and the summary mail group rows by event,
deadline first, so NUSD sees "31 accounts for this workshop, due Friday" as one
block. The code is optional and deliberately generic: it costs nothing when unused and
any future cohort is the same mechanism.

### 2.2 Who may run an event

Creating or editing an event, pasting a roster under it, and seeing its rows are one
capability with three doors, all of which exist today:

| Door | Who | How it is checked |
|---|---|---|
| The project | its lead and its single admin | derived from the project on every request, exactly as membership changes are — nothing stored |
| The event | one optional **extra sponsor** (`extra_sponsor_user_id`, say an instructor who is neither lead nor admin) | the one stored sponsor; a join table only if a real event ever needs more than three people |
| RBAC | staff — a new `MANAGE_ACCOUNT_REQUESTS` permission in the `_ALLOCATION_ADMIN` set (`webapp/utils/rbac.py`), which is exactly the `nusd` and `csg` bundles | system-wide, any project |

The route guard is the existing `require_project_permission(Permission.MANAGE_ACCOUNT_REQUESTS)`
(permission system-wide, or the project's lead/admin) with one added clause for the
event's extra sponsor, as a sibling decorator in `webapp/api/access_control.py` that
resolves the event code to its project and passes the event object to the view. The
facility-scoped manager tier reaches it through the facility variant of the same
decorator if that tier is ever granted the permission. The queue in § 3.3 is
permission-only and never project-scoped. The single-administrator model is
untouched: sponsorship adds no role, only one column.

## 3. Surfaces

### 3.1 The public form

`GET/POST /register`, and `/register/<event_code>`, which pre-fills and locks the
code. Unauthenticated, which has precedent (the status dashboard serves anonymous
visitors), with the protections that precedent already carries: CSRF, and the
per-IP login-POST rate tier (`RATELIMIT_AUTH_LOGIN`) rather than the anonymous page
tier, because a POST that creates rows is the shape the login tier exists for. The
row is created `submitted` but invisible to the queue until the requester clicks a
verification link mailed to the address they gave (`verified_at`); an unverified row
older than a configured horizon is purged. An unknown, closed or inactive event code
is refused with the reason. The form asks for the person fields and, without a code,
a free-text "why" that the queue shows.

Validation is a `sam.schemas.forms` schema; the route is a `HtmxFormHandler`
subclass; the write runs inside `management_transaction`. No login means no
`current_user`: `created_by = 'self'`.

### 3.2 Sponsor enrollment

From the project card, any sponsor (§ 2.2: the project's lead or admin, the event's
extra sponsor, or csg/NUSD staff) has two ways to the same rows:

- **Create an event** for the project — code, name, deadline, window — and hand the
  code out; participants register themselves through § 3.1.
- **Paste a roster** (one `name <email>` per line) under an event; each line becomes
  a `submitted`, pre-verified row with `sponsor_user_id` set. Lines whose email
  already resolves to an active SAM user skip the queue and go straight to
  membership.

The event card on the project page lists its sponsors and its open rows, so any of
them can see progress. On fulfillment the observer calls `add_user_to_project`
(`sam/manage`), the same function the member form and the XRAS handlers use. This is the first bulk
membership path in SAM, and it should reuse the one "lead must exist and be active"
predicate the lifecycle document asks for rather than add a third.

### 3.3 The NUSD queue

Admin → Accounts → **Requests**, behind the `MANAGE_ACCOUNT_REQUESTS` permission of
§ 2.2 — held by the NUSD bundle, which fields XRAS failure mail today, and by csg. One table,
grouped by event with the nearest deadline first, then ungrouped rows by age; filters
on state, purpose, event and age. Per row: **claim** (sets `assignee`; a second
operator sees who has it), **dismiss** (with a reason — a duplicate, a person who
already has an account under another address), **reject** (with a reason the
requester is told). Rows carry the person fields, the sponsor and project when there
is one, the deadline, and a "waiting since".

One action for the whole queue: **Send queue summary to NUSD**, which mails a digest
of every open row — counts by purpose and event, then the rows, grouped as the card
is — to the configured NUSD address, and stamps `requested_at` on every row it
included. It goes through `sam.notify` as a new family `account` and kind
`account_queue_summary`, with a dedup key on the day so two clicks do not send two
mails, and lands in `notification_log` like everything else that leaves. A weekly
task can send the same digest unattended; it inherits the `expiration_notices`
guards (fail-closed on `NOTIFY_ENABLED`, a cap, a `SAM_TASKS_DISABLED` entry in the
same change that registers it).

The digest carries names and addresses. Its audience is one team, its content is the
queue they own, and the address is deployment configuration, not a form field.

### 3.4 The fulfillment observer

Every render of the queue, and every digest run, resolves each open row against the
mirror: by `desired_username` (casefolded — the `users` collation trap Pending Users
already guards) and then by email through the existing email → user derivation,
honoring its `ambiguous` outcome rather than guessing. A hit stamps `user_id` and
`upid`, fires the row's linked action, and the row leaves the queue:

| `purpose` | On fulfillment |
|---|---|
| `standalone` | nothing further; the row is history |
| `enrollment` | `add_user_to_project(project_id)`; the sponsor is notified once per event when the last row lands |
| `submission` | the XRAS merge in § 4 |

`users.creation_time` is written by the mirror and read by nothing today; the gap
between `requested_at` and it is NUSD's lead time, worth a number on the card.

What SAM cannot observe: that NUSD has started, or declined, on their side. The
`claimed` and `requested` states are SAM's memory of what it did, not a mirror of
their queue. If NUSD wants a ticket per request, that is a second transport on the
same rows, later.

### 3.5 Pending Users becomes a view of the queue

The XRAS worklist's rows are `account_request` rows the sweep did not have a table
for: every handoff roster member with no active `users` row becomes a `submitted`
row with `purpose = submission`, `created_by = 'task:xras_sweep'`, `xras_username`
the ARC placeholder, and `project_id` once the handoff lands. The card keeps its
classification and its merge-target ranking, reads the rows instead of recomputing
them, and gains the claim and dismiss that it has lacked. Shims first: the classifier
keeps running beside the table until every row it would produce already exists.

## 4. The XRAS phase-3 link

A person with no account may still need to start an allocation request, and review
should not wait on account creation. `XRAS_SUBMISSION.md` § 7 phase 3 does this
under a SAM-minted XRAS identity:

1. The registration row exists and is verified.
2. SAM creates the XRAS person (`POST /v1/people/<username>`, `isReconciled = false`,
   a username of SAM's own pattern so it is recognizable), stores it in
   `xras_username`, and creates the submission with that person as PI. The
   `xras_submission` row carries `registration_id`.
3. On fulfillment the observer merges the placeholder into the real username with
   `merge_placeholder` (`sam/manage/xras_remediation.py`), which re-points every
   XRAS role; the submission row is updated to the real `user_id`.

This is ARC's placeholder mechanism made accountable: every placeholder SAM mints is
named on the row that will merge it, so none can be orphaned, and none can be born
reconciled. The requester sees their request's state in SAM meanwhile — the account
request and, once created, the allocation request — not XRAS's internal view.

The person-create verb is not yet in the write client and has not been probed;
`XRAS_WRITE_PROBES.md` § 4.4 applies (a 200 proves nothing; verify by re-reading
`GET /v1/people/<u>`).

## 5. Out of scope, and open

- SAM still never writes `users`, `active` or `locked`. Reactivation of a locked
  account is a NUSD action, and a request for one is a row here with a purpose
  worth adding (`reactivation`) when the queue exists.
- Identity proofing and 2FA are upstream.
- What NUSD wants on the digest, and whether they want a ticket per request, is a
  conversation with them before the card is built.
- The public form is the first internet-facing write in SAM that is not a login.
  It gets the hardening pass the internet-hardening record prescribes before it ships.

## 6. References

| | |
|---|---|
| [`../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | § 2: SAM never creates users; the upstream owner |
| [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) | the Pending Users queue this generalizes; the designed `xras_account_event` |
| [`../xras/outgoing/XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the account worklist and its two feeds |
| [`XRAS_SUBMISSION.md`](XRAS_SUBMISSION.md) | phase 3, the consumer of § 4 |
| [`../xras/outgoing/XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | the write discipline the person-create verb inherits |
| [`implemented/RATE_LIMITING.md`](implemented/RATE_LIMITING.md) | the anonymous and login-POST tiers |

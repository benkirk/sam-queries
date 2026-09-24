# HPC account registration — the request portal and the NUSD queue

**Status: implemented.** Phases 1 and 2 merged in #569 (2026-09-16), the events
views in #576 and their follow-ups in #578 (records: `EVENTS_VIEWS.md`,
`EVENTS_FOLLOWUPS.md`, beside this file). Phase 3 has its columns and nothing
else. **Future work** is listed at the end of § 0. § 0 records what the build changed
against the design below. A standalone product: the way
a person asks for an NCAR HPC account, the queue the account-creating team works
from, and the hook that lets SAM act the moment the account exists. It ships in two
phases (§ 5) — **internal** first, where authenticated sponsors invite people who
have no account and the XRAS sweep feeds the queue, with no dependency outside SAM;
then **external** self-registration. It is also the identity step that
`XRAS_SUBMISSION.md` phase 3 needs, but nothing here depends on XRAS.

---

## 0. As built — deviations from the design

Eleven commits, each green on its own, in the order the dependencies run:
permission → tables and ORM → query/manage tier → the event guard → the
sweep feed → the notify family → the two tasks → the Accounts queue → the
Pending Users link → the Invitations tab → the flag and the public form.

| | Decision | Why |
|---|---|---|
| D1 | **A render never writes.** The queue derives `open / ready / fulfilled` per render; every write goes through `reconcile_account_requests()` in `sam/manage/account_requests.py`, run by the hourly `account_requests_reconcile` task, the queue's *Reconcile now* button, and the digest before it selects. | § 3.3 had every render stamping rows and calling `add_user_to_project`, which raises on a project with no accounts — a membership commit inside a GET. |
| D2 | `state ∈ submitted · claimed · rejected · dismissed`; `requested_at` is an orthogonal stamp. | A claimed row must be digestable without losing its assignee. |
| D3 | No "sponsor notified when the last row lands" mail. The event row shows `n of m fulfilled`. | A third kind; "last" is unstable as rosters grow; NOTIFY is off everywhere it would be tested. |
| D4 | The project surface is a fourth **Manage Project** tab, `?tab=invitations`. The invitation routes walk the project tree; the page gate (`require_project_permission(EDIT_PROJECTS)`) does not, so a tree-ancestor lead reaches the routes but not the page today. Widening `edit_project_page` and the card link with `include_ancestors=True` is a one-line decision left open. | Confirmed with the operator. |
| D5 | `desired_username` is a hint shown to the operator, never a match key. Email through `sam_merge_targets`, honoring `ambiguous`, is the only resolver. | A casefolded hit on a stranger's username is a plausible collision. |
| D6 | The sweep feeds `absent` rows only; `inactive` is the deferred `reactivation`. | § 6. |
| D7 | The public form is plain PRG with the hidden CSRF input, not an `HtmxFormHandler`. | A phone-facing page for people with no account. |
| D8 | Link and page tokens on two salts of `SECRET_KEY`; the code stored as an HMAC of `id:code`. | A leaked "check your mail" URL cannot verify; constant-time, fixed width; the brute-force bound is the rate limit. |
| D9 | An operator **Mark verified** action on the queue (vouch). | Needed in production anyway (bounced mail, a phone call), and the only path where NOTIFY is off. `NOTIFY_TRANSPORT=console` shows the mail body in compose. |
| D10 | The digest recipient is `NOTIFY_ACCOUNT_QUEUE_TO`, its link `NOTIFY_ACCOUNT_QUEUE_URL`; dedup key `account_queue_summary:<day>:<address>`. | `NOTIFY_`-prefixed keys reach the CronJob by prefix with no template edit. |
| D11 | `email VARCHAR(255)`; `event_id` instead of `event_code` on the request row; `closed_by/closed_at/closed_reason` for both closures; `verified_by`, `verify_code_hash CHAR(64)`, `verify_expires_at`, `fulfilled_at`, `fulfill_error`. Index names never equal a table name — Postgres keeps both in one namespace. | D1, D8, and the dual backend. |
| D13 | The affiliation is labeled **Institution** on every surface (SAM's word: the university, lab or company; an *Organization* is a UCAR/NCAR unit under it). The column stays `organization`, XRAS's wire name for the same thing, so the sweep and a future `POST /v1/people` need no mapping. Both forms offer live institutions as a `<datalist>` (free text still allowed). A UCAR organization second level and an `institution_id` stamp on a matched name are later ALTERs. | Terminology pass, 2026-09-17. |
| D14 | **A global send ceiling on the public form**, `RATELIMIT_REGISTER_GLOBAL` (a fixed limiter key so every registration POST shares one bucket; default `10 per hour; 30 per day`), stacked on the per-IP and per-address tiers. Deliberately low: it doubles as a safety default so enabling the form cannot open an unbounded mailer, and is raised by env once the human-challenge gate lands. | The per-IP tier is blind until the platform forwards the client IP (D12), so a fixed-key ceiling is the only cap on *breadth* abuse — one attacker, many victims — and on the `ndir.ucar.edu` relay's blast radius. See § 6.1. |
| D16 | **`ACCOUNT_REGISTRATION_LOGIN_REQUIRED`**, default on: every `/register` route (the mailed verify link included) redirects an anonymous visitor to login, and the form and pending pages carry a preview banner. The dev overlay also pins `RATELIMIT_REGISTER_GLOBAL` to `5 per hour; 20 per day`. Switching it off per deployment is what opens the public form, once § 6.1's gate lands. | The form can be shown to signed-in testers on dev now without exposing an anonymous mailer; the undo is a config flip, not a code change. |
| D17 | **A signed-in visitor to `/register/<event_code>` gets a self-enroll shortcut, not the anonymous creation form.** `POST /register/<event_code>/enroll` adds their own account to the event's project (`add_user_to_project`, idempotent); the open event link is the capability, the session is the identity, no mail round-trip. Enrolling others stays in the RBAC'd Invitations panel (§ 3.1). | A logged-in internal user already has an account, so the creation form (phone-for-Duo, academic status, country) is out of context; their real intent on an event link is "enroll me." 2026-09-18. |
| D18 | **A dedicated enrollment ledger, `account_request_event_enrollment` (§ 2.3), is the single source of truth for who joined an event.** One helper `enroll_user_in_event` does `add_user_to_project` + an idempotent upsert of `(event_id, user_id, source)`, called by every path (self-enroll, existing-user invite, roster, reconcile of a queued request). The Invitations event row expands to the enrollee list; the user's **My Events** tab reads it the other way. | The only prior tie was `account_request.(event_id, user_id)`, which the two direct-add paths (existing-user invite `OUTCOME_ADDED`, signed-in self-enroll) never wrote — so "who enrolled via this event" was unanswerable. DDL was still uncommitted-to-prod, so a table was a same-PR change. 2026-09-18. |
| D19 | **`copy_button` (`fragments/clipboard.html` + `static/js/clipboard.js`) is the app's first shared copy-to-clipboard control.** CSP-safe: one delegated `[data-copy]` listener, feedback on the `showToast` channel. Its first use is the Invitations tab's Event Registration URL. Backport candidates (not done here): "copy link to this view" on the routable/deep-linked dashboards, and the XRAS operator identifiers pasted back into XRAS. | Operators asked for a shareable event link; the control is built reusable so the deep-link and XRAS copies are one macro call each on their own tracks. 2026-09-18. |
| D15 | **The rejection notice is the operator's choice.** The reject form carries an "Email this reason" checkbox; ticked, `build_rejection_message` (kind `account_rejected`, family `account`) mails the recorded reason to the requester after the commit and stamps `closure_notified_at` only on a delivered send. Unticked, nothing leaves. A reopen clears the stamp and a second reject mints a new key. | The queue copy promised a notice that did not exist; a mail nobody chose would surprise both the operator and a sweep-derived stranger. |
| D12 | The pre-production retrospective. Every person-typed or person-echoing column is utf8mb4 (`academic_status` widened to 64, `residence_country`, `fulfill_error`); `xras_username` is stored lower-cased and matched with a plain `IN`; `requested_at` is *first told* and never moves; `modified_time` is `NOT NULL`, stamped at create. Added now so no later `ALTER` is needed: `account_request_event.instructions` (sponsor prose on the public form), `verify_sent_count` + `source_ip` (the abuse signals; the ingress address today, the client's once the platform forwards it), `closure_notified_at` (D15), `merged_at` (phase 3). The unused `account_request_event_deadline` index is gone. | `fulfill_error` holds `str(ValueError)` with interpolated names, and a 4-byte character there failed the reconcile pass outside its savepoint; the rest is the design's own rule, every column from the start. |
| D20 | **The sponsor may mail the invitee a link to finish their own row** (§ 3.6). A checkbox on Invite and on roster paste, ticked by default, plus a Resend action; kind `account_invite`, sent first and stamped second (`invite_sent_at`). The link opens the terms gate, then a pre-filled form; submitting updates the same row (phone, country, academic status, ORCID, preferred username) and stamps `completed_at`, `eula_sha`, `eula_accepted_at`. The row is in NUSD's queue from the start, badged *awaiting invitee* until then. Events gain `invite_only`, which closes every self-service path. Amends § 3.1's "no mail to the invitee". | NUSD received a name and an email only: no phone for Duo, no country, no academic status, and no recorded terms acceptance. The invitee is the one person who knows those. Record: `docs/plans/implemented/ACCOUNT_INVITE_LINKS.md`. 2026-09-24. |

**Operator handoffs, not automated:** apply `scripts/sql/create_account_request_event.sql`
then `create_account_request.sql` to production and read the columns back by
name; regenerate the obfuscated LFS blob afterwards and verify both purges ran;
clear `account_requests_reconcile` and then `account_queue_digest` from
`SAM_TASKS_DISABLED` once NUSD confirms `NOTIFY_ACCOUNT_QUEUE_TO`; set
`ACCOUNT_REGISTRATION_ENABLED` per deployment (dark in `values.yaml`, on in
`values-dev.yaml`); decide D4; `sam-admin cache --refresh` after deploy.

**Future work, for a follow-up:**

- **Real client-IP forwarding** -- § 6.1 #2. The human challenge (#1) is in
  (§ 6.3); the client IP still has to reach the app through the ingress for the
  per-IP tier to mean anything, and until then `ACCOUNT_REGISTRATION_LOGIN_REQUIRED`
  stays on in prod.
- **The production config switch.** `ACCOUNT_REGISTRATION_ENABLED=1` with
  `ACCOUNT_REGISTRATION_LOGIN_REQUIRED=1` lights self-enroll, the public Upcoming
  Events card and the enrolled counts with no anonymous mailer; the write-up and
  its caveat are in `EVENTS_FOLLOWUPS.md`. With it go the two task switches in
  `SAM_TASKS_DISABLED` named above.
- Phase 3 (§ 4), `reactivation` as a purpose (§ 6), and
  `account_request_event.modified_by` (a prod ALTER; the lifecycle logs the actor
  until then).

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
| A project lead wants to **invite a user who has no account** — one external collaborator onto an existing project, or thirty workshop participants at once | Leads and admins can add *existing* users only (`_AddMemberHandler` refuses an unknown username); anyone else is a help-desk mail. An XRAS handoff silently skips unknown non-PI members, and thirty `absent` rows appear on Pending Users afterwards |
| A new allocation request from someone without an account (`XRAS_SUBMISSION.md` phase 3) | Not possible from SAM; ARC mints an untracked XRAS placeholder, which is 55% of `New` handoff failures |

One record, one queue, one observer serves all three. NUSD gets a worklist instead of
a card to transcribe; leads get an invitation path that does not exist — for one
person or a cohort, since not every project runs workshops but every project has a
reason to bring in an outside collaborator; the XRAS submission gets a tracked
identity instead of an orphaned placeholder.

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
| The event | one optional **extra sponsor** (`extra_sponsor_user_id`, say an instructor who is neither lead nor admin), picked from the SAM user search (`sponsor` context, gated like the event routes) | the one stored sponsor; a join table only if a real event ever needs more than three people |
| RBAC | staff — a new `MANAGE_ACCOUNT_REQUESTS` permission in the `_ALLOCATION_ADMIN` set (`webapp/utils/rbac.py`), which is exactly the `nusd` and `csg` bundles | system-wide, any project |

An event's own lifecycle (create, edit, close, reopen) moved to `MANAGE_EVENTS`,
held by the same bundles -- see `docs/plans/implemented/EVENTS_VIEWS.md`.

The route guard is the existing `require_project_permission(Permission.MANAGE_ACCOUNT_REQUESTS)`
(permission system-wide, or the project's lead/admin) with one added clause for the
event's extra sponsor, as a sibling decorator in `webapp/api/access_control.py` that
resolves the event code to its project and passes the event object to the view. The
facility-scoped manager tier reaches it through the facility variant of the same
decorator if that tier is ever granted the permission. The queue in § 3.3 is
permission-only and never project-scoped. The single-administrator model is
untouched: sponsorship adds no role, only one column.

### 2.3 The enrollment ledger (D18)

`account_request_event_enrollment` (`scripts/sql/create_account_request_event_enrollment.sql`,
model `EventEnrollment`) is the durable event↔user tie: one row per user who
joined an event, `UNIQUE(event_id, user_id)`, with a `source`
(`self`/`invite`/`roster`/`reconcile`). It exists because `add_user_to_project`
records only project membership, so the two direct-add paths — existing-user
invite (`OUTCOME_ADDED`) and signed-in self-enroll (D17) — left no event trace,
and the request row's `(event_id, user_id)` never covered them. The single write
point is `enroll_user_in_event(session, event, user, source, by)` in
`sam.manage.account_requests` (membership + idempotent upsert, together so they
cannot drift); every path calls it, including `reconcile` for a fulfilled queued
request. Reads: `enrollees_for_event` (the Invitations event row expands to the
list) and `events_for_user` (the user's **My Events** tab). No FKs, per the
family rule; a row survives its event or project being retired.

## 3. Surfaces

### 3.1 Inviting a user (phase 1)

Leads and admins can already add an existing user to their project. This is the
same gesture for a person who has no account yet. From the project card, any sponsor
(§ 2.2: the project's lead or admin, the event's extra sponsor, or csg/NUSD staff)
has three ways to the same rows:

- **Invite user** — name and email for one person, an optional note NUSD will see,
  and the project they join on fulfillment. The everyday case: a collaborator at
  another institution, a new student, a visitor.
- **Create an event** for the project — code, name, deadline, window — and hand the
  code out; participants register themselves through § 3.5 once phase 2 ships. Until
  then the sponsor pastes the roster.
- **Paste a roster** (one `name <email>` per line) under an event; each line becomes
  a row with `sponsor_user_id` set. Lines whose email already resolves to an active
  SAM user skip the queue and go straight to membership.

A sponsor-created row is `submitted` with `verified_at` set at creation: the sponsor
is authenticated and vouching, so the queue sees the row at once, with no mail round
trip. The sponsor may also mail the invitee a link to fill in the rest of the row
themselves (§ 3.6, D20). A workshop is therefore nothing special — an invitation
with a code and a deadline attached, grouped for NUSD.

The event card on the project page lists its sponsors and its open rows, so any of
them can see progress. On fulfillment the observer calls `add_user_to_project`
(`sam/manage`), the same function the member form and the XRAS handlers use. This is
the first bulk membership path in SAM, and it should reuse the one "lead must exist
and be active" predicate the lifecycle document asks for rather than add a third.

### 3.2 The NUSD queue (phase 1)

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

### 3.3 The fulfillment observer (phase 1)

Every render of the queue, and every digest run, resolves each open row against the
mirror: by `desired_username` (casefolded — the `users` collation trap Pending Users
already guards) and then by email through the existing email → user derivation,
honoring its `ambiguous` outcome rather than guessing. A hit stamps `user_id` and
`upid`, fires the row's linked action, and the row leaves the queue:

| `purpose` | On fulfillment |
|---|---|
| `standalone` | nothing further; the row is history |
| `enrollment` | `add_user_to_project(project_id)`; the sponsor is notified once per event when the last row lands |
| `submission` | the XRAS merge in § 4 (phase 3) |

`users.creation_time` is written by the mirror and read by nothing today; the gap
between `requested_at` and it is NUSD's lead time, worth a number on the card.

What SAM cannot observe: that NUSD has started, or declined, on their side. The
`claimed` and `requested` states are SAM's memory of what it did, not a mirror of
their queue. If NUSD wants a ticket per request, that is a second transport on the
same rows, later.

### 3.4 XRAS-derived rows (phase 1)

The queue is useful on its first day without anyone typing, because the XRAS
worklist's rows are `account_request` rows the sweep did not have a table for: every handoff roster member with no active `users` row becomes a `submitted`
row with `purpose = submission`, `created_by = 'task:xras_sweep'`, `xras_username`
the ARC placeholder, and `project_id` once the handoff lands. The card keeps its
classification and its merge-target ranking, reads the rows instead of recomputing
them, and gains the claim and dismiss that it has lacked. Shims first: the classifier
keeps running beside the table until every row it would produce already exists.

### 3.5 The public form (phase 2)

`GET/POST /register`, and `/register/<event_code>`, which pre-fills and locks the
code. Unauthenticated, which has precedent (the status dashboard serves anonymous
visitors), with the protections that precedent already carries: CSRF, and the
per-IP login-POST rate tier (`RATELIMIT_AUTH_LOGIN`) rather than the anonymous page
tier, because a POST that creates rows is the shape the login tier exists for. An
unknown, closed or inactive event code is refused with the reason. The form asks for
the person fields and, without a code, a free-text "why" that the queue shows.

The row is created `submitted` but invisible to the queue until the address is
verified (`verified_at`). Verification needs nothing SAM does not have: the mailer,
the ledger and the relay exist, and Flask ships a signed, expiring token serializer
keyed on `FLASK_SECRET_KEY`. One mail carries both a **link** (the token names the
row; the route checks signature and age) and a **six-digit code** (its hash and
expiry on the row) typed into the page the person is already on — the code helps
where a mail client rewrites links or the person is on a phone. The mail body
carries nothing the visitor typed, so SAM cannot be used as a relay with a UCAR
return address, and the form is limited per IP and per address so nobody can flood
a stranger's inbox. An unverified row older than a configured horizon is purged.
SMS is not part of this: see § 6.

Validation is a `sam.schemas.forms` schema; the route is a `HtmxFormHandler`
subclass; the write runs inside `management_transaction`. No login means no
`current_user`: `created_by = 'self'`.

**Signed in, the event link is a self-enroll shortcut (D17).** A visitor to
`/register/<event_code>` who is already authenticated does not need the creation
form — they have an account. `form_for_event` renders a one-click confirm
instead, and `POST /register/<event_code>/enroll` adds their own account to the
event's project (`add_user_to_project`, idempotent) with no email round-trip:
the open event link is the capability, the session is the identity. Registering
*others* is unchanged — the RBAC'd Invitations panel (§ 3.1) — so the lowest
tier self-enrolls but cannot enroll anyone else. The anonymous creation form is
still what an unauthenticated visitor sees when `LOGIN_REQUIRED` is off.

### 3.6 The invitation link and invitation-only events (D20)

**The link.** Invite and roster paste carry a *send a link* checkbox, ticked by
default; the Invitations list has a Resend action per open, uncompleted sponsor
row. `webapp/register/invite_mail.py` builds one `account_invite` message per row
(no sponsor note: that is addressed to NUSD), sends, and stamps `invite_sent_at`
only on a delivered message. The token (salt `account-invite`, max age
`ACCOUNT_INVITE_TTL_DAYS`, default 30) carries that stamp, so a resend voids
every older link.

**The page.** `register_invite` (`webapp/register/invite.py`, `/register/invite/`)
is mounted with the invitations switch and has **no login hook**: the invitee has
no account, the token is the capability, and the route sends no mail, so it
cannot relay. It shares `form.html`, `gate.html` and the form context
(`webapp/register/common.py`) with the public form. A bad, expired or superseded
token, or a closed or fulfilled row, gets a refusal page at 200; a completed row
gets *already received*. Otherwise the terms gate (an accept bound to this link),
then the form pre-filled from the row with the email read-only. Submitting runs
`AccountRequest.complete_invite()`: the same row, updated in place. No verify
mail: opening the link proved the address. The event window does not apply, since
the row already exists.

**Recording the terms.** Both flows stamp `eula_sha` (the git blob SHA of
`webapp/register/eula.md`, `eula.eula_sha()`) and `eula_accepted_at` (the gate's
accept time). The public form stamps them only when its gate was passed.

**`invite_only` events.** `_open_event` refuses such a code with "by invitation
only", which closes `/register/<code>`, a posted `event_code` and signed-in
self-enroll; the event is left out of the form's picker, and the public card
shows *By invitation* where the register button would be (`listed` still decides
whether it appears at all). Personal links ignore the flag: the roster is the way
in. The event form's box posts an `invite_only_present` sentinel, like `listed`.

**Queue surfaces.** The *awaiting invitee* / *completed &lt;date&gt;* badge is on
the Invitations list, the Accounts queue card and the digest rows; the request
detail shows the invite stamps and `EULA <sha7> @ <time>`.

## 4. The XRAS phase-3 link

A person with no account may still need to start an allocation request, and review
should not wait on account creation. `XRAS_SUBMISSION.md` § 2 phase 3 does this
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

## 5. Phases

| Phase | Ships | Needs |
|---|---|---|
| **1 — internal** ✅ built | the `account_request` and `account_request_event` tables and ORM, every column from the start (nullable where a later phase fills it — a column added later is a hand DDL on the production VM); the NUSD queue card with claim, dismiss and reject, grouped by event, behind `MANAGE_ACCOUNT_REQUESTS`; **Invite user** and the event/roster workflow on the project card for lead, admin, extra sponsor and staff; rows derived from the XRAS sweep rosters; the fulfillment observer with `add_user_to_project`; the queue-summary mail once NUSD has said what they want on it | nothing outside SAM; the NUSD conversation, for the digest only |
| **2 — external** ✅ built, dark in production | the public self-registration form with email verification (link and code), the login-POST rate tier, the hardening pass; event codes accepted from the public form | phase 1; the internet-hardening review |
| **3 — XRAS link** | the placeholder-and-merge path of § 4; `registration_id` on `xras_submission` | phase 2; `XRAS_SUBMISSION.md` phase 2 in production |

Phase 1 does double duty twice over: the invitation replaces a help-desk mail for
every project, workshop or not, and the sweep-derived rows give the XRAS work the
account queue it has lacked. The public form is last because it is the one surface
that needs verification, abuse limits and a hardening review before it exists.

## 6. Out of scope, and open

- SAM still never writes `users`, `active` or `locked`. Reactivation of a locked
  account is a NUSD action, and a request for one is a row here with a purpose
  worth adding (`reactivation`) when the queue exists.
- Identity proofing and 2FA are upstream.
- What NUSD wants on the digest, and whether they want a ticket per request, is a
  conversation with them before the card is built.
- The public form is the first internet-facing write in SAM that is not a login.
  It gets the hardening pass the internet-hardening record prescribes before it ships.
- **SMS verification** is an optional later transport behind the same interface as
  the emailed code. It is not free, and not for a licensing reason: every reliable
  path is a metered API (Twilio, AWS SNS, Vonage), and US carriers require sender
  registration for application-originated texts, which takes weeks. The carrier
  email-to-text gateways are unreliable and being retired. If UCAR holds such an
  account it is a small addition; it is never a prerequisite.

### 6.1 Registration abuse (email-bombing) — the ceiling that is in, and the follow-on

The form mails a verification link to whatever address is submitted. Double
opt-in already prevents a *fake account* — an unverified row never reaches the
queue (D1, § 3.5) — so the residual risks are (a) email-bombing a third party,
(b) reputation damage to the `ndir.ucar.edu` relay, (c) queue noise.

**In place now:** the per-address cap `RATELIMIT_REGISTER_EMAIL` (3/hr, 5/day)
bounds a single victim; a honeypot (`website`); the verify-TTL purge; and the
**global ceiling** `RATELIMIT_REGISTER_GLOBAL` (D14), which bounds the site-wide
send rate regardless of source and defaults low so an enabled form is a trickle
until deliberately raised.

**Gaps the ceiling does not close — deferred because each needs an external
dependency, and required before `ACCOUNT_REGISTRATION_ENABLED=1` in prod:**

1. **A human challenge — DONE (§ 6.3).** Cloudflare Turnstile on the form's
   submit, verified server-side before the write and the mail, failing closed.
2. **A real client IP** — the per-IP tier is blind behind the load balancer
   (D12): `get_remote_address()` collapses to the ingress address. Confirm the
   proxy chain and set `PROXYFIX_X_FOR`, or have the ingress forward a trusted
   client-IP header. This is the precondition for any per-IP defense.

Notes on the ceiling's limits: it counts POST *attempts* (an upper bound on
mails, so it fails safe), it weakens to per-worker if the limiter falls back to
`memory://` instead of shared Redis, and the verification `dedup_key` changes on
every issue — so the limiter, not the ledger, is what caps repeats. (1) + (2)
are the pair that actually closes the gap; this subsection is the "hardening
pass" the § 6 bullet names.

### 6.2 The accept-first gate and the branded flow (follow-on)

A proof-of-concept follow-on puts the public form behind an accept-first gate
and restyles the whole `/register` flow onto a standalone branded shell that
carries the shared email design (the navy "sheet" of `_email_base.html`), so
the page and the confirmation mail read as one thing.

**The gate** (`ACCOUNT_REGISTRATION_GATE_ENABLED`, on by default; off in
`TestingConfig`). One URL, dynamic content: `GET /register/` renders a
terms-of-use (EULA) acceptance, and only a recent accept in the session lets
the open form through. It is **enforced
server-side** — `submit()` re-checks the session marker and writes nothing
without it, so the gate is not merely a hidden UI. `POST /register/accept`
validates `RegisterGateForm` (the terms box), then sets the marker (cleared after a submission, so each request re-accepts).
The marker lasts two hours: an expiry at submit bounces to the gate and the
typed form is lost, so the window is generous. Clearing it is best effort — the
session is a client-held cookie, so a saved post-accept cookie replays inside
the window; the rate limits are the bound. That is why the human check is
verified **at submit**, not here (§ 6.3). The terms box stays disabled until the
end of the agreement has been scrolled into view (`register.js`, an
`IntersectionObserver` on a sentinel after the text) — a reading aid, not a
control: the server only checks `accept=1`, and with JS off the box is live.
Below the panel, "Open full terms" opens `GET /register/terms` in a new tab: the
same vendored text at full height, read-only (accepting stays on the gate).

**The event code is a picker, not free text.** The open form offers the
publicly `listed` open events as an optional select, fed by the memoized
`upcoming_events_data()` the status page already uses (every lifecycle write
invalidates it); with nothing listed the field is absent. An unlisted event is
reachable only through its `/register/<code>` link, whose hidden code also posts
`event_locked`, so an error re-render stays locked instead of dropping a code
the select cannot show. `submit()` still validates through `_open_event` —
hand-posting an unlisted code is equivalent to holding the link.

**Country of residence is a datalist.** `country_names()` (`sam/queries/admin.py`)
feeds all 224 live `country` names into a static `<datalist>` — pick or type,
no round trip, still free text in a string column. The table is upper case, so
names are display-cased; two rows are stored double-encoded and are corrected by
ISO code there rather than in the database. The shell loads htmx **only** for
the Institution search (1,400 names, so that one stays a typeahead); a test pins
it, because the search dies silently without the script.

**The EULA is the real NWSC agreement**, vendored verbatim from `NCAR/HPC-Docs`
(`docs/getting-started/end-user-agreement.md`) as `webapp/register/eula.md` and
rendered from markdown at request time (`webapp/register/eula.py`, cached;
`gate.html` emits it). mkdocs uses the same python-markdown
engine, so the site's markdown reproduces here; relative doc links are mapped
onto the published site. Refresh with `scripts/update_eula.py` and review the
diff in a PR — the accepted legal text is what changed — the same discipline as
the vendored front-end assets. Vendoring (not a live import) keeps the build
deterministic and records exactly what a visitor accepted.

**Refreshing the EULA.** Driven by the `update-vendored-assets` skill.
`python scripts/update_eula.py [--ref <tag|sha>]` overwrites `eula.md` and
prints the upstream blob SHA; `git diff` is then the review, and no diff means
current. There is no safe/high-risk split as for a library — any wording change
alters what people agree to, so a human decides. When approved:

- Never hand-edit `eula.md`, not even a typo or a link; it is exempt from the
  prose and link gates for that reason (`RECORD_FILES` in
  `tests/unit/gates/test_docs.py`). A wording problem is fixed upstream, then
  re-pulled.
- Record the blob SHA and the upstream ref in the commit message — the SHA is
  the only version identifier the text has, and the acceptance record below
  will stamp it. Commit the refresh alone so it reverts alone.
- The output is injected **unescaped** on the premise that the source carries no
  raw HTML; if upstream adds any, that premise needs a fresh look first.
  mkdocs-only syntax (admonitions, attribute lists, snippets) renders as literal
  text under plain python-markdown, and a new relative link must match the
  sibling `*.md` pattern `eula.py` rewrites or it 404s from our page.
- Gates: `pytest tests/unit/webapp/test_account_registration.py -k "Eula or Gate"`
  and `tests/unit/gates/test_docs.py`. Then look at the gate on webdev in both
  themes. `eula_html()` is `lru_cache`d per process, so restart webdev to see
  new text; a deploy restarts the workers and the gate page is not in the Redis
  page cache, so no cache refresh is needed.

**Recording EULA acceptance (done, D20).** `account_request.eula_sha` holds the
git blob SHA of the accepted `eula.md` (what `update_eula.py` prints and
`eula.eula_sha()` computes) and `eula_accepted_at` the gate's accept time, from
the public form and from the invitation link alike (§ 3.6).

This points at a larger, separate project: EULA acceptance is **not
registration-only**. Eventually *every* user may need to accept — and
**re-accept annually** as the terms are revised — which belongs on the
user/account, not the request row, with its own currency check ("accepted the
current version within the last year?") gating access. The registration gate is
the first, narrow instance of that.

**The shell.** `templates/register/base_register.html` (cloned from
`auth/login.html`) + a thin token-only `static/css/register.css`; every
`register/*` template re-parents onto it. Behavior is `static/js/register.js`
(the scroll-to-end hold on the terms box, and the form's submit held until the
human-check widget reports — niceties only; the server enforces). Design language: docs/plans/EMAIL_STYLING.md.

### 6.3 The human check (Cloudflare Turnstile)

**Where.** On the open form, verified in `submit()` after the schema, event and
purpose checks and immediately before the write and the mail — so a typo never
spends the single-use token, and every mail-sending POST costs a fresh solve
(the gate's replayable cookie buys nothing). The gate carried a same-origin
stub until this landed; it was removed, not kept as a fallback.

**Provider-neutral by construction.** Turnstile, hCaptcha and reCAPTCHA share
the protocol: a widget `<div class=… data-sitekey data-callback>` plus one
`<script src>`, and a server-side `siteverify` form POST of `secret` + `response`
returning `{"success": …, "error-codes": […]}`. So `webapp/utils/human_check.py`
holds a `PROVIDERS` table of static facts (script, verify URL, response field,
widget class, CSP origins) and one generic `verify()`. Adding hCaptcha is one
row; the env names (`HUMAN_CHECK_PROVIDER`, `HUMAN_CHECK_SITE_KEY`,
`HUMAN_CHECK_SECRET_KEY`) and the chart block (`humanCheckCredentials`) do not
change. `webapp/utils/csp.py` adds the active provider's origins to
`script-src`/`frame-src` site-wide (the policy is built once, like the calendar
iframe's).

**Behavior.** No token → "complete the verification", no outbound call.
`success: false` → re-render with the typed form kept, `error-codes` logged at
WARNING. Transport error or bad JSON → **fail closed** ("service unavailable"),
nothing written, nothing mailed. `remoteip` is not sent: the ingress collapses
every client to one address (§ 6.1 #2), and a wrong IP is worse than none.
`validate()` refuses to start on an unknown provider or a provider without both
keys; ProductionConfig warns when the form is public with provider `none`.

**Configuration.**

| | `HUMAN_CHECK_PROVIDER` | keys from |
|---|---|---|
| prod (`values.yaml`) | `turnstile` | OpenBao `csg/sam-turnstile` → `site_key`, `secret_key` |
| samuel-dev | inherited | the same path, **shared by decision** |
| local k8s (`values-local.yaml`) | `none` | — |
| webdev / tests | `none` by default | `.env`; tests patch `human_check._siteverify` |

**Operator setup (one widget for every deployment).**

1. Cloudflare dashboard → Turnstile → Add widget. Name it for SAM registration;
   hostnames `sam.hpc.ucar.edu`, `samuel.k8s.ucar.edu`,
   `samuel-dev.k8s.ucar.edu` (the zones need not be on Cloudflare); mode
   **Managed**; pre-clearance **off**. Copy the site key and secret key.
2. Write both to OpenBao `csg/sam-turnstile` as `site_key` / `secret_key`
   (readable through the `csg-ro` SecretStore). **Before** the chart deploys:
   a missing path means no Secret, and new pods stall in
   `CreateContainerConfigError` while the old ones keep serving.
3. After a deploy, force-sync if needed
   (`kubectl annotate externalsecret <name>-human-check-credentials-esos force-sync=$(date +%s) --overwrite`),
   then `/register` shows the widget. The widget's Cloudflare analytics show
   solves per hostname.

**Rotation.** Rotate the secret in the widget's settings, update
`csg/sam-turnstile.secret_key`, force-sync, `kubectl rollout restart`.

**Local.** Cloudflare's published dummy keys render a "testing only" widget on
any host, localhost included: site `1x00000000000000000000AA` with secret
`1x0000000000000000000000000000000AA` (always passes) or
`2x0000000000000000000000000000000AA` (always fails). See `.env.example`.

## 7. References

| | |
|---|---|
| [`../../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | § 2: SAM never creates users; the upstream owner |
| [`../XRAS_ACCOUNT_QUEUE.md`](../XRAS_ACCOUNT_QUEUE.md) | the Pending Users queue this generalizes; the designed `xras_account_event` |
| [`../../xras/outgoing/XRAS_OUTGOING_QUERIES.md`](../../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the account worklist and its two feeds |
| [`../XRAS_SUBMISSION.md`](../XRAS_SUBMISSION.md) | phase 3, the consumer of § 4 |
| [`../../xras/outgoing/XRAS_WRITE_PROBES.md`](../../xras/outgoing/XRAS_WRITE_PROBES.md) | the write discipline the person-create verb inherits |
| [`RATE_LIMITING.md`](RATE_LIMITING.md) | the anonymous and login-POST tiers |

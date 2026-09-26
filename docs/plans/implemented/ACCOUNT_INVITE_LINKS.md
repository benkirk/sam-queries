# Invitation email: the invitee completes their own request

> **Status:** built 2026-09-24 (branch `account-invite-links`, PR to `staging`).
> The design below is kept as approved; the deviations are listed first.

## As built — deviations

- `build_invite_message` takes `sent_at` (the stamp the link is signed with, so
  the dedup key exists before the row is stamped) and the `event` object (the
  mail needs its instructions and deadline, not just its name), plus `requested_by`.
- One sender, `webapp/register/invite_mail.py:send_invite_links`, serves the
  invite checkbox, roster paste (both copies) and Resend.
- `send_invite` is not injected into the form: an absent checkbox already loads
  as `False` via `load_default`, and nothing here means "unchanged".
- `register_invite` also serves `/terms` (the gate's "Open full terms" link,
  since `register.terms` may be unmounted) and `/complete`; the accept POST is
  `/<token>/accept`. The blueprint joins `DASHBOARD_BLUEPRINTS` in the route-map gate.
- The invite gate is always shown, whatever `ACCOUNT_REGISTRATION_GATE_ENABLED`
  says: it is what records the acceptance.
- The invitee may also correct their name and institution
  (`AccountRequest.INVITE_FIELDS`); the email is never a form field.
- Resend applies to sponsor rows only (`sponsor_user_id` set): a sweep or
  self-registered row has no sponsor to vouch for it.
- Admin → Events is always mounted, so its roster form draws the checkbox only
  when `ACCOUNT_INVITATIONS_ENABLED` is on (the link route needs it).
- `invite_only` is editable by anyone who may edit the event, not operator-only
  like `listed`: it only ever narrows who may join.
- The roster cap is `RosterPasteForm`'s 20,000-character bound; sends go out in
  chunks of 50 per relay connection.
- The digest's text template carries the badge too, not only the HTML cell.
- Added in the same PR: an event's "accounts needed by" date may not be in the
  past (the picker's floor, and the server on create or on a changed date), and
  one under `DEADLINE_LEAD_DAYS` (28) saves with a warning toast. Both create
  routes share `EventCreateHandler` in `event_lifecycle.py` for it.
- CLAUDE.md's template count is 30: the 22 it stated predated the three
  lifecycle kinds' six files.

## Context

Today a sponsor's "Invite user" (Manage Project → Invitations, and roster paste) calls
`invite_user` (`src/sam/manage/account_requests.py:61`). An active SAM user is added to the
project directly. Anyone else gets an `account_request` row that is created already
**verified**, because the sponsor vouches for it. That row goes straight into NUSD's queue
and the digest, and **no mail goes to the invitee**. This was deliberate in §3.1 of
`docs/plans/implemented/ACCOUNT_REGISTRATION.md`: "NUSD contacts the invitee anyway."

Two gaps motivate a change.

- **NUSD gets a thin record.** The invite collects name, email and optional institution
  only. It has no phone (needed for Duo), no country, no academic status, no ORCID and no
  preferred username.
- **No EULA acceptance is recorded anywhere.** The `/register` gate sets only a session
  marker, and the design doc parks `eula_sha` as a follow-up.

Decisions (Ben, 2026-09-24):

| Topic | Decision |
|---|---|
| Queue | The row stays **visible immediately**, badged "awaiting invitee" until the invitee completes it. |
| Trigger | A checkbox on the invite form and on roster paste, **ticked by default**, plus a Resend action. Mail leaves only by the sponsor's choice, the same pattern as D15. |
| Gating | The link belongs to the **invitations** feature: it works whenever `ACCOUNT_INVITATIONS_ENABLED` is on, **independent of** `ACCOUNT_REGISTRATION_ENABLED` and `ACCOUNT_REGISTRATION_LOGIN_REQUIRED`. Anonymous registration stays a separate deployment choice. |

- **Why the link needs no login:**
  - The invitee has no account, so a login wall would make the link dead.
  - The signed token is the capability.
  - The route sends no mail, so it cannot be used as a relay.
- **Mounting:** the `register` blueprint is mounted only when registration is on. So the
  invite pages get their own small blueprint, `register_invite` (see below), mounted by
  the invitations switch. They reuse the `/register` shell, templates and form helpers.

Branch from `origin/staging`; the PR targets `staging`.

## Data (one idempotent ALTER; prod handoff)

`scripts/sql/alter_account_request_invites.sql`, modeled on
`scripts/sql/alter_account_request_event_listed.sql`. It touches both tables:

- `account_request_event.invite_only`: TINYINT(1) NOT NULL DEFAULT 0, after `listed`.
  Existing events backfill to 0. See "Events" below.
- Four nullable columns on `account_request`:

| Column | Type | Meaning |
|---|---|---|
| `invite_sent_at` | DATETIME | Last time the invite mail was sent. It is also the token binding, so a resend invalidates older links. |
| `completed_at` | DATETIME | When the invitee finished the form. |
| `eula_sha` | VARCHAR(40) | The git blob SHA of the agreement that was accepted. |
| `eula_accepted_at` | DATETIME | When that agreement was accepted. |

Wiring:

- ORM columns go in `src/sam/core/account_requests.py`.
- New model methods, per CLAUDE.md §7:
  - `mark_invite_sent(clock)`
  - `complete_invite(*, fields, eula_sha, accepted_at, source_ip, clock)`, which updates the
    person fields and the stamps, then flushes
- Rerun `tests/integration/test_schema_validation.py`.
- Regenerate the LFS test blob in the PR: `make regen-lfs-blob` from a reset 3307. The
  bootstrap is table-gated.
- Run the PG tier on :5434.

**Self-registration gets the EULA stamp too.** `submit()` copies the session's gate-accept
time and `eula.eula_sha()` onto the new row.

`eula.eula_sha()` is new: the git blob SHA of `eula.md`, `lru_cache`d. It is the same
identifier `scripts/update_eula.py` prints.

## Mail

- **New kind:** `account_invite`, in the `account` family, in `src/sam/notify/kinds.py`.
- **Builder:** `build_invite_message(row, *, invite_url, sponsor_name, projcode, event_name,
  expires_days)` in `src/sam/queries/account_notices.py`.
  - Dedup key: `account_invite:<id>:<invite_sent_at iso>`.
  - Content is sponsor- and operator-known only: invitee name, sponsor, project and event.
  - The sponsor's **note stays out**. It is addressed to NUSD.
- **Templates:** `src/sam/notify/templates/account_invite.{txt,html}`, on `_email_base.html`.
- **Sample:** add an entry in `sam/notify/samples.py`, which feeds the template editor and
  previews. The shipped-template count in CLAUDE.md goes from 22 to 24.
- **Send order:** send first, then stamp. `invite_sent_at` is written only when the ledger
  status is sent or redirected.

## Sending (sponsor side)

- **Invite form** (`src/sam/schemas/forms/account_requests.py`):
  - `InviteUserForm` gains `send_invite` (bool).
  - The route injects `'send_invite' in request.form`, because an unchecked box is absent
    from the POST.
- **Invite handler** (`src/webapp/dashboards/project_invites.py`):
  - `_InviteUserHandler.after_commit`: for `OUTCOME_QUEUED` with `send_invite` on, build the message, send it, then run `mark_invite_sent` in a
    `management_transaction`.
  - The success message says whether a link was sent.
- **Roster paste** (`htmx_roster_paste` + `paste_roster`, and the admin events copy in
  `admin/events_routes.py`):
  - Same checkbox.
  - Send to the QUEUED rows with `send_many(chunk_size=…)`.
  - Cap it with the existing roster size bound.
- **Resend:**
  - Route: `POST /project-invitations/<projcode>/requests/<id>/resend-invite`, under the
    same `_GUARD`.
  - It only applies to open, not-yet-completed rows with an email.
  - It re-stamps `invite_sent_at` and so invalidates the old link.
- **Visibility:** no extra switch. The checkbox and the Resend button live on surfaces that
  already exist only when invitations are on.

## Completing (invitee side): new `src/webapp/register/invite.py`

- **Blueprint:** `register_invite`, with `url_prefix='/register/invite'`.
  - Mounted in `src/webapp/run.py` when `ACCOUNT_INVITATIONS_ENABLED` is on.
  - Independent of the `register` blueprint's own mount.
  - It has no login hook at all.
- **Shared pieces:** move the form-context builder out of `blueprint.py` into
  `webapp/register/common.py`, re-exported so imports keep working. That covers the
  academic options, `country_names`, and the institution options.
- **Templates:** parameterize `form.html` and `gate.html` on `form_action`, `accept_url`
  and `institutions_url`, in place of their hardcoded `url_for('register.…')` calls. That
  way they render from either blueprint.
- **Endpoints in `register_invite`:**
  - its own anonymous `institutions` fragment
  - its own accept POST
  - the two invite pages described below
- **Route-map snapshot:** regenerate it (`ROUTE_MAP_REGEN=1`) for the new endpoints.

- **Tokens** (`tokens.py`):
  - New salt `'account-invite'`.
  - Payload `{'id', 'sent': invite_sent_at}`.
  - `read_invite_token` checks the signature and the age. Max age is
    `ACCOUNT_INVITE_TTL_DAYS`, a new config key defaulting to 30.
- **`GET /register/invite/<token>`** (in `register_invite`):
  - A bad token, an expired token, a `sent` mismatch (a newer link exists) or a closed or
    fulfilled row renders a refusal page with a reason, at status 200.
  - An already-completed row renders a "thanks, already received" page.
  - Otherwise the visitor sees the EULA gate: `gate.html`, whose `accept_url` points at
    the invite accept POST. That POST sets a session marker tied to this token and
    redirects back. After that, `form.html` renders in invite mode:
    - pre-filled from the row
    - email shown read-only, because it is what the sponsor vouched for
    - no event picker, no purpose note, no honeypot or human check
- **`POST /register/invite/<token>`:**
  - Load `RegisterForm`, ignoring `email` and `event_code`.
  - Call `row.complete_invite(...)` inside `management_transaction`, stamping the EULA from
    the session's gate time.
  - Redirect to a "complete" page.
  - **No verify mail.** Clicking the link already proved the address. Since
    2026-09-26 the submit mails the invitee a receipt (`account_request_received`,
    with the accepted agreement appended) and files NUSD's ticket
    (ACCOUNT_REGISTRATION.md D22, D23).
  - Rate limit: the anonymous tier per IP, plus a per-token limit.
- **Login gate:** not applicable. `register_invite` has no login hook, and the `register`
  blueprint's `_login_gate` is untouched.

## Events: roster paste and "invitation only"

**How roster paste works with this.** `paste_roster` already runs `invite_user` once per
pasted person, with the event attached. With the checkbox ticked:

- **No SAM account yet:** each such person gets their own invite link. Their row already
  carries `event_id` and `project_id`, so completing the form only fills in the person
  details and the EULA stamp.
- **Already an active SAM user:** they are enrolled directly, as today, and get no link.
- **Mail content:** for an event invite, the mail and the invite form both show the event
  name, the sponsor's `instructions` and the `accounts_needed_by` date.
- **Event window:** completing a link is allowed while the row is open, even after the
  event's `closes_at`. The window governs *new* registrations; this row already exists in
  NUSD's queue and is only being filled in.

**The `invite_only` flag.**

- Model: add `invite_only` to `AccountRequestEvent` and to its `create`/`update`, next to
  `listed`.
- Lifecycle UI: an "Invitation only" checkbox on the event form, in
  `webapp/dashboards/event_lifecycle.py` and its form schema. Admin → Events and the
  Invitations tab share that code.
- With the flag set, the event is **closed to every self-service path**:
  - `_open_event` refuses the code with: "<CODE> is by invitation only; use the link in
    your invitation email." That covers `/register/<code>`, the event picker and a
    hand-posted `event_code`.
  - The event is left out of the public form's event picker (`_event_options`).
  - A signed-in visitor gets the same refusal. Self-enroll is off, so only people on the
    roster join.
  - On the public Upcoming Events card, `listed` still decides whether the event appears.
    An invitation-only event shows "By invitation" in place of the register link.
- **Personal invite links ignore `invite_only`.** That's the point of the flag: the roster
  becomes the only way in.
- `invite_only` works the same whether anonymous registration is on or off. With
  registration off it only closes signed-in self-enroll; with it on, it also closes the
  anonymous code path.

## Queue surfaces

The badge is "awaiting invitee" when `invite_sent_at` is set and `completed_at` is null, and
"completed <date>" once filled. It appears in three places:

- the Invitations list rows
- the Admin → Accounts queue card
- the digest cell (`_account_cells.html`)

The request detail shows the new person fields and `EULA <sha7> @ <time>`.

## Tests

- **Model and manage:** `complete_invite` updates the row in place, keeping row count and
  id. `mark_invite_sent` stamps the time.
- **Tokens:**
  - round trip
  - wrong salt
  - expiry
  - a `sent` mismatch after a resend is refused
- **Invite route:**
  - bad, expired and superseded tokens are refused
  - closed or fulfilled rows are refused
  - the gate appears first, then a pre-filled form with the email read-only
  - a submit updates, never inserts, and stamps `completed_at`, `eula_sha` and
    `eula_accepted_at`
  - the page is reachable with `ACCOUNT_REGISTRATION_ENABLED=False` and with
    `LOGIN_REQUIRED` on; `/register/` itself still 404s in that app
  - it 404s when `ACCOUNT_INVITATIONS_ENABLED` is off
  - a completed row shows the done page
  - no notifier call happens
- **Sponsor side:**
  - the checkbox on sends one message and stamps the row; off sends nothing
  - a mail that is not delivered leaves the row unstamped
  - roster paste sends one message per queued row
  - resend invalidates the old link
- **Self-register:** submit stamps `eula_sha` and `eula_accepted_at`.
- **Invitation-only events:**
  - `/register/<code>` and a posted `event_code` are refused with the invitation message
  - a signed-in self-enroll is refused
  - the event is absent from the picker
  - the public card shows "By invitation"
  - a personal invite link for that event still completes
  - roster paste with the checkbox sends one message per queued row, and the mail carries
    the event instructions and deadline
  - the lifecycle form round-trips `invite_only`
- **Notify:**
  - the kind is registered in its family
  - the template renders against the sample
  - the snapshot under `tests/unit/snapshots/notify_renders/` is regenerated
  - the template-editor count
- **Also:** the route-map parity snapshot (`ROUTE_MAP_REGEN=1`), the gates, and the full
  suite on MySQL plus the PG tier.

## Docs

- `ACCOUNT_REGISTRATION.md`:
  - a new D-row that amends the §3.1 "no invitee mail" rationale
  - mark the EULA-acceptance follow-up as done
  - a short §3.x on the invite link and invitation-only events
- CLAUDE.md: add `account_invite` to the account-family row and change the template count
  from 22 to 24.

## Verification

1. Run `pytest` (full suite) and `SAM_TEST_DB_URL=…:5434 pytest` (PG tier).
2. Local webdev with `NOTIFY_TRANSPORT=console`:
   1. Invite a new address, with the box ticked.
   2. The console shows the mail. Open its link in a fresh browser; no login should be needed.
   3. Go through the gate, then the pre-filled form, then submit.
   4. The same row id now has phone, country and the other fields plus the EULA stamps.
   5. The queue badge flips from "awaiting invitee" to "completed".
   6. Resend, and check that the old link is refused.
   7. Create an invitation-only event and paste a roster with two new addresses and one
      existing user.
      - The two new addresses get links; the existing user is enrolled.
      - `/register/<code>` is refused.
      - Each link completes into its own row.
3. Ben's handoff: apply the ALTER script to prod before the image rolls. It is idempotent and
   prints a verification SELECT.

## Out of scope

- An invitee who self-registers at `/register` instead of using the link still creates a
  second row. `register_request` does no deduplication; this is noted for later.
- No mail to already-active users on `OUTCOME_ADDED`.

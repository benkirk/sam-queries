---
paths:
  - "src/sam/core/account_requests.py"
  - "src/sam/manage/account_requests.py"
  - "src/webapp/register/**/*"
  - "src/webapp/dashboards/event_lifecycle.py"
  - "tests/factories/account_requests.py"
  - "tests/unit/webapp/test_account_requests_*.py"
  - "tests/unit/tasks/test_task_account_requests_*.py"
---

# Account requests and events

Loaded with the account-request model, its manage layer and `/register`.

## Account requests (`sam/core/account_requests.py`)

- **AccountRequest** / **AccountRequestEvent**: a person who needs an HPC
  account, and the cohort (workshop) they register under. SAM never originates
  users (they arrive by the LDAP sync API); the row holds what SAM cannot re-derive. `state` is
  submitted/claimed/rejected/dismissed — fulfilled is derived from `users` by
  email (`sam_merge_targets`) and stamped only by
  `sam.manage.account_requests.reconcile_account_requests()`; a render never
  writes. Design + as-built record: `docs/plans/implemented/ACCOUNT_REGISTRATION.md`.
  Surfaces: Admin → Accounts (`MANAGE_ACCOUNT_REQUESTS`), Manage Project →
  Invitations, the Pending Users column, and the anonymous creation form
  (`/register/`, `ACCOUNT_REGISTRATION_ENABLED`, off in prod). The `/register/<code>`
  event pages (`register/events.py`) ride `ACCOUNT_INVITATIONS_ENABLED` instead.
  The **event lifecycle** (create/edit/close/reopen) is `MANAGE_EVENTS`
  everywhere: Admin → Events (always mounted) and the Invitations tab share
  `webapp/dashboards/event_lifecycle.py`. `listed` opts an event onto the public
  Upcoming Events card on `/status/events` (rendered only while
  `ACCOUNT_INVITATIONS_ENABLED` is on; memoized, invalidated by every lifecycle
  write). Admin → Events is facility-scoped (`all_events(facility_names=)` + a
  per-event check) and carries the enrollee list and roster paste; a roster is an
  account-request write, so its routes stay `MANAGE_ACCOUNT_REQUESTS`. Records:
  `docs/plans/implemented/EVENTS_VIEWS.md`, `EVENTS_FOLLOWUPS.md`.
  The sponsor's **invitation link** is `register_invite` (`/register/invite/`),
  mounted with `ACCOUNT_INVITATIONS_ENABLED`, **no login hook** (the signed token,
  bound to `invite_sent_at`, is the capability); submitting updates the sponsor's
  row in place and mails the invitee a receipt. `invite_only` events close every
  self-service path. Record: `docs/plans/implemented/ACCOUNT_INVITE_LINKS.md`.
  **NUSD's handoff is one text-only Jira ticket per request** (`account_ticket`,
  `register/handoff_mail.py`, ACCOUNT_REGISTRATION.md D22); the digest stays off.

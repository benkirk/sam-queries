# Follow-on: Events management (Admin) + public Upcoming Events + `MANAGE_EVENTS`

> **Handoff doc** written straight after the account-registration PR (#575)
> merged to `staging`, while that context was hot. Implement in a fresh session:
> **branch from `origin/staging`.** #575 delivered the event → registration →
> enrollment workflow (self-enroll D17, the enrollment ledger D18, `copy_button`
> D19, My Events, Invitations enrollees); this builds the two remaining views on
> the same `AccountRequestEvent` data. Read `docs/plans/ACCOUNT_REGISTRATION.md`
> (D16–D19, §2.3) first.

> **As built (2026-09-18, one PR).** The handoff below is kept as written; where
> the build differs, this section wins.

## As built -- deviations from the handoff

| Handoff said | Built | Why |
|---|---|---|
| Three PRs | One PR (Parts A-D); the `copy_button` backports and the loose-bucket split stay out | Owner preference; unrelated tracks |
| "The conftest bootstrap creates the column ... CI is green without the blob" | Blob regenerated **in the PR** (`ALTER` on 3307, `make regen-lfs-blob`, then `clone-pg-test`) | The bootstrap is table-gated and the blob already carried the table, so the CREATE script reaches no test DB |
| Card + hand-built link always | Card **and its query** gated on `ACCOUNT_REGISTRATION_ENABLED` | The hand-built URL avoids the BuildError but 404s in prod; the per-project precedent is safe only because that tab is itself dark |
| Tab `visible` also keyed on `upcoming_events` | Dropped | Prod sets the calendar URL, so the tab is always visible; the change would put a SAM query on all six anonymous status pages |
| `listed` sentinel-gated **and** injected from `request.form` | `updates['listed'] = 'listed' in request.form`, applied only for an operator (`can_create_events`) | Presence-gating makes a checkbox set-only; a steward's edit must not publish an event |
| `upcoming_listed_events(today=)` | `now=` naive-Mountain, never the status page's UTC `now` | Matches `is_open_at`; UTC would be 6-7 h off |
| Reuse `enrollees_for_event` count / `events_for_user` | Grouped `func.count`; `enrolled_event_ids` | The former is N+1 across events |
| Parameterize or fork `event_form_htmx.html` | Parameterized; Admin -> Events includes the Invitations modal shell | One form, one shell |
| Project picker / sponsor search unspecified | A `MANAGE_EVENTS` project typeahead; `context=sponsor` without `projcode` needs `MANAGE_EVENTS` | Existing pickers are gated on `CREATE_PROJECTS` / `SYSTEM_ADMIN`; the create form has no project yet |
| Schema validation "on MySQL and Postgres" | MySQL only; Postgres is the suite on a re-cloned 5434 | Both schema gates are `mysql_only` |

Also touched, unlisted in the handoff: the `steward_less_client` fixture (strips
`MANAGE_EVENTS` too), the exact column set in `test_schema_validation.py`,
`test_nav.py`, both pins in `test_modal_shell_contract.py`, and the page title.

### Status-page caching

The status blueprint predates the caching layer and has none. Whole-page
`@cache.cached` is the wrong tool: `base.html` bakes a session-bound CSRF token
into every page and `user_aware_cache_key` puts every anonymous visitor on one
key. So data is cached, not HTML: `event_lifecycle.upcoming_events_data()` is a
`@cache.memoize` of plain dicts, and create / edit / close / reopen call
`invalidate_upcoming_events()` after commit (`delete_memoized` is real for
functions, unlike views). The fail-soft `try` sits outside the memo, so a SAM
outage is never cached. Verified against Redis on webdev: close removes the
card on the next request, reopen restores it.

**What the follow-up found** (`docs/plans/implemented/EVENTS_FOLLOWUPS.md`):
`webapp/audit/events.py` flushes the whole Flask cache on *every* Session commit,
any bind -- the collectors commit at least three times per five minutes. A
status-page memoize therefore lives well under its TTL, and
`invalidate_upcoming_events()` is belt-and-braces (kept: a non-web writer would
not trigger the hook). So the status pages got query fixes, not a cache:

1. `?hours=` / `?days=` on the anonymous drill-downs is one clamped parser
   (`[1, 720]`, junk falls back to 7 days); it was an unguarded `int()`.
2. Snapshot reads opt out of the `lazy='selectin'` child collections ingest
   needs; the lookups the queries already join are `contains_eager`.
3. Latent, not exploitable today: the per-user full-page caches embed
   `csrf_token()`. `/allocations/projects` carries no state-changing request. If
   one is ever added, put a session component in `user_aware_cache_key`.

### Facility scope (added by the follow-up)

Dropped from the first PR without a record. Admin -> Events admits a
facility-scoped `MANAGE_EVENTS` holder: `all_events(facility_names=)` filters the
list, every per-event route checks `has_permission_for_facility` on the event's
project (403 outside it; a vanished project needs the unscoped grant), and the
project typeahead and create refuse an out-of-scope project. Nobody holds a
scoped grant today.

### The `listed` sentinel (supersedes the table row above)

The form posts a hidden `listed_present=1` beside the checkbox and the handler
sets `listed` only when it is there (and the caller is an operator). Without it,
any PUT that never drew the box -- a script, a future slimmer form -- unpublished
the event.

### Rollout

Apply `scripts/sql/alter_account_request_event_listed.sql` to prod **before**
the code rolls -- the ORM selects `listed`.

---

## Context

Events are still only reachable **per project** (Manage Project → Invitations
tab, the dark `project_invites` blueprint). Two gaps:

1. **No central pane** for an operator to see/edit *all* events across projects.
2. **No public surface** — a participant handed a code has nowhere to discover
   upcoming events and their registration links.

### Decisions (settled with the user)
- **`MANAGE_EVENTS` owns the event lifecycle everywhere** — the new central
  Admin → Events page *and* the existing per-project event create/edit/close/
  reopen (repointed off `MANAGE_ACCOUNT_REQUESTS`). Inviting people / rosters
  stay on `MANAGE_ACCOUNT_REQUESTS`.
- **Public discoverability is opt-in** — a new `listed` flag on the event; only
  listed events appear on the public card. Unlisted events stay reachable by
  direct link only.
- **Public card lives on the existing `/status/events`**, as the **top card,
  shown only when non-empty**; **rename the tab** "Events" → **"Calendar &
  Events"** (the page already shows PBS maintenance reservations + a calendar,
  so the single word is overloaded).

RBAC is **code-only** — no DB seed/migration (`src/webapp/utils/rbac.py`;
the `role`/`role_user` tables are never consulted by the web UI).

---

## Part A — `MANAGE_EVENTS` permission + backport

1. **Enum** — add `MANAGE_EVENTS = "manage_events"` next to
   `MANAGE_ACCOUNT_REQUESTS` in `rbac.py` (~:153). The `manage_` prefix fails
   closed (never swept into the `ALL_*` aggregates).
2. **Bundle** — add `Permission.MANAGE_EVENTS` explicitly to `_ALLOCATION_ADMIN`
   (`rbac.py:202-223`) → grants `nusd`/`csg`. `benkirk` auto-holds it (full-set
   override). Leave `ssg` / `USER_FACILITY_PERMISSIONS` out unless asked.
3. **Repoint the per-project event guards** — the event *lifecycle* only:
   - `src/webapp/dashboards/project_invites.py`: `_CREATE_GUARD` →
     `require_project_facility_permission(Permission.MANAGE_EVENTS)`; a new
     `_EVENT_MANAGE_GUARD = require_event_sponsor_access(Permission.MANAGE_EVENTS)`
     for `htmx_event_edit_form`/`update`/`close`/`reopen`. **Keep** `_GUARD`
     (tab + invite) and roster (`htmx_roster_*`) on `MANAGE_ACCOUNT_REQUESTS`
     (they create account requests, not events).
   - `src/webapp/utils/project_permissions.py`: `can_create_events` and
     `can_manage_events` → `MANAGE_EVENTS`.
4. **Tests** — `tests/unit/test_rbac.py`: add `'MANAGE_EVENTS'` to
   `TestPermissionEnumSurface.test_new_permission_member_exists`; add a
   `TestManageEventsGrants` class modeled on `TestAccountRequestGrants`
   (nusd/csg hold it, ssg doesn't, fails closed vs `ALL_*`). Update
   `test_project_permissions.py` / `test_project_invites_routes.py` /
   `test_access_control_decorators.py` where they assert the old permission on
   event routes.

## Part B — the `listed` flag (DDL)

Add `listed TINYINT(1) NOT NULL DEFAULT 0` to `account_request_event` (opt-in;
default off).
- **DDL**: add the column to `scripts/sql/create_account_request_event.sql`
  (fresh installs + the conftest bootstrap) **and** ship
  `scripts/sql/alter_account_request_event_listed.sql` (ALTER for the
  already-applied prod table). The CREATE verification counts
  (`utf8mb4_cols_expect_2`, `indexes_expect_3`, `fks_expect_0`) are unchanged —
  a plain TINYINT adds no utf8mb4 col / index / fk.
- **ORM** (`sam/core/account_requests.py`): add `listed = Column(Boolean,
  nullable=False, default=False)`; thread it through `create(...)` (default
  False) and `update(...)` (sentinel-gated like `instructions`).
- **Forms** (`sam/schemas/forms/account_requests.py`): add `listed = f.Bool(...)`
  to `AccountRequestEventForm` + `...EditForm`. Unchecked checkboxes send no key
  — inject `data['listed'] = 'listed' in request.form` in the route/handler
  (CLAUDE.md §9), not `load_default`.
- **Event form template** (`event_form_htmx.html`): a `checkbox_field('listed',
  'List on the public Upcoming Events page', ...)`.
- Schema validation picks up the new column once ORM+SQL converge; run it on
  MySQL and Postgres :5434.

## Part C — central Admin → Events page

Model it on the **Accounts** page (`admin/account_requests_routes.py` +
`account_requests.html` + the htmx fragment card).

- **New query** `all_events(session, *, facility_names=None)` in
  `sam/queries/account_requests.py` — every `AccountRequestEvent` with batched
  project-code, sponsor, and enrollee-count lookups (mirror `request_views`'s
  batching; reuse `enrollees_for_event` count or a grouped count). No
  cross-project listing exists today (only `_project_events` per project).
- **New module** `src/webapp/dashboards/admin/events_routes.py`, appended to the
  import line at `admin/blueprint.py:1268`. Routes, all
  `@login_required @require_permission(Permission.MANAGE_EVENTS)`:
  - `GET /admin/events` — page (extends `base_admin.html`, hidden filter form +
    htmx fragment target, like `account_requests.html`).
  - `GET /admin/events/fragment` — the list card: code + copy-link, name,
    project, deadline, window, **listed** badge, enrollee count, sponsor;
    filters (active/closed, upcoming/past, facility); row → edit modal;
    close/reopen buttons.
  - Event **create/edit/close/reopen** here (project-agnostic; create needs a
    **project picker** + the global `event_code` uniqueness check).
- **Reuse, don't duplicate:** the existing `htmx_event_update/close/reopen` are
  already global-by-code (`require_event_sponsor_access` resolves an unscoped
  `event_code`), but they live in the `project_invites` blueprint which is dark
  behind `ACCOUNT_INVITATIONS_ENABLED`. So **extract** the event lifecycle
  (create-with-uniqueness, the `_EventEditHandler` partial-update, the `_switch`
  close/reopen) into a shared helper module both blueprints call — the admin
  page must not depend on the invitations flag. Reuse the schemas
  (`AccountRequestEventForm`/`EditForm`) and model methods as-is; parameterize
  `event_form_htmx.html` (its project title `:11` and sponsor-search URL `:38`
  are the only project couplings) or give the admin page its own thin template.
- **Tab** — add `{'endpoint': 'admin_dashboard.events', 'label': 'Events',
  'icon': 'fa-solid fa-calendar-days', 'visible':
  has_permission(Permission.MANAGE_EVENTS)}` to the `page_tabs([...])` in
  `templates/dashboards/admin/base_admin.html:30`.
- **Nav** — add the item to the admin section in `webapp/utils/nav.py:204` with
  a `_can_manage_events` predicate (`has_permission(..., MANAGE_EVENTS)`).
- **Route-map parity** — regen `tests/unit/snapshots/dashboard_route_map.json`.

## Part D — public "Upcoming Events" card on `/status/events`

- **New query** `upcoming_listed_events(session, today=None)` in
  `sam/queries/account_requests.py` — `active AND listed AND` the `is_open_at`
  window replicated in SQL (`opens_at` null-or-≤now, `closes_at` null-or->now)
  `AND accounts_needed_by >= today`, ordered by `accounts_needed_by`.
  (`is_open_at` is a Python method, not a SQL expression — replicate the
  predicate.)
- **New fragment** `templates/dashboards/status/fragments/upcoming_events.html`
  — a `.card` per event (name, project, deadline, instructions) + the
  registration link `/register/<code>` via the shared `copy_button` (D19) and a
  visible "Register" link. Anonymous-safe.
- **Wire it as the TOP card** in `events_page.html`, before the reservations
  include, gated on the data (`{% if upcoming_events %}`). Add `upcoming_events`
  to `status/blueprint.py` `_page_context()` (or the `events()` route). Build the
  reg URL by hand (`request.url_root...`) — the register blueprint is unmounted
  in prod, so `url_for('register...')` would BuildError (same pattern already in
  `project_invites.invitations_fragment`).
- **Tiering** (the `can_* = is_authenticated and has_permission(...)` idiom):
  anonymous sees the card + register link; a **signed-in** viewer additionally
  gets the D17 self-enroll (the `/register/<code>` link already routes them to
  the one-click confirm) and an **"Enrolled" badge** for events they're in
  (reuse `events_for_user` / a set of their event ids). No new permission — the
  card is public.
- **Rename the tab** "Events" → **"Calendar & Events"** in
  `base_status.html:110-129` (label + the `events_label` count line) **and** the
  status section of `webapp/utils/nav.py:157`. Update the tab `visible`
  condition so it also shows when only `upcoming_events` exist.
- No caching on the status dashboard, so no cache-key work.

## Companion follow-ups (smaller, separable — recommend their own PR)

- **`copy_button` backports (D19):** a "copy link to this view" control on the
  routable/deep-linked dashboards (`page_tabs` pages, admin Notifications
  `?tab=`, XRAS/allocations deep-links), and copy on the XRAS operator
  identifiers pasted back into XRAS (`xras_table.html`, `xras_accounts_card.html`,
  `mnemonic_codes_table_htmx.html`). Pure reuse of the shared macro.
- **Queue loose-bucket split:** group event-less project invitations by project
  in the admin Accounts card (today they share one "Project Invitations"
  header). Touches `group_by_event` (shared with the digest) — scope carefully.
- **Registration go-live prereqs (not codeable now):** CAPTCHA + real-client-IP
  before `ACCOUNT_REGISTRATION_ENABLED=1` (external deps;
  `ACCOUNT_REGISTRATION.md` §6.1). Note only.

## Critical files

- RBAC: `src/webapp/utils/rbac.py`, `src/webapp/utils/project_permissions.py`,
  `src/webapp/api/access_control.py` (decorators, reused as-is).
- Model/DDL: `src/sam/core/account_requests.py`,
  `scripts/sql/create_account_request_event.sql` (+ new `alter_..._listed.sql`),
  `tests/conftest.py` `_BOOTSTRAP_TABLES` (already lists the event table).
- Queries: `src/sam/queries/account_requests.py` (`all_events`,
  `upcoming_listed_events`).
- Admin page: `src/webapp/dashboards/admin/events_routes.py` (new),
  `admin/blueprint.py:1268`, `templates/dashboards/admin/base_admin.html:30`,
  a new `templates/dashboards/admin/events.html` + fragment card.
- Shared event handler: extract from
  `src/webapp/dashboards/project_invites.py` (`htmx_event_create`,
  `_EventEditHandler`, `_switch`) into a shared module.
- Public card: `src/webapp/dashboards/status/blueprint.py` (`events`,
  `_page_context`), `templates/dashboards/status/events_page.html`,
  `.../fragments/upcoming_events.html` (new), `.../base_status.html`,
  `src/webapp/utils/nav.py` (status + admin sections).
- Schemas/templates: `src/sam/schemas/forms/account_requests.py`,
  `templates/project_members/fragments/event_form_htmx.html`.

## Tests

- RBAC enum + grants (Part A). Guard-repoint tests on the event routes.
- Model: `listed` create/update; `all_events` + `upcoming_listed_events` shape
  (extend `test_account_requests_queries.py`; factories already have
  `make_account_request_event` — add a `listed=` kwarg).
- Admin Events page: render smoke + create (uniqueness)/edit(partial)/close/
  reopen; route-map snapshot regen.
- Status card: renders when a listed open event exists and is hidden otherwise;
  anonymous vs signed-in tiers; tab renamed. Direct-render tests guard new
  context keys by truthiness (Jinja `Undefined`).
- Dashboard structural gates (modal-shell, collapse, action-nowrap, static,
  CSP, css-tokens) + schema validation on MySQL **and** Postgres :5434
  (`reference_postgres_index_table_namespace`).

## DDL / rollout (owner: @benkirk)

- Hand-apply `alter_account_request_event_listed.sql` to prod, then regenerate
  the obfuscated LFS blob via `make -C containers/sam-sql-dev everything-coherent`
  (as done for #575). The conftest bootstrap creates the column from the CREATE
  script, so CI is green without the blob; the blob still needs regen so the
  committed test DB carries the column.
- `MANAGE_EVENTS` is code-only — no seed. It reaches nusd/csg via the bundle.
- The admin Events page ships **live** (gated on `MANAGE_EVENTS`, held by
  operators); the public card ships **live but empty** until an event is marked
  `listed` and open. No feature flag needed — data-gated like My Events.

## Verification (end-to-end, on webdev)

- `docker compose up webdev --watch`; seed a `listed`, open event on a project.
- **Admin:** as an operator, `/admin/events` lists it across projects; create a
  second event (project picker + uniqueness), edit it (partial), close/reopen;
  confirm a non-operator (a lead without `MANAGE_EVENTS`) gets no tab and a 403
  on the routes.
- **Public:** log out → `/status/events` shows the "Upcoming Events" top card
  with the registration link (tab reads "Calendar & Events"); an unlisted event
  does **not** appear. Log in → the same card offers the self-enroll shortcut and
  badges events you're enrolled in.
- Playwright smoke both tiers; flush Redis between template edits
  (`docker exec samuel-cache redis-cli -n 0 FLUSHDB`).
- `pytest tests/unit/test_rbac.py tests/unit/test_account_requests_*.py
  tests/integration/test_schema_validation.py` then the full tier + route-map
  regen.

## Suggested PR split

1. **PR 1** — `MANAGE_EVENTS` + backport + the `listed` DDL + central Admin →
   Events page (Parts A–C). One coherent "manage events centrally" track.
2. **PR 2** — public Upcoming Events card + tab rename (Part D), on top of PR 1's
   query/flag.
3. **PR 3** — the `copy_button` backports (independent).

(Parts A–C could be one PR; D depends on B's `listed` flag and the reg-URL
helper. Keep the copy-backports separate per the one-track rule.)

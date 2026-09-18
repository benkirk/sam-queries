# PR 3 — deferred gaps from #575 / #576 (one PR vs `staging`)

**Status: implemented** on `events_followups`. The plan below is kept as written.

## As built -- deviations from the plan

| Plan said | Built | Why |
|---|---|---|
| Query-count assertions in `tests/perf/` with `baselines.json` entries | Always-on statement assertions in `tests/integration/test_status_dashboard.py` | The perf harness counts on `db.engine` (the SAM bind); status reads go to the `system_status` bind. "No child-table or lookup SELECT" is exact, needs no headroom, and runs in CI |
| Clamp only | Also fixed an anonymous 500: `get_user_permissions` read `user.roles` on `AnonymousUserMixin`, so `/status/queue-history/...` never rendered signed-out | Found by the new anonymous clamp tests; it fails closed now |
| Copy button on the Accounts card username cell | Skipped | That card's rows carry no glyphs by design (`e2e/test_xras_accounts_card.py::test_the_row_icons_are_gone`); the person detail in its expansion has name and email buttons |
| `compact=true` in cells | New `inline=true` variant (borderless, beside the value) | A bordered `btn-sm` in a dense cell grows the row |
| Card chevron via `collapse_toggle` | Chevron cell **and** the Enrolled count toggle; the code cell cannot (it holds a copy button) | Capture-phase data-api |
| Roster button always | Drawn for a `MANAGE_ACCOUNT_REQUESTS` holder on an active event whose project is active | A roster on a retired project queues requests nobody can fulfill |

## Prod posture option (Ben's switch, not flipped here)

`ACCOUNT_REGISTRATION_ENABLED=1` with `ACCOUNT_REGISTRATION_LOGIN_REQUIRED=1`
lights the self-enroll shortcut, the public Upcoming Events card and the enrolled
counts with **no anonymous mailer**. Caveat: the signed-in creation form can
still mail a third party (one verification message, globally capped per hour).

## Still blocked outside this repo (note only)

- CAPTCHA on the anonymous form.
- Client IP at the ingress: until it arrives, `RATELIMIT_ANON` is one global
  bucket in prod, and `/status/*` rides the default tier.
- The `account_requests_reconcile` / `account_queue_digest` switches in
  `SAM_TASKS_DISABLED`.
- Phase 3 of `docs/plans/implemented/ACCOUNT_REGISTRATION.md`.
- "Copy link to this view": re-file as *URL-complete filter state* -- only two
  pages keep their filters in the URL; the htmx cards hold them in hidden forms.
- `account_request_event.modified_by`: a prod ALTER; the lifecycle logs the actor
  until then.

---

## Context

#575 (account registration) and #576 (events views) each deferred work. A census
of both plan docs plus the code (three read-only explorers, key claims
re-verified by hand) sorted it into codeable-now medium/high gaps vs. externally
blocked items. Ben chose four tracks for one small PR. Branch from
`origin/staging` **after #576 merges** (it is green). No DDL, no blob regen, no
new routes beyond two htmx fragments.

**The finding that reshaped "status caching":** `webapp/audit/events.py:192-208`
registers `after_commit → caching.clear('flask')` on *every* Session, not
bind-filtered. Collectors commit ≥3× per 5 min, so any `@cache.memoize` on a
status page lives well under 100 s, and hand-written `delete_memoized` calls are
redundant. The status pages' real costs are query-side. So Track 1 adds **no
cache**.

## State at handoff (2026-09-18) — read this first after compaction

- **#576 is MERGED to `staging`** (all 12 checks green; prod ALTER for
  `account_request_event.listed` already applied by Ben — output verified).
  Still Ben's: `make -C containers/sam-sql-dev everything-coherent` later.
- **Nothing of PR 3 is written yet.** First steps: `git fetch origin`;
  `git switch -c events_followups origin/staging` (never from local branches);
  copy this plan to `docs/plans/EVENTS_FOLLOWUPS.md` as the first commit
  (house rule: handoffs live in `docs/plans/`, which is gate-exempt).
- Local env: `source etc/config_env.sh`;
  `export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'`;
  `make pytest-pg` for 5434 (both already carry `listed`). `webdev` is up on
  :5050 and dev DB 3306 has the column; quick-login `tfair` = NUSD operator.
  Flush fragments: `docker exec samuel-cache redis-cli -n 0 FLUSHDB`.
- Load the `wire-dashboard-feature` skill before touching templates.
- Lessons already paid for in #576 — do not relearn:
  1. A GET rule with no `<converter>` and no `/htmx/` segment is swept as a
     **page** by `e2e/conftest.py` (or name the endpoint `*_fragment`).
  2. A test that **commits** a row needs a per-test unique key (uuid) — xdist
     workers share one DB — and must delete it in teardown.
  3. `test_docs.py::test_cited_paths_exist` reads `git ls-files`: `git add -N`
     new files before running it.
  4. `gh pr checks` right after a push shows the *previous* run.
  5. PR body/commits must not contain skip-ci tokens.
- Files #576 created that this PR builds on:
  `src/webapp/dashboards/event_lifecycle.py`,
  `src/webapp/dashboards/admin/events_routes.py`,
  `templates/dashboards/admin/{events.html,fragments/events_card.html}`,
  `templates/dashboards/status/fragments/upcoming_events.html`,
  `tests/unit/{test_admin_events_routes,test_status_upcoming_events}.py`.
  Record: `docs/plans/implemented/EVENTS_VIEWS.md`.
- Suggested commit order: Track 1 → 3 → 4 → 2 (smallest blast radius first;
  Track 2 is the largest). One PR, `--base staging`.

## Track 1 — Status hardening (High)

`src/webapp/dashboards/status/blueprint.py`, `src/system_status/queries/__init__.py`

1. **Clamp + guard `hours`.** `nodetype_history` / `partition_history` /
   `queue_history` (`:295`, `:348`, `:418`) each do an unguarded
   `int(request.args['hours'])` with no bound, anonymously: `?hours=abc` → 500;
   `?hours=999999` → full-table scan + render. Replace the three inline parsers
   with one helper built on `_parse_selected_hours` (`:29`): `ValueError` → the
   168 default, clamp to `[1, 720]` (the picker's own max,
   `fragments/time_range_picker.html:24`). `_page_context` uses the same helper.
2. **Stop the `selectin` fan-out on read paths.** `DerechoStatus` /
   `CasperStatus` declare 4–5 `lazy='selectin'` collections
   (`models/derecho.py:28-50`, `models/casper.py:40-67`) that no status template
   reads; `get_system_partition_history` drags them for every snapshot in the
   window to read 7 scalars. **Leave the mapping alone** (ingest relies on it:
   `api/v1/status.py:199`, `user_proj_queue_ingest.py:81`) — add
   `.options(lazyload('*'))` (or a column `select`) in
   `get_latest_derecho_status`, `get_latest_casper_status`,
   `get_system_partition_history`, `get_latest_system_partition_status`.
3. **`*_name` N+1.** `get_latest_*_queues/filesystems/login_nodes`,
   `get_active_outages`, `get_upcoming_reservations` already JOIN the lookup
   table but discard it; the `@property` names then lazy-load per row (~25
   round trips per landing page). Add `contains_eager` (joinedload for
   outages/reservations → `system`).
4. Tests: clamp/garbage cases on all three routes (anonymous `client`);
   a query-count assertion per landing page + partition-history in
   `tests/perf/` with `baselines.json` entries (perf-tier memory: measured
   baselines, name every consumer). Existing `test_status_dashboard.py` must
   pass unchanged.

## Track 2 — Events operability (High)

1. **Enrollees + roster on Admin → Events.** Both live only in the prod-dark
   `project_invites`. Move `_RosterHandler` + result rendering into
   `webapp/dashboards/event_lifecycle.py` (same extraction as #576); admin gets
   `GET /admin/htmx/events/<code>/enrollees` (on-demand fragment — never
   `enrollees_for_event` per list row), `…/roster-form`, `POST …/roster`.
   Roster creates account requests → guard these two on
   `MANAGE_ACCOUNT_REQUESTS` (and draw the button only for holders), enrollees
   on `MANAGE_EVENTS`. Card: a chevron cell via `collapse.collapse_toggle`
   (non-link `<td>`), body loads the fragment; keep `/htmx/` in every new rule
   (the e2e sweep lesson from #576).
2. **Inactive-project guards.** `create_event` → `FormError` unless
   `project.is_active`; `upcoming_listed_events` joins on `Project.is_active`;
   `all_events` returns `project_active`, card shows a warning badge.
3. **Facility scoping** (handoff Part C, dropped in #576 and unrecorded):
   `all_events(facility_names=)` via `user_facility_scope` /
   `filter_rows_by_facility` (`rbac.py:374,430`); routes →
   `require_permission_any_facility`; per-event writes check
   `has_permission_for_facility` on the event's project. Add the as-built row
   to `EVENTS_VIEWS.md`. (No facility-scoped user holds `MANAGE_EVENTS` today —
   this closes the door before one does.)
4. **`listed` presence sentinel.** Hidden `listed_present=1` drawn with the
   checkbox; `EventEditHandler` sets `listed` only when it is present — the
   `extra_sponsor_user_id` idiom. Route-level regression test.
5. **Attribution.** `event_lifecycle.py` has no logger: one structured line per
   create / edit (incl. `listed` old→new) / close / reopen with the acting
   username. (`modified_by` column = prod ALTER, out of scope.)
6. **Real-cache test.** Tests run NullCache and patch the memoized function, so
   `memoize`/`delete_memoized` never execute. A SimpleCache-backed app fixture:
   second call hits, invalidation misses. Also note in `EVENTS_VIEWS.md` that
   the commit hook makes `invalidate_upcoming_events` belt-and-braces (kept: a
   non-web writer would not flush).
7. Route test: a closed / past-`closes_at` code renders `register/refused.html`.

## Track 3 — Copy buttons (Medium)

Reuse `fragments/clipboard.html:copy_button` (JS already global). Trap-free
placements only, `compact=true` in cells:
`xras_table.html:98` request # · `fragments/xras_person_detail.html:40` email +
name · `xras_accounts_card.html:268` username cell · 
`mnemonic_codes_table_htmx.html:60` · `xras_notify_manual_fallback.html:55` ·
`scheduled_tasks_log.html:100` runner_id.

**Gates (the important part):** add `copy_button\(` to `_CELL_ACTION` in
`tests/unit/test_collapse_trigger_rows.py:87` and `_ACTION` in
`test_action_cells_nowrap.py:24` — a macro call is invisible to both today, so a
trapped button would pass CI. Add a clipboard specimen to `/dev/gallery`.

**Dropped:** "copy link to this view" — only two pages have URL-complete state;
the htmx card pages keep filters in hidden forms (one `hx-push-url` in the whole
tree). Re-file as "URL-complete filter state" in the doc. Do **not** touch
`project_link`, the activity-card projcode cell, or the Accounts name/email
cells (collapse-toggle cells; no button-side fix).

## Track 4 — Loose-bucket split (Medium)

`group_by_event` (`sam/queries/account_requests.py`): emit one `event=None`
group **per project** (then one "Other requests" group for project-less rows)
instead of a single loose bucket. Digest (`account_notices.py:61-72`) is
transparent — it only keys on truthy `event`. Card
(`account_requests_card.html:123,137`): per-group collapse id instead of the
hardcoded `'loose'`, label from the group. Update
`test_account_requests_queries.py::TestGrouping` + a digest-unchanged assertion.

## Docs

`docs/plans/implemented/EVENTS_VIEWS.md`: replace the "caching gaps" list with
what was found (commit-flush hook; query fixes done; CSRF-in-page-cache =
latent, non-exploitable today — `/allocations/projects` has no state-changing
request; suggest a session component in `user_aware_cache_key` if one is ever
added). New short § **Prod posture option**: `ACCOUNT_REGISTRATION_ENABLED=1` +
`LOGIN_REQUIRED=1` lights self-enroll / card / counts with no anonymous mailer;
caveat = signed-in creation form can mail a third party (10/hr global); Ben's
switch. List the externally blocked items (CAPTCHA, client IP — which also makes
`RATELIMIT_ANON` one global bucket in prod, `SAM_TASKS_DISABLED` switches,
phase 3) as note-only. Update memory `reference_webapp_cache_landscape`.

## Verification

- `pytest` on 3307 and `make pytest-pg`; `pytest -m perf -n 0` for the new
  baselines; structural gates incl. the two widened regexes; route-map regen.
- webdev as `tfair`: `/admin/events` expand → enrollees; paste a roster; create
  on an inactive project refused; edit without touching `listed` keeps it.
  Anonymous curl: `?hours=abc` → 200, `?hours=999999` → clamped; compare
  query counts on `/status/derecho` before/after via the request db-timing log.
- Copy buttons: click each, confirm toast and that no row toggles; gallery page.
- Watch the e2e sweep on the PR (new rules all under `/htmx/`).

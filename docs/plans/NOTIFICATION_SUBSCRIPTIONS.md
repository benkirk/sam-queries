# Notification subscriptions

**Status: design, not started. Every phase waits for an explicit go-ahead.**

Project members choose the mail they get about their projects: a monthly
usage digest, and an alert when an allocation crosses a percentage they
pick. Leads and admins can subscribe to a whole project subtree. The table
is generic on purpose. Outage notices and routine announcements are the
next consumers, and they need no schema change (§ 7).

This is the use case `implemented/NOTIFICATION_FRAMEWORK.md` § 6 waited for. Its rule
holds: **"no row" is never the policy; the kind is.** Both new kinds are
`default_subscribed=False`, so a row means "I opted in". A row with
`subscribed=0` is an opt-out from a default-on kind. No current kind needs
one, but the column costs nothing now and an ALTER later.

## 1. Pieces reused

| Piece | Where | Role here |
|---|---|---|
| Mailer | `sam.notify` (`Notifier`, `Message`, ledger) | Rendering, addressing, dedup, the `notification_log` record |
| Kinds and families | `src/sam/notify/kinds.py` | New `usage` family, two kinds |
| Templates and overrides | `src/sam/notify/templates/`, `src/sam/notify/samples.py` | Editable in Admin → Notifications → Templates |
| Usage read model | `account_allocation_state`, `fresh_state()` in `src/sam/queries/allocation_state.py` | `percent_used` per allocation, refreshed hourly |
| Project tree | `NestedSetMixin` in `src/sam/base.py` | Subtree = same `tree_root`, left/right inside the anchor's |
| Membership | `Project.member_ids_by_project` | Recipient check at send time |
| Scheduler | `src/scheduling/` (`MonthlyDay`, `Daily`, `@task`) | Two new tasks |
| Mail guards | `src/scheduling/tasks/_notice_common.py` | `NOTIFY_ENABLED` and audience-cap checks |

The table goes in **`sam`**, not `system_status`. Subscriptions point at users,
projects and resources, and the read model they join to is in `sam`.
`system_status` is UTC telemetry on another server, so no join could reach it.

## 2. The table

**Why a new table.** Subscriptions need DDL. The alternatives fall short:

| Alternative | Why not |
|---|---|
| A preferences column on `users` | alters a legacy table, and cannot be queried by kind or project |
| One table per kind | every outage or announcement kind would need its own DDL ticket |
| `notification_addressing` | holds deployment-wide cc/bcc per family, not per-user choices |

One generic table with typed columns serves every kind.
`implemented/NOTIFICATION_FRAMEWORK.md` § 6 cut a dormant subscription table because it had no consumer to test its
shape. The two `usage` kinds are that consumer.

Applied by hand from `create_notification_subscription.sql` in `scripts/sql/`,
following the conventions of `scripts/sql/create_notification_addressing.sql`:

- `NO FOREIGN KEYS` (the app-owned-table convention).
- utf8mb3.
- Times from the app clock, naive Mountain.
- Verification SELECTs at the end.

| Column | Type | Meaning |
|---|---|---|
| `notification_subscription_id` | INT PK AI | |
| `user_id` | INT NOT NULL | the subscriber |
| `kind` | VARCHAR(32) NOT NULL | a `NOTIFICATION_KINDS` key |
| `scope` | VARCHAR(16) NOT NULL | `project` \| `resource` \| `facility` \| `global` |
| `project_id` | INT NULL | set when scope = `project` |
| `resource_id` | INT NULL | set when scope = `resource`, or to narrow a project scope to one resource |
| `facility_id` | INT NULL | set when scope = `facility` |
| `include_subtree` | TINYINT(1) NOT NULL DEFAULT 0 | project scope only |
| `threshold_pct` | SMALLINT NULL | threshold kinds only, one of 50/75/90/100 |
| `subscribed` | TINYINT(1) NOT NULL DEFAULT 1 | 0 = opt-out of a default-on kind |
| `created_by` | VARCHAR(35) NOT NULL | username; differs from the subscriber when staff act for them |
| `creation_time`, `modified_time` | DATETIME | |

- Indexes: `(user_id)` and `(kind, scope)`.
- **No SQL unique key.** MySQL treats NULLs as distinct, so a unique key over
  the nullable scope columns would admit duplicates. `NotificationSubscription.create()`
  enforces one row per (user, kind, scope target, threshold), and a test pins it.
- ORM: `NotificationSubscription(Base, SessionMixin)` in `sam.notify`, exported
  from `sam/__init__.py` **without** an eager import (`test_notify_import_graph.py`).
- Registered in `_BOOTSTRAP_TABLES` (`tests/conftest.py`) and pinned in
  `tests/integration/test_schema_validation.py`.
- Then `make regen-lfs-blob`.
- Added to the anonymizer's purge list, because a row links a person to projects.

## 3. Who may subscribe to what

Each kind declares the scopes it accepts in a new `NotificationKind.scopes`
field. The default is `()`, meaning not subscribable. Both `usage` kinds
declare `('project',)`. `NotificationSubscription.create()` refuses a scope
its kind does not declare. Until a later kind declares `resource`, `facility`
or `global` with its own rules (§ 7), no row can use those columns.

For the project scope, `can_subscribe(user, project, include_subtree)` goes in
`src/webapp/utils/project_permissions.py`:

| Request | Allowed when |
|---|---|
| A project, no subtree | the user is a member (an unended row, or lead or admin) |
| A project with `include_subtree` | the user is lead or admin of that project or an ancestor (`include_ancestors` semantics) |
| For another user | `EDIT_PROJECT_MEMBERS` (staff) |

A plain member cannot subscribe to a subtree. This is deliberate: a subtree
covers projects the member may not belong to.

Access is **re-checked at send time**. A subscriber who has left the project,
lost the lead or admin role, or is not `User.is_active` is skipped and counted
in the run's `detail`. The row is kept, so access that comes back resumes the mail.

## 4. Kinds and messages

A new family, `usage` ("Allocation usage notices", `about_project=True`), holds
two kinds. Both are `default_subscribed=False` and `facility_aware=False`.
Mail goes to `user.primary_email`.

| Kind | One message per | Dedup key |
|---|---|---|
| `usage_digest` | subscriber per month, covering every project in scope | `usage_digest:<user_id>:<YYYY-MM>` |
| `usage_threshold` | allocation, level and address | `usage_threshold:<allocation_id>:<pct>:<address>` |

- Builders live in `sam.queries.usage_notices`, on the `expiration_notices` pattern.
  They return `Message`s and feed both the task and the template editor's
  "Preview for".
- **The subtree is resolved at send time** from the anchor's nested-set
  coordinates. The row stores only the anchor, so a child project added later
  is included and a moved one drops out without a write.
- **Shared pools.** For an inheriting allocation, `percent_used` is the whole
  pool's, which is what runs out, so alert on it. A subtree subscriber sees one
  pool through N children, so the builder collapses rows by
  `parent_allocation_id` before it builds messages.
- **A new allocation re-arms the alert**, because its `allocation_id` is new.
  A supplement that drops usage back below a level does **not** re-arm it
  (open question 1).
- Both templates end with a link to the user dashboard's Notifications tab.
  There is no token unsubscribe; login is required.

## 5. Scheduled tasks

| Task | Schedule | Notes |
|---|---|---|
| `usage_digest` | `MonthlyDay(1, 8, 0)` Mountain | summary mail to `SAM_TASKS_SUMMARY_TO`; cap `SAM_TASKS_EMAIL_MAX` |
| `usage_threshold` | `Daily` 07:30 Mountain | no summary mail (the `xras_notices` precedent); same cap |

Both declare `needs=('sam',)`, raise when `NOTIFY_ENABLED` is false, and put
the selected, suppressed, skipped and audience counts in `detail`.

- **Pre-filter is required on `usage_threshold`.** An allocation stays over its
  level for weeks. Without `already_sent_many` filtering before `send_many`,
  every daily run writes a `suppressed` row per such allocation into the table
  the admin card reads. This is the `expiration_notices` trap.
- **The read model must be fresh.** Use `fresh_state()`. When it falls back,
  the task fails rather than alerting from stale numbers. The next day's run
  catches up, and dedup makes any cadence safe.
- **Both are named in `SAM_TASKS_DISABLED` in `helm/values.yaml` in the same
  change** (fail-open), with a test that greps for each name. Dev enables them
  with mail off.

## 6. UI

The user dashboard gets a **Notifications** tab beside `/info`. It lists the
user's subscriptions and lets them add or remove one:

- project select, limited to the user's projects;
- the subtree checkbox, shown only where the user is lead or admin;
- the kind;
- the threshold.

It follows the `wire-dashboard-feature` skill (htmx display/form fragments).
Writes run inside `management_transaction` through `HtmxFormHandler`, and a
form schema goes in `sam/schemas/forms/`.

Admin → Notifications gets a **Subscriptions** count card
(`VIEW_SYSTEM_CONFIG`) and a list view (`SYSTEM_ADMIN`, because it shows addresses).

Route-map snapshot regenerated (`ROUTE_MAP_REGEN=1`).

## 7. The exemplar: outages and announcements (not in scope)

| Future kind | Scope | Trigger |
|---|---|---|
| `outage_notice` | `resource` (Derecho) or `global` | a `SystemOutage` created or changed in `system_status`, polled by a task keyed on outage id and status |
| `announcement` | `facility` or `global` | an operator-composed message, previewed then sent |

Neither needs DDL. Each needs a kind, a template, a builder and a trigger.
Each also declares its `scopes` and brings the subscribe and send-time rules
for them, which § 3 does not cover. The proposed rules, settled in that kind's plan:

| Scope | May subscribe | Skipped at send time when |
|---|---|---|
| `resource`, `global` | any `User.is_active` user, since outage news is public | the user is not `User.is_active` |
| `facility` | a member of a live project in that facility, or staff with `EDIT_PROJECT_MEMBERS` acting for them | the user is not `User.is_active` or has no live project there (open question 4) |
| For another user | staff with `EDIT_PROJECT_MEMBERS`, as in § 3 | |

Like § 3, a skipped row is kept and counted in the run's `detail`.
`implemented/EXPIRATION_NOTICES.md` already sized the send path for an outage audience of 2000+.

## 8. Phases

| Phase | Delivers | Gate |
|---|---|---|
| 0 | DDL file, ORM, `create()`/`update()`, `NotificationKind.scopes`, bootstrap, schema pin, factory, unit tests | go-ahead; then an operator applies the DDL in prod |
| 1 | `usage` family and kinds, templates, samples, builders, preview | go-ahead |
| 2 | Both tasks, helm disable-list entries, task tests | go-ahead |
| 3 | User Notifications tab, admin card, browser smoke | go-ahead |
| 4 | Dev run with `NOTIFY_REDIRECT_TO`, then take the prod names off the disable list | go-ahead per environment |

## 9. Verification

- Unit tests:
  - model: one-row rule, scope validation, a scope the kind does not declare refused;
  - `can_subscribe`: member, ancestor lead, plain member denied a subtree;
  - builders: subtree expansion, pool collapse, dedup keys, lapsed member skipped;
  - tasks: pre-filter, `NOTIFY_ENABLED` guard, cap, stale read model.
- Gates: `test_schema_validation.py`, `test_notify_import_graph.py`,
  `test_layer_imports.py`, route-map parity, `test_docs.py`,
  `helm/tests/test-cronjob-render.sh`.
- Replay with `sam-admin tasks --run usage_threshold --force --dry-run`.
- `e2e/` browser smoke on the Notifications tab.

## 10. Open questions

1. Should a supplement re-arm a threshold alert, for example with the
   allocation amount in the dedup key?
2. Should the digest go out on the 1st, or after the month's charges settle
   (for example on the 3rd, alongside `deactivate_expired`)?
3. Should a project with no subscribers show leads a one-time "you can subscribe"
   hint on the dashboard?
4. Should a facility subscription follow the user's projects, so it lapses
   when their last live project there ends? Or should any active user be able
   to follow a facility's announcements?
5. Alerts fire per allocation or shared pool. Should a subtree subscriber also
   get an alert when the subtree's combined use crosses a level, when its
   children hold separate allocations?

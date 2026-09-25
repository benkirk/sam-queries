# `/database` — the read-only row browser

**Status:** built (branch `db-browser`). Replaces Flask-Admin.

## Why

Flask-Admin was hand-built early, and by 2026-09 it had these problems:

- **Local only.** `FLASK_ADMIN_ENABLED` was off in helm.
- **SAM only.** It never showed system_status, job_history or fs_scans.
- **Slow.** Three causes:
  - relationship dropdowns loaded whole tables (no `form_ajax_refs`)
  - list pages hit `lazy='selectin'` cascades (User → AccountUser → Account → Project)
  - every page ran `COUNT(*)`, including on the 1M-row charge tables
- **Weak gating.** It only checked `is_authenticated`, and `ApiCredentials` was browsable.
- **Unsafe writes.** Its create/edit forms bypassed every domain invariant (allocation replay, membership, audit).

What was actually needed was a fast, comprehensive, **read-only** view of table
rows for low-level diagnosis, available in production.

## Decisions (2026-09-24)

| | |
|---|---|
| Where | Every environment, prod included, behind `Permission.ADMIN_DATABASE` (csg bundle + full-admin override). The `admin_` prefix means no `ALL_*` aggregate sweeps it in. `DB_BROWSER_ENABLED=0` unmounts it. |
| Writes | None. Fixes go through the dashboards and the CLI, where invariants and audit live. |
| SQL console | No. Structured filters only: nothing to parse, sandbox or leak through. |
| Build | In-house, on reflection, rather than a sidecar (pgweb/Adminer): SAM auth, every engine, links into SAM pages. |

## Shape

```
src/dbbrowse/          SQLAlchemy only (gate: tests/unit/gates/test_dbbrowse_import_graph.py)
  readonly.py          read_only_connection: the only way a query runs
  catalog.py           names + kinds + row estimates, reflection, FK graph, MetadataCache
  overlay.py           ORM knowledge (class names, view PKs, ORM-only FKs) from a registry argument
  filters.py query.py  (column, op, value) -> bound WHERE; keyset / capped-OFFSET paging
  redact.py cells.py   what is never selected; how a value displays
src/webapp/utils/engine_inventory.py   the engine list (also feeds the Configuration card and
                                       /api/v1/health/db-pool)
src/webapp/db_browser/                 blueprint: gate + access log, sources, URL state, routes
  sources.py           BrowseSource (an EngineSource that can connect and reflect, cached per process)
                       and BrowsedTable (one table as this request's user may see it: pk, hidden,
                       sortable, FK links); every table route resolves one and reads from it
  params.py            ViewState: the query string as a value (from_args / to_args / matches)
  routes.py            views; one errorhandler turns any database failure into a 503 page or a
                       fragment message, so only the table page's own query is caught inline
```

### Safety, per dialect (`readonly.py`)

| dialect | setup | exit |
|---|---|---|
| Postgres | `SET TRANSACTION READ ONLY`, `set_config('statement_timeout', ms, true)`, then **verify** `SHOW transaction_read_only = on`. An AUTOCOMMIT engine only warns on `SET TRANSACTION`, so without the check it would silently not be read-only. | rollback clears both |
| MySQL | session `max_execution_time` (saved, restored); `SET TRANSACTION READ ONLY` for the next transaction. It raises 1568 rather than implicitly committing if one is open. | rollback, restore |
| MariaDB | as MySQL, with `max_statement_time` in seconds | rollback, restore |
| SQLite | `PRAGMA query_only = ON` | `OFF` |
| other | `UnsupportedDialect` | |

It always calls `engine.connect()` and never uses `db.session`, so the audit hooks never see it. If a restore fails, the connection is invalidated rather than returned to the pool in an unknown state.

### Performance

- **No COUNT on the page path.** The header shows the catalog estimate (MySQL `TABLE_ROWS`, which can be up to 24 h stale; PG `reltuples`, where `-1` means never analyzed). The exact count is a button.
- **Paging** is keyset (`pk > :after`) on a single-column PK when no user sort is set, and has no depth limit. Otherwise it is OFFSET, capped at 10,000 rows.
- **Large columns** are cut server-side (`substr(col, 1, N+1)`; binary columns come back as `length()`). The row page fetches 4,000 chars, and "load full value" fetches up to 1M.
- **Metadata** (catalog, reflected tables, FK graph) is cached per process with a 15 min TTL and an RLock. The gunicorn gthread workers share the process. "Refresh metadata" clears it for this worker.
- **Reflection** uses `resolve_fks=False`, and the MySQL FK graph is one `KEY_COLUMN_USAGE` query.
- **Measured locally:** a warm table page runs exactly one page SELECT, and `test_warm_page_runs_one_select_and_no_count` pins that. Pages took 0.06–0.38 s on webdev, with the 506k-row `comp_charge_summary` sorted by date the slowest.

### Top values

The chart icon on a sortable column header runs `dbbrowse.top_values`: a
GROUP BY under the current filters, the 20 most frequent values, NULL included,
under the same timeout. Each value links to the table filtered on it. It is
not offered on long, JSON, binary or redacted columns.

### Redaction

Name-token rules (`password`, `secret`, `salt`, `*_hash`, `*_token`,
`*api_key`, ...) plus explicit `(table, column)` entries. `xras_action_log.raw_payload`
is also hidden from anyone without `MANAGE_XRAS`.

A redacted column is never selected, filtered or sorted. Otherwise a `LIKE`
filter or a sort order would leak it one character at a time.

`test_every_secret_shaped_sam_column_is_decided` sweeps the reflected schema.
It fails on any column that looks like a secret and is neither redacted nor listed as safe.

### URL = state

Filters are `f<i>.col` / `f<i>.op` / `f<i>.v` (up to 10). The other parameters are
`sort_by`, `sort_dir`, `page` or `after`, `per_page` and `cols`.

The pages use plain GET forms, and the route redirects to the canonical URL, which
drops empty filter rows and defaults. So any view can be pasted into a ticket.
htmx is used only for the count, cell-expand and table-filter fragments, so the
browser needs no new JS.

## Deliberately not done

- **Writes and a SQL console** (decisions above).
- **Cluster-wide cache refresh.** Only the current worker is cleared; the TTL bounds staleness on the others. The metadata cache is not on the Admin Caching card and `sam-admin cache --refresh` does not reach it, so after a DDL change other workers may reflect the old columns for up to 15 min (a dropped column then shows as a query error, not a wrong value).
- **An ORM overlay for the plugin databases.** Their reflected PKs and FKs are enough.
- **A `sam-admin db` CLI.** `dbbrowse` is Flask-free so one can be added.

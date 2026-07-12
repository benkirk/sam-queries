# Global cache refresh API

> **Status: implemented** on branch `cache_mgmt` (PR → `staging`). This doc
> is the original design; the shipped work refined it in three ways:
> 1. **Five** bypass routes were consolidated, not three — PR #346 added
>    `queue.py` and `wallclock_exemption.py` after this doc was written.
> 2. Added an **Admin UX**: a `SYSTEM_ADMIN`-gated "Clear…" dropdown on the
>    Configuration tab's Caching card (`POST /admin/htmx/cache/clear`,
>    re-rendering the extracted `caching_card_body.html` fragment).
> 3. The CLI reuses the existing `SAM_API_USER` / `SAM_API_PASS` /
>    `SAM_API_BASE` env vars (from the systems-integration client), not a new
>    `SAM_WEBAPP_URL`. It stays a pure HTTP client (no webapp import).

## Context

SAM has several per-resource `/refresh` endpoints (`project_access`, `fstree_access`,
`directory_access`) but no single "refresh everything" entry point. Investigation shows
the caching layer is already well-centralized behind one facade
(`webapp/caching/__init__.py::Caching`, singleton `caching`) — every cache adapter
(Flask-Caching, chart SVG caches, sam-package allocation-usage cache, fs-scans cache)
implements the shared `CacheBase` contract and `caching.clear(category=None)` **already**
clears everything and returns a `{category: count_cleared}` dict. It just has zero HTTP
call sites today. Two things bypass this facade and call the deprecated `cache` alias's
blunt `.clear()` directly instead: the 3 existing `/refresh` routes, and an `after_commit`
SQLAlchemy hook in `webapp/audit/events.py` that fires on every DB write.

Goal: add a real "global refresh" HTTP endpoint on top of the existing facade, consolidate
the existing bypass call sites onto it, and add CLI parity — with minimal new surface area
since the hard part (central registry) is already built.

## Decisions from discussion

- Also fix the 3 existing per-resource routes + the audit hook to go through
  `caching.clear()` instead of their current uncoordinated `cache.clear()` calls.
- The new endpoint supports an optional `?category=` query param (`flask|chart|usage|scans`),
  passed straight through to `caching.clear(category)`.
- Add a `sam-admin cache --refresh` CLI command in addition to the HTTP endpoint.

## Implementation

### 1. New admin API blueprint

`src/webapp/api/v1/admin.py` (new file), modeled on `health.py`'s admin-only pattern:

```python
from flask import Blueprint, jsonify, request
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import csrf
from webapp.api.helpers import register_error_handlers
from webapp.caching import caching

bp = Blueprint('api_admin', __name__)
register_error_handlers(bp)

_VALID_CATEGORIES = {'flask', 'chart', 'usage', 'scans'}

@bp.route('/cache/refresh', methods=['POST'])
@csrf.exempt
@login_or_token_required(Permission.SYSTEM_ADMIN)
def refresh_cache():
    category = request.args.get('category')
    if category is not None and category not in _VALID_CATEGORIES:
        abort(400, f"Invalid category {category!r}; must be one of {sorted(_VALID_CATEGORIES)}")
    result = caching.clear(category)
    return jsonify({'status': 'ok', 'cleared': result})
```

Register in `src/webapp/run.py` alongside the other `api/v1` blueprints (import near
line 29-37, `register_blueprint(..., url_prefix='/api/v1/admin')` near line 381-389).

Gate on `Permission.SYSTEM_ADMIN` (the `db-pool` endpoint precedent in `health.py:108`),
not the view-level permissions the 3 existing routes use — this clears strictly more than
any one of them, so it needs the stronger permission.

### 2. Consolidate existing bypass call sites onto the facade

Same behavior, just routed through `caching.clear('flask')` instead of the deprecated
`cache` alias's `.clear()` — scope stays `'flask'` only in each case, preserving current
behavior (none of these should start also wiping chart/usage/scans caches):

- `src/webapp/api/v1/project_access.py:105` — `cache.clear()` → `caching.clear('flask')`
- `src/webapp/api/v1/fstree_access.py:248` — same
- `src/webapp/api/v1/directory_access.py:~99` — same
- `src/webapp/audit/events.py:203` (`_flush_view_cache`, fires on every `after_commit`) —
  same swap; keep the `RuntimeError` guard for no-app-context (CLI/test teardown) as-is

Each of these files already imports `cache` from `webapp.extensions`; add
`from webapp.caching import caching` and drop the now-unused `cache` import if nothing
else in the file still needs it (check `delete_memoized` calls in the 3 route files —
those still use `cache`, so keep that import there; `events.py` can drop it entirely).

### 3. CLI parity — `sam-admin cache --refresh`

Architectural note baked into this design: the cache facade lives inside the running Flask
worker process. `sam-admin` is a separate process that only opens a direct DB session
(`cli/cmds/admin.py`) — it has no Flask app context and, critically, the in-process cache
fallback is explicitly load-bearing (works with no Redis configured), so a CLI process
cannot invalidate another process's in-memory caches by importing `webapp.caching` locally.
The CLI command must instead be a thin HTTP client hitting the real running webapp's new
endpoint — same shape as the M2M collector auth path already built for `API_KEYS_*`
Basic-auth (`webapp/config.py:21-27`, `webapp/utils/api_auth.py`).

- New command in `src/cli/cmds/admin.py`, following the existing `@cli.command()` pattern:
  `sam-admin cache --refresh [--category flask|chart|usage|scans]`
- Needs a base URL and Basic-auth credentials — introduce `SAM_WEBAPP_URL` (new env var,
  no existing equivalent found) plus reuse the `API_KEYS_*` mechanism: CLI reads
  `SAM_ADMIN_API_USER` / `SAM_ADMIN_API_PASSWORD` (or similar) from env, does
  `requests.post(f"{SAM_WEBAPP_URL}/api/v1/admin/cache/refresh", auth=(user, password), params={'category': category})`.
  `requests` is already a project dependency (`pyproject.toml:46`).
- Command lives in a new small `CacheAdminCommand` under `src/cli/` (or inline in
  `cmds/admin.py` if trivial) — follow existing exit-code conventions
  (`EXIT_SUCCESS`/`EXIT_ERROR`) and print a short rich summary of `cleared` counts on
  success, an error message with the HTTP status/response body on failure (e.g. bad
  credentials, webapp unreachable, wrong category).
- Document the new command + env vars in the CLAUDE.md "CLI Tools" / "Quick Reference"
  sections once implemented.

## Verification

1. `pytest tests/api/` (new test file `tests/api/test_admin_cache.py`): auth (403 without
   `SYSTEM_ADMIN`), success (200 + `cleared` dict shape), invalid `?category=` (400).
2. `pytest tests/unit/test_sam_search_cli.py`-style CliRunner test for
   `sam-admin cache --refresh` (mock the HTTP call).
3. Manual: `docker compose up`, then
   `curl -u <api_key_user>:<password> -X POST http://localhost:5050/api/v1/admin/cache/refresh`
   and confirm a JSON `{"status": "ok", "cleared": {...}}` with all 4 categories present;
   repeat with `?category=chart` and confirm only `chart` key is present.
4. Full `pytest` run (~65s) to confirm no regressions in the 3 modified refresh routes or
   the audit hook.

# Rate Limiting for SAM Webapp

## Context

The webapp currently has **no rate limiting**. Concrete exposure:

- `/auth/login` (stub provider) accepts unlimited POSTs — no brute-force barrier.
- `/api/v1/status/{derecho,casper,jupyterhub,outage}` are bcrypt-protected; without a limit, an attacker can both DoS CPU and attempt online cracking.
- Authenticated GETs (e.g. `/api/v1/projects/<projcode>/charges`) are cached, but a runaway script in a tight loop can still exercise the cold path repeatedly.

We want protection against brute-force, runaway scripts, and accidental DoS — plus visibility into when limits trip, surfaced in the existing Admin > Configuration tab.

**Phasing:** Phase 1 is intentionally minimal — Flask-Limiter on per-worker memory, structured logs on every 429, and a tile in the existing Admin > Configuration card. **No new tables, no new admin page, no Alembic migration in Phase 1.** When we move to shared storage (Phase 2), we add the `RateLimitEvent` table (via Alembic), the dedicated `/admin/htmx/rate-limits` page, and the unblock action.

**Storage decision** (per discussion): start with **per-worker memory** (`memory://`), with a clean swap path to a **MySQL/Postgres-backed adapter on the `system_status` DB** when we want cross-worker accuracy. The `system_status` DB is the right target because we own its schema today; the SAM DB is shared with legacy and off-limits for new tables. `system_status.session` already supports both MySQL and Postgres via `STATUS_DB_DRIVER`, so a future swap is one storage URI change.

The per-worker phase is honest: with `(2×cores)+1` gunicorn workers, an N/min limit is effectively N×workers/min globally. That's fine for catching unsophisticated brute-force and runaway scripts; we'll document this and tighten when we move to shared storage.

---

## Architecture

### Library
**Flask-Limiter** — sole reasonable choice. Decorator-based, supports per-IP and per-user keying, exempts routes by tag, exposes events via signals/hooks.

### Limiter init (single source of truth)
New module `src/webapp/extensions/limiter.py` (alongside the existing `extensions/__init__.py`) that exposes a module-level `limiter = Limiter(...)` instance, configured in `src/webapp/run.py` next to the cache init (run.py:104-109 area).

```python
# src/webapp/extensions/limiter.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def _key_func():
    """Per-user when authenticated, per-IP otherwise. API-key M2M
    callers key on api-key username (set on flask.g by api_auth)."""
    from flask import g
    from flask_login import current_user
    if getattr(g, 'api_key_user', None):
        return f"apikey:{g.api_key_user}"
    if current_user.is_authenticated:
        return f"user:{current_user.username}"
    return f"ip:{get_remote_address()}"

limiter = Limiter(key_func=_key_func, headers_enabled=True)
```

`headers_enabled=True` adds `X-RateLimit-*` response headers — useful for clients writing well-behaved retry logic.

### ProxyFix
**Verify** (and fix if missing): `ProxyFix` middleware in `run.py` so `get_remote_address()` returns the real client IP behind nginx/traefik, not the proxy IP. Without this, every request looks like it comes from the same internal LB address.

### Limit tiers (config, not hardcoded)

Add to `SAMWebappConfig` in `src/webapp/config.py`:

```python
RATELIMIT_ENABLED        = os.getenv('RATELIMIT_ENABLED', '1').lower() in ('1','true','yes')
RATELIMIT_STORAGE_URI    = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')
RATELIMIT_STRATEGY       = 'fixed-window'   # cheaper than moving-window on SQL backend

# Tier limits — tunable without code change
RATELIMIT_AUTH_LOGIN     = os.getenv('RATELIMIT_AUTH_LOGIN',     '5 per minute; 20 per hour')
RATELIMIT_M2M            = os.getenv('RATELIMIT_M2M',            '120 per minute')
RATELIMIT_AUTHED         = os.getenv('RATELIMIT_AUTHED',         '200 per minute')
RATELIMIT_ANON           = os.getenv('RATELIMIT_ANON',           '30 per minute')
RATELIMIT_EXEMPT_PATHS   = ('/api/v1/health',)  # prefix match
```

`TestingConfig.RATELIMIT_ENABLED = False` — non-negotiable. The xdist parallel test run will trip global limits otherwise.

### Where each limit gets applied

| Tier | Decorator location | Routes |
|---|---|---|
| `RATELIMIT_AUTH_LOGIN` | `src/webapp/auth/blueprint.py` on the POST `/auth/login` handler | stub login |
| `RATELIMIT_M2M` | `src/webapp/utils/api_auth.py` — apply inside the existing `@api_key_required` decorator so every M2M route inherits it | `/api/v1/status/*` POSTs |
| `RATELIMIT_AUTHED` | `Limiter.default_limits` applied via `limiter.init_app(app)`, with `@limiter.exempt` on health routes | all authenticated routes |
| `RATELIMIT_ANON` | Same default mechanism — `_key_func` distinguishes via `ip:` prefix; we add a separate `@limiter.limit(...)` on the few unauth routes that aren't health | `/` redirect, login GET, OIDC kickoff |
| Exempt | `@limiter.exempt` | `/api/v1/health/*`, static |

Auth login limits are **per-IP** specifically — even though `_key_func` would key on username for an unauthenticated POST (the user isn't logged in yet), we override to `key_func=get_remote_address` on the decorator so attackers can't sidestep by varying usernames.

### Event logging — the monitoring spine (Phase 1: logs + in-memory counters)

Flask-Limiter's storage tracks counters but doesn't keep an event log. In Phase 1 we keep this lightweight:

1. **Structured log line on every 429** — actor, endpoint, method, path, limit string, key. Flows through the existing app logger (console + rotating file when `LOG_FILE` is set).
2. **Per-worker in-memory rolling counters** — a small bounded structure (e.g. `collections.Counter` keyed by actor, plus a 24h sliding bucket of total 429s) maintained on each worker. Read at request time by the Configuration tile.

```python
# src/webapp/utils/rate_limit_events.py
@app.errorhandler(429)
def _record_429(e):
    current_app.logger.warning(
        "rate_limit_exceeded",
        extra={'actor': _key_func(),
               'endpoint': request.endpoint,
               'method': request.method,
               'path': request.path,
               'limit': str(e.description)}
    )
    rate_limit_counters.bump(actor=_key_func(), path=request.path)
    return _format_429_response(e)   # JSON for /api/*, HTMX-friendly otherwise
```

**Why no DB table in Phase 1:** keeps Phase 1 truly memory-only with zero schema commitment. The counter shape may change once we have real production data; locking it into Alembic before that would invite churn.

**Caveat made explicit:** the per-worker counter on the Configuration tile undercounts — only shows 429s the *current* worker has seen. We label it that way in the UI ("429s this worker, last 24h") rather than implying global truth. Phase 2 fixes both monitoring and counter accuracy by introducing `RateLimitEvent`.

**Content-type-aware response**: 429 on `/api/*` → JSON; everywhere else → small HTML page or HTMX-friendly fragment for HTMX paths.

### Monitoring surface (Phase 1)

#### Admin > Configuration tile (extends existing card)

Extend `webapp/utils/config_inspect.gather_runtime_state()` with a `rate_limits` block:

```python
{
  'enabled': True,
  'storage': 'memory://',
  'tiers': {'auth_login': '5/min; 20/hr', 'm2m': '120/min', ...},
  'events_24h_this_worker': 17,
  'top_offenders_24h_this_worker': [('ip:10.0.0.5', 8), ('user:foo', 4), ...],
}
```

Source: per-worker in-memory counter from `webapp/utils/rate_limit_events.py`. Tile label makes the per-worker scope explicit (no implied global truth).

Render as a new `<dl>` tile in `templates/dashboards/admin/fragments/configuration_card.html`, between the Caching and Audit tiles. Read-only — matches the existing "no mutation" convention of that page.

**No dedicated rate-limits page in Phase 1** — see Phase 2 below. Investigation of incidents in Phase 1 is via grep/tail of logs (`rate_limit_exceeded` lines).

---

## Files to modify (Phase 1)

**New:**
- `src/webapp/extensions/limiter.py` — Limiter instance + `_key_func`
- `src/webapp/utils/rate_limit_events.py` — 429 handler, structured log line, per-worker counters, content-type-aware response

**Modified:**
- `src/webapp/config.py` — add `RATELIMIT_*` keys (after `CACHE_DEFAULT_TIMEOUT` at line 55), set `TestingConfig.RATELIMIT_ENABLED = False`
- `src/webapp/run.py` — `limiter.init_app(app)` near cache init (~line 110), register 429 handler
- `src/webapp/auth/blueprint.py` — `@limiter.limit(cfg['RATELIMIT_AUTH_LOGIN'], key_func=get_remote_address)` on stub login POST
- `src/webapp/utils/api_auth.py` — apply `RATELIMIT_M2M` inside `@api_key_required`
- `src/webapp/api/v1/health.py` — `@limiter.exempt` on each health route
- `src/webapp/utils/config_inspect.py` — extend `gather_runtime_state` with `rate_limits` block
- `templates/dashboards/admin/fragments/configuration_card.html` — new tile between Caching and Audit
- `requirements.txt` / `pyproject.toml` — add `Flask-Limiter`

**Not in Phase 1** (deferred to Phase 2 alongside shared storage): `RateLimitEvent` model, Alembic migration, dedicated `/admin/htmx/rate-limits` page, unblock action.

**Verify before touching (uncertainty pockets):**
- `ProxyFix` placement in `run.py` — check whether it's already applied with correct `x_for=` count

---

## Reuse — existing helpers/patterns

- **`@api_key_required`** (`webapp/utils/api_auth.py`) — already sets `g.api_key_user`; `_key_func` reads from there. Don't add a parallel mechanism.
- **`gather_runtime_state`** (`webapp/utils/config_inspect.py`) — extend, don't bypass. The Configuration card pulls everything from this single function.
- **`fmt.fmt_number` / `fmt_date`** (`sam/fmt.py`) — use Jinja filters for the new tile values (per CLAUDE.md §"Display Formatting").
- **App logger** (`webapp/logging_config.py`) — the structured `rate_limit_exceeded` line uses the existing logger; no new sink.

---

## Verification

1. **Unit tests** (`tests/unit/test_rate_limiter.py`):
   - `_key_func` branches: anon → `ip:`, authenticated → `user:`, M2M → `apikey:`
   - 429 handler emits the expected log line and bumps the per-worker counter
   - JSON vs HTML response shape based on `request.path.startswith('/api/')`

2. **Integration tests** (`tests/integration/test_rate_limit_flow.py`):
   - With `RATELIMIT_ENABLED=True` for this test only: hammer `/auth/login` 6 times → 6th returns 429
   - Hammer a rate-limited API endpoint as authenticated user → eventually 429
   - Health endpoints (`/api/v1/health/*`) never 429 even when hammered

3. **Manual smoke**:
   - `docker compose up webdev --watch`, log in as `bdobbins` (per memory: profiling target user), open Admin > Configuration → new rate-limit tile renders with current tier strings and "0 / this worker / last 24h"
   - `for i in {1..10}; do curl -X POST localhost:5050/auth/login -d 'username=foo&password=bar'; done` → 429s appear in logs; tile counter ticks up (when refresh hits the same worker)

4. **Test-suite regression**: run full `pytest` — should still pass in ~65s, no false 429s. If anything trips, `TestingConfig.RATELIMIT_ENABLED = False` is wrong/unset.

5. **Production sanity check after deploy**: tail logs for `rate_limit_exceeded` lines for the first 24h. If real users are tripping the `RATELIMIT_AUTHED` limit, raise it; tier knobs are env-vars so no code change.

---

## Phase 2 (deferred — DB-aware version)

Triggered when Phase 1 logs show we want cross-worker accuracy and historical event browsing. Bundles all of:

1. **Switch storage**: set `RATELIMIT_STORAGE_URI='mysql+pymysql://.../system_status'` (or postgres equivalent). `flask-limiter` supports SQLAlchemy storage natively. Counters become exact across workers; `moving-window` strategy becomes viable.
2. **Add `RateLimitEvent` model** (`src/system_status/models/rate_limit.py`):
   ```python
   class RateLimitEvent(StatusBase):
       __tablename__ = 'rate_limit_event'
       id          = Column(Integer, primary_key=True, autoincrement=True)
       occurred_at = Column(DateTime, nullable=False, index=True, default=datetime.now)
       actor_kind  = Column(String(16), nullable=False)
       actor       = Column(String(128), nullable=False, index=True)
       endpoint    = Column(String(255), nullable=False)
       method      = Column(String(8),   nullable=False)
       limit_str   = Column(String(64),  nullable=False)
       path        = Column(String(255), nullable=False)
   ```
3. **Alembic migration**: `make migrate-status-revision MSG="add rate_limit_event"`, review autogen output (verify indices on `occurred_at` and `actor`), `make migrate-status-up`. The `tests/integration/test_alembic_migrations.py` drift test catches model/migration mismatches.
4. **Wire 429 handler to write `RateLimitEvent`** in addition to log line + counter. Best-effort INSERT (never block the response).
5. **Retention**: `prune_rate_limit_events(days=30)` invoked from existing daily-task path; or 1%-sampled prune-on-insert.
6. **Dedicated admin page** `/admin/htmx/rate-limits` — `src/webapp/dashboards/admin/rate_limits_routes.py`, gated on `Permission.SYSTEM_ADMIN`. Sections:
   - **Recent events** — paginated `RateLimitEvent` rows, filterable by actor / path / time window
   - **Per-route hit counts** — last 24h aggregation
   - **Active blocks** — listing current bucket state (now exact, queried from shared storage)
   - **Unblock button** — POST that deletes a bucket from the limiter's storage. Audit-logged. Now meaningful because storage is shared.
7. **Configuration tile**: replace per-worker counters with global `RateLimitEvent` aggregates.
8. **HtmxFormSchema** for the unblock form (single-field `ClearRateLimitForm`) per CLAUDE.md §9.

The Phase 1 architecture is built to absorb (1) with no code change; everything else is additive.

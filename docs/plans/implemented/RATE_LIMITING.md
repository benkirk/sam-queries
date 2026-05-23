# Rate Limiting for SAM Webapp

## Context

The webapp currently has **no rate limiting**. Concrete exposure:

- `/auth/login` (stub provider) accepts unlimited POSTs — no brute-force barrier.
- `/api/v1/status/{derecho,casper,jupyterhub,outage}` are bcrypt-protected; without a limit, an attacker can both DoS CPU and attempt online cracking.
- Authenticated GETs (e.g. `/api/v1/projects/<projcode>/charges`) are cached, but a runaway script in a tight loop can still exercise the cold path repeatedly.

We want protection against brute-force, runaway scripts, and accidental DoS — plus visibility into when limits trip, surfaced both on the existing Admin > Configuration tab and on a dedicated admin page.

**Single-phase plan.** An earlier draft of this document phased the work
(Phase 1: per-worker `memory://`; Phase 2: shared Redis backend +
`RateLimitEvent` MySQL table + dedicated admin page). Phase 2's
prerequisite has shipped:

- `docs/plans/REDIS.md` is merged (commit `df0e9cd`, PR #242).
- Both `compose.yaml` and `helm/templates/deployment.yaml` already inject
  `RATELIMIT_STORAGE_URI=redis://…/1` for the webapp.
- The `webapp.caching.Caching` facade demonstrates the
  Redis-with-graceful-fallback pattern we mirror here.
- `sam.caching.redis_client.make_redis_client` already handles connectivity
  + PING with timeout.
- `ProxyFix` is correctly configured in `src/webapp/run.py:62–63`, so
  `get_remote_address()` returns the real client IP behind nginx/Traefik.

Because the storage backend is solved, we ship the whole feature in one
pass: Redis storage, all endpoints covered, dedicated admin page with
unblock action, and **no MySQL table / no Alembic migration** (a Redis
sorted set with 24h retention replaces durable history; long-term
forensics live in the structured app log).

If `RATELIMIT_STORAGE_URI` is unset or Redis is unreachable, the limiter
gracefully falls back to per-worker `memory://` and logs a warning —
matching the Caching facade. The webapp keeps serving.

---

## Architecture

### Library
**Flask-Limiter** — sole reasonable choice. Decorator-based, supports per-IP and per-user keying, exempts routes by tag, exposes events via signals/hooks, supports a Redis storage backend natively via the `limits[redis]` driver.

Add to `pyproject.toml`:
```toml
"Flask-Limiter",
"limits[redis]",   # Flask-Limiter's Redis storage driver
```

### Storage — Redis-first with graceful fallback

Decision tree at startup, mirroring `webapp.caching.Caching`:

| `RATELIMIT_STORAGE_URI` set? | Redis PING ok? | Result |
|---|---|---|
| yes | yes | `redis://…` storage; cross-worker accuracy + dedicated page works |
| yes | no | downgrade to `memory://`, log a warning (matches `webapp/caching/__init__.py:85–91`) |
| no | — | `memory://` (dev-without-Redis or unit tests) |

Reuse `sam.caching.redis_client.make_redis_client` for the PING check —
it already handles `socket_timeout` and raises on failure. Don't
duplicate.

Strategy: `fixed-window` (cheaper than `moving-window` even on Redis;
sufficient for production needs).

### Limiter facade — `src/webapp/limiter/`

New module, structured the same way as `src/webapp/caching/`:

```
src/webapp/limiter/
  __init__.py   # Limiting facade + module-level `limiter` singleton
  events.py     # Redis-backed event ring + per-worker fallback
```

`src/webapp/limiter/__init__.py` exposes:

```python
# src/webapp/limiter/__init__.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def _key_func():
    """Per-API-key when M2M, per-user when authenticated, per-IP otherwise."""
    from flask import g
    from flask_login import current_user
    if getattr(g, 'api_key_user', None):
        return f"apikey:{g.api_key_user}"
    if current_user.is_authenticated:
        return f"user:{current_user.username}"
    return f"ip:{get_remote_address()}"

class Limiting:
    """Single facade. Owns the Flask-Limiter instance, resolves the
    storage backend at init_app time, and proxies the event ring.

    On construction the storage URI defaults to memory://. init_app
    upgrades to redis:// when reachable; otherwise stays on memory://
    and logs a warning. Mirrors webapp.caching.Caching.
    """
    def __init__(self):
        self.limiter = Limiter(
            key_func=_key_func,
            headers_enabled=True,
            strategy='fixed-window',
            storage_uri='memory://',          # resolved in init_app
        )
        self._redis_client = None

    def init_app(self, app):
        if not app.config.get('RATELIMIT_ENABLED', True):
            self.limiter.enabled = False
        uri = app.config.get('RATELIMIT_STORAGE_URI') or ''
        if uri.startswith('redis://') or uri.startswith('rediss://'):
            try:
                from sam.caching import make_redis_client
                self._redis_client = make_redis_client(uri)
                self.limiter.storage_uri = uri
            except Exception as exc:
                app.logger.warning(
                    "Limiter: RATELIMIT_STORAGE_URI=%s set but Redis is "
                    "unreachable (%s); falling back to memory://.",
                    uri, exc,
                )
                self.limiter.storage_uri = 'memory://'
        # default_limits applied to every route that doesn't carry its own
        self.limiter.default_limits = [app.config['RATELIMIT_AUTHED']]
        self.limiter.init_app(app)
        from webapp.limiter.events import init_events, register_429_handler
        init_events(app, self._redis_client)
        register_429_handler(app)

    def stats(self):
        from flask import current_app
        from webapp.limiter.events import recent, top_offenders, active_blocks
        cfg = current_app.config
        return {
            'enabled': bool(self.limiter.enabled),
            'storage': self.limiter.storage_uri,
            'tiers': {
                'auth_login': cfg['RATELIMIT_AUTH_LOGIN'],
                'm2m':        cfg['RATELIMIT_M2M'],
                'authed':     cfg['RATELIMIT_AUTHED'],
                'anon':       cfg['RATELIMIT_ANON'],
            },
            'events_24h':           len(recent(limit=cfg['RATELIMIT_EVENT_MAX'])),
            'top_offenders_24h':    top_offenders(n=5),
            'active_blocks_count':  len(active_blocks()),
        }

# Module-level singleton — import this from anywhere in webapp.
limiter = Limiting()
```

`headers_enabled=True` adds `X-RateLimit-*` response headers — useful for
clients writing well-behaved retry logic.

### Event recording — Redis sorted set (with deque fallback)

`src/webapp/limiter/events.py` exposes:

- **`record_429(actor, endpoint, method, path, limit_str)`** — `ZADD` to
  the Redis sorted set `ratelimit:events` keyed on `unix_timestamp`,
  followed by `ZREMRANGEBYSCORE` to drop entries older than
  `RATELIMIT_EVENT_RETENTION_HOURS`, plus a hard `ZREMRANGEBYRANK` cap at
  `RATELIMIT_EVENT_MAX` to bound memory under bursts. When the resolved
  storage is `memory://`, falls back to a per-worker
  `collections.deque(maxlen=RATELIMIT_EVENT_MAX)` (best-effort, scoped to
  the worker — same caveat the in-memory cache carries).
- **`recent(limit=200)`** — `ZREVRANGE` newest-first; returns list of
  decoded event dicts.
- **`top_offenders(window_seconds=86400, n=10)`** — derived from
  `recent()` via `collections.Counter`.
- **`active_blocks()`** — enumerate currently-blocked actors via
  `flask_limiter.storage` accessors (storage-agnostic; works on both
  Redis and memory).
- **`clear_bucket(actor_key)`** — delete the bucket for one actor; used
  by the unblock action.

### ProxyFix
Already in place at `src/webapp/run.py:62–63`:
`ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)`. No
change needed. `get_remote_address()` returns the real client IP behind
nginx/Traefik.

### Limit tiers (config, not hardcoded)

Add to `SAMWebappConfig` in `src/webapp/config.py` after
`CACHE_DEFAULT_TIMEOUT` (line 55):

```python
# Rate limiting (see docs/plans/RATE_LIMITING.md). Storage URI is set in
# helm/compose; absence = memory:// fallback for local dev without Redis.
RATELIMIT_ENABLED      = os.getenv('RATELIMIT_ENABLED', '1').lower() in ('1','true','yes')
RATELIMIT_STORAGE_URI  = os.getenv('RATELIMIT_STORAGE_URI', '')   # empty = memory://
RATELIMIT_STRATEGY     = 'fixed-window'

RATELIMIT_AUTH_LOGIN   = os.getenv('RATELIMIT_AUTH_LOGIN',   '5 per minute; 20 per hour')
RATELIMIT_M2M          = os.getenv('RATELIMIT_M2M',          '120 per minute')
RATELIMIT_AUTHED       = os.getenv('RATELIMIT_AUTHED',       '200 per minute')
RATELIMIT_ANON         = os.getenv('RATELIMIT_ANON',         '30 per minute')
RATELIMIT_EVENT_RETENTION_HOURS = int(os.getenv('RATELIMIT_EVENT_RETENTION_HOURS', 24))
RATELIMIT_EVENT_MAX             = int(os.getenv('RATELIMIT_EVENT_MAX', 1000))
```

`TestingConfig.RATELIMIT_ENABLED = False` — non-negotiable. The xdist
parallel test run would trip global limits otherwise.

### Where each tier gets applied

| Tier | Decorator location | Routes |
|---|---|---|
| `RATELIMIT_AUTH_LOGIN` (per-IP) | `src/webapp/auth/blueprint.py:45` on POST `/auth/login` | stub login |
| `RATELIMIT_M2M` | inside `@api_key_required` in `src/webapp/utils/api_auth.py:30` | all `POST /api/v1/status/*` |
| `RATELIMIT_AUTHED` | `Limiter.default_limits` global default | `/api/v1/projects/*`, `/api/v1/users/*`, `/api/v1/allocations/*`, `/api/v1/charges`, `/api/v1/{directory,project,fstree}_access`, all HTMX `/admin/htmx/*` and `/user/htmx/*` |
| `RATELIMIT_ANON` | `@limiter.limit(cfg['RATELIMIT_ANON'])` on GET `/`, GET `/auth/login`, OIDC kickoff/callback | unauthenticated GETs |
| Exempt | `@limiter.exempt` | `/api/v1/health/*`, Flask static |

The `default_limits=[RATELIMIT_AUTHED]` covers the long tail without
per-route decoration. We only add explicit decorators for the four routes
that need a different tier (login POST, login GET, M2M, OIDC kickoff)
and for `exempt`.

Auth login limits are **per-IP** specifically — even though `_key_func`
would key on username for an unauthenticated POST (the user isn't logged
in yet), we override to `key_func=get_remote_address` on the decorator so
attackers can't sidestep by varying usernames.

OIDC callback, login GET, and `/` redirect get the ANON tier explicitly.
This avoids the AUTHED default applying to anon traffic where the actor
key collapses to `ip:…` — a single shared NAT can chew through 200/min
quickly.

### 429 errorhandler

```python
@app.errorhandler(429)
def _handle_429(e):
    actor = _key_func()
    current_app.logger.warning(
        "rate_limit_exceeded",
        extra={
            'actor': actor,
            'endpoint': request.endpoint,
            'method': request.method,
            'path': request.path,
            'limit': str(e.description),
        },
    )
    record_429(actor, request.endpoint, request.method, request.path, str(e.description))
    return _format_429_response(e)
```

`_format_429_response` is content-type-aware:
- `request.path.startswith('/api/')` → JSON `{error, retry_after, limit}`
- `request.headers.get('HX-Request')` → small HTML fragment for HTMX swap
- otherwise → full HTML page

The `Retry-After` header is preserved from `e.description` in all cases.

### Configuration tile (extension of existing card)

`src/webapp/utils/config_inspect.py` — extend `gather_runtime_state`
between the `caching` block (lines 355–371) and `audit_logging` (line 373):

```python
try:
    from webapp.limiter import limiter as _limiter_facade
    rate_limits_block = _limiter_facade.stats()
except Exception:
    rate_limits_block = {
        'enabled': False, 'storage': None, 'tiers': {},
        'events_24h': 0, 'top_offenders_24h': [], 'active_blocks_count': 0,
    }
```

Tile rendered in
`src/webapp/templates/dashboards/admin/fragments/configuration_card.html`
between the Caching card (ends ~line 302) and Audit & Logging card
(~line 305). Read-only — matches the existing convention of that page.

The tile shows: enabled, storage backend (URI masked if it contains
credentials), all four tier strings, total 429 events in last 24h
(globally accurate via Redis), top 5 offenders, count of active blocks,
and a link to the dedicated page.

### Dedicated admin page — `/admin/htmx/rate-limits`

New blueprint route module
`src/webapp/dashboards/admin/rate_limits_routes.py`, gated on
`Permission.SYSTEM_ADMIN`. Page template
`src/webapp/templates/dashboards/admin/rate_limits.html` extends the admin
base. Each section is its own HTMX fragment so they refresh
independently.

Sections:

1. **Recent events** — paginated table from `events.recent()`. Columns:
   timestamp, actor, endpoint, method, path, limit. Filter by actor
   substring + time window (last 1h / 24h).
2. **Top offenders (last 24h)** — table from `events.top_offenders()`.
3. **Active blocks** — actors currently rate-limited, with an
   "Unblock" button. Form posts to
   `POST /admin/htmx/rate-limits/unblock` with the actor key. Calls
   `events.clear_bucket(actor)`. Audit-logged via the existing audit
   hook (matches other admin mutation routes).
4. **Tier configuration** — read-only display of the same tier strings
   shown on the Configuration tile, for a self-contained ops view.

The unblock form uses an `HtmxFormSchema` named `ClearRateLimitForm` —
**add to** `src/sam/schemas/forms/admin.py` (creating the file if absent)
**before** wiring the route, per CLAUDE.md §9. Export it from
`src/sam/schemas/forms/__init__.py`.

```python
class ClearRateLimitForm(HtmxFormSchema):
    """Validate the actor key for the rate-limit unblock action.

    No DB lookup — the actor key (e.g. 'ip:10.0.0.5') is a Redis
    bucket identifier; existence is checked at storage layer in the
    route handler.
    """
    actor = fields.String(required=True, validate=validate.Length(min=1, max=128))
```

Page registered in `src/webapp/dashboards/admin/blueprint.py` next to the
existing route module imports.

---

## Files to modify

**New:**
- `src/webapp/limiter/__init__.py` — `Limiting` facade + module-level
  `limiter` singleton, mirroring `src/webapp/caching/__init__.py`
- `src/webapp/limiter/events.py` — `record_429`, `recent`,
  `top_offenders`, `active_blocks`, `clear_bucket`; Redis sorted-set
  with deque fallback
- `src/webapp/dashboards/admin/rate_limits_routes.py` —
  `/admin/htmx/rate-limits` page + `/admin/htmx/rate-limits/unblock` action
- `src/webapp/templates/dashboards/admin/rate_limits.html`
- `src/webapp/templates/dashboards/admin/fragments/rate_limits_recent.html`
- `src/webapp/templates/dashboards/admin/fragments/rate_limits_offenders.html`
- `src/webapp/templates/dashboards/admin/fragments/rate_limits_blocks.html`
- `src/sam/schemas/forms/admin.py` — `ClearRateLimitForm`
- `tests/unit/test_rate_limiter.py` — `_key_func` branches, 429 handler
  shape, Redis sorted-set retention math, fallback to deque
- `tests/integration/test_rate_limit_flow.py` — end-to-end with
  `RATELIMIT_ENABLED=True` for that test only; verifies login / M2M /
  AUTHED tiers; verifies `/api/v1/health/*` never 429s; verifies
  unblock action

**Modified:**
- `pyproject.toml` — add `Flask-Limiter`, `limits[redis]`
- `src/webapp/config.py` — add `RATELIMIT_*` keys after line 55; set
  `TestingConfig.RATELIMIT_ENABLED = False`
- `src/webapp/run.py` — call `from webapp.limiter import limiter; limiter.init_app(app)`
  immediately after `caching.init_app(app)` (~line 122)
- `src/webapp/auth/blueprint.py` —
  `@limiter.limit(lambda: current_app.config['RATELIMIT_AUTH_LOGIN'], key_func=get_remote_address, methods=['POST'])`
  on the `/auth/login` view; ANON tier on the GET path; OIDC kickoff/callback
- `src/webapp/utils/api_auth.py` — apply `RATELIMIT_M2M` inside the
  `@api_key_required` decorator
- `src/webapp/api/v1/health.py` — `@limiter.exempt` on each route
- `src/webapp/utils/config_inspect.py` — add `rate_limits` block in
  `gather_runtime_state`
- `src/webapp/templates/dashboards/admin/fragments/configuration_card.html` —
  new tile between Caching and Audit cards
- `src/webapp/dashboards/admin/blueprint.py` — register
  `rate_limits_routes` module
- `src/sam/schemas/forms/__init__.py` — export `ClearRateLimitForm`

**No changes needed (already in place):**
- `compose.yaml:52–53, 114–115` — `RATELIMIT_STORAGE_URI=redis://cache:6379/1`
  already injected for both `webapp` and `webdev`
- `helm/templates/deployment.yaml` — `RATELIMIT_STORAGE_URI` already
  injected via the `cache.enabled` branch
- `src/webapp/run.py:62–63` — ProxyFix already correctly configured
- `src/sam/caching/redis_client.py` — `make_redis_client` already exists
  and handles PING

---

## Reuse — existing helpers/patterns

- **`webapp.caching.Caching` facade** (`src/webapp/caching/__init__.py`)
  — exact pattern to mirror: singleton facade, `init_app` reconciling
  backend on PING failure, `stats()` for the admin tile, graceful
  downgrade to per-worker fallback
- **`sam.caching.make_redis_client`** (`src/sam/caching/redis_client.py`)
  — reuse for the storage URI PING check; do not duplicate
- **`@api_key_required`** (`src/webapp/utils/api_auth.py:30`) — already
  sets `g.api_key_user`; `_key_func` reads from there
- **`gather_runtime_state`** (`src/webapp/utils/config_inspect.py:268`) —
  extend, don't bypass. The Configuration card pulls everything from this
  single function.
- **`fmt.fmt_number` / `fmt_date`** (`src/sam/fmt.py`) — Jinja filters
  for the new tile and dedicated page (per CLAUDE.md §"Display Formatting")
- **`HtmxFormSchema`** (`src/sam/schemas/forms/__init__.py`) — base class
  for `ClearRateLimitForm` (per CLAUDE.md §9)
- **App logger** (`src/webapp/logging_config.py`) — emit
  `rate_limit_exceeded` warnings; no new sink
- **Audit hook** for the unblock action — reuse the existing audit
  decorator/event used by other admin mutation routes (under `webapp.audit`)

---

## Verification

1. **Unit tests** (`tests/unit/test_rate_limiter.py`):
   - `_key_func` branches: anon → `ip:`, authenticated → `user:`,
     M2M → `apikey:`
   - `record_429` writes the right sorted-set entry; retention drops
     entries older than `RATELIMIT_EVENT_RETENTION_HOURS`; cap holds at
     `RATELIMIT_EVENT_MAX` under bursts
   - `record_429` falls back to a bounded deque when Redis client is `None`
   - 429 response shape: JSON for paths starting `/api/`, HTML fragment
     for HTMX, full page otherwise
   - `Limiting.stats()` returns the documented dict shape

2. **Integration tests** (`tests/integration/test_rate_limit_flow.py`):
   With `RATELIMIT_ENABLED=True` and a `fakeredis` storage URI for the
   test only:
   - hammer `/auth/login` 6× from the same IP → 6th returns 429
   - hammer a rate-limited API endpoint as authenticated user past
     `RATELIMIT_AUTHED` → eventually 429
   - hammer `/api/v1/status/derecho` past `RATELIMIT_M2M` → 429
   - `/api/v1/health/*` never returns 429 even when hammered 1000×
   - unblock action clears a known bucket and the next request from that
     actor succeeds

3. **Manual smoke**:
   - `docker compose up webdev --watch`, log in as `bdobbins`, open
     Admin > Configuration → new tile shows
     `storage: redis://cache:6379/1`, `0 events / 24h`
   - `for i in {1..10}; do curl -X POST localhost:5050/auth/login -d 'username=foo&password=bar'; done`
     → 429s appear in logs; tile counter ticks up; events appear on
     `/admin/htmx/rate-limits`
   - Click "Unblock" on the offender → next attempt succeeds
   - `docker compose stop cache`, restart webapp → tile shows
     `storage: memory:// (Redis unreachable)`; webapp keeps serving;
     warning logged

4. **Test-suite regression**: `pytest` should still pass in ~65s with
   `TestingConfig.RATELIMIT_ENABLED = False`. If anything trips, the
   testing flag is wrong/unset.

5. **Production sanity check**: tail logs for `rate_limit_exceeded` for
   the first 24h post-deploy. If real users trip `RATELIMIT_AUTHED`,
   raise it via env var without a code change.

---

## Future work (out of scope)

- **Per-route override tiers** — current plan keeps tiers global; if
  specific endpoints prove noisy in prod (e.g. expensive analytics
  endpoints), add per-route `@limiter.limit(...)` overrides. Trivially
  additive on top of this plan.
- **Persistent event history** — Redis sorted-set with 24h retention is
  intentional; durable forensics live in the structured app log. If SQL
  queryability over historical events becomes a real need, revisit
  adding a `RateLimitEvent` table and a sampled write path.
- **Per-tenant tiers** — currently a single AUTHED tier for all users.
  If specific accounts need higher quotas, add a lookup in `_key_func`
  or a tier-resolution helper.

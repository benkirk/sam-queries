# Production Readiness — SAM Web Application

## Status at a Glance

| # | Item | Priority | Status |
|---|------|----------|--------|
| 1 | Hardcoded `SECRET_KEY` | Critical | ✅ Done |
| 2 | Dev auth enabled by default | Critical | 🔴 Open |
| 3 | Session cookie security flags | Critical | ✅ Done |
| 4 | Security headers | High | 🔴 Open |
| 5 | Gunicorn production config | High | ✅ Done |
| 6 | Health check + DB pool endpoints | High | ✅ Done |
| 7 | Config/env-var checking | High | 🔴 **Next** |
| 8 | Structured logging | High | 🔴 Open |
| 9 | Request ID tracking | High | 🔴 Open |
| 10 | Security testing suite | Medium | 🔴 Open |
| 11 | Deployment checklist / docs | Medium | 🔴 Open |
| 12 | Metrics & alerting | Future | 🔴 Open |

---

## ✅ Completed Items

### 1 — Hardcoded Secret Key
`FLASK_SECRET_KEY` env var required; `ValueError` raised on startup if unset.
Added to `.env.example`, `containers/webapp/Dockerfile`, and local `.env`.
**Commit**: `101e383`

### 3 — Session Cookie Security Flags
`SESSION_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE='Lax'`,
and `PERMANENT_SESSION_LIFETIME=12 h` set in `run.py` when `app.debug` is False.
**Commit**: `101e383`

### 5 — Gunicorn Production Config
`containers/webapp/gunicorn_config.py` created; production Dockerfile stage runs:
`gunicorn -c containers/webapp/gunicorn_config.py "webapp.run:create_app()"`.
Workers tuned to `(2 × CPU) + 1`, `preload_app=True`, logs to stdout/stderr.
**Commit**: `eb7fa8f`

### 6 — Health Check + DB Pool Monitoring
`src/webapp/api/v1/health.py` blueprint at `/api/v1/health`:
- `GET /` and `GET /ready` — ping both DB binds, 200/503 (public)
- `GET /live` — immediate alive response, no DB call (public, k8s liveness)
- `GET /db-pool` — pool stats for all engines (admin login required)

22 tests in `tests/api/test_health_endpoints.py`.
**Commits**: `f6840ae`, `a5991aa`, `ddd052a`

---

## Open Items — Prioritised

---

### Priority 1 — Configuration & Environment Variable Checking

#### 2 — Dev Auth Enabled by Default (Critical)

`compose.yaml` sets `DISABLE_AUTH=1` / `DEV_AUTO_LOGIN_USER=benkirk` for the `webdev`
service only, but it must **never** reach the production (`webapp`) service.

**Actions**:
- Add `# WARNING: never set in production` comments in `compose.yaml` next to the dev vars.
- Create `.env.production.example` documenting required production env vars (see §11).
- Ensure the `webapp` service in `compose.yaml` does **not** set `DISABLE_AUTH` or
  `DEV_AUTO_LOGIN_USER`.

#### 7 — Environment-Based Config Validation (High)

**Problem**: `run.py` is a growing config monolith and gives no early error on missing env vars
(other than `FLASK_SECRET_KEY`).

**Approach**: Add a `validate_environment()` call at the top of `create_app()`:

```python
def validate_environment():
    required = {
        'SAM_DB_USERNAME': 'SAM database username',
        'SAM_DB_PASSWORD': 'SAM database password',
        'SAM_DB_SERVER':   'SAM database hostname',
    }
    missing = [f"  {k}: {v}" for k, v in required.items() if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            "Missing required environment variables:\n" + "\n".join(missing) +
            "\n\nSee .env.example for a template."
        )
```

A full `src/webapp/config.py` with `DevelopmentConfig` / `ProductionConfig` / `TestingConfig`
classes (see original doc) is a good follow-on refactor but **not** required before launch.

**Files**: `src/webapp/run.py` (immediate), optionally `src/webapp/config.py` later.

---

### Priority 2 — Security Headers

#### 4 — HTTP Security Headers (High)

**Background**: flask-talisman was attempted (`f6840ae`) and immediately reverted (`a5991aa`)
because `force_https=True` caused an infinite redirect loop in the containerised deployment
(HTTP-only container behind a TLS-terminating proxy).

**Recommended approach — manual `after_request` hook** in `run.py`:

```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options']        = 'SAMEORIGIN'
    response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    if not app.debug:
        response.headers['Strict-Transport-Security'] = (
            'max-age=31536000; includeSubDomains'
        )
    return response
```

A Content-Security-Policy header is the most valuable but requires auditing
which CDN domains the templates use before enabling. Defer CSP until a dedicated
audit pass; the four headers above are safe to add immediately.

**Alternative**: configure the nginx reverse proxy to inject headers at the proxy layer
(no code change required). Either approach satisfies the requirement.

**Files**: `src/webapp/run.py` (after_request hook) *or* nginx config.

---

### Priority 3 — Logging

#### 8 — Structured Application Logging (High)

**Problem**: The app currently has no `app.logger` instrumentation; debug info relies on
`print()` or gunicorn's access log.

**Approach**: Create `src/webapp/logging_config.py` with a `configure_logging(app)` function:

- **Development**: human-readable `%(asctime)s %(levelname)s %(name)s — %(message)s`
- **Production**: JSON lines (one object per log event) for log-aggregator ingestion

Wire into `create_app()`:
```python
from webapp.logging_config import configure_logging
configure_logging(app)
app.logger.info("SAM webapp starting — config: %s", os.getenv('FLASK_CONFIG', 'default'))
```

Log level from `LOG_LEVEL` env var (default `INFO`); rotating file if `LOG_FILE` is set.
Suppress noisy third-party loggers (`werkzeug`, `sqlalchemy.engine`) to `WARNING`.

**Files**: `src/webapp/logging_config.py` (new), `src/webapp/run.py`.

#### 9 — Request ID Tracking (High)

**Problem**: No way to correlate a user complaint / browser error with a specific server log line.

**Approach**: Add `before_request` / `after_request` hooks to `run.py`:

```python
import uuid, time
from flask import g, request

@app.before_request
def _set_request_id():
    g.request_id    = request.headers.get('X-Request-ID', str(uuid.uuid4()))
    g.request_start = time.monotonic()

@app.after_request
def _log_request(response):
    elapsed_ms = round((time.monotonic() - g.request_start) * 1000, 1)
    response.headers['X-Request-ID'] = g.request_id
    app.logger.info(
        '%s %s → %s  (%.1f ms)  rid=%s',
        request.method, request.path, response.status_code,
        elapsed_ms, g.request_id,
    )
    if elapsed_ms > 5000:
        app.logger.warning('Slow request: %.1f ms  %s %s', elapsed_ms,
                           request.method, request.path)
    return response
```

Adding `rid=` to every log line makes grepping for a single request trivial.

**Files**: `src/webapp/run.py`.

---

### Priority 4 — Miscellaneous / Other

#### 10 — Security Testing Suite (Medium)

Current coverage (~77%) is strong on happy paths but thin on security boundaries.
Create `tests/security/` with:

- **`test_auth_boundaries.py`**: unauthenticated access → redirect, cross-user data access → 403
- **`test_input_validation.py`**: SQLi in search params returns 200/400 (not 500), no data leak
- **`test_csrf.py`**: POST endpoints without CSRF token are rejected when WTF_CSRF_ENABLED=True

These can be added incrementally; start with auth boundary tests (highest impact, least effort).

#### 11 — Deployment Checklist & Production Env Template (Medium)

Create two files:

**`DEPLOYMENT.md`** — pre-flight checklist covering:
- Security env vars verified (SECRET_KEY, no DISABLE_AUTH)
- SSL/TLS and HSTS checked
- DB credentials use a read-only or service account
- Health endpoint responding (`GET /api/v1/health/`)
- Log paths exist and are writable
- Rollback procedure documented

**`.env.production.example`** — annotated template for all required prod vars:
`FLASK_SECRET_KEY`, `SAM_DB_*`, `STATUS_DB_*`, `LOG_LEVEL`, `LOG_FILE`,
`AUDIT_ENABLED`, `AUDIT_LOG_PATH`.

#### 12 — Metrics & Alerting (Future)

Not needed before launch. Revisit once the application is running in production
and baseline traffic patterns are known.

Options: `prometheus-flask-exporter` + Grafana, or Sentry for error tracking.
Key metrics to capture when the time comes: request latency p95, error rate (5xx),
DB pool utilization (already available via `/api/v1/health/db-pool`).

---

## Implementation Order

```
Next sprint:
  1. #2  — compose.yaml dev-auth comments + webapp service audit      (~10 min)
  2. #7  — validate_environment() in run.py                           (~20 min)
  3. #4  — after_request security headers                             (~15 min)
  4. #8  — logging_config.py + wire into run.py                       (~45 min)
  5. #9  — request ID before/after hooks                              (~15 min)

Before launch:
  6. #11 — DEPLOYMENT.md + .env.production.example                    (~30 min)

Post-launch:
  7. #10 — Security test suite                                        (~3 h)
  8. #12 — Metrics & alerting                                         (future)
```

---

*Last updated: 2026-02-23 — webprod branch*

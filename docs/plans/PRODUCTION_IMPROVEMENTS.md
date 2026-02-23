# Production Readiness — SAM Web Application

## Status at a Glance

| # | Item | Priority | Status |
|---|------|----------|--------|
| 1 | Hardcoded `SECRET_KEY` | Critical | ✅ Done |
| 2 | Dev auth enabled by default | Critical | ✅ Done |
| 3 | Session cookie security flags | Critical | ✅ Done |
| 4 | Security headers | High | 🔴 Open |
| 5 | Gunicorn production config | High | ✅ Done |
| 6 | Health check + DB pool endpoints | High | ✅ Done |
| 7 | Config/env-var checking | High | ✅ Done |
| 8 | Structured logging | High | ✅ Done |
| 9 | Request ID tracking | High | ✅ Done |
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

### 7 — Config / Environment-Variable Validation
`src/config.py`: `BaseConfig` centralises all env-var reading for SAM DB, STATUS DB,
and mail. `BaseConfig.validate()` fails fast at startup with a clear list of missing
variables. `src/webapp/config.py`: `WebappConfig` hierarchy
(`DevelopmentConfig` / `ProductionConfig` / `TestingConfig`) replaces scattered inline
settings in `run.py`. `ProductionConfig.validate()` adds length-check on `FLASK_SECRET_KEY`.
`DEV_ROLE_MAPPING` moved from `run.py` into `DevelopmentConfig`.
CLI entry points (`sam-search`, `sam-admin`, `sam-status`) call `BaseConfig.validate()`
at startup so missing vars produce a clean error before any DB connection is attempted.

### 2 — Dev Auth Enabled by Default
`compose.yaml`: added `# WARNING: dev-only — NEVER set in production` comments on
`DISABLE_AUTH=1` and `DEV_AUTO_LOGIN_USER=benkirk` in the `webdev` service; added
explanatory comment on `DISABLE_AUTH=0` in the `webapp` (production) service.
`containers/webapp/Dockerfile`: added `ENV DISABLE_AUTH=0` and `ENV DEV_AUTO_LOGIN_USER=`
to the `production` stage so the production image actively clears these vars regardless
of build-time environment or accidental inheritance.

### 8 & 9 — Structured Logging + Request ID Tracking
`src/webapp/logging_config.py` (new): `configure_logging(app)` wires a fixed-format
`%(asctime)s %(levelname)-8s %(name)s — %(message)s` handler into `app.logger`.
Console always on; optional rotating file via `LOG_FILE` env var; level from `LOG_LEVEL`
(default `INFO`). Noisy third-party loggers silenced to `WARNING`.
`src/webapp/run.py`: `before_request` hook attaches `g.request_id` (from `X-Request-ID`
header or a new UUID) and `g.request_start`; `after_request` hook logs method, path,
status, elapsed ms, and `rid=` and echoes `X-Request-ID` in the response.
Slow-request warning at >5 000 ms.
`src/sam/session/__init__.py`: replaced `print()` with `logging.debug()` so connection
info is silent at `INFO` and appears in webapp log when `LOG_LEVEL=DEBUG`.
`src/webapp/config.py`: added `LOG_LEVEL` and `LOG_FILE` attrs to `SAMWebappConfig`.

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

### Priority 1 — Security Headers

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

### Priority 3 — Miscellaneous / Other

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
  1. #4  — after_request security headers                             (~15 min)

Before launch:
  2. #11 — DEPLOYMENT.md + .env.production.example                    (~30 min)

Post-launch:
  3. #10 — Security test suite                                        (~3 h)
  4. #12 — Metrics & alerting                                         (future)
```

---

*Last updated: 2026-02-23 — webprod branch*

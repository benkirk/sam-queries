# Production Readiness — SAM Web Application
## Status & Roadmap

*Last updated: 2026-02-23 — webprod branch*

---

## Executive Summary

**8 of 12 original items complete.** All four *Critical* items are done. All
*High* items that are fully self-contained in application code are done. The
remaining work falls into three buckets:

1. **One open item from the original plan** that needs to be done before launch
   (security response headers, #4).
2. **Several new items** identified for a public-facing deployment that were not
   in the original assessment (ProxyFix, least-privilege DB account, CSRF audit,
   container hardening, custom error pages, dependency scanning).
3. **Post-launch items** that are valuable but not blocking (CSP, rate limiting,
   security test suite, metrics/alerting).

**Critical security baseline: complete.** The app can be deployed without fear
of catastrophic auth bypass, session hijacking, or secret leakage. The remaining
open items reduce the attack surface further and improve operational visibility.

---

## Status at a Glance

### Original 12-Item Plan

| # | Item | Priority | Status |
|---|------|----------|--------|
| 1 | Hardcoded `SECRET_KEY` | Critical | ✅ Done |
| 2 | Dev auth enabled by default | Critical | ✅ Done |
| 3 | Session cookie security flags | Critical | ✅ Done |
| 4 | HTTP security headers | High | 🔴 Open — next |
| 5 | Gunicorn production config | High | ✅ Done |
| 6 | Health check + DB pool endpoints | High | ✅ Done |
| 7 | Config / env-var validation | High | ✅ Done |
| 8 | Structured logging | High | ✅ Done |
| 9 | Request ID tracking | High | ✅ Done |
| 10 | Security testing suite | Medium | 🔴 Open |
| 11 | Deployment checklist / env template | Medium | 🔴 Open |
| 12 | Metrics & alerting | Future | 🔴 Open |

### New Items for Public-Facing Deployment

| # | Item | Priority | Status |
|---|------|----------|--------|
| A | Rate limiting | High | 🔴 Open |
| B | CSRF protection audit | High | 🔴 Open |
| C | Content Security Policy | High | 🔴 Open (needs audit first) |
| D | Container: non-root user | Medium | 🔴 Open |
| E | Database least-privilege account | High | 🔴 Open |
| F | Custom error pages | Low | 🔴 Open |
| G | Dependency vulnerability scanning | Medium | 🔴 Open |
| H | ProxyFix + reverse-proxy documentation | Low | 🔴 Open (deferred — topology TBD) |
| I | Session fixation / invalidation audit | Low | 🔴 Open |
| J | Secrets management path | Low | 🔴 Open |

---

## Completed Work

### 1 — Hardcoded `SECRET_KEY`
`FLASK_SECRET_KEY` env var required; `ValueError` raised at startup if unset or
too short (<32 chars). Added to `.env.example` and `containers/webapp/Dockerfile`.
**Commit**: `101e383` · **Files**: `src/webapp/run.py`, `.env.example`

### 2 — Dev Auth Safeguards
`compose.yaml`: `DISABLE_AUTH=1` / `DEV_AUTO_LOGIN_USER=benkirk` in the `webdev`
service carry `# WARNING: dev-only — NEVER set in production` comments. The
`production` Dockerfile stage now explicitly sets `ENV DISABLE_AUTH=0` and
`ENV DEV_AUTO_LOGIN_USER=` so the production image clears these vars at build time.
**Commit**: `1a53690` · **Files**: `compose.yaml`, `containers/webapp/Dockerfile`

### 3 — Session Cookie Security Flags
`SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`,
`SESSION_COOKIE_SAMESITE='Lax'`, `PERMANENT_SESSION_LIFETIME=12 h` wired into
`ProductionConfig`; `DevelopmentConfig` sets `SECURE=False` (no HTTPS in dev).
**Commit**: `101e383` · **File**: `src/webapp/config.py`

### 5 — Gunicorn Production Config
`containers/webapp/gunicorn_config.py`: workers = `(2 × CPU) + 1`,
`preload_app=True`, `max_requests=1000`, logs to stdout/stderr for container log
aggregation. Production Dockerfile CMD updated.
**Commit**: `eb7fa8f` · **File**: `containers/webapp/gunicorn_config.py`

### 6 — Health Check + DB Pool Monitoring
`src/webapp/api/v1/health.py` blueprint at `/api/v1/health/`:
- `GET /` and `GET /ready` — ping both DB binds, 200 / 503
- `GET /live` — immediate alive, no DB call (k8s liveness probe)
- `GET /db-pool` — pool stats for all engines (admin login required)

22 tests in `tests/api/test_health_endpoints.py`.
**Commits**: `f6840ae`, `a5991aa`, `ddd052a`

### 7 — Config / Environment-Variable Validation
`src/config.py`: `BaseConfig` centralises all env-var reading; `BaseConfig.validate()`
fails fast at startup with a clear list of missing variables.
`src/webapp/config.py`: `DevelopmentConfig` / `ProductionConfig` / `TestingConfig`
hierarchy with `ProductionConfig.validate()` checking `FLASK_SECRET_KEY` length.
CLI entry points call `BaseConfig.validate()` before any DB connection attempt.
**Commit**: `a1358ed` · **Files**: `src/config.py`, `src/webapp/config.py`

### 8 & 9 — Structured Logging + Request ID Tracking
`src/webapp/logging_config.py`: `configure_logging(app)` — fixed-format handler
wired into `app.logger`; console always on; optional `RotatingFileHandler` via
`LOG_FILE`; level from `LOG_LEVEL` (default `INFO`); noisy third-party loggers
silenced to `WARNING`.

`src/webapp/run.py`: `_set_request_id()` before-request hook stores `g.request_id`
(from `X-Request-ID` header or new UUID4) and `g.request_start`;
`_log_request()` after-request hook logs `METHOD path → status  elapsed  rid=…`
and echoes `X-Request-ID` in the response. Slow-request warning at >5 000 ms.

`src/sam/session/__init__.py`: replaced bare `print()` with `logging.debug()`.
**Commit**: `1a53690` · **Files**: `src/webapp/logging_config.py`, `src/webapp/run.py`,
`src/webapp/config.py` (`LOG_LEVEL`, `LOG_FILE` attrs), `src/sam/session/__init__.py`

---

## Open Items — Original Plan

---

### #4 — HTTP Security Headers *(High, ~30 min — do next)*

**Background**: flask-talisman was attempted (`f6840ae`) and immediately reverted
(`a5991aa`) because `force_https=True` caused an infinite redirect loop in the
containerised deployment (HTTP-only container behind a TLS-terminating proxy).

**Recommended approach** — manual `after_request` hook in `src/webapp/run.py`:

```python
@app.after_request
def _add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options']        = 'SAMEORIGIN'
    response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    if not app.debug:
        response.headers['Strict-Transport-Security'] = (
            'max-age=31536000; includeSubDomains'
        )
    return response
```

**CSP deferred**: a Content-Security-Policy header is the highest-value header
but requires auditing which CDN origins and inline scripts the templates use
(Bootstrap CDN, jQuery CDN, Font Awesome CDN, chart.js, inline `<script>` blocks
in several templates). See item **C** below.

**Files**: `src/webapp/run.py`

---

### #10 — Security Testing Suite *(Medium, ~3 h)*

Current coverage (~77%) is strong on happy paths but thin on security boundaries.
Create `tests/security/`:

- **`test_auth_boundaries.py`**: unauthenticated access → redirect, cross-user
  data access → 403, admin-only endpoints → 403 for regular users
- **`test_input_validation.py`**: SQLi in search params returns 200/400 (not 500),
  no data leakage in error responses
- **`test_csrf.py`**: state-changing POST endpoints reject requests without a
  valid CSRF token when `WTF_CSRF_ENABLED=True`

Start with auth-boundary tests (highest impact, least effort).

---

### #11 — Deployment Checklist + `.env.production.example` *(Medium, ~30 min)*

**`docs/DEPLOYMENT.md`** — pre-flight checklist:
- `FLASK_SECRET_KEY` set (≥32 chars), `FLASK_CONFIG=production`
- `DISABLE_AUTH` not set / `DEV_AUTO_LOGIN_USER` not set
- DB credentials use a least-privilege account (see item **E**)
- SSL/TLS cert valid, HSTS confirmed
- Health endpoint responding (`GET /api/v1/health/`)
- Log paths exist and are writable
- Rollback procedure documented

**`.env.production.example`** — annotated template for:
`FLASK_SECRET_KEY`, `FLASK_CONFIG`, `SAM_DB_*`, `STATUS_DB_*`,
`LOG_LEVEL`, `LOG_FILE`, `AUDIT_ENABLED`, `AUDIT_LOG_PATH`

---

### #12 — Metrics & Alerting *(Future)*

Not blocking. Revisit once the application is running in production and baseline
traffic patterns are known.

Options: `prometheus-flask-exporter` + Grafana, or Sentry for error tracking.
Key metrics to capture when the time comes: request latency p95, error rate (5xx),
DB pool utilisation (already available via `/api/v1/health/db-pool`).

---

## New Items for Public-Facing Deployment

---

### H — ProxyFix Middleware *(Low, ~15 min — deferred, topology TBD)*

**Status**: deferred. The production deployment topology is not yet defined in
the repo (no nginx service, no reverse-proxy config). ProxyFix is only needed if
gunicorn sits behind a TLS-terminating proxy (nginx, load balancer, etc.) that
forwards `X-Forwarded-Proto`. Without it in that scenario, Flask generates
`http://` URLs and HSTS is ineffective.

**When needed**, one line in `create_app()` in `src/webapp/run.py`:

```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
```

Revisit when the production network topology is finalised.

---

### E — Database Least-Privilege Account *(High, ~20 min)*

**Problem**: `compose.yaml`, `.env`, and the Dockerfile default `.env` all connect
as `root`. In production a compromised app could drop tables or exfiltrate the
entire database.

**Fix**: create a dedicated MySQL account with only the permissions the app needs:

```sql
CREATE USER 'sam_webapp'@'%' IDENTIFIED BY '<strong-password>';
GRANT SELECT, INSERT, UPDATE, DELETE ON sam.* TO 'sam_webapp'@'%';
-- No DROP, CREATE, GRANT, ALTER
FLUSH PRIVILEGES;
```

Update `SAM_DB_USERNAME` / `SAM_DB_PASSWORD` in `.env.production.example` to
reference this account. The test suite uses its own database and is unaffected.

---

### B — CSRF Protection Audit *(High, ~20 min)*

**Problem**: `TestingConfig` sets `WTF_CSRF_ENABLED = False` but
`ProductionConfig` and `DevelopmentConfig` have no explicit setting. CSRF
protection is only active if Flask-WTF's `CSRFProtect(app)` is initialised in
`create_app()`. This needs to be confirmed and, if absent, added.

**Fix**:
1. Check whether `CSRFProtect` is already initialised (grep `run.py`, `extensions.py`).
2. If not, add `from flask_wtf.csrf import CSRFProtect; csrf = CSRFProtect(app)`.
3. Ensure all state-changing forms include `{{ form.hidden_tag() }}` or the
   AJAX endpoints send `X-CSRFToken`.
4. Add a test to `tests/security/test_csrf.py`.

---

### A — Rate Limiting *(High, ~30 min)*

**Problem**: The login endpoint and admin API search are open to brute-force and
scraping without any rate limiting.

**Options**:

| Approach | Pros | Cons |
|----------|------|------|
| nginx `limit_req` | Zero app code, works across all services | Requires nginx config access |
| `flask-limiter` | Per-endpoint granularity, dev-friendly | Adds dependency; needs Redis for multi-worker |

**Recommendation**: use nginx `limit_req_zone` for the `/auth/login` endpoint
at the proxy layer (no code change), and add `flask-limiter` decorators to the
admin search and user-enumeration API endpoints.

**Flask-Limiter minimal setup**:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(get_remote_address, app=app, default_limits=["200/day", "50/hour"])

# On the login route:
@auth_bp.route('/login', methods=['POST'])
@limiter.limit("10/minute")
def login(): ...
```

---

### C — Content Security Policy *(High, needs audit first)*

**Problem**: CSP is the most effective browser-side XSS mitigation but requires
knowing every external origin and every inline script the templates use.

**Known CDN origins** (from templates):
- `https://cdn.jsdelivr.net` (Bootstrap, Popper)
- `https://code.jquery.com`
- `https://cdnjs.cloudflare.com` (Font Awesome)

**Inline scripts**: several templates use `<script>` blocks and `onclick=`
attributes (member-management, jobs-table, admin dashboard). These require either:
- `'unsafe-inline'` (weakens CSP significantly), or
- Refactoring inline scripts to external `.js` files + nonce-based CSP

**Recommended path**:
1. Audit all templates for inline scripts and CDN domains.
2. Start with a report-only CSP header (`Content-Security-Policy-Report-Only`)
   to discover violations without breaking anything.
3. Move inline scripts to the existing JS files in `src/webapp/static/js/`.
4. Enable enforcing CSP once violations are resolved.

---

### D — Container: Non-Root User *(Medium, ~15 min)*

**Problem**: The production Docker image runs as `root`. If the app is
compromised, the attacker has root access to the container.

**Fix**: add to `containers/webapp/Dockerfile` production stage:

```dockerfile
RUN addgroup --system sam && adduser --system --ingroup sam sam
# Ensure log directory is writable by sam user
RUN mkdir -p /var/log/sam && chown sam:sam /var/log/sam
USER sam
```

Verify that `AUDIT_LOG_PATH` and `LOG_FILE` directories are created before the
`USER sam` switch (or mounted with correct permissions via compose volumes).

---

### F — Custom Error Pages *(Low, ~20 min)*

**Problem**: Flask's default `404` / `500` pages expose the framework name and
(in debug mode) the full stack trace. Production error pages should be styled
consistently and reveal nothing about internals.

**Fix**: create `src/webapp/templates/errors/{404,403,500}.html` and register
handlers in `src/webapp/run.py`:

```python
@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(500)
def server_error(e):
    app.logger.exception('Unhandled server error')
    return render_template('errors/500.html'), 500
```

The 500 handler also adds `app.logger.exception()` coverage for unhandled errors
that currently produce no log output.

---

### G — Dependency Vulnerability Scanning *(Medium, ~15 min setup)*

**Problem**: No automated scanning for known CVEs in installed Python packages.

**Fix**: add `pip-audit` to the development dependency set and to CI:

```bash
pip install pip-audit
pip-audit  # scans current environment
```

No application code changes required. Can also be wired into GitHub Actions or
a pre-commit hook.

---

### I — Session Fixation & Invalidation Audit *(Low, audit only)*

Flask-Login 0.5+ regenerates the session cookie on `login_user()` and invalidates
it on `logout_user()` by default. Verify this is behaving correctly with a test:

```python
def test_login_regenerates_session(client):
    pre_cookie = client.get_cookie('session')
    client.post('/auth/login', data={...})
    post_cookie = client.get_cookie('session')
    assert pre_cookie != post_cookie  # cookie was regenerated
```

No code changes expected; this is a confirmation test.

---

### J — Secrets Management Path *(Low, process documentation)*

The current approach (`.env` file, Docker env vars) is acceptable for initial
deployment. Document a path to stronger secret management when the deployment
matures:

- **Short term**: Docker Compose secrets (`secrets:` key) mounts files at
  `/run/secrets/` rather than env vars
- **Medium term**: HashiCorp Vault with the Vault Agent Injector or
  `hvac` Python client
- **Long term**: Cloud-native secret stores (AWS Secrets Manager,
  Azure Key Vault, GCP Secret Manager) if the deployment moves to cloud

---

## Recommended Implementation Order

```
Before launch — critical path:
  1. #4      — Security headers                       (~15 min)
  2. E       — Database least-privilege account       (~20 min)
  3. B       — CSRF audit / CSRFProtect confirm       (~20 min)
  4. #11     — DEPLOYMENT.md + .env.production.ex    (~30 min)

Before launch — recommended:
  5. D       — Container non-root user                (~15 min)
  6. F       — Custom error pages (+ 500 logging)    (~20 min)
  7. G       — pip-audit added to CI                  (~15 min)

Post-launch:
  8. C       — Content Security Policy               (~2 h audit + 30 min impl)
  9. A       — Rate limiting                          (~30 min)
  10. #10    — Security testing suite                (~3 h)
  11. I      — Session fixation confirmation test    (~30 min)
  12. #12    — Metrics & alerting                    (future)
  13. H      — ProxyFix (if reverse proxy is added)  (~15 min, topology TBD)
  14. J      — Secrets management upgrade            (future)
```

---

## Reference: Key Files

| File | Role |
|------|------|
| `src/webapp/run.py` | Security headers, ProxyFix, request hooks |
| `src/webapp/config.py` | Config hierarchy, env-var defaults |
| `src/webapp/logging_config.py` | App logging setup |
| `src/webapp/api/v1/health.py` | Health + DB pool endpoints |
| `containers/webapp/Dockerfile` | Non-root user, dev-auth ENV overrides |
| `containers/webapp/gunicorn_config.py` | Production WSGI server config |
| `compose.yaml` | Service definitions, dev vs. prod separation |
| `src/config.py` | `BaseConfig` with `validate()` |

---

*Branch: webprod · Test status: 494 passed, 19 skipped, 2 xpassed*

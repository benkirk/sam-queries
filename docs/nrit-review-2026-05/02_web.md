# Phase 2 — Web (`src/webapp/`)

> Largest and most user-facing subsystem. Flask + Jinja2 + HTMX + Flask-Admin, OIDC (Entra), RBAC, API, audit log, rate limiter, caching. Highest blast radius for a directional review.

## Scope

- `src/webapp/` (all subtrees: `admin/`, `api/`, `audit/`, `auth/`, `caching/`, `dashboards/`, `limiter/`, `templates/`, `static/`, `utils/`)
- `src/webapp/config.py`, `extensions.py`, `logging_config.py`, `run.py`
- In-tree design docs: `DESIGN.md`, `IMPLEMENTATION_SUMMARY.md`, `QUICK_START_RBAC.md`, `REFACTORING_PLAN.md`
- Tests: `tests/api/`, webapp-related entries in `tests/unit/` and `tests/integration/`
- `compose.yaml` `webapp` and `webdev` services (only as they relate to running the app)

## Method

1. Sketch the request lifecycle: middleware → auth decorator → route → schema → ORM → response.
2. Walk the OIDC sign-in path and the dev-auto-login path side-by-side.
3. Walk RBAC enforcement: `@require_project_access`, `@require_project_member_access(Permission.X)`. Look for any route that bypasses or hand-rolls authz.
4. Audit log: what's logged, what's not, can it be tampered with?
5. Form validation pattern: do POST/PUT routes (HTMX + API) use `sam.schemas.forms`? Any inline `datetime.strptime` / `float()` ladders?
6. Templates / HTMX: a11y baseline (semantic HTML, alt text, focus management, form labels).
7. Rate limiter: applied where? Bypassable by which paths?
8. Caching: where, with what invalidation, any stampede risk?
9. Run the webapp via `docker compose up` and click through 1–2 main flows in a browser.

## Lenses applied

- Architecture
- Security (primary focus)
- Testing
- Performance
- UX / A11y
- Operability

## Findings

*TBD*

### Architecture

*TBD*

### Security

*TBD*

### Testing

*TBD*

### Performance

*TBD*

### UX / A11y

*TBD*

### Operability

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*

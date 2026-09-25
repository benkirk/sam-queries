"""Health check and application readiness endpoints.

Intended consumers:
  GET /api/v1/health/       — monitoring: every bind, plus ORM <-> database
                              schema drift; 503 on any failure
  GET /api/v1/health/live   — Kubernetes liveness probe (no DB call)
  GET /api/v1/health/ready  — Kubernetes readiness probe: 503 only when the
                              primary (``sam``) bind is down; a secondary bind
                              is reported as degraded. ``?strict=1`` restores
                              503-on-any-bind (compose startup ordering).
  GET /api/v1/health/db-pool — admin: connection pool statistics

See ``readiness`` for why neither drift nor a secondary bind may empty the
Service.
"""
import time
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required
from sqlalchemy import text

from webapp.extensions import db
from webapp.request_timing import RequestProfile
from webapp.api.helpers import register_error_handlers
from webapp.limiter import limiter as _rate_limit
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.config_inspect import (
    classify_connection_error,
    pool_stats,
    schema_drift,
)
from webapp.utils.engine_inventory import engine_sources

bp = Blueprint('api_health', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ping_engine(engine):
    """Ping a SQLAlchemy engine with SELECT 1.

    Returns (ok: bool, latency_ms: float | None, error: str | None).
    """
    profile = RequestProfile.current()
    start = datetime.now()
    try:
        connect_t0 = time.perf_counter()
        with engine.connect() as conn:
            # Connection acquisition (pool checkout) is where a /ready stall
            # hides — the SELECT 1 itself is trivial. Attribute it to `pool` so
            # the slow-request line reads pool=… instead of an unexplained rest.
            if profile is not None:
                profile.add_pool((time.perf_counter() - connect_t0) * 1000.0)
            conn.execute(text('SELECT 1'))
        latency_ms = round((datetime.now() - start).total_seconds() * 1000, 2)
        return True, latency_ms, None
    except Exception as exc:
        return False, None, str(exc)


# Binds whose failure means the app cannot serve at all. Everything else is a
# secondary bind: reported, and fatal only under `strict`.
_REQUIRED_BINDS = frozenset({'sam'})


def _collect_health(include_schema=False, strict=False):
    """Ping every configured bind; return (healthy, degraded, checks).

    A required bind failing is unhealthy. A secondary bind failing is
    degraded, and unhealthy only when ``strict``. With ``include_schema``,
    additionally diff the ORM against the live ``sam`` schema (``sam_schema``).
    """
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status')
    if ss_engine:
        engines['system_status'] = ss_engine

    checks = {}
    healthy = True
    degraded = False

    for name, engine in engines.items():
        required = name in _REQUIRED_BINDS
        ok, latency_ms, error = _ping_engine(engine)
        checks[name] = {'status': 'healthy' if ok else 'unhealthy', 'required': required}
        if ok:
            checks[name]['latency_ms'] = latency_ms
        else:
            checks[name]['error'] = error
            if required or strict:
                healthy = False
            else:
                degraded = True

    # Only meaningful when the bind is reachable; a drift probe against a
    # dead connection would report 'unknown' and add nothing.
    if include_schema and checks['sam']['status'] == 'healthy':
        checks['sam_schema'] = schema_drift(db.engine)
        if checks['sam_schema']['status'] == 'unhealthy':
            healthy = False

    return healthy, degraded, checks


def _health_response(include_schema, strict):
    """Build the shared (payload, status_code) pair for / and /ready."""
    healthy, degraded, checks = _collect_health(include_schema=include_schema, strict=strict)
    if healthy and degraded:
        failing = ', '.join(f"{n}: {c.get('error')}" for n, c in checks.items()
                            if c['status'] == 'unhealthy')
        current_app.logger.warning('readiness degraded (still serving): %s', failing)
    status = 'unhealthy' if not healthy else ('degraded' if degraded else 'healthy')
    return jsonify({
        'status': status,
        'service': 'sam-webapp',
        'timestamp': datetime.now().isoformat(),
        'checks': checks,
    }), 200 if healthy else 503


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@_rate_limit.limiter.exempt
def health():
    """Health check for load balancers — pings all DB binds, plus schema drift.

    Returns 200 when all checks pass, 503 if any fail. This is the endpoint
    monitoring should watch: it is the *only* one that reports ORM <-> database
    schema drift, the failure mode that took the site down on 2026-08-10 while
    every connectivity probe stayed green.

    Public endpoint (no login required). Exempt from rate limiting so
    LB/Kubernetes probes never get throttled.
    """
    return _health_response(include_schema=True, strict=True)


@bp.route('/live', methods=['GET'])
@_rate_limit.limiter.exempt
def liveness():
    """Kubernetes liveness probe — confirms the process is running.

    No DB calls. Returns immediately. Public endpoint.
    """
    return jsonify({'status': 'alive', 'service': 'sam-webapp'}), 200


@bp.route('/ready', methods=['GET'])
@_rate_limit.limiter.exempt
def readiness():
    """Kubernetes readiness probe — confirms the app can serve traffic.

    503 only when the primary ``sam`` bind is down. Two things are reported
    but never fail readiness, for the same reason: they affect every replica
    identically, so failing readiness on them empties the Service and turns
    a degraded site into an unreachable one.

    - Schema drift (``/`` carries it): a paging signal, not a "take this pod
      out" signal, and failing on it would stall the deploy that ships the fix.
    - A secondary bind (``system_status``): on 2026-09-13 a csg-postgres
      rolling restart failed this ping on both pods for 3.5 minutes while
      ``sam`` answered in 4 ms, and the whole site was unreachable for the
      duration. Now it renders as ``degraded`` and the status pages fail soft.

    ``?strict=1`` restores 503-on-any-bind, for compose startup ordering.
    Public endpoint.
    """
    strict = request.args.get('strict') == '1'
    return _health_response(include_schema=False, strict=strict)


@bp.route('/db-pool', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def db_pool():
    """Connection pool statistics for every engine in ``engine_sources()``.

    Keys: ``sam``, ``system_status``, ``job_history:<machine>`` and
    ``fs_scans:<database>/<collection>`` (one pool per collection engine).
    ``error_detail`` tells server-side slot exhaustion (pool tuning will *not*
    fix it) from local pool exhaustion. Requires SYSTEM_ADMIN.
    """
    engines = {}
    for src in engine_sources(current_app, db):
        for schema, engine in src.engines.items():
            name = src.key.replace('.', ':', 1)
            engines[f'{name}/{schema}' if schema else name] = engine

    pools = {}
    for name, engine in engines.items():
        entry = pool_stats(engine.pool)
        ok, latency_ms, err = _ping_engine(engine)
        entry['reachable'] = ok
        entry['latency_ms'] = latency_ms
        entry['error'] = err
        entry['error_detail'] = classify_connection_error(err)
        pools[name] = entry

    return jsonify({
        'pools':     pools,
        'timestamp': datetime.now().isoformat(),
    }), 200

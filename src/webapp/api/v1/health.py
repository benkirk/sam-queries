"""Health check and application readiness endpoints.

Intended consumers:
  GET /api/v1/health/       — load balancers + monitoring: connectivity AND
                              ORM ↔ database schema drift
  GET /api/v1/health/live   — Kubernetes liveness probe (no DB call)
  GET /api/v1/health/ready  — Kubernetes readiness probe (connectivity only)
  GET /api/v1/health/db-pool — admin: connection pool statistics

``/`` and ``/ready`` deliberately differ: only ``/`` fails on schema drift.
See ``readiness`` for why a drifted schema must not empty the Service.
"""
from datetime import datetime

from flask import Blueprint, current_app, jsonify
from flask_login import login_required
from sqlalchemy import text

from webapp.extensions import db
from webapp.api.helpers import register_error_handlers
from webapp.limiter import limiter as _rate_limit
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.config_inspect import (
    classify_connection_error,
    pool_stats,
    schema_drift,
)

bp = Blueprint('api_health', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ping_engine(engine):
    """Ping a SQLAlchemy engine with SELECT 1.

    Returns (ok: bool, latency_ms: float | None, error: str | None).
    """
    start = datetime.now()
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        latency_ms = round((datetime.now() - start).total_seconds() * 1000, 2)
        return True, latency_ms, None
    except Exception as exc:
        return False, None, str(exc)


def _collect_health(include_schema=False):
    """Ping all configured DB engines and return a (healthy, checks) tuple.

    With ``include_schema``, additionally diff the ORM's mapped columns
    against the live ``sam`` schema and report it as a ``sam_schema`` check.
    See ``schema_drift`` for why connectivity alone is not enough.
    """
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status')
    if ss_engine:
        engines['system_status'] = ss_engine

    checks = {}
    healthy = True

    for name, engine in engines.items():
        ok, latency_ms, error = _ping_engine(engine)
        checks[name] = {'status': 'healthy' if ok else 'unhealthy'}
        if ok:
            checks[name]['latency_ms'] = latency_ms
        else:
            checks[name]['error'] = error
            healthy = False

    # Only meaningful when the bind is reachable; a drift probe against a
    # dead connection would report 'unknown' and add nothing.
    if include_schema and checks['sam']['status'] == 'healthy':
        checks['sam_schema'] = schema_drift(db.engine)
        if checks['sam_schema']['status'] == 'unhealthy':
            healthy = False

    return healthy, checks


def _health_response(include_schema):
    """Build the shared (payload, status_code) pair for / and /ready."""
    healthy, checks = _collect_health(include_schema=include_schema)
    return jsonify({
        'status': 'healthy' if healthy else 'unhealthy',
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
    monitoring should watch: it is the *only* one that reports ORM ↔ database
    schema drift, the failure mode that took the site down on 2026-08-10 while
    every connectivity probe stayed green.

    Public endpoint (no login required). Exempt from rate limiting so
    LB/Kubernetes probes never get throttled.
    """
    return _health_response(include_schema=True)


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

    Connectivity only: deliberately does NOT include the schema-drift check
    that ``/`` reports. Drift affects every replica of an image identically,
    so failing readiness on it would empty the Service and turn a degraded
    site into an unreachable one — and stall the very rolling deploy that
    ships the fix. Drift is a paging signal, not a "take this pod out"
    signal; ``/`` carries it.

    Public endpoint.
    """
    return _health_response(include_schema=False)


@bp.route('/db-pool', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def db_pool():
    """Connection pool statistics for all configured DB engines.

    Returns pool size, utilization, overflow, and a health assessment
    for each configured engine bind. Includes the hpc-usage-queries
    plugin engines (``job_history:<machine>``) when the plugin is loaded.
    Each entry includes an ``error_detail`` classifier when the engine
    cannot be pinged, distinguishing server-side slot exhaustion (which
    pool tuning will *not* fix) from local pool exhaustion.
    Requires SYSTEM_ADMIN permission.
    """
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status')
    if ss_engine:
        engines['system_status'] = ss_engine

    # hpc-usage-queries plugin engines (registered on app.extensions
    # by webapp.jobs.init_job_history at startup; empty when disabled).
    jh_state = current_app.extensions.get('hpc_usage_queries') or {}
    for machine, engine in (jh_state.get('engines') or {}).items():
        engines[f'job_history:{machine}'] = engine

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

"""Health check and application readiness endpoints.

Intended consumers:
  GET /api/v1/health/       — load balancer health checks
  GET /api/v1/health/live   — Kubernetes liveness probe (no DB call)
  GET /api/v1/health/ready  — Kubernetes readiness probe
  GET /api/v1/health/db-pool — admin: connection pool statistics
"""
from datetime import datetime

from flask import Blueprint, jsonify
from flask_login import login_required
from sqlalchemy import text

from webapp.extensions import db
from webapp.api.helpers import register_error_handlers
from webapp.limiter import limiter as _rate_limit
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.config_inspect import pool_stats

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


def _collect_health():
    """Ping all configured DB engines and return a (healthy, checks) tuple."""
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

    return healthy, checks


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@_rate_limit.limiter.exempt
def health():
    """Health check for load balancers — pings all DB binds.

    Returns 200 when all checks pass, 503 if any fail.
    Public endpoint (no login required). Exempt from rate limiting so
    LB/Kubernetes probes never get throttled.
    """
    healthy, checks = _collect_health()
    return jsonify({
        'status': 'healthy' if healthy else 'unhealthy',
        'service': 'sam-webapp',
        'timestamp': datetime.now().isoformat(),
        'checks': checks,
    }), 200 if healthy else 503


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

    Delegates to the full DB health check. Public endpoint.
    """
    return health()


@bp.route('/db-pool', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def db_pool():
    """Connection pool statistics for all configured DB engines.

    Returns pool size, utilisation, overflow, and a health assessment
    for each configured engine bind. Requires SYSTEM_ADMIN permission.
    """
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status')
    if ss_engine:
        engines['system_status'] = ss_engine

    return jsonify({
        'pools':     {name: pool_stats(engine.pool) for name, engine in engines.items()},
        'timestamp': datetime.now().isoformat(),
    }), 200

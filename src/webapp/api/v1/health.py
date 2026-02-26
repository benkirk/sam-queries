"""Health check and application readiness endpoints.

Intended consumers:
  GET /api/v1/health/       — load balancer health checks
  GET /api/v1/health/live   — Kubernetes liveness probe (no DB call)
  GET /api/v1/health/ready  — Kubernetes readiness probe
  GET /api/v1/health/db-pool — admin: connection pool statistics
"""
from datetime import datetime

from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from webapp.extensions import db
from webapp.api.helpers import register_error_handlers

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
def health():
    """Health check for load balancers — pings all DB binds.

    Returns 200 when all checks pass, 503 if any fail.
    Public endpoint (no login required).
    """
    healthy, checks = _collect_health()
    return jsonify({
        'status': 'healthy' if healthy else 'unhealthy',
        'service': 'sam-webapp',
        'timestamp': datetime.now().isoformat(),
        'checks': checks,
    }), 200 if healthy else 503


@bp.route('/live', methods=['GET'])
def liveness():
    """Kubernetes liveness probe — confirms the process is running.

    No DB calls. Returns immediately. Public endpoint.
    """
    return jsonify({'status': 'alive', 'service': 'sam-webapp'}), 200


@bp.route('/ready', methods=['GET'])
def readiness():
    """Kubernetes readiness probe — confirms the app can serve traffic.

    Delegates to the full DB health check. Public endpoint.
    """
    return health()


@bp.route('/db-pool', methods=['GET'])
@login_required
def db_pool():
    """Connection pool statistics for all configured DB engines (admin only).

    Returns pool size, utilisation, overflow, and a health assessment
    for each configured engine bind.
    """
    if 'admin' not in current_user.roles:
        return jsonify({'error': 'Admin access required'}), 403

    def _pool_stats(pool):
        size = pool.size()
        checked_out = pool.checkedout()
        utilization_pct = round(checked_out / size * 100, 1) if size else 0
        return {
            'pool_size':       size,
            'checked_in':      pool.checkedin(),
            'checked_out':     checked_out,
            'overflow':        pool.overflow(),
            'max_overflow':    pool._max_overflow,
            'utilization_pct': utilization_pct,
            'health': 'warning' if utilization_pct > 80 else 'healthy',
        }

    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status')
    if ss_engine:
        engines['system_status'] = ss_engine

    return jsonify({
        'pools':     {name: _pool_stats(engine.pool) for name, engine in engines.items()},
        'timestamp': datetime.now().isoformat(),
    }), 200

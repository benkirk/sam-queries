"""
Queue API endpoints (v1).

Provides the active job-queue configuration consumed by batch schedulers and
systems-integration tooling.  Reproduces the output of the legacy SAM Java
endpoints:
  GET /api/protected/admin/ssg/queue
  GET /api/protected/admin/ssg/queue/<Resource>

Example usage:
    GET  /api/v1/queue/                — all active queues on all active resources
    GET  /api/v1/queue/Derecho          — queues for a single resource
    GET  /api/v1/queue/Derecho%20GPU    — resource with space (URL-encoded)
    POST /api/v1/queue/refresh          — invalidate cache

Response format::

    {
        "name": "queues",
        "resources": [
            {
                "resourceName": "Derecho",
                "queues": [
                    {
                        "queueName": "main",
                        "wallClockHoursLimit": 12.0,
                        "startDate": "2023-01-01T00:00:00",
                        "endDate": null,
                        "cosId": 5
                    }
                ]
            }
        ]
    }
"""

from flask import Blueprint, jsonify, abort, request
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache, csrf
from webapp.api.helpers import register_error_handlers
from sam.queries.queue_access import get_queue_data

bp = Blueprint('api_queue', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Memoized query wrapper (keyed on resource_name)
# ---------------------------------------------------------------------------

@cache.memoize()
def _queue_data(resource_name=None):
    return get_queue_data(db.session, resource_name=resource_name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_RESOURCES)
@cache.cached(query_string=True)
def get_queues():
    """
    Return active queues for all active resources.

    Returns:
        JSON with "name" (``"queues"``) and "resources" keys, each resource
        containing a list of queues with wallclock limits and class-of-service.
    """
    return jsonify(_queue_data())


@bp.route('/<path:resource_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_RESOURCES)
@cache.cached(query_string=True)
def get_queues_for_resource(resource_name: str):
    """
    Return active queues filtered to a single resource.

    Args:
        resource_name: Resource name (e.g. "Derecho", "Derecho GPU").
                       Names with spaces should be URL-encoded by the caller.

    Returns:
        JSON with the same schema as the all-resources endpoint but containing
        only the specified resource.
        404 if the resource name is not recognized or has no active queues.
    """
    result = _queue_data(resource_name)
    if not result.get('resources'):
        abort(404, f'Resource {resource_name!r} not found or has no active queues')
    return jsonify(result)


@bp.route('/refresh', methods=['POST'])
@csrf.exempt          # token path is Basic-auth (no cookies); the session
                      # path losing CSRF on an idempotent cache refresh is
                      # an accepted trade-off
@login_or_token_required(Permission.VIEW_RESOURCES)
def refresh_cache():
    """
    Invalidate the queue cache.

    Forces the next GET request to recompute from the database.

    Returns:
        JSON with {"status": "ok"}
    """
    cache.delete_memoized(get_queues)
    cache.delete_memoized(get_queues_for_resource)
    cache.delete_memoized(_queue_data)
    cache.clear()
    return jsonify({'status': 'ok'})

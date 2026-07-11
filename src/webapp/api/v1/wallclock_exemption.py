"""
Wallclock Exemption API endpoints (v1).

Provides the active per-user queue wallclock overrides consumed by batch
schedulers.  Reproduces the output of the legacy SAM Java endpoint:
  GET /api/protected/admin/ssg/wallClockExemption

The legacy endpoint has no per-resource variant; a symmetric
``/<Resource>`` filter is added here for convenience (parallel to the queue API).

Example usage:
    GET  /api/v1/wallclock_exemption/                — all active exemptions
    GET  /api/v1/wallclock_exemption/Derecho          — filtered to one resource
    GET  /api/v1/wallclock_exemption/Derecho%20GPU    — resource with space
    POST /api/v1/wallclock_exemption/refresh          — invalidate cache

Response format::

    {
        "name": "exemptions",
        "resources": [
            {
                "resourceName": "Derecho",
                "queues": [
                    {
                        "queueName": "main",
                        "limits": [
                            {"username": "benkirk", "wallClockLimit": 48.0}
                        ]
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
from sam.queries.wallclock_exemption_access import get_wallclock_exemption_data

bp = Blueprint('api_wallclock_exemption', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Memoized query wrapper (keyed on resource_name)
# ---------------------------------------------------------------------------

@cache.memoize()
def _wallclock_exemption_data(resource_name=None):
    return get_wallclock_exemption_data(db.session, resource_name=resource_name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_RESOURCES)
@cache.cached(query_string=True)
def get_wallclock_exemptions():
    """
    Return active wallclock exemptions for all resources.

    Returns:
        JSON with "name" (``"exemptions"``) and "resources" keys, each resource
        containing queues with per-user wallclock limits.
    """
    return jsonify(_wallclock_exemption_data())


@bp.route('/<path:resource_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_RESOURCES)
@cache.cached(query_string=True)
def get_wallclock_exemptions_for_resource(resource_name: str):
    """
    Return active wallclock exemptions filtered to a single resource.

    Args:
        resource_name: Resource name (e.g. "Derecho", "Derecho GPU").
                       Names with spaces should be URL-encoded by the caller.

    Returns:
        JSON with the same schema as the all-resources endpoint but containing
        only the specified resource.
        404 if the resource name is not recognized or has no active exemptions.
    """
    result = _wallclock_exemption_data(resource_name)
    if not result.get('resources'):
        abort(404, f'Resource {resource_name!r} not found or has no active exemptions')
    return jsonify(result)


@bp.route('/refresh', methods=['POST'])
@csrf.exempt          # token path is Basic-auth (no cookies); the session
                      # path losing CSRF on an idempotent cache refresh is
                      # an accepted trade-off
@login_or_token_required(Permission.VIEW_RESOURCES)
def refresh_cache():
    """
    Invalidate the wallclock exemption cache.

    Forces the next GET request to recompute from the database.

    Returns:
        JSON with {"status": "ok"}
    """
    cache.delete_memoized(get_wallclock_exemptions)
    cache.delete_memoized(get_wallclock_exemptions_for_resource)
    cache.delete_memoized(_wallclock_exemption_data)
    cache.clear()
    return jsonify({'status': 'ok'})

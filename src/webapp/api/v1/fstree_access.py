"""
FairShare Tree API endpoints (v1).

Provides the PBS fairshare tree data consumed by batch schedulers and LDAP
tooling.  Reproduces the output of the legacy SAM Java endpoint:
  GET /api/protected/admin/ssg/fairShareTree/v3/<Resource>

Example usage:
    GET /api/v1/fstree_access/              — all HPC+DAV resources
    GET /api/v1/fstree_access/Derecho       — single resource
    GET /api/v1/fstree_access/Derecho%20GPU — resource with space (URL-encoded)
    POST /api/v1/fstree_access/refresh      — invalidate cache

Response format (partial):
    {
        "name": "fairShareTree",
        "facilities": [
            {
                "name": "CSL",
                "description": "Climate Simulation Laboratory",
                "fairSharePercentage": 31.0,
                "allocationTypes": [
                    {
                        "name": "C_CSL",
                        "description": "CSL",
                        "fairSharePercentage": 0.0,
                        "projects": [
                            {
                                "projectCode": "P93300041",
                                "active": true,
                                "resources": [
                                    {
                                        "name": "Derecho",
                                        "accountStatus": "Normal",
                                        "cutoffThreshold": 100,
                                        "adjustedUsage": 48883597,
                                        "balance": 2616402,
                                        "allocationAmount": 51500000,
                                        "users": [
                                            {"username": "travisa", "uid": 29642}
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
"""

from flask import Blueprint, jsonify, abort
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache
from webapp.api.helpers import register_error_handlers
from sam.queries.fstree_access import get_fstree_data

bp = Blueprint('api_fstree_access', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(timeout=300, query_string=True)
def get_fstree():
    """
    Return the fairshare tree for all HPC+DAV resources.

    Returns:
        JSON with "name" and "facilities" keys, each facility containing
        allocationTypes, each with a projects list including per-resource
        balance and user data.
    """
    return jsonify(get_fstree_data(db.session))


@bp.route('/<path:resource_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(timeout=300, query_string=True)
def get_fstree_resource(resource_name: str):
    """
    Return the fairshare tree filtered to a single resource.

    Args:
        resource_name: Resource name (e.g. "Derecho", "Derecho GPU").
                       Names with spaces should be URL-encoded by the caller
                       (e.g. "Derecho%20GPU") — Flask decodes automatically.

    Returns:
        JSON with the same schema as the all-resources endpoint but containing
        only entries for the specified resource.
        404 if the resource name is not recognized or has no data.
    """
    result = get_fstree_data(db.session, resource_name=resource_name)
    if not result.get('facilities'):
        abort(404, f'Resource {resource_name!r} not found or has no fairshare data')
    return jsonify(result)


@bp.route('/refresh', methods=['POST'])
@login_or_token_required(Permission.VIEW_PROJECTS)
def refresh_cache():
    """
    Invalidate the fairshare tree cache.

    Forces the next GET request to recompute from the database.

    Returns:
        JSON with {"status": "ok"}
    """
    cache.delete_memoized(get_fstree)
    cache.delete_memoized(get_fstree_resource)
    cache.clear()
    return jsonify({'status': 'ok'})

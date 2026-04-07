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

    GET /api/v1/fstree_access/projects/                      — all projects (project-keyed view)
    GET /api/v1/fstree_access/projects/SCSG0001              — single project
    GET /api/v1/fstree_access/projects/?resource=Derecho     — filtered by resource
    GET /api/v1/fstree_access/projects/SCSG0001?resource=Derecho

    GET /api/v1/fstree_access/users/                         — all users (user-keyed view)
    GET /api/v1/fstree_access/users/benkirk                  — single user
    GET /api/v1/fstree_access/users/?resource=Derecho        — filtered by resource
    GET /api/v1/fstree_access/users/benkirk?resource=Derecho

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

from flask import Blueprint, jsonify, abort, request
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache
from webapp.api.helpers import register_error_handlers
from sam.queries.fstree_access import get_fstree_data, get_project_fsdata, get_user_fsdata

bp = Blueprint('api_fstree_access', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Memoized query wrappers
# (keyed on resource_name so all item endpoints share one cached computation
#  per resource filter — @cache.memoize differs from @cache.cached in that it
#  keys on function arguments rather than the request URL)
# ---------------------------------------------------------------------------

@cache.memoize()
def _fstree_data(resource_name=None):
    return get_fstree_data(db.session, resource_name=resource_name)


@cache.memoize()
def _project_fstree_data(resource_name=None):
    return get_project_fsdata(db.session, resource_name=resource_name)


@cache.memoize()
def _user_fstree_data(resource_name=None):
    return get_user_fsdata(db.session, resource_name=resource_name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_fstree():
    """
    Return the fairshare tree for all HPC+DAV resources.

    Returns:
        JSON with "name" and "facilities" keys, each facility containing
        allocationTypes, each with a projects list including per-resource
        balance and user data.
    """
    return jsonify(_fstree_data())


@bp.route('/<path:resource_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
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
    result = _fstree_data(resource_name)
    if not result.get('facilities'):
        abort(404, f'Resource {resource_name!r} not found or has no fairshare data')
    return jsonify(result)


@bp.route('/projects/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_project_fstree():
    """
    Return fairshare data reorganized by project code.

    Optional query parameter:
        resource: Filter to a single resource (e.g. ``?resource=Derecho``).

    Returns:
        JSON with "name" (``"projectFairShareData"``) and "projects" keys.
        Each project entry contains active, facility, allocationType,
        allocationTypeDescription, and resources (sorted by name).
    """
    resource_name = request.args.get('resource')
    return jsonify(_project_fstree_data(resource_name))


@bp.route('/projects/<projcode>', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_project_fstree_item(projcode: str):
    """
    Return fairshare data for a single project.

    Args:
        projcode: Project code (e.g. "SCSG0001").

    Optional query parameter:
        resource: Filter to a single resource (e.g. ``?resource=Derecho``).

    Returns:
        JSON dict keyed by projcode with the project's fairshare entry.
        404 if the project is not found in the fairshare data.
    """
    resource_name = request.args.get('resource')
    result = _project_fstree_data(resource_name)
    proj = result['projects'].get(projcode)
    if proj is None:
        abort(404, f'Project {projcode!r} not found in fairshare data')
    return jsonify({projcode: proj})


@bp.route('/users/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_user_fstree():
    """
    Return fairshare data reorganized by username.

    Optional query parameter:
        resource: Filter to a single resource (e.g. ``?resource=Derecho``).

    Returns:
        JSON with "name" (``"userFairShareData"``) and "users" keys.
        Each user entry contains uid and projects (keyed by projcode).
    """
    resource_name = request.args.get('resource')
    return jsonify(_user_fstree_data(resource_name))


@bp.route('/users/<username>', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_user_fstree_item(username: str):
    """
    Return fairshare data for a single user.

    Args:
        username: Unix username (e.g. "benkirk").

    Optional query parameter:
        resource: Filter to a single resource (e.g. ``?resource=Derecho``).

    Returns:
        JSON dict keyed by username with the user's fairshare entry.
        404 if the user has no active allocations in the fairshare data.
    """
    resource_name = request.args.get('resource')
    result = _user_fstree_data(resource_name)
    user = result['users'].get(username)
    if user is None:
        abort(404, f'User {username!r} not found in fairshare data')
    return jsonify({username: user})


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
    cache.delete_memoized(get_project_fstree)
    cache.delete_memoized(get_project_fstree_item)
    cache.delete_memoized(get_user_fstree)
    cache.delete_memoized(get_user_fstree_item)
    cache.delete_memoized(_fstree_data)
    cache.delete_memoized(_project_fstree_data)
    cache.delete_memoized(_user_fstree_data)
    cache.clear()
    return jsonify({'status': 'ok'})

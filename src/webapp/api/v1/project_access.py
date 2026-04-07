"""
Project Access API endpoints (v1).

Provides per-access-branch project group status consumed by LDAP provisioning
and other tooling.  Reproduces the output of the legacy SAM Java endpoint:
  GET /api/protected/admin/sysacct/groupstatus/{access_branch}

Example usage:
    GET /api/v1/project_access/           — all access branches
    GET /api/v1/project_access/hpc        — single branch
    GET /api/v1/project_access/hpc-data   — single branch
    POST /api/v1/project_access/refresh   — invalidate cache

Response format (all-branches):
    {
        "hpc": [
            {
                "groupName": "wyom0218",
                "panel": "WRAP",
                "autoRenewing": false,
                "projectActive": true,
                "status": "ACTIVE",
                "expiration": "2028-07-01",
                "resourceGroupStatuses": [
                    {"resourceName": "Derecho",  "endDate": "2028-07-01"},
                    {"resourceName": "Casper",   "endDate": "2028-07-01"}
                ]
            },
            ...
        ],
        "hpc-data": [...],
        "hpc-dev":  [...]
    }
"""

from flask import Blueprint, jsonify, abort
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache
from webapp.api.helpers import register_error_handlers
from sam.queries.project_access import get_project_group_status, ACCESS_GRACE_PERIOD

bp = Blueprint('api_project_access', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_project_access():
    """
    Return project group status for all access branches.

    Returns:
        JSON dict keyed by access branch name, each value a list of project
        group status objects containing groupName, panel, autoRenewing,
        projectActive, status, expiration, and resourceGroupStatuses.
    """
    return jsonify(get_project_group_status(db.session,
                                            grace_period_days=ACCESS_GRACE_PERIOD))


@bp.route('/<access_branch_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_project_access_branch(access_branch_name: str):
    """
    Return project group status for a single access branch.

    Args:
        access_branch_name: Name of the access branch (e.g. "hpc", "hpc-data").

    Returns:
        JSON dict with a single key matching the branch name.
        404 if the access branch has no data.
    """
    result = get_project_group_status(db.session,
                                      access_branch=access_branch_name,
                                      grace_period_days=ACCESS_GRACE_PERIOD)
    if not result:
        abort(404, f'Access branch {access_branch_name!r} not found or has no data')
    return jsonify(result)


@bp.route('/refresh', methods=['POST'])
@login_or_token_required(Permission.VIEW_PROJECTS)
def refresh_cache():
    """
    Invalidate the project access cache.

    Forces the next GET request to recompute from the database.

    Returns:
        JSON with {"status": "ok"}
    """
    cache.delete_memoized(get_project_access)
    cache.delete_memoized(get_project_access_branch)
    cache.clear()
    return jsonify({'status': 'ok'})

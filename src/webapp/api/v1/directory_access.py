"""
Directory Access API endpoints (v1).

Provides the unix group and account data consumed by LDAP provisioning systems.
Reproduces the output of the legacy SAM Java endpoint:
  GET /api/protected/admin/sysacct/directoryaccess

Example usage:
    GET /api/v1/directory_access/          — all access branches
    GET /api/v1/directory_access/hpc       — single branch
    GET /api/v1/directory_access/hpc-data  — single branch
    POST /api/v1/directory_access/refresh  — invalidate cache
"""

from flask import Blueprint, jsonify, abort
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache
from webapp.api.helpers import register_error_handlers
from sam.queries.directory_access import (
    group_populator,
    user_populator,
    build_directory_access_response,
    ACCESS_GRACE_PERIOD,
)

bp = Blueprint('api_directory_access', __name__)
register_error_handlers(bp)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_response(access_branch: str | None = None) -> dict:
    """Run both populators and assemble the response dict."""
    branch_groups = group_populator(db.session, access_branch=access_branch,
                                    grace_period_days=ACCESS_GRACE_PERIOD)
    branch_accounts = user_populator(db.session, access_branch=access_branch,
                                     grace_period_days=ACCESS_GRACE_PERIOD)
    return build_directory_access_response(branch_groups, branch_accounts)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_USERS)
@cache.cached(timeout=300, query_string=True)
def get_directory_access():
    """
    Return the full directory access data for all access branches.

    Returns:
        JSON with "accessBranchDirectories" list, each containing
        "accessBranchName", "unixGroups", and "unixAccounts".
    """
    return jsonify(_build_response())


@bp.route('/<access_branch_name>', methods=['GET'])
@login_or_token_required(Permission.VIEW_USERS)
@cache.cached(timeout=300, query_string=True)
def get_directory_access_branch(access_branch_name: str):
    """
    Return directory access data for a single access branch.

    Args:
        access_branch_name: Name of the access branch (e.g. "hpc", "hpc-data")

    Returns:
        JSON with "accessBranchDirectories" list containing a single branch entry.
        404 if the access branch has no groups or accounts.
    """
    result = _build_response(access_branch=access_branch_name)
    if not result['accessBranchDirectories']:
        abort(404, f'Access branch {access_branch_name!r} not found or has no data')
    return jsonify(result)


@bp.route('/refresh', methods=['POST'])
@login_or_token_required(Permission.VIEW_USERS)
def refresh_cache():
    """
    Invalidate the directory access cache.

    Forces the next GET request to recompute from the database.

    Returns:
        JSON with {"status": "ok"}
    """
    cache.delete_memoized(get_directory_access)
    cache.delete_memoized(get_directory_access_branch)
    # Also clear any cached versions by key pattern
    cache.clear()
    return jsonify({'status': 'ok'})

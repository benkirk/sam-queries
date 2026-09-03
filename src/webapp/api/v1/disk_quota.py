"""Disk-quota API endpoint (v1).

Per-project disk allocations and paths for DASG provisioning. Reproduces legacy
``GET /api/protected/admin/dasg/diskquota``.

``GET /`` all disk accounts, ``POST /refresh`` invalidates the cache. Unlike the
other legacy-compat blueprints the JSON is produced by a Marshmallow schema
(``DiskQuotaSchema``, ``data_key`` for the camelCase legacy shape), not a
hand-built dict -- see CLAUDE.md section API. Response shape:
``docs/apis/SYSTEMS_INTEGRATION_APIs.md``.

Note: legacy gates this behind ``ROLE_API_DASG``; SAM has no disk-specific
permission, so it uses ``VIEW_PROJECTS`` (as project_access/fstree_access do).
"""

from flask import Blueprint, jsonify
from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import db, cache, csrf
from webapp.caching import caching
from webapp.api.helpers import register_error_handlers
from sam.queries.disk_quota import get_disk_quotas
from sam.schemas import DiskQuotaSchema

bp = Blueprint('api_disk_quota', __name__)
register_error_handlers(bp)

_schema = DiskQuotaSchema(many=True)


@bp.route('/', methods=['GET'])
@login_or_token_required(Permission.VIEW_PROJECTS)
@cache.cached(query_string=True)
def get_disk_quota():
    """Return disk-quota records for all qualifying DISK accounts.

    Returns:
        JSON list of objects with projcode, groupName, dataManager,
        resourceName, quota, and paths.
    """
    return jsonify(_schema.dump(get_disk_quotas(db.session)))


@bp.route('/refresh', methods=['POST'])
@csrf.exempt          # token path is Basic-auth (no cookies); the session
                      # path losing CSRF on an idempotent cache refresh is
                      # an accepted trade-off
@login_or_token_required(Permission.VIEW_PROJECTS)
def refresh_cache():
    """Invalidate the disk-quota cache. Returns {"status": "ok"}."""
    cache.delete_memoized(get_disk_quota)
    caching.clear('flask')
    return jsonify({'status': 'ok'})

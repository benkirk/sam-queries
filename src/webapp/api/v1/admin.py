"""
Admin API endpoints (v1).

Operational endpoints for sysadmins and machine-to-machine tooling that
mutate or invalidate runtime state (as opposed to the read-only
Configuration dashboard). Gated on ``Permission.SYSTEM_ADMIN``.

  POST /api/v1/admin/cache/refresh              — clear every cache
  POST /api/v1/admin/cache/refresh?category=X   — clear one category

``category`` is one of ``flask|chart|usage|scans`` and is passed straight
through to the caching facade's ``clear()``.

Response format::

    {"status": "ok", "cleared": {"flask": 12, "chart": 3, "usage": 0, "scans": 1}}

Worker affinity: with the in-process cache fallback (no Redis) and multiple
gunicorn workers, this only clears the worker that handled the request. With
Redis configured (production) the shared stores clear globally. See
``webapp.caching`` for the fallback semantics.
"""

from flask import Blueprint, jsonify, request, abort

from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.extensions import csrf
from webapp.api.helpers import register_error_handlers
from webapp.caching import caching

bp = Blueprint('api_admin', __name__)
register_error_handlers(bp)

# Categories understood by caching.clear(); None (omitted) clears all.
_VALID_CATEGORIES = {'flask', 'chart', 'usage', 'scans'}


@bp.route('/cache/refresh', methods=['POST'])
@csrf.exempt          # token path is Basic-auth (no cookies); the session
                      # path losing CSRF on an idempotent cache refresh is
                      # an accepted trade-off, matching the per-resource
                      # /refresh routes.
@login_or_token_required(Permission.SYSTEM_ADMIN)
def refresh_cache():
    """
    Invalidate caches, optionally scoped to a single category.

    Query params:
        category: one of ``flask|chart|usage|scans``. Omit to clear all.

    Returns:
        JSON ``{"status": "ok", "cleared": {category: count_cleared}}``.
        400 if ``category`` is present but not recognized.
    """
    category = request.args.get('category')
    if category is not None and category not in _VALID_CATEGORIES:
        abort(400, f"Invalid category {category!r}; "
                   f"must be one of {sorted(_VALID_CATEGORIES)}")
    return jsonify({'status': 'ok', 'cleared': caching.clear(category)})

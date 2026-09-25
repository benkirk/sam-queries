"""The /database browser: read-only rows from every engine the webapp holds.

Mounted when ``DB_BROWSER_ENABLED``; every route is gated here by
``Permission.ADMIN_DATABASE``, so no route can forget the check. Query logic
lives in ``dbbrowse`` (Flask-free); design record: docs/plans/DB_BROWSER.md.
"""
import logging

from flask import Blueprint, abort, current_app, request
from flask_login import current_user

from webapp.utils.rbac import Permission, has_permission

bp = Blueprint('db_browser', __name__)
logger = logging.getLogger(__name__)


@bp.before_request
def _gate():
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not has_permission(current_user, Permission.ADMIN_DATABASE):
        abort(403)
    # Who read what: every route, names only, never filter or key values.
    logger.info('db_browser user=%s %s %s', current_user.username, request.endpoint,
                ' '.join(f'{k}={v}' for k, v in sorted(request.view_args.items())))


@bp.context_processor
def _rail_context():
    from .sources import browse_sources
    return {'sources_menu': lambda: list(browse_sources().values())}


@bp.after_request
def _no_store(response):
    # Rows can carry PII: never cache a page in a browser or proxy.
    response.headers['Cache-Control'] = 'private, no-store'
    return response


from . import routes  # noqa: E402,F401  (registers the views on bp)

"""
API key authentication for machine-to-machine routes (e.g., HPC status collectors).

Validates HTTP Basic Auth credentials against bcrypt hashes stored in
app.config['API_KEYS'] = {'username': '$2b$12$...hash...'}.

Usage:
    # M2M only — token required, no session fallback
    @bp.route('/derecho', methods=['POST'])
    @api_key_required
    def ingest_derecho():
        ...

    # Either session login (with permission check) OR token auth
    @bp.route('/resource', methods=['GET'])
    @login_or_token_required(Permission.VIEW_PROJECTS)
    def get_resource():
        actor = get_auth_actor()
        ...
"""

import bcrypt
from functools import wraps
from typing import Optional
from flask import request, jsonify, current_app, g, make_response, url_for
from flask_login import current_user
from webapp.utils.rbac import has_permission, Permission


def api_key_required(f):
    """
    Decorator: requires valid HTTP Basic Auth API key credentials.

    Reads API_KEYS from app config (dict of username -> bcrypt hash).
    Returns 401 + WWW-Authenticate header on auth failure.
    Stores authenticated username in g.api_key_user for logging.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not auth.username or not auth.password:
            return (
                jsonify({'error': 'Authentication required'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        api_keys = current_app.config.get('API_KEYS', {})
        stored_hash = api_keys.get(auth.username)

        if not stored_hash:
            return (
                jsonify({'error': 'Invalid credentials'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        # bcrypt.checkpw is timing-safe
        try:
            valid = bcrypt.checkpw(
                auth.password.encode('utf-8'),
                stored_hash.encode('utf-8'),
            )
        except Exception:
            valid = False

        if not valid:
            return (
                jsonify({'error': 'Invalid credentials'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        g.api_key_user = auth.username  # available to view functions for logging
        return f(*args, **kwargs)

    return decorated_function


def login_or_token_required(permission: Optional[Permission] = None):
    """
    Decorator factory: grants access via HTTP Basic Auth API key OR Flask-Login session.

    Auth path is determined by the presence of a ``request.authorization`` header —
    the two paths are mutually exclusive with no fallback between them.

    Token path (``Authorization: Basic ...`` header present):
      - Validates credentials against ``API_KEYS`` bcrypt hashes (same as ``api_key_required``)
      - No RBAC check; any valid key grants access
      - Sets ``g.api_key_user`` for downstream logging

    Session path (no ``Authorization`` header):
      - Requires ``current_user.is_authenticated`` (Flask-Login)
      - If ``permission`` is given, also checks ``has_permission(current_user, permission)``
      - Returns HTMX-aware 401 (``HX-Redirect`` to login) or JSON 401; JSON 403 on permission failure

    Args:
        permission: Optional ``Permission`` enum value. Session users must hold this permission.
                    Token users bypass RBAC entirely. ``None`` means just be authenticated.

    Usage::

        @bp.route('/resource', methods=['GET'])
        @login_or_token_required(Permission.VIEW_PROJECTS)
        def get_resource():
            actor = get_auth_actor()  # works for both token and session paths
            ...

        @bp.route('/simple', methods=['GET'])
        @login_or_token_required()   # any authenticated caller; parens always required
        def simple():
            ...

    Note: Do NOT combine with ``@require_project_access`` / ``@require_project_member_access``
    — those decorators assume a session ``current_user`` and are incompatible with token callers.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # ── Token path ────────────────────────────────────────────────────
            # Presence of request.authorization means "Authorization: Basic ..."
            # was sent. Honour this path only; do not fall back to session auth.
            if request.authorization:
                auth = request.authorization
                if not auth.username or not auth.password:
                    return (
                        jsonify({'error': 'Authentication required'}),
                        401,
                        {'WWW-Authenticate': 'Basic realm="SAM API"'},
                    )

                api_keys = current_app.config.get('API_KEYS', {})
                stored_hash = api_keys.get(auth.username)

                if not stored_hash:
                    return (
                        jsonify({'error': 'Invalid credentials'}),
                        401,
                        {'WWW-Authenticate': 'Basic realm="SAM API"'},
                    )

                try:
                    valid = bcrypt.checkpw(
                        auth.password.encode('utf-8'),
                        stored_hash.encode('utf-8'),
                    )
                except Exception:
                    valid = False

                if not valid:
                    return (
                        jsonify({'error': 'Invalid credentials'}),
                        401,
                        {'WWW-Authenticate': 'Basic realm="SAM API"'},
                    )

                g.api_key_user = auth.username
                return f(*args, **kwargs)

            # ── Session path ──────────────────────────────────────────────────
            if not current_user.is_authenticated:
                # Mirror run.py unauthorized_handler: HTMX gets HX-Redirect,
                # plain API callers get a JSON 401.
                if request.headers.get('HX-Request'):
                    response = make_response('', 401)
                    response.headers['HX-Redirect'] = url_for('auth.login')
                    return response
                return jsonify({'error': 'Authentication required'}), 401

            if permission is not None and not has_permission(current_user, permission):
                return jsonify({'error': 'Forbidden - insufficient permissions'}), 403

            return f(*args, **kwargs)

        return decorated_function
    return decorator


def get_auth_actor() -> str:
    """Return the authenticated actor name for logging (works for both token and session auth).

    Use this in view functions instead of reading ``g.api_key_user`` directly::

        actor = get_auth_actor()
        current_app.logger.info('action performed by %s', actor)
    """
    return getattr(g, 'api_key_user', None) or getattr(current_user, 'username', 'anonymous')

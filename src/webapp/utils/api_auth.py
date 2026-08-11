"""
API key authentication for machine-to-machine routes (e.g., HPC status collectors).

Validates HTTP Basic Auth credentials against bcrypt hashes drawn from two
sources, in precedence order:

  1. ``app.config['API_KEYS'] = {'username': '$2b$12$...hash...'}`` — populated
     from ``API_KEYS_<USER>`` env vars (prod) or a hard-coded dev/test dict.
  2. The ``api_credentials`` SQL table (legacy SAM) — enabled rows, read live
     behind a short in-process TTL cache. This lets existing legacy API clients
     keep their credentials while calling the new API paths.

Config always wins: a username defined in ``API_KEYS`` is verified against the
config hash only and never falls through to the DB.

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

import time
import bcrypt
from functools import wraps
from typing import Callable, Iterable, Optional
from flask import request, jsonify, current_app, g, make_response, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user
from webapp.utils.rbac import has_permission, Permission


# In-process cache of the enabled ``api_credentials`` rows, refreshed every
# ``API_KEYS_DB_TTL`` seconds. Holding the full enabled set (not per-username
# rows) means an unknown username is an in-memory miss — no per-attempt DB query
# — which keeps failed-auth traffic off the DB and friendly to RATELIMIT_M2M.
_DB_KEY_CACHE = {'at': None, 'map': {}}


def _auth_challenge(message: str = 'Authentication required'):
    """Standard 401 + WWW-Authenticate response for the Basic-Auth realm."""
    return (
        jsonify({'error': message}),
        401,
        {'WWW-Authenticate': 'Basic realm="SAM API"'},
    )


def _bcrypt_matches(password: str, stored_hash: str) -> bool:
    """Timing-safe bcrypt check that tolerates legacy ``$2a$``/``$2y$`` hashes."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
    except Exception:
        return False


def _get_db_api_keys() -> dict:
    """Return ``{username: {'hash', 'roles'}}`` for enabled ``api_credentials``.

    Cached for ``API_KEYS_DB_TTL`` seconds so a Basic-Auth attempt is a dict
    lookup, not a DB round-trip. ``TTL=0`` disables caching (every call
    refreshes) — used in tests. On DB error, logs a warning and serves the
    last-good map, degrading gracefully to config-only auth.
    """
    if not current_app.config.get('API_KEYS_DB_ENABLED', True):
        return {}

    ttl = current_app.config.get('API_KEYS_DB_TTL', 60)
    now = time.monotonic()
    last = _DB_KEY_CACHE['at']
    if ttl and last is not None and (now - last) < ttl:
        return _DB_KEY_CACHE['map']

    try:
        from webapp.extensions import db
        from sam.security.roles import ApiCredentials
        fresh = ApiCredentials.as_api_key_map(db.session)
    except Exception:
        current_app.logger.warning(
            'api_credentials DB lookup failed; serving last-good API-key map',
            exc_info=True,
        )
        return _DB_KEY_CACHE['map']

    _DB_KEY_CACHE['at'] = now
    _DB_KEY_CACHE['map'] = fresh
    return fresh


def _verify_api_key(username: str, password: str) -> Optional[dict]:
    """Resolve and verify a Basic-Auth API key across config + DB sources.

    Precedence: ``config['API_KEYS']`` wins. A username defined there is checked
    against the config hash ONLY and never falls through to the DB (so a stale
    DB row cannot shadow a rotated config key). Usernames absent from config are
    checked against the enabled ``api_credentials`` rows.

    Returns an identity ``{'username', 'source': 'config'|'db', 'roles': [...]}``
    on success, else ``None``.
    """
    config_keys = current_app.config.get('API_KEYS', {})
    if username in config_keys:
        if _bcrypt_matches(password, config_keys[username]):
            return {'username': username, 'source': 'config', 'roles': []}
        return None

    entry = _get_db_api_keys().get(username)
    if entry and _bcrypt_matches(password, entry['hash']):
        return {'username': username, 'source': 'db', 'roles': entry['roles']}
    return None


def _set_api_identity(ident: dict) -> None:
    """Stash the authenticated API identity on ``g`` for logging and authz.

    ``g.api_key_roles`` holds the role names carried by the ``api_credentials``
    row (see ``ApiCredentials.as_api_key_map``); it is always populated for
    logging, and is *enforced* on routes that pass ``roles=`` to
    ``login_or_token_required``. Config-sourced keys always resolve to ``[]``.
    """
    g.api_key_user = ident['username']
    g.api_key_source = ident['source']
    g.api_key_roles = ident['roles']


def api_key_required(f):
    """
    Decorator: requires valid HTTP Basic Auth API key credentials.

    Reads API_KEYS from app config (dict of username -> bcrypt hash).
    Returns 401 + WWW-Authenticate header on auth failure.
    Stores authenticated username in g.api_key_user for logging.

    Stacks the M2M rate limit (``RATELIMIT_M2M``, per-IP) on every wrapped
    route — Flask-Limiter checks limits in a before_request hook, which
    fires before this decorator gets a chance to set ``g.api_key_user``,
    so we pin ``key_func`` to the source IP. 120/min default is enough
    headroom for legitimate collectors while still bounding abuse.
    """
    from webapp.limiter import limiter as _facade

    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not auth.username or not auth.password:
            return _auth_challenge()

        ident = _verify_api_key(auth.username, auth.password)
        if ident is None:
            return _auth_challenge('Invalid credentials')

        _set_api_identity(ident)  # available to view functions for logging
        return f(*args, **kwargs)

    return _facade.limiter.limit(
        lambda: current_app.config['RATELIMIT_M2M'],
        # Per-IP key with the ``ip:`` prefix our event log uses, so admin
        # unblock-by-actor matches the storage key cleanly.
        key_func=lambda: f'ip:{get_remote_address()}',
    )(decorated_function)


def login_or_token_required(
    permission: Optional[Permission] = None,
    *,
    roles: Optional[Iterable[str]] = None,
    deny: Optional[Callable[[int, str], object]] = None,
):
    """
    Decorator factory: grants access via HTTP Basic Auth API key OR Flask-Login session.

    Auth path is determined by the presence of a ``request.authorization`` header —
    the two paths are mutually exclusive with no fallback between them.

    Token path (``Authorization: Basic ...`` header present):
      - Validates credentials against config ``API_KEYS`` bcrypt hashes and, as a
        fallback, the enabled ``api_credentials`` DB rows (same as ``api_key_required``)
      - If ``roles`` is given, the caller must hold at least one of them
        (see below); otherwise any valid key grants access, with the key's role
        names available on ``g.api_key_roles`` for logging
      - Sets ``g.api_key_user`` / ``g.api_key_source`` for downstream logging

    Session path (no ``Authorization`` header):
      - Requires ``current_user.is_authenticated`` (Flask-Login)
      - If ``permission`` is given, also checks ``has_permission(current_user, permission)``
      - Returns HTMX-aware 401 (``HX-Redirect`` to login) or JSON 401; JSON 403 on permission failure

    Args:
        permission: Optional ``Permission`` enum value. Session users must hold this
                    permission. Token users bypass RBAC entirely — ``roles`` is the
                    token-path analogue, not a substitute. ``None`` means just be
                    authenticated.
        roles:      Optional iterable of API-key role names, as carried by
                    ``api_credentials`` via ``role_api_credentials`` (e.g. ``ROLE_XRAS``).
                    The token caller must hold at least one, else 403.

                    ⚠️ When ``roles`` is given the **session path is closed** and an
                    unauthenticated request is a 401: a browser session has no API-key
                    roles at all, so "holds an API-key role" and "is logged in via
                    Flask-Login" are mutually exclusive conditions. Routes needing both
                    want two decorated views, not this parameter.

                    ⚠️ Config-sourced keys (``API_KEYS_<USER>``) always resolve with an
                    empty role list, because config carries no role assignments. Defining
                    a key in config that a ``roles``-gated route expects therefore fails
                    **closed**, silently, while other routes keep working.
        deny:       Optional ``callable(status: int, message: str)`` returning a Flask
                    response, for blueprints whose error bodies are part of a fixed
                    legacy wire contract. Defaults to this module's standard shapes —
                    ``_auth_challenge`` for 401 (which carries ``WWW-Authenticate``) and
                    a JSON 403 — so every existing call site is byte-unchanged.

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

        # M2M with role enforcement and a caller-supplied wire contract
        @bp.route('/xras', methods=['GET'])
        @login_or_token_required(roles=('ROLE_XRAS',), deny=_xras_deny)
        def xras_only():
            ...

    Note: Do NOT combine with ``@require_project_access`` / ``@require_project_member_access``
    — those decorators assume a session ``current_user`` and are incompatible with token callers.
    """
    required_roles = frozenset(roles) if roles else None

    def _deny(status: int, message: str):
        if deny is not None:
            return deny(status, message)
        if status == 401:
            return _auth_challenge(message)
        return jsonify({'error': message}), status

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # ── Token path ────────────────────────────────────────────────────
            # Presence of request.authorization means "Authorization: Basic ..."
            # was sent. Honour this path only; do not fall back to session auth.
            if request.authorization:
                auth = request.authorization
                if not auth.username or not auth.password:
                    return _deny(401, 'Authentication required')

                ident = _verify_api_key(auth.username, auth.password)
                if ident is None:
                    return _deny(401, 'Invalid credentials')

                _set_api_identity(ident)

                if required_roles is not None and not required_roles.intersection(
                    ident['roles']
                ):
                    return _deny(403, 'Forbidden - insufficient permissions')

                return f(*args, **kwargs)

            # ── Session path ──────────────────────────────────────────────────
            # A ``roles`` gate is API-key-only by construction: session users carry
            # no API-key roles, so there is nothing here that could satisfy it.
            if required_roles is not None:
                return _deny(401, 'Authentication required')

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

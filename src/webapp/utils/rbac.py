"""Role-Based Access Control for the SAM Web UI.

Permissions, the role catalog, and the access checks used by dashboard routes
and API endpoints. A user's permissions are the union of the grants held by
their username and by each POSIX group they belong to (read from
``adhoc_system_account_entry``); a grant is unscoped or names one facility.

``RBAC_SOURCE`` picks the catalog: ``defaults`` reads the module dicts below,
built from ``sam.security.rbac_defaults``; ``db`` reads the ``samuel_role_*``
tables through a per-process snapshot refreshed every ``RBAC_DB_TTL`` seconds
and dropped by ``invalidate_catalog()`` on every admin write. In ``db`` mode
there is NO fallback to the defaults: that would hand a revoked user their old
grants back whenever the tables are empty.

WARNING: the SAM ``role_user`` / ``role`` tables are never consulted here;
they carry the API-key role names only (``webapp.utils.api_auth``).
"""

import time
from typing import Set, Dict
from functools import wraps
from flask import abort, current_app, has_app_context
from flask_login import current_user

from sam.security.permissions import (  # noqa: F401 -- re-exported
    ALL_CREATE, ALL_DELETE, ALL_EDIT, ALL_VIEW, Permission, _perms_with_action,
)
from sam.security.rbac_catalog import RoleCatalog, build_catalog
from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES


# The three legacy shapes, derived from the factory defaults through the same
# closure the DB path uses. Mutable on purpose: tests register bundles here,
# and in ``defaults`` mode the predicates read them live.
GROUP_PERMISSIONS: Dict[str, Set[Permission]]
USER_PERMISSION_OVERRIDES: Dict[str, Set[Permission]]
USER_FACILITY_PERMISSIONS: Dict[str, Dict[str, Set[Permission]]]
GROUP_PERMISSIONS, USER_PERMISSION_OVERRIDES, USER_FACILITY_PERMISSIONS = (
    build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS).as_dicts()
)

_DB_CACHE: Dict[str, object] = {'at': None, 'catalog': None}


def _db_mode() -> bool:
    return has_app_context() and current_app.config.get('RBAC_SOURCE') == 'db'


def _load_db_catalog() -> RoleCatalog:
    """One round trip over the samuel_role_* tables; tests patch this name."""
    from sam.security.samuel_roles import load_catalog
    from webapp.extensions import db
    return load_catalog(db.session)


def invalidate_catalog() -> None:
    """Drop this process's snapshot; the next check reloads it."""
    _DB_CACHE['at'] = None


def _db_catalog() -> RoleCatalog:
    """The TTL snapshot. On a load error the last-good snapshot is served, and
    with none an empty catalog: nobody holds anything until the DB answers."""
    ttl = current_app.config.get('RBAC_DB_TTL', 60)
    now = time.monotonic()
    last = _DB_CACHE['at']
    if ttl and last is not None and _DB_CACHE['catalog'] is not None and (now - last) < ttl:
        return _DB_CACHE['catalog']
    try:
        fresh = _load_db_catalog()
    except Exception:
        current_app.logger.error('samuel_role_* load failed; serving the last-good '
                                 'role catalog', exc_info=True)
        _rollback_session()
        if _DB_CACHE['catalog'] is None:
            _DB_CACHE['catalog'] = RoleCatalog(roles={}, unscoped={}, scoped={}, source='db')
        return _DB_CACHE['catalog']
    if not fresh.unscoped and not fresh.scoped:
        # The tile that says this sits behind VIEW_SYSTEM_CONFIG, which nobody
        # holds in this state, so the log is the only signal.
        current_app.logger.error('RBAC_SOURCE=db and samuel_role_* hold no active grant: '
                                 'nobody holds anything; seed with sam-admin rbac --seed')
    _DB_CACHE['at'] = now
    _DB_CACHE['catalog'] = fresh
    return fresh


def _rollback_session() -> None:
    """A failed load leaves Postgres in an aborted transaction; end it so the
    request's later queries still run."""
    try:
        from webapp.extensions import db
        db.session.rollback()
    except Exception:
        current_app.logger.warning('rollback after a samuel_role_* load failure failed',
                                   exc_info=True)


def active_catalog() -> RoleCatalog:
    """The catalog every predicate reads: the module dicts in ``defaults``
    mode, the TTL snapshot of the tables in ``db`` mode."""
    if _db_mode():
        return _db_catalog()
    return RoleCatalog.from_dicts(GROUP_PERMISSIONS, USER_PERMISSION_OVERRIDES,
                                  USER_FACILITY_PERMISSIONS)


def _resolve(user, catalog: RoleCatalog):
    """``(unscoped set, {facility: set})`` for ``user``, group grants folded in.
    Memoized on the user object per catalog snapshot in ``db`` mode only, so a
    monkeypatched dict in ``defaults`` mode is still read live."""
    cached = getattr(user, '_rbac_resolved', None)
    if isinstance(cached, tuple) and cached[0] is catalog:
        return cached[1], cached[2]
    username = getattr(user, 'username', None)
    groups = getattr(user, 'roles', None)
    groups = tuple(groups) if isinstance(groups, (set, frozenset, list, tuple)) else ()
    unscoped: Set[Permission] = set()
    scoped: Dict[str, Set[Permission]] = {}
    if getattr(user, 'is_authenticated', False) and username is not None:
        unscoped |= catalog.permissions('user', username)
        for facility, perms in catalog.facility_permissions('user', username).items():
            scoped.setdefault(facility, set()).update(perms)
    for g in groups:
        unscoped |= catalog.permissions('group', g)
        for facility, perms in catalog.facility_permissions('group', g).items():
            scoped.setdefault(facility, set()).update(perms)
    if catalog.source == 'db':
        try:
            user._rbac_resolved = (catalog, unscoped, scoped)
        except (AttributeError, TypeError):
            pass
    return unscoped, scoped


def get_user_permissions(user) -> Set[Permission]:
    """The permissions ``user`` holds unconditionally: their own grants plus
    those of every POSIX group in ``user.roles``. The predicates below route
    through this module-level name on purpose; tests patch it."""
    return set(_resolve(user, active_catalog())[0])


def _scoped(user) -> Dict[str, Set[Permission]]:
    return _resolve(user, active_catalog())[1]


def has_permission(user, permission: Permission) -> bool:
    """True if ``user`` holds ``permission`` unconditionally."""
    return permission in get_user_permissions(user)


def has_permission_for_facility(user, permission: Permission,
                                facility_name) -> bool:
    """True if ``user`` holds ``permission`` unconditionally, or for
    ``facility_name`` through a facility-scoped grant.

    ``facility_name`` is ``None`` for orphan projects (no allocation_type
    chain), which only unscoped permission holders can act on.
    """
    if has_permission(user, permission):
        return True
    if facility_name is None:
        return False
    return permission in _scoped(user).get(facility_name, ())


def has_permission_any_facility(user, permission: Permission) -> bool:
    """True if ``user`` can exercise ``permission`` **somewhere** -- either
    unconditionally or in at least one facility.

    For route-level gates that admit scoped users; the route body then
    intersects their scope against whatever the request targeted. Contrast
    with ``has_permission``, the right question for routes that must stay
    pure system-admin domain.
    """
    if has_permission(user, permission):
        return True
    return any(permission in perms for perms in _scoped(user).values())


def user_facility_scope(user, permission: Permission):
    """Facility names where ``user`` may exercise ``permission``: ``None`` for
    unscoped (skip the filter), a set to constrain to, or an empty set for no
    way to exercise it at all."""
    if has_permission(user, permission):
        return None
    return {f for f, perms in _scoped(user).items() if permission in perms}


def allowed_facility_names(user, permission: Permission, *, active_only=True):
    """Sorted facility-name universe for ``permission``: the user's grant if
    scoped, else every facility in the DB (active only unless overridden).

    For building selector vocabularies. Enforcement belongs to
    ``apply_facility_scope`` / ``filter_rows_by_facility`` at query time.
    """
    allowed = user_facility_scope(user, permission)
    if allowed is not None:
        return sorted(allowed)
    # Deferred: importing sam models at rbac module load would trigger the
    # ORM init chain before create_app is ready.
    from sam.resources.facilities import Facility
    from webapp.extensions import db
    q = db.session.query(Facility)
    if active_only:
        q = q.filter(Facility.is_active)
    return [f.facility_name for f in q.order_by(Facility.facility_name).all()]


def apply_facility_scope(requested, permission: Permission, default=None):
    """The effective facility-name list for a request: the submitted
    ``facilities`` combined with the caller's scoped grants for ``permission``.

    Unscoped users: ``requested`` wins, else ``default``, else ``None`` for no
    restriction. Scoped users: the intersection, clamped back to their full
    allowed set when the request is empty or disjoint (they asked for nothing
    they can see; that is not an error). Empty scope returns ``[]``, meaning no
    rows.
    """
    allowed = user_facility_scope(current_user, permission)
    if allowed is None:
        return list(requested) if requested else (list(default) if default else None)
    if not allowed:
        return []
    if not requested:
        return sorted(allowed)
    intersected = [f for f in requested if f in allowed]
    return intersected or sorted(allowed)


def filter_rows_by_facility(rows, allowed):
    """Drop rows whose ``'facility'`` key isn't in ``allowed``.

    Pass ``None`` for ``allowed`` to skip filtering (unscoped / global
    view). Used by the allocations dashboard's post-fetch scope filter
    -- every row returned by the summary / usage / transactions
    queries carries a ``'facility'`` field."""
    if allowed is None:
        return rows
    if not allowed:
        return []
    allowed_set = allowed if isinstance(allowed, (set, frozenset)) else set(allowed)
    return [r for r in rows if r.get('facility') in allowed_set]


def can_impersonate(caller, target) -> bool:
    """The no-escalation rule: ``target``'s permissions must be a subset of
    ``caller``'s (equal sets, i.e. peer impersonation, are allowed).

    This does NOT check ``Permission.IMPERSONATE_USERS`` -- the route decorator
    gates that. This enforces only the no-escalation invariant.
    """
    return get_user_permissions(target) <= get_user_permissions(caller)


def has_role(user, role_name: str) -> bool:
    """True if ``user`` belongs to a POSIX group the catalog names.

    Display logic only — authorization decisions use ``has_permission``.
    """
    return user.has_role(role_name)


# Decorator for requiring permissions in views
def require_permission(permission: Permission):
    """
    Decorator to require a specific permission for a view.

    Usage:
        @app.route('/admin/users')
        @login_required
        @require_permission(Permission.VIEW_USERS)
        def list_users():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)  # Unauthorized
            if not has_permission(current_user, permission):
                abort(403)  # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission_any_facility(permission: Permission):
    """Admit callers holding ``permission`` unconditionally **or** in at least
    one facility; the route body then intersects their scope against the
    request.

    For admin routes a facility-scoped manager must reach. Routes that must
    stay pure system-admin domain use ``require_permission`` instead.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not has_permission_any_facility(current_user, permission):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Context processor for templates
def rbac_context_processor():
    """
    Add RBAC utilities to template context.

    Register this in your app:
        app.context_processor(rbac_context_processor)

    Then use in templates:
        {% if has_permission(Permission.EDIT_USERS) %}
            <a href="/users/edit">Edit Users</a>
        {% endif %}

        {# Project-scoped check via the conditional helper. Returns True
           when current_user has the system permission OR is project
           lead/admin (or ancestor lead/admin if include_ancestors=True). #}
        {% if can_act_on_project(Permission.EDIT_ALLOCATIONS, project, include_ancestors=True) %}
            <a href="...">Redistribute</a>
        {% endif %}
    """
    # Late import to avoid the circular path
    # rbac -> project_permissions -> rbac at module import time.
    from webapp.utils.project_permissions import (
        is_project_steward, can_create_events,
    )

    def _can_act_on_project(permission, project, include_ancestors=False):
        if project is None:
            return False
        if current_user is None or not current_user.is_authenticated:
            return False
        return is_project_steward(
            current_user, project, permission, include_ancestors=include_ancestors
        )

    def _can_create_events(project):
        if project is None or current_user is None or not current_user.is_authenticated:
            return False
        return can_create_events(current_user, project)

    return {
        'Permission': Permission,
        'has_permission': lambda p: has_permission(current_user, p) if current_user.is_authenticated else False,
        'has_permission_any_facility': lambda p: (
            has_permission_any_facility(current_user, p)
            if current_user.is_authenticated else False
        ),
        'has_role': lambda r: has_role(current_user, r) if current_user.is_authenticated else False,
        'user_permissions': get_user_permissions(current_user) if current_user.is_authenticated else set(),
        'can_act_on_project': _can_act_on_project,
        'can_create_events': _can_create_events,
    }

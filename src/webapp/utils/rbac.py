"""Role-Based Access Control for the SAM Web UI.

Permissions, POSIX-group-to-permission bundles, and the access checks used by
both Flask-Admin views and API endpoints.

A user's permissions are the union of two sources: the ``GROUP_PERMISSIONS``
bundle of each POSIX group they belong to (read from
``adhoc_system_account_entry`` in dev, test and production alike), plus any
``USER_PERMISSION_OVERRIDES`` granted to them by name.

WARNING: there is NO dependency on the SAM ``role_user`` / ``role`` tables --
the webapp's RBAC layer never consults them.

``AuthUser.roles`` returns the POSIX group names that have a bundle, which
keeps ``has_role('csg')`` working as a coarse display label. The source of
truth for authorization is the permission set, not the role label.
"""

from enum import Enum
from typing import Set, List, Dict
from functools import wraps
from flask import abort
from flask_login import current_user


class Permission(Enum):
    """System-wide permissions, granted via ``GROUP_PERMISSIONS`` bundles or
    ``USER_PERMISSION_OVERRIDES``."""

    # User management
    VIEW_USERS = "view_users"
    EDIT_USERS = "edit_users"
    CREATE_USERS = "create_users"
    DELETE_USERS = "delete_users"

    # Project management
    VIEW_PROJECTS = "view_projects"
    EDIT_PROJECTS = "edit_projects"
    CREATE_PROJECTS = "create_projects"
    DELETE_PROJECTS = "delete_projects"
    VIEW_PROJECT_MEMBERS = "view_project_members"
    EDIT_PROJECT_MEMBERS = "edit_project_members"

    # Allocation management
    VIEW_ALLOCATIONS = "view_allocations"
    EDIT_ALLOCATIONS = "edit_allocations"
    CREATE_ALLOCATIONS = "create_allocations"
    DELETE_ALLOCATIONS = "delete_allocations"

    # Resource management (machines, queues, resource definitions)
    VIEW_RESOURCES = "view_resources"
    EDIT_RESOURCES = "edit_resources"
    CREATE_RESOURCES = "create_resources"
    DELETE_RESOURCES = "delete_resources"

    # Facility management (UNIV, WNA, ...)
    VIEW_FACILITIES = "view_facilities"
    EDIT_FACILITIES = "edit_facilities"
    CREATE_FACILITIES = "create_facilities"
    DELETE_FACILITIES = "delete_facilities"

    # Group management (adhoc/POSIX groups)
    VIEW_GROUPS = "view_groups"
    EDIT_GROUPS = "edit_groups"
    CREATE_GROUPS = "create_groups"
    DELETE_GROUPS = "delete_groups"

    # Organizational metadata: organizations, institutions, mnemonic
    # codes, areas of interest. Slowly-changing reference data.
    VIEW_ORG_METADATA = "view_org_metadata"
    EDIT_ORG_METADATA = "edit_org_metadata"
    CREATE_ORG_METADATA = "create_org_metadata"
    DELETE_ORG_METADATA = "delete_org_metadata"

    # Contracts: the awards/grants funding projects, plus their sources
    # and NSF programs — the whole /admin/contracts surface. Carved out
    # of ORG_METADATA because contract administration tracks allocation
    # administration (who funds this project, through when) rather than
    # the slowly-changing directory reference data above.
    #
    # DELETE_CONTRACTS is a soft retire, not a row delete: the contract
    # route stamps ``end_date`` (contracts_routes.py) and the generated
    # source/program deletes set ``active=False``.
    VIEW_CONTRACTS = "view_contracts"
    EDIT_CONTRACTS = "edit_contracts"
    CREATE_CONTRACTS = "create_contracts"
    DELETE_CONTRACTS = "delete_contracts"

    # Reports and analytics
    VIEW_REPORTS = "view_reports"
    VIEW_CHARGE_SUMMARIES = "view_charge_summaries"
    MANAGE_CHARGE_SUMMARIES = "manage_charge_summaries"  # Write charge summary records
    EXPORT_DATA = "export_data"

    # Filesystem scans (elevated)
    # Browse all filesystem-scan data across a disk resource, UNSCOPED — every
    # user's paths / sizes / owner UIDs, cross-project and cross-user. The
    # project-scoped fs-scans card needs no permission (members see their own
    # tree); this gates only the resource-wide explorer. Named ``view_*`` so
    # it is auto-granted to the operator bundles via ``ALL_VIEW`` (today exactly
    # nusd/csg/ssg) and NOT to the facility-scoped tier, which enumerates its
    # VIEW_* grants explicitly. Campaign collections don't map onto
    # UNIV/WNA/NCAR facilities, so this is intentionally global, not
    # facility-scoped.
    VIEW_ALL_FILESYSTEM_DATA = "view_all_filesystem_data"

    # Job history (elevated)
    # Browse per-job data across an entire machine, UNSCOPED — every user's
    # jobs, queues, and charges, cross-project and cross-user (the machine-wide
    # jobs explorer + the Status page "Job History" tab). The project-scoped
    # jobs card needs no permission (project access already gates it) and the
    # "My Jobs" view pins to the session user; this gates only the machine-wide
    # surfaces. Named ``view_*`` so it is auto-granted to the operator bundles
    # via ``ALL_VIEW`` (today exactly nusd/csg/ssg) and NOT to the
    # facility-scoped tier, which enumerates its VIEW_* grants explicitly.
    # Plugin machines (derecho/casper) don't map onto UNIV/WNA/NCAR
    # facilities, so this is intentionally global, not facility-scoped.
    VIEW_ALL_JOB_DATA = "view_all_job_data"

    # System administration
    ACCESS_ADMIN_DASHBOARD = "access_admin_dashboard"  # Land on /admin/ and see the navbar tab
    MANAGE_ROLES = "manage_roles"
    IMPERSONATE_USERS = "impersonate_users"  # Actually log in as another user
    # The queue-load chart is visible to any logged-in user; this narrows two
    # operator-only enrichments on top: the per-user/per-project rollup table on
    # the drill-down page, and click-through from the legend into detail modals.
    VIEW_SYSTEM_STATUS_USER_INFO = "view_system_status_user_info"
    MANAGE_SYSTEM_STATUS = "manage_system_status"  # Update system status data (collector/API)
    EDIT_SYSTEM_STATUS = "edit_system_status"  # GUI create/edit/delete outages
    VIEW_SYSTEM_CONFIG = "view_system_config"  # Read-only Configuration tab on Admin dashboard
    # XRAS triage, split three ways: the audit trail, the payloads, and
    # destroying a request are three different authorities.
    #   VIEW_XRAS    action log, filters, error lists. Named ``view_*`` so
    #                ALL_VIEW auto-grants it to the operator bundles.
    #   MANAGE_XRAS  the raw payload panel -- the request body verbatim, real
    #                PII -- and the replay button, which is a write.
    #   ADMIN_XRAS   DESTRUCTIVE lifecycle verbs: delete, renew, add action.
    #                Irreversible in XRAS, so it rides with SYSTEM_ADMIN, NOT
    #                _ALLOCATION_ADMIN -- a MANAGE_XRAS operator gets the full
    #                non-destructive editor and never these
    #                (docs/xras/outgoing/REQUEST_EDITOR.md section 1).
    # WARNING: no ALL_* aggregate matches ``manage_`` or ``admin_`` -- they match
    # ``view_``/``edit_``/``create_``/``delete_`` on the VALUE. Both fail closed
    # and must be granted explicitly.
    VIEW_XRAS = "view_xras"
    MANAGE_XRAS = "manage_xras"
    ADMIN_XRAS = "admin_xras"
    SYSTEM_ADMIN = "system_admin"  # Full access to everything


# Building blocks for group bundles. ``_perms_with_action`` returns every
# Permission whose value starts with one of the given action prefixes; the four
# ``ALL_*`` slices let bundles use set arithmetic (``ALL_VIEW | ALL_EDIT |
# {Permission.EXPORT_DATA}``, ``ALL_VIEW - {Permission.VIEW_GROUPS}``). A new
# CRUD domain in the enum is therefore picked up by every bundle automatically.
def _perms_with_action(*action_prefixes: str) -> Set[Permission]:
    """All Permission members whose value starts with one of the given
    action prefixes (``'view'``, ``'edit'``, ``'create'``, ``'delete'``)."""
    return {
        p for p in Permission
        if any(p.value.startswith(f'{a}_') for a in action_prefixes)
    }


ALL_VIEW   = _perms_with_action('view')
ALL_EDIT   = _perms_with_action('edit')
ALL_CREATE = _perms_with_action('create')
ALL_DELETE = _perms_with_action('delete')


# POSIX-group-to-Permission mapping. A user receives the union over every group
# they belong to that appears in ``GROUP_PERMISSIONS``; groups absent from it
# confer nothing.

# ---- The allocation-administrator tier ----
#
# Provisions and manages projects, allocations and contracts end to end. The
# defining exclusion is the **definition layer** — resources, machines, queues
# and facilities describe the plant, not who may use it.
#
# WARNING: deletes are enumerated positively, never as ``ALL_DELETE - {...}``,
# so a future ``delete_*`` domain must be added deliberately rather than
# inherited. Every delete granted here is a SOFT retire (the generated CRUD
# sets ``active=False``; the contract delete stamps ``end_date``). The withheld
# ones are where delete is harsher or machine-shaped: DELETE_RESOURCES
# (hard-deletes disk-root and fair-share override rows, decommissions
# machines/queues), DELETE_FACILITIES, and DELETE_USERS / DELETE_GROUPS (hard
# row deletes via Flask-Admin).
#
# Known limitation (accepted): AllocationType and Panel live under the
# *_FACILITIES family, so default allocation amounts and fair-share
# percentages are NOT editable at this tier — see facilities_routes.py.
_ALLOCATION_ADMIN: Set[Permission] = (
    ALL_VIEW
    | (ALL_EDIT - {Permission.EDIT_RESOURCES, Permission.EDIT_FACILITIES})
    | {
        Permission.ACCESS_ADMIN_DASHBOARD,
        Permission.CREATE_PROJECTS,
        Permission.CREATE_ALLOCATIONS,
        Permission.CREATE_ORG_METADATA,
        Permission.CREATE_CONTRACTS,
        # Soft retires only — see the note above.
        Permission.DELETE_PROJECTS,
        Permission.DELETE_ALLOCATIONS,
        Permission.DELETE_ORG_METADATA,
        Permission.DELETE_CONTRACTS,
        Permission.IMPERSONATE_USERS,
        # XRAS payloads + replay. Explicit because no ALL_* aggregate matches a
        # ``manage_`` prefix, which is the behavior we want. This tier is where
        # it belongs: NUSD fields the XRAS failure mail from hdt@ucar.edu, and
        # XRAS actions are allocation provisioning by another name. (VIEW_XRAS
        # needs no entry — ALL_VIEW above already carries it.)
        Permission.MANAGE_XRAS,
    }
)


GROUP_PERMISSIONS: Dict[str, Set[Permission]] = {
    # ---- Real POSIX group bundles (provisional) ----

    # nusd: the allocation-administrator tier, unmodified — NUSD's job is
    # allocations and contracts, not machines or queues. A strict subset of
    # csg, so the can_impersonate no-escalation rule blocks nusd from
    # impersonating a csg user.
    'nusd': _ALLOCATION_ADMIN,

    # csg: the allocation-administrator tier PLUS edit on resources — CSG runs
    # the plant. Create/delete of resources stays withheld (ssg holds
    # CREATE_RESOURCES).
    'csg': _ALLOCATION_ADMIN | {Permission.EDIT_RESOURCES},

    # ssg: read-only across the board, plus resource create/edit and
    # edit system status (for outages...)
    'ssg': ALL_VIEW | {
        Permission.ACCESS_ADMIN_DASHBOARD,
        Permission.EDIT_RESOURCES, Permission.CREATE_RESOURCES,
        Permission.EDIT_SYSTEM_STATUS
    },
}

# Per-user permission overrides: {username: {Permission, ...}}, additive to
# whatever the user's group memberships confer. For one-off grants that do not
# justify touching a bundle or POSIX group membership.
USER_PERMISSION_OVERRIDES: Dict[str, Set[Permission]] = {
    # 'someuser': {Permission.EXPORT_DATA, Permission.VIEW_REPORTS},
    'benkirk' : [p for p in Permission],  # admin-equivalent: full access
    'mcjones' : ALL_VIEW | {
        Permission.ACCESS_ADMIN_DASHBOARD,
    },
}

# full permissions for some other app/dev/investigators:
USER_PERMISSION_OVERRIDES['kyledavis'] = USER_PERMISSION_OVERRIDES['benkirk']
#USER_PERMISSION_OVERRIDES['mtrahan'] = USER_PERMISSION_OVERRIDES['benkirk']


# Per-user, per-facility grants — the third RBAC tier, additive to the two
# unconditional ones above. ``permission`` applies only when the target
# project's facility is in the set. Any number of facilities per user.
#
# Format: {username: {facility_name: {Permission, ...}}}
USER_FACILITY_PERMISSIONS: Dict[str, Dict[str, Set[Permission]]] = {
    # WNA-scoped admin — provisions and manages WNA projects and
    # allocations. Holds no authority over NCAR/UNIV/CISL/CSL/XSEDE/ASD.
    'sureshm': {
        'WNA': {
            Permission.ACCESS_ADMIN_DASHBOARD,
            Permission.VIEW_PROJECTS,
            Permission.EDIT_PROJECTS,
            Permission.CREATE_PROJECTS,
            Permission.VIEW_PROJECT_MEMBERS,
            Permission.EDIT_PROJECT_MEMBERS,
            Permission.VIEW_ALLOCATIONS,
            Permission.EDIT_ALLOCATIONS,
            Permission.CREATE_ALLOCATIONS,
            # Reference-data + directory viewers, granted globally: directory
            # lookup is inherently cross-facility, since project membership
            # spans users outside WNA. Write buttons stay hidden — they gate on
            # CREATE_/EDIT_/DELETE_, which this tier does not confer.
            Permission.VIEW_RESOURCES,
            Permission.VIEW_ORG_METADATA,
            # Enumerated tiers get no ALL_VIEW auto-pickup, so every new VIEW_*
            # domain must be added here by hand or this tier silently loses its
            # card — as *_CONTRACTS nearly did.
            Permission.VIEW_CONTRACTS,
            Permission.VIEW_FACILITIES,
            Permission.VIEW_USERS,
            Permission.VIEW_GROUPS,
            # NOTE — VIEW_XRAS / MANAGE_XRAS are deliberately absent: the one
            # VIEW_* domain the rule above does NOT cover. An XRAS action is not
            # facility-scopable — a New action has no project yet, only a
            # requestNumber, and a malformed body has no facility at all. The
            # XRAS routes therefore gate on plain require_permission(), so a
            # scoped manager gets a clean 403 rather than a partial, misleading
            # view of an integration log.
        },
    },
}


def get_user_permissions(user) -> Set[Permission]:
    """Union of the user's ``GROUP_PERMISSIONS`` bundles (keyed by
    ``user.roles``) and their ``USER_PERMISSION_OVERRIDES``."""
    permissions: Set[Permission] = set()

    for group_name in user.roles:
        if group_name in GROUP_PERMISSIONS:
            permissions.update(GROUP_PERMISSIONS[group_name])

    overrides = USER_PERMISSION_OVERRIDES.get(getattr(user, 'username', None))
    if overrides:
        permissions.update(overrides)

    return permissions


def has_permission(user, permission: Permission) -> bool:
    """True if ``user`` holds ``permission`` unconditionally."""
    return permission in get_user_permissions(user)


def has_permission_for_facility(user, permission: Permission,
                                facility_name) -> bool:
    """True if ``user`` holds ``permission`` unconditionally, or holds it for
    ``facility_name`` via ``USER_FACILITY_PERMISSIONS``.

    ``facility_name`` is ``None`` for orphan projects (no allocation_type
    chain), which only unscoped system-permission holders can act on.
    """
    # System grant — applies to every facility, including unknown ones.
    if has_permission(user, permission):
        return True
    if facility_name is None:
        # Orphan projects: only unscoped system-permission holders can act.
        return False
    if not getattr(user, 'is_authenticated', False):
        return False
    username = getattr(user, 'username', None)
    if username is None:
        return False
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return permission in scoped.get(facility_name, set())


def has_permission_any_facility(user, permission: Permission) -> bool:
    """True if ``user`` can exercise ``permission`` **somewhere** — either
    unconditionally or in at least one facility.

    For route-level gates that admit scoped users; the body then intersects
    their scope against whatever the request targeted. Contrast with
    ``has_permission``, which asks "unconditionally?" — the right question for
    routes that must stay pure system-admin domain.
    """
    if has_permission(user, permission):
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    username = getattr(user, 'username', None)
    if username is None:
        return False
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return any(permission in perms for perms in scoped.values())


def user_facility_scope(user, permission: Permission):
    """Facility names where ``user`` may exercise ``permission``: ``None`` for
    unscoped (skip the filter), a set to constrain to, or an empty set for no
    way to exercise it at all."""
    if has_permission(user, permission):
        return None
    if not getattr(user, 'is_authenticated', False):
        return set()
    username = getattr(user, 'username', None)
    if username is None:
        return set()
    scoped = USER_FACILITY_PERMISSIONS.get(username, {})
    return {f for f, perms in scoped.items() if permission in perms}


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
    — every row returned by the summary / usage / transactions
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

    This does NOT check ``Permission.IMPERSONATE_USERS`` — the route decorator
    gates that. This enforces only the no-escalation invariant.
    """
    return get_user_permissions(target) <= get_user_permissions(caller)


def has_role(user, role_name: str) -> bool:
    """True if ``user`` belongs to the named ``GROUP_PERMISSIONS`` bundle.

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
    from webapp.utils.project_permissions import _is_project_steward

    def _can_act_on_project(permission, project, include_ancestors=False):
        if project is None:
            return False
        if current_user is None or not current_user.is_authenticated:
            return False
        return _is_project_steward(
            current_user, project, permission, include_ancestors=include_ancestors
        )

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
    }

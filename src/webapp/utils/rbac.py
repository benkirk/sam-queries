"""
Role-Based Access Control (RBAC) utilities for SAM Web UI.

Defines permissions, POSIX-group-to-permission mappings, and utility
functions for checking access. Works with both Flask-Admin views and
custom API endpoints.

Authorization model
-------------------
A user's permissions are derived from two sources, unioned together:

1. **POSIX group membership** — each group the user belongs to may map
   to a bundle of permissions in ``GROUP_PERMISSIONS``. Group membership
   is read from ``adhoc_system_account_entry`` via
   ``get_user_group_access()`` in dev, test, and production alike.

2. **Per-user overrides** — ``USER_PERMISSION_OVERRIDES`` grants
   additional permissions to specific usernames on top of whatever
   their groups confer. Useful for one-off privilege grants without
   touching group membership.

There is **no dependency on the SAM ``role_user`` / ``role`` tables**;
those are not consulted by the webapp's RBAC layer.

The string set returned by ``AuthUser.roles`` is the set of POSIX group
names the user belongs to that have a ``GROUP_PERMISSIONS`` bundle.
This keeps ``has_role('csg')``-style checks working as a coarse display
label, but the source of truth for authorization is the permission set,
not the role label.
"""

from enum import Enum
from typing import Set, List, Dict
from functools import wraps
from flask import abort
from flask_login import current_user


class Permission(Enum):
    """
    System-wide permissions for SAM Web UI.

    These permissions can be assigned to POSIX-group bundles (see
    ``GROUP_PERMISSIONS``) or granted to individual users (see
    ``USER_PERMISSION_OVERRIDES``), and checked in views, templates,
    and API endpoints.
    """

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
    # The user/project queue-load chart itself is visible to any logged-in
    # user on the status dashboard. This permission narrows to two
    # operator-only enrichments on top of that:
    #   1. The per-user / per-project rollup table on the queue-history
    #      drill-down page (richer than the chart legend).
    #   2. Click-through from the chart legend into the per-user and
    #      per-project detail modals (link_kind in _render_user_proj_chart).
    VIEW_SYSTEM_STATUS_USER_INFO = "view_system_status_user_info"
    MANAGE_SYSTEM_STATUS = "manage_system_status"  # Update system status data (collector/API)
    EDIT_SYSTEM_STATUS = "edit_system_status"  # GUI create/edit/delete outages
    VIEW_SYSTEM_CONFIG = "view_system_config"  # Read-only Configuration tab on Admin dashboard
    # XRAS integration triage. Split in two on purpose, because reading the audit
    # trail, reading the payloads, and re-submitting one are three different
    # authorities:
    #
    #   VIEW_XRAS    the Allocations > XRAS page — the action log, its filters and
    #                its error lists. Named ``view_*`` so ALL_VIEW auto-grants it to
    #                the operator bundles: this is an audit surface and the people
    #                who already read every other audit table should read this one.
    #
    #   MANAGE_XRAS  the raw payload panel AND the replay button. The payload is the
    #                request body verbatim and carries real PII — participant names,
    #                emails, phone numbers, grant-officer contacts — so it is gated
    #                above the audit view rather than with it. Replay is a write.
    #
    # MANAGE_XRAS is picked up by NO ``ALL_*`` aggregate (they match ``view_``/
    # ``edit_``/``create_``/``delete_`` prefixes on the *value*, and there is no
    # ALL_MANAGE), so it must be granted explicitly — see _ALLOCATION_ADMIN below.
    #   ADMIN_XRAS   the DESTRUCTIVE lifecycle verbs — delete a whole request,
    #                renew it, add an action. Irreversible in XRAS, so it rides
    #                with SYSTEM_ADMIN, NOT ``_ALLOCATION_ADMIN``: a MANAGE_XRAS
    #                operator gets the full non-destructive editor but never
    #                these (operator decision 2026-08-22,
    #                docs/xras/outgoing/REQUEST_EDITOR.md §1). Like MANAGE_XRAS the
    #                ``admin_`` prefix is matched by no ALL_* aggregate, so it
    #                fails closed and is held only where SYSTEM_ADMIN is — today
    #                the full-admin override, and any future system-admin bundle.
    VIEW_XRAS = "view_xras"
    MANAGE_XRAS = "manage_xras"
    ADMIN_XRAS = "admin_xras"
    SYSTEM_ADMIN = "system_admin"  # Full access to everything


# Building blocks for group bundles
# ----------------------------------
# ``_perms_with_action`` returns every Permission whose value starts
# with one of the given action prefixes — e.g. all ``VIEW_*`` or all
# ``EDIT_*``. The four ``ALL_*`` constants below pre-compute the common
# slices so bundles can use plain set arithmetic:
#
#     'foo': ALL_VIEW | ALL_EDIT | {Permission.EXPORT_DATA}
#     'bar': ALL_VIEW - {Permission.VIEW_GROUPS}
#
# When a new entity domain (e.g. CONTRACTS) gets a full CRUD set in the
# Permission enum, every bundle expressed via these constants picks up
# the new permissions automatically — no need to edit each bundle.
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


# POSIX-group-to-Permission mapping
#
# Keys are POSIX group names (e.g. real groups like 'csg', 'nusd', 'hsg'.
# A user receives the union of permissions across all groups they belong
# to that appear here.
#
# Groups that don't appear in this dict simply confer no permissions.
# ---- The allocation-administrator tier ----
#
# Provisions and manages projects, allocations and contracts end to end.
# The defining exclusion is the **definition layer**: an allocation
# administrator has no authority over resources, machines, queues or
# facilities — those describe the plant, not who may use it.
#
# Reads everything (ALL_VIEW), edits everything except that definition
# layer, and creates the entities its job requires.
#
# Deletes are enumerated positively rather than as ``ALL_DELETE - {...}``.
# Every delete granted here is a **soft** retire — the generated CRUD
# deletes set ``active=False`` (crud.py → handle_htmx_soft_delete) and the
# bespoke contract delete stamps ``end_date``. The withheld ones are where
# delete means something harsher or machine-shaped:
#
#   DELETE_RESOURCES   hard-deletes disk-root rows and fair-share override
#                      rows, and decommissions machines/queues
#   DELETE_FACILITIES  facility definitions
#   DELETE_USERS       hard row delete via the Flask-Admin layer
#   DELETE_GROUPS      ditto
#
# Failing closed matters more than the ALL_* auto-pickup here: a future
# ``delete_*`` domain must be added deliberately, not inherited.
#
# Known limitation (accepted): AllocationType and Panel are
# allocation-shaped concepts that live under the *_FACILITIES family, so
# default allocation amounts and fair-share percentages are NOT editable
# at this tier — see facilities_routes.py.
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
        # ``manage_`` prefix — which is the behaviour we want: an integration-admin
        # capability should not be swept in by a naming coincidence. This tier is
        # where it belongs: NUSD fields the XRAS failure mail from hdt@ucar.edu
        # today, and XRAS actions are allocation provisioning by another name.
        # (VIEW_XRAS needs no entry — ALL_VIEW above already carries it.)
        Permission.MANAGE_XRAS,
    }
)


GROUP_PERMISSIONS: Dict[str, Set[Permission]] = {
    # ---- Real POSIX group bundles (provisional) ----

    # nusd: the allocation-administrator tier, unmodified. Deliberately
    # holds nothing over the resource/facility definition layer — NUSD's
    # job is allocations and contracts, and they should not have to think
    # about machines or queues.
    #
    # May impersonate any user whose permission set is a subset of nusd's
    # (the can_impersonate rule blocks escalation). Note nusd is now a
    # strict subset of csg, so nusd cannot impersonate a csg user.
    'nusd': _ALLOCATION_ADMIN,

    # csg: the allocation-administrator tier PLUS edit on resources —
    # CSG runs the plant, so machines, queues, disk roots and per-facility
    # fair-share overrides stay editable. Create/delete of resources is
    # still withheld (ssg holds CREATE_RESOURCES for that).
    'csg': _ALLOCATION_ADMIN | {Permission.EDIT_RESOURCES},

    # ssg: read-only across the board, plus resource create/edit and
    # edit system status (for outages...)
    'ssg': ALL_VIEW | {
        Permission.ACCESS_ADMIN_DASHBOARD,
        Permission.EDIT_RESOURCES, Permission.CREATE_RESOURCES,
        Permission.EDIT_SYSTEM_STATUS
    },
}

# Per-user permission overrides
#
# Grants additional permissions to a specific username on top of
# whatever their group memberships confer. Useful for one-off privilege
# grants (e.g. a non-`hsg` user who needs EXPORT_DATA temporarily)
# without modifying group bundles or POSIX group membership.
#
# Keys: usernames. Values: set of Permission enum members to grant.
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


# Per-user, per-facility permission grants — the third RBAC tier.
#
# A user is granted ``permission`` here only when the target project's
# facility is in the configured set. Permissions held here are ADDITIVE
# to whatever ``USER_PERMISSION_OVERRIDES`` / ``GROUP_PERMISSIONS``
# confer (which apply unconditionally).
#
# Example — a WNA-scoped manager who may CRUD WNA projects/allocations
# but has no authority anywhere else:
#
#     'sureshm': {
#         'WNA': {
#             Permission.CREATE_PROJECTS, Permission.EDIT_PROJECTS,
#             Permission.CREATE_ALLOCATIONS, Permission.EDIT_ALLOCATIONS,
#             ...
#         },
#     }
#
# Multi-facility entries are supported — the outer dict's value may
# name any number of facilities, each mapping to its own permission set.
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
            # Reference-data + directory viewers: the admin dashboard's
            # Resources, Organizations, Facilities, and Users & Groups
            # tabs all pull read-only card fragments gated on these
            # VIEW_* permissions. Granting them here lets a scoped
            # manager see the cards globally (directory lookup is
            # inherently cross-facility — project membership spans
            # users outside WNA). Write buttons remain hidden — they
            # gate on CREATE_/EDIT_/DELETE_ which this tier does not
            # confer.
            Permission.VIEW_RESOURCES,
            Permission.VIEW_ORG_METADATA,
            # Enumerated tiers do NOT get the ALL_VIEW auto-pickup that
            # group bundles do, so a new *_CONTRACTS family had to be
            # added here by hand or this tier would silently lose the
            # contracts card. Any future VIEW_* domain needs the same.
            Permission.VIEW_CONTRACTS,
            Permission.VIEW_FACILITIES,
            Permission.VIEW_USERS,
            Permission.VIEW_GROUPS,
            # NOTE — VIEW_XRAS / MANAGE_XRAS are deliberately absent, and this is
            # the one VIEW_* domain the "add every new one here" rule above does
            # NOT apply to. An XRAS action is not facility-scopable: it arrives
            # before we know its facility (a New action has no project yet, only a
            # requestNumber), and a malformed body has no facility at all — there
            # is nothing to intersect a scope against. Rather than invent a
            # fallback rule for the unscopable rows, the XRAS routes gate on plain
            # require_permission(), so a facility-scoped manager gets a clean 403
            # instead of a partial, misleading view of an integration log.
        },
    },
}


def get_user_permissions(user) -> Set[Permission]:
    """
    Get all permissions for a user.

    Composes the union of:
    - Permissions from each POSIX group the user belongs to that has a
      bundle in ``GROUP_PERMISSIONS`` (read from ``user.roles``, which
      is the set of bundle-matching group names the AuthUser exposed)
    - Per-user overrides from ``USER_PERMISSION_OVERRIDES``

    Args:
        user: AuthUser object (Flask-Login current_user)

    Returns:
        Set of Permission enum values the user has
    """
    permissions: Set[Permission] = set()

    for group_name in user.roles:
        if group_name in GROUP_PERMISSIONS:
            permissions.update(GROUP_PERMISSIONS[group_name])

    overrides = USER_PERMISSION_OVERRIDES.get(getattr(user, 'username', None))
    if overrides:
        permissions.update(overrides)

    return permissions


def has_permission(user, permission: Permission) -> bool:
    """
    Check if user has a specific permission.

    Args:
        user: AuthUser object
        permission: Permission to check

    Returns:
        True if user has the permission, False otherwise
    """
    return permission in get_user_permissions(user)


def has_permission_for_facility(user, permission: Permission,
                                facility_name) -> bool:
    """
    Check if ``user`` holds ``permission`` for the given facility.

    True iff either:
    - The user has ``permission`` unconditionally (system grant via
      groups or ``USER_PERMISSION_OVERRIDES``) — applies to any facility.
    - ``USER_FACILITY_PERMISSIONS[user.username][facility_name]``
      contains ``permission``.

    Args:
        user: AuthUser object (Flask-Login current_user). Unauthenticated
            users always fail.
        permission: Permission enum member to check.
        facility_name: ``Facility.facility_name`` string, or ``None``
            for orphan projects (no allocation_type chain). Orphans can
            only be acted on by unscoped system-permission holders.

    Returns:
        True if the permission applies to this facility, else False.
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
    """True if ``user`` can exercise ``permission`` **somewhere** —
    either unconditionally (system grant) or in at least one facility
    via ``USER_FACILITY_PERMISSIONS``.

    Use this for route-level gates that admit scoped users: they reach
    the route, and the body intersects their scope against whatever
    the request targeted (listing filter, create-target facility, …).

    Contrast with ``has_permission``: that one answers "does the user
    hold this unconditionally?" — the right question for routes that
    must remain pure system-admin domain (impersonation, system
    status, etc.)."""
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
    """
    Return the set of facility names where ``user`` may exercise
    ``permission``, or ``None`` for "unscoped" (any facility, including
    orphan projects).

    Use at listing-filter call sites:
      - ``None`` → skip the facility filter entirely (system-permission
        holder; sees everything).
      - ``set`` → constrain results to those facilities.
      - empty ``set`` → user has no way to exercise this permission.

    Args:
        user: AuthUser object.
        permission: Permission enum member.
    """
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
    """
    The user's facility-name universe for ``permission``, as a sorted list.

    For facility-scoped users this is their grant (sorted); for unscoped
    users (system-permission holders) it is every facility name in the DB,
    filtered to active facilities unless ``active_only=False``.

    Use for building facility selector vocabularies (multi-selects,
    filter pills) — enforcement still belongs to ``apply_facility_scope``
    / ``filter_rows_by_facility`` at query time.
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
    """
    Combine a user-submitted ``facilities`` list with the caller's
    facility-scoped RBAC grants for ``permission``, returning the
    effective facility-name list to pass to downstream queries.

    Semantics:
    - **Unscoped users** (system-permission holders): ``requested``
      wins; if empty, ``default`` applies; ``None`` means
      "no restriction".
    - **Scoped users**: returns the intersection of ``requested`` with
      their allowed set. Falls back to the full allowed set when the
      request is empty or the intersection is empty (clamp, don't
      error — the user just asked for nothing they can see).
    - **Users with an empty scope** (no entry at all): returns ``[]``.
      Caller should treat as "no rows".

    Used as the single source of truth for "what facility names do I
    actually filter on, given this user + this request?" at both the
    admin expirations/search routes and the allocations dashboard.
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
    """
    Decide whether ``caller`` is permitted to impersonate ``target``.

    No-escalation rule: ``target``'s permission set must be a subset of
    ``caller``'s. Equal sets (peer impersonation) are allowed; strictly
    smaller sets ("lessor" users — regular users, project leads with no
    system permissions, etc.) are allowed; any permission ``target``
    holds that ``caller`` does not blocks the impersonation.

    Note: this does NOT check whether ``caller`` has
    ``Permission.IMPERSONATE_USERS`` — the route decorator should still
    gate that. ``can_impersonate`` only enforces the no-escalation
    invariant once impersonation is otherwise allowed.

    Args:
        caller: AuthUser doing the impersonation.
        target: AuthUser being impersonated.

    Returns:
        True if ``target``'s permissions are a (non-strict) subset of
        ``caller``'s permissions; False otherwise.
    """
    return get_user_permissions(target) <= get_user_permissions(caller)


def has_role(user, role_name: str) -> bool:
    """
    Check if user belongs to a specific group bundle (display label).

    Note: 'role' here is the name of a ``GROUP_PERMISSIONS`` bundle
    (POSIX group name). For authorization decisions prefer
    ``has_permission``; use this only for display logic or coarse
    role-name checks.
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
    """
    Decorator that admits callers who hold ``permission`` either
    unconditionally (system grant) **or** in at least one facility via
    ``USER_FACILITY_PERMISSIONS``.

    The route body is then responsible for intersecting the user's
    facility scope against whatever the request targets. Use for admin
    routes that a facility-scoped manager must be able to reach (e.g.
    the admin dashboard, project search, expirations fragment,
    project-create form) even though they don't hold the permission
    globally.

    For routes that must remain pure system-admin domain (impersonation,
    global system administration), use ``require_permission`` instead.
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
    # rbac → project_permissions → rbac at module import time.
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

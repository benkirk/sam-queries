"""The factory-default role catalog: what the code enforces in
``RBAC_SOURCE=defaults`` mode, what ``sam-admin rbac --seed`` writes, and
what ``--diff`` compares a live database against.

Roles are enumerated, never ``ALL_EDIT - {...}`` arithmetic in the seed: the
DB editor shows exact rows, and ``sam-admin rbac --diff`` plus the WITHHELD
gate test keep a new ``Permission`` from being silently absent. Facility
scope lives on the grant, not the role.
"""

from sam.security.permissions import (ALL_CREATE, ALL_DELETE, ALL_EDIT, ALL_VIEW,
                                      Permission)
from sam.security.rbac_catalog import GrantDef, RoleDef

P = Permission

# ---- The allocation-administrator tier ----
# Provisions projects, allocations and contracts end to end. The defining
# exclusion is the definition layer: resources, machines, queues and
# facilities describe the plant, not who may use it.
# WARNING: deletes are enumerated positively; every one is a SOFT retire
# (generated CRUD sets active=False, the contract delete stamps end_date).
# DELETE_RESOURCES decommissions resources/machines and expires queues, and
# DELETE_FACILITIES / DELETE_USERS / DELETE_GROUPS have no web surface, so all
# four stay withheld. Known limitation: AllocationType and Panel live under
# *_FACILITIES, so default amounts and fair-share percentages are not editable
# at this tier.
_ALLOCATION_ADMIN = frozenset(
    ALL_VIEW
    | (ALL_EDIT - {P.EDIT_RESOURCES, P.EDIT_FACILITIES})
    | {
        P.ACCESS_ADMIN_DASHBOARD,
        P.CREATE_PROJECTS, P.CREATE_ALLOCATIONS,
        P.CREATE_ORG_METADATA, P.CREATE_CONTRACTS,
        P.DELETE_PROJECTS, P.DELETE_ALLOCATIONS,
        P.DELETE_ORG_METADATA, P.DELETE_CONTRACTS,
        P.IMPERSONATE_USERS,
        # XRAS payloads + replay: NUSD fields the XRAS failure mail, and an
        # XRAS action is allocation provisioning by another name.
        P.MANAGE_XRAS,
        # The account-request queue is NUSD's worklist by design.
        P.MANAGE_ACCOUNT_REQUESTS,
    }
)

# The WNA program manager's set. Reference-data viewers are included because
# directory lookup is inherently cross-facility; write buttons stay hidden
# because they gate on CREATE_/EDIT_/DELETE_. VIEW_XRAS / MANAGE_XRAS are
# deliberately absent: an XRAS action is not facility-scopable, and the XRAS
# routes gate on plain require_permission so a scoped manager gets a clean 403.
_FACILITY_MANAGER = frozenset({
    P.ACCESS_ADMIN_DASHBOARD,
    P.VIEW_PROJECTS, P.EDIT_PROJECTS, P.CREATE_PROJECTS,
    P.VIEW_PROJECT_MEMBERS, P.EDIT_PROJECT_MEMBERS,
    P.VIEW_ALLOCATIONS, P.EDIT_ALLOCATIONS, P.CREATE_ALLOCATIONS,
    P.VIEW_RESOURCES, P.VIEW_ORG_METADATA, P.VIEW_CONTRACTS,
    P.VIEW_FACILITIES, P.VIEW_USERS, P.VIEW_GROUPS,
})

# What any API key can reach today: the union the token-accepting
# legacy-compat blueprints declare (api/v1 fstree_access, project_access,
# directory_access, disk_quota, queue, wallclock_exemption, users, fairshare).
_API_LEGACY = frozenset({
    P.VIEW_USERS, P.VIEW_PROJECTS, P.VIEW_RESOURCES, P.VIEW_ALL_JOB_DATA,
})

DEFAULT_ROLES = (
    RoleDef('allocation_admin', _ALLOCATION_ADMIN,
            description='Projects, allocations and contracts end to end; '
                        'not the resources/facilities layer.'),
    # csg runs the plant: edit on resources, the event lifecycle and the
    # /database browser. Create/delete of resources stays with ssg.
    RoleDef('csg', frozenset({P.EDIT_RESOURCES, P.MANAGE_EVENTS, P.ADMIN_DATABASE}),
            extends='allocation_admin',
            description='allocation_admin plus resources edit, events and the database browser.'),
    RoleDef('ssg', frozenset(ALL_VIEW | {P.ACCESS_ADMIN_DASHBOARD, P.EDIT_RESOURCES,
                                         P.CREATE_RESOURCES, P.EDIT_SYSTEM_STATUS}),
            description='Read everything; create/edit resources; post outages.'),
    RoleDef('system_admin', frozenset({P.SYSTEM_ADMIN}),
            description='Every permission, including the ones added tomorrow.'),
    RoleDef('viewer', frozenset(ALL_VIEW | {P.ACCESS_ADMIN_DASHBOARD}),
            description='Read-only access to the admin dashboards.'),
    RoleDef('facility_manager', _FACILITY_MANAGER,
            description='Provision and manage projects and allocations; grant per facility.'),
    RoleDef('api_legacy', _API_LEGACY,
            description='What the legacy integration endpoints declare; every key held this before enforcement.'),
    RoleDef('api_collector', frozenset({P.MANAGE_SYSTEM_STATUS, P.MANAGE_CHARGE_SUMMARIES}),
            description='Status and charge-summary ingest.'),
    RoleDef('api_admin', frozenset({P.SYSTEM_ADMIN}),
            description='The sam-admin cache --refresh credential.'),
)

DEFAULT_GRANTS = (
    GrantDef('group', 'nusd', role='allocation_admin'),
    GrantDef('group', 'csg', role='csg'),
    GrantDef('group', 'ssg', role='ssg'),
    GrantDef('user', 'benkirk', role='system_admin'),
    GrantDef('user', 'kyledavis', role='system_admin'),
    GrantDef('user', 'mcjones', role='viewer'),
    GrantDef('user', 'sureshm', role='facility_manager', facility='WNA'),
    GrantDef('apikey', 'collector', role='api_collector'),
)

#: Permissions no default role grants. A new Permission goes into a role above
#: or into this set, deliberately; the gate test refuses a third option.
WITHHELD = frozenset({
    P.CREATE_USERS, P.DELETE_USERS,
    P.CREATE_GROUPS, P.DELETE_GROUPS,
    P.DELETE_RESOURCES, P.DELETE_FACILITIES,
    P.EDIT_FACILITIES, P.CREATE_FACILITIES,
    P.EXPORT_DATA,
    P.MANAGE_ROLES, P.ADMIN_XRAS,
})

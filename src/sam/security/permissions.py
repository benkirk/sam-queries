"""The webapp's permission vocabulary, as pure data.

Lives under ``sam`` so ORM models, form schemas, the CLI and the role seed can
name a permission without importing Flask; ``webapp.utils.rbac`` re-exports
every name here.
"""

from enum import Enum
from typing import Set


class Permission(Enum):
    """System-wide permissions. The value is the wire/DB form (``view_users``)."""

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
    # The HPC account-request queue (Admin -> Accounts) and the project-side
    # invitation surface. ``manage_`` on purpose: SAM never creates accounts,
    # so the holder works a worklist handed to NUSD, and a project's lead or
    # admin reaches the invitation routes through the steward check instead
    # (docs/plans/implemented/ACCOUNT_REGISTRATION.md section 2.2).
    MANAGE_ACCOUNT_REQUESTS = "manage_account_requests"
    # The event lifecycle everywhere: Admin -> Events, and create / edit /
    # close / reopen on a project's Invitations tab. Inviting people and
    # pasting rosters stay on MANAGE_ACCOUNT_REQUESTS.
    MANAGE_EVENTS = "manage_events"
    # The read-only /database row browser over every engine the webapp holds
    # (webapp/db_browser). ``admin_`` so no ALL_* aggregate grants it: rows
    # include PII, and redaction covers secrets, not people.
    ADMIN_DATABASE = "admin_database"
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

"""
SAM Query Helpers

Centralized database query functions organized by domain.
All functions are re-exported from submodules for backward compatibility.

Usage:
    from sam.queries import find_user_by_username, get_projects_expiring_soon

Or use direct imports for explicit dependencies:
    from sam.queries.lookups import find_user_by_username
    from sam.queries.expirations import get_projects_expiring_soon

Modules:
    lookups: Simple find/get operations for users, projects, groups, resources
    expirations: Project expiration tracking and queries
    dashboard: Dashboard data aggregation for users and projects
    charges: Charge aggregation and usage queries
    projects: Project search and filtering
    users: User-related queries and project membership
    allocations: Allocation queries and history
    statistics: Statistics and reporting
    directory_access: LDAP directory access (unix groups + accounts by access branch)
    project_access: LDAP project group status (by access branch)
    fstree_access: PBS fairshare tree (Facility → AllocationType → Project → Resource)
"""

# Lookups
from .lookups import (
    get_available_resources,
    get_resources_by_type,
    find_user_by_username,
    find_users_by_name,
    find_project_by_code,
    get_group_by_name,
    get_user_group_access,
    get_group_members,
)

# Expirations
from .expirations import (
    get_projects_by_allocation_end_date,
    get_projects_expiring_soon,
    get_projects_with_expired_allocations,
)

# Dashboard
from .dashboard import (
    get_user_dashboard_data,
    get_project_dashboard_data,
    get_resource_detail_data,
)

# Charges
from .charges import (
    get_daily_charge_trends_for_accounts,
    get_raw_charge_summaries_for_accounts,
    get_recent_charge_adjustments,
    get_user_breakdown_for_project,
    get_user_queue_breakdown_for_project,
    get_daily_breakdown_for_project,
)

# Projects
from .projects import (
    search_projects_by_code_or_title,
    search_projects_by_title,
    get_active_projects,
    get_projects_by_lead,
    get_project_with_full_details,
    get_project_members,
)

# Users
from .users import (
    get_users_on_project,
    get_project_member_user_ids,
    search_users_by_pattern,
    search_users_by_email,
    get_active_users,
    get_user_with_details,
    get_users_by_institution,
    get_users_by_organization,
    get_user_emails,
    get_user_emails_detailed,
    get_users_with_multiple_emails,
    get_users_without_primary_email,
)

# Allocations
from .allocations import (
    get_project_allocations,
    get_active_allocation,
    get_latest_allocation_for_project,
    get_allocation_history,
    get_recent_allocation_transactions,
    get_allocations_by_type,
    get_allocations_by_resource,
    get_allocation_summary_by_facility,
    get_allocation_summary,
    get_allocation_summary_with_usage,
)

# Usage cache
from .usage_cache import (
    cached_allocation_usage,
    purge_usage_cache,
    usage_cache_info,
)

# Statistics
from .statistics import (
    get_user_statistics,
    get_project_statistics,
    get_institution_project_count,
    get_user_project_access,
)

# Directory Access (LDAP population)
from .directory_access import (
    group_populator,
    user_populator,
    build_directory_access_response,
)

# Project Access (LDAP project group status)
from .project_access import (
    get_project_group_status,
)

# FairShare Tree (PBS fairshare / scheduler tree)
from .fstree_access import (
    get_fstree_data,
    get_project_fsdata,
    get_user_fsdata,
)

# Rolling window usage (30/90-day trailing charge analysis)
from .rolling_usage import get_project_rolling_usage

# Shell queries
from .shells import (
    active_login_resources,
    get_allowable_shell_names,
    get_user_current_shell,
)

# Admin dashboard queries (heavy eager-load chains)
from .admin import (
    get_organizations_with_members,
    get_institution_type_tree,
    get_institutions_with_members,
    get_aoi_groups_with_areas,
    get_areas_of_interest_with_projects,
    get_contracts_with_pi,
    get_nsf_programs_with_contracts,
)


__all__ = [
    # Lookups
    'get_available_resources',
    'get_resources_by_type',
    'find_user_by_username',
    'find_users_by_name',
    'find_project_by_code',
    'get_group_by_name',
    'get_user_group_access',
    'get_group_members',
    # Expirations
    'get_projects_by_allocation_end_date',
    'get_projects_expiring_soon',
    'get_projects_with_expired_allocations',
    # Dashboard
    'get_user_dashboard_data',
    'get_project_dashboard_data',
    'get_resource_detail_data',
    # Charges
    'get_daily_charge_trends_for_accounts',
    'get_raw_charge_summaries_for_accounts',
    'get_recent_charge_adjustments',
    'get_user_breakdown_for_project',
    'get_user_queue_breakdown_for_project',
    'get_daily_breakdown_for_project',
    # Projects
    'search_projects_by_code_or_title',
    'search_projects_by_title',
    'get_active_projects',
    'get_projects_by_lead',
    'get_project_with_full_details',
    'get_project_members',
    # Users
    'get_users_on_project',
    'get_project_member_user_ids',
    'search_users_by_pattern',
    'search_users_by_email',
    'get_active_users',
    'get_user_with_details',
    'get_users_by_institution',
    'get_users_by_organization',
    'get_user_emails',
    'get_user_emails_detailed',
    'get_users_with_multiple_emails',
    'get_users_without_primary_email',
    # Allocations
    'get_project_allocations',
    'get_active_allocation',
    'get_latest_allocation_for_project',
    'get_allocation_history',
    'get_recent_allocation_transactions',
    'get_allocations_by_type',
    'get_allocations_by_resource',
    'get_allocation_summary_by_facility',
    'get_allocation_summary',
    'get_allocation_summary_with_usage',
    # Usage cache
    'cached_allocation_usage',
    'purge_usage_cache',
    'usage_cache_info',
    # Statistics
    'get_user_statistics',
    'get_project_statistics',
    'get_institution_project_count',
    'get_user_project_access',
    # Directory Access
    'group_populator',
    'user_populator',
    'build_directory_access_response',
    # Project Access
    'get_project_group_status',
    # FairShare Tree
    'get_fstree_data',
    'get_project_fsdata',
    'get_user_fsdata',
    # Rolling window usage
    'get_project_rolling_usage',
    # Shells
    'active_login_resources',
    'get_allowable_shell_names',
    'get_user_current_shell',
    # Admin dashboard queries
    'get_organizations_with_members',
    'get_institution_type_tree',
    'get_institutions_with_members',
    'get_aoi_groups_with_areas',
    'get_areas_of_interest_with_projects',
    'get_contracts_with_pi',
    'get_nsf_programs_with_contracts',
]

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
"""

# Lookups
from .lookups import (
    get_available_resources,
    get_resources_by_type,
    find_user_by_username,
    find_users_by_name,
    find_project_by_code,
    get_group_by_name,
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
    get_user_charge_summary,
    get_project_usage_summary,
    get_daily_usage_trend,
    get_jobs_for_project,
    get_queue_usage_breakdown,
    get_user_usage_on_project,
    get_user_breakdown_for_project,
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
    get_allocations_by_type,
    get_allocations_by_resource,
    get_allocation_summary_by_facility,
    get_allocation_summary,
    get_allocation_summary_with_usage,
)

# Statistics
from .statistics import (
    get_user_statistics,
    get_project_statistics,
    get_institution_project_count,
    get_user_project_access,
)


__all__ = [
    # Lookups
    'get_available_resources',
    'get_resources_by_type',
    'find_user_by_username',
    'find_users_by_name',
    'find_project_by_code',
    'get_group_by_name',
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
    'get_user_charge_summary',
    'get_project_usage_summary',
    'get_daily_usage_trend',
    'get_jobs_for_project',
    'get_queue_usage_breakdown',
    'get_user_usage_on_project',
    'get_user_breakdown_for_project',
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
    'get_allocations_by_type',
    'get_allocations_by_resource',
    'get_allocation_summary_by_facility',
    'get_allocation_summary',
    'get_allocation_summary_with_usage',
    # Statistics
    'get_user_statistics',
    'get_project_statistics',
    'get_institution_project_count',
    'get_user_project_access',
]

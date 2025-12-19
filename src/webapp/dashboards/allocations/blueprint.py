"""
Allocations dashboard blueprint for admin/staff.

Provides drill-down allocation dashboard showing allocation summaries
grouped hierarchically by Resource → Facility → Allocation Type → Projects.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required
from datetime import datetime, timedelta
from typing import List, Dict

from webapp.extensions import db
from sam.queries.allocations import get_allocation_summary, get_allocation_summary_with_usage
from sam.queries.lookups import find_project_by_code
from webapp.utils.rbac import require_permission, Permission
from ..charts import generate_facility_pie_chart_matplotlib


from sam.resources.resources import Resource

bp = Blueprint('allocations_dashboard', __name__, url_prefix='/allocations')

# Resources to hide by default from the dashboard
HIDDEN_RESOURCES = ["CMIP Analysis Platform", "Data_Access", "HPC_Futures_Lab"]


def group_by_resource_facility(summary_data: List[Dict]) -> Dict:
    """
    Transform flat summary list into nested structure for tabs.

    Args:
        summary_data: List of allocation summary dicts from get_allocation_summary()

    Returns:
        Nested dict structure:
        {
            'Derecho': {
                'UNIV': [
                    {'allocation_type': 'NSC', 'total_amount': 641710650, 'count': 26, ...},
                    {'allocation_type': 'Small', 'total_amount': 177267070, 'count': 248, ...}
                ],
                'WNA': [...]
            },
            'Casper': {...}
        }
    """
    grouped = {}
    for row in summary_data:
        resource = row['resource']
        facility = row['facility']

        if resource not in grouped:
            grouped[resource] = {}
        if facility not in grouped[resource]:
            grouped[resource][facility] = []

        grouped[resource][facility].append(row)

    return grouped


def get_facility_overview_data(session, resource_name: str, active_at: datetime) -> List[Dict]:
    """
    Calculate facility-level summaries for a specific resource.

    Queries individual allocations and properly calculates annualized rates
    using actual allocation durations, then aggregates by facility.

    Args:
        session: SQLAlchemy session
        resource_name: Resource to filter for
        active_at: Date to check for active status

    Returns:
        List of dicts with keys: facility, annualized_rate, count, total_amount, percent
    """
    # Get individual allocations for this resource (not aggregated)
    # This ensures each allocation has a properly calculated annualized_rate
    individual_allocations = get_allocation_summary(
        session=session,
        resource_name=resource_name,
        facility_name=None,      # Group by all facilities
        allocation_type=None,    # Group by all types
        projcode=None,           # Group by individual projects (not aggregated)
        active_only=True,
        active_at=active_at
    )

    # Aggregate by facility, summing annualized rates
    facility_totals = {}

    for alloc in individual_allocations:
        facility = alloc['facility']
        if facility not in facility_totals:
            facility_totals[facility] = {
                'total_amount': 0.0,
                'annualized_rate': 0.0,
                'count': 0
            }

        facility_totals[facility]['total_amount'] += alloc['total_amount']
        facility_totals[facility]['count'] += alloc['count']

        # Sum annualized rates (properly calculated from actual durations)
        if alloc.get('annualized_rate') is not None:
            facility_totals[facility]['annualized_rate'] += alloc['annualized_rate']

    # Calculate percentages based on annualized rate
    total_annualized_rate = sum(f['annualized_rate'] for f in facility_totals.values())

    overview = []
    for facility, data in facility_totals.items():
        percent = (data['annualized_rate'] / total_annualized_rate * 100) if total_annualized_rate > 0 else 0
        overview.append({
            'facility': facility,
            'total_amount': data['total_amount'],
            'annualized_rate': data['annualized_rate'],
            'count': data['count'],
            'percent': percent
        })

    # Sort by annualized rate descending
    overview.sort(key=lambda x: x['annualized_rate'], reverse=True)

    return overview


@bp.route('/')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def index():
    """
    Main allocations dashboard page.

    Shows allocation summaries grouped by Resource → Facility → Type.
    Active allocations only, with optional date filter and resource selector.

    Query parameters:
        active_at: Date to check for active status (YYYY-MM-DD), default: today
        resources: List of resource names to display
    """
    # Parse active_at parameter (default to today)
    active_at_str = request.args.get('active_at')
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'error')
            active_at = datetime.now()
    else:
        active_at = datetime.now()

    # Get all active resources for the selector
    all_resources = [
        r.resource_name for r in db.session.query(Resource.resource_name)
        .filter(Resource.is_active == True)
        .order_by(Resource.resource_name)
        .all()
    ]

    # Parse selected resources
    selected_resources = request.args.getlist('resources')
    if not selected_resources:
        # Default subset: all active resources except HIDDEN_RESOURCES
        selected_resources = [r for r in all_resources if r not in HIDDEN_RESOURCES]

    # Get summary data grouped by Resource, Facility, Type (sum across projects)
    # We use projcode="TOTAL" to sum across all projects
    summary_data = get_allocation_summary(
        session=db.session,
        resource_name=selected_resources, # Filtered list
        facility_name=None,      # Group by all facilities
        allocation_type=None,    # Group by all types
        projcode="TOTAL",        # Sum across projects
        active_only=True,
        active_at=active_at
    )

    # Group results hierarchically for tab structure
    grouped_data = group_by_resource_facility(summary_data)

    # For each resource, generate facility overview data and pie chart
    resource_overviews = {}
    for resource_name in grouped_data.keys():
        overview_data = get_facility_overview_data(db.session, resource_name, active_at)
        pie_chart = generate_facility_pie_chart_matplotlib(overview_data)
        resource_overviews[resource_name] = {
            'table_data': overview_data,
            'chart': pie_chart
        }

    return render_template(
        'dashboards/allocations/dashboard.html',
        grouped_data=grouped_data,
        resource_overviews=resource_overviews,
        active_at=active_at.strftime('%Y-%m-%d'),
        all_resources=all_resources,
        selected_resources=selected_resources
    )


@bp.route('/projects')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def projects_fragment():
    """
    AJAX fragment showing individual projects for a specific Resource/Facility/Type.

    Query parameters:
        resource: Resource name (required)
        facility: Facility name (required)
        allocation_type: Allocation type (required)
        active_at: Date to check for active status (YYYY-MM-DD)

    Returns:
        HTML table fragment of projects
    """
    resource = request.args.get('resource')
    facility = request.args.get('facility')
    allocation_type = request.args.get('allocation_type')
    active_at_str = request.args.get('active_at')

    # Validate required params
    if not resource or not facility or not allocation_type:
        return '<p class="text-danger mb-0">Missing required parameters</p>'

    # Parse date
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            return '<p class="text-danger mb-0">Invalid date format</p>'
    else:
        active_at = datetime.now()

    # Get individual projects (projcode=None means group by projects)
    projects = get_allocation_summary(
        session=db.session,
        resource_name=resource,
        facility_name=facility,
        allocation_type=allocation_type,
        projcode=None,  # Group by individual projects
        active_only=True,
        active_at=active_at
    )

    if not projects:
        return '<p class="text-muted mb-0">No active projects found</p>'

    # Enrich with project titles
    from sam.projects.projects import Project
    for project_data in projects:
        project = find_project_by_code(db.session, project_data['projcode'])
        project_data['title'] = project.title if project else None

    # Sort by amount descending
    projects.sort(key=lambda p: p['total_amount'], reverse=True)

    return render_template(
        'dashboards/allocations/partials/project_table.html',
        projects=projects,
        resource=resource,
        facility=facility,
        allocation_type=allocation_type,
        active_at=active_at.strftime('%Y-%m-%d')
    )


@bp.route('/usage/<projcode>/<resource>')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def usage_modal(projcode: str, resource: str):
    """
    AJAX fragment showing detailed usage for a specific project+resource.

    Returns:
        HTML fragment for Bootstrap modal body showing usage breakdown
    """
    active_at_str = request.args.get('active_at')

    # Parse date
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            return '<p class="text-danger mb-0">Invalid date format</p>'
    else:
        active_at = datetime.now()

    # Get project
    project = find_project_by_code(db.session, projcode)
    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    # Get allocation with usage details
    usage_data = get_allocation_summary_with_usage(
        session=db.session,
        resource_name=resource,
        projcode=projcode,
        active_only=True,
        active_at=active_at
    )

    if not usage_data:
        return '<p class="text-muted mb-0">No active allocation found</p>'

    # Should only be one result
    allocation_info = usage_data[0] if usage_data else None

    return render_template(
        'dashboards/allocations/partials/usage_modal.html',
        project=project,
        resource=resource,
        allocation=allocation_info,
        active_at=active_at.strftime('%Y-%m-%d')
    )

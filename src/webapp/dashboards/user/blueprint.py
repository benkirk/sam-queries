"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify
from flask_login import login_required, current_user, login_user
from datetime import datetime, timedelta

from webapp.extensions import db
from sam.queries import (
    get_user_dashboard_data, get_resource_detail_data, get_users_on_project,
    get_user_breakdown_for_project, get_jobs_for_project, find_project_by_code,
    get_project_dashboard_data, search_projects_by_code_or_title
)
from sam.projects.projects import Project
from webapp.auth.models import AuthUser
from sam.core.users import User
from webapp.utils.charts import generate_usage_timeseries_matplotlib
from webapp.utils.project_permissions import can_manage_project_members, can_change_admin
from webapp.utils.rbac import require_permission, Permission


bp = Blueprint('user_dashboard', __name__, url_prefix='/user')


# Usage threshold configuration (percentage)
USAGE_WARNING_THRESHOLD = 75  # Yellow warning
USAGE_CRITICAL_THRESHOLD = 90  # Red critical


@bp.route('/')
@login_required
def index():
    """
    Main user dashboard.

    Shows user's projects and their allocation spending.
    Data is loaded server-side using direct ORM queries for improved performance.
    """
    impersonator_id = session.get('impersonator_id')
    
    if impersonator_id:
        # When impersonating, current_user is the impersonated user.
        user_to_display = current_user
    else:
        user_to_display = current_user

    # Fetch all dashboard data using optimized query helper
    dashboard_data = get_user_dashboard_data(db.session, user_to_display.user_id)

    return render_template(
        'dashboards/user/dashboard.html',
        user=user_to_display,
        dashboard_data=dashboard_data,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD,
        impersonator_id=impersonator_id
    )


@bp.route('/impersonate', methods=['POST'])
@login_required
@require_permission(Permission.IMPERSONATE_USERS)
def impersonate():
    """
    Allows an admin to impersonate another user.
    """
    username = request.form.get('username')
    impersonator_id = current_user.user_id

    sam_user_to_impersonate = db.session.query(User).filter_by(username=username).first()

    if not sam_user_to_impersonate:
        flash(f'User "{username}" not found', 'error')
        return redirect(url_for('user_dashboard.index'))

    user_to_impersonate = AuthUser(sam_user_to_impersonate)

    # Prevent impersonating other admins unless you are a super-admin
    if user_to_impersonate.has_role('admin') and not current_user.has_role('admin'):
         flash('You do not have permission to impersonate an administrator.', 'danger')
         return redirect(url_for('user_dashboard.index'))

    # Store current user in session to be able to go back
    session['impersonator_id'] = impersonator_id

    # Log in as the other user
    login_user(user_to_impersonate)

    flash(f'You are now impersonating {user_to_impersonate.display_name}', 'success')
    return redirect(url_for('user_dashboard.index'))


@bp.route('/stop_impersonating')
@login_required
def stop_impersonating():
    """
    Stops impersonating and returns to the original user.
    """
    impersonator_id = session.get('impersonator_id')

    if not impersonator_id:
        flash('You are not currently impersonating anyone', 'warning')
        return redirect(url_for('user_dashboard.index'))

    sam_impersonator = db.session.query(User).filter_by(user_id=impersonator_id).first()

    if not sam_impersonator:
        flash('Could not find original user to restore session', 'error')
        # Clear the impersonation session key and send to login
        session.pop('impersonator_id', None)
        return redirect(url_for('auth.login'))

    impersonator = AuthUser(sam_impersonator)

    # Log back in as the original user
    login_user(impersonator)
    session.pop('impersonator_id', None)

    flash('You have stopped impersonating and returned to your account', 'success')
    return redirect(url_for('user_dashboard.index'))


@bp.route('/resource-details')
@login_required
def resource_details():
    """
    Resource usage detail view showing charts and job history.

    Query parameters:
        projcode: Project code
        resource: Resource name
        start_date: Optional start date (default: 90 days ago)
        end_date: Optional end date (default: today)

    Returns:
        HTML page with server-rendered charts and usage data
    """
    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        flash('Missing project code or resource name', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Parse date range (default to last 90 days)
    try:
        if request.args.get('start_date'):
            start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d')
        else:
            start_date = datetime.now() - timedelta(days=90)

        if request.args.get('end_date'):
            end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d')
        else:
            end_date = datetime.now()
    except ValueError:
        flash('Invalid date format. Please use YYYY-MM-DD.', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Fetch resource detail data
    detail_data = get_resource_detail_data(
        db.session,
        projcode,
        resource_name,
        start_date,
        end_date
    )

    if not detail_data:
        flash(f'Project {projcode} or resource {resource_name} not found', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Fetch user breakdown data
    user_breakdown = get_user_breakdown_for_project(
        db.session,
        projcode,
        start_date,
        end_date,
        resource_name
    )

    # Generate charts server-side
    usage_chart = generate_usage_timeseries_matplotlib(detail_data['daily_charges'])
    #breakdown_chart = generate_charge_breakdown_bars(detail_data['charge_totals'])

    return render_template(
        'dashboards/user/resource_details.html',
        user=current_user,
        projcode=projcode,
        resource_name=resource_name,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        detail_data=detail_data,
        user_breakdown=user_breakdown,
        usage_chart=usage_chart,
    )


@bp.route('/members/<projcode>')
@login_required
def members_fragment(projcode):
    """
    Lazy-loaded HTML fragment showing project members.

    Returns:
        HTML table of project members with management controls (if authorized)
    """
    project = Project.get_by_projcode(db.session, projcode)

    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    members = get_users_on_project(db.session, projcode)

    if not members:
        return '<p class="text-muted mb-0">No members found or project not accessible</p>'

    return render_template(
        'dashboards/user/fragments/members_table.html',
        members=sorted(members, key=lambda member: member["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/tree/<projcode>')
@login_required
def tree_fragment(projcode):
    """
    Lazy-loaded HTML fragment showing project hierarchy tree.

    Returns:
        HTML tree structure (no full page layout)
    """
    from sam.queries import find_project_by_code

    project = find_project_by_code(db.session, projcode)

    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    # Get the root of the project tree
    root = project.get_root() if hasattr(project, 'get_root') else project

    # Render tree structure recursively
    def render_tree_node(node, current_projcode, level=0):
        is_current = node.projcode == current_projcode
        style = 'background: #fff3cd; font-weight: bold; border-left-color: #ffc107;' if is_current else ''
        icon = '<i class="fas fa-arrow-right text-warning mr-1"></i>' if is_current else ''

        html = f'<li style="{style}">{icon}<strong>{node.projcode}</strong>'

        if node.title:
            html += f' - {node.title}'

        # Recursively render children
        children = node.children if hasattr(node, 'children') and node.children else []
        if children:
            html += '<ul class="tree-list">'
            for child in children:
                html += render_tree_node(child, current_projcode, level + 1)
            html += '</ul>'

        html += '</li>'
        return html

    tree_html = f'<ul class="tree-list">{render_tree_node(root, projcode)}</ul>'

    return tree_html


@bp.route('/jobs/<projcode>/<resource>')
@login_required
def jobs_fragment(projcode, resource):
    """
    Lazy-loaded HTML fragment showing project jobs with pagination.

    Query parameters:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        page: Page number (default 1)

    Returns:
        HTML table of jobs (no full page layout)
    """
    # Get date range from query params
    try:
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d')
    except (TypeError, ValueError):
        return '<p class="text-danger mb-0">Invalid date range</p>'

    # Pagination parameters
    page = int(request.args.get('page', 1))
    per_page = 50

    # Get all jobs to calculate total count
    from sam.queries import get_jobs_for_project
    all_jobs = get_jobs_for_project(
        db.session,
        projcode,
        start_date,
        end_date,
        resource
    )

    total_jobs = len(all_jobs)
    total_pages = (total_jobs + per_page - 1) // per_page  # Ceiling division

    # Get paginated subset
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    jobs = all_jobs[start_idx:end_idx]

    if not jobs and page == 1:
        return '<p class="text-muted mb-0">No jobs found for this period</p>'

    return render_template(
        'dashboards/user/fragments/jobs_table.html',
        jobs=jobs,
        page=page,
        per_page=per_page,
        total_jobs=total_jobs,
        total_pages=total_pages,
        projcode=projcode,
        resource=resource,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d')
    )



@bp.route('/project-card/<projcode>')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def project_card(projcode):
    """
    Get HTML fragment for a single project card (for admin project search).

    Returns:
        HTML project card fragment (calls the render_project_card macro)
    """
    # Get project data using the new helper function
    project_data = get_project_dashboard_data(db.session, projcode)

    if not project_data:
        return '<div class="alert alert-warning">Project not found</div>'

    # Render a wrapper template that calls the macro
    return render_template(
        'dashboards/user/fragments/project_card_wrapper.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    )


# Note: Member management API endpoints have been moved to /api/v1/projects/
# See src/webapp/api/v1/projects.py for:
#   POST /api/v1/projects/<projcode>/members - Add member
#   DELETE /api/v1/projects/<projcode>/members/<username> - Remove member
#   PUT /api/v1/projects/<projcode>/admin - Change admin
# See src/webapp/api/v1/users.py for:
#   GET /api/v1/users/search - Search users

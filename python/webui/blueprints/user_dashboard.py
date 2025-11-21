"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import login_required, current_user
from datetime import datetime, timedelta

from webui.extensions import db
from sam.queries import (
    get_user_dashboard_data, get_resource_detail_data, get_users_on_project,
    get_user_breakdown_for_project, get_jobs_for_project,
    add_user_to_project, remove_user_from_project, change_project_admin,
    search_users_by_pattern, get_project_member_user_ids, find_project_by_code
)
from sam.core.users import User
from sam.projects.projects import Project
from webui.utils.charts import generate_usage_timeseries_matplotlib
from webui.utils.project_permissions import can_manage_project_members, can_change_admin

bp = Blueprint('user_dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@login_required
def index():
    """
    Main user dashboard.

    Shows user's projects and their allocation spending.
    Data is loaded server-side using direct ORM queries for improved performance.
    """
    # Fetch all dashboard data using optimized query helper
    dashboard_data = get_user_dashboard_data(db.session, current_user.user_id)

    return render_template(
        'user/dashboard.html',
        user=current_user,
        dashboard_data=dashboard_data
    )


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
        'user/resource_details.html',
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
        'user/fragments/members_table.html',
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
        'user/fragments/jobs_table.html',
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


# ============================================================================
# Project Member Management Routes
# ============================================================================

@bp.route('/projects/<projcode>/members/add', methods=['POST'])
@login_required
def add_member(projcode):
    """
    Add a member to a project.

    Form parameters:
        username: Username to add
        start_date: Membership start date (YYYY-MM-DD)
        end_date: Membership end date (YYYY-MM-DD)

    Returns:
        Updated members HTML fragment on success, error message on failure
    """
    project = Project.get_by_projcode(db.session, projcode)

    if not project:
        return 'Project not found', 404

    if not can_manage_project_members(current_user, project):
        return 'Unauthorized', 403

    username = request.form.get('username')
    if not username:
        return 'Username is required', 400

    user = User.get_by_username(db.session, username)
    if not user:
        return f'User "{username}" not found', 404

    # Parse dates - start_date defaults to now, end_date is optional (can be NULL)
    start_date = None
    end_date = None

    try:
        start_date_str = request.form.get('start_date', '').strip()
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        # else: start_date stays None, will default to now in add_user_to_project

        end_date_str = request.form.get('end_date', '').strip()
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        # else: end_date stays None (no end date)
    except ValueError:
        return 'Invalid date format. Use YYYY-MM-DD.', 400

    # Add user to project
    try:
        add_user_to_project(db.session, project.project_id, user.user_id, start_date, end_date)
    except ValueError as e:
        return str(e), 400

    # Return updated members fragment
    members = get_users_on_project(db.session, projcode)
    return render_template(
        'user/fragments/members_table.html',
        members=sorted(members, key=lambda m: m["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/projects/<projcode>/members/<username>/remove', methods=['POST'])
@login_required
def remove_member(projcode, username):
    """
    Remove a member from a project.

    Returns:
        Updated members HTML fragment on success, error message on failure
    """
    project = Project.get_by_projcode(db.session, projcode)

    if not project:
        return 'Project not found', 404

    if not can_manage_project_members(current_user, project):
        return 'Unauthorized', 403

    user = User.get_by_username(db.session, username)
    if not user:
        return f'User "{username}" not found', 404

    # Remove user from project
    try:
        remove_user_from_project(db.session, project.project_id, user.user_id)
    except ValueError as e:
        return str(e), 400

    # Return updated members fragment
    members = get_users_on_project(db.session, projcode)
    return render_template(
        'user/fragments/members_table.html',
        members=sorted(members, key=lambda m: m["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/projects/<projcode>/admin', methods=['POST'])
@login_required
def update_admin(projcode):
    """
    Change the project admin.

    Form parameters:
        admin_username: Username for new admin (empty to clear admin)

    Returns:
        Updated members HTML fragment on success, error message on failure
    """
    project = Project.get_by_projcode(db.session, projcode)

    if not project:
        return 'Project not found', 404

    if not can_change_admin(current_user, project):
        return 'Unauthorized - only project lead or system admin can change admin', 403

    admin_username = request.form.get('admin_username', '').strip()

    if admin_username:
        new_admin = User.get_by_username(db.session, admin_username)
        if not new_admin:
            return f'User "{admin_username}" not found', 404

        try:
            change_project_admin(db.session, project.project_id, new_admin.user_id)
        except ValueError as e:
            return str(e), 400
    else:
        # Clear admin
        change_project_admin(db.session, project.project_id, None)

    # Return updated members fragment
    # Need to refresh project to get updated admin
    db.session.refresh(project)
    members = get_users_on_project(db.session, projcode)
    return render_template(
        'user/fragments/members_table.html',
        members=sorted(members, key=lambda m: m["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/users/search')
@login_required
def search_users():
    """
    Search users for autocomplete functionality.

    Query parameters:
        q: Search query (minimum 2 characters)
        projcode: Optional project code to exclude existing members

    Returns:
        JSON array of matching users
    """
    query = request.args.get('q', '').strip()

    if len(query) < 2:
        return jsonify([])

    # Get existing members to exclude if projcode provided
    exclude_ids = None
    projcode = request.args.get('projcode')
    if projcode:
        project = Project.get_by_projcode(db.session, projcode)
        if project:
            exclude_ids = get_project_member_user_ids(db.session, project.project_id)

    users = search_users_by_pattern(db.session, query, limit=20, exclude_user_ids=exclude_ids)

    return jsonify([
        {
            'username': u.username,
            'display_name': u.display_name,
            'email': u.primary_email or ''
        }
        for u in users
    ])

"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify, make_response
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta

from webapp.extensions import db
from sam.queries.dashboard import get_user_dashboard_data, get_resource_detail_data, get_project_dashboard_data
from sam.queries.users import get_users_on_project
from sam.queries.charges import get_jobs_for_project, get_user_breakdown_for_project
from sam.queries.lookups import find_project_by_code
from sam.projects.projects import Project
from webapp.utils.project_permissions import can_manage_project_members, can_change_admin
from webapp.utils.rbac import require_permission, Permission
from ..charts import generate_usage_timeseries_matplotlib


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
    project = find_project_by_code(db.session, projcode)

    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    # Get the root of the project tree
    root = project.get_root() if hasattr(project, 'get_root') else project

    # Render tree structure recursively
    def render_tree_node(node, current_projcode, level=0):
        is_current = node.projcode == current_projcode
        is_active = node.active if hasattr(node, 'active') else True

        # Build style and classes
        style_parts = []
        if is_current:
            style_parts.append('background: #fff3cd; font-weight: bold; border-left-color: #ffc107;')
        if not is_active:
            style_parts.append('color: #6c757d; opacity: 0.6;')

        style = ' '.join(style_parts)
        icon = '<i class="fas fa-arrow-right text-warning mr-1"></i>' if is_current else ''
        inactive_badge = ' <span class="badge badge-secondary badge-sm">Inactive</span>' if not is_active else ''

        # Make project code clickable to open details modal
        detail_url = url_for('user_dashboard.project_details_modal', projcode=node.projcode)
        projcode_html = (
            f'<button class="btn btn-link p-0" title="View project details"'
            f' data-bs-toggle="modal" data-bs-target="#projectDetailsModal"'
            f' hx-get="{detail_url}" hx-target="#projectDetailsModalBody" hx-swap="innerHTML"'
            f' onclick="event.stopPropagation()">'
            f'<strong>{node.projcode}</strong></button>'
        )
        html = f'<li style="{style}">{icon}{projcode_html}'

        if node.title:
            html += f' - {node.title}'

        html += inactive_badge

        # Recursively render children
        children = node.children if hasattr(node, 'children') and node.children else []
        if children:
            html += '<ul class="tree-list">'
            for child in sorted(children, key=lambda c: c.projcode):
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



@bp.route('/project-details-modal/<projcode>')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def project_details_modal(projcode):
    """
    Get HTML fragment for project details modal content (reusable across dashboards).

    Returns:
        HTML fragment with project info and resources for modal body
    """
    # Validate project exists
    project = find_project_by_code(db.session, projcode)
    if not project:
        return '<p class="alert alert-danger">Project not found</p>'

    # Get full project data
    project_data = get_project_dashboard_data(db.session, projcode)

    return render_template(
        'dashboards/user/partials/project_details_modal.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    )


# ============================================================================
# htmx Routes
# ============================================================================
# Server-rendered HTML fragment routes for htmx-driven form handling.
# These replace custom JavaScript with hx-* attributes on HTML elements.
# All routes are prefixed with /htmx/ to avoid conflicts with API endpoints.
# ============================================================================

@bp.route('/htmx/add-member-form/<projcode>')
@login_required
def htmx_add_member_form(projcode):
    """
    Return the add member form as an HTML fragment.

    Called when the htmx Add Member button is clicked. Returns a fresh form
    pre-populated with today's start date, ready to be inserted into the modal.
    """
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger m-3">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger m-3">Unauthorized</div>', 403

    return render_template(
        'dashboards/user/fragments/add_member_form_htmx.html',
        projcode=projcode,
        start_date=date.today().strftime('%Y-%m-%d'),
        errors=[]
    )


@bp.route('/htmx/search-users')
@login_required
def htmx_search_users():
    """
    Search users and return results as an HTML fragment.

    Replaces the JSON /api/v1/users/search endpoint for htmx usage.
    Returns clickable list items with inline onclick handlers.
    """
    from sam.queries.users import search_users_by_pattern, get_project_member_user_ids

    query = request.args.get('q', '').strip()
    projcode = request.args.get('projcode', '')

    if len(query) < 2:
        return ''

    # Exclude users already on the project
    exclude_ids = None
    if projcode:
        project = db.session.query(Project).filter_by(projcode=projcode).first()
        if project:
            exclude_ids = get_project_member_user_ids(db.session, project.project_id)

    users = search_users_by_pattern(
        db.session, query, limit=20, exclude_user_ids=exclude_ids
    )

    return render_template(
        'dashboards/user/fragments/user_search_results_htmx.html',
        users=users
    )


@bp.route('/htmx/add-member/<projcode>', methods=['POST'])
@login_required
def htmx_add_member(projcode):
    """
    Handle add member form submission (htmx).

    On validation error: returns the form with error messages (htmx swaps
    it back into the modal, user sees inline errors).

    On success: returns a success message + OOB swap to update the members
    table, then auto-closes the modal.
    """
    from sam.manage import add_user_to_project, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger m-3">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger m-3">Unauthorized</div>', 403

    # Parse form fields
    username = request.form.get('username', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()

    errors = []

    if not username:
        errors.append('Please select a user first')

    # Parse dates
    start_date = None
    end_date = None
    try:
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    except ValueError:
        errors.append('Invalid date format. Use YYYY-MM-DD.')

    if start_date and end_date and end_date <= start_date:
        errors.append('End date must be after start date')

    if errors:
        return render_template(
            'dashboards/user/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=start_date_str,
            end_date=end_date_str,
            errors=errors
        )

    # Look up the user
    user = db.session.query(User).filter_by(username=username).first()
    if not user:
        return render_template(
            'dashboards/user/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=start_date_str,
            end_date=end_date_str,
            errors=[f'User "{username}" not found']
        )

    # Add the member
    try:
        with management_transaction(db.session):
            add_user_to_project(
                db.session, project.project_id, user.user_id,
                start_date, end_date
            )
    except (ValueError, Exception) as e:
        return render_template(
            'dashboards/user/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=start_date_str,
            end_date=end_date_str,
            errors=[str(e)]
        )

    # Success — render updated members table for OOB swap
    members_html = _render_members_table(projcode, project)

    return render_template(
        'dashboards/user/fragments/add_member_success_htmx.html',
        message=f'Added {user.display_name} to project {projcode}',
        projcode=projcode,
        members_html=members_html
    )


@bp.route('/htmx/edit-allocation-form/<int:allocation_id>')
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation_form(allocation_id):
    """
    Return the edit allocation form as an HTML fragment, pre-populated from DB.

    Replaces the JS pattern of: fetch JSON → populate form fields client-side.
    """
    from sam.accounting.allocations import Allocation

    allocation = db.session.get(Allocation, allocation_id)
    if not allocation:
        return '<div class="alert alert-danger m-3">Allocation not found</div>', 404

    # Derive resource name and projcode from the allocation's account
    account = allocation.account
    resource_name = account.resource.resource_name if account and account.resource else 'Unknown'
    projcode = request.args.get('projcode', account.project.projcode if account and account.project else '')

    return render_template(
        'dashboards/user/fragments/edit_allocation_form_htmx.html',
        allocation=allocation,
        resource_name=resource_name,
        projcode=projcode,
        errors=[]
    )


@bp.route('/htmx/edit-allocation/<int:allocation_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation(allocation_id):
    """
    Handle edit allocation form submission (htmx).

    On error: returns the form with error messages.
    On success: returns a script that closes the modal and triggers a
    refresh event so any open project details modal reloads.
    """
    from sam.accounting.allocations import Allocation
    from sam.manage import update_allocation, management_transaction

    allocation = db.session.get(Allocation, allocation_id)
    if not allocation:
        return '<div class="alert alert-danger m-3">Allocation not found</div>', 404

    account = allocation.account
    resource_name = account.resource.resource_name if account and account.resource else 'Unknown'
    projcode = request.form.get('projcode', '')

    # Parse form fields
    errors = []
    updates = {}

    amount_str = request.form.get('amount', '').strip()
    if amount_str:
        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than 0')
            else:
                updates['amount'] = amount
        except ValueError:
            errors.append('Invalid amount format')
    else:
        errors.append('Amount is required')

    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    try:
        if start_date_str:
            updates['start_date'] = datetime.strptime(start_date_str, '%Y-%m-%d')
        if end_date_str:
            updates['end_date'] = datetime.strptime(end_date_str, '%Y-%m-%d')
        else:
            updates['end_date'] = None  # Explicitly clear end date
    except ValueError:
        errors.append('Invalid date format. Use YYYY-MM-DD.')

    if 'start_date' in updates and 'end_date' in updates and updates['end_date']:
        if updates['end_date'] <= updates['start_date']:
            errors.append('End date must be after start date')

    description = request.form.get('description', '')
    updates['description'] = description

    if errors:
        return render_template(
            'dashboards/user/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            resource_name=resource_name,
            projcode=projcode,
            errors=errors
        )

    try:
        with management_transaction(db.session):
            update_allocation(
                db.session, allocation_id, current_user.user_id,
                **updates
            )
    except (ValueError, Exception) as e:
        return render_template(
            'dashboards/user/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            resource_name=resource_name,
            projcode=projcode,
            errors=[str(e)]
        )

    # Success — close modal and trigger refresh
    response = make_response('''
        <div class="modal-body text-center text-success py-4">
            <i class="fas fa-check-circle fa-2x"></i>
            <p class="mt-2 mb-0">Allocation updated successfully</p>
        </div>
        <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        </div>
        <script>
        setTimeout(function() {
            var modal = bootstrap.Modal.getInstance(document.getElementById('editAllocationModal'));
            if (modal) modal.hide();
        }, 1000);
        </script>
    ''')
    response.headers['HX-Trigger'] = 'allocationUpdated'
    return response


def _render_members_table(projcode, project):
    """Render the members table fragment for a project (shared by htmx routes)."""
    members = get_users_on_project(db.session, projcode)
    return render_template(
        'dashboards/user/fragments/members_table.html',
        members=sorted(members, key=lambda m: m["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/htmx/remove-member/<projcode>/<username>', methods=['DELETE'])
@login_required
def htmx_remove_member(projcode, username):
    """
    Remove a member from a project (htmx).

    Returns the updated members table HTML on success, or an error alert.
    """
    from sam.manage import remove_user_from_project, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger">Unauthorized</div>', 403

    user = db.session.query(User).filter_by(username=username).first()
    if not user:
        return f'<div class="alert alert-danger">User "{username}" not found</div>', 404

    try:
        with management_transaction(db.session):
            remove_user_from_project(db.session, project.project_id, user.user_id)
    except (ValueError, Exception) as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return _render_members_table(projcode, project)


@bp.route('/htmx/change-admin/<projcode>', methods=['PUT'])
@login_required
def htmx_change_admin(projcode):
    """
    Change or remove the project admin (htmx).

    Form field: admin_username (empty string to remove admin role).
    Returns the updated members table HTML on success.
    """
    from sam.manage import change_project_admin, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found</div>', 404

    if not can_change_admin(current_user, project):
        return '<div class="alert alert-danger">Unauthorized — only project lead can change admin</div>', 403

    admin_username = request.form.get('admin_username', '').strip()

    try:
        with management_transaction(db.session):
            if admin_username:
                new_admin = db.session.query(User).filter_by(username=admin_username).first()
                if not new_admin:
                    return f'<div class="alert alert-danger">User "{admin_username}" not found</div>', 404
                change_project_admin(db.session, project.project_id, new_admin.user_id)
            else:
                change_project_admin(db.session, project.project_id, None)
    except (ValueError, Exception) as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return _render_members_table(projcode, project)

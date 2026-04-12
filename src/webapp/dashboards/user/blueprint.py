"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify, make_response
from webapp.utils.htmx import htmx_success
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from webapp.api.helpers import parse_input_end_date

from webapp.extensions import db
from sam.queries.dashboard import get_user_dashboard_data, get_resource_detail_data, get_project_dashboard_data
from sam.queries.rolling_usage import get_project_rolling_usage
from sam.queries.users import get_users_on_project
from sam.queries.charges import get_user_queue_breakdown_for_project, get_daily_breakdown_for_project, get_charges_by_projcode
from sam.queries.lookups import find_project_by_code
from sam.projects.projects import Project
from webapp.utils.project_permissions import (
    can_manage_project_members, can_change_admin, can_edit_consumption_threshold
)
from webapp.utils.rbac import require_permission, Permission, has_permission
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

    # Fetch 30d/90d rolling window usage (HPC/DAV only; None for DISK/ARCHIVE)
    rolling_usage = get_project_rolling_usage(db.session, projcode, resource_name=resource_name)
    rolling_windows = rolling_usage.get(resource_name, {}).get('windows', {})
    rolling_30 = rolling_windows.get(30)
    rolling_90 = rolling_windows.get(90)

    # Load root project
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        flash(f'Project {projcode} not found', 'error')
        return redirect(url_for('user_dashboard.index'))

    can_edit_threshold = can_edit_consumption_threshold(current_user, project)
    has_children = bool(project.has_children)

    # Scope: which tree node's subtree the analysis cards aggregate.
    # Defaults to the root projcode (show everything); clicking tree nodes sets scope=<child>.
    scope = request.args.get('scope', projcode)

    # Validate scope belongs to this project's tree; fall back to root if not
    if scope != projcode:
        scope_project = Project.get_by_projcode(db.session, scope)
        if not scope_project or scope_project.tree_root != project.tree_root:
            scope = projcode
            scope_project = project
    else:
        scope_project = project

    scope_has_children = bool(scope_project.has_children)

    # All projcodes covered by the selected scope (for user/daily breakdown queries)
    if scope_has_children:
        all_projcodes = [p.projcode for p in scope_project.get_descendants(include_self=True)]
    else:
        all_projcodes = [scope]

    # Fetch resource detail data; scope controls which subtree the daily trend uses
    detail_data = get_resource_detail_data(
        db.session,
        projcode,
        resource_name,
        start_date,
        end_date,
        scope_projcode=scope,
    )

    if not detail_data:
        flash(f'Project {projcode} or resource {resource_name} not found', 'error')
        return redirect(url_for('user_dashboard.index'))

    # Fetch enriched breakdown data for the current scope
    user_breakdown = get_user_queue_breakdown_for_project(
        db.session, all_projcodes, resource_name, start_date, end_date
    )
    daily_breakdown = get_daily_breakdown_for_project(
        db.session, all_projcodes, resource_name, start_date, end_date
    )

    # Build annotated project tree (only needed when project has children)
    tree_data = None
    if has_children:
        # Get the tree root (may differ from projcode if project itself is a sub-tree node)
        tree_root = project.get_root() or project

        # Query direct charges for every node in the full tree (one query)
        all_tree_projcodes = [p.projcode for p in tree_root.get_descendants(include_self=True)]
        direct_charges = get_charges_by_projcode(
            db.session, all_tree_projcodes, resource_name, start_date, end_date
        )

        # Build nested dict — only active children; roll up subtree charge totals
        def _build_node(node):
            active_children = sorted(
                [c for c in node.children if c.active],
                key=lambda c: c.projcode
            )
            child_nodes = [_build_node(c) for c in active_children]
            subtotal = direct_charges.get(node.projcode, 0.0) + sum(
                c['subtree_charges'] for c in child_nodes
            )
            return {
                'projcode': node.projcode,
                'title': node.title,
                'direct_charges': direct_charges.get(node.projcode, 0.0),
                'subtree_charges': subtotal,
                'children': child_nodes,
            }

        tree_data = _build_node(tree_root)

    # Generate charts server-side
    usage_chart = generate_usage_timeseries_matplotlib(detail_data['daily_charges'])

    # Extract allocation start date for the "Epoch" date picker preset
    alloc_start = detail_data['resource_summary'].get('start_date')
    alloc_start_date = alloc_start.strftime('%Y-%m-%d') if alloc_start else None

    return render_template(
        'dashboards/user/resource_details.html',
        user=current_user,
        projcode=projcode,
        resource_name=resource_name,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        detail_data=detail_data,
        user_breakdown=user_breakdown,
        daily_breakdown=daily_breakdown,
        date_span_days=(end_date - start_date).days,
        usage_chart=usage_chart,
        rolling_30=rolling_30,
        rolling_90=rolling_90,
        can_edit_threshold=can_edit_threshold,
        has_children=has_children,
        scope=scope,
        tree_data=tree_data,
        alloc_start_date=alloc_start_date,
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
    active_only = request.args.get('active_only') == '1'
    root = project.get_root() if hasattr(project, 'get_root') else project
    can_view_projects = has_permission(current_user, Permission.VIEW_PROJECTS)

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

        # Make project code clickable if user has VIEW_PROJECTS permission
        if can_view_projects:
            detail_url = url_for('user_dashboard.project_details_modal', projcode=node.projcode)
            projcode_html = (
                f'<button class="btn btn-link p-0" title="View project details"'
                f" onclick=\"event.stopPropagation();"
                f" htmx.ajax('GET', '{detail_url}', {{target: '#projectDetailsModalBody', swap: 'innerHTML'}});"
                f" bootstrap.Modal.getOrCreateInstance(document.getElementById('projectDetailsModal')).show();\">"
                f'<strong>{node.projcode}</strong></button>'
            )
        else:
            projcode_html = f'<strong>{node.projcode}</strong>'
        html = f'<li style="{style}">{icon}{projcode_html}'

        if node.title:
            html += f' - {node.title}'

        html += inactive_badge

        # Recursively render children
        children = node.children if hasattr(node, 'children') and node.children else []
        if active_only:
            children = [c for c in children if getattr(c, 'active', True)]
        if children:
            html += '<ul class="tree-list">'
            for child in sorted(children, key=lambda c: c.projcode):
                html += render_tree_node(child, current_projcode, level + 1)
            html += '</ul>'

        html += '</li>'
        return html

    tree_html = f'<ul class="tree-list">{render_tree_node(root, projcode)}</ul>'

    return tree_html



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

    import json
    resp = make_response(render_template(
        'dashboards/user/partials/project_details_modal.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    ))
    resp.headers['HX-Trigger'] = json.dumps({'setModalTitle': f'Project Details \u2014 {projcode}'})
    return resp


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
            end_date = parse_input_end_date(end_date_str)
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

    return htmx_success(
        'dashboards/user/fragments/add_member_success_htmx.html',
        {'closeModal': 'addMemberModal'},
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

    # Shared (inheriting) allocations are read-only — block direct edits
    if allocation.is_inheriting:
        return render_template(
            'dashboards/user/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            resource_name=resource_name,
            projcode=projcode,
            errors=["This is a shared (inherited) allocation. "
                    "To modify it, edit the master parent allocation."],
            read_only=True,
        )

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
            updates['end_date'] = parse_input_end_date(end_date_str)
        else:
            updates['end_date'] = None  # Explicitly clear end date
    except ValueError:
        errors.append('Invalid date format. Use YYYY-MM-DD.')

    if 'start_date' in updates and 'end_date' in updates and updates['end_date']:
        if updates['end_date'] <= updates['start_date']:
            errors.append('End date must be after start date')

    description = request.form.get('description', '').strip()
    updates['description'] = description if description else None

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


# ---------------------------------------------------------------------------
# Rolling consumption rate threshold editing (htmx)
# ---------------------------------------------------------------------------

def _get_project_and_account(projcode, resource_name):
    """Return (project, account) for a given project code and resource name.

    Returns (None, None) if the project is not found.
    Returns (project, None) if no matching account exists.
    """
    from sam import Account
    from sam.resources.resources import Resource

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return None, None

    account = (
        db.session.query(Account)
        .join(Account.resource)
        .filter(Account.project_id == project.project_id)
        .filter(Resource.resource_name == resource_name)
        .filter(Account.deleted == False)
        .first()
    )
    return project, account


@bp.route('/htmx/rolling-section/<projcode>/<resource_name>')
@login_required
def htmx_rolling_section(projcode, resource_name):
    """
    Return the re-rendered Rolling Consumption Rate section fragment.

    Used by the threshold form's cancel button and after a successful save
    to restore / refresh the rolling section without a full page reload.
    """
    project, _ = _get_project_and_account(projcode, resource_name)
    if not project:
        return '<div class="alert alert-danger">Project not found</div>', 404

    rolling_usage = get_project_rolling_usage(db.session, projcode, resource_name=resource_name)
    windows = rolling_usage.get(resource_name, {}).get('windows', {})

    return render_template(
        'dashboards/user/fragments/rolling_rate_htmx.html',
        projcode=projcode,
        resource_name=resource_name,
        rolling_30=windows.get(30),
        rolling_90=windows.get(90),
        can_edit_threshold=can_edit_consumption_threshold(current_user, project),
    )


@bp.route('/htmx/threshold-form/<projcode>/<resource_name>/<int:window>')
@login_required
def htmx_threshold_form(projcode, resource_name, window):
    """
    Return an inline threshold edit form for a specific rolling window.

    The form replaces the Add/Edit button via hx-target="this" hx-swap="outerHTML".
    window must be 30 or 90.
    """
    project, account = _get_project_and_account(projcode, resource_name)
    if not project or not can_edit_consumption_threshold(current_user, project):
        return '<span class="text-danger small">Unauthorized</span>', 403

    current = account.first_threshold if window == 30 else account.second_threshold

    return render_template(
        'dashboards/user/fragments/threshold_form_htmx.html',
        projcode=projcode,
        resource_name=resource_name,
        window=window,
        current_threshold=current,
        error=None,
    )


@bp.route('/htmx/threshold/<projcode>/<resource_name>/<int:window>', methods=['POST'])
@login_required
def htmx_save_threshold(projcode, resource_name, window):
    """
    Save a rolling consumption rate threshold for one window (30 or 90 days).

    Accepts form field: threshold_pct (integer > 100, or empty to clear).
    Returns the re-rendered rolling section on success, or the form with an
    error message on validation failure.
    """
    from sam.manage import management_transaction

    project, account = _get_project_and_account(projcode, resource_name)
    if not project or not can_edit_consumption_threshold(current_user, project):
        return '<div class="alert alert-danger">Unauthorized</div>', 403
    if not account:
        return '<div class="alert alert-danger">Account not found for this resource</div>', 404

    raw = request.form.get('threshold_pct', '').strip()
    if raw == '':
        new_val = None
    else:
        try:
            new_val = int(raw)
            if new_val <= 100:
                raise ValueError
        except ValueError:
            return render_template(
                'dashboards/user/fragments/threshold_form_htmx.html',
                projcode=projcode,
                resource_name=resource_name,
                window=window,
                current_threshold=raw,
                error='Must be an integer greater than 100, or leave blank to remove the limit.',
            )

    with management_transaction(db.session):
        if window == 30:
            account.update_thresholds(first_threshold=new_val)
        else:
            account.update_thresholds(second_threshold=new_val)

    rolling_usage = get_project_rolling_usage(db.session, projcode, resource_name=resource_name)
    windows = rolling_usage.get(resource_name, {}).get('windows', {})

    return render_template(
        'dashboards/user/fragments/rolling_rate_htmx.html',
        projcode=projcode,
        resource_name=resource_name,
        rolling_30=windows.get(30),
        rolling_90=windows.get(90),
        can_edit_threshold=True,
    )

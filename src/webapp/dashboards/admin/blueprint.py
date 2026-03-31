"""
Admin dashboard blueprint.

Provides administrative functionality including user impersonation,
project search, and allocation expirations tracking.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, Response
from flask_login import login_required, current_user, login_user
from datetime import datetime, timedelta
from typing import List, Tuple, Dict
import csv
import io

from webapp.extensions import db, cache
from sam.queries.dashboard import get_project_dashboard_data
from sam.queries.expirations import get_projects_by_allocation_end_date, get_projects_with_expired_allocations
from sam.queries.lookups import find_project_by_code
from webapp.auth.models import AuthUser
from sam.core.users import User
from webapp.utils.rbac import require_permission, Permission


bp = Blueprint('admin_dashboard', __name__, url_prefix='/admin')


# Usage threshold configuration (percentage)
USAGE_WARNING_THRESHOLD = 75  # Yellow warning
USAGE_CRITICAL_THRESHOLD = 90  # Red critical

# Time range presets for upcoming expirations
UPCOMING_PRESETS = {
    '7days': 7,
    '31days': 31,
    '60days': 60
}


@bp.route('/')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def index():
    """
    Admin dashboard main page.

    Shows admin tools:
    - User impersonation
    - Project search
    - Allocation expirations tracking
    """
    return render_template(
        'dashboards/admin/dashboard.html',
        user=current_user
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
        return redirect(url_for('admin_dashboard.index'))

    user_to_impersonate = AuthUser(sam_user_to_impersonate)

    # Prevent impersonating other admins unless you are a super-admin
    if user_to_impersonate.has_role('admin') and not current_user.has_role('admin'):
         flash('You do not have permission to impersonate an administrator.', 'danger')
         return redirect(url_for('admin_dashboard.index'))

    # Store current user in session to be able to go back
    session['impersonator_id'] = impersonator_id

    # Log in as the other user
    login_user(user_to_impersonate)

    flash(f'You are now impersonating {user_to_impersonate.display_name}', 'success')
    return redirect(url_for('user_dashboard.index'))


@bp.route('/stop-impersonating')
@login_required
def stop_impersonating():
    """
    Stops impersonating and returns to the original user.
    """
    impersonator_id = session.get('impersonator_id')

    if not impersonator_id:
        flash('You are not currently impersonating anyone', 'warning')
        return redirect(url_for('admin_dashboard.index'))

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
    return redirect(url_for('admin_dashboard.index'))


@bp.route('/project/<projcode>')
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
        'dashboards/admin/fragments/project_card_wrapper.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    )


@bp.route('/user/<username>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def user_card(username):
    """
    Get HTML fragment for a single user card (for admin user search).

    Returns:
        HTML user card fragment
    """
    sam_user = db.session.query(User).filter_by(username=username).first()

    if not sam_user:
        return '<div class="alert alert-warning">User not found</div>'

    return render_template(
        'dashboards/admin/fragments/user_card_wrapper.html',
        sam_user=sam_user
    )


# ============================================================================
# Expirations Panel
# ============================================================================

def _build_expiration_project_data(expiring_results: List[Tuple]) -> List[Dict]:
    """
    Transform expiration query results into project_data format for rendering.

    Args:
        expiring_results: List of (Project, Allocation, resource_name, days) tuples

    Returns:
        List of project_data dicts (expiration info calculated from resources)
    """
    # Get unique projects from results
    seen_projcodes = set()
    projects_data = []

    for project, allocation, resource_name, days in expiring_results:
        if project.projcode not in seen_projcodes:
            seen_projcodes.add(project.projcode)
            # Call get_project_dashboard_data once per project
            # The resources will have days_until_expiration calculated
            project_data = get_project_dashboard_data(db.session, project.projcode)
            if project_data:
                projects_data.append(project_data)

    return projects_data


def _get_abandoned_users_data(expired_results: List[Tuple]) -> List[Dict]:
    """
    Find users who only have expired projects.

    Args:
        expired_results: List of (Project, Allocation, resource_name, days) tuples

    Returns:
        List of dicts with username, display_name, email, projects
    """
    all_users = set()
    expired_projcodes = set()

    # Collect all users from expired projects
    for proj, alloc, res_name, days in expired_results:
        all_users.update(proj.roster)
        expired_projcodes.add(proj.projcode)

    # Find users whose active projects are all in the expired set
    abandoned_users = []
    for user in all_users:
        user_active_projcodes = set(p.projcode for p in user.active_projects())

        # If user has active projects and they're ALL in the expired set, user is abandoned
        if user_active_projcodes and user_active_projcodes.issubset(expired_projcodes):
            # Format user data
            project_codes = [p.projcode for p in user.active_projects()]
            abandoned_users.append({
                'username': user.username,
                'display_name': user.display_name,
                'email': user.primary_email or 'N/A',
                'project_count': len(project_codes),
                'projects': ', '.join(sorted(project_codes))
            })

    return sorted(abandoned_users, key=lambda u: u['username'])


@bp.route('/expirations')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def expirations_fragment():
    """
    AJAX endpoint for loading expirations data.

    Query parameters:
        view: 'upcoming' | 'expired' | 'abandoned'
        facilities: List of facility names (multi-select)
        resource: Optional resource name
        time_range: '7days' | '31days' | '60days' (upcoming only)

    Returns:
        HTML fragment with project cards or user table
    """
    view_type = request.args.get('view', 'upcoming')
    facilities = request.args.getlist('facilities')
    if not facilities:
        facilities = ['UNIV', 'WNA']
    resource = request.args.get('resource', None)
    if resource == '':
        resource = None
    time_range = request.args.get('time_range', '31days')

    if view_type == 'upcoming':
        days = UPCOMING_PRESETS.get(time_range, 31)
        results = get_projects_by_allocation_end_date(
            db.session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=days),
            facility_names=facilities,
            resource_name=resource
        )

    # get expired project details - necessary for both
    #
    elif view_type == 'expired' or view_type == 'abandoned':
        results = get_projects_with_expired_allocations(
            db.session,
            min_days_expired=90,
            max_days_expired=365,
            facility_names=facilities,
            resource_name=resource
        )

        if view_type == 'abandoned':
            abandoned_users = _get_abandoned_users_data(results)

            html = render_template(
                'dashboards/admin/fragments/abandoned_users_table.html',
                abandoned_users=abandoned_users
            )
            badge = f'<span id="abandoned-count" hx-swap-oob="true" class="badge bg-primary">{len(abandoned_users)}</span>'
            return html + badge

    else:
        return '<div class="alert alert-danger">Invalid view type</div>'

    # Transform to project_data format
    projects_data = _build_expiration_project_data(results)

    html = render_template(
        'dashboards/admin/fragments/expirations_cards.html',
        projects_data=projects_data,
        view_type=view_type,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    )
    badge = f'<span id="{view_type}-count" hx-swap-oob="true" class="badge bg-primary">{len(projects_data)}</span>'
    return html + badge


@bp.route('/expirations/export')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def expirations_export():
    """
    Export expirations data to CSV.

    Query parameters:
        export_type: 'upcoming' | 'expired' | 'abandoned'
        facilities: List of facility names (multi-select)
        resource: Optional resource name
        time_range: '7days' | '31days' | '60days' (upcoming only)

    Returns:
        CSV file download
    """
    export_type = request.args.get('export_type', 'upcoming')
    facilities = request.args.getlist('facilities')
    if not facilities:
        facilities = ['UNIV', 'WNA']
    resource = request.args.get('resource', None)
    if resource == '':
        resource = None
    time_range = request.args.get('time_range', '31days')

    # Create CSV in memory
    output = io.StringIO()

    if export_type == 'abandoned':
        # Export abandoned users
        expired_results = get_projects_with_expired_allocations(
            db.session,
            min_days_expired=90,
            max_days_expired=365,
            facility_names=facilities,
            resource_name=resource
        )
        abandoned_users = _get_abandoned_users_data(expired_results)

        writer = csv.writer(output)
        writer.writerow(['Username', 'Display Name', 'Email', 'Expired Projects'])

        for user_info in abandoned_users:
            writer.writerow([
                user_info['username'],
                user_info['display_name'],
                user_info['email'],
                user_info['projects']
            ])

        filename = f'abandoned_users_{datetime.now().strftime("%Y%m%d")}.csv'

    else:
        # Export projects (upcoming or expired)
        if export_type == 'upcoming':
            days = UPCOMING_PRESETS.get(time_range, 31)
            results = get_projects_by_allocation_end_date(
                db.session,
                start_date=datetime.now(),
                end_date=datetime.now() + timedelta(days=days),
                facility_names=facilities,
                resource_name=resource
            )
            days_label = 'Days Remaining'
        else:
            assert(False)
            # Expired exports
            results = get_projects_with_expired_allocations(
                db.session,
                min_days_expired=90,
                max_days_expired=365,
                facility_names=facilities,
                resource_name=resource
            )
            days_label = 'Days Since Expiration'

        writer = csv.writer(output)
        writer.writerow([
            'Project Code', 'Title', 'Lead Name', 'Lead Username', 'Resource', 'End Date', days_label
        ])

        for proj, alloc, res_name, days_val in results:
            writer.writerow([
                proj.projcode,
                proj.title,
                proj.lead.display_name if proj.lead else 'N/A',
                proj.lead.username if proj.lead else 'N/A',
                res_name,
                alloc.end_date.strftime('%Y-%m-%d') if alloc.end_date else 'N/A',
                days_val
            ])

        filename = f'{export_type}_projects_{datetime.now().strftime("%Y%m%d")}.csv'

    # Create response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ============================================================================
# htmx Routes
# ============================================================================

@bp.route('/htmx/search-users-impersonate')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_search_users_impersonate():
    """
    Search active users for impersonation, returning HTML fragments.
    """
    from sam.queries.users import search_users_by_pattern

    query = request.args.get('q', '').strip()
    active_only = request.args.get('active_only', '') == 'true'

    if len(query) < 2:
        return ''

    users = search_users_by_pattern(db.session, query, limit=20, active_only=active_only)

    return render_template(
        'dashboards/admin/fragments/user_search_results_htmx.html',
        users=users
    )


@bp.route('/htmx/search-projects')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_search_projects():
    """
    Search projects and return results as HTML fragments.

    Each result has hx-get to load the project card directly into
    #projectCardContainer when clicked.
    """
    from sam.queries.projects import search_projects_by_code_or_title

    query = request.args.get('q', '').strip()
    active_only = request.args.get('active_only', '') == 'true'

    if len(query) < 1:
        return ''

    projects = search_projects_by_code_or_title(
        db.session, query, active=True if active_only else None
    )[:10]  # Limit results

    return render_template(
        'dashboards/admin/fragments/project_search_results_htmx.html',
        projects=projects
    )


# ============================================================================
# Wallclock Exemption HTMX Routes
# ============================================================================

@bp.route('/htmx/exemption-form/<username>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_exemption_form(username):
    """
    Return the add-exemption form fragment for a user (loaded into modal).
    """
    from sam.resources.resources import Resource

    sam_user = db.session.query(User).filter_by(username=username).first()
    if not sam_user:
        return '<div class="alert alert-warning">User not found</div>'

    # Active resources that have queues
    resources = (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )
    resources = [r for r in resources if r.queues]

    return render_template(
        'dashboards/admin/fragments/add_exemption_form_htmx.html',
        sam_user=sam_user,
        resources=resources,
        today=datetime.now().strftime('%Y-%m-%d')
    )


@bp.route('/htmx/exemption/<username>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_exemption(username):
    """
    Create a wallclock exemption for the given user.
    On success returns a script that closes the modal and refreshes the user card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.resources import Resource
    from sam.operational import WallclockExemption
    from sam.manage import management_transaction

    sam_user = db.session.query(User).filter_by(username=username).first()
    if not sam_user:
        return '<div class="alert alert-danger">User not found</div>', 404

    errors = []

    queue_id = request.form.get('queue_id', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    limit_str = request.form.get('time_limit_hours', '').strip()
    comment = request.form.get('comment', '').strip()

    # Validate
    if not queue_id:
        errors.append('Queue is required.')
    if not start_date_str:
        errors.append('Start date is required.')
    if not end_date_str:
        errors.append('End date is required.')
    if not limit_str:
        errors.append('Time limit (hours) is required.')
    else:
        try:
            time_limit_hours = float(limit_str)
            if time_limit_hours <= 0:
                errors.append('Time limit must be a positive number.')
        except ValueError:
            errors.append('Time limit must be a number.')
            time_limit_hours = None

    start_date = end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid end date format.')

    if start_date and end_date and end_date <= start_date:
        errors.append('End date must be after start date.')

    if errors:
        resources = (
            db.session.query(Resource)
            .filter(Resource.is_active)
            .order_by(Resource.resource_name)
            .all()
        )
        resources = [r for r in resources if r.queues]
        return render_template(
            'dashboards/admin/fragments/add_exemption_form_htmx.html',
            sam_user=sam_user,
            resources=resources,
            today=datetime.now().strftime('%Y-%m-%d'),
            errors=errors,
            form=request.form
        )

    try:
        with management_transaction(db.session):
            WallclockExemption.create(
                db.session,
                user_id=sam_user.user_id,
                queue_id=int(queue_id),
                start_date=start_date,
                end_date=end_date,
                time_limit_hours=time_limit_hours,
                comment=comment or None
            )
    except Exception as e:
        resources = (
            db.session.query(Resource)
            .filter(Resource.is_active)
            .order_by(Resource.resource_name)
            .all()
        )
        resources = [r for r in resources if r.queues]
        return render_template(
            'dashboards/admin/fragments/add_exemption_form_htmx.html',
            sam_user=sam_user,
            resources=resources,
            today=datetime.now().strftime('%Y-%m-%d'),
            errors=[f'Error creating exemption: {e}'],
            form=request.form
        )

    # Success: close modal + refresh user card
    return render_template(
        'dashboards/admin/fragments/exemption_success_htmx.html',
        username=username
    )


@bp.route('/htmx/exemption-edit-form/<int:exemption_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_exemption_form(exemption_id):
    """
    Return the edit-exemption form fragment (loaded into modal).
    """
    from sam.operational import WallclockExemption

    exemption = db.session.get(WallclockExemption, exemption_id)
    if not exemption:
        return '<div class="alert alert-warning">Exemption not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_exemption_form_htmx.html',
        exemption=exemption
    )


@bp.route('/htmx/exemption-edit/<int:exemption_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_exemption(exemption_id):
    """
    Update a wallclock exemption.
    On success returns a script that closes the modal and refreshes the user card.
    On error re-renders the form with validation messages.
    """
    from sam.operational import WallclockExemption
    from sam.manage import management_transaction

    exemption = db.session.get(WallclockExemption, exemption_id)
    if not exemption:
        return '<div class="alert alert-danger">Exemption not found</div>', 404

    errors = []

    end_date_str = request.form.get('end_date', '').strip()
    limit_str = request.form.get('time_limit_hours', '').strip()
    comment = request.form.get('comment', '').strip()

    end_date = None
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            if end_date <= exemption.start_date:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')
    else:
        errors.append('End date is required.')

    time_limit_hours = None
    if limit_str:
        try:
            time_limit_hours = float(limit_str)
            if time_limit_hours <= 0:
                errors.append('Time limit must be a positive number.')
        except ValueError:
            errors.append('Time limit must be a number.')
    else:
        errors.append('Time limit (hours) is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_exemption_form_htmx.html',
            exemption=exemption,
            errors=errors,
            form=request.form
        )

    username = exemption.user.username
    try:
        with management_transaction(db.session):
            exemption.update(
                end_date=end_date,
                time_limit_hours=time_limit_hours,
                comment=comment
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_exemption_form_htmx.html',
            exemption=exemption,
            errors=[f'Error updating exemption: {e}'],
            form=request.form
        )

    # Success: close modal + refresh user card
    return render_template(
        'dashboards/admin/fragments/exemption_success_htmx.html',
        username=username
    )


@bp.route('/htmx/queues-for-resource')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_queues_for_resource():
    """
    Return queue <option> elements for a given resource_id (cascading select).
    """
    from sam.resources.machines import Queue

    resource_id = request.args.get('resource_id', '').strip()
    if not resource_id:
        return '<option value="">-- Select a resource first --</option>'

    now = datetime.now()
    queues = (
        db.session.query(Queue)
        .filter(
            Queue.resource_id == int(resource_id),
            (Queue.end_date == None) | (Queue.end_date >= now)
        )
        .order_by(Queue.queue_name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/queues_for_resource_htmx.html',
        queues=queues
    )


# ── Resource Management Routes ─────────────────────────────────────────────


@bp.route('/htmx/resources')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resources_card():
    """
    Return the Resources card body fragment with four tabs:
    Resources, Resource Types, Machines, Queues.
    Lazy-loaded when the Resources collapsible section is first expanded.
    """
    from sam.resources.resources import Resource, ResourceType
    from sam.resources.machines import Machine, Queue

    active_only = request.args.get('active_only') == '1'
    now = datetime.now()

    resource_q = db.session.query(Resource).order_by(Resource.resource_name)
    if active_only:
        resource_q = resource_q.filter(Resource.is_active)
    resources = resource_q.all()

    resource_types = db.session.query(ResourceType).order_by(ResourceType.resource_type).all()

    machine_q = db.session.query(Machine).order_by(Machine.resource_id, Machine.name)
    if active_only:
        machine_q = machine_q.filter(Machine.is_active)
    machines = machine_q.all()

    queue_q = db.session.query(Queue).order_by(Queue.resource_id, Queue.queue_name)
    if active_only:
        queue_q = queue_q.filter(Queue.is_active)
    queues = queue_q.all()

    return render_template(
        'dashboards/admin/fragments/resources_card.html',
        resources=resources,
        resource_types=resource_types,
        machines=machines,
        queues=queues,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


@bp.route('/htmx/resource-edit-form/<int:resource_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_edit_form(resource_id):
    """Return the resource edit form fragment (loaded into modal)."""
    from sam.resources.resources import Resource

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return '<div class="alert alert-warning">Resource not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_resource_form_htmx.html',
        resource=resource,
    )


@bp.route('/htmx/resource-edit/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_edit(resource_id):
    """
    Update a resource.
    On success returns a script that closes the modal and refreshes the resources card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.resources import Resource
    from sam.manage import management_transaction

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return '<div class="alert alert-danger">Resource not found</div>', 404

    errors = []

    commission_str = request.form.get('commission_date', '').strip()
    decommission_str = request.form.get('decommission_date', '').strip()
    description = request.form.get('description', '').strip()
    charging_exempt = bool(request.form.get('charging_exempt'))

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')
    else:
        errors.append('Commission date is required.')

    decommission_date = None
    if decommission_str:
        try:
            decommission_date = datetime.strptime(decommission_str, '%Y-%m-%d')
            if commission_date and decommission_date <= commission_date:
                errors.append('Decommission date must be after commission date.')
        except ValueError:
            errors.append('Invalid decommission date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_resource_form_htmx.html',
            resource=resource,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            resource.update(
                description=description,
                commission_date=commission_date,
                decommission_date=decommission_date,
                charging_exempt=charging_exempt,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_resource_form_htmx.html',
            resource=resource,
            errors=[f'Error updating resource: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/resource_edit_success_htmx.html')


@bp.route('/htmx/resource-type-edit-form/<int:resource_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_type_edit_form(resource_type_id):
    """Return the resource type edit form fragment (loaded into modal)."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return '<div class="alert alert-warning">Resource type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
        resource_type=resource_type,
    )


@bp.route('/htmx/resource-type-edit/<int:resource_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_type_edit(resource_type_id):
    """
    Update a resource type.
    On success returns a script that closes the modal and refreshes the resources card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.resources import ResourceType
    from sam.manage import management_transaction

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return '<div class="alert alert-danger">Resource type not found</div>', 404

    errors = []

    grace_period_str = request.form.get('grace_period_days', '').strip()

    grace_period_days = None
    if grace_period_str:
        try:
            grace_period_days = int(grace_period_str)
            if grace_period_days < 0:
                errors.append('Grace period days must be >= 0.')
        except ValueError:
            errors.append('Grace period days must be a whole number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
            resource_type=resource_type,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            resource_type.update(
                grace_period_days=grace_period_days,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
            resource_type=resource_type,
            errors=[f'Error updating resource type: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/resource_edit_success_htmx.html')


@bp.route('/htmx/machine-edit-form/<int:machine_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_machine_edit_form(machine_id):
    """Return the machine edit form fragment (loaded into modal)."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return '<div class="alert alert-warning">Machine not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_machine_form_htmx.html',
        machine=machine,
    )


@bp.route('/htmx/machine-edit/<int:machine_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_machine_edit(machine_id):
    """
    Update a machine.
    On success returns a script that closes the modal and refreshes the resources card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.machines import Machine
    from sam.manage import management_transaction

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return '<div class="alert alert-danger">Machine not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    cpus_str = request.form.get('cpus_per_node', '').strip()
    commission_str = request.form.get('commission_date', '').strip()
    decommission_str = request.form.get('decommission_date', '').strip()

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')
    else:
        errors.append('Commission date is required.')

    decommission_date = None
    if decommission_str:
        try:
            decommission_date = datetime.strptime(decommission_str, '%Y-%m-%d')
            if commission_date and decommission_date <= commission_date:
                errors.append('Decommission date must be after commission date.')
        except ValueError:
            errors.append('Invalid decommission date format.')

    cpus_per_node = None
    if cpus_str:
        try:
            cpus_per_node = int(cpus_str)
            if cpus_per_node <= 0:
                errors.append('CPUs per node must be a positive integer.')
        except ValueError:
            errors.append('CPUs per node must be a whole number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_machine_form_htmx.html',
            machine=machine,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            machine.update(
                description=description,
                cpus_per_node=cpus_per_node,
                commission_date=commission_date,
                decommission_date=decommission_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_machine_form_htmx.html',
            machine=machine,
            errors=[f'Error updating machine: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/resource_edit_success_htmx.html')


@bp.route('/htmx/queue-edit-form/<int:queue_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_queue_edit_form(queue_id):
    """Return the queue edit form fragment (loaded into modal)."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return '<div class="alert alert-warning">Queue not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_queue_form_htmx.html',
        queue=queue,
    )


@bp.route('/htmx/queue-edit/<int:queue_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_queue_edit(queue_id):
    """
    Update a queue.
    On success returns a script that closes the modal and refreshes the resources card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.machines import Queue
    from sam.manage import management_transaction

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return '<div class="alert alert-danger">Queue not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    wallclock_str = request.form.get('wall_clock_hours_limit', '').strip()
    end_date_str = request.form.get('end_date', '').strip()

    wall_clock_hours_limit = None
    if wallclock_str:
        try:
            wall_clock_hours_limit = float(wallclock_str)
            if wall_clock_hours_limit <= 0:
                errors.append('Wallclock limit must be a positive number.')
        except ValueError:
            errors.append('Wallclock limit must be a number.')
    else:
        errors.append('Wallclock limit (hours) is required.')

    end_date = None
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            if queue.start_date and end_date <= queue.start_date:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            queue.update(
                description=description,
                wall_clock_hours_limit=wall_clock_hours_limit,
                end_date=end_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=[f'Error updating queue: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/resource_edit_success_htmx.html')


# ── Facility Management Routes ─────────────────────────────────────────────


@bp.route('/htmx/facilities')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facilities_card():
    """
    Return the Facility card body fragment with four tabs:
    Facilities, Panels, Panel Sessions, Allocation Types.
    Lazy-loaded when the Facility collapsible section is first expanded.
    """
    from sam.resources.facilities import Facility

    active_only = request.args.get('active_only') == '1'

    facility_q = db.session.query(Facility).order_by(Facility.facility_name)
    if active_only:
        facility_q = facility_q.filter(Facility.is_active)
    facilities = facility_q.all()

    return render_template(
        'dashboards/admin/fragments/facility_card.html',
        facilities=facilities,
        is_admin=True,
        active_only=active_only,
    )


@bp.route('/htmx/facility-edit-form/<int:facility_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facility_edit_form(facility_id):
    """Return the facility edit form fragment (loaded into modal)."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return '<div class="alert alert-warning">Facility not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_facility_form_htmx.html',
        facility=facility,
    )


@bp.route('/htmx/facility-edit/<int:facility_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facility_edit(facility_id):
    """
    Update a facility.
    On success returns a script that closes the modal and refreshes the facility card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.facilities import Facility
    from sam.manage import management_transaction

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return '<div class="alert alert-danger">Facility not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()
    active = bool(request.form.get('active'))

    if not description:
        errors.append('Description is required.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            facility.update(
                description=description,
                fair_share_percentage=fair_share_percentage,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=[f'Error updating facility: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


@bp.route('/htmx/panel-edit-form/<int:panel_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_edit_form(panel_id):
    """Return the panel edit form fragment (loaded into modal)."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return '<div class="alert alert-warning">Panel not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_panel_form_htmx.html',
        panel=panel,
    )


@bp.route('/htmx/panel-edit/<int:panel_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_edit(panel_id):
    """
    Update a panel.
    On success returns a script that closes the modal and refreshes the facility card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.facilities import Panel
    from sam.manage import management_transaction

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return '<div class="alert alert-danger">Panel not found</div>', 404

    description = request.form.get('description', '').strip()
    active = bool(request.form.get('active'))

    try:
        with management_transaction(db.session):
            panel.update(
                description=description or None,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_form_htmx.html',
            panel=panel,
            errors=[f'Error updating panel: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


@bp.route('/htmx/panel-session-edit-form/<int:panel_session_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_session_edit_form(panel_session_id):
    """Return the panel session edit form fragment (loaded into modal)."""
    from sam.resources.facilities import PanelSession

    panel_session = db.session.get(PanelSession, panel_session_id)
    if not panel_session:
        return '<div class="alert alert-warning">Panel session not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
        panel_session=panel_session,
    )


@bp.route('/htmx/panel-session-edit/<int:panel_session_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_session_edit(panel_session_id):
    """
    Update a panel session.
    On success returns a script that closes the modal and refreshes the facility card.
    On error re-renders the form with validation messages.
    """
    from sam.resources.facilities import PanelSession
    from sam.manage import management_transaction

    panel_session = db.session.get(PanelSession, panel_session_id)
    if not panel_session:
        return '<div class="alert alert-danger">Panel session not found</div>', 404

    errors = []

    start_str = request.form.get('start_date', '').strip()
    end_str = request.form.get('end_date', '').strip()
    meeting_str = request.form.get('panel_meeting_date', '').strip()
    description = request.form.get('description', '').strip()

    start_date = None
    if start_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    else:
        errors.append('Start date is required.')

    end_date = None
    if end_str:
        try:
            end_date = datetime.strptime(end_str, '%Y-%m-%d')
            effective_start = start_date or panel_session.start_date
            if effective_start and end_date <= effective_start:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    panel_meeting_date = None
    if meeting_str:
        try:
            panel_meeting_date = datetime.strptime(meeting_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid panel meeting date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            panel_session.update(
                description=description or None,
                start_date=start_date,
                end_date=end_date,
                panel_meeting_date=panel_meeting_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=[f'Error updating panel session: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


@bp.route('/htmx/allocation-type-edit-form/<int:allocation_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_allocation_type_edit_form(allocation_type_id):
    """Return the allocation type edit form fragment (loaded into modal)."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return '<div class="alert alert-warning">Allocation type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
        allocation_type=allocation_type,
    )


@bp.route('/htmx/allocation-type-edit/<int:allocation_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_allocation_type_edit(allocation_type_id):
    """
    Update an allocation type.
    On success returns a script that closes the modal and refreshes the facility card.
    On error re-renders the form with validation messages.
    """
    from sam.accounting.allocations import AllocationType
    from sam.manage import management_transaction

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return '<div class="alert alert-danger">Allocation type not found</div>', 404

    errors = []

    amount_str = request.form.get('default_allocation_amount', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()
    active = bool(request.form.get('active'))

    default_allocation_amount = None
    if amount_str:
        try:
            default_allocation_amount = float(amount_str)
            if default_allocation_amount < 0:
                errors.append('Default allocation amount must be >= 0.')
        except ValueError:
            errors.append('Default allocation amount must be a number.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            allocation_type.update(
                default_allocation_amount=default_allocation_amount,
                fair_share_percentage=fair_share_percentage,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=[f'Error updating allocation type: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Organization Management Routes ────────────────────────────────────────


@bp.route('/htmx/organizations-card')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
@cache.cached(timeout=300, query_string=True)
def htmx_organizations_card():
    """
    Return the Organization card body fragment with seven tabs:
    Organizations, Institutions, AOI Groups, Areas of Interest,
    Contract Sources, Contracts, NSF Programs.
    Lazy-loaded when the Organization collapsible section is first expanded.
    """
    from sam.core.organizations import Organization, Institution, InstitutionType, UserInstitution
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.projects.contracts import Contract, ContractSource, NSFProgram
    from sam.projects.projects import Project
    from sqlalchemy.orm import subqueryload, selectinload, lazyload

    active_only = request.args.get('active_only') == '1'
    now = datetime.now()

    org_q = db.session.query(Organization).options(
        subqueryload(Organization.children),
        selectinload(Organization.users),           # org.users|length accessed in template
    )
    if active_only:
        org_q = org_q.filter(Organization.is_active)
    organizations = org_q.all()

    # Build DFS-ordered flat tree: [(org, depth, has_children)]
    _children = {}
    for _o in organizations:
        _pid = _o.parent_org_id
        _children.setdefault(_pid, []).append(_o)
    for _pid in _children:
        _children[_pid].sort(key=lambda o: o.acronym or '')

    def _dfs(_pid, _depth):
        result = []
        for _o in _children.get(_pid, []):
            _has_ch = bool(_children.get(_o.organization_id))
            result.append((_o, _depth, _has_ch))
            result.extend(_dfs(_o.organization_id, _depth + 1))
        return result

    org_tree = _dfs(None, 0)

    # InstitutionType and Institution have no active flag — always show all.
    # The template sorts inst.users by sort(attribute='user.username'), so we must
    # chain all the way to User. Suppress User.accounts and User.email_addresses
    # selectin cascades (lazy='selectin' on both) with per-query lazyload overrides.
    # Note: sibling options on the same entity need separate chain arguments — you
    # cannot chain .lazyload(User.email_addresses) after .lazyload(User.accounts)
    # because that would try to link email_addresses from AccountUser, not from User.
    institution_types = db.session.query(InstitutionType).options(
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(InstitutionType.type).all()
    institutions = db.session.query(Institution).options(
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(Institution.name).all()

    aoi_group_q = db.session.query(AreaOfInterestGroup).options(
        selectinload(AreaOfInterestGroup.areas),    # g.areas|length accessed in template
    ).order_by(AreaOfInterestGroup.name)
    if active_only:
        aoi_group_q = aoi_group_q.filter(AreaOfInterestGroup.is_active)
    aoi_groups = aoi_group_q.all()

    aoi_q = db.session.query(AreaOfInterest).options(
        # Suppress Project.accounts lazy='selectin' cascade — we only need len(a.projects)
        selectinload(AreaOfInterest.projects).lazyload(Project.accounts),
    ).order_by(AreaOfInterest.area_of_interest)
    if active_only:
        aoi_q = aoi_q.filter(AreaOfInterest.is_active)
    aois = aoi_q.all()

    cs_q = db.session.query(ContractSource).order_by(ContractSource.contract_source)
    if active_only:
        cs_q = cs_q.filter(ContractSource.is_active)
    contract_sources = cs_q.all()

    contract_q = db.session.query(Contract).options(
        # c.principal_investigator.username in template; suppress User cascade selectins
        selectinload(Contract.principal_investigator)
            .lazyload(User.accounts),
        selectinload(Contract.principal_investigator)
            .lazyload(User.email_addresses),
    ).order_by(Contract.contract_number)
    if active_only:
        contract_q = contract_q.filter(Contract.is_active)
    contracts = contract_q.all()

    nsf_q = db.session.query(NSFProgram).options(
        selectinload(NSFProgram.contracts),         # p.contracts|length accessed in template
    ).order_by(NSFProgram.nsf_program_name)
    if active_only:
        nsf_q = nsf_q.filter(NSFProgram.is_active)
    nsf_programs = nsf_q.all()

    # Resolve MnemonicCodes via "soft link" string matching (same strategy as legacy Java).
    # Load all active codes once (336 rows) then do Python-side lookups — no N+1.
    from sam.core.organizations import MnemonicCode
    _mc_lookup = {
        mc.description: mc.code
        for mc in db.session.query(MnemonicCode).filter(MnemonicCode.is_active).all()
    }
    # Institutions: match "Name, City" first, fall back to "Name" alone
    inst_to_mnemonic = {
        inst.institution_id: (
            _mc_lookup.get(f"{inst.name}, {inst.city}" if inst.city else inst.name)
            or _mc_lookup.get(inst.name)
        )
        for inst in institutions
    }
    # Organizations: match by name only (UserOrganizationStrategy)
    org_to_mnemonic = {
        org.organization_id: _mc_lookup.get(org.name)
        for org in organizations
    }

    return render_template(
        'dashboards/admin/fragments/organization_card.html',
        organizations=organizations,
        org_tree=org_tree,
        institution_types=institution_types,
        institutions=institutions,
        aoi_groups=aoi_groups,
        aois=aois,
        contract_sources=contract_sources,
        contracts=contracts,
        nsf_programs=nsf_programs,
        inst_to_mnemonic=inst_to_mnemonic,
        org_to_mnemonic=org_to_mnemonic,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


@bp.route('/htmx/organization-edit-form/<int:org_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_organization_edit_form(org_id):
    """Return the organization edit form fragment (loaded into modal)."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return '<div class="alert alert-warning">Organization not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_organization_form_htmx.html',
        org=org,
    )


@bp.route('/htmx/organization-edit/<int:org_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_organization_edit(org_id):
    """Update an organization."""
    from sam.core.organizations import Organization
    from sam.manage import management_transaction

    org = db.session.get(Organization, org_id)
    if not org:
        return '<div class="alert alert-danger">Organization not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()
    description = request.form.get('description', '').strip()
    active = bool(request.form.get('active'))

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            org.update(
                name=name, acronym=acronym,
                description=description or None,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=[f'Error updating organization: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/institution-type-edit-form/<int:institution_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_type_edit_form(institution_type_id):
    """Return the institution type edit form fragment (loaded into modal)."""
    from sam.core.organizations import InstitutionType

    inst_type = db.session.get(InstitutionType, institution_type_id)
    if not inst_type:
        return '<div class="alert alert-warning">Institution type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
        inst_type=inst_type,
    )


@bp.route('/htmx/institution-type-edit/<int:institution_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_type_edit(institution_type_id):
    """Update an institution type."""
    from sam.core.organizations import InstitutionType
    from sam.manage import management_transaction

    inst_type = db.session.get(InstitutionType, institution_type_id)
    if not inst_type:
        return '<div class="alert alert-danger">Institution type not found</div>', 404

    errors = []
    type_name = request.form.get('type', '').strip()

    if not type_name:
        errors.append('Type name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            inst_type.update(type=type_name)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=[f'Error updating institution type: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/institution-edit-form/<int:inst_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_edit_form(inst_id):
    """Return the institution edit form fragment (loaded into modal)."""
    from sam.core.organizations import Institution

    institution = db.session.get(Institution, inst_id)
    if not institution:
        return '<div class="alert alert-warning">Institution not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_institution_form_htmx.html',
        institution=institution,
    )


@bp.route('/htmx/institution-edit/<int:inst_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_edit(inst_id):
    """Update an institution."""
    from sam.core.organizations import Institution
    from sam.manage import management_transaction

    institution = db.session.get(Institution, inst_id)
    if not institution:
        return '<div class="alert alert-danger">Institution not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            institution.update(
                name=name,
                acronym=acronym,
                nsf_org_code=request.form.get('nsf_org_code', '').strip() or None,
                address=request.form.get('address', '').strip() or None,
                city=request.form.get('city', '').strip() or None,
                zip=request.form.get('zip', '').strip() or None,
                code=request.form.get('code', '').strip() or None,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=[f'Error updating institution: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/aoi-group-edit-form/<int:group_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_group_edit_form(group_id):
    """Return the AOI group edit form fragment (loaded into modal)."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return '<div class="alert alert-warning">AOI group not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
        group=group,
    )


@bp.route('/htmx/aoi-group-edit/<int:group_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_group_edit(group_id):
    """Update an AOI group."""
    from sam.projects.areas import AreaOfInterestGroup
    from sam.manage import management_transaction

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return '<div class="alert alert-danger">AOI group not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    active = bool(request.form.get('active'))

    if not name:
        errors.append('Name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            group.update(name=name, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=[f'Error updating AOI group: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/aoi-edit-form/<int:aoi_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_edit_form(aoi_id):
    """Return the AOI edit form fragment (loaded into modal)."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-warning">Area of interest not found</div>'

    aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()

    return render_template(
        'dashboards/admin/fragments/edit_aoi_form_htmx.html',
        aoi=aoi,
        aoi_groups=aoi_groups,
    )


@bp.route('/htmx/aoi-edit/<int:aoi_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_edit(aoi_id):
    """Update an area of interest."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.manage import management_transaction

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-danger">Area of interest not found</div>', 404

    errors = []
    area_of_interest = request.form.get('area_of_interest', '').strip()
    group_id_str = request.form.get('area_of_interest_group_id', '').strip()
    active = bool(request.form.get('active'))

    if not area_of_interest:
        errors.append('Name is required.')

    group_id = None
    if group_id_str:
        try:
            group_id = int(group_id_str)
        except ValueError:
            errors.append('Invalid group selection.')
    else:
        errors.append('Group is required.')

    if errors:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            aoi.update(
                area_of_interest=area_of_interest,
                area_of_interest_group_id=group_id,
                active=active,
            )
    except Exception as e:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups,
            errors=[f'Error updating area of interest: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/contract-source-edit-form/<int:source_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_source_edit_form(source_id):
    """Return the contract source edit form fragment (loaded into modal)."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return '<div class="alert alert-warning">Contract source not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
        source=source,
    )


@bp.route('/htmx/contract-source-edit/<int:source_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_source_edit(source_id):
    """Update a contract source."""
    from sam.projects.contracts import ContractSource
    from sam.manage import management_transaction

    source = db.session.get(ContractSource, source_id)
    if not source:
        return '<div class="alert alert-danger">Contract source not found</div>', 404

    errors = []
    contract_source = request.form.get('contract_source', '').strip()
    active = bool(request.form.get('active'))

    if not contract_source:
        errors.append('Source name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            source.update(contract_source=contract_source, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=[f'Error updating contract source: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/contract-edit-form/<int:contract_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_edit_form(contract_id):
    """Return the contract edit form fragment (loaded into modal)."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return '<div class="alert alert-warning">Contract not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_contract_form_htmx.html',
        contract=contract,
    )


@bp.route('/htmx/contract-edit/<int:contract_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_edit(contract_id):
    """Update a contract."""
    from sam.projects.contracts import Contract
    from sam.manage import management_transaction

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return '<div class="alert alert-danger">Contract not found</div>', 404

    errors = []
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    start_str = request.form.get('start_date', '').strip()
    end_str = request.form.get('end_date', '').strip()

    if not title:
        errors.append('Title is required.')

    start_date = None
    if start_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    else:
        errors.append('Start date is required.')

    end_date = None
    if end_str:
        try:
            end_date = datetime.strptime(end_str, '%Y-%m-%d')
            effective_start = start_date or contract.start_date
            if effective_start and end_date <= effective_start:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            contract.update(
                title=title,
                url=url or None,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=[f'Error updating contract: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


@bp.route('/htmx/nsf-program-edit-form/<int:nsf_program_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_nsf_program_edit_form(nsf_program_id):
    """Return the NSF program edit form fragment (loaded into modal)."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return '<div class="alert alert-warning">NSF program not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
        program=program,
    )


@bp.route('/htmx/nsf-program-edit/<int:nsf_program_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_nsf_program_edit(nsf_program_id):
    """Update an NSF program."""
    from sam.projects.contracts import NSFProgram
    from sam.manage import management_transaction

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return '<div class="alert alert-danger">NSF program not found</div>', 404

    errors = []
    nsf_program_name = request.form.get('nsf_program_name', '').strip()
    active = bool(request.form.get('active'))

    if not nsf_program_name:
        errors.append('Program name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            program.update(nsf_program_name=nsf_program_name, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=[f'Error updating NSF program: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')

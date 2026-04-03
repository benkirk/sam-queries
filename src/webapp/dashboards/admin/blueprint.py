"""
Admin dashboard blueprint.

Provides administrative functionality including user impersonation,
project search, and allocation expirations tracking.

Domain-specific routes are split into sub-modules imported at the bottom:
  resources_routes.py  — Resources, Resource Types, Machines, Queues
  facilities_routes.py — Facilities, Panels, Panel Sessions, Allocation Types
  orgs_routes.py       — Organizations, Institutions, AOIs, Contracts, NSF Programs
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, Response
from flask_login import login_required, current_user, login_user
from datetime import datetime, timedelta
from webapp.api.helpers import parse_input_end_date
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
# htmx Search Routes
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
            end_date = parse_input_end_date(end_date_str)
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
            end_date = parse_input_end_date(end_date_str)
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


# ============================================================================
# Domain route modules — must be imported AFTER bp is defined
# ============================================================================

from . import resources_routes, facilities_routes, orgs_routes  # noqa: E402, F401

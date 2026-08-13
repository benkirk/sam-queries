"""
Admin dashboard blueprint.

Provides administrative functionality including user impersonation,
project search, and allocation expirations tracking.

Domain-specific routes are split into sub-modules imported at the bottom:
  resources_routes.py  — Resources, Resource Types, Machines, Queues
  facilities_routes.py — Facilities, Panels, Panel Sessions, Allocation Types
  orgs_routes.py       — Organizations, Institutions, AOIs
  contracts_routes.py  — Contracts, Contract Sources, NSF Programs
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, Response, abort
from webapp.utils.htmx import (htmx_success, htmx_success_message, htmx_not_found,
                               read_active_only, register_typeahead)
from flask_login import login_required, current_user, login_user
from datetime import datetime, timedelta
from webapp.api.helpers import parse_input_end_date
from typing import List, Tuple, Dict
import csv
import io

from webapp.extensions import db, cache
from webapp.utils.form_handler import FormError, HtmxFormHandler
from sam.schemas.forms import (
    AdminCreateWallclockExemptionForm,
    CreateWallclockExemptionForm,
    EditWallclockExemptionForm,
)
from sam.queries.dashboard import get_project_dashboard_data
from sam.queries.expirations import get_projects_by_allocation_end_date, get_projects_with_expired_allocations
from sam.queries.lookups import find_project_by_code, get_user_group_access
from sam.queries.notifications import get_expiration_notice_status
from webapp.auth.models import AuthUser
from sam.core.users import User
from webapp.utils.rbac import (
    apply_facility_scope, can_impersonate, get_user_permissions,
    has_permission, has_permission_any_facility, Permission,
    require_permission, require_permission_any_facility,
    user_facility_scope,
    allowed_facility_names as _allowed_facility_names,
)
import logging
logger = logging.getLogger(__name__)


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
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def index():
    """Bare section URL — redirect to the default page (Projects),
    preserving query params (``?projcode=`` re-hydration back-links)."""
    return redirect(url_for('admin_dashboard.projects', **request.args))


@bp.route('/projects')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def projects():
    """
    Admin Projects page.

    Shows project search/create, the project card display area, and
    allocation expirations tracking.
    """
    # Full facility list for unscoped users, the user's allowed set
    # otherwise. The template iterates this to build the expirations
    # facility multi-select so scoped users can't see (or pick)
    # facilities they cannot act on.
    # active_only=False preserves this page's pre-helper behavior — it was
    # the one copy of this block that skipped the Facility.is_active
    # filter, so inactive facilities appear in the multi-select here.
    allowed_facility_names = _allowed_facility_names(
        current_user, Permission.VIEW_PROJECTS, active_only=False)

    # The two default selections carry over from the hardcoded template
    # (UNIV and WNA). Keep them only if they survive the allowed set.
    default_selected = [f for f in ('UNIV', 'WNA') if f in allowed_facility_names]

    # Optional re-hydration: when arriving from a back-link like
    # /admin/projects?projcode=SCSG0001, auto-render the project card via
    # HTMX on page load so the user lands where they left off.
    auto_load_projcode = (request.args.get('projcode') or '').strip() or None

    return render_template(
        'dashboards/admin/projects.html',
        user=current_user,
        allowed_facility_names=allowed_facility_names,
        default_selected_facilities=default_selected,
        auto_load_projcode=auto_load_projcode,
    )


@bp.route('/projects/directories')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def projects_directories():
    """Admin Project Directories page (htmx-loaded table)."""
    return render_template('dashboards/admin/projects_directories.html', user=current_user)


@bp.route('/users-groups')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def users_groups():
    """Admin Users & Groups page — search users/groups, card display areas."""
    return render_template('dashboards/admin/users_groups.html', user=current_user)


@bp.route('/resources')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def resources():
    """Admin Resources page (htmx-loaded card)."""
    return render_template('dashboards/admin/resources.html', user=current_user)


@bp.route('/organizations')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def organizations():
    """Admin Organizations page (htmx-loaded card)."""
    return render_template('dashboards/admin/organizations.html', user=current_user)


@bp.route('/contracts')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def contracts():
    """Admin Contracts page — two searches, card display area, and the table.

    ``contract_sources`` is passed eagerly because the candidate-search
    Source filter is part of the initial render; everything else on the page
    (results, contract card, the table) arrives via htmx.
    """
    from webapp.dashboards.admin.contracts_routes import active_contract_sources
    return render_template('dashboards/admin/contracts.html',
                           user=current_user,
                           contract_sources=active_contract_sources())


@bp.route('/facilities')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
def facilities():
    """Admin Facilities & Allocations page (htmx-loaded card)."""
    return render_template('dashboards/admin/facilities.html', user=current_user)


@bp.route('/configuration')
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def configuration():
    """Admin Configuration page — read-only system snapshot."""
    return render_template('dashboards/admin/configuration.html', user=current_user)


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

    # No-escalation guard: caller may only impersonate users whose
    # permission set is a subset of their own. Peers and "lessor" users
    # (regular users, project leads with no system permissions, …) are
    # fine; users with extra permissions are not.
    if not can_impersonate(current_user, user_to_impersonate):
        extra = get_user_permissions(user_to_impersonate) - get_user_permissions(current_user)
        logger.warning(
            "Impersonation refused: caller=%s target=%s extra_perms=%s",
            current_user.username, username, sorted(p.value for p in extra),
        )
        flash(
            f'Cannot impersonate {username} — they hold permissions you do not.',
            'danger',
        )
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
@require_permission_any_facility(Permission.VIEW_PROJECTS)
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

    # Facility-scoped users must not see cards for projects outside
    # their granted facilities — even if they somehow land on the URL.
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if allowed is not None:
        project = find_project_by_code(db.session, projcode)
        if project is None or project.facility_name not in allowed:
            abort(403)

    # Render a wrapper template that calls the macro
    return render_template(
        'dashboards/admin/fragments/project_card_wrapper.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD,
    )


@bp.route('/user/<username>')
@login_required
@require_permission_any_facility(Permission.VIEW_USERS)
def user_card(username):
    """
    Get HTML fragment for a single user card (for admin user search).

    Returns:
        HTML user card fragment
    """
    # Eager-load both affiliation graphs: the card renders each of them twice
    # (current + former blocks), which would otherwise lazy-load per row.
    from sqlalchemy.orm import selectinload, joinedload
    from sam.core.organizations import UserInstitution, UserOrganization

    sam_user = db.session.query(User).options(
        selectinload(User.institutions).joinedload(UserInstitution.institution),
        selectinload(User.organizations).joinedload(UserOrganization.organization),
    ).filter_by(username=username).first()

    if not sam_user:
        return '<div class="alert alert-warning">User not found</div>'

    rows = get_user_group_access(db.session, username=username).get(username, [])
    user_groups = {}
    for r in rows:
        user_groups.setdefault(r['access_branch_name'], []).append({
            'group_name': r['group_name'],
            'unix_gid': r['unix_gid'],
        })

    from sam.core.groups import resolve_group_name
    primary_group_name = resolve_group_name(db.session, sam_user.primary_gid)

    from sam.queries.shells import get_user_current_shell, get_allowable_shell_names
    current_shell = get_user_current_shell(db.session, sam_user)
    allowable_shells = get_allowable_shell_names(db.session)
    can_edit_shell = has_permission(current_user, Permission.EDIT_USERS)

    from webapp.dashboards.user.blueprint import _available_primary_groups
    available_groups = _available_primary_groups(db.session, sam_user.username)
    can_edit_primary_gid = has_permission(current_user, Permission.EDIT_USERS)

    return render_template(
        'dashboards/admin/fragments/user_card_wrapper.html',
        sam_user=sam_user,
        user_groups=user_groups,
        primary_group_name=primary_group_name,
        current_shell=current_shell,
        allowable_shells=allowable_shells,
        can_edit_shell=can_edit_shell,
        available_groups=available_groups,
        can_edit_primary_gid=can_edit_primary_gid,
    )


@bp.route('/group/<group_name>')
@login_required
@require_permission_any_facility(Permission.VIEW_GROUPS)
def group_card(group_name):
    """HTML fragment for a single adhoc-group card (admin group search).

    Assembles members across every access branch the group exists in, plus
    the list of users whose primary_gid points at this group.
    """
    from sam.core.groups import AdhocGroup
    from sam.queries.lookups import get_group_branches, get_group_members

    group = AdhocGroup.get_by_name(db.session, group_name)
    if group is None:
        return '<div class="alert alert-warning">Group not found</div>'

    branches = get_group_branches(db.session, group_name, active_only=False)
    members_by_branch = {}
    for branch in branches:
        data = get_group_members(db.session, group_name, branch, active_only=False)
        if data is not None:
            members_by_branch[branch] = data['members']

    primary_gid_users = (
        db.session.query(User)
        .filter(User.primary_gid == group.unix_gid)
        .order_by(User.last_name, User.first_name, User.username)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/group_card_wrapper.html',
        group=group,
        members_by_branch=members_by_branch,
        primary_gid_users=primary_gid_users,
        # Same gate as the user_card route the member rows target.
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS),
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

    # ONE bulk query for the whole page, outside the loop above — which is
    # already N+1 on get_project_dashboard_data and does not need a second
    # per-project round trip stapled to it.
    #
    # The key is set ONLY here, and for every project on the page including
    # the never-notified ones. `render_project_card` is shared with the user
    # dashboard, which never sets it, so the badge is absent there by
    # construction rather than by a permission check that could be forgotten.
    notices = get_expiration_notice_status(db.session, sorted(seen_projcodes))
    for project_data in projects_data:
        project_data['notification'] = notices[project_data['project'].projcode]

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
@require_permission_any_facility(Permission.VIEW_PROJECTS)
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
    facilities = apply_facility_scope(
        request.args.getlist('facilities'),
        Permission.VIEW_PROJECTS,
        default=['UNIV', 'WNA'],
    )
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


@bp.route('/expirations/deactivate-expired', methods=['POST'])
@login_required
@require_permission_any_facility(Permission.EDIT_PROJECTS)
def deactivate_expired():
    """
    Bulk-deactivate every project currently shown on the Expired (90+ days)
    tab, respecting the same facility/resource filters. Re-runs the query
    server-side so the action operates on exactly the set the user saw.
    """
    facilities = apply_facility_scope(
        request.form.getlist('facilities'),
        Permission.EDIT_PROJECTS,
        default=['UNIV', 'WNA'],
    )
    resource = request.form.get('resource') or None

    results = get_projects_with_expired_allocations(
        db.session,
        min_days_expired=90,
        max_days_expired=365,
        facility_names=facilities,
        resource_name=resource,
    )
    # Query returns (project, allocation, ...) tuples; a project can have
    # multiple expired allocations — deduplicate by project_id.
    unique_projects = {p.project_id: p for (p, _a, _r, _d) in results}.values()
    for project in unique_projects:
        project.update(active=False)
    db.session.commit()

    # Re-query so the now-inactive projects fall out (include_inactive_projects
    # defaults to False), then re-render the Expired fragment + OOB count badge.
    refreshed = get_projects_with_expired_allocations(
        db.session,
        min_days_expired=90,
        max_days_expired=365,
        facility_names=facilities,
        resource_name=resource,
    )
    projects_data = _build_expiration_project_data(refreshed)
    html = render_template(
        'dashboards/admin/fragments/expirations_cards.html',
        projects_data=projects_data,
        view_type='expired',
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD,
    )
    badge = f'<span id="expired-count" hx-swap-oob="true" class="badge bg-primary">{len(projects_data)}</span>'
    return html + badge


@bp.route('/expirations/export')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
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
    facilities = apply_facility_scope(
        request.args.getlist('facilities'),
        Permission.VIEW_PROJECTS,
        default=['UNIV', 'WNA'],
    )
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

@bp.route('/htmx/search/users')
@login_required
def htmx_search_users():
    """Unified user search endpoint.

    The ``context`` query parameter selects the response template AND
    the permission gate:
      fk          → FK-picker badge list (create_resource, create_contract,
                    create_project); requires VIEW_USERS (any-facility —
                    scoped managers need to pick users for their
                    in-scope projects).
      impersonate → admin user list with active_only filter (shown in the
                    Users & Groups tab). Requires VIEW_USERS (any-facility);
                    the impersonate button inside each result row is
                    itself gated on IMPERSONATE_USERS in the template,
                    so non-impersonators see a plain directory listing.
      member      → project member add list; requires can_manage_project_members
                    on the target project (projcode required), so project
                    leads/admins can search when building the add-member form.

    All other contexts fall back to ``fk``.
    """
    from sam.queries.users import search_users_by_pattern, get_project_member_user_ids
    from sam.projects.projects import Project
    from webapp.utils.project_permissions import can_manage_project_members

    q = request.args.get('q', '').strip()
    context = request.args.get('context', 'fk')

    if len(q) < 2:
        return ''

    template_map = {
        'fk':          'dashboards/admin/fragments/user_search_results_fk_htmx.html',
        'impersonate': 'dashboards/admin/fragments/user_search_results_htmx.html',
        'member':      'dashboards/user/fragments/user_search_results_htmx.html',
    }
    template = template_map.get(context, template_map['fk'])

    # The 'impersonate' box drives this from a checkbox (absent = unchecked =
    # include inactive). The fk/member pickers have no checkbox and never send
    # the param, so they keep the active-only default.
    active_only = read_active_only(request.args,
                                   default=context != 'impersonate')
    exclude_ids = None

    if context == 'member':
        projcode = request.args.get('projcode', '')
        if not projcode:
            abort(400)
        project = db.session.query(Project).filter_by(projcode=projcode).first()
        if not project:
            abort(404)
        if not can_manage_project_members(current_user, project):
            abort(403)
        exclude_ids = get_project_member_user_ids(db.session, project.project_id)
    else:
        # Both 'fk' and 'impersonate' contexts return a listing of users;
        # the impersonate button inside the 'impersonate' result row is
        # template-gated on IMPERSONATE_USERS, so admitting VIEW_USERS
        # holders (including facility-scoped managers) is safe here.
        if not has_permission_any_facility(current_user, Permission.VIEW_USERS):
            abort(403)

    users = search_users_by_pattern(
        db.session, q, limit=20 if context == 'impersonate' else 15,
        active_only=active_only, exclude_user_ids=exclude_ids
    )

    return render_template(template, users=users, q=q)


def _search_groups(q, active_only):
    from sam.queries.lookups import search_groups_by_pattern
    return search_groups_by_pattern(db.session, q, limit=20,
                                    active_only=active_only)


register_typeahead(
    bp, rule='/htmx/search/groups', endpoint='htmx_search_groups',
    permission=Permission.VIEW_GROUPS, any_facility=True,
    search=_search_groups,
    template='dashboards/admin/fragments/group_search_results_htmx.html',
    ctx_key='groups',
)


def _search_users_impersonate(q, active_only):
    from sam.queries.users import search_users_by_pattern
    return search_users_by_pattern(db.session, q, limit=20,
                                   active_only=active_only)


# Old impersonate endpoint kept as alias for backward compatibility —
# deprecated in favor of /htmx/search/users?context=impersonate.
register_typeahead(
    bp, rule='/htmx/search-users-impersonate',
    endpoint='htmx_search_users_impersonate',
    permission=Permission.IMPERSONATE_USERS,
    search=_search_users_impersonate,
    template='dashboards/admin/fragments/user_search_results_htmx.html',
    ctx_key='users',
)


@bp.route('/htmx/search-projects')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def htmx_search_projects():
    """
    Search projects and return results as HTML fragments.

    Each result has hx-get to load the project card directly into
    #projectCardContainer when clicked.
    """
    from sam.queries.projects import search_projects_by_code_or_title

    query = request.args.get('q', '').strip()
    active_only = read_active_only(request.args)

    if len(query) < 1:
        return ''

    # Facility-scoped users see only their allowed set. None → no filter.
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if allowed == set():
        # Scoped user with no VIEW_PROJECTS entry anywhere → no results.
        return ''
    facility_filter = None if allowed is None else sorted(allowed)

    projects = search_projects_by_code_or_title(
        db.session, query,
        active=True if active_only else None,
        facility_names=facility_filter,
    )[:10]  # Limit results

    return render_template(
        'dashboards/admin/fragments/project_search_results_htmx.html',
        projects=projects
    )


# ============================================================================
# Wallclock Exemption HTMX Routes
# ============================================================================

def _resources_with_queues():
    """Active resources that have queues — the exemption forms' picker."""
    from sam.resources.resources import Resource
    resources = (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )
    return [r for r in resources if r.queues]


@bp.route('/htmx/exemption-form/<username>')
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_add_exemption_form(username):
    """
    Return the add-exemption form fragment for a user (loaded into modal).
    """
    sam_user = db.session.query(User).filter_by(username=username).first()
    if not sam_user:
        return '<div class="alert alert-warning">User not found</div>'

    return render_template(
        'dashboards/admin/fragments/add_exemption_form_htmx.html',
        sam_user=sam_user,
        resources=_resources_with_queues(),
        today=datetime.now().strftime('%Y-%m-%d')
    )


class _AddExemptionHandler(HtmxFormHandler):
    """Create a wallclock exemption for a route-resolved user."""

    schema_cls = CreateWallclockExemptionForm
    template = 'dashboards/admin/fragments/add_exemption_form_htmx.html'
    error_prefix = 'Error creating exemption'
    success_message = 'Exemption saved successfully.'

    def perform(self, data):
        from sam.operational import WallclockExemption
        WallclockExemption.create(
            db.session,
            user_id=self.sam_user.user_id,
            queue_id=data['queue_id'],
            start_date=datetime.combine(data['start_date'], datetime.min.time()),
            end_date=data['end_date'],
            time_limit_hours=data['time_limit_hours'],
            comment=data.get('comment') or None,
        )

    def context(self):
        return {
            'sam_user': self.sam_user,
            'resources': _resources_with_queues(),
            'today': datetime.now().strftime('%Y-%m-%d'),
        }

    def render_errors(self, errors, field_errors=None):
        # queue_id is the cascading Resource→Queue inline <select>, not a
        # form_fields macro — its errors have no inline slot, so surface
        # them in the panel (the schema messages are full sentences).
        field_errors = dict(field_errors or {})
        errors = list(errors) + field_errors.pop('queue_id', [])
        return super().render_errors(errors, field_errors)

    def triggers(self, result):
        return {'closeActiveModal': {},
                'reloadUserCard': self.sam_user.username,
                'reloadResourcesCard': {}}


@bp.route('/htmx/exemption/<username>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_add_exemption(username):
    """
    Create a wallclock exemption for the given user.
    On success returns a script that closes the modal and refreshes the user card.
    On error re-renders the form with validation messages.
    """
    sam_user = db.session.query(User).filter_by(username=username).first()
    if not sam_user:
        return '<div class="alert alert-danger">User not found</div>', 404

    return _AddExemptionHandler(sam_user=sam_user).handle()


@bp.route('/htmx/admin/exemption-form')
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_admin_exemption_form():
    """Return the add-exemption form fragment for the admin "New" button on
    the Wallclock Exemptions card row (no preselected user)."""
    return render_template(
        'dashboards/admin/fragments/add_exemption_form_htmx.html',
        sam_user=None,
        resources=_resources_with_queues(),
        today=datetime.now().strftime('%Y-%m-%d')
    )


class _AdminCreateExemptionHandler(_AddExemptionHandler):
    """Admin "New" button variant — the user arrives from an FK picker."""

    schema_cls = AdminCreateWallclockExemptionForm

    def clean(self, data):
        self.sam_user = db.session.get(User, data['user_id'])
        if not self.sam_user:
            raise FormError('Selected user does not exist.')
        return data

    def context(self):
        # Not super().context() — that reads self.sam_user, which is only
        # set once clean() has run (schema errors re-render before that).
        return {
            'sam_user': None,
            'resources': _resources_with_queues(),
            'today': datetime.now().strftime('%Y-%m-%d'),
        }

    def triggers(self, result):
        return {'closeActiveModal': {}, 'reloadResourcesCard': {}}


@bp.route('/htmx/admin/exemption/create', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_admin_exemption_create():
    """Create a wallclock exemption from the admin "New" button.

    Reads user_id from the FK picker; otherwise mirrors htmx_add_exemption.
    """
    return _AdminCreateExemptionHandler().handle()


@bp.route('/htmx/exemption-edit-form/<int:exemption_id>')
@login_required
@require_permission(Permission.EDIT_USERS)
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


class _EditExemptionHandler(HtmxFormHandler):
    """Update a wallclock exemption's end date / limit / comment."""

    schema_cls = EditWallclockExemptionForm
    template = 'dashboards/admin/fragments/edit_exemption_form_htmx.html'
    error_prefix = 'Error updating exemption'
    success_message = 'Exemption saved successfully.'

    def clean(self, data):
        if data['end_date'] <= self.exemption.start_date:
            raise FormError('End date must be after start date.')
        return data

    def perform(self, data):
        self.exemption.update(
            end_date=data['end_date'],
            time_limit_hours=data['time_limit_hours'],
            comment=data.get('comment') or '',
        )

    def context(self):
        return {'exemption': self.exemption}

    def triggers(self, result):
        return {'closeActiveModal': {},
                'reloadUserCard': self.exemption.user.username,
                'reloadResourcesCard': {}}


@bp.route('/htmx/exemption-edit/<int:exemption_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_edit_exemption(exemption_id):
    """
    Update a wallclock exemption.
    On success returns a script that closes the modal and refreshes the user card.
    On error re-renders the form with validation messages.
    """
    from sam.operational import WallclockExemption

    exemption = db.session.get(WallclockExemption, exemption_id)
    if not exemption:
        return '<div class="alert alert-danger">Exemption not found</div>', 404

    return _EditExemptionHandler(exemption=exemption).handle()


@bp.route('/htmx/exemption-deactivate/<int:exemption_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_deactivate_exemption(exemption_id):
    """
    Soft-deactivate a wallclock exemption by setting its end_date to now.
    Fires reloadUserCard + reloadResourcesCard so both views refresh.
    """
    from sam.operational import WallclockExemption
    from sam.manage import management_transaction

    exemption = db.session.get(WallclockExemption, exemption_id)
    if not exemption:
        return htmx_not_found('Exemption')

    username = exemption.user.username
    with management_transaction(db.session):
        exemption.deactivate()

    return htmx_success_message(
        {'reloadUserCard': username, 'reloadResourcesCard': {}},
        'Exemption deactivated.',
    )


@bp.route('/htmx/queues-for-resource')
@login_required
@require_permission_any_facility(Permission.VIEW_RESOURCES)
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

from . import resources_routes, facilities_routes, orgs_routes, contracts_routes, projects_routes, configuration_routes, rate_limits_routes, notifications_routes, tasks_routes  # noqa: E402, F401

"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, abort, render_template, request, flash, redirect, url_for, session, jsonify, make_response, current_app
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from marshmallow import ValidationError

from sam.schemas.forms.user import EditAllocationForm, SetShellForm, SetPrimaryGidForm

from webapp.extensions import db
from sam.queries.dashboard import get_user_dashboard_data, get_resource_detail_data, get_project_dashboard_data
from sam.queries.disk_usage import (
    build_disk_subtree,
    get_directory_user_breakdown_at,
    get_disk_usage_timeseries_by_user,
    get_disk_usage_timeseries_for_directory,
    get_subtree_directory_usage_at,
)
from sam.queries.rolling_usage import get_project_rolling_usage
from sam.queries.charges import (
    get_user_queue_breakdown_for_project,
    get_daily_breakdown_for_project,
    get_user_summary_for_project,
    get_daily_summary_for_project,
    get_daily_user_usage_for_project,
    get_monthly_user_counts_for_project,
    get_charges_by_projcode,
)
from sam.queries.lookups import find_project_by_code, get_user_group_access, get_group_members
from sam.queries.shells import get_allowable_shell_names, get_user_current_shell
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.core.users import User
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.summaries.disk_summaries import DiskChargeSummary
from sqlalchemy import func
from webapp.utils.project_permissions import can_edit_consumption_threshold
from webapp.utils.rbac import require_permission, Permission, has_permission
from webapp.api.access_control import (
    require_allocation_permission,
    require_project_access,
    require_threshold_edit,
)
from ..charts import (
    generate_usage_timeseries_matplotlib,
    generate_usage_timeseries_stacked_by_user,
    generate_disk_usage_stacked_area,
)
from webapp.disk_scans import is_enabled as is_fs_scans_enabled
from webapp.disk_scans import service as disk_scans_service


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

    # Adhoc group memberships, regrouped by access branch for the user card tabs
    user_groups = _group_access_by_branch(db.session, user_to_display.username)

    from sam.core.groups import resolve_group_name
    primary_group_name = resolve_group_name(db.session, user_to_display.primary_gid)

    current_shell = get_user_current_shell(db.session, user_to_display)
    allowable_shells = get_allowable_shell_names(db.session)

    available_groups = _available_primary_groups(db.session, user_to_display.username)

    # "My Data" tab — per-user filesystem-scan card (one subtab per warmed disk
    # resource). Available to every authenticated user (no permission gate); the
    # scan owner is pinned to the user's unix_uid server-side in disk_scans
    # routes. Hidden when the fs-scans plugin is off / no resource is warmed, or
    # when the account has no filesystem identity (unix_uid). fs_scan_resources
    # also feeds the subtab strip in my_data_scans.html.
    fs_scan_resources = disk_scans_service.scan_capable_resources()
    my_data_available = bool(
        getattr(user_to_display, 'unix_uid', None) is not None
        and fs_scan_resources
    )

    return render_template(
        'dashboards/user/dashboard.html',
        user=user_to_display,
        dashboard_data=dashboard_data,
        user_groups=user_groups,
        primary_group_name=primary_group_name,
        current_shell=current_shell,
        allowable_shells=allowable_shells,
        can_edit_shell=True,   # user is editing their own
        available_groups=available_groups,
        can_edit_primary_gid=True,   # user is editing their own
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD,
        impersonator_id=impersonator_id,
        my_data_available=my_data_available,
        fs_scan_resources=fs_scan_resources,
    )


# ---------------------------------------------------------------------------
# Login-shell HTMX routes
# ---------------------------------------------------------------------------

def _can_edit_shell_for(username):
    """Self or admin."""
    return (current_user.is_authenticated and (
        current_user.username == username
        or has_permission(current_user, Permission.EDIT_USERS)
    ))


def _load_user_for_shell(username):
    user = db.session.query(User).filter_by(username=username).first()
    if user is None:
        return None, ('<div class="alert alert-warning m-2">User not found</div>', 404)
    if not _can_edit_shell_for(username):
        return None, ('<div class="alert alert-danger m-2">Unauthorized</div>', 403)
    return user, None


@bp.route('/htmx/shell-display/<username>')
@login_required
def htmx_shell_display(username):
    """Re-render the read-only shell row (post-save and cancel target)."""
    user, err = _load_user_for_shell(username)
    if err:
        return err
    return render_template(
        'dashboards/user/fragments/shell_display_htmx.html',
        sam_user=user,
        current_shell=get_user_current_shell(db.session, user),
        can_edit_shell=True,
    )


@bp.route('/htmx/shell-form/<username>')
@login_required
def htmx_shell_form(username):
    """Render the inline picker + Save/Cancel."""
    user, err = _load_user_for_shell(username)
    if err:
        return err
    return render_template(
        'dashboards/user/fragments/shell_form_htmx.html',
        sam_user=user,
        current_shell=get_user_current_shell(db.session, user),
        allowable_shells=get_allowable_shell_names(db.session),
        errors=[],
    )


@bp.route('/htmx/shell/<username>', methods=['POST'])
@login_required
def htmx_set_shell(username):
    """Apply shell to all active HPC/DAV resources; return the display row."""
    user, err = _load_user_for_shell(username)
    if err:
        return err

    allowable = get_allowable_shell_names(db.session)

    try:
        form_data = SetShellForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/user/fragments/shell_form_htmx.html',
            sam_user=user,
            current_shell=get_user_current_shell(db.session, user),
            allowable_shells=allowable,
            errors=SetShellForm.flatten_errors(e.messages),
        )

    shell_name = form_data['shell_name']
    if shell_name not in allowable:
        return render_template(
            'dashboards/user/fragments/shell_form_htmx.html',
            sam_user=user,
            current_shell=get_user_current_shell(db.session, user),
            allowable_shells=allowable,
            errors=[f'Shell {shell_name!r} is not in the allowable set.'],
        )

    from sam.manage import management_transaction
    try:
        with management_transaction(db.session):
            user.set_login_shell(shell_name)
    except ValueError as e:
        return render_template(
            'dashboards/user/fragments/shell_form_htmx.html',
            sam_user=user,
            current_shell=get_user_current_shell(db.session, user),
            allowable_shells=allowable,
            errors=[str(e)],
        )

    return render_template(
        'dashboards/user/fragments/shell_display_htmx.html',
        sam_user=user,
        current_shell=get_user_current_shell(db.session, user),
        can_edit_shell=True,
    )


# ---------------------------------------------------------------------------
# Primary-GID HTMX routes
# ---------------------------------------------------------------------------

def _can_edit_primary_gid_for(username):
    """Self or admin."""
    return (current_user.is_authenticated and (
        current_user.username == username
        or has_permission(current_user, Permission.EDIT_USERS)
    ))


def _load_user_for_primary_gid(username):
    user = db.session.query(User).filter_by(username=username).first()
    if user is None:
        return None, ('<div class="alert alert-warning m-2">User not found</div>', 404)
    if not _can_edit_primary_gid_for(username):
        return None, ('<div class="alert alert-danger m-2">Unauthorized</div>', 403)
    return user, None


def _available_primary_groups(session, username):
    """De-duplicated list of {unix_gid, group_name} a user may set as their
    primary GID, sorted by group_name. Wraps ``get_user_group_access`` with
    ``include_projects=True`` to match legacy union semantics."""
    memberships = get_user_group_access(
        session, username=username, include_projects=True,
    ).get(username, [])
    seen = {}
    for m in memberships:
        gid = m['unix_gid']
        if gid is not None:
            seen.setdefault(gid, m['group_name'])
    return sorted(
        ({'unix_gid': gid, 'group_name': name} for gid, name in seen.items()),
        key=lambda g: g['group_name'],
    )


@bp.route('/htmx/primary-gid-display/<username>')
@login_required
def htmx_primary_gid_display(username):
    """Re-render the read-only primary-GID row (post-save and cancel target)."""
    user, err = _load_user_for_primary_gid(username)
    if err:
        return err
    from sam.core.groups import resolve_group_name
    return render_template(
        'dashboards/user/fragments/primary_gid_display_htmx.html',
        sam_user=user,
        primary_group_name=resolve_group_name(db.session, user.primary_gid),
        can_edit_primary_gid=True,
    )


@bp.route('/htmx/primary-gid-form/<username>')
@login_required
def htmx_primary_gid_form(username):
    """Render the inline picker + Save/Cancel."""
    user, err = _load_user_for_primary_gid(username)
    if err:
        return err
    return render_template(
        'dashboards/user/fragments/primary_gid_form_htmx.html',
        sam_user=user,
        available_groups=_available_primary_groups(db.session, user.username),
        errors=[],
    )


@bp.route('/htmx/primary-gid/<username>', methods=['POST'])
@login_required
def htmx_set_primary_gid(username):
    """Apply the selected GID to the user's primary_gid; return the display row."""
    user, err = _load_user_for_primary_gid(username)
    if err:
        return err

    available = _available_primary_groups(db.session, user.username)

    try:
        form_data = SetPrimaryGidForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/user/fragments/primary_gid_form_htmx.html',
            sam_user=user,
            available_groups=available,
            errors=SetPrimaryGidForm.flatten_errors(e.messages),
        )

    from sam.manage import management_transaction
    try:
        with management_transaction(db.session):
            user.set_primary_gid(form_data['unix_gid'])
    except ValueError as e:
        return render_template(
            'dashboards/user/fragments/primary_gid_form_htmx.html',
            sam_user=user,
            available_groups=available,
            errors=[str(e)],
        )

    from sam.core.groups import resolve_group_name
    return render_template(
        'dashboards/user/fragments/primary_gid_display_htmx.html',
        sam_user=user,
        primary_group_name=resolve_group_name(db.session, user.primary_gid),
        can_edit_primary_gid=True,
    )


@bp.route('/htmx/group-members/<group_name>')
@login_required
def htmx_group_members(group_name):
    """
    HTMX payload: group+branch header and member table for the group modal.
    """
    branch = request.args.get('branch', '')
    if not branch:
        return '<div class="alert alert-danger m-3">Missing access branch</div>', 400
    data = get_group_members(db.session, group_name, branch)
    if not data:
        return '<div class="alert alert-warning m-3">Group not found</div>', 404
    return render_template(
        'dashboards/fragments/group_members_fragment.html',
        group=data,
    )


def _group_access_by_branch(session, username):
    """Regroup get_user_group_access() output into {branch: [{group_name, unix_gid}, ...]}.

    Returns an empty dict when the user has no adhoc group memberships.
    """
    rows = get_user_group_access(session, username=username).get(username, [])
    by_branch = {}
    for r in rows:
        by_branch.setdefault(r['access_branch_name'], []).append({
            'group_name': r['group_name'],
            'unix_gid': r['unix_gid'],
        })
    return by_branch


@bp.route('/resource-details/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def resource_details(project):
    """
    Resource usage detail view showing charts and job history.

    Path parameters:
        projcode: Project code (access-checked by @require_project_access)

    Query parameters:
        resource: Resource name
        start_date: Optional start date (default: 90 days ago)
        end_date: Optional end date (default: today)

    Returns:
        HTML page with server-rendered charts and usage data
    """
    projcode = project.projcode
    resource_name = request.args.get('resource')

    if not resource_name:
        flash('Missing resource name', 'error')
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

    # Fetch 30d/90d rolling window usage (HPC/DAV only; None for DISK/ARCHIVE).
    # When the allocation is inheriting, `charges` reported here is *pool burn*
    # across the whole shared-allocation tree (the runway-meaningful number);
    # `self_charges` on each window surfaces the per-project slice.
    rolling_usage = get_project_rolling_usage(db.session, projcode, resource_name=resource_name)
    rolling_resource = rolling_usage.get(resource_name, {})
    rolling_windows = rolling_resource.get('windows', {})
    rolling_30 = rolling_windows.get(30)
    rolling_90 = rolling_windows.get(90)
    rolling_is_inheriting = rolling_resource.get('is_inheriting', False)
    rolling_root_projcode = rolling_resource.get('root_projcode')

    # `project` is supplied (and access-checked) by @require_project_access.

    # Disk has different semantics than HPC/DAV (capacity, not burn-rate)
    # so it gets its own template + data assembly. Branch here once the
    # project is loaded; all the HPC-shaped scaffolding below is skipped.
    resource = db.session.query(Resource).filter(
        Resource.resource_name == resource_name,
    ).first()
    if resource is not None and resource.resource_type \
            and resource.resource_type.resource_type == 'DISK':
        return _render_disk_resource_details(
            project=project, resource=resource,
            start_date=start_date, end_date=end_date,
        )

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

    # Fetch top-tier summary rows only — one row per user / one row per
    # day. The deeper queue/date breakdowns are loaded lazily via the
    # /resource-details/{user,day}-subtree/<projcode> partial routes
    # when an analyst expands a row. Avoids producing tens of thousands
    # of leaf rows in the initial HTML for busy projects.
    user_breakdown = get_user_summary_for_project(
        db.session, all_projcodes, resource_name, start_date, end_date
    )
    daily_breakdown = get_daily_summary_for_project(
        db.session, all_projcodes, resource_name, start_date, end_date
    )
    # Monthly user-count header only matters in 3-level daily mode
    # (span > 45 days); skip the second query when we won't render it.
    monthly_user_counts = (
        get_monthly_user_counts_for_project(
            db.session, all_projcodes, resource_name, start_date, end_date,
        )
        if (end_date - start_date).days > 45
        else {}
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

    # Note: the Usage Trend chart is now loaded via HTMX
    # (resource_details_usage_chart route below) so the metric pill
    # selector — Charges / Job Count / Core-Hours — can swap the SVG
    # in place. The parent template just renders a loader div.

    # Which metric pills are available depends on resource type.
    # Disk/Archive summaries don't carry num_jobs / core_hours, so
    # those metric variants are suppressed for non-compute resources.
    usage_chart_metrics = ['charges']
    if detail_data.get('daily_jobs') is not None:
        usage_chart_metrics.append('jobs')
    if detail_data.get('daily_core_hours') is not None:
        usage_chart_metrics.append('core_hours')

    # Extract allocation start date for the "Epoch" date picker preset
    alloc_start = detail_data['resource_summary'].get('start_date')
    alloc_start_date = alloc_start.strftime('%Y-%m-%d') if alloc_start else None

    # Resolve the billing account whose charge adjustments we should surface.
    # For inheriting allocations, real charges (and any adjustments) live on
    # the master/shared account up at the root project — show those instead
    # of the empty local account row.
    summary = detail_data['resource_summary']
    adjustments_account_id = summary.get('account_id')
    adjustments_master_projcode = None
    if summary.get('is_inheriting') and summary.get('root_projcode') and resource is not None:
        master_account = (db.session.query(Account)
                                     .join(Project, Account.project_id == Project.project_id)
                                     .filter(Project.projcode == summary['root_projcode'])
                                     .filter(Account.resource_id == resource.resource_id)
                                     .first())
        if master_account is not None:
            adjustments_account_id = master_account.account_id
            adjustments_master_projcode = summary['root_projcode']

    account_adjustments = []
    if adjustments_account_id:
        adj_account = db.session.get(Account, adjustments_account_id)
        if adj_account is not None:
            account_adjustments = sorted(
                adj_account.charge_adjustments,
                key=lambda a: a.adjustment_date or datetime.min,
                reverse=True,
            )

    # Allocation history — transactions that built this allocation up to its
    # current amount/end-date. We show the *local* allocation's history so
    # the audit trail aligns with the "Allocated" figure in the table above.
    allocation_transactions = []
    history_allocation_id = summary.get('allocation_id')
    if history_allocation_id:
        allocation_obj = db.session.get(Allocation, history_allocation_id)
        if allocation_obj is not None:
            allocation_transactions = sorted(
                allocation_obj.transactions,
                key=lambda t: t.creation_time or datetime.min,
                reverse=True,
            )

    # Per-job drill-down (hpc-usage-queries) is keyed on the physical
    # machine, not the SAM resource_name — Derecho GPU jobs still live in
    # the "derecho" DB. None disables the "Show jobs" affordance.
    rn = (resource_name or '').lower()
    if 'derecho' in rn:
        jobs_machine = 'derecho'
    elif 'casper' in rn:
        jobs_machine = 'casper'
    else:
        jobs_machine = None

    return render_template(
        'dashboards/user/resource_details.html',
        user=current_user,
        project=project,
        projcode=projcode,
        resource_name=resource_name,
        jobs_machine=jobs_machine,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        detail_data=detail_data,
        user_breakdown=user_breakdown,
        daily_breakdown=daily_breakdown,
        monthly_user_counts=monthly_user_counts,
        date_span_days=(end_date - start_date).days,
        usage_chart_metrics=usage_chart_metrics,
        rolling_30=rolling_30,
        rolling_90=rolling_90,
        rolling_is_inheriting=rolling_is_inheriting,
        rolling_root_projcode=rolling_root_projcode,
        can_edit_threshold=can_edit_threshold,
        has_children=has_children,
        scope=scope,
        tree_data=tree_data,
        alloc_start_date=alloc_start_date,
        account_adjustments=account_adjustments,
        adjustments_master_projcode=adjustments_master_projcode,
        allocation_transactions=allocation_transactions,
    )


def _resolve_scope_projcodes(project, scope_projcode):
    """Expand a tree-scope projcode into the list of projcodes to query.

    Mirrors the logic in :func:`resource_details` so the subtree partial
    routes pull the same row set the main page does. An invalid scope
    silently falls back to the page's root projcode.
    """
    if scope_projcode == project.projcode:
        scope_project = project
    else:
        scope_project = Project.get_by_projcode(db.session, scope_projcode)
        if not scope_project or scope_project.tree_root != project.tree_root:
            scope_project = project

    if scope_project.has_children:
        return [p.projcode for p in scope_project.get_descendants(include_self=True)]
    return [scope_project.projcode]


def _parse_subtree_dates(start_raw, end_raw):
    """Parse YYYY-MM-DD start / end query params; defaults to last 90 days."""
    try:
        start_date = (datetime.strptime(start_raw, '%Y-%m-%d')
                      if start_raw else datetime.now() - timedelta(days=90))
        end_date = (datetime.strptime(end_raw, '%Y-%m-%d')
                    if end_raw else datetime.now())
    except ValueError:
        return None, None, 'Invalid date format. Please use YYYY-MM-DD.'
    return start_date, end_date, None


def _resolve_jobs_machine(resource_name):
    """Map a SAM resource name → hpc-usage-queries machine key. None disables drill."""
    rn = (resource_name or '').lower()
    if 'derecho' in rn:
        return 'derecho'
    if 'casper' in rn:
        return 'casper'
    return None


@bp.route('/resource-details/user-subtree/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def resource_details_user_subtree(project):
    """HTMX fragment: queue/date breakdown for a single user.

    Lazy-loaded by the resource-details page when an analyst expands a
    user row. Returns just the queue + date sub-rows for ``username``
    on ``resource`` in the date window; the page's table macro swaps
    the fragment into the empty ``<tbody>`` placeholder under the
    expanded user row.
    """
    resource_name = (request.args.get('resource') or '').strip()
    username      = (request.args.get('username') or '').strip()
    if not resource_name or not username:
        abort(400, 'resource and username are required')

    start_date, end_date, err = _parse_subtree_dates(
        request.args.get('start_date'), request.args.get('end_date'),
    )
    if err:
        abort(400, err)

    scope = (request.args.get('scope') or '').strip() or project.projcode
    all_projcodes = _resolve_scope_projcodes(project, scope)

    # One user's full queue/date breakdown — same shape the main page
    # used to fetch in bulk, but now scoped to one user.
    user_breakdown = get_user_queue_breakdown_for_project(
        db.session, all_projcodes, resource_name, start_date, end_date,
        username=username,
    )
    user_data = user_breakdown[0] if user_breakdown else None

    return render_template(
        'dashboards/user/partials/user_subtree.html',
        user=user_data,
        uid=request.args.get('uid', 'u'),
        projcode=project.projcode,
        jobs_machine=_resolve_jobs_machine(resource_name),
    )


@bp.route('/resource-details/day-subtree/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def resource_details_day_subtree(project):
    """HTMX fragment: user/queue breakdown for a single day.

    Lazy-loaded by the resource-details daily-breakdown table when an
    analyst expands a day (or day-within-month) row. Returns the
    user/queue sub-rows for ``date`` on ``resource``.
    """
    resource_name = (request.args.get('resource') or '').strip()
    date_str      = (request.args.get('date') or '').strip()
    if not resource_name or not date_str:
        abort(400, 'resource and date are required')

    try:
        day = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        abort(400, 'Invalid date format. Please use YYYY-MM-DD.')

    scope = (request.args.get('scope') or '').strip() or project.projcode
    all_projcodes = _resolve_scope_projcodes(project, scope)

    # Single day; passing start=end=day scopes the breakdown to that
    # date without a new query function.
    daily_breakdown = get_daily_breakdown_for_project(
        db.session, all_projcodes, resource_name, day, day,
    )
    day_data = daily_breakdown[0] if daily_breakdown else None

    return render_template(
        'dashboards/user/partials/day_subtree.html',
        day=day_data,
        did=request.args.get('did', 'd'),
        projcode=project.projcode,
        jobs_machine=_resolve_jobs_machine(resource_name),
    )


_VALID_USAGE_CHART_METRIC = {'charges', 'jobs', 'core_hours'}
_USAGE_CHART_DATA_KEY = {
    'charges':    'daily_charges',
    'jobs':       'daily_jobs',
    'core_hours': 'daily_core_hours',
}


@bp.route('/resource-details/usage-chart/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def resource_details_usage_chart(project):
    """HTMX fragment: Usage Trend chart for the selected metric.

    Loaded by the resource-details page on initial render and re-fetched
    when the analyst toggles a metric pill (Charges / Job Count /
    Core-Hours). Each metric variant has its own ``chart_cached`` entry
    keyed on the daily series hash + metric tag.
    """
    resource_name = (request.args.get('resource') or '').strip()
    if not resource_name:
        abort(400, 'resource is required')

    metric = request.args.get('metric', 'charges')
    if metric not in _VALID_USAGE_CHART_METRIC:
        metric = 'charges'

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
        abort(400, 'Invalid date format. Please use YYYY-MM-DD.')

    scope = (request.args.get('scope') or '').strip() or project.projcode
    # Validate scope belongs to this project's tree; fall back to root if not
    if scope != project.projcode:
        scope_project = Project.get_by_projcode(db.session, scope)
        if not scope_project or scope_project.tree_root != project.tree_root:
            scope = project.projcode

    detail_data = get_resource_detail_data(
        db.session,
        project.projcode,
        resource_name,
        start_date,
        end_date,
        scope_projcode=scope,
    )

    # Available metrics depend on resource type — disk/archive lack jobs / core_hours.
    available = ['charges']
    if detail_data and detail_data.get('daily_jobs') is not None:
        available.append('jobs')
    if detail_data and detail_data.get('daily_core_hours') is not None:
        available.append('core_hours')
    if metric not in available:
        metric = 'charges'

    # Stacked-by-user variant: each daily bar is segmented by the top-10
    # users over the whole window + "Others", ranked by the displayed metric.
    # Falls back to the flat single-series bars when the period has <= 1 user
    # (a 1-colour stack is pointless and the Usage by User card is hidden
    # there too) or for non-compute resources, where comp_charge_summary
    # yields no per-user rows.
    scope_projcodes = _resolve_scope_projcodes(project, scope)
    stacked = get_daily_user_usage_for_project(
        db.session, scope_projcodes, resource_name, start_date, end_date,
        metric=metric,
    )
    named_series = [s for s in stacked['series'] if s['label'] != 'Others']

    if len(named_series) > 1:
        svg = generate_usage_timeseries_stacked_by_user(stacked, metric=metric)
        has_data = True
        is_stacked = True
    else:
        series = (detail_data or {}).get(_USAGE_CHART_DATA_KEY[metric])
        svg = generate_usage_timeseries_matplotlib(
            series or {'dates': [], 'values': []},
            link_to_day_rows=True,
            metric=metric,
        )
        has_data = bool(series and series.get('values'))
        is_stacked = False

    return render_template(
        'dashboards/user/partials/usage_chart.html',
        chart_svg=svg,
        metric=metric,
        available_metrics=available,
        projcode=project.projcode,
        resource_name=resource_name,
        scope=scope,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        has_data=has_data,
        is_stacked=is_stacked,
    )


def _disk_subtree_total_bytes(node) -> int:
    """Sum ``current_bytes`` over a tree node and all its descendants."""
    total = node.get('current_bytes', 0) or 0
    for c in node.get('children', []):
        total += _disk_subtree_total_bytes(c)
    return total


def _disk_subtree_total_files(node) -> int:
    total = node.get('file_count', 0) or 0
    for c in node.get('children', []):
        total += _disk_subtree_total_files(c)
    return total


def _disk_subtree_latest_activity_date(node):
    """Most recent ``activity_date`` across the subtree (or None)."""
    best = node.get('activity_date')
    for c in node.get('children', []):
        cand = _disk_subtree_latest_activity_date(c)
        if cand is not None and (best is None or cand > best):
            best = cand
    return best


def _find_disk_node(tree, projcode):
    """Locate a node by projcode in a build_disk_subtree tree dict."""
    if tree.get('projcode') == projcode:
        return tree
    for c in tree.get('children', []):
        hit = _find_disk_node(c, projcode)
        if hit is not None:
            return hit
    return None


def _render_disk_resource_details(*, project, resource, start_date, end_date):
    """Disk-flavored Resource Usage Details page.

    Replaces the HPC/DAV daily-charges shape with capacity-oriented
    components: a current-snapshot capacity header, a stacked-area chart
    of bytes vs time (top-N users + Others), a filesystem-style project
    tree, and a per-user table for the latest snapshot. Scope (subtree
    selection) is set via the ``?scope=`` query param exactly like the
    HPC view.
    """
    resource_name = resource.resource_name
    scope = request.args.get('scope', project.projcode)
    fileset = request.args.get('fileset') or None

    # Validate scope belongs to this project's tree; fall back to root.
    if scope != project.projcode:
        candidate = Project.get_by_projcode(db.session, scope)
        if candidate is None or candidate.tree_root != project.tree_root:
            scope = project.projcode

    # Always build the full tree from the user's root project so the
    # tree-navigation card shows everything; chart + table re-scope by
    # locating the scope node within it.
    full = build_disk_subtree(db.session, project, resource_name)
    full_tree = full['tree']
    scope_node = _find_disk_node(full_tree, scope) or full_tree
    scope_account_ids = _collect_disk_account_ids(scope_node)

    # Build the {directory_name → projcode} map by walking the scoped
    # subtree's in-memory ProjectDirectory data (already loaded by
    # build_disk_subtree). Lets the Filesets-card query hit
    # disk_activity directly via directory_name IN (...) — a range
    # seek on disk_activity_unique_idx with no disk_charge / account
    # / project join.
    directory_to_projcode = _collect_directory_to_projcode(scope_node)

    # Aggregate per-fileset rows across the entire scoped subtree at
    # the latest snapshot date — single query covering every fileset
    # in the scope. This populates the Filesets card on *both*
    # leaf-project and tree pages (replacing the per-node
    # `directories` payload that build_disk_subtree only attached
    # for the multi-fileset case).
    subtree_activity_date = _disk_subtree_latest_activity_date(scope_node)
    fileset_dirs = get_subtree_directory_usage_at(
        db.session,
        directory_to_projcode=directory_to_projcode,
        resource_name=resource_name,
        activity_date=subtree_activity_date,
    )
    # Make the Filesets card visible to the template by hanging
    # the aggregated list off scope_node.
    scope_node['directories'] = fileset_dirs

    # Validate the fileset (if any) is one of the scope's directories.
    # An invalid `?fileset=` is silently dropped — fall back to
    # project-scope rendering.
    if fileset is not None:
        if not any(d['name'] == fileset for d in fileset_dirs):
            fileset = None

    # Pool capacity (TiB) for the scope: the master allocation's
    # amount, NOT the sum across child accounts. Inheriting children
    # share the parent's cap; summing them double-counts. See
    # `sam-admin accounting --reconcile-quotas` — NMMM0003 reads 16.4
    # PiB total, not 6 × parent.
    allocated_tib = _scope_disk_allocation_tib(
        db.session, scope_node['projcode'], resource,
    )

    if fileset is None:
        # Project-scope rendering (default).
        used_bytes = _disk_subtree_total_bytes(scope_node)
        used_tib = used_bytes / (1024 ** 4)
        total_files = _disk_subtree_total_files(scope_node)
        activity_date = _disk_subtree_latest_activity_date(scope_node)
        timeseries = get_disk_usage_timeseries_by_user(
            db.session,
            account_ids=scope_account_ids,
            start_date=start_date.date() if hasattr(start_date, 'date') else start_date,
            end_date=end_date.date() if hasattr(end_date, 'date') else end_date,
            top_n=15,
        )
        user_rows = _build_disk_user_table(
            db.session, scope_account_ids, activity_date, used_bytes,
        )
    else:
        # Fileset-scoped rendering: chart + per-user table read from
        # `disk_activity` filtered by `directory_name` directly — a
        # range seek on the existing unique index, no join through
        # `disk_charge`. The fileset name itself uniquely identifies
        # the data we want; subtree containment was already
        # validated when we constructed `fileset_dirs`.
        fileset_row = next(d for d in fileset_dirs if d['name'] == fileset)
        used_bytes = fileset_row['bytes']
        used_tib = used_bytes / (1024 ** 4)
        total_files = fileset_row['files']
        activity_date = subtree_activity_date
        timeseries = get_disk_usage_timeseries_for_directory(
            db.session,
            resource_name=resource_name,
            directory_name=fileset,
            start_date=start_date.date() if hasattr(start_date, 'date') else start_date,
            end_date=end_date.date() if hasattr(end_date, 'date') else end_date,
            top_n=15,
        )
        breakdown = get_directory_user_breakdown_at(
            db.session,
            resource_name=resource_name,
            directory_name=fileset,
            activity_date=activity_date,
        )
        user_rows = [{
            'user_id':       r['user_id'],
            'username':      r['username'],
            'bytes':         r['bytes'],
            'file_count':    r['files'],
            'percent_of_project':
                (r['bytes'] / used_bytes * 100) if used_bytes > 0 else 0.0,
        } for r in breakdown]

    # Filesystem Scans card: shown only when the fs-scans plugin is loaded
    # AND the viewed resource resolves to a live scan collection
    # (Campaign_Store today; Destor later). One scoped call also yields the
    # per-collection scan dates for the header freshness badge. Scoped to the
    # page's current ?scope= node so the card hides when a child scope owns no
    # scannable dirs even if the root project does.
    show_scans = False
    scan_info = None
    if is_fs_scans_enabled():
        # `scope` was already validated to be in this project's tree (or
        # reset to the root projcode), so a non-root scope is a known code.
        scope_project = (
            project if scope == project.projcode
            else Project.get_by_projcode(db.session, scope)
        )
        try:
            scan_info = disk_scans_service.scan_overview(
                db.session, scope_project, resource_name,
            )
            show_scans = bool(scan_info['collections'])
        except Exception:
            current_app.logger.exception(
                'disk scans: scope resolution failed for project=%s resource=%s',
                scope_node['projcode'], resource_name,
            )

    percent_used = (used_tib / allocated_tib * 100) if allocated_tib > 0 else 0.0
    # Operators (VIEW_USERS) get clickable legend usernames that pop the
    # user-details modal; non-operators get plain text since the modal
    # endpoint would 403 on click. Same gating shape as the queue-load
    # chart in status/blueprint.py:_render_user_proj_chart.
    disk_link_kind = (
        'user' if has_permission(current_user, Permission.VIEW_USERS) else None
    )
    usage_chart = generate_disk_usage_stacked_area(timeseries, link_kind=disk_link_kind)

    return render_template(
        'dashboards/user/resource_details_disk.html',
        user=current_user,
        projcode=project.projcode,
        project=project,
        resource_name=resource_name,
        resource=resource,
        scope=scope,
        scope_node=scope_node,
        fileset=fileset,
        tree_data=full_tree,
        has_children=bool(project.has_children),
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        usage_chart=usage_chart,
        show_scans=show_scans,
        scan_info=scan_info,
        capacity={
            'allocated_tib':  allocated_tib,
            'used_tib':       used_tib,
            'percent_used':   percent_used,
            'used_bytes':     used_bytes,
            'total_files':    total_files,
            'activity_date':  activity_date,
        },
        user_rows=user_rows,
    )


def _collect_disk_account_ids(node) -> list:
    out = []
    if node.get('account_id') is not None:
        out.append(node['account_id'])
    for c in node.get('children', []):
        out.extend(_collect_disk_account_ids(c))
    return out


def _collect_directory_to_projcode(node) -> dict:
    """Walk a build_disk_subtree node and return ``{directory_name: projcode}``.

    The map covers every node in the subtree that has at least one
    active fileset. Used by the disk dashboard to call
    ``get_subtree_directory_usage_at`` without a database round-trip
    for the projcode mapping.
    """
    out = {}
    for d in node.get('fileset_paths', []):
        out[d] = node.get('projcode')
    for c in node.get('children', []):
        out.update(_collect_directory_to_projcode(c))
    return out


def _scope_disk_allocation_tib(session, scope_projcode, resource) -> float:
    """Pool capacity (TiB) for the scoped project on a disk resource.

    The disk pool cap is the *master* allocation's amount — children
    that inherit from it share the cap, they do not add to it. So
    summing across all subtree allocations would double-count. We
    instead locate the scope project's account on this resource (or
    walk up the project tree if the scope itself doesn't hold an
    account), pick the active allocation, and return
    ``allocation.root.amount`` — which is the pool cap regardless of
    whether the scope is the root or an inheriting child. Returns 0.0
    if no active allocation can be located.
    """
    from sam.accounting.allocations import Allocation
    scope_project = Project.get_by_projcode(session, scope_projcode)
    if scope_project is None:
        return 0.0
    candidates = [scope_project] + scope_project.get_ancestors(include_self=False)
    now = datetime.now()
    for proj in candidates:
        account = session.query(Account).filter(
            Account.project_id == proj.project_id,
            Account.resource_id == resource.resource_id,
            Account.deleted == False,  # noqa: E712
        ).first()
        if account is None:
            continue
        for a in account.allocations:
            if a.deleted:
                continue
            if a.start_date and a.start_date > now:
                continue
            if a.end_date and a.end_date < now:
                continue
            return float(a.root.amount)
    return 0.0


def _build_disk_user_table(session, account_ids, activity_date, scope_bytes):
    """Per-user current-snapshot rows for the scope, sorted by bytes desc."""
    if not account_ids or activity_date is None:
        return []
    rows = session.query(
        DiskChargeSummary.user_id,
        DiskChargeSummary.username,
        func.coalesce(func.sum(DiskChargeSummary.bytes), 0).label('bytes'),
        func.coalesce(func.sum(DiskChargeSummary.number_of_files), 0).label('files'),
    ).filter(
        DiskChargeSummary.account_id.in_(account_ids),
        DiskChargeSummary.activity_date == activity_date,
    ).group_by(
        DiskChargeSummary.user_id,
        DiskChargeSummary.username,
    ).all()

    out = []
    for user_id, username, b, f in rows:
        bytes_ = int(b or 0)
        out.append({
            'user_id':       user_id,
            'username':      username or f'uid_{user_id}',
            'bytes':         bytes_,
            'file_count':    int(f or 0),
            'percent_of_project': (bytes_ / scope_bytes * 100) if scope_bytes > 0 else 0.0,
        })
    out.sort(key=lambda r: r['bytes'], reverse=True)
    return out


@bp.route('/tree/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def tree_fragment(project):
    """
    Lazy-loaded HTML fragment showing project hierarchy tree.

    Renders the shared render_project_tree macro
    (dashboards/shared/project_tree.html) via a thin wrapper template.

    Clickability (``can_view``) is true when the user has system
    VIEW_PROJECTS OR has any affiliation with the tree root (direct
    member / lead / admin, or lead/admin of an ancestor of the root).
    When true, every node in the displayed tree is clickable. When
    false, nodes render as plain text — the user can still enter a
    specific node's modal via other affordances if they're affiliated
    there, but the tree itself surfaces only the structure.

    Returns:
        HTML tree structure (no full page layout)
    """
    from webapp.api.access_control import _user_can_access_project, _get_sam_user

    active_only = request.args.get('active_only') == '1'
    root = project.get_root() if hasattr(project, 'get_root') else project
    sam_user = _get_sam_user()
    can_view = (
        has_permission(current_user, Permission.VIEW_PROJECTS)
        or _user_can_access_project(sam_user, root, include_ancestors=True)
    )

    return render_template(
        'dashboards/user/fragments/tree_htmx.html',
        root=root,
        current_projcode=project.projcode,
        active_only=active_only,
        can_view=can_view,
    )



@bp.route('/project-details-modal/<projcode>')
@login_required
@require_project_access(include_ancestors=True)
def project_details_modal(project):
    """
    Get HTML fragment for project details modal content (reusable across dashboards).

    Access: system VIEW_PROJECTS, direct project affiliation (member /
    lead / admin), or lead/admin of any ancestor in the project tree.

    Returns:
        HTML fragment with project info and resources for modal body
    """
    project_data = get_project_dashboard_data(db.session, project.projcode)

    import json
    resp = make_response(render_template(
        'dashboards/user/partials/project_details_modal.html',
        project_data=project_data,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    ))
    resp.headers['HX-Trigger'] = json.dumps({'setModalTitle': f'Project Details \u2014 {project.projcode}'})
    return resp


# ============================================================================
# htmx Routes
# ============================================================================
# Server-rendered HTML fragment routes for htmx-driven form handling.
# These replace custom JavaScript with hx-* attributes on HTML elements.
# All routes are prefixed with /htmx/ to avoid conflicts with API endpoints.
# ============================================================================

@bp.route('/htmx/edit-allocation-form/<int:allocation_id>')
@login_required
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation_form(allocation):
    """
    Return the edit allocation form as an HTML fragment, pre-populated from DB.

    Replaces the JS pattern of: fetch JSON → populate form fields client-side.
    """
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
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def htmx_edit_allocation(allocation):
    """
    Handle edit allocation form submission (htmx).

    On error: returns the form with error messages.
    On success: returns a script that closes the modal and triggers a
    refresh event so any open project details modal reloads.
    """
    from sam.manage import update_allocation, management_transaction

    allocation_id = allocation.allocation_id
    account = allocation.account
    resource_name = account.resource.resource_name if account and account.resource else 'Unknown'
    projcode = request.form.get('projcode', '')

    try:
        form_data = EditAllocationForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/user/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            resource_name=resource_name,
            projcode=projcode,
            errors=EditAllocationForm.flatten_errors(e.messages)
        )

    updates = {
        'amount': form_data['amount'],
        'end_date': form_data['end_date'],  # None explicitly clears end date
        'description': form_data['description'],
    }
    if form_data.get('start_date'):
        updates['start_date'] = datetime.combine(form_data['start_date'], datetime.min.time())

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


# ---------------------------------------------------------------------------
# Rolling consumption rate threshold editing (htmx)
# ---------------------------------------------------------------------------

def _get_account(project, resource_name):
    """Return the project's active account for a resource name, or None."""
    from sam import Account
    from sam.resources.resources import Resource

    return (
        db.session.query(Account)
        .join(Account.resource)
        .filter(Account.project_id == project.project_id)
        .filter(Resource.resource_name == resource_name)
        .filter(Account.deleted == False)
        .first()
    )


@bp.route('/htmx/rolling-section/<projcode>/<resource_name>')
@login_required
@require_project_access
def htmx_rolling_section(project, resource_name):
    """
    Return the re-rendered Rolling Consumption Rate section fragment.

    Used by the threshold form's cancel button and after a successful save
    to restore / refresh the rolling section without a full page reload.
    Membership (or VIEW_PROJECTS) required — previously unauthenticated
    access-check-free [PR295 P1-6].
    """
    projcode = project.projcode
    rolling_usage = get_project_rolling_usage(db.session, projcode, resource_name=resource_name)
    rolling_resource = rolling_usage.get(resource_name, {})
    windows = rolling_resource.get('windows', {})

    return render_template(
        'dashboards/user/fragments/rolling_rate_htmx.html',
        projcode=projcode,
        resource_name=resource_name,
        rolling_30=windows.get(30),
        rolling_90=windows.get(90),
        rolling_is_inheriting=rolling_resource.get('is_inheriting', False),
        rolling_root_projcode=rolling_resource.get('root_projcode'),
        can_edit_threshold=can_edit_consumption_threshold(current_user, project),
    )


@bp.route('/htmx/threshold-form/<projcode>/<resource_name>/<int:window>')
@login_required
@require_threshold_edit
def htmx_threshold_form(project, resource_name, window):
    """
    Return an inline threshold edit form for a specific rolling window.

    The form replaces the Add/Edit button via hx-target="this" hx-swap="outerHTML".
    window must be 30 or 90.
    """
    account = _get_account(project, resource_name)
    if not account:
        return '<div class="alert alert-danger">Account not found for this resource</div>', 404

    current = account.first_threshold if window == 30 else account.second_threshold

    return render_template(
        'dashboards/user/fragments/threshold_form_htmx.html',
        projcode=project.projcode,
        resource_name=resource_name,
        window=window,
        current_threshold=current,
        error=None,
    )


@bp.route('/htmx/threshold/<projcode>/<resource_name>/<int:window>', methods=['POST'])
@login_required
@require_threshold_edit
def htmx_save_threshold(project, resource_name, window):
    """
    Save a rolling consumption rate threshold for one window (30 or 90 days).

    Accepts form field: threshold_pct (integer > 100, or empty to clear).
    Returns the re-rendered rolling section on success, or the form with an
    error message on validation failure.
    """
    from sam.manage import management_transaction

    projcode = project.projcode
    account = _get_account(project, resource_name)
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
    rolling_resource = rolling_usage.get(resource_name, {})
    windows = rolling_resource.get('windows', {})

    return render_template(
        'dashboards/user/fragments/rolling_rate_htmx.html',
        projcode=projcode,
        resource_name=resource_name,
        rolling_30=windows.get(30),
        rolling_90=windows.get(90),
        rolling_is_inheriting=rolling_resource.get('is_inheriting', False),
        rolling_root_projcode=rolling_resource.get('root_projcode'),
        can_edit_threshold=True,
    )

"""
User dashboard blueprint for regular users.

Provides dashboard view for users to see their projects and allocation spending.

Refactored to use server-side rendering with direct ORM queries instead of
JavaScript API calls for improved performance and simplicity.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify, make_response
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from marshmallow import ValidationError

from sam.schemas.forms.user import EditAllocationForm, SetShellForm, SetPrimaryGidForm

from webapp.extensions import db
from sam.queries.dashboard import get_user_dashboard_data, get_resource_detail_data, get_project_dashboard_data
from sam.queries.disk_usage import build_disk_subtree, get_disk_usage_timeseries_by_user
from sam.queries.rolling_usage import get_project_rolling_usage
from sam.queries.charges import get_user_queue_breakdown_for_project, get_daily_breakdown_for_project, get_charges_by_projcode
from sam.queries.lookups import find_project_by_code, get_user_group_access, get_group_members
from sam.queries.shells import get_allowable_shell_names, get_user_current_shell
from sam.core.users import User
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.summaries.disk_summaries import DiskChargeSummary
from sqlalchemy import func
from webapp.utils.project_permissions import can_edit_consumption_threshold
from webapp.utils.rbac import require_permission, Permission, has_permission
from webapp.api.access_control import require_allocation_permission, require_project_access
from ..charts import generate_usage_timeseries_matplotlib, generate_disk_usage_stacked_area


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
        impersonator_id=impersonator_id
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

    # Load root project
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        flash(f'Project {projcode} not found', 'error')
        return redirect(url_for('user_dashboard.index'))

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
        rolling_is_inheriting=rolling_is_inheriting,
        rolling_root_projcode=rolling_root_projcode,
        can_edit_threshold=can_edit_threshold,
        has_children=has_children,
        scope=scope,
        tree_data=tree_data,
        alloc_start_date=alloc_start_date,
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

    # Active disk allocation on the scope's first account drives the
    # capacity bar's "allocated" denominator. For multi-account scopes
    # (rare for disk) sum allocations across the scope.
    allocated_tib = _sum_active_disk_allocations(
        db.session, scope_account_ids,
    )

    used_bytes = _disk_subtree_total_bytes(scope_node)
    used_tib = used_bytes / (1024 ** 4)
    total_files = _disk_subtree_total_files(scope_node)
    activity_date = _disk_subtree_latest_activity_date(scope_node)
    percent_used = (used_tib / allocated_tib * 100) if allocated_tib > 0 else 0.0

    # Time-series for the stacked-area chart.
    timeseries = get_disk_usage_timeseries_by_user(
        db.session,
        account_ids=scope_account_ids,
        start_date=start_date.date() if hasattr(start_date, 'date') else start_date,
        end_date=end_date.date() if hasattr(end_date, 'date') else end_date,
        top_n=10,
    )
    usage_chart = generate_disk_usage_stacked_area(timeseries)

    # Per-user table at the latest snapshot date for the scope.
    user_rows = _build_disk_user_table(
        db.session, scope_account_ids, activity_date, used_bytes,
    )

    return render_template(
        'dashboards/user/resource_details_disk.html',
        user=current_user,
        projcode=project.projcode,
        project=project,
        resource_name=resource_name,
        resource=resource,
        scope=scope,
        scope_node=scope_node,
        tree_data=full_tree,
        has_children=bool(project.has_children),
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
        usage_chart=usage_chart,
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


def _sum_active_disk_allocations(session, account_ids) -> float:
    """Sum the active disk allocation amounts (TiB) for a set of accounts."""
    if not account_ids:
        return 0.0
    from sam.accounting.allocations import Allocation
    now = datetime.now()
    rows = session.query(Allocation).filter(
        Allocation.account_id.in_(account_ids),
        Allocation.deleted == False,  # noqa: E712
        Allocation.start_date <= now,
        ((Allocation.end_date.is_(None)) | (Allocation.end_date >= now)),
    ).all()
    return float(sum(a.amount for a in rows))


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

"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, make_response, current_app
from flask_login import login_required, current_user
from webapp.utils.rbac import require_permission, Permission
from datetime import datetime, timedelta
import logging

from webapp.extensions import db
from ..charts import (
    generate_nodetype_history_matplotlib,
    generate_queue_history_matplotlib,
    generate_user_proj_stacked_area,
)

from system_status import queries as status_queries

bp = Blueprint('status_dashboard', __name__, url_prefix='/status')
logger = logging.getLogger(__name__)

@bp.route('/')
def index():
    """
    Main system status dashboard landing page.

    Queries latest status from all systems and renders server-side.
    Status models are routed to the `system_status` bind via
    `__bind_key__`, so `db.session` handles both reads and writes.

    The optional ``hours`` (or legacy ``days``) query param doesn't change
    what the dashboard queries — it's a stateless passthrough so drill-down
    row clicks inherit the user's last-set time range, and the back link
    on detail pages can carry it through. ``selected_hours`` is None when
    the param is absent (matches today's row-click URLs bit-for-bit).
    """
    session = db.session

    selected_hours = None
    if request.args.get('hours'):
        try:
            selected_hours = int(request.args['hours'])
        except ValueError:
            selected_hours = None
    elif request.args.get('days'):
        try:
            selected_hours = int(request.args['days']) * 24
        except ValueError:
            selected_hours = None

    # Get latest Derecho status
    derecho_status = status_queries.get_latest_derecho_status(session)

    derecho_queues = []
    derecho_filesystems = []
    derecho_login_nodes = []
    if derecho_status:
        derecho_queues = status_queries.get_latest_derecho_queues(session, derecho_status.timestamp)
        derecho_filesystems = status_queries.get_latest_derecho_filesystems(session, derecho_status.timestamp)
        derecho_login_nodes = status_queries.get_latest_derecho_login_nodes(session, derecho_status.timestamp)

    # Get latest Casper status
    casper_status = status_queries.get_latest_casper_status(session)

    casper_node_types = []
    casper_queues = []
    casper_login_nodes = []
    casper_filesystems = []
    if casper_status:
        casper_node_types = status_queries.get_latest_casper_node_types(session, casper_status.timestamp)
        casper_queues = status_queries.get_latest_casper_queues(session, casper_status.timestamp)
        casper_login_nodes = status_queries.get_latest_casper_login_nodes(session, casper_status.timestamp)
        casper_filesystems = status_queries.get_latest_casper_filesystems(session, casper_status.timestamp)

    # Get latest JupyterHub status
    jupyterhub_status = status_queries.get_latest_jupyterhub_status(session)

    # Get active outages
    outages = status_queries.get_active_outages(session)

    # Get upcoming reservations
    reservations = status_queries.get_upcoming_reservations(session)

    # Default the landing-page chart window to 7 days when no
    # ?hours= override is in the URL — matches the drill-down default
    # so the time_range_picker on each chart card reads sensibly on
    # first load. selected_hours stays None when absent (other code
    # paths that introspect it still get the same signal).
    chart_hours = selected_hours if selected_hours is not None else 168

    return render_template(
        'dashboards/status/dashboard.html',
        user=current_user,
        derecho_status=derecho_status,
        derecho_queues=derecho_queues,
        derecho_filesystems=derecho_filesystems,
        derecho_login_nodes=derecho_login_nodes,
        casper_status=casper_status,
        casper_node_types=casper_node_types,
        casper_queues=casper_queues,
        casper_login_nodes=casper_login_nodes,
        casper_filesystems=casper_filesystems,
        jupyterhub_status=jupyterhub_status,
        outages=outages,
        reservations=reservations,
        google_calendar_embed_url=current_app.config.get('GOOGLE_CALENDAR_EMBED_URL', ''),
        now=datetime.now(),
        selected_hours=selected_hours,
        chart_hours=chart_hours,
    )


@bp.route('/nodetype-history/<system>/<node_type>')
def nodetype_history(system, node_type):
    """
    Display historical trends for a specific node type (Casper only).

    Args:
        system: System name (casper)
        node_type: Node type name (e.g., 'gpu-a100', 'standard')
    """
    # Get time range from query params; 'hours' is primary, 'days' kept for backward compat
    if request.args.get('hours'):
        hours = int(request.args.get('hours'))
    elif request.args.get('days'):
        hours = int(request.args.get('days')) * 24
    else:
        hours = 168  # 7-day default
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours)

    session = db.session

    if system.lower() == 'casper':
        # Query Casper node type history
        history_data = status_queries.get_casper_nodetype_history(
            session, node_type, start_date, end_date
        )

        # Get latest record for current status
        latest_status = status_queries.get_latest_casper_nodetype_status(
            session, node_type
        )

    else:
        flash(f'System {system} not yet supported for node type history', 'warning')
        return redirect(url_for('status_dashboard.index'))

    # Generate chart
    chart_svg = generate_nodetype_history_matplotlib(history_data)

    return render_template(
        'dashboards/status/nodetype_history.html',
        user=current_user,
        system=system,
        node_type=node_type,
        latest_status=latest_status,
        history_data=history_data,
        chart_svg=chart_svg,
        hours=hours,
        start_date=start_date,
        end_date=end_date,
    )


@bp.route('/partition-history/<system>/<partition>')
def partition_history(system, partition):
    """
    Display historical trends for a specific system partition (CPU, GPU, or VIZ).

    Args:
        system: System name (derecho, casper)
        partition: Partition name ('cpu', 'gpu', or 'viz')
    """
    # Get time range from query params; 'hours' is primary, 'days' kept for backward compat
    if request.args.get('hours'):
        hours = int(request.args.get('hours'))
    elif request.args.get('days'):
        hours = int(request.args.get('days')) * 24
    else:
        hours = 168  # 7-day default
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours)

    session = db.session

    # Validate system
    if system.lower() not in ['derecho', 'casper']:
        flash(f'Unknown system: {system}', 'warning')
        return redirect(url_for('status_dashboard.index'))

    # Validate partition
    partition_lower = partition.lower()
    if partition_lower not in ['cpu', 'gpu', 'viz']:
        flash(f'Unknown partition: {partition}', 'warning')
        return redirect(url_for('status_dashboard.index'))

    # VIZ is only valid for Casper
    if partition_lower == 'viz' and system.lower() != 'casper':
        flash(f'VIZ partition is only available for Casper', 'warning')
        return redirect(url_for('status_dashboard.index'))

    # Query partition history using generic function
    history_data = status_queries.get_system_partition_history(
        session, system, partition, start_date, end_date
    )

    # Get latest status using generic function
    latest_status = status_queries.get_latest_system_partition_status(
        session, system, partition
    )

    # Format partition name for display
    partition_display = f"{partition.upper()} Partition"

    # Generate chart
    chart_svg = generate_nodetype_history_matplotlib(history_data)

    return render_template(
        'dashboards/status/nodetype_history.html',
        user=current_user,
        system=system,
        node_type=partition_display,
        partition=partition,
        is_partition=True,
        latest_status=latest_status,
        history_data=history_data,
        chart_svg=chart_svg,
        hours=hours,
        start_date=start_date,
        end_date=end_date,
    )


@bp.route('/queue-history/<system>/<queue_name>')
def queue_history(system, queue_name):
    """
    Display historical trends for a specific queue.

    Args:
        system: System name (casper, derecho)
        queue_name: Queue name (e.g., 'regular', 'gpu')
    """
    # Get time range from query params; 'hours' is primary, 'days' kept for backward compat
    if request.args.get('hours'):
        hours = int(request.args.get('hours'))
    elif request.args.get('days'):
        hours = int(request.args.get('days')) * 24
    else:
        hours = 168  # 7-day default
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours)

    session = db.session

    # Query queue history
    history_data = status_queries.get_queue_history(
        session, system, queue_name, start_date, end_date
    )

    # Get latest record for current status
    latest_status = status_queries.get_latest_queue_status(
        session, system, queue_name
    )

    # Generate chart
    chart_svg = generate_queue_history_matplotlib(history_data)

    # Per-user / per-project rollup table — only fetched and rendered for
    # operators with VIEW_SYSTEM_STATUS_USER_INFO. Skipping the query
    # entirely (vs. always fetching + hiding in template) avoids the join
    # cost for pages viewed by non-privileged users.
    from webapp.utils.rbac import has_permission
    can_view_user_info = (
        current_user.is_authenticated
        and has_permission(current_user, Permission.VIEW_SYSTEM_STATUS_USER_INFO)
    )
    user_proj_rows = []
    if can_view_user_info:
        user_proj_rows = status_queries.get_latest_user_proj_queue_snapshot(
            session, system=system, queue_name=queue_name
        )

    return render_template(
        'dashboards/status/queue_history.html',
        user=current_user,
        system=system,
        queue_name=queue_name,
        latest_status=latest_status,
        history_data=history_data,
        chart_svg=chart_svg,
        hours=hours,
        start_date=start_date,
        end_date=end_date,
        can_view_user_info=can_view_user_info,
        user_proj_rows=user_proj_rows,
    )


def _render_user_proj_chart(*, system, queue_name, endpoint_name, endpoint_kwargs):
    """Shared body for the queue-scoped and system-scoped chart endpoints.

    Reads selector params from ``request.args`` (``state``, ``metric``,
    ``group_by``, ``hours``), validates + clamps invalid combos, calls
    the aggregator (queue-scoped if ``queue_name`` set, else
    system-wide), and renders the chart partial. ``endpoint_name`` and
    ``endpoint_kwargs`` are passed to the partial so its selector
    buttons emit URLs against the right route.
    """
    valid_states = ('running', 'pending', 'held')
    valid_metrics = ('jobs', 'cores', 'gpus', 'nodes')
    valid_groups = ('user', 'project')
    valid_rank_by = ('current', 'peak')

    state = request.args.get('state', 'running')
    if state not in valid_states:
        state = 'running'
    metric = request.args.get('metric', 'jobs')
    if metric not in valid_metrics:
        metric = 'jobs'
    group_by = request.args.get('group_by', 'user')
    if group_by not in valid_groups:
        group_by = 'user'
    rank_by = request.args.get('rank_by', 'current')
    if rank_by not in valid_rank_by:
        rank_by = 'current'

    # `nodes` only exists for state='running' (no nodes_pending /
    # nodes_held columns in QueueRollupMetricsMixin). Clamp to cores
    # rather than rejecting — the UI does the same when toggling state.
    if metric == 'nodes' and state != 'running':
        metric = 'cores'

    if request.args.get('hours'):
        try:
            hours = int(request.args['hours'])
        except ValueError:
            hours = 168
    elif request.args.get('days'):
        try:
            hours = int(request.args['days']) * 24
        except ValueError:
            hours = 168
    else:
        hours = 168

    top_n = 15
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours)

    timeseries = status_queries.get_user_proj_timeseries(
        db.session,
        system=system,
        queue_name=queue_name,
        start_date=start_date,
        end_date=end_date,
        state=state,
        metric=metric,
        group_by=group_by,
        top_n=top_n,
        rank_by=rank_by,
    )

    # Clamp metric=gpus → cores when this scope+window has no GPU
    # activity. Mirrors the (state, metric) clamp above; protects
    # against direct URL access or stale selector state when the time
    # window scrolls past a previously-GPU-active period. Re-fetch so
    # the rendered chart matches the buttons we'll show.
    if metric == 'gpus' and not timeseries.get('has_gpus', False):
        metric = 'cores'
        timeseries = status_queries.get_user_proj_timeseries(
            db.session,
            system=system,
            queue_name=queue_name,
            start_date=start_date,
            end_date=end_date,
            state=state,
            metric=metric,
            group_by=group_by,
            top_n=top_n,
            rank_by=rank_by,
        )

    # group_by=user → username legend → user modal; group_by=project →
    # projcode legend → project modal. svg-legend-links.js dispatches.
    # Only operators (VIEW_SYSTEM_STATUS_USER_INFO) get clickable legend
    # entries — the user/project detail modal endpoints have their own
    # RBAC and would 403 a non-operator's click. Plain-text labels for
    # everyone else keeps the chart universally readable.
    from webapp.utils.rbac import has_permission
    can_link_legend = (
        current_user.is_authenticated
        and has_permission(current_user, Permission.VIEW_SYSTEM_STATUS_USER_INFO)
    )
    if can_link_legend:
        link_kind = 'user' if group_by == 'user' else 'project'
    else:
        link_kind = None
    chart_svg = generate_user_proj_stacked_area(
        timeseries, link_kind=link_kind, rank_by=rank_by,
    )

    # Two of these cards render on the landing page (one per system
    # tab), so the chart wrapper div needs a scope-unique id —
    # otherwise the second card's selector-button hx-target=#... lookup
    # finds the first card's div and swaps the wrong one.
    if queue_name is not None:
        chart_dom_id = f'upq-chart-{system}-{queue_name}'
    else:
        chart_dom_id = f'upq-chart-{system}'

    return render_template(
        'dashboards/status/partials/user_proj_chart.html',
        system=system,
        queue_name=queue_name,
        hours=hours,
        state=state,
        metric=metric,
        metric_label=timeseries.get('metric_label', metric),
        group_by=group_by,
        group_by_label=timeseries.get('group_by_label', group_by),
        rank_by=rank_by,
        top_n=top_n,
        chart_svg=chart_svg,
        endpoint_name=endpoint_name,
        endpoint_kwargs=endpoint_kwargs,
        chart_dom_id=chart_dom_id,
        has_gpus=timeseries.get('has_gpus', False),
    )


@bp.route('/htmx/queue-history/<system>/<queue_name>/user-proj-chart')
@login_required
def htmx_user_proj_chart(system, queue_name):
    """Render the user/project chart scoped to one queue.

    Used by selector buttons in the queue-history drill-down. Visible
    to any logged-in user; legend click-through is gated separately
    in ``_render_user_proj_chart`` on
    ``Permission.VIEW_SYSTEM_STATUS_USER_INFO``.
    """
    return _render_user_proj_chart(
        system=system,
        queue_name=queue_name,
        endpoint_name='status_dashboard.htmx_user_proj_chart',
        endpoint_kwargs={'system': system, 'queue_name': queue_name},
    )


@bp.route('/htmx/system/<system>/user-proj-chart')
@login_required
def htmx_user_proj_chart_system(system):
    """Render the user/project chart summed across all queues for ``system``.

    Used by the chart card on the system landing page (Derecho /
    Casper tabs of the status dashboard). Same selector behaviour as
    the per-queue endpoint, but the aggregator filters on system_id
    rather than queue_id. Visible to any logged-in user; legend
    click-through is gated separately in ``_render_user_proj_chart``
    on ``Permission.VIEW_SYSTEM_STATUS_USER_INFO``.
    """
    return _render_user_proj_chart(
        system=system,
        queue_name=None,
        endpoint_name='status_dashboard.htmx_user_proj_chart_system',
        endpoint_kwargs={'system': system},
    )


# ============================================================================
# htmx Routes — Outage Management
# ============================================================================

@bp.route('/htmx/outage', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def htmx_create_outage():
    """Create an outage and redirect back to the status page.

    Form datetime-local values are TZ-blind on the wire. The companion
    `tz` hidden field carries the operator's browser TZ (IANA name) so
    we can normalize the entered wall-clock time to naive-UTC for
    storage. `tz` falls back to the configured display TZ if missing
    (older clients, scripted POSTs)."""
    from system_status.models import SystemOutage
    from sam.fmt import naive_local_to_utc

    system_name = request.form.get('system_name', '').strip()
    title = request.form.get('title', '').strip()
    severity = request.form.get('severity', '').strip()

    if not system_name or not title or not severity:
        flash('System, title, and severity are required.', 'error')
        return redirect(url_for('status_dashboard.index'))

    operator_tz = request.form.get('tz', '').strip() or None

    outage = SystemOutage(
        system_name=system_name,
        title=title,
        severity=severity,
        component=request.form.get('component', '').strip() or None,
        description=request.form.get('description', '').strip() or None,
        status='investigating',
        start_time=datetime.now(),  # already UTC under TZ=UTC
    )

    start_time_str = request.form.get('start_time', '').strip()
    if start_time_str:
        try:
            outage.start_time = naive_local_to_utc(
                datetime.fromisoformat(start_time_str), operator_tz)
        except ValueError:
            pass

    est_res_str = request.form.get('estimated_resolution', '').strip()
    if est_res_str:
        try:
            outage.estimated_resolution = naive_local_to_utc(
                datetime.fromisoformat(est_res_str), operator_tz)
        except ValueError:
            pass

    db.session.add(outage)
    db.session.commit()

    flash('Outage reported.', 'warning')
    response = make_response('')
    response.headers['HX-Redirect'] = url_for('status_dashboard.index')
    return response


@bp.route('/htmx/outage/<int:outage_id>/edit', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def htmx_update_outage(outage_id):
    """Update an outage and redirect back to the status page."""
    from system_status.models import SystemOutage

    outage = db.session.query(SystemOutage).get(outage_id)
    if not outage:
        flash('Outage not found.', 'error')
        response = make_response('')
        response.headers['HX-Redirect'] = url_for('status_dashboard.index')
        return response

    valid_statuses = ['investigating', 'identified', 'monitoring', 'resolved']
    valid_severities = ['critical', 'major', 'minor', 'maintenance']

    title = request.form.get('title', '').strip()
    if title:
        outage.title = title
    status = request.form.get('status', '').strip()
    if status in valid_statuses:
        outage.status = status
    severity = request.form.get('severity', '').strip()
    if severity in valid_severities:
        outage.severity = severity

    outage.description = request.form.get('description', '').strip() or None

    # Naive-UTC conversion mirrors htmx_create_outage; see its docstring.
    from sam.fmt import naive_local_to_utc
    operator_tz = request.form.get('tz', '').strip() or None
    est_res_str = request.form.get('estimated_resolution', '').strip()
    if est_res_str:
        try:
            outage.estimated_resolution = naive_local_to_utc(
                datetime.fromisoformat(est_res_str), operator_tz)
        except ValueError:
            pass
    else:
        outage.estimated_resolution = None

    outage.updated_at = datetime.now()
    db.session.commit()

    flash('Outage updated.', 'success')
    response = make_response('')
    response.headers['HX-Redirect'] = url_for('status_dashboard.index')
    return response


@bp.route('/htmx/outage/<int:outage_id>/resolve', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def htmx_resolve_outage(outage_id):
    """Quick-resolve an outage and redirect."""
    from system_status.models import SystemOutage

    outage = db.session.query(SystemOutage).get(outage_id)
    if outage:
        outage.status = 'resolved'
        outage.updated_at = datetime.now()
        db.session.commit()
        flash('Outage resolved.', 'success')
    else:
        flash('Outage not found.', 'error')

    response = make_response('')
    response.headers['HX-Redirect'] = url_for('status_dashboard.index')
    return response


@bp.route('/htmx/outage/<int:outage_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def htmx_delete_outage(outage_id):
    """Delete an outage and redirect."""
    from system_status.models import SystemOutage

    outage = db.session.query(SystemOutage).get(outage_id)
    if outage:
        db.session.delete(outage)
        db.session.commit()
        flash('Outage deleted.', 'success')
    else:
        flash('Outage not found.', 'error')

    response = make_response('')
    response.headers['HX-Redirect'] = url_for('status_dashboard.index')
    return response

"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, make_response, current_app
from flask_login import login_required, current_user
from webapp.utils.rbac import require_permission, Permission
from datetime import datetime, timedelta
import sys
from pathlib import Path
import logging

from webapp.extensions import db
from ..charts import generate_nodetype_history_matplotlib, generate_queue_history_matplotlib

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import create_status_engine, get_session
from system_status import queries as status_queries

bp = Blueprint('status_dashboard', __name__, url_prefix='/status')
logger = logging.getLogger(__name__)

@bp.route('/')
def index():
    """
    Main system status dashboard landing page.

    Queries latest status from all systems and renders server-side.
    """
    engine, SessionLocal = create_status_engine()

    # Create session with expire_on_commit=False so objects remain accessible
    session = SessionLocal(expire_on_commit=False)
    try:
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
        )
    finally:
        session.close()


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

    engine, SessionLocal = create_status_engine()
    session = SessionLocal(expire_on_commit=False)

    try:
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
        chart_svg = generate_nodetype_history_matplotlib(history_data, node_type)

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

    finally:
        session.close()


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

    engine, SessionLocal = create_status_engine()
    session = SessionLocal(expire_on_commit=False)

    try:
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
        chart_svg = generate_nodetype_history_matplotlib(history_data, partition_display)

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

    finally:
        session.close()


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

    engine, SessionLocal = create_status_engine()
    session = SessionLocal(expire_on_commit=False)

    try:
        # Query queue history
        history_data = status_queries.get_queue_history(
            session, system, queue_name, start_date, end_date
        )

        # Get latest record for current status
        latest_status = status_queries.get_latest_queue_status(
            session, system, queue_name
        )

        # Generate chart
        chart_svg = generate_queue_history_matplotlib(history_data, queue_name, system)

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
        )

    finally:
        session.close()


# ============================================================================
# htmx Routes — Outage Management
# ============================================================================

@bp.route('/htmx/outage', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def htmx_create_outage():
    """Create an outage and redirect back to the status page."""
    from system_status.models import SystemOutage

    system_name = request.form.get('system_name', '').strip()
    title = request.form.get('title', '').strip()
    severity = request.form.get('severity', '').strip()

    if not system_name or not title or not severity:
        flash('System, title, and severity are required.', 'error')
        return redirect(url_for('status_dashboard.index'))

    outage = SystemOutage(
        system_name=system_name,
        title=title,
        severity=severity,
        component=request.form.get('component', '').strip() or None,
        description=request.form.get('description', '').strip() or None,
        status='investigating',
        start_time=datetime.now(),
    )

    start_time_str = request.form.get('start_time', '').strip()
    if start_time_str:
        try:
            outage.start_time = datetime.fromisoformat(start_time_str)
        except ValueError:
            pass

    est_res_str = request.form.get('estimated_resolution', '').strip()
    if est_res_str:
        try:
            outage.estimated_resolution = datetime.fromisoformat(est_res_str)
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

    est_res_str = request.form.get('estimated_resolution', '').strip()
    if est_res_str:
        try:
            outage.estimated_resolution = datetime.fromisoformat(est_res_str)
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

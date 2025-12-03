"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import sys
from pathlib import Path

from webapp.extensions import db
from webapp.utils.charts import generate_nodetype_history_matplotlib, generate_queue_history_matplotlib

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    JupyterHubStatus,
    FilesystemStatus,
    SystemOutage, ResourceReservation,
    LoginNodeStatus, QueueStatus
)

bp = Blueprint('status_dashboard', __name__, url_prefix='/status')


@bp.route('/')
@login_required
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
        derecho_status = session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        derecho_queues = []
        derecho_filesystems = []
        derecho_login_nodes = []
        if derecho_status:
            derecho_queues = session.query(QueueStatus).filter_by(
                timestamp=derecho_status.timestamp,
                system_name='derecho'
            ).all()
            derecho_filesystems = session.query(FilesystemStatus).filter_by(
                timestamp=derecho_status.timestamp,
                system_name='derecho'
            ).all()
            derecho_login_nodes = session.query(LoginNodeStatus).filter_by(
                timestamp=derecho_status.timestamp,
                system_name='derecho'
            ).all()

        # Get latest Casper status
        casper_status = session.query(CasperStatus).order_by(
            CasperStatus.timestamp.desc()
        ).first()

        casper_node_types = []
        casper_queues = []
        casper_login_nodes = []
        casper_filesystems = []
        if casper_status:
            casper_node_types = session.query(CasperNodeTypeStatus).filter_by(
                timestamp=casper_status.timestamp
            ).all()
            casper_queues = session.query(QueueStatus).filter_by(
                timestamp=casper_status.timestamp,
                system_name='casper'
            ).all()
            casper_login_nodes = session.query(LoginNodeStatus).filter_by(
                timestamp=casper_status.timestamp,
                system_name='casper'
            ).all()
            casper_filesystems = session.query(FilesystemStatus).filter_by(
                timestamp=casper_status.timestamp,
                system_name='casper'
            ).all()

        # Get latest JupyterHub status
        jupyterhub_status = session.query(JupyterHubStatus).order_by(
            JupyterHubStatus.timestamp.desc()
        ).first()

        # Get active outages
        outages = session.query(SystemOutage).filter(
            SystemOutage.status != 'resolved'
        ).order_by(SystemOutage.start_time.desc()).all()

        # Get upcoming reservations
        reservations = session.query(ResourceReservation).filter(
            ResourceReservation.end_time >= datetime.now()
        ).order_by(ResourceReservation.start_time).all()

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
            now=datetime.now(),
        )
    finally:
        session.close()


@bp.route('/nodetype-history/<system>/<node_type>')
@login_required
def nodetype_history(system, node_type):
    """
    Display historical trends for a specific node type.

    Args:
        system: System name (casper, derecho)
        node_type: Node type name (e.g., 'gpu-a100', 'standard')
    """
    # Get date range from query params (default: last 7 days)
    days = int(request.args.get('days', 7))
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    engine, SessionLocal = create_status_engine()
    session = SessionLocal(expire_on_commit=False)

    try:
        if system.lower() == 'casper':
            # Query Casper node type history
            history_records = session.query(CasperNodeTypeStatus).filter(
                CasperNodeTypeStatus.node_type == node_type,
                CasperNodeTypeStatus.timestamp >= start_date,
                CasperNodeTypeStatus.timestamp <= end_date
            ).order_by(CasperNodeTypeStatus.timestamp).all()

            # Convert to dictionaries
            history_data = [
                {
                    'timestamp': record.timestamp,
                    'nodes_total': record.nodes_total,
                    'nodes_available': record.nodes_available,
                    'nodes_down': record.nodes_down,
                    'nodes_allocated': record.nodes_allocated,
                    'utilization_percent': record.utilization_percent,
                    'memory_utilization_percent': record.memory_utilization_percent,
                }
                for record in history_records
            ]

            # Get latest record for current status
            latest_status = session.query(CasperNodeTypeStatus).filter(
                CasperNodeTypeStatus.node_type == node_type
            ).order_by(CasperNodeTypeStatus.timestamp.desc()).first()

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
            days=days,
            start_date=start_date,
            end_date=end_date,
        )

    finally:
        session.close()


@bp.route('/queue-history/<system>/<queue_name>')
@login_required
def queue_history(system, queue_name):
    """
    Display historical trends for a specific queue.

    Args:
        system: System name (casper, derecho)
        queue_name: Queue name (e.g., 'regular', 'gpu')
    """
    # Get date range from query params (default: last 7 days)
    days = int(request.args.get('days', 7))
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    engine, SessionLocal = create_status_engine()
    session = SessionLocal(expire_on_commit=False)

    try:
        # Query queue history
        history_records = session.query(QueueStatus).filter(
            QueueStatus.queue_name == queue_name,
            QueueStatus.system_name == system,
            QueueStatus.timestamp >= start_date,
            QueueStatus.timestamp <= end_date
        ).order_by(QueueStatus.timestamp).all()

        # Convert to dictionaries
        history_data = [
            {
                'timestamp': record.timestamp,
                'running_jobs': record.running_jobs,
                'pending_jobs': record.pending_jobs,
                'held_jobs': record.held_jobs,
                'active_users': record.active_users,
                'cores_allocated': record.cores_allocated,
                'cores_pending': record.cores_pending,
                'gpus_allocated': record.gpus_allocated,
                'gpus_pending': record.gpus_pending,
            }
            for record in history_records
        ]

        # Get latest record for current status
        latest_status = session.query(QueueStatus).filter(
            QueueStatus.queue_name == queue_name,
            QueueStatus.system_name == system
        ).order_by(QueueStatus.timestamp.desc()).first()

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
            days=days,
            start_date=start_date,
            end_date=end_date,
        )

    finally:
        session.close()

"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import sys
from pathlib import Path
import os
import logging

from webapp.extensions import db
from ..charts import generate_nodetype_history_matplotlib, generate_queue_history_matplotlib

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import create_status_engine, get_session
from system_status import queries as status_queries

# Import JupyterHub client for real-time statistics
from webapp.clients import JupyterHubClient
from webapp.clients.jupyterhub import (
    JupyterHubAPIError,
    JupyterHubAuthError,
    JupyterHubConnectionError
)

bp = Blueprint('status_dashboard', __name__, url_prefix='/status')
logger = logging.getLogger(__name__)


# JupyterHub client singleton
_jupyterhub_client = None


def get_jupyterhub_client():
    """Get or create JupyterHub client singleton."""
    global _jupyterhub_client
    if _jupyterhub_client is None:
        base_url = os.getenv('JUPYTERHUB_API_URL', 'https://jupyterhub.hpc.ucar.edu')
        cache_ttl = int(os.getenv('JUPYTERHUB_CACHE_TTL', '300'))
        try:
            _jupyterhub_client = JupyterHubClient(
                base_url=base_url,
                instance='stable',
                cache_ttl=cache_ttl
            )
        except JupyterHubAuthError as e:
            logger.warning(f"Failed to initialize JupyterHub client: {str(e)}")
            return None
    return _jupyterhub_client


def get_realtime_jupyterhub_status(session):
    """
    Fetch real-time JupyterHub statistics from the API.

    Combines real-time user/session stats from JupyterHub API with
    node data from the database (if available).

    Args:
        session: SQLAlchemy session for database queries

    Returns:
        Object with JupyterHub status data, or None if unavailable
    """
    client = get_jupyterhub_client()
    if not client:
        # Fallback to database if client unavailable
        return status_queries.get_latest_jupyterhub_status(session)

    try:
        # Fetch real-time statistics from JupyterHub API
        stats = client.get_statistics(use_cache=True)

        # Fetch node data from database (if available)
        db_status = status_queries.get_latest_jupyterhub_status(session)
        nodes = db_status.nodes if db_status and db_status.nodes else []

        # Calculate node counts from node data
        nodes_free = 0
        nodes_busy = 0
        nodes_down = 0
        if nodes:
            for node in nodes:
                state = node.get('state', '').lower()
                if state == 'free':
                    nodes_free += 1
                elif 'busy' in state:
                    nodes_busy += 1
                elif 'down' in state:
                    nodes_down += 1

        # Create status object matching template expectations
        class JupyterHubStatusView:
            """View object for JupyterHub status display."""
            def __init__(self, stats_data, nodes_data):
                self.available = True  # API call succeeded
                self.timestamp = datetime.now()
                self.active_users = stats_data.get('active_users', 0)
                self.active_sessions = stats_data.get('active_sessions', 0)
                self.casper_login_jobs = stats_data.get('casper_login_jobs', 0)
                self.casper_batch_jobs = stats_data.get('casper_batch_jobs', 0)
                self.derecho_batch_jobs = stats_data.get('derecho_batch_jobs', 0)
                self.jobs_suspended = stats_data.get('broken_jobs', 0)
                self.nodes = nodes_data
                self.nodes_free = nodes_free
                self.nodes_busy = nodes_busy
                self.nodes_down = nodes_down

        return JupyterHubStatusView(stats, nodes)

    except (JupyterHubConnectionError, JupyterHubAPIError) as e:
        logger.warning(f"Failed to fetch real-time JupyterHub stats: {str(e)}")
        # Fallback to database
        return status_queries.get_latest_jupyterhub_status(session)


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

        # Get real-time JupyterHub status (combines API stats with DB node data)
        jupyterhub_status = get_realtime_jupyterhub_status(session)

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
            days=days,
            start_date=start_date,
            end_date=end_date,
        )

    finally:
        session.close()

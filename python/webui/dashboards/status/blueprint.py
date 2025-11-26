"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import sys
from pathlib import Path

from webui.extensions import db

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus, DerechoQueueStatus,
    CasperStatus, CasperNodeTypeStatus, CasperQueueStatus,
    JupyterHubStatus,
    FilesystemStatus,
    SystemOutage, ResourceReservation,
    DerechoLoginNodeStatus, CasperLoginNodeStatus
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
            derecho_queues = session.query(DerechoQueueStatus).filter_by(
                timestamp=derecho_status.timestamp
            ).all()
            derecho_filesystems = session.query(FilesystemStatus).filter_by(
                timestamp=derecho_status.timestamp,
                system_name='derecho'
            ).all()
            derecho_login_nodes = session.query(DerechoLoginNodeStatus).filter_by(
                timestamp=derecho_status.timestamp
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
            casper_queues = session.query(CasperQueueStatus).filter_by(
                timestamp=casper_status.timestamp
            ).all()
            casper_login_nodes = session.query(CasperLoginNodeStatus).filter_by(
                timestamp=casper_status.timestamp
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

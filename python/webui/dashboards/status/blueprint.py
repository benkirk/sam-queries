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
    DerechoStatus, DerechoQueueStatus, DerechoFilesystemStatus,
    CasperStatus, CasperNodeTypeStatus, CasperQueueStatus,
    JupyterHubStatus,
    SystemOutage, ResourceReservation
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

    with get_session(SessionLocal) as session:
        # Get latest Derecho status
        derecho_status = session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        derecho_queues = []
        derecho_filesystems = []
        if derecho_status:
            derecho_queues = session.query(DerechoQueueStatus).filter_by(
                timestamp=derecho_status.timestamp
            ).all()
            derecho_filesystems = session.query(DerechoFilesystemStatus).filter_by(
                timestamp=derecho_status.timestamp
            ).all()

        # Get latest Casper status
        casper_status = session.query(CasperStatus).order_by(
            CasperStatus.timestamp.desc()
        ).first()

        casper_node_types = []
        casper_queues = []
        if casper_status:
            casper_node_types = session.query(CasperNodeTypeStatus).filter_by(
                timestamp=casper_status.timestamp
            ).all()
            casper_queues = session.query(CasperQueueStatus).filter_by(
                timestamp=casper_status.timestamp
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
        casper_status=casper_status,
        casper_node_types=casper_node_types,
        casper_queues=casper_queues,
        jupyterhub_status=jupyterhub_status,
        outages=outages,
        reservations=reservations,
        now=datetime.now(),
    )

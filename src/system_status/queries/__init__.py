"""
System Status Query Helpers.

Centralized queries for the system status dashboard.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import desc

from system_status.models import (
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    JupyterHubStatus,
    FilesystemStatus,
    SystemOutage, ResourceReservation,
    LoginNodeStatus, QueueStatus
)


def get_latest_derecho_status(session: Session) -> Optional[DerechoStatus]:
    """Get the latest Derecho system status."""
    return session.query(DerechoStatus).order_by(
        DerechoStatus.timestamp.desc()
    ).first()


def get_latest_derecho_queues(session: Session, timestamp: datetime) -> List[QueueStatus]:
    """Get Derecho queue status for a specific timestamp."""
    return session.query(QueueStatus).filter_by(
        timestamp=timestamp,
        system_name='derecho'
    ).all()


def get_latest_derecho_filesystems(session: Session, timestamp: datetime) -> List[FilesystemStatus]:
    """Get Derecho filesystem status for a specific timestamp."""
    return session.query(FilesystemStatus).filter_by(
        timestamp=timestamp,
        system_name='derecho'
    ).all()


def get_latest_derecho_login_nodes(session: Session, timestamp: datetime) -> List[LoginNodeStatus]:
    """Get Derecho login node status for a specific timestamp."""
    return session.query(LoginNodeStatus).filter_by(
        timestamp=timestamp,
        system_name='derecho'
    ).all()


def get_latest_casper_status(session: Session) -> Optional[CasperStatus]:
    """Get the latest Casper system status."""
    return session.query(CasperStatus).order_by(
        CasperStatus.timestamp.desc()
    ).first()


def get_latest_casper_node_types(session: Session, timestamp: datetime) -> List[CasperNodeTypeStatus]:
    """Get Casper node type status for a specific timestamp."""
    return session.query(CasperNodeTypeStatus).filter_by(
        timestamp=timestamp
    ).all()


def get_latest_casper_queues(session: Session, timestamp: datetime) -> List[QueueStatus]:
    """Get Casper queue status for a specific timestamp."""
    return session.query(QueueStatus).filter_by(
        timestamp=timestamp,
        system_name='casper'
    ).all()


def get_latest_casper_login_nodes(session: Session, timestamp: datetime) -> List[LoginNodeStatus]:
    """Get Casper login node status for a specific timestamp."""
    return session.query(LoginNodeStatus).filter_by(
        timestamp=timestamp,
        system_name='casper'
    ).all()


def get_latest_casper_filesystems(session: Session, timestamp: datetime) -> List[FilesystemStatus]:
    """Get Casper filesystem status for a specific timestamp."""
    return session.query(FilesystemStatus).filter_by(
        timestamp=timestamp,
        system_name='casper'
    ).all()


def get_latest_jupyterhub_status(session: Session) -> Optional[JupyterHubStatus]:
    """Get the latest JupyterHub status."""
    return session.query(JupyterHubStatus).order_by(
        JupyterHubStatus.timestamp.desc()
    ).first()


def get_active_outages(session: Session) -> List[SystemOutage]:
    """Get all active system outages."""
    return session.query(SystemOutage).filter(
        SystemOutage.status != 'resolved'
    ).order_by(SystemOutage.start_time.desc()).all()


def get_upcoming_reservations(session: Session) -> List[ResourceReservation]:
    """Get upcoming resource reservations."""
    return session.query(ResourceReservation).filter(
        ResourceReservation.end_time >= datetime.now()
    ).order_by(ResourceReservation.start_time).all()


def get_casper_nodetype_history(
    session: Session,
    node_type: str,
    start_date: datetime,
    end_date: datetime
) -> List[Dict[str, Any]]:
    """
    Get historical data for a specific Casper node type.
    Returns a list of dictionaries suitable for charting.
    """
    history_records = session.query(CasperNodeTypeStatus).filter(
        CasperNodeTypeStatus.node_type == node_type,
        CasperNodeTypeStatus.timestamp >= start_date,
        CasperNodeTypeStatus.timestamp <= end_date
    ).order_by(CasperNodeTypeStatus.timestamp).all()

    return [
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


def get_latest_casper_nodetype_status(session: Session, node_type: str) -> Optional[CasperNodeTypeStatus]:
    """Get the latest status for a specific Casper node type."""
    return session.query(CasperNodeTypeStatus).filter(
        CasperNodeTypeStatus.node_type == node_type
    ).order_by(CasperNodeTypeStatus.timestamp.desc()).first()


def get_queue_history(
    session: Session,
    system: str,
    queue_name: str,
    start_date: datetime,
    end_date: datetime
) -> List[Dict[str, Any]]:
    """
    Get historical data for a specific queue.
    Returns a list of dictionaries suitable for charting.
    """
    history_records = session.query(QueueStatus).filter(
        QueueStatus.queue_name == queue_name,
        QueueStatus.system_name == system,
        QueueStatus.timestamp >= start_date,
        QueueStatus.timestamp <= end_date
    ).order_by(QueueStatus.timestamp).all()

    return [
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


def get_latest_queue_status(session: Session, system: str, queue_name: str) -> Optional[QueueStatus]:
    """Get the latest status for a specific queue."""
    return session.query(QueueStatus).filter(
        QueueStatus.queue_name == queue_name,
        QueueStatus.system_name == system
    ).order_by(QueueStatus.timestamp.desc()).first()
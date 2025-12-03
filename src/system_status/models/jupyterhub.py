#-------------------------------------------------------------------------bh-
# JupyterHub Status Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, Float, Boolean, Index, JSON
from ..base import StatusBase, StatusSnapshotMixin, SessionMixin


class JupyterHubStatus(StatusBase, StatusSnapshotMixin, SessionMixin):
    """
    JupyterHub metrics (5-minute intervals).
    Collects data from jhlnodes, find_zombie_jobs, and jhstat commands.
    """
    __bind_key__ = "system_status" # <-- database for connection, if not default
    __tablename__ = 'jupyterhub_status'

    __table_args__ = (
        Index('ix_jupyterhub_status_created_at', 'created_at'),
    )

    status_id = Column(Integer, primary_key=True, autoincrement=True)

    # Basic Metrics
    available = Column(Boolean, nullable=False, default=True)
    active_users = Column(Integer, nullable=False, default=0)
    active_sessions = Column(Integer, nullable=False, default=0)

    # Node Metrics
    nodes_total = Column(Integer, nullable=True, default=0)
    nodes_free = Column(Integer, nullable=True, default=0)
    nodes_busy = Column(Integer, nullable=True, default=0)
    nodes_down = Column(Integer, nullable=True, default=0)

    # CPU Metrics
    cpus_total = Column(Integer, nullable=True, default=0)
    cpus_free = Column(Integer, nullable=True, default=0)
    cpus_used = Column(Integer, nullable=True, default=0)
    cpu_utilization_percent = Column(Float, nullable=True)

    # GPU Metrics
    gpus_total = Column(Integer, nullable=True, default=0)
    gpus_free = Column(Integer, nullable=True, default=0)
    gpus_used = Column(Integer, nullable=True, default=0)
    gpu_utilization_percent = Column(Float, nullable=True)

    # Memory Metrics
    memory_total_gb = Column(Float, nullable=True, default=0.0)
    memory_free_gb = Column(Float, nullable=True, default=0.0)
    memory_used_gb = Column(Float, nullable=True, default=0.0)
    memory_utilization_percent = Column(Float, nullable=True)

    # Job Metrics
    jobs_running = Column(Integer, nullable=True, default=0)
    casper_login_jobs = Column(Integer, nullable=True, default=0)
    casper_batch_jobs = Column(Integer, nullable=True, default=0)
    derecho_batch_jobs = Column(Integer, nullable=True, default=0)
    jobs_suspended = Column(Integer, nullable=True, default=0)

    # Individual Node Details (JSON array)
    nodes = Column(JSON, nullable=True)  # List of node detail dicts

#-------------------------------------------------------------------------bh-
# System Status Common Models
#-------------------------------------------------------------------------eh-

from sqlalchemy import Column, Integer, Float

class SystemStatusMixin:
    """
    A mixin for common system status metrics shared by DerechoStatus and CasperStatus.
    
    This provides a standard set of columns for compute node counts, CPU/GPU
    utilization, memory usage, and job statistics.
    """
    # Compute Nodes - CPU Partition
    cpu_nodes_total = Column(Integer, nullable=False)
    cpu_nodes_available = Column(Integer, nullable=False)
    cpu_nodes_down = Column(Integer, nullable=False, default=0)
    cpu_nodes_reserved = Column(Integer, nullable=False, default=0)

    # Compute Nodes - GPU Partition
    gpu_nodes_total = Column(Integer, nullable=False)
    gpu_nodes_available = Column(Integer, nullable=False)
    gpu_nodes_down = Column(Integer, nullable=False, default=0)
    gpu_nodes_reserved = Column(Integer, nullable=False, default=0)

    # CPU Utilization
    cpu_cores_total = Column(Integer, nullable=False)
    cpu_cores_allocated = Column(Integer, nullable=False)
    cpu_cores_idle = Column(Integer, nullable=False)
    cpu_utilization_percent = Column(Float, nullable=True)

    # GPU Utilization
    gpu_count_total = Column(Integer, nullable=False)
    gpu_count_allocated = Column(Integer, nullable=False)
    gpu_count_idle = Column(Integer, nullable=False)
    gpu_utilization_percent = Column(Float, nullable=True)

    # Memory Utilization
    memory_total_gb = Column(Float, nullable=False)
    memory_allocated_gb = Column(Float, nullable=False)
    memory_utilization_percent = Column(Float, nullable=True)

    # Jobs
    running_jobs = Column(Integer, nullable=False, default=0)
    pending_jobs = Column(Integer, nullable=False, default=0)
    held_jobs = Column(Integer, nullable=False, default=0)
    active_users = Column(Integer, nullable=False, default=0)

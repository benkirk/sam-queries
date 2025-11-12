"""
XRAS Integration Views

These are READ-ONLY database views that provide external reporting interfaces
for the XRAS (eXternal Resource Allocation System).

IMPORTANT: These are views, not tables:
- No primary key constraints
- No foreign key relationships
- No write operations (INSERT/UPDATE/DELETE)
- Column names match view definitions exactly (camelCase from views)
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Text
from sqlalchemy.ext.declarative import declarative_base

from ..base import Base

# ============================================================================
# XRAS Views
# ============================================================================

class XrasUserView(Base):
    """
    XRAS User Profile View - Read-only

    Provides user profile information for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_user'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    # Using username as primary key for ORM purposes (views need PK)
    username = Column(String(35), primary_key=True)
    firstName = Column(String(50))
    middleName = Column(String(40))
    lastName = Column(String(50))
    phone = Column(String(50))
    organization = Column(String(80))
    email = Column(String(255))
    academicStatus = Column(String(100))

    def __repr__(self):
        return f"<XrasUserView(username='{self.username}', email='{self.email}')>"


class XrasRoleView(Base):
    """
    XRAS Role View - Read-only

    Shows user roles on projects for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_role'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    # Composite primary key (views need PK for ORM)
    projectId = Column(String(30), primary_key=True)
    username = Column(String(35), primary_key=True)
    role = Column(String(17), nullable=False)

    def __repr__(self):
        return f"<XrasRoleView(project='{self.projectId}', user='{self.username}', role='{self.role}')>"


class XrasActionView(Base):
    """
    XRAS Action View - Read-only

    Shows allocation actions/transactions for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_action'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    # Using allocationId as primary key (though it may not be unique in view)
    allocationId = Column(Integer, primary_key=True)
    projectId = Column(String(30), nullable=False)
    actionType = Column(String(12))
    amount = Column(Float(15))  # float(15,2) in DB
    endDate = Column(DateTime)
    dateApplied = Column(DateTime, nullable=False)

    def __repr__(self):
        return f"<XrasActionView(allocation={self.allocationId}, project='{self.projectId}', action='{self.actionType}')>"


class XrasAllocationView(Base):
    """
    XRAS Allocation View - Read-only

    Shows allocation summary information for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_allocation'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    allocationId = Column(Integer, primary_key=True)
    projectId = Column(String(30), nullable=False)
    allocationBeginDate = Column(DateTime, nullable=False)
    allocationEndDate = Column(DateTime)
    allocatedAmount = Column(Float(15))  # float(15,2) in DB
    remainingAmount = Column(Float(25))  # double(25,8) in DB
    resourceRepositoryKey = Column(Integer)

    def __repr__(self):
        return f"<XrasAllocationView(id={self.allocationId}, project='{self.projectId}', amount={self.allocatedAmount})>"


class XrasHpcAllocationAmountView(Base):
    """
    XRAS HPC Allocation Amount View - Read-only

    Shows HPC allocation usage by allocation for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_hpc_allocation_amount'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    allocation_id = Column(Integer, primary_key=True)
    allocated = Column(Float(15))  # float(15,2) in DB
    used = Column(Float(22))  # double(22,8) in DB
    remaining = Column(Float(22))  # double(22,8) in DB

    def __repr__(self):
        return f"<XrasHpcAllocationAmountView(allocation={self.allocation_id}, allocated={self.allocated}, used={self.used})>"


class XrasRequestView(Base):
    """
    XRAS Request View - Read-only

    Shows allocation request information for XRAS external reporting.
    This is a database VIEW, not a table.
    """
    __tablename__ = 'xras_request'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    # Using projectId as primary key (may not be truly unique)
    projectId = Column(String(30), primary_key=True)
    requestBeginDate = Column(Date)
    requestEndDate = Column(Date)
    allocationIds = Column(Text)  # Comma-separated list
    allocationType = Column(String(20), nullable=False)
    projectTitle = Column(String(255), nullable=False)
    xrasFosTypeId = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<XrasRequestView(project='{self.projectId}', type='{self.allocationType}')>"


# ============================================================================
# Computational Activity Charge View
# ============================================================================

class CompActivityChargeView(Base):
    """
    Computational Activity Charge View - Read-only

    Denormalized view combining computational activity and charge information
    for reporting purposes. This is a database VIEW, not a table.

    Note: This view may have duplicate job_ids if charges were adjusted,
    so it's not suitable for using job_id as a unique key.
    """
    __tablename__ = 'comp_activity_charge'
    __table_args__ = {'info': dict(is_view=True), 'extend_existing': True}

    # Composite key: job_idx + util_idx should be unique
    job_idx = Column(Integer, primary_key=True)
    util_idx = Column(Integer, primary_key=True)

    # User and project info
    unix_uid = Column(Integer)
    username = Column(String(35))
    projcode = Column(String(30), nullable=False)

    # Job identification
    job_id = Column(String(35), nullable=False)
    job_name = Column(String(255))

    # Resource info
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)

    # Timing (epoch timestamps)
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    submit_time = Column(Integer, nullable=False)

    # Resource usage
    unix_user_time = Column(Float)  # double in DB
    unix_system_time = Column(Float)  # double in DB
    queue_wait_time = Column(Integer, nullable=False)
    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    wall_time = Column(Float)  # double in DB

    # Job metadata
    cos = Column(Integer)  # Class of service
    exit_status = Column(String(20))
    interactive = Column(Integer)  # boolean as int
    processing_status = Column(Integer)  # bit(1) as int
    error_comment = Column(Text)

    # Dates
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)
    charge_date = Column(DateTime)

    # Charges
    external_charge = Column(Float(22))  # float(22,8) in DB
    core_hours = Column(Float(22))  # float(22,8) in DB
    charge = Column(Float(22))  # float(22,8) in DB

    def __repr__(self):
        return f"<CompActivityChargeView(job='{self.job_id}', user='{self.username}', project='{self.projcode}', charge={self.charge})>"


# ============================================================================
# Export all view classes
# ============================================================================

__all__ = [
    'XrasUserView',
    'XrasRoleView',
    'XrasActionView',
    'XrasAllocationView',
    'XrasHpcAllocationAmountView',
    'XrasRequestView',
    'CompActivityChargeView',
]

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
    __table_args__ = {'info': dict(is_view=True)}

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
    __table_args__ = {'info': dict(is_view=True)}

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
    __table_args__ = {'info': dict(is_view=True)}

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
    __table_args__ = {'info': dict(is_view=True)}

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
    __table_args__ = {'info': dict(is_view=True)}

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
    __table_args__ = {'info': dict(is_view=True)}

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
# Export all view classes
# ============================================================================

__all__ = [
    'XrasUserView',
    'XrasRoleView',
    'XrasActionView',
    'XrasAllocationView',
    'XrasHpcAllocationAmountView',
    'XrasRequestView',
]

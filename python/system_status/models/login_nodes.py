"""
Login Node Status Models

Tracks individual login node status for Derecho and Casper systems.
Each login node records availability, user load, and system metrics.
"""

from sqlalchemy import Column, Integer, String, Float, Enum
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin


class LoginNodeStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """
    Individual login node status.

    Each node tracks availability, user count, and load metrics.

    Fields:
        login_node_id: Auto-increment primary key
        timestamp: Links to parent {derecho,casper}_status snapshot (via StatusSnapshotMixin)
        node_name: Login node hostname (e.g., 'derecho1', 'derecho2')
        node_type: Node type - 'cpu', 'gpu', or 'data-access'
        available: Boolean flag - is node accepting logins? (via AvailabilityMixin)
        degraded: Boolean flag - is node degraded but still available? (via AvailabilityMixin)
        user_count: Current number of logged-in users
        load_1min: 1-minute load average
        load_5min: 5-minute load average
        load_15min: 15-minute load average

    Example:
        >>> node = LoginNodeStatus(
        ...     timestamp=datetime.now(),
        ...     node_name='derecho1',
        ...     node_type='cpu',
        ...     system_name='derecho',
        ...     available=True,
        ...     degraded=False,
        ...     user_count=42,
        ...     load_1min=3.5,
        ...     load_5min=3.2,
        ...     load_15min=3.0
        ... )
    """
    __tablename__ = 'login_node_status'

    # Primary key
    login_node_id = Column(Integer, primary_key=True, autoincrement=True)

    # Login node identification
    node_name = Column(String(50), nullable=False, index=True,
                      comment='Login node hostname (e.g., derecho1, derecho2)')
    node_type = Column(Enum('cpu', 'gpu', 'data-access', name='login_node_type'),
                      nullable=False,
                      comment='Node type - CPU or GPU enabled')
    system_name = Column(String(32), nullable=False, index=True,
                         comment='System to which ths queue belongs (derecho, casper, etc.)')

    # User and load metrics
    user_count = Column(Integer, nullable=True,
                       comment='Current number of logged-in users')
    load_1min = Column(Float, nullable=True,
                      comment='1-minute load average')
    load_5min = Column(Float, nullable=True,
                      comment='5-minute load average')
    load_15min = Column(Float, nullable=True,
                       comment='15-minute load average')

    # Note: available, degraded inherited from AvailabilityMixin
    # Note: timestamp, created_at inherited from StatusSnapshotMixin
    # Note: session, get(), get_all() inherited from SessionMixin

    def __repr__(self):
        return (f"<LoginNodeStatus(node_name='{self.node_name}', "
                f"system='{self.system_name}', "
                f"type='{self.node_type}', available={self.available}, "
                f"users={self.user_count}, load_1min={self.load_1min})>")

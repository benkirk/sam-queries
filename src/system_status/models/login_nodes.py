"""
Login Node Status Models

Tracks individual login node status for Derecho and Casper systems.
Each login node records availability, user load, and system metrics.
"""

from sqlalchemy import Column, Integer, Float, ForeignKey
from sqlalchemy.orm import relationship
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin
from .lookups import System, LoginNodeDef


class LoginNodeStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """
    Individual login node status snapshot.

    Phase 2 (PR-A): legacy text columns ``node_name`` / ``system_name`` and
    the ``node_type`` enum are lifted into the ``login_nodes`` definition
    table (one row per (system, hostname) pair, carrying ``node_type``).
    The snapshot row now references the definition via
    ``login_node_def_id`` and the system via ``system_id``. Property
    accessors below preserve the legacy attribute interface.
    """
    __bind_key__ = "system_status"
    __tablename__ = 'login_node_status'

    # Primary key
    login_node_id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys to parent status records (nullable - links to one or the other)
    derecho_status_id = Column(Integer, ForeignKey('derecho_status.status_id', ondelete='CASCADE'),
                               nullable=True, index=True,
                               comment='FK to parent Derecho status snapshot')
    casper_status_id = Column(Integer, ForeignKey('casper_status.status_id', ondelete='CASCADE'),
                              nullable=True, index=True,
                              comment='FK to parent Casper status snapshot')

    # Lookup FKs (Phase 2)
    system_id = Column(Integer, ForeignKey('systems.system_id'),
                       nullable=False, index=True)
    login_node_def_id = Column(Integer, ForeignKey('login_nodes.login_node_def_id'),
                               nullable=False, index=True,
                               comment='FK to login_nodes definition (system+name+type)')

    # User and load metrics
    user_count = Column(Integer, nullable=True,
                       comment='Current number of logged-in users')
    num_cpus = Column(Integer, nullable=True,
                     comment='Total CPU count on node (from nproc --all); used to compute load percentages')
    load_1min = Column(Float, nullable=True,
                      comment='1-minute load average as % of CPU capacity (raw_load / num_cpus * 100)')
    load_5min = Column(Float, nullable=True,
                      comment='5-minute load average as % of CPU capacity (raw_load / num_cpus * 100)')
    load_15min = Column(Float, nullable=True,
                       comment='15-minute load average as % of CPU capacity (raw_load / num_cpus * 100)')

    # Note: available, degraded inherited from AvailabilityMixin
    # Note: timestamp, created_at inherited from StatusSnapshotMixin

    # Relationships
    derecho_status = relationship('DerechoStatus', back_populates='login_nodes',
                                  foreign_keys=[derecho_status_id])
    casper_status = relationship('CasperStatus', back_populates='login_nodes',
                                foreign_keys=[casper_status_id])
    system = relationship(System, foreign_keys=[system_id])
    login_node_def = relationship(LoginNodeDef, foreign_keys=[login_node_def_id])

    # ------------------------------------------------------------------
    # Backward-compat property accessors
    # ------------------------------------------------------------------
    @property
    def system_name(self):
        pending = self.__dict__.get('_pending_system_name')
        if pending is not None:
            return pending
        return self.system.name if self.system is not None else None

    @system_name.setter
    def system_name(self, value):
        self.__dict__['_pending_system_name'] = value

    @property
    def node_name(self):
        pending = self.__dict__.get('_pending_node_name')
        if pending is not None:
            return pending
        return self.login_node_def.name if self.login_node_def is not None else None

    @node_name.setter
    def node_name(self, value):
        self.__dict__['_pending_node_name'] = value

    @property
    def node_type(self):
        pending = self.__dict__.get('_pending_node_type')
        if pending is not None:
            return pending
        return self.login_node_def.node_type if self.login_node_def is not None else None

    @node_type.setter
    def node_type(self, value):
        self.__dict__['_pending_node_type'] = value

    def __str__(self):
        return f"{self.node_name} ({self.system_name}, {self.node_type})"

    def __repr__(self):
        load_str = f"{self.load_1min:.1f}%" if self.load_1min is not None else "N/A"
        return (f"<LoginNodeStatus(node_name='{self.node_name}', "
                f"system='{self.system_name}', "
                f"type='{self.node_type}', available={self.available}, "
                f"users={self.user_count}, load_1min={load_str})>")

#-------------------------------------------------------------------------bh-
# Lookup tables — System / Queue / Filesystem / LoginNodeDef
#
# Phase 2 normalization (PR-A): replaces the denormalized text columns
# `system_name`, `queue_name`, `filesystem_name`, `node_name`, `node_type`
# on the snapshot tables with FK references against these four small
# lookup tables. Lookup rows are write-once (resolved via
# `get_or_create_*` helpers in `system_status.queries.lookups`).
#-------------------------------------------------------------------------eh-

from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, text
)
from sqlalchemy.orm import relationship

from ..base import StatusBase, SessionMixin


class System(StatusBase, SessionMixin):
    """A compute system identified by name (e.g. 'derecho', 'casper', 'jupyterhub')."""
    __bind_key__ = "system_status"
    __tablename__ = "systems"

    system_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    queues = relationship("QueueDef", back_populates="system",
                          cascade="all, delete-orphan")
    login_node_defs = relationship("LoginNodeDef", back_populates="system",
                                   cascade="all, delete-orphan")

    def __str__(self):
        return f"{self.name} (system_id={self.system_id})"

    def __repr__(self):
        return f"<System(system_id={self.system_id}, name='{self.name}')>"


class QueueDef(StatusBase, SessionMixin):
    """A queue on a specific system (e.g. ('derecho', 'main'), ('casper', 'casper')).

    Named ``QueueDef`` (table ``queues``) rather than ``Queue`` to avoid a
    class-name collision with ``sam.resources.Queue`` in the shared
    SQLAlchemy declarative registry (Flask-SQLAlchemy uses one base for
    both the ``sam`` and ``system_status`` binds).
    """
    __bind_key__ = "system_status"
    __tablename__ = "queues"

    __table_args__ = (
        UniqueConstraint("system_id", "name", name="uq_queues_system_id_name"),
    )

    queue_id = Column(Integer, primary_key=True, autoincrement=True)
    system_id = Column(Integer, ForeignKey("systems.system_id"), nullable=False, index=True)
    name = Column(String(32), nullable=False)
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    system = relationship("System", back_populates="queues")

    def __str__(self):
        return f"{self.name} (system_id={self.system_id})"

    def __repr__(self):
        return f"<QueueDef(queue_id={self.queue_id}, system_id={self.system_id}, name='{self.name}')>"


class Filesystem(StatusBase, SessionMixin):
    """A filesystem identified by name (e.g. 'glade', 'campaign', 'scratch').

    Filesystems are shared across systems, so the lookup is keyed by name only
    (no system_id). The `filesystem_status` snapshot rows still record the
    consuming system via their own `system_id` FK.
    """
    __bind_key__ = "system_status"
    __tablename__ = "filesystems"

    filesystem_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    def __str__(self):
        return f"{self.name} (filesystem_id={self.filesystem_id})"

    def __repr__(self):
        return f"<Filesystem(filesystem_id={self.filesystem_id}, name='{self.name}')>"


class UserDef(StatusBase, SessionMixin):
    """A user identified by username (e.g. 'benkirk', 'bdobbins').

    Used by ``user_proj_queue_status`` to denormalize per-row username
    storage into a 4-byte FK. Names are global (not scoped to a system) —
    the same username refers to the same person regardless of which HPC
    system they're using.

    Named ``UserDef`` (table ``users``) rather than ``User`` to avoid a
    class-name collision with ``sam.core.User`` in the shared SQLAlchemy
    declarative registry — same rationale as ``QueueDef`` vs.
    ``sam.resources.Queue``. This table is intentionally not FK-linked to
    ``sam.users``; later queries by username string will JOIN by name.
    """
    __bind_key__ = "system_status"
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(32), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    def __str__(self):
        return f"{self.username} (user_id={self.user_id})"

    def __repr__(self):
        return f"<UserDef(user_id={self.user_id}, username='{self.username}')>"


class ProjectCodeDef(StatusBase, SessionMixin):
    """A project code identified by name (e.g. 'SCSG0001').

    Used by ``user_proj_queue_status``. ``Account_Name`` from PBS qstat
    output maps to this. Jobs without an Account_Name are bucketed under
    a fixed ``'_unknown_'`` sentinel by the collector parser so totals
    remain reconcilable with ``QueueStatus``.

    Named ``ProjectCodeDef`` (table ``project_codes``) to avoid collision
    with ``sam.projects.Project`` — same rationale as the other Def
    classes. This table is intentionally not FK-linked to ``sam.project``;
    later queries by projcode string will JOIN by name.
    """
    __bind_key__ = "system_status"
    __tablename__ = "project_codes"

    project_code_id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(16), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    def __str__(self):
        return f"{self.project_code} (project_code_id={self.project_code_id})"

    def __repr__(self):
        return (f"<ProjectCodeDef(project_code_id={self.project_code_id}, "
                f"project_code='{self.project_code}')>")


class LoginNodeDef(StatusBase, SessionMixin):
    """A login node definition on a specific system (hostname + node_type)."""
    __bind_key__ = "system_status"
    __tablename__ = "login_nodes"

    __table_args__ = (
        UniqueConstraint("system_id", "name", name="uq_login_nodes_system_id_name"),
    )

    login_node_def_id = Column(Integer, primary_key=True, autoincrement=True)
    system_id = Column(Integer, ForeignKey("systems.system_id"), nullable=False, index=True)
    name = Column(String(32), nullable=False)
    node_type = Column(
        Enum("cpu", "gpu", "data-access", name="login_node_type", native_enum=False),
        nullable=False,
    )
    created_at = Column(DateTime, nullable=False,
                        default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

    system = relationship("System", back_populates="login_node_defs")

    def __str__(self):
        return f"{self.name} ({self.node_type}, system_id={self.system_id})"

    def __repr__(self):
        return (f"<LoginNodeDef(id={self.login_node_def_id}, "
                f"system_id={self.system_id}, name='{self.name}', node_type='{self.node_type}')>")

"""When each user was last seen, per access source.

``user_last_seen`` holds one row per (user, source), overwritten in place by a
monotonic upsert (``system_status.queries.last_seen``). It is never purged: a
user last seen three years ago is the answer it exists to give, so neither it
nor ``status_users`` may enter ``retention.SNAPSHOT_TABLES``
(``tests/unit/models/test_last_seen.py`` is the gate).

Timestamps are naive UTC. Rows backfilled from SAM's daily charge summaries
are day-granular until a live observation supersedes them.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import relationship

from ..base import SessionMixin, StatusBase
from ..timeutil import utcnow_naive
from .lookups import System, UserDef

#: ``webapp`` sources belong to the ``samuel`` system row, so system_id is never NULL.
SOURCE_KINDS = ('webapp', 'login', 'pbs', 'jupyterhub')


class AccessSource(StatusBase, SessionMixin):
    """One place a user can be seen: a kind on a system, e.g. ('login', 'casper')."""
    __bind_key__ = "system_status"
    __tablename__ = "access_sources"

    __table_args__ = (
        UniqueConstraint("kind", "system_id", name="uq_access_sources_kind_system_id"),
    )

    source_id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(32), nullable=False)
    system_id = Column(Integer, ForeignKey("systems.system_id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False,
                        default=utcnow_naive,
                        server_default=text("CURRENT_TIMESTAMP"))

    system = relationship(System, foreign_keys=[system_id])

    def __str__(self):
        return f"{self.kind} (source_id={self.source_id}, system_id={self.system_id})"

    def __repr__(self):
        return f"<AccessSource(source_id={self.source_id}, kind='{self.kind}', system_id={self.system_id})>"


class UserLastSeen(StatusBase, SessionMixin):
    """The first and last time one user was seen on one source."""
    __bind_key__ = "system_status"
    __tablename__ = "user_last_seen"

    __table_args__ = (
        # "who used X since T" and "who has been dormant on X".
        Index("ix_user_last_seen_source_id_last_seen", "source_id", "last_seen"),
    )

    user_id = Column(Integer, ForeignKey("status_users.user_id"), primary_key=True)
    source_id = Column(Integer, ForeignKey("access_sources.source_id"), primary_key=True)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)

    user = relationship(UserDef, foreign_keys=[user_id])
    source = relationship(AccessSource, foreign_keys=[source_id])

    def __str__(self):
        return f"user_id={self.user_id} source_id={self.source_id} last_seen={self.last_seen}"

    def __repr__(self):
        return (f"<UserLastSeen(user_id={self.user_id}, source_id={self.source_id}, "
                f"last_seen={self.last_seen})>")

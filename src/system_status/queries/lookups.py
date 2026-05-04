"""Lookup-table helpers + before_flush translator.

The status tables (`queue_status`, `filesystem_status`, `login_node_status`,
`system_outages`, `resource_reservations`) carry FK columns into the four
lookup tables defined in `system_status.models.lookups`. Code that knows
the name strings (collectors, tests, ad-hoc construction) can keep
assigning to ``obj.system_name = 'derecho'`` etc. — the corresponding
status models declare property setters that stage these strings as
``_pending_*`` instance attributes, and a ``before_flush`` listener
defined here resolves them into FK ids using the helpers below.

This means the public construction surface of the snapshot models is
**unchanged** by Phase 2 — the migration drops the text columns, but
test code and ingest paths that pass ``system_name='derecho'`` keep
working.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

from system_status.models.lookups import (
    Filesystem,
    LoginNodeDef,
    QueueDef,
    System,
)


# ---------------------------------------------------------------------------
# get_or_create helpers — used by the ingest path's _handle_reservations
# and any direct caller. These flush so the returned object has its id
# populated. They are NOT called from inside the before_flush listener
# (which would deadlock on re-entrant flush).
# ---------------------------------------------------------------------------

def get_or_create_system(session: Session, name: str) -> System:
    obj = session.query(System).filter(System.name == name).one_or_none()
    if obj is None:
        obj = System(name=name)
        session.add(obj)
        session.flush()
    return obj


def get_or_create_queue(session: Session, system: System, name: str) -> QueueDef:
    obj = session.query(QueueDef).filter(
        QueueDef.system_id == system.system_id, QueueDef.name == name
    ).one_or_none()
    if obj is None:
        obj = QueueDef(system_id=system.system_id, name=name)
        session.add(obj)
        session.flush()
    return obj


def get_or_create_filesystem(session: Session, name: str) -> Filesystem:
    obj = session.query(Filesystem).filter(Filesystem.name == name).one_or_none()
    if obj is None:
        obj = Filesystem(name=name)
        session.add(obj)
        session.flush()
    return obj


def get_or_create_login_node_def(
    session: Session, system: System, name: str, node_type: str
) -> LoginNodeDef:
    obj = session.query(LoginNodeDef).filter(
        LoginNodeDef.system_id == system.system_id, LoginNodeDef.name == name
    ).one_or_none()
    if obj is None:
        obj = LoginNodeDef(system_id=system.system_id, name=name, node_type=node_type)
        session.add(obj)
        session.flush()
    return obj


# ---------------------------------------------------------------------------
# before_flush listener
#
# Resolves the `_pending_*_name` / `_pending_node_type` strings staged by
# property setters on the snapshot models into ORM relationships. We
# assign the looked-up Python object (e.g. `target.system = sys`) rather
# than the FK id — SQLAlchemy resolves the FK at the topological-sort
# stage of the same flush, so brand-new lookup rows work too.
#
# We must NOT call session.flush() inside this listener (re-entrant
# flush is forbidden), so we do our own in-flush-safe lookups: query
# the DB first, then scan session.new for pending matches; if neither
# finds it, instantiate a new lookup row.
# ---------------------------------------------------------------------------

def _snapshot_models():
    """Lazy import to avoid circular imports during module load."""
    from system_status.models.queues import QueueStatus
    from system_status.models.filesystems import FilesystemStatus
    from system_status.models.login_nodes import LoginNodeStatus
    from system_status.models.outages import SystemOutage, ResourceReservation
    return (QueueStatus, FilesystemStatus, LoginNodeStatus,
            SystemOutage, ResourceReservation)


def _find_in_session(session: Session, cls, **filters):
    """Look for a flushed-or-pending row matching `filters`.

    Checks session.new first (pending objects this flush cycle), then the
    DB. We avoid session.query() autoflush because we're inside a flush
    already.
    """
    for obj in session.new:
        if isinstance(obj, cls) and all(getattr(obj, k) == v for k, v in filters.items()):
            return obj
    q = session.query(cls)
    for k, v in filters.items():
        q = q.filter(getattr(cls, k) == v)
    return q.with_for_update(read=False, of=cls).first() if False else q.first()


def _ensure_system(session: Session, cache: dict, name: str) -> System:
    if name in cache:
        return cache[name]
    obj = _find_in_session(session, System, name=name)
    if obj is None:
        obj = System(name=name)
        session.add(obj)
    cache[name] = obj
    return obj


def _ensure_queue(session: Session, cache: dict, system: System, name: str) -> QueueDef:
    key = (id(system), name)
    if key in cache:
        return cache[key]
    obj = None
    # Pending matches first.
    for o in session.new:
        if isinstance(o, QueueDef) and o.name == name and o.system is system:
            obj = o
            break
    # Fall through to DB if `system` is already-flushed (has id).
    if obj is None and getattr(system, "system_id", None) is not None:
        obj = session.query(QueueDef).filter(
            QueueDef.system_id == system.system_id, QueueDef.name == name
        ).first()
    if obj is None:
        obj = QueueDef(system=system, name=name)
        session.add(obj)
    cache[key] = obj
    return obj


def _ensure_filesystem(session: Session, cache: dict, name: str) -> Filesystem:
    if name in cache:
        return cache[name]
    obj = _find_in_session(session, Filesystem, name=name)
    if obj is None:
        obj = Filesystem(name=name)
        session.add(obj)
    cache[name] = obj
    return obj


def _ensure_login_node_def(
    session: Session, cache: dict, system: System, name: str, node_type: str
) -> LoginNodeDef:
    key = (id(system), name)
    if key in cache:
        return cache[key]
    obj = None
    for o in session.new:
        if isinstance(o, LoginNodeDef) and o.name == name and o.system is system:
            obj = o
            break
    if obj is None and getattr(system, "system_id", None) is not None:
        obj = session.query(LoginNodeDef).filter(
            LoginNodeDef.system_id == system.system_id, LoginNodeDef.name == name
        ).first()
    if obj is None:
        obj = LoginNodeDef(system=system, name=name, node_type=node_type)
        session.add(obj)
    cache[key] = obj
    return obj


@event.listens_for(Session, "before_flush")
def _resolve_pending_lookup_names(session, flush_context, instances):
    snapshot_classes = _snapshot_models()
    (QueueStatus, FilesystemStatus, LoginNodeStatus,
     SystemOutage, ResourceReservation) = snapshot_classes

    sys_cache: dict = {}
    queue_cache: dict = {}
    fs_cache: dict = {}
    ln_cache: dict = {}

    # `session.new` is a lazy IdentitySet — copy to a list so we can iterate
    # while adding new objects (lookup rows) to the session.
    for obj in list(session.new):
        if not isinstance(obj, snapshot_classes):
            continue

        pending_system = obj.__dict__.pop("_pending_system_name", None)
        if pending_system:
            obj.system = _ensure_system(session, sys_cache, pending_system)
        sys_obj = obj.system  # may already have been set; may be None for legacy code

        if isinstance(obj, QueueStatus):
            pending_queue = obj.__dict__.pop("_pending_queue_name", None)
            if pending_queue and sys_obj is not None:
                obj.queue = _ensure_queue(session, queue_cache, sys_obj, pending_queue)

        elif isinstance(obj, FilesystemStatus):
            pending_fs = obj.__dict__.pop("_pending_filesystem_name", None)
            if pending_fs:
                obj.filesystem = _ensure_filesystem(session, fs_cache, pending_fs)

        elif isinstance(obj, LoginNodeStatus):
            pending_node = obj.__dict__.pop("_pending_node_name", None)
            pending_type = obj.__dict__.pop("_pending_node_type", None)
            if pending_node and sys_obj is not None:
                obj.login_node_def = _ensure_login_node_def(
                    session, ln_cache, sys_obj, pending_node, pending_type or "cpu"
                )

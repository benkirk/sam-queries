"""Record and read the per-(user, source) last-seen ledger.

``record_seen`` is the one write path for collectors, the webapp login hook and
the backfill. It is a bulk upsert that only ever widens a row's window:
``last_seen`` never moves back and ``first_seen`` never moves forward, so a
late, replayed or backfilled observation is always safe to apply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from system_status.models.last_seen import SOURCE_KINDS, AccessSource, UserLastSeen
from system_status.models.lookups import System, UserDef

from .lookups import get_or_create_system

#: The ``systems`` row that webapp logins are recorded against.
WEBAPP_SYSTEM = 'samuel'

#: Rows per INSERT statement; keeps bind-parameter counts well under every dialect's limit.
_CHUNK = 500

Sighting = tuple[str, datetime, datetime]  # (username, first_seen, last_seen)


def get_or_create_source(session: Session, kind: str, system_name: str) -> AccessSource:
    if kind not in SOURCE_KINDS:
        raise ValueError(f"unknown access source kind {kind!r}; expected one of {SOURCE_KINDS}")
    system = get_or_create_system(session, system_name)
    obj = session.query(AccessSource).filter(
        AccessSource.kind == kind, AccessSource.system_id == system.system_id
    ).one_or_none()
    if obj is None:
        obj = AccessSource(kind=kind, system_id=system.system_id)
        session.add(obj)
        session.flush()
    return obj


def _user_ids(session: Session, usernames: set[str]) -> dict[str, int]:
    """username -> status_users.user_id, creating the missing rows in one flush."""
    ids: dict[str, int] = {}
    names = sorted(usernames)
    for i in range(0, len(names), _CHUNK):
        chunk = names[i:i + _CHUNK]
        ids.update(session.query(UserDef.username, UserDef.user_id)
                   .filter(UserDef.username.in_(chunk)).all())
    missing = [UserDef(username=n) for n in names if n not in ids]
    if missing:
        session.add_all(missing)
        session.flush()
        ids.update((u.username, u.user_id) for u in missing)
    return ids


def _merge(sightings: Iterable[Sighting]) -> dict[str, tuple[datetime, datetime]]:
    """Collapse repeats of a username; one statement may not touch a key twice on Postgres."""
    merged: dict[str, tuple[datetime, datetime]] = {}
    for username, first, last in sightings:
        username = (username or '').strip()
        if not username:
            continue
        if username in merged:
            f, l = merged[username]
            merged[username] = (min(f, first), max(l, last))
        else:
            merged[username] = (first, last)
    return merged


def _upsert_statement(dialect: str, rows: list[dict]):
    t = UserLastSeen.__table__
    if dialect in ('mysql', 'mariadb'):
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(t).values(rows)
        return stmt.on_duplicate_key_update(
            first_seen=func.least(t.c.first_seen, stmt.inserted.first_seen),
            last_seen=func.greatest(t.c.last_seen, stmt.inserted.last_seen),
        )
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
        greatest, least = func.greatest, func.least
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
        greatest, least = func.max, func.min  # SQLite's two-argument scalar forms
    else:
        raise NotImplementedError(f"record_seen has no upsert for dialect {dialect!r}")
    stmt = insert(t).values(rows)
    ex = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=[t.c.user_id, t.c.source_id],
        set_={'first_seen': least(t.c.first_seen, ex.first_seen),
              'last_seen': greatest(t.c.last_seen, ex.last_seen)},
        # Skip no-op rewrites: on Postgres every UPDATE leaves a dead tuple.
        where=or_(ex.last_seen > t.c.last_seen, ex.first_seen < t.c.first_seen),
    )


def record_seen(session: Session, kind: str, system_name: str,
                sightings: Iterable[Sighting]) -> int:
    """Widen each user's (first_seen, last_seen) window on one source; returns users applied.

    Flushes but does not commit: the caller owns the transaction.
    """
    merged = _merge(sightings)
    if not merged:
        return 0
    source = get_or_create_source(session, kind, system_name)
    ids = _user_ids(session, set(merged))
    rows = [{'user_id': ids[name], 'source_id': source.source_id,
             'first_seen': first, 'last_seen': last}
            for name, (first, last) in merged.items()]
    dialect = session.get_bind(mapper=UserLastSeen.__mapper__).dialect.name
    for i in range(0, len(rows), _CHUNK):
        session.execute(_upsert_statement(dialect, rows[i:i + _CHUNK]))
    return len(rows)


def record_seen_at(session: Session, kind: str, system_name: str,
                   usernames: Iterable[str], when: datetime) -> int:
    """``record_seen`` for a set of users all observed at one instant."""
    return record_seen(session, kind, system_name, ((u, when, when) for u in usernames))


def get_last_seen(session: Session, username: str) -> list[dict]:
    """One dict per source the user has been seen on, most recent first."""
    rows = (session.query(AccessSource.kind, System.name,
                          UserLastSeen.first_seen, UserLastSeen.last_seen)
            .join(UserLastSeen.source).join(AccessSource.system).join(UserLastSeen.user)
            .filter(UserDef.username == username)
            .order_by(UserLastSeen.last_seen.desc())
            .all())
    return [{'kind': kind, 'system': system, 'first_seen': first, 'last_seen': last}
            for kind, system, first, last in rows]


def get_last_seen_by_user(session: Session) -> dict[str, dict[str, tuple[datetime, str]]]:
    """``{username: {kind: (last_seen, system)}}``, the newest row per user and kind."""
    rows = (session.query(UserDef.username, AccessSource.kind, System.name,
                          UserLastSeen.last_seen)
            .join(UserLastSeen.source).join(AccessSource.system).join(UserLastSeen.user)
            .all())
    out: dict[str, dict[str, tuple[datetime, str]]] = {}
    for username, kind, system, last in rows:
        per_kind = out.setdefault(username, {})
        if kind not in per_kind or last > per_kind[kind][0]:
            per_kind[kind] = (last, system)
    return out

"""before_flush listener coverage for UserProjQueueStatus.

These tests construct ORM instances directly (no API/schema involvement)
and assert that the listener resolves the staged
``_pending_username`` / ``_pending_project_code`` /
``_pending_queue_name`` / ``_pending_system_name`` strings into FK
relationships at flush time, including the case where two snapshot rows
in the same flush share a brand-new lookup row.
"""

from datetime import datetime

import pytest

from system_status import (
    UserProjQueueStatus,
    UserDef,
    ProjectCodeDef,
    System,
    QueueDef,
)


pytestmark = pytest.mark.integration


def test_single_row_resolves_all_pending_names(status_session):
    ts = datetime(2026, 5, 4, 12, 0, 0)
    obj = UserProjQueueStatus(
        timestamp=ts,
        last_seen=ts,
        system_name='derecho',
        queue_name='main',
        username='benkirk',
        project_code='SCSG0001',
        running_jobs=3,
        cores_allocated=64,
    )
    status_session.add(obj)
    status_session.flush()

    assert obj.system is not None and obj.system.name == 'derecho'
    assert obj.queue is not None and obj.queue.name == 'main'
    assert obj.user is not None and obj.user.username == 'benkirk'
    assert obj.project is not None and obj.project.project_code == 'SCSG0001'

    # Pending strings consumed.
    assert '_pending_system_name' not in obj.__dict__
    assert '_pending_username' not in obj.__dict__
    assert '_pending_project_code' not in obj.__dict__


def test_multiple_rows_share_new_lookup_rows_in_one_flush(status_session):
    """Two rows in the same flush referencing the same new user/project
    should reuse the same UserDef / ProjectCodeDef rows, not duplicate them."""
    ts = datetime(2026, 5, 4, 12, 0, 0)
    rows = [
        UserProjQueueStatus(
            timestamp=ts, last_seen=ts, system_name='derecho', queue_name='main',
            username='benkirk', project_code='SCSG0001', running_jobs=2,
        ),
        UserProjQueueStatus(
            timestamp=ts, last_seen=ts, system_name='derecho', queue_name='preempt',
            username='benkirk', project_code='SCSG0001', pending_jobs=1,
        ),
    ]
    status_session.add_all(rows)
    status_session.flush()

    assert status_session.query(UserDef).count() == 1
    assert status_session.query(ProjectCodeDef).count() == 1
    # 'main' and 'preempt' are distinct queues, both on 'derecho' → 2 QueueDef rows
    assert status_session.query(QueueDef).count() == 2
    assert status_session.query(System).count() == 1
    assert rows[0].user is rows[1].user
    assert rows[0].project is rows[1].project


def test_existing_lookup_rows_are_reused(status_session):
    """If UserDef/ProjectCodeDef rows already exist, the listener must
    reuse them rather than violate the unique constraint."""
    status_session.add(UserDef(username='benkirk'))
    status_session.add(ProjectCodeDef(project_code='SCSG0001'))
    status_session.flush()

    ts = datetime(2026, 5, 4, 12, 0, 0)
    obj = UserProjQueueStatus(
        timestamp=ts, last_seen=ts,
        system_name='derecho', queue_name='main',
        username='benkirk', project_code='SCSG0001', running_jobs=1,
    )
    status_session.add(obj)
    status_session.flush()

    assert status_session.query(UserDef).count() == 1
    assert status_session.query(ProjectCodeDef).count() == 1
    assert obj.user.username == 'benkirk'
    assert obj.project.project_code == 'SCSG0001'


# ---------------------------------------------------------------------------
# Span-coalescer tests
# ---------------------------------------------------------------------------

class _FakeParent:
    """Plain stand-in for a transient DerechoStatus / CasperStatus parent.

    The coalescer treats ``parent_status.user_project_queues`` as a plain
    Python list (the parent isn't in the session yet), so we don't need
    a real parent ORM object for these tests.
    """
    def __init__(self, user_project_queues):
        self.user_project_queues = user_project_queues


def _make_child(ts, *, username='benkirk', project_code='SCSG0001',
                queue_name='main', system_name='derecho', **counts):
    """Build a transient UserProjQueueStatus mirroring schema-loaded shape."""
    return UserProjQueueStatus(
        timestamp=ts,
        system_name=system_name,
        queue_name=queue_name,
        username=username,
        project_code=project_code,
        **counts,
    )


def test_second_flush_with_same_counts_extends_last_seen(status_session):
    from system_status.queries.user_proj_queue_ingest import (
        coalesce_user_proj_queue_spans,
    )

    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = datetime(2026, 5, 4, 12, 5, 0)

    # First tick: insert a fresh span via the coalescer.
    parent0 = _FakeParent([
        _make_child(t0, running_jobs=3, cores_allocated=64),
    ])
    counts0 = coalesce_user_proj_queue_spans(status_session, parent0, t0)
    status_session.add_all(parent0.user_project_queues)
    status_session.flush()

    assert counts0 == {'inserted': 1, 'extended': 0}
    assert status_session.query(UserProjQueueStatus).count() == 1

    # Second tick, identical counts → should extend, not insert.
    parent1 = _FakeParent([
        _make_child(t1, running_jobs=3, cores_allocated=64),
    ])
    counts1 = coalesce_user_proj_queue_spans(status_session, parent1, t1)
    status_session.add_all(parent1.user_project_queues)
    status_session.flush()

    assert counts1 == {'inserted': 0, 'extended': 1}
    assert status_session.query(UserProjQueueStatus).count() == 1
    row = status_session.query(UserProjQueueStatus).one()
    assert row.timestamp == t0
    assert row.last_seen == t1


def test_second_flush_with_different_counts_inserts_new_span(status_session):
    from system_status.queries.user_proj_queue_ingest import (
        coalesce_user_proj_queue_spans,
    )

    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = datetime(2026, 5, 4, 12, 5, 0)

    parent0 = _FakeParent([
        _make_child(t0, running_jobs=3, cores_allocated=64),
    ])
    coalesce_user_proj_queue_spans(status_session, parent0, t0)
    status_session.add_all(parent0.user_project_queues)
    status_session.flush()

    parent1 = _FakeParent([
        _make_child(t1, running_jobs=4, cores_allocated=128),  # changed
    ])
    counts = coalesce_user_proj_queue_spans(status_session, parent1, t1)
    status_session.add_all(parent1.user_project_queues)
    status_session.flush()

    assert counts == {'inserted': 1, 'extended': 0}
    rows = status_session.query(UserProjQueueStatus).order_by(
        UserProjQueueStatus.timestamp).all()
    assert len(rows) == 2
    # Original span closed at its own first_seen (never extended).
    assert rows[0].timestamp == t0 and rows[0].last_seen == t0
    # New span starts at t1.
    assert rows[1].timestamp == t1 and rows[1].last_seen == t1


def test_disappearing_tuple_is_left_alone(status_session):
    """A tuple that vanishes at the next tick keeps its old span as-is."""
    from system_status.queries.user_proj_queue_ingest import (
        coalesce_user_proj_queue_spans,
    )

    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = datetime(2026, 5, 4, 12, 5, 0)

    parent0 = _FakeParent([
        _make_child(t0, username='alice', running_jobs=1, cores_allocated=8),
        _make_child(t0, username='bob', running_jobs=2, cores_allocated=16),
    ])
    coalesce_user_proj_queue_spans(status_session, parent0, t0)
    status_session.add_all(parent0.user_project_queues)
    status_session.flush()

    # Bob disappears; alice still here.
    parent1 = _FakeParent([
        _make_child(t1, username='alice', running_jobs=1, cores_allocated=8),
    ])
    counts = coalesce_user_proj_queue_spans(status_session, parent1, t1)
    status_session.add_all(parent1.user_project_queues)
    status_session.flush()

    assert counts == {'inserted': 0, 'extended': 1}
    rows = {r.user.username: r for r in
            status_session.query(UserProjQueueStatus).all()}
    assert rows['alice'].timestamp == t0 and rows['alice'].last_seen == t1
    # Bob's row still has last_seen == t0 (untouched).
    assert rows['bob'].timestamp == t0 and rows['bob'].last_seen == t0


def test_long_gap_starts_fresh_span(status_session):
    """If T_new - prev_ts > MAX_SPAN_GAP, even matching counts insert."""
    from datetime import timedelta as _td
    from system_status.queries.user_proj_queue_ingest import (
        coalesce_user_proj_queue_spans, MAX_SPAN_GAP,
    )

    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t_after_gap = t0 + MAX_SPAN_GAP + _td(minutes=1)

    parent0 = _FakeParent([
        _make_child(t0, running_jobs=1, cores_allocated=8),
    ])
    coalesce_user_proj_queue_spans(status_session, parent0, t0)
    status_session.add_all(parent0.user_project_queues)
    status_session.flush()

    parent1 = _FakeParent([
        _make_child(t_after_gap, running_jobs=1, cores_allocated=8),
    ])
    counts = coalesce_user_proj_queue_spans(status_session, parent1, t_after_gap)
    status_session.add_all(parent1.user_project_queues)
    status_session.flush()

    assert counts == {'inserted': 1, 'extended': 0}
    assert status_session.query(UserProjQueueStatus).count() == 2

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
    obj = UserProjQueueStatus(
        timestamp=datetime(2026, 5, 4, 12, 0, 0),
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
            timestamp=ts, system_name='derecho', queue_name='main',
            username='benkirk', project_code='SCSG0001', running_jobs=2,
        ),
        UserProjQueueStatus(
            timestamp=ts, system_name='derecho', queue_name='preempt',
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

    obj = UserProjQueueStatus(
        timestamp=datetime(2026, 5, 4, 12, 0, 0),
        system_name='derecho', queue_name='main',
        username='benkirk', project_code='SCSG0001', running_jobs=1,
    )
    status_session.add(obj)
    status_session.flush()

    assert status_session.query(UserDef).count() == 1
    assert status_session.query(ProjectCodeDef).count() == 1
    assert obj.user.username == 'benkirk'
    assert obj.project.project_code == 'SCSG0001'

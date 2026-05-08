"""Unit tests for ``system_status.queries.get_user_proj_timeseries``.

Focused on top-N selection semantics: ``rank_by='peak'`` (default) ranks
by the maximum value over the window; ``rank_by='current'`` ranks by the
value at the latest tick.
"""

from datetime import datetime, timedelta

import pytest

from system_status import DerechoStatus, UserProjQueueStatus
from system_status.queries.user_proj_queues import get_user_proj_timeseries


pytestmark = pytest.mark.unit


def _make_derecho(session, ts):
    parent = DerechoStatus(
        timestamp=ts,
        cpu_nodes_total=100, cpu_nodes_available=50, cpu_nodes_down=0,
        gpu_nodes_total=10, gpu_nodes_available=5, gpu_nodes_down=0,
        cpu_cores_total=12800, cpu_cores_allocated=6400, cpu_cores_idle=6400,
        gpu_count_total=40, gpu_count_allocated=20, gpu_count_idle=20,
        memory_total_gb=10000.0, memory_allocated_gb=5000.0,
    )
    session.add(parent)
    return parent


def _make_upq(session, parent, *, queue, user, project, cores, last_seen=None):
    """Build a span-shaped UserProjQueueStatus.

    By default the span is degenerate (last_seen == parent.timestamp), so
    legacy per-tick fixtures still work. Pass ``last_seen=<datetime>`` to
    build a span covering multiple parent ticks.
    """
    if last_seen is None:
        last_seen = parent.timestamp
    row = UserProjQueueStatus(
        timestamp=parent.timestamp,
        last_seen=last_seen,
        system_name='derecho', queue_name=queue,
        username=user, project_code=project,
        running_jobs=1 if cores else 0,
        cores_allocated=cores,
    )
    row.derecho_status = parent
    session.add(row)
    return row


def test_rank_by_peak_vs_current_diverge(status_session):
    """Two users diverge between peak and latest-tick rankings.

    Window has three ticks. User 'spiker' peaks at t0 (1000) and is gone
    by t2 (0). User 'steady' rises to 500 at t2 (its latest value, also
    its peak). Selecting top_n=1:

    - rank_by='peak'    → 'spiker' wins (1000 > 500)
    - rank_by='current' → 'steady' wins (500 > 0 at t2)
    """
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    t2 = t0 + timedelta(minutes=10)
    p0 = _make_derecho(status_session, t0)
    p1 = _make_derecho(status_session, t1)
    p2 = _make_derecho(status_session, t2)

    _make_upq(status_session, p0, queue='main',
              user='spiker', project='SCSG0001', cores=1000)
    _make_upq(status_session, p1, queue='main',
              user='spiker', project='SCSG0001', cores=200)
    _make_upq(status_session, p1, queue='main',
              user='steady', project='SCSG0001', cores=300)
    _make_upq(status_session, p2, queue='main',
              user='steady', project='SCSG0001', cores=500)
    status_session.flush()

    common = dict(
        session=status_session,
        system='derecho',
        queue_name='main',
        start_date=t0,
        end_date=t2,
        state='running',
        metric='cores',
        group_by='user',
        top_n=1,
    )

    peak = get_user_proj_timeseries(**common, rank_by='peak')
    current = get_user_proj_timeseries(**common, rank_by='current')

    # Series order: 'Others' first, then named series small→large by rank.
    # With top_n=1 we expect exactly two entries: ['Others', winner].
    peak_named = [s['label'] for s in peak['series'] if s['label'] != 'Others']
    current_named = [s['label'] for s in current['series'] if s['label'] != 'Others']

    assert peak_named == ['spiker'], peak['series']
    assert current_named == ['steady'], current['series']


def test_chart_explodes_span_across_ticks(status_session):
    """A single span covering three parent ticks contributes its constant
    value at every tick."""
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    t2 = t0 + timedelta(minutes=10)
    _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    p2 = _make_derecho(status_session, t2)

    # Single span [t0..t2] anchored at p0 timestamp, last_seen at t2.
    row = UserProjQueueStatus(
        timestamp=t0, last_seen=t2,
        system_name='derecho', queue_name='main',
        username='steady', project_code='SCSG0001',
        running_jobs=1, cores_allocated=100,
    )
    # Parent FK on first_seen tick — match the production convention.
    row.derecho_status = status_session.query(DerechoStatus).filter_by(
        timestamp=t0).one()
    status_session.add(row)
    # Touch p2 to silence flake about unused parent.
    assert p2.timestamp == t2
    status_session.flush()

    out = get_user_proj_timeseries(
        status_session,
        system='derecho',
        queue_name='main',
        start_date=t0,
        end_date=t2,
        state='running',
        metric='cores',
        group_by='user',
        top_n=5,
        rank_by='peak',
    )

    named = {s['label']: s['values'] for s in out['series']
             if s['label'] != 'Others'}
    assert named == {'steady': [100, 100, 100]}


def test_rank_by_invalid_raises(status_session):
    with pytest.raises(ValueError, match='rank_by'):
        get_user_proj_timeseries(
            status_session,
            system='derecho',
            queue_name='main',
            start_date=datetime(2026, 5, 4, 12, 0, 0),
            end_date=datetime(2026, 5, 4, 13, 0, 0),
            state='running',
            metric='cores',
            group_by='user',
            rank_by='median',
        )

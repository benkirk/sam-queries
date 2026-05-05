"""Unit tests for ``system_status.queries.user_proj_usage``.

Covers:
- empty / single-tick / two-tick boundary cases
- variable-dt integration (heterogeneous tick intervals)
- sparse (user, project, queue) tuples (appear / disappear / reappear)
- system / queue / user / project filtering
- exclude_unknown_project
- reconciliation against summed QueueStatus core-hours
- multi-chunk windows (chunk_days < window length)
"""

from datetime import datetime, timedelta

import pytest

from system_status import (
    CasperStatus,
    DerechoStatus,
    QueueStatus,
    UserProjQueueStatus,
)
from system_status.queries import get_user_proj_usage


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_derecho(session, ts):
    """Minimal DerechoStatus parent row at timestamp ``ts``."""
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


def _make_casper(session, ts):
    parent = CasperStatus(
        timestamp=ts,
        cpu_nodes_total=50, cpu_nodes_available=30, cpu_nodes_down=0,
        gpu_nodes_total=20, gpu_nodes_available=10, gpu_nodes_down=0,
        viz_nodes_total=5, viz_nodes_available=5, viz_nodes_down=0,
        cpu_cores_total=2000, cpu_cores_allocated=1000, cpu_cores_idle=1000,
        gpu_count_total=80, gpu_count_allocated=40, gpu_count_idle=40,
        viz_count_total=5, viz_count_allocated=2, viz_count_idle=3,
        memory_total_gb=5000.0, memory_allocated_gb=2500.0,
    )
    session.add(parent)
    return parent


def _make_upq(session, parent, *, system, queue, user, project,
              cores=0, gpus=0, nodes=0):
    """Add a UserProjQueueStatus row attached to its system parent."""
    row = UserProjQueueStatus(
        timestamp=parent.timestamp,
        system_name=system, queue_name=queue,
        username=user, project_code=project,
        running_jobs=1 if cores or gpus or nodes else 0,
        cores_allocated=cores,
        gpus_allocated=gpus,
        nodes_allocated=nodes,
    )
    if system == 'derecho':
        row.derecho_status = parent
    else:
        row.casper_status = parent
    session.add(row)
    return row


# ---------------------------------------------------------------------------
# Boundary cases
# ---------------------------------------------------------------------------

def test_empty_window_returns_empty(status_session):
    """No parent ticks in [start, end] → no integration possible."""
    out = get_user_proj_usage(
        status_session,
        system='derecho',
        start_date=datetime(2026, 5, 4, 12, 0, 0),
        end_date=datetime(2026, 5, 4, 13, 0, 0),
    )
    assert out == []


def test_single_tick_returns_empty(status_session):
    """One parent tick → no successor → no interval to integrate over."""
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    parent = _make_derecho(status_session, t0)
    _make_upq(status_session, parent, system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=128)
    status_session.flush()

    out = get_user_proj_usage(
        status_session,
        system='derecho',
        start_date=t0 - timedelta(hours=1),
        end_date=t0 + timedelta(hours=1),
    )
    assert out == []


def test_two_ticks_left_step_integration(status_session):
    """Cores=128 at t0, then 0 (absent) at t1 — left-step credits the
    full first interval at 128, contributes nothing for t1 (no successor).
    Expected: 128 cores × 5 min = 128/12 core-hours."""
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    p0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)   # tick exists, but no rows for this user
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=128, gpus=0, nodes=4)
    status_session.flush()

    out = get_user_proj_usage(
        status_session,
        system='derecho',
        start_date=t0,
        end_date=t1,
    )
    assert len(out) == 1
    r = out[0]
    assert r['username'] == 'benkirk'
    assert r['project_code'] == 'SCSG0001'
    assert r['queue_name'] == 'main'
    assert r['system'] == 'derecho'
    expected_core_h = 128 * 300.0 / 3600.0   # cores × seconds / 3600
    assert r['core_hours'] == pytest.approx(expected_core_h)
    assert r['gpu_hours'] == pytest.approx(0.0)
    assert r['node_hours'] == pytest.approx(4 * 300.0 / 3600.0)
    assert r['tick_count'] == 1
    assert r['first_seen'] == t0
    assert r['last_seen'] == t0


# ---------------------------------------------------------------------------
# Variable-dt — collector irregularity
# ---------------------------------------------------------------------------

def test_variable_tick_intervals(status_session):
    """Three ticks at t0, t0+5min, t0+15min. A user with cores=128 at all
    three ticks should integrate to:
      tick 0 (5 min @ 128) + tick 1 (10 min @ 128) + tick 2 (no successor)
      = 128 × (300 + 600) / 3600 = 32 core-hours
    Verifies the implementation does NOT hard-code 5 min."""
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    t2 = t0 + timedelta(minutes=15)
    parents = [_make_derecho(status_session, t) for t in (t0, t1, t2)]
    for p in parents:
        _make_upq(status_session, p, system='derecho', queue='main',
                  user='benkirk', project='SCSG0001', cores=128)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t2,
    )
    assert len(out) == 1
    expected = 128 * (300 + 600) / 3600.0
    assert out[0]['core_hours'] == pytest.approx(expected)
    assert out[0]['tick_count'] == 3
    assert out[0]['first_seen'] == t0
    assert out[0]['last_seen'] == t2


# ---------------------------------------------------------------------------
# Sparse tuples (the headline complication)
# ---------------------------------------------------------------------------

def test_sparse_tuple_appears_disappears_reappears(status_session):
    """benkirk runs at t0 and t2 with 64 cores, but is absent at t1.
    The interval (t1, t2) is a "gap" for benkirk — absence means 0 cores
    for that interval, so it contributes nothing.

    Tick layout (5 min spacing):
      t0 (64) ──5min──> t1 (absent) ──5min──> t2 (64) ──5min──> t3 (last)

    Left-step integration:
      t0: 64 × 5min = 320 core-min
      t1: absent → 0
      t2: 64 × 5min = 320 core-min
      t3: no successor → 0
    Total = 640 core-min = 10.667 core-hours
    """
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    ticks = [t0 + timedelta(minutes=5 * i) for i in range(4)]
    parents = [_make_derecho(status_session, t) for t in ticks]
    # Present at t0, absent at t1, present at t2 (no row at t3 either)
    _make_upq(status_session, parents[0], system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=64)
    _make_upq(status_session, parents[2], system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=64)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=ticks[-1],
    )
    assert len(out) == 1
    expected = 64 * 600.0 / 3600.0   # 2 ticks × 5 min worth
    assert out[0]['core_hours'] == pytest.approx(expected)
    assert out[0]['tick_count'] == 2
    assert out[0]['first_seen'] == ticks[0]
    assert out[0]['last_seen'] == ticks[2]


def test_sparse_does_not_carry_forward(status_session):
    """If a tuple appears at t0 only and never again, it must be charged
    for ONE tick interval, not the whole window.

    Naïve carry-forward would credit benkirk for every tick after t0.
    """
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    # 6 ticks, benkirk only present at the first
    ticks = [t0 + timedelta(minutes=5 * i) for i in range(6)]
    parents = [_make_derecho(status_session, t) for t in ticks]
    _make_upq(status_session, parents[0], system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=128)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=ticks[-1],
    )
    assert len(out) == 1
    assert out[0]['core_hours'] == pytest.approx(128 * 300.0 / 3600.0)
    assert out[0]['tick_count'] == 1


# ---------------------------------------------------------------------------
# Reconciliation — the QueueStatus invariant must hold over windows too
# ---------------------------------------------------------------------------

def test_reconciliation_against_queue_status(status_session):
    """Per the user_proj_queue_status invariant: at every tick, summing
    user_proj rows by queue equals the parent QueueStatus row. Therefore
    integrated core-hours from get_user_proj_usage (summed across all
    tuples on a queue) must equal the integral of QueueStatus on the
    same queue.
    """
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    ticks = [t0 + timedelta(minutes=5 * i) for i in range(4)]
    parents = [_make_derecho(status_session, t) for t in ticks]

    # Contributions per tick — designed so the QueueStatus totals match
    # the sum of user_proj rows.
    layout = {
        # tick_idx: list of (user, project, cores, gpus, nodes)
        0: [('alice', 'P1', 64, 0, 2), ('bob', 'P2', 32, 0, 1)],
        1: [('alice', 'P1', 64, 0, 2)],
        2: [('alice', 'P1', 32, 0, 1), ('bob', 'P2', 96, 0, 3),
            ('carol', 'P3', 16, 4, 1)],
        3: [('bob', 'P2', 16, 0, 1)],   # last tick, contributes 0 anyway
    }

    for i, entries in layout.items():
        total_cores = sum(e[2] for e in entries)
        total_gpus = sum(e[3] for e in entries)
        # Parent QueueStatus row aggregating that tick's users on 'main'
        qs = QueueStatus(
            timestamp=ticks[i],
            derecho_status=parents[i],
            system_name='derecho', queue_name='main',
            running_jobs=len(entries),
            cores_allocated=total_cores,
            gpus_allocated=total_gpus,
            active_users=len(set(e[0] for e in entries)),
        )
        status_session.add(qs)
        for user, proj, cores, gpus, nodes in entries:
            _make_upq(status_session, parents[i], system='derecho',
                      queue='main', user=user, project=proj,
                      cores=cores, gpus=gpus, nodes=nodes)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=ticks[-1], queue_name='main',
    )

    # Hand-computed expected core-hours per tick interval (left-step):
    #   interval 0 (t0→t1, 5 min): 96 cores × 5/60 = 8.0
    #   interval 1 (t1→t2, 5 min): 64 cores × 5/60 = 5.333
    #   interval 2 (t2→t3, 5 min): 144 cores × 5/60 = 12.0
    #   interval 3 (last tick): 0 (no successor)
    expected_total_core_hours = (96 + 64 + 144) * 300.0 / 3600.0
    summed = sum(r['core_hours'] for r in out)
    assert summed == pytest.approx(expected_total_core_hours)

    # And the GPU-hours: only carol at t2 has 4 gpus, contributes 4×5/60
    expected_gpu_hours = 4 * 300.0 / 3600.0
    assert sum(r['gpu_hours'] for r in out) == pytest.approx(expected_gpu_hours)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def test_filter_by_queue(status_session):
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    p0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='benkirk', project='SCSG0001', cores=64)
    _make_upq(status_session, p0, system='derecho', queue='preempt',
              user='benkirk', project='SCSG0001', cores=128)
    status_session.flush()

    out_main = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, queue_name='main',
    )
    assert len(out_main) == 1
    assert out_main[0]['queue_name'] == 'main'
    assert out_main[0]['core_hours'] == pytest.approx(64 * 300.0 / 3600.0)

    out_preempt = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, queue_name='preempt',
    )
    assert len(out_preempt) == 1
    assert out_preempt[0]['core_hours'] == pytest.approx(128 * 300.0 / 3600.0)

    out_all = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1,
    )
    # benkirk on two queues = two rows
    assert len(out_all) == 2
    assert sum(r['core_hours'] for r in out_all) == pytest.approx(
        (64 + 128) * 300.0 / 3600.0
    )


def test_filter_by_user_and_project(status_session):
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    p0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='alice', project='P1', cores=64)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='bob', project='P1', cores=128)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='alice', project='P2', cores=32)
    status_session.flush()

    only_alice = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, username='alice',
    )
    assert {r['project_code'] for r in only_alice} == {'P1', 'P2'}
    assert all(r['username'] == 'alice' for r in only_alice)

    only_p1 = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, project_code='P1',
    )
    assert {r['username'] for r in only_p1} == {'alice', 'bob'}

    alice_p1 = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, username='alice', project_code='P1',
    )
    assert len(alice_p1) == 1
    assert alice_p1[0]['core_hours'] == pytest.approx(64 * 300.0 / 3600.0)


def test_unknown_user_or_project_returns_empty(status_session):
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    _make_derecho(status_session, t0)
    _make_derecho(status_session, t0 + timedelta(minutes=5))
    status_session.flush()

    assert get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t0 + timedelta(minutes=5),
        username='nobody-here',
    ) == []
    assert get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t0 + timedelta(minutes=5),
        project_code='no-such-project',
    ) == []


def test_exclude_unknown_project(status_session):
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    p0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='alice', project='SCSG0001', cores=64)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='bob', project='_unknown_', cores=128)
    status_session.flush()

    with_unknown = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1,
    )
    assert {r['project_code'] for r in with_unknown} == {'SCSG0001', '_unknown_'}

    without = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1, exclude_unknown_project=True,
    )
    assert {r['project_code'] for r in without} == {'SCSG0001'}


def test_system_filter_isolates_derecho_from_casper(status_session):
    """A user on derecho and casper at the same timestamp must not
    cross-contaminate when querying one system."""
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    pd0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    pc0 = _make_casper(status_session, t0)
    _make_casper(status_session, t1)
    _make_upq(status_session, pd0, system='derecho', queue='main',
              user='alice', project='P1', cores=64)
    _make_upq(status_session, pc0, system='casper', queue='htc',
              user='alice', project='P1', cores=999)
    status_session.flush()

    only_d = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1,
    )
    assert len(only_d) == 1
    assert only_d[0]['system'] == 'derecho'
    assert only_d[0]['core_hours'] == pytest.approx(64 * 300.0 / 3600.0)

    only_c = get_user_proj_usage(
        status_session, system='casper',
        start_date=t0, end_date=t1,
    )
    assert len(only_c) == 1
    assert only_c[0]['system'] == 'casper'
    assert only_c[0]['core_hours'] == pytest.approx(999 * 300.0 / 3600.0)


def test_invalid_system_raises(status_session):
    with pytest.raises(ValueError):
        get_user_proj_usage(
            status_session, system='no-such-system',
            start_date=datetime(2026, 5, 4),
            end_date=datetime(2026, 5, 5),
        )


# ---------------------------------------------------------------------------
# Multi-chunk windows — exercises the streaming aggregator merge path
# ---------------------------------------------------------------------------

def test_chunk_size_is_invariant(status_session):
    """The aggregator must yield identical results regardless of
    chunk_days — different values just trade memory for query count."""
    t0 = datetime(2026, 5, 1, 0, 0, 0)
    # Span 4 days at 6-hour spacing → forces ≥4 chunks at chunk_days=1.
    ticks = [t0 + timedelta(hours=6 * i) for i in range(17)]
    parents = [_make_derecho(status_session, t) for t in ticks]
    for i, p in enumerate(parents):
        cores = 64 if i % 2 == 0 else 128
        _make_upq(status_session, p, system='derecho', queue='main',
                  user='benkirk', project='SCSG0001', cores=cores)
    status_session.flush()

    one_chunk = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=ticks[-1], chunk_days=30,
    )
    multi_chunk = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=ticks[-1], chunk_days=1,
    )
    assert len(one_chunk) == 1
    assert len(multi_chunk) == 1
    assert one_chunk[0]['core_hours'] == pytest.approx(multi_chunk[0]['core_hours'])
    assert one_chunk[0]['tick_count'] == multi_chunk[0]['tick_count']
    assert one_chunk[0]['first_seen'] == multi_chunk[0]['first_seen']
    assert one_chunk[0]['last_seen'] == multi_chunk[0]['last_seen']


def test_multi_day_window_with_small_chunks(status_session):
    """Force multiple chunks by spanning >2 days with chunk_days=1.

    Verifies that first_seen/last_seen/tick_count merge correctly across
    chunks where the SAME tuple appears in multiple chunks.
    """
    t0 = datetime(2026, 5, 1, 0, 0, 0)
    # 3 ticks per day for 3 days = 9 ticks, all benkirk@SCSG0001 with 100 cores
    ticks = []
    for day in range(3):
        for hour in (0, 8, 16):
            ticks.append(t0 + timedelta(days=day, hours=hour))
    parents = [_make_derecho(status_session, t) for t in ticks]
    for p in parents:
        _make_upq(status_session, p, system='derecho', queue='main',
                  user='benkirk', project='SCSG0001', cores=100)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=ticks[0], end_date=ticks[-1], chunk_days=1,
    )
    assert len(out) == 1
    r = out[0]
    assert r['tick_count'] == 9
    assert r['first_seen'] == ticks[0]
    assert r['last_seen'] == ticks[-1]
    # 8 intervals of 8 hours each (last tick contributes 0)
    expected = 100 * 8 * 8 * 3600.0 / 3600.0   # cores × intervals × seconds / 3600
    assert r['core_hours'] == pytest.approx(expected)


def test_results_sorted_by_core_hours_desc(status_session):
    t0 = datetime(2026, 5, 4, 12, 0, 0)
    t1 = t0 + timedelta(minutes=5)
    p0 = _make_derecho(status_session, t0)
    _make_derecho(status_session, t1)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='small', project='P1', cores=10)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='big', project='P1', cores=1000)
    _make_upq(status_session, p0, system='derecho', queue='main',
              user='medium', project='P1', cores=100)
    status_session.flush()

    out = get_user_proj_usage(
        status_session, system='derecho',
        start_date=t0, end_date=t1,
    )
    assert [r['username'] for r in out] == ['big', 'medium', 'small']

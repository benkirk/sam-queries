#!/usr/bin/env python3
"""
Validate ``get_user_proj_usage`` against the local system_status DB.

Designed for the local dev DB which has <24 h of real collector data
including missed Derecho ticks (gaps > 5 min). The variable-dt path
of the integrator is exercised by real organic non-uniformity, not
hand-crafted fixtures.

Run from the repo host (not inside the docker network):

    source etc/config_env.sh
    STATUS_DB_DRIVER=mysql STATUS_DB_SERVER=127.0.0.1 \
        python scripts/validate_user_proj_usage.py

The env overrides are because the default .env points at the
docker-compose-internal `mysql` hostname / postgres driver, neither of
which resolves from the host. The container's own port 3306 is exposed
on the host loopback.
"""

import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

# Allow running the script from anywhere in the repo.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / 'src'))

from system_status import (   # noqa: E402
    create_status_engine,
    get_session,
    DerechoStatus,
    CasperStatus,
    QueueStatus,
    UserProjQueueStatus,
    System,
    QueueDef,
)
from sqlalchemy import func   # noqa: E402
from system_status.queries import get_user_proj_usage   # noqa: E402


GAP_THRESHOLD_S = 360   # anything > 6 min is a "gap" worth surfacing


# --------------------------------------------------------------------------
# Tick distribution
# --------------------------------------------------------------------------

def get_upu_window(session, system_id):
    """Return (start, end) of UserProjQueueStatus rows for ``system_id``.

    user_proj_queue_status is the new Phase-B table — it has *much*
    less data than the parent status tables. Reconcile only over its
    actual coverage window, otherwise QueueStatus integrates 5 months
    of activity that has no upu rows to compare against.
    """
    row = session.execute(
        select(func.min(UserProjQueueStatus.timestamp),
               func.max(UserProjQueueStatus.timestamp))
        .where(UserProjQueueStatus.system_id == system_id)
    ).one()
    return row[0], row[1]


def report_tick_distribution(session, parent_model, label, start=None, end=None):
    q = select(parent_model.timestamp)
    if start is not None:
        q = q.where(parent_model.timestamp >= start)
    if end is not None:
        q = q.where(parent_model.timestamp <= end)
    q = q.order_by(parent_model.timestamp)
    rows = session.execute(q).all()
    ts = [r[0] for r in rows]
    if len(ts) < 2:
        print(f'  {label}: <2 ticks, skipping')
        return None

    arr = np.array(ts, dtype='datetime64[s]')
    dt = np.diff(arr).astype('int64')
    span_h = (ts[-1] - ts[0]).total_seconds() / 3600
    print(f'  {label}: {len(ts)} ticks across {span_h:.2f} h '
          f'[{ts[0]} → {ts[-1]}]')
    print(f'    dt seconds: '
          f'min={dt.min()}  '
          f'p50={int(np.median(dt))}  '
          f'p95={int(np.percentile(dt, 95))}  '
          f'max={dt.max()}')
    gaps = dt[dt > GAP_THRESHOLD_S]
    if gaps.size:
        unique = sorted(set(gaps.tolist()))
        print(f'    {gaps.size} gaps > {GAP_THRESHOLD_S}s; '
              f'distinct gap lengths (first 10): {unique[:10]}')
    else:
        print(f'    no gaps > {GAP_THRESHOLD_S}s — uniform 5-min cadence')
    return ts


# --------------------------------------------------------------------------
# Reconciliation: integrate QueueStatus directly, compare to summed
# get_user_proj_usage by queue.
# --------------------------------------------------------------------------

def integrate_queue_status_by_queue(session, parent_model, system, start, end):
    """Left-step integrate QueueStatus.cores_allocated / gpus_allocated
    by queue over [start, end], using the **parent tick set** as the
    timeline (same source as get_user_proj_usage uses internally) so
    the dt[i] values align byte-for-byte and the comparison is
    apples-to-apples.

    Per-queue series is filled with 0 at parent ticks where the queue
    has no QueueStatus row.

    Returns: dict[queue_name] -> (core_hours, gpu_hours).
    """
    parent_rows = session.execute(
        select(parent_model.timestamp)
        .where(parent_model.timestamp >= start,
               parent_model.timestamp <= end)
        .order_by(parent_model.timestamp)
    ).all()
    parent_ts = [r[0] for r in parent_rows]
    if len(parent_ts) < 2:
        return {}

    ticks = np.array(parent_ts, dtype='datetime64[us]')
    n_ticks = ticks.size
    dt_s = np.zeros(n_ticks, dtype=np.float64)
    dt_s[:-1] = np.diff(ticks).astype('int64').astype(np.float64) / 1e6
    ts_to_idx = {t: i for i, t in enumerate(parent_ts)}

    qs_rows = session.execute(
        select(QueueStatus.timestamp,
               QueueDef.name,
               QueueStatus.cores_allocated,
               QueueStatus.gpus_allocated)
        .join(QueueDef, QueueStatus.queue_id == QueueDef.queue_id)
        .join(System, QueueStatus.system_id == System.system_id)
        .where(System.name == system,
               QueueStatus.timestamp >= start,
               QueueStatus.timestamp <= end)
    ).all()

    # Aggregate per (queue, tick_idx). Skip QueueStatus rows whose
    # timestamp doesn't fall on a parent tick (defensive — should never
    # happen given the per-tick invariant).
    out_core = {}
    out_gpu = {}
    skipped = 0
    for ts, qname, cores, gpus in qs_rows:
        i = ts_to_idx.get(ts)
        if i is None:
            skipped += 1
            continue
        c = (cores or 0) * dt_s[i]
        g = (gpus or 0) * dt_s[i]
        out_core[qname] = out_core.get(qname, 0.0) + float(c)
        out_gpu[qname] = out_gpu.get(qname, 0.0) + float(g)
    if skipped:
        print(f'    NOTE: {skipped} QueueStatus rows had no matching '
              f'parent tick (alignment mismatch)')
    SEC_PER_HOUR = 3600.0
    return {qn: (out_core.get(qn, 0.0) / SEC_PER_HOUR,
                 out_gpu.get(qn, 0.0) / SEC_PER_HOUR)
            for qn in set(out_core) | set(out_gpu)}


def reconcile(session, parent_model, system, start, end):
    """Compare per-queue QueueStatus integrals against summed
    get_user_proj_usage on each queue. Print PASS/FAIL with delta."""
    qs_totals = integrate_queue_status_by_queue(
        session, parent_model, system, start, end,
    )

    upu_rows = get_user_proj_usage(
        session, system=system, start_date=start, end_date=end,
    )
    upu_totals = {}
    for r in upu_rows:
        qn = r['queue_name']
        prev = upu_totals.get(qn, (0.0, 0.0))
        upu_totals[qn] = (prev[0] + r['core_hours'],
                          prev[1] + r['gpu_hours'])

    # Union of queue names — flag any queue that appears in only one source.
    all_queues = sorted(set(qs_totals) | set(upu_totals))
    if not all_queues:
        print('    no queues to reconcile')
        return

    overall_pass = True
    print(f'    {"queue":<14} {"qs core_h":>12} {"upu core_h":>12} '
          f'{"Δ":>10}  {"qs gpu_h":>10} {"upu gpu_h":>10} {"Δ":>8}  '
          f'verdict')
    for qn in all_queues:
        qs_c, qs_g = qs_totals.get(qn, (0.0, 0.0))
        upu_c, upu_g = upu_totals.get(qn, (0.0, 0.0))
        d_c = upu_c - qs_c
        d_g = upu_g - qs_g
        denom = max(abs(qs_c), abs(upu_c), 1.0)
        ok = (abs(d_c) / denom < 1e-6) and (abs(d_g) / max(abs(qs_g), abs(upu_g), 1.0) < 1e-6)
        verdict = 'PASS' if ok else 'FAIL'
        if not ok:
            overall_pass = False
        print(f'    {qn:<14} {qs_c:>12.3f} {upu_c:>12.3f} {d_c:>+10.3e}  '
              f'{qs_g:>10.3f} {upu_g:>10.3f} {d_g:>+8.3e}  {verdict}')
    print(f'    Overall: {"PASS" if overall_pass else "FAIL"}')


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def report_top_consumers(rows, label, limit=10):
    print(f'\n  Top {min(limit, len(rows))} {label} by core-hours:')
    print(f'    {"username":>14} {"project":<10} {"queue":<10} '
          f'{"core_h":>12} {"gpu_h":>10} {"node_h":>10} {"ticks":>6}')
    for r in rows[:limit]:
        print(f'    {(r["username"] or "?"):>14} '
              f'{(r["project_code"] or "?"):<10} '
              f'{(r["queue_name"] or "?"):<10} '
              f'{r["core_hours"]:>12.3f} '
              f'{r["gpu_hours"]:>10.3f} '
              f'{r["node_hours"]:>10.3f} '
              f'{r["tick_count"]:>6}')


def _resolve_system_id(session, name):
    return session.execute(
        select(System.system_id).where(System.name == name)
    ).scalar_one_or_none()


def main():
    engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as session:
        for system, parent in (('derecho', DerechoStatus),
                               ('casper', CasperStatus)):
            sys_id = _resolve_system_id(session, system)
            if sys_id is None:
                print(f'\n=== {system}: not in DB, skipping ===')
                continue
            start, end = get_upu_window(session, sys_id)
            if start is None:
                print(f'\n=== {system}: no user_proj_queue_status rows, '
                      f'skipping ===')
                continue

            span_h = (end - start).total_seconds() / 3600
            print()
            print('=' * 72)
            print(f'{system}: upu window {start} → {end} ({span_h:.2f} h)')
            print('=' * 72)
            report_tick_distribution(session, parent, f'{system} parent ticks',
                                     start=start, end=end)

            rows = get_user_proj_usage(
                session, system=system,
                start_date=start, end_date=end,
            )
            print(f'\n  {len(rows)} (user, project, queue) tuples with usage > 0')
            print(f'  Total core-hours: '
                  f'{sum(r["core_hours"] for r in rows):>14.3f}')
            print(f'  Total GPU-hours:  '
                  f'{sum(r["gpu_hours"] for r in rows):>14.3f}')
            print(f'  Total node-hours: '
                  f'{sum(r["node_hours"] for r in rows):>14.3f}')

            report_top_consumers(rows, f'{system} consumers')

            print(f'\n  Reconciliation against QueueStatus integrals:')
            reconcile(session, parent, system, start, end)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Validate + benchmark ``get_user_proj_usage`` against the local
system_status DB.

This script does triple duty:

1. **Correctness** — reconciles the integrated user_proj output against
   the integral of the parent ``QueueStatus`` rows over the SAME parent
   tick set. Equality up to float64 epsilon on every queue is the
   load-bearing assertion that the algorithm is sound.

2. **Benchmark / capacity-planning** — prints wall-clock timing and
   rows/sec for each phase (tick fetch, integration, reconciliation).
   The intent is to **revisit this on a cadence** as upu rows accumulate.
   Under PR #248 spans the absolute row count is suppressed by the
   coalescing compression ratio, so the threshold conversation is now
   about (rows × compression) rather than ticks × tuples. Sub-window
   benchmarks (1 h / 6 h / 24 h) provide the growth-shape signal for
   the daily-summary table decision.

   Each timed phase prints rows processed and wall-clock seconds, so a
   `git diff` of two runs can answer "did we cross the line yet?"
   without re-deriving anything.

3. **Span-coalescing health (PR #248)** — compression ratio vs naive
   per-tick equivalent, span tick-coverage histogram, extension hit
   rate, and spans-per-tuple distribution. Quantifies the refactor's
   payoff at the table level, separate from the per-query benchmarks.

## What "the daily-summary decision" hinges on

The design plan flagged a future
`user_proj_queue_daily_usage` rollup as the right answer if year-scale
queries become a UI need. The right time to build it is when:
- A typical UI request window (e.g. last 7 days) takes >1 s consistently.
- A reporting window (1 month) takes >30 s, blocking offline jobs.
- DB row growth makes the chunked Python aggregation memory-bound on
  the smallest deployable host.

This script is the instrument we'll measure those thresholds with.

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
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table
from rich import box
from sqlalchemy import select

console = Console()


@contextmanager
def timed(label, *, n=None):
    """Context manager that prints elapsed wall time, and rows/sec if
    a row count is given. Output is a single right-aligned line so
    consecutive timings stack visually for comparison run-to-run."""
    t0 = time.perf_counter()
    yield
    dt = time.perf_counter() - t0
    if n is None:
        print(f'    [t]  {label:<45} {dt:>8.3f} s')
    else:
        rate = (n / dt) if dt > 0 else float('inf')
        print(f'    [t]  {label:<45} {dt:>8.3f} s  '
              f'({n:>9,} rows, {rate:>10,.0f} rows/s)')

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
    UserDef,
    ProjectCodeDef,
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
    with timed(f'fetch parent ticks ({label})'):
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
# Span-coalescing health (PR #248)
# --------------------------------------------------------------------------

def report_span_statistics(session, system_id, parent_ts, start, end, n_rows):
    """Coalescer effectiveness + span-shape report for one system+window.

    Quantifies whether the span refactor is paying off: compression
    ratio, tick-coverage distribution, extension hit rate,
    spans-per-tuple distribution, and the most-coalesced tuples.
    Output is rich Tables; querying is dialect-portable so this also
    runs against prod Postgres.
    """
    if n_rows == 0 or parent_ts is None or len(parent_ts) < 2:
        console.print('[dim]  span statistics: no rows in window, skipping[/dim]')
        return

    with timed('fetch span endpoints + keys'):
        rows = session.execute(
            select(UserProjQueueStatus.timestamp,
                   UserProjQueueStatus.last_seen,
                   UserProjQueueStatus.user_id,
                   UserProjQueueStatus.project_code_id,
                   UserProjQueueStatus.queue_id)
            .where(UserProjQueueStatus.system_id == system_id,
                   UserProjQueueStatus.timestamp >= start,
                   UserProjQueueStatus.timestamp <= end)
        ).all()

    ticks = np.array(parent_ts, dtype='datetime64[us]')
    fs = np.array([r[0] for r in rows], dtype='datetime64[us]')
    ls = np.array([r[1] for r in rows], dtype='datetime64[us]')
    uids = np.array([r[2] for r in rows], dtype=np.int64)
    pids = np.array([r[3] for r in rows], dtype=np.int64)
    qids = np.array([r[4] for r in rows], dtype=np.int64)

    i_first = np.searchsorted(ticks, fs, side='left')
    i_last = np.searchsorted(ticks, ls, side='right') - 1
    coverage = np.clip(i_last - i_first + 1, 1, None)
    naive = int(coverage.sum())
    ratio = naive / n_rows if n_rows else 0
    extended = int((ls > fs).sum())

    # 1. Headline KPI table.
    t = Table(title='Coalescing compression', box=box.SIMPLE_HEAVY,
              show_header=False)
    t.add_column('metric', justify='left', style='cyan')
    t.add_column('value', justify='right', style='bold')
    t.add_row('actual rows', f'{n_rows:,}')
    t.add_row('naive (per-tick) equivalent', f'{naive:,}')
    t.add_row('compression ratio', f'{ratio:.2f}×')
    t.add_row('extension hit rate',
              f'{extended:,} / {n_rows:,} ({100*extended/n_rows:.1f}%)')
    console.print(t)

    # 2. Span tick-coverage histogram.
    cov_buckets = [(1, 1, '1 (degenerate)'),
                   (2, 2, '2'),
                   (3, 3, '3'),
                   (4, 9, '4–9'),
                   (10, 49, '10–49'),
                   (50, None, '50+')]
    t = Table(title='Span tick-coverage distribution', box=box.SIMPLE_HEAVY)
    t.add_column('ticks', justify='right')
    t.add_column('count', justify='right')
    t.add_column('%', justify='right', style='dim')
    for lo, hi, label in cov_buckets:
        upper = hi if hi is not None else 10**9
        n = int(((coverage >= lo) & (coverage <= upper)).sum())
        t.add_row(label, f'{n:,}', f'{100*n/n_rows:.1f}')
    console.print(t)

    # 3. Spans-per-tuple histogram.
    keys = np.stack([uids, pids, qids], axis=1)
    _, counts = np.unique(keys, axis=0, return_counts=True)
    spt_buckets = [(1, 1, '1'),
                   (2, 2, '2'),
                   (3, 3, '3'),
                   (4, 5, '4–5'),
                   (6, 10, '6–10'),
                   (11, None, '11+')]
    t = Table(title=f'Spans per (user, project, queue) tuple  '
                    f'[{counts.size:,} tuples, mean={counts.mean():.2f}]',
              box=box.SIMPLE_HEAVY)
    t.add_column('spans', justify='right')
    t.add_column('count', justify='right')
    t.add_column('%', justify='right', style='dim')
    for lo, hi, label in spt_buckets:
        upper = hi if hi is not None else 10**9
        n = int(((counts >= lo) & (counts <= upper)).sum())
        t.add_row(label, f'{n:,}', f'{100*n/counts.size:.1f}')
    console.print(t)

    # 4. Top-5 most-coalesced tuples — max single-span coverage per tuple.
    best = {}
    for u, p, q, c in zip(uids.tolist(), pids.tolist(), qids.tolist(),
                          coverage.tolist()):
        key = (u, p, q)
        if best.get(key, 0) < c:
            best[key] = c
    top = sorted(best.items(), key=lambda kv: -kv[1])[:5]
    if not top:
        return
    uids_top = {k[0] for k, _ in top}
    pids_top = {k[1] for k, _ in top}
    qids_top = {k[2] for k, _ in top}
    u_map = dict(session.execute(
        select(UserDef.user_id, UserDef.username)
        .where(UserDef.user_id.in_(uids_top))).all())
    p_map = dict(session.execute(
        select(ProjectCodeDef.project_code_id, ProjectCodeDef.project_code)
        .where(ProjectCodeDef.project_code_id.in_(pids_top))).all())
    q_map = dict(session.execute(
        select(QueueDef.queue_id, QueueDef.name)
        .where(QueueDef.queue_id.in_(qids_top))).all())
    t = Table(title='Top 5 longest single spans',
              caption='most-coalesced workloads',
              box=box.SIMPLE_HEAVY)
    t.add_column('user', justify='right')
    t.add_column('project', justify='left')
    t.add_column('queue', justify='left')
    t.add_column('coverage', justify='right', style='bold')
    for (u, p, q), cov in top:
        t.add_row(u_map.get(u) or '?',
                  p_map.get(p) or '?',
                  q_map.get(q) or '?',
                  f'{cov} ticks')
    console.print(t)


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

    with timed(f'fetch QueueStatus rows ({system})'):
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
    with timed(f'reconcile QueueStatus integral ({system})'):
        qs_totals = integrate_queue_status_by_queue(
            session, parent_model, system, start, end,
        )

    with timed(f'get_user_proj_usage full window ({system})'):
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
            console.print()
            console.rule(
                f'[bold cyan]{system}[/bold cyan]: upu window '
                f'{start} → {end} ({span_h:.2f} h)',
                style='cyan',
            )
            parent_ts = report_tick_distribution(
                session, parent, f'{system} parent ticks',
                start=start, end=end,
            )

            # Count input rows in the window — denominator for rows/sec.
            n_rows = session.execute(
                select(func.count())
                .select_from(UserProjQueueStatus)
                .where(UserProjQueueStatus.system_id == sys_id,
                       UserProjQueueStatus.timestamp >= start,
                       UserProjQueueStatus.timestamp <= end)
            ).scalar_one()

            console.print()
            report_span_statistics(session, sys_id, parent_ts, start, end, n_rows)

            with timed(f'get_user_proj_usage full window ({system})',
                       n=n_rows):
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

            # Sub-window benchmarks. The growth shape vs the full-window
            # number tells us whether the integration scales linearly
            # (expected) and how much headroom exists before any one
            # query class blows past the UI/offline thresholds. Revisit
            # the absolute numbers as upu accumulates more data.
            print(f'\n  Sub-window benchmarks (extrapolation feedstock):')
            for label, hours in (('1 h', 1), ('6 h', 6), ('24 h (or full)', 24)):
                w_end = end
                w_start = max(start, end - timedelta(hours=hours))
                if w_start >= w_end:
                    continue
                w_n = session.execute(
                    select(func.count())
                    .select_from(UserProjQueueStatus)
                    .where(UserProjQueueStatus.system_id == sys_id,
                           UserProjQueueStatus.timestamp >= w_start,
                           UserProjQueueStatus.timestamp <= w_end)
                ).scalar_one()
                with timed(f'  trailing {label}', n=w_n):
                    get_user_proj_usage(
                        session, system=system,
                        start_date=w_start, end_date=w_end,
                    )

            print(f'\n  Reconciliation against QueueStatus integrals:')
            reconcile(session, parent, system, start, end)


if __name__ == '__main__':
    main()

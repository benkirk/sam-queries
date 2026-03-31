"""
Profiling script for the allocations dashboard index route.

Profiles two scenarios (show_usage=False and show_usage=True) with all
caches bypassed to expose true hotspots.  Outputs:
  - Per-phase wall-clock timing
  - SQL query count + total time (via SQLAlchemy cursor events)
  - Top-10 slowest SQL statements
  - cProfile top-20 functions by cumulative time
  - line_profiler on get_allocation_summary_with_usage() if installed

Usage:
    source etc/config_env.sh
    pip install line_profiler   # optional
    python profile_allocations.py 2>&1 | tee profile_output.txt
"""

# ---------------------------------------------------------------------------
# Must happen before any webapp/sam imports so env vars are visible to module
# initializers (especially usage_cache._get_cache()).
# ---------------------------------------------------------------------------
import os
os.environ.setdefault('FLASK_CONFIG', 'development')
os.environ.setdefault('FLASK_SECRET_KEY', 'profiling-key-not-for-production')
os.environ.setdefault('AUDIT_ENABLED', '0')               # silence before_flush noise
os.environ.setdefault('ALLOCATION_USAGE_CACHE_TTL',  '0') # disable TTLCache entirely
os.environ.setdefault('ALLOCATION_USAGE_CACHE_SIZE', '0')

import cProfile
import io
import pstats
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List

import sqlalchemy
from sqlalchemy import event

# ---------------------------------------------------------------------------
# Path setup — script lives at project root, src/ is the package root
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'src'))

# Flask app
from webapp.run import create_app
from webapp.extensions import db

# Query functions (called directly, bypassing @cache.cached on the route)
from sam.queries.allocations import get_allocation_summary, get_allocation_summary_with_usage, _aggregate_usage_to_total
from sam.queries.usage_cache import cached_allocation_usage
from sam.resources.resources import Resource

# Blueprint helpers (plain functions, no decorators)
from webapp.dashboards.allocations.blueprint import (
    HIDDEN_RESOURCES,
    get_all_facility_overviews,
    get_all_facility_usage_overviews,
    get_resource_types,
    group_by_resource_facility,
)

# Chart functions — public wrappers have .cache_clear() via _attach_cache_methods()
from webapp.dashboards.charts import (
    generate_allocation_type_pie_chart_matplotlib,
    generate_facility_pie_chart_matplotlib,
)

# Optional: line_profiler for line-by-line breakdown of the N+1 hotspot
try:
    from line_profiler import LineProfiler
    HAS_LINE_PROFILER = True
except ImportError:
    HAS_LINE_PROFILER = False


# ---------------------------------------------------------------------------
# SQL instrumentation
# ---------------------------------------------------------------------------

class _SQLStats:
    """Accumulate SQL query count and wall-clock time via Engine cursor events."""

    def __init__(self):
        self._t: Dict[int, float] = {}
        self.reset()

    def reset(self):
        self.count = 0
        self.total_time = 0.0
        self.slowest: List[tuple] = []   # [(elapsed, sql_fragment), ...]
        self._t.clear()

    # SQLAlchemy event handlers -------------------------------------------
    def before(self, conn, cursor, statement, parameters, context, executemany):
        self._t[id(conn)] = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        elapsed = time.perf_counter() - self._t.pop(id(conn), time.perf_counter())
        self.count += 1
        self.total_time += elapsed
        self.slowest.append((elapsed, statement.strip()[:140]))
        self.slowest.sort(reverse=True)
        self.slowest = self.slowest[:10]


_sql = _SQLStats()


def _attach(engine):
    event.listen(engine, 'before_cursor_execute', _sql.before)
    event.listen(engine, 'after_cursor_execute',  _sql.after)


def _detach(engine):
    event.remove(engine, 'before_cursor_execute', _sql.before)
    event.remove(engine, 'after_cursor_execute',  _sql.after)


# ---------------------------------------------------------------------------
# cProfile helper
# ---------------------------------------------------------------------------

@contextmanager
def _cprofiled():
    pr = cProfile.Profile()
    pr.enable()
    yield pr
    pr.disable()


def _print_cprofile(pr, label, top_n=20):
    buf = io.StringIO()
    ps = pstats.Stats(pr, stream=buf).sort_stats('cumulative')
    ps.print_stats(top_n)
    print(f"\n{'='*72}")
    print(f"cProfile — {label}  (top {top_n} by cumtime)")
    print('='*72)
    print(buf.getvalue())


# ---------------------------------------------------------------------------
# Scenario functions
# ---------------------------------------------------------------------------

def _clear_chart_caches():
    generate_facility_pie_chart_matplotlib.cache_clear()
    generate_allocation_type_pie_chart_matplotlib.cache_clear()


def run_scenario_no_usage(session, selected_resources, active_at) -> Dict[str, float]:
    """Mirror blueprint index() with show_usage=False, phase-timed."""
    phases: Dict[str, float] = {}
    t0 = time.perf_counter()

    # Phase 1 — main allocation summary
    t = time.perf_counter()
    summary_data = get_allocation_summary(
        session=session,
        resource_name=selected_resources,
        facility_name=None,
        allocation_type=None,
        projcode='TOTAL',
        active_only=True,
        active_at=active_at,
    )
    phases['get_allocation_summary (projcode=TOTAL)'] = time.perf_counter() - t

    # Phase 2 — resource metadata
    t = time.perf_counter()
    all_resources = [
        r.resource_name for r in
        session.query(Resource.resource_name)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name).all()
    ]
    resource_types = get_resource_types(session)
    phases['resource metadata queries'] = time.perf_counter() - t

    # Phase 3 — Python grouping (no DB)
    t = time.perf_counter()
    grouped_data = group_by_resource_facility(summary_data)
    phases['group_by_resource_facility (Python only)'] = time.perf_counter() - t

    # Phase 4 — facility overviews (2nd get_allocation_summary call)
    t = time.perf_counter()
    all_overviews, type_annualized_rates = get_all_facility_overviews(
        session, list(grouped_data.keys()), active_at
    )
    phases['get_all_facility_overviews  [2nd get_allocation_summary]'] = time.perf_counter() - t

    # Phase 5 — facility pie charts (cold lru_cache)
    _clear_chart_caches()
    t = time.perf_counter()
    resource_overviews = {}
    for rn in grouped_data.keys():
        overview_data = all_overviews.get(rn, [])
        rt = resource_types.get(rn, 'HPC')
        title = ('Data Volume by Facility\n' if rt in ('DISK', 'ARCHIVE')
                 else 'Annual Rate by Facility\n') + rn
        resource_overviews[rn] = {
            'table_data': overview_data,
            'chart': generate_facility_pie_chart_matplotlib(overview_data, title=title),
        }
    phases['facility pie chart generation (lru cold)'] = time.perf_counter() - t

    # Phase 6 — allocation type pie charts
    t = time.perf_counter()
    for resource_name, facilities in grouped_data.items():
        rt = resource_types.get(resource_name, 'HPC')
        for facility_name, types in facilities.items():
            if len(types) > 1:
                generate_allocation_type_pie_chart_matplotlib(
                    types, rt, resource_name, facility_name
                )
    phases['alloc-type pie chart generation (lru cold)'] = time.perf_counter() - t

    phases['TOTAL'] = time.perf_counter() - t0
    return phases, grouped_data, resource_types


def run_scenario_with_usage(session, selected_resources, active_at) -> Dict[str, float]:
    """Mirror blueprint index() with show_usage=True, phase-timed."""
    phases: Dict[str, float] = {}
    t0 = time.perf_counter()

    # Phases 1-6 identical to no-usage scenario -------------------------
    t = time.perf_counter()
    summary_data = get_allocation_summary(
        session=session,
        resource_name=selected_resources,
        facility_name=None,
        allocation_type=None,
        projcode='TOTAL',
        active_only=True,
        active_at=active_at,
    )
    phases['get_allocation_summary (projcode=TOTAL)'] = time.perf_counter() - t

    t = time.perf_counter()
    resource_types = get_resource_types(session)
    phases['resource metadata queries'] = time.perf_counter() - t

    t = time.perf_counter()
    grouped_data = group_by_resource_facility(summary_data)
    phases['group_by_resource_facility (Python only)'] = time.perf_counter() - t

    t = time.perf_counter()
    all_overviews, type_annualized_rates = get_all_facility_overviews(
        session, list(grouped_data.keys()), active_at
    )
    phases['get_all_facility_overviews  [2nd get_allocation_summary]'] = time.perf_counter() - t

    _clear_chart_caches()
    t = time.perf_counter()
    resource_overviews = {}
    for rn in grouped_data.keys():
        overview_data = all_overviews.get(rn, [])
        rt = resource_types.get(rn, 'HPC')
        title = ('Data Volume by Facility\n' if rt in ('DISK', 'ARCHIVE')
                 else 'Annual Rate by Facility\n') + rn
        resource_overviews[rn] = {
            'table_data': overview_data,
            'chart': generate_facility_pie_chart_matplotlib(overview_data, title=title),
        }
    phases['facility pie chart generation (lru cold)'] = time.perf_counter() - t

    t = time.perf_counter()
    for resource_name, facilities in grouped_data.items():
        rt = resource_types.get(resource_name, 'HPC')
        for facility_name, types in facilities.items():
            if len(types) > 1:
                generate_allocation_type_pie_chart_matplotlib(
                    types, rt, resource_name, facility_name
                )
    phases['alloc-type pie chart generation (lru cold)'] = time.perf_counter() - t

    # Phase 7 — per-project usage: single fetch (projcode=None), mirrors refactored blueprint
    t = time.perf_counter()
    per_project_usage = cached_allocation_usage(
        session=session,
        resource_name=selected_resources,
        facility_name=None,
        allocation_type=None,
        projcode=None,        # Per-project rows; covers both usage views
        active_only=True,
        active_at=active_at,
        force_refresh=True,   # TTLCache disabled anyway; belt-and-suspenders
    )
    phases['cached_allocation_usage (projcode=None)  [single fetch, per-project rows]'] = time.perf_counter() - t

    # Phase 7b — derive projcode=TOTAL grouping Python-side (no DB call)
    t = time.perf_counter()
    usage_type_data = _aggregate_usage_to_total(per_project_usage)
    phases['_aggregate_usage_to_total (Python aggregation, derives TOTAL grouping)'] = time.perf_counter() - t

    # Phase 8 — usage type pie charts
    _clear_chart_caches()
    t = time.perf_counter()
    usage_by_rf: Dict[str, Dict[str, List]] = {}
    for row in usage_type_data:
        usage_by_rf.setdefault(row['resource'], {}).setdefault(row['facility'], []).append(row)
    allocation_type_usage_charts: Dict[str, Dict] = {}
    for resource_name, facilities in grouped_data.items():
        allocation_type_usage_charts[resource_name] = {}
        rt = resource_types.get(resource_name, 'HPC')
        for facility_name, types in facilities.items():
            usage_rows = usage_by_rf.get(resource_name, {}).get(facility_name, [])
            chartable = [
                {
                    'allocation_type': row['allocation_type'],
                    'total_amount': row.get('total_used', 0.0),
                    'count': row.get('count', 0),
                    'avg_amount': row.get('total_used', 0.0),
                }
                for row in usage_rows if row.get('total_used', 0.0) > 0
            ]
            if len(chartable) > 1:
                generate_allocation_type_pie_chart_matplotlib(
                    chartable, rt, resource_name, facility_name
                )
    phases['usage type pie chart generation'] = time.perf_counter() - t

    # Phase 9 — facility usage overviews from pre-fetched data (no DB call)
    t = time.perf_counter()
    all_usage_overviews = get_all_facility_usage_overviews(
        session, list(grouped_data.keys()), active_at, _usage=per_project_usage
    )
    phases['get_all_facility_usage_overviews (from pre-fetched usage, no DB call)'] = time.perf_counter() - t

    # Phase 10 — usage facility pie charts
    t = time.perf_counter()
    for rn in grouped_data.keys():
        usage_overview_data = all_usage_overviews.get(rn, [])
        chartable = [d for d in usage_overview_data if d.get('total_used', 0.0) > 0]
        if chartable:
            generate_facility_pie_chart_matplotlib(
                chartable, title=f'Usage by Facility\n{rn}'
            )
    phases['usage facility pie chart generation'] = time.perf_counter() - t

    phases['TOTAL'] = time.perf_counter() - t0
    return phases


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _print_report(label: str, phases: Dict[str, float]):
    sql_count   = _sql.count
    sql_time    = _sql.total_time
    total       = phases['TOTAL']
    non_sql     = total - sql_time

    print(f"\n{'#'*72}")
    print(f"SCENARIO: {label}")
    print(f"{'#'*72}")
    print(f"  Total wall time              : {total*1000:9.1f} ms")
    print(f"  SQL queries issued           : {sql_count:9d}")
    print(f"  SQL total time               : {sql_time*1000:9.1f} ms  ({sql_time/total*100:.0f}% of total)")
    print(f"  Non-SQL overhead (Python/charts): {non_sql*1000:7.1f} ms  ({non_sql/total*100:.0f}% of total)")
    print()
    print("  Phase breakdown:")
    for phase, elapsed in phases.items():
        if phase == 'TOTAL':
            continue
        pct = elapsed / total * 100
        bar = '#' * int(pct / 2)
        print(f"    {phase:<58s} {elapsed*1000:7.1f} ms  {pct:4.1f}%  {bar}")
    print()
    print("  Top-10 slowest SQL queries:")
    for i, (t, sql) in enumerate(_sql.slowest[:10], 1):
        print(f"    [{i:2d}] {t*1000:7.1f} ms  {sql!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = create_app()
    with app.app_context():
        engine = db.engine
        _attach(engine)

        active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        all_resources = [
            r.resource_name for r in
            db.session.query(Resource.resource_name)
            .filter(Resource.is_active)
            .order_by(Resource.resource_name).all()
        ]
        selected_resources = [r for r in all_resources if r not in HIDDEN_RESOURCES]

        print(f"Profiling date : {active_at.date()}")
        print(f"Resources      : {selected_resources}")
        print(f"line_profiler  : {'available' if HAS_LINE_PROFILER else 'not installed (pip install line_profiler)'}")

        # ── Scenario 1: show_usage=False ─────────────────────────────────
        _sql.reset()
        _clear_chart_caches()
        with _cprofiled() as pr1:
            phases_no, grouped_data, resource_types = run_scenario_no_usage(
                db.session, selected_resources, active_at
            )
        _print_report("show_usage=False  (cold lru_cache, no TTL cache)", phases_no)
        _print_cprofile(pr1, "show_usage=False")

        # ── Scenario 2: show_usage=True ──────────────────────────────────
        _sql.reset()
        _clear_chart_caches()

        if HAS_LINE_PROFILER:
            lp = LineProfiler()
            lp.add_function(get_allocation_summary_with_usage)
            lp_run = lp(run_scenario_with_usage)

            with _cprofiled() as pr2:
                phases_yes = lp_run(db.session, selected_resources, active_at)

            _print_report("show_usage=True  (cold lru_cache, TTL cache disabled)", phases_yes)
            print("\n--- line_profiler: get_allocation_summary_with_usage() ---")
            lp.print_stats()
            _print_cprofile(pr2, "show_usage=True")
        else:
            with _cprofiled() as pr2:
                phases_yes = run_scenario_with_usage(db.session, selected_resources, active_at)
            _print_report("show_usage=True  (cold lru_cache, TTL cache disabled)", phases_yes)
            _print_cprofile(pr2, "show_usage=True")

        _detach(engine)

        # ── Summary comparison ───────────────────────────────────────────
        print(f"\n{'='*72}")
        print("SUMMARY COMPARISON")
        print(f"{'='*72}")
        print(f"  show_usage=False : {phases_no['TOTAL']*1000:8.1f} ms")
        print(f"  show_usage=True  : {phases_yes['TOTAL']*1000:8.1f} ms")
        overhead = phases_yes['TOTAL'] - phases_no['TOTAL']
        print(f"  Usage overhead   : {overhead*1000:8.1f} ms  ({overhead/phases_no['TOTAL']*100:.0f}x slowdown)")


if __name__ == '__main__':
    main()

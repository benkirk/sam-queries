"""
Profiling script for the user dashboard route (/user/).

Mirrors the structure of profile_admin_orgs.py — fetches data with
get_user_dashboard_data() and renders dashboards/user/dashboard.html in the
same request context so SQLAlchemy lazy loads triggered during template
rendering are visible to the SQL counter.

Default target user is bdobbins (override with --user). bdobbins has broader
project membership than benkirk, which exercises more of the relationship
graph and surfaces N+1 hot paths a thinly-connected user would miss.

Usage:
    source etc/config_env.sh
    python utils/profiling/profile_user_dashboard.py 2>&1 | tee profile_user_dashboard.txt
    python utils/profiling/profile_user_dashboard.py --user benkirk
"""

import argparse
import os
os.environ.setdefault('FLASK_CONFIG', 'development')
os.environ.setdefault('FLASK_SECRET_KEY', 'profiling-key-not-for-production')
os.environ.setdefault('AUDIT_ENABLED', '0')

import cProfile
import io
import pstats
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List

from sqlalchemy import event

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', '..', 'src'))

from webapp.run import create_app
from webapp.extensions import db
from flask import render_template
from flask_login import login_user

from sam.core.users import User
from sam.queries.dashboard import get_user_dashboard_data
from webapp.auth.models import AuthUser


DEFAULT_USERNAME = 'bdobbins'

# Match the constants in src/webapp/dashboards/user/blueprint.py
USAGE_WARNING_THRESHOLD = 75
USAGE_CRITICAL_THRESHOLD = 90


# ---------------------------------------------------------------------------
# SQL instrumentation (same pattern as profile_admin_orgs.py / profile_allocations.py)
# ---------------------------------------------------------------------------

import re
_TABLE_RE = re.compile(r'\bFROM\s+([a-z_][a-z0-9_]*)', re.IGNORECASE)


def _extract_table(statement: str) -> str:
    """Best-effort: pull the first FROM <table> name out of a SQL statement."""
    m = _TABLE_RE.search(statement)
    if m:
        return m.group(1).lower()
    # Fall back to first word (e.g. WITH ... statements)
    return statement.strip().split(None, 1)[0].lower()[:30]


class _SQLStats:
    def __init__(self):
        self._t: Dict[int, float] = {}
        self.reset()

    def reset(self):
        self.count = 0
        self.total_time = 0.0
        self.slowest: List[tuple] = []
        self.by_table: Dict[str, List[float]] = {}
        self._t.clear()

    def before(self, conn, cursor, statement, parameters, context, executemany):
        self._t[id(conn)] = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        elapsed = time.perf_counter() - self._t.pop(id(conn), time.perf_counter())
        self.count += 1
        self.total_time += elapsed
        self.slowest.append((elapsed, statement.strip()[:140]))
        self.slowest.sort(reverse=True)
        self.slowest = self.slowest[:10]

        table = _extract_table(statement)
        # Bucket WITH-anchors CTE batches under a synthetic name
        if statement.strip().lower().startswith('with anchors'):
            table = '<batched WITH anchors CTE>'
        elif statement.strip().lower().startswith('with w '):
            table = '<batched WITH w (rolling) CTE>'
        self.by_table.setdefault(table, []).append(elapsed)


_sql = _SQLStats()


def _attach(engine):
    event.listen(engine, 'before_cursor_execute', _sql.before)
    event.listen(engine, 'after_cursor_execute',  _sql.after)


def _detach(engine):
    event.remove(engine, 'before_cursor_execute', _sql.before)
    event.remove(engine, 'after_cursor_execute',  _sql.after)


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
# Scenario runner — fetch + render in ONE request context so the SQLAlchemy
# session scope is shared and selectinload results remain visible to templates.
# ---------------------------------------------------------------------------

def run_scenario(app, username: str):
    """Mirror /user/ route end-to-end for the given username."""
    phases = {}
    fetch_sql_count = render_sql_count = render_sql_time = 0
    fetch_slowest: List[tuple] = []
    render_slowest: List[tuple] = []
    fetch_by_table: Dict[str, List[float]] = {}
    render_by_table: Dict[str, List[float]] = {}

    with app.test_request_context('/user/'):
        session = db.session

        user = session.query(User).filter_by(username=username).first()
        if user is None:
            raise SystemExit(f"User {username!r} not found in database")

        # login_user populates Flask-Login's current_user proxy so the base
        # template (dashboards/base.html) can access current_user.username,
        # current_user.primary_email, etc., without crashing. Flask-Login
        # requires its UserMixin shape, hence the AuthUser wrapper.
        login_user(AuthUser(user))

        # --- Phase 1: data fetch (get_user_dashboard_data) ---
        _sql.reset()
        t0 = time.perf_counter()
        dashboard_data = get_user_dashboard_data(session, user.user_id)
        phases['get_user_dashboard_data'] = time.perf_counter() - t0
        phases['TOTAL_FETCH'] = phases['get_user_dashboard_data']
        fetch_sql_count = _sql.count
        fetch_slowest = list(_sql.slowest)
        fetch_by_table = dict(_sql.by_table)

        # --- Phase 2: template render (lazy loads fire here) ---
        _sql.reset()
        t = time.perf_counter()
        render_template(
            'dashboards/user/dashboard.html',
            user=user,
            dashboard_data=dashboard_data,
            usage_warning_threshold=USAGE_WARNING_THRESHOLD,
            usage_critical_threshold=USAGE_CRITICAL_THRESHOLD,
            impersonator_id=None,
        )
        phases['TOTAL_RENDER'] = time.perf_counter() - t
        render_sql_count = _sql.count
        render_sql_time = _sql.total_time
        render_slowest = list(_sql.slowest)
        render_by_table = dict(_sql.by_table)

    return (phases, fetch_sql_count, fetch_slowest, fetch_by_table,
            render_sql_count, render_sql_time, render_slowest, render_by_table, user)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_by_table(label, by_table):
    if not by_table:
        return
    rows = sorted(by_table.items(), key=lambda kv: -len(kv[1]))
    print(f"\n  Per-table query distribution ({label}):")
    print(f"    {'table':<40s}  {'count':>5s}  {'total ms':>10s}")
    for table, elapsed_list in rows:
        print(f"    {table:<40s}  {len(elapsed_list):>5d}  "
              f"{sum(elapsed_list)*1000:>9.1f}")


def _print_report(label, phases, fetch_sql, fetch_slowest, fetch_by_table,
                  render_sql, render_sql_time, render_slowest, render_by_table):
    total = phases['TOTAL_FETCH'] + phases['TOTAL_RENDER']

    print(f"\n{'#'*72}")
    print(f"SCENARIO: {label}")
    print(f"{'#'*72}")
    print(f"  Total wall time              : {total*1000:9.1f} ms")
    print(f"  Data fetch SQL queries       : {fetch_sql:9d}")
    print(f"  Template render SQL queries  : {render_sql:9d}   ← lazy loads")
    print(f"  Template render SQL time     : {render_sql_time*1000:7.1f} ms")
    print()
    print("  Phase breakdown:")
    for phase, elapsed in phases.items():
        if phase.startswith('TOTAL'):
            continue
        pct = elapsed / total * 100 if total > 0 else 0.0
        bar = '#' * int(pct / 2)
        print(f"    {phase:<58s} {elapsed*1000:7.1f} ms  {pct:4.1f}%  {bar}")
    print(f"    {'[data fetch total]':<58s} {phases['TOTAL_FETCH']*1000:7.1f} ms")
    print(f"    {'[template render total]':<58s} {phases['TOTAL_RENDER']*1000:7.1f} ms")
    print()
    if fetch_slowest:
        print("  Top-10 slowest SQL queries (data fetch phase):")
        for i, (t, sql) in enumerate(fetch_slowest[:10], 1):
            print(f"    [{i:2d}] {t*1000:7.1f} ms  {sql!r}")
    _print_by_table('data fetch', fetch_by_table)
    if render_slowest:
        print()
        print("  Top-10 slowest SQL queries (template render phase):")
        for i, (t, sql) in enumerate(render_slowest[:10], 1):
            print(f"    [{i:2d}] {t*1000:7.1f} ms  {sql!r}")
    _print_by_table('template render', render_by_table)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument(
        '--user',
        default=DEFAULT_USERNAME,
        help=f"Username to profile (default: {DEFAULT_USERNAME})",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        engine = db.engine
        _attach(engine)

        print(f"Profiling date  : {datetime.now().date()}")
        print(f"Target user     : {args.user}")
        print("NOTE: Fetch + render run in the same request context so")
        print("      SQLAlchemy session-scoped selectinload data is visible")
        print("      to the template renderer.\n")

        with _cprofiled() as pr:
            (phases, fetch_sql, fetch_slowest, fetch_by_table,
             render_sql, render_sql_time, render_slowest, render_by_table,
             user) = run_scenario(app, args.user)

        _print_report(
            f"USER DASHBOARD — {args.user} (user_id={user.user_id})",
            phases, fetch_sql, fetch_slowest, fetch_by_table,
            render_sql, render_sql_time, render_slowest, render_by_table,
        )
        _print_cprofile(pr, f"USER DASHBOARD — {args.user}")

        _detach(engine)

        # Compact summary
        total = phases['TOTAL_FETCH'] + phases['TOTAL_RENDER']
        print(f"\n{'='*72}")
        print("SUMMARY")
        print(f"{'='*72}")
        print(f"  {'Data fetch SQL':<44s}  {fetch_sql:>10d}")
        print(f"  {'Template render SQL (lazy loads)':<44s}  {render_sql:>10d}")
        print(f"  {'Total SQL':<44s}  {fetch_sql + render_sql:>10d}")
        print(f"  {'Total wall time (ms)':<44s}  {total*1000:>10.1f}")


if __name__ == '__main__':
    main()

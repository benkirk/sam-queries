"""
Profiling script for the Admin > Organizations card route.

Profiles two scenarios (baseline vs. with eager-loading fixes) to quantify
N+1 lazy-load hotspots triggered by the Jinja2 template rendering phase.

Unlike the allocations profiler, the dominant cost here is in SQLAlchemy lazy
loads that fire during template rendering. This script renders the template
inside the SAME request context used for data fetching — essential because
Flask-SQLAlchemy scopes its session to the request context, so the pre-loaded
relationship data is only visible to template code running in the same scope.

Usage:
    source etc/config_env.sh
    python utils/profiling/profile_admin_orgs.py 2>&1 | tee profile_admin_orgs.txt
"""

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

import sqlalchemy
from sqlalchemy import event

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', '..', 'src'))

from webapp.run import create_app
from webapp.extensions import db
from flask import render_template


# ---------------------------------------------------------------------------
# SQL instrumentation (same pattern as profile_allocations.py)
# ---------------------------------------------------------------------------

class _SQLStats:
    def __init__(self):
        self._t: Dict[int, float] = {}
        self.reset()

    def reset(self):
        self.count = 0
        self.total_time = 0.0
        self.slowest: List[tuple] = []
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
# Tree build helper (mirrors htmx_organizations_card logic)
# ---------------------------------------------------------------------------

def _build_org_tree(organizations):
    _children = {}
    for _o in organizations:
        _pid = _o.parent_org_id
        _children.setdefault(_pid, []).append(_o)
    for _pid in _children:
        _children[_pid].sort(key=lambda o: o.acronym or '')

    def _dfs(_pid, _depth):
        result = []
        for _o in _children.get(_pid, []):
            _has_ch = bool(_children.get(_o.organization_id))
            result.append((_o, _depth, _has_ch))
            result.extend(_dfs(_o.organization_id, _depth + 1))
        return result

    return _dfs(None, 0)


# ---------------------------------------------------------------------------
# Scenario runners — fetch + render in ONE request context so the SQLAlchemy
# session scope is shared and selectinload results remain visible to templates.
# ---------------------------------------------------------------------------

def run_scenario_baseline(app):
    """Baseline: mirror original code with no extra eager-loading."""
    from sam.core.organizations import Organization, Institution, InstitutionType
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.projects.contracts import Contract, ContractSource, NSFProgram
    from sqlalchemy.orm import subqueryload

    phases = {}
    fetch_sql_count = render_sql_count = render_sql_time = 0
    render_slowest = []

    with app.test_request_context('/admin/htmx/organizations-card'):
        session = db.session

        # --- Phase 1: data fetch ---
        _sql.reset()
        t0 = time.perf_counter()

        t = time.perf_counter()
        organizations = session.query(Organization).options(
            subqueryload(Organization.children)
        ).all()
        org_tree = _build_org_tree(organizations)
        phases['organizations (subqueryload children only)'] = time.perf_counter() - t

        t = time.perf_counter()
        institution_types = session.query(InstitutionType).order_by(InstitutionType.type).all()
        institutions = session.query(Institution).order_by(Institution.name).all()
        phases['institution_types + institutions (no eager loading)'] = time.perf_counter() - t

        t = time.perf_counter()
        aoi_groups = session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        aois = session.query(AreaOfInterest).order_by(AreaOfInterest.area_of_interest).all()
        phases['aoi_groups + aois (no eager loading)'] = time.perf_counter() - t

        t = time.perf_counter()
        contract_sources = session.query(ContractSource).order_by(ContractSource.contract_source).all()
        contracts = session.query(Contract).order_by(Contract.contract_number).all()
        nsf_programs = session.query(NSFProgram).order_by(NSFProgram.nsf_program_name).all()
        phases['contract_sources + contracts + nsf_programs (no eager loading)'] = time.perf_counter() - t

        phases['TOTAL_FETCH'] = time.perf_counter() - t0
        fetch_sql_count = _sql.count

        # --- Phase 2: template render (lazy loads fire here) ---
        _sql.reset()
        t = time.perf_counter()
        render_template(
            'dashboards/admin/fragments/organization_card.html',
            organizations=organizations,
            org_tree=org_tree,
            institution_types=institution_types,
            institutions=institutions,
            aoi_groups=aoi_groups,
            aois=aois,
            contract_sources=contract_sources,
            contracts=contracts,
            nsf_programs=nsf_programs,
            is_admin=True,
            now=datetime.now(),
            active_only=False,
        )
        phases['TOTAL_RENDER'] = time.perf_counter() - t
        render_sql_count = _sql.count
        render_sql_time = _sql.total_time
        render_slowest = list(_sql.slowest)

    return phases, fetch_sql_count, render_sql_count, render_sql_time, render_slowest


def run_scenario_fixed(app):
    """Fixed: full eager-loading chain including cascade suppression."""
    from sam.core.organizations import Organization, Institution, InstitutionType, UserInstitution
    from sam.core.users import User
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.projects.contracts import Contract, ContractSource, NSFProgram
    from sam.projects.projects import Project
    from sqlalchemy.orm import subqueryload, selectinload, lazyload

    phases = {}
    fetch_sql_count = render_sql_count = render_sql_time = 0
    render_slowest = []

    with app.test_request_context('/admin/htmx/organizations-card'):
        session = db.session

        # --- Phase 1: data fetch with full eager-loading chain ---
        _sql.reset()
        t0 = time.perf_counter()

        t = time.perf_counter()
        organizations = session.query(Organization).options(
            subqueryload(Organization.children),
            selectinload(Organization.users),           # org.users|length
        ).all()
        org_tree = _build_org_tree(organizations)
        phases['organizations (subqueryload children + selectinload users)'] = time.perf_counter() - t

        t = time.perf_counter()
        # Template: sort(attribute='user.username') accesses UserInstitution.user.
        # Suppress User.accounts + User.email_addresses (lazy='selectin' cascades).
        # Sibling options on the same entity need separate chains — cannot chain
        # .lazyload(User.email_addresses) after .lazyload(User.accounts).
        institution_types = session.query(InstitutionType).options(
            selectinload(InstitutionType.institutions)
                .selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.accounts),
            selectinload(InstitutionType.institutions)
                .selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.email_addresses),
        ).order_by(InstitutionType.type).all()
        institutions = session.query(Institution).options(
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.accounts),
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.email_addresses),
        ).order_by(Institution.name).all()
        phases['institution_types + institutions (deep chain + cascade suppression)'] = time.perf_counter() - t

        t = time.perf_counter()
        aoi_groups = session.query(AreaOfInterestGroup).options(
            selectinload(AreaOfInterestGroup.areas),    # g.areas|length accessed in template
        ).order_by(AreaOfInterestGroup.name).all()
        aois = session.query(AreaOfInterest).options(
            # Only need len(a.projects); suppress Project.accounts lazy='selectin' cascade
            selectinload(AreaOfInterest.projects).lazyload(Project.accounts),
        ).order_by(AreaOfInterest.area_of_interest).all()
        phases['aoi_groups (selectin areas) + aois (selectin projects, suppress Project.accounts)'] = time.perf_counter() - t

        t = time.perf_counter()
        contract_sources = session.query(ContractSource).order_by(ContractSource.contract_source).all()
        contracts = session.query(Contract).options(
            # Suppress User selectin cascades when loading the PI user
            selectinload(Contract.principal_investigator)
                .lazyload(User.accounts),
            selectinload(Contract.principal_investigator)
                .lazyload(User.email_addresses),
        ).order_by(Contract.contract_number).all()
        nsf_programs = session.query(NSFProgram).options(
            selectinload(NSFProgram.contracts),
        ).order_by(NSFProgram.nsf_program_name).all()
        phases['contract_sources + contracts (selectin PI + cascade suppression) + nsf'] = time.perf_counter() - t

        phases['TOTAL_FETCH'] = time.perf_counter() - t0
        fetch_sql_count = _sql.count

        # --- Phase 2: template render ---
        _sql.reset()
        t = time.perf_counter()
        render_template(
            'dashboards/admin/fragments/organization_card.html',
            organizations=organizations,
            org_tree=org_tree,
            institution_types=institution_types,
            institutions=institutions,
            aoi_groups=aoi_groups,
            aois=aois,
            contract_sources=contract_sources,
            contracts=contracts,
            nsf_programs=nsf_programs,
            is_admin=True,
            now=datetime.now(),
            active_only=False,
        )
        phases['TOTAL_RENDER'] = time.perf_counter() - t
        render_sql_count = _sql.count
        render_sql_time = _sql.total_time
        render_slowest = list(_sql.slowest)

    return phases, fetch_sql_count, render_sql_count, render_sql_time, render_slowest


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_report(label, phases, fetch_sql, render_sql, render_sql_time, render_slowest):
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
        pct = elapsed / total * 100
        bar = '#' * int(pct / 2)
        print(f"    {phase:<62s} {elapsed*1000:7.1f} ms  {pct:4.1f}%  {bar}")
    print(f"    {'[data fetch total]':<62s} {phases['TOTAL_FETCH']*1000:7.1f} ms")
    print(f"    {'[template render total]':<62s} {phases['TOTAL_RENDER']*1000:7.1f} ms")
    print()
    if render_slowest:
        print("  Top-10 slowest SQL queries (template render phase):")
        for i, (t, sql) in enumerate(render_slowest[:10], 1):
            print(f"    [{i:2d}] {t*1000:7.1f} ms  {sql!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = create_app()
    with app.app_context():
        engine = db.engine
        _attach(engine)

        print(f"Profiling date : {datetime.now().date()}")
        print("NOTE: Each scenario runs fetch + render in the same request context")
        print("      so SQLAlchemy session-scoped selectinload data is visible to templates.\n")

        # ── Scenario 1: BASELINE ─────────────────────────────────────────
        with _cprofiled() as pr1:
            phases_base, fetch_sql_base, render_sql_base, \
                render_sql_time_base, render_slowest_base = run_scenario_baseline(app)
        _print_report("BASELINE (no extra eager loading)",
                      phases_base, fetch_sql_base, render_sql_base,
                      render_sql_time_base, render_slowest_base)
        _print_cprofile(pr1, "BASELINE")

        # ── Scenario 2: FIXED ─────────────────────────────────────────────
        with _cprofiled() as pr2:
            phases_fix, fetch_sql_fix, render_sql_fix, \
                render_sql_time_fix, render_slowest_fix = run_scenario_fixed(app)
        _print_report("FIXED (full eager-loading + cascade suppression)",
                      phases_fix, fetch_sql_fix, render_sql_fix,
                      render_sql_time_fix, render_slowest_fix)
        _print_cprofile(pr2, "FIXED")

        _detach(engine)

        # ── Summary comparison ────────────────────────────────────────────
        total_base = phases_base['TOTAL_FETCH'] + phases_base['TOTAL_RENDER']
        total_fix  = phases_fix['TOTAL_FETCH'] + phases_fix['TOTAL_RENDER']
        print(f"\n{'='*72}")
        print("SUMMARY COMPARISON")
        print(f"{'='*72}")
        print(f"  {'':44s}  {'BASELINE':>10s}  {'FIXED':>10s}  {'CHANGE':>10s}")
        print(f"  {'Data fetch SQL':<44s}  {fetch_sql_base:>10d}  {fetch_sql_fix:>10d}")
        print(f"  {'Template render SQL (lazy loads)':<44s}  {render_sql_base:>10d}  {render_sql_fix:>10d}  "
              f"{render_sql_fix - render_sql_base:>+10d}")
        print(f"  {'Total SQL':<44s}  {fetch_sql_base+render_sql_base:>10d}  "
              f"{fetch_sql_fix+render_sql_fix:>10d}  "
              f"{(fetch_sql_fix+render_sql_fix) - (fetch_sql_base+render_sql_base):>+10d}")
        print(f"  {'Total wall time (ms)':<44s}  {total_base*1000:>10.1f}  {total_fix*1000:>10.1f}")
        if total_fix > 0:
            print(f"  {'Speedup':<44s}  {'':>10s}  {total_base/total_fix:>9.1f}x")


if __name__ == '__main__':
    main()

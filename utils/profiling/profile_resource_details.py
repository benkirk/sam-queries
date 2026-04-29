"""
Profiling script for the Resource Usage Details (disk) route
``/user/resource-details?projcode=...&resource=Campaign_Store``.

Mirrors the structure of profile_user_dashboard.py — fetches data and
renders the disk template in the same request context so SQLAlchemy
lazy loads triggered during template rendering are visible to the SQL
counter.

Default targets contrast a multi-tier subtree project with a leaf:
    NRAL0002    — multi-tier subtree (exercises descendant fanout)
    P43713000   — flat / leaf project (isolates Layer-2 join cost)

Each target is profiled in three phases:
    1. build_disk_subtree            (walks descendants, snapshot per node)
    2. get_subtree_directory_usage_at (Layer-2 disk_activity / disk_charge join)
    3. full Flask request render     (template + lazy loads)

Default login user: bdobbins (matches profile_user_dashboard.py).

Usage:
    source etc/config_env.sh
    python utils/profiling/profile_resource_details.py 2>&1 | tee profile_resource_details.txt
    python utils/profiling/profile_resource_details.py --projcode NRAL0002 --resource Campaign_Store
    python utils/profiling/profile_resource_details.py --user benkirk
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
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.join(_HERE, '..', '..')
sys.path.insert(0, os.path.join(_REPO, 'src'))
# Reuse the canonical SQLStats helper used by tests/perf rather than
# duplicating it inline.
sys.path.insert(0, _REPO)

from tests.perf._query_count import SQLStats  # noqa: E402

from webapp.run import create_app  # noqa: E402
from webapp.extensions import db  # noqa: E402
from flask_login import login_user  # noqa: E402

from sam.core.users import User  # noqa: E402
from sam.projects.projects import Project  # noqa: E402
from sam.resources.resources import Resource  # noqa: E402
from sam.accounting.accounts import Account  # noqa: E402
from sam.queries.disk_usage import (  # noqa: E402
    build_disk_subtree,
    get_subtree_directory_usage_at,
)
from webapp.auth.models import AuthUser  # noqa: E402
from webapp.dashboards.user.blueprint import (  # noqa: E402
    _render_disk_resource_details,
    _disk_subtree_latest_activity_date,
    _find_disk_node,
    _collect_disk_account_ids,
)


DEFAULT_USERNAME = 'bdobbins'
DEFAULT_TARGETS: List[Tuple[str, str]] = [
    ('NRAL0002', 'Campaign_Store'),
    ('P43713000', 'Campaign_Store'),
]


def discover_root_projects(session, resource_name: str) -> List[str]:
    """Return projcodes of root projects whose subtree has accounts on *resource_name*.

    A "root" is a project whose own ``project_id`` equals its ``tree_root``
    (MPPT root). We find every project with a non-deleted account on the
    given resource, take ``DISTINCT tree_root``, and resolve back to the
    root projcodes. Order by projcode for deterministic output.
    """
    tree_root_ids = [
        row[0] for row in (
            session.query(Project.tree_root)
            .join(Account, Account.project_id == Project.project_id)
            .join(Resource, Resource.resource_id == Account.resource_id)
            .filter(
                Resource.resource_name == resource_name,
                Account.deleted == False,  # noqa: E712 — SQL expression
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    ]
    if not tree_root_ids:
        return []
    roots = (
        session.query(Project)
        .filter(
            Project.project_id.in_(tree_root_ids),
            Project.is_active,
        )
        .order_by(Project.projcode)
        .all()
    )
    return [r.projcode for r in roots]


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
    print('=' * 72)
    print(buf.getvalue())


# ---------------------------------------------------------------------------
# Per-phase scoped capture using the shared SQLStats. We attach a fresh
# SQLStats per phase so per-table buckets and slowest lists don't bleed
# across phases — easier to reason about than reset() in the middle.
# ---------------------------------------------------------------------------

@contextmanager
def _phase(engine):
    """Yield (stats, elapsed_ref). On exit, fills elapsed_ref[0]."""
    stats = SQLStats()
    stats.attach(engine)
    elapsed_ref = [0.0]
    t0 = time.perf_counter()
    try:
        yield stats, elapsed_ref
    finally:
        elapsed_ref[0] = time.perf_counter() - t0
        stats.detach(engine)


# ---------------------------------------------------------------------------
# Tree shape helpers — surface "how big is the subtree" so the reader
# can interpret query counts as a fanout ratio.
# ---------------------------------------------------------------------------

def _count_nodes(node) -> int:
    return 1 + sum(_count_nodes(c) for c in node.get('children', []))


def _count_nodes_with_account(node) -> int:
    n = 1 if node.get('account_id') is not None else 0
    for c in node.get('children', []):
        n += _count_nodes_with_account(c)
    return n


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(app, engine, username: str, projcode: str, resource_name: str):
    """Profile the disk path of /user/resource-details for one target.

    Returns a dict with phase metrics and tree shape info.
    """
    url = f'/user/resource-details?projcode={projcode}&resource={resource_name}'

    with app.test_request_context(url):
        session = db.session

        user = session.query(User).filter_by(username=username).first()
        if user is None:
            raise SystemExit(f"User {username!r} not found in database")
        login_user(AuthUser(user))

        project = Project.get_by_projcode(session, projcode)
        if project is None:
            raise SystemExit(f"Project {projcode!r} not found in database")

        resource = session.query(Resource).filter(
            Resource.resource_name == resource_name,
        ).first()
        if resource is None:
            raise SystemExit(
                f"Resource {resource_name!r} not found in database"
            )

        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        # ----- Phase 1: build_disk_subtree (descendant walk + snapshot) -----
        with _phase(engine) as (s1, e1):
            tree_payload = build_disk_subtree(session, project, resource_name)
        full_tree = tree_payload['tree']
        node_count = _count_nodes(full_tree)
        node_count_with_account = _count_nodes_with_account(full_tree)
        scope_account_ids = _collect_disk_account_ids(full_tree)

        # ----- Phase 2: get_subtree_directory_usage_at (Layer-2 join) -----
        snapshot_date = _disk_subtree_latest_activity_date(full_tree)
        with _phase(engine) as (s2, e2):
            fileset_dirs = get_subtree_directory_usage_at(
                session,
                account_ids=scope_account_ids,
                resource_name=resource_name,
                activity_date=snapshot_date,
            )

        # ----- Phase 3: full template render via _render_disk_resource_details -----
        # This re-runs build_disk_subtree + get_subtree_directory_usage_at
        # internally (so phase 3's count overlaps phases 1+2) and adds
        # the timeseries/user-table queries plus any template lazy loads.
        with _phase(engine) as (s3, e3):
            _render_disk_resource_details(
                project=project,
                resource=resource,
                start_date=start_date,
                end_date=end_date,
            )

    return {
        'projcode': projcode,
        'resource': resource_name,
        'user_id': user.user_id,
        'project_id': project.project_id,
        'node_count': node_count,
        'node_count_with_account': node_count_with_account,
        'scope_account_ids_len': len(scope_account_ids),
        'snapshot_date': snapshot_date,
        'fileset_dirs_len': len(fileset_dirs),
        'phases': [
            {
                'name': 'build_disk_subtree',
                'elapsed': e1[0],
                'stats': s1,
            },
            {
                'name': 'get_subtree_directory_usage_at',
                'elapsed': e2[0],
                'stats': s2,
            },
            {
                'name': '_render_disk_resource_details (full route)',
                'elapsed': e3[0],
                'stats': s3,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_by_table(stats: SQLStats, indent: str = '      '):
    if not stats.by_table:
        return
    rows = sorted(stats.by_table.items(), key=lambda kv: -len(kv[1]))
    print(f"{indent}Per-table query distribution:")
    print(f"{indent}  {'table':<40s}  {'count':>5s}  {'total ms':>10s}")
    for table, elapsed_list in rows:
        print(
            f"{indent}  {table:<40s}  {len(elapsed_list):>5d}  "
            f"{sum(elapsed_list)*1000:>9.1f}"
        )


def _print_slowest(stats: SQLStats, indent: str = '      '):
    if not stats.slowest:
        return
    print(f"{indent}Top slowest queries this phase:")
    for i, (t, sql) in enumerate(stats.slowest[:5], 1):
        print(f"{indent}  [{i}] {t*1000:7.1f} ms  {sql!r}")


def _print_report(result: Dict):
    label = f"{result['projcode']}  /  {result['resource']}"
    print(f"\n{'#'*72}")
    print(f"SCENARIO: {label}")
    print(f"{'#'*72}")
    print(f"  project_id          : {result['project_id']}")
    print(f"  tree node count     : {result['node_count']}  "
          f"({result['node_count_with_account']} with disk account on this resource)")
    print(f"  subtree account_ids : {result['scope_account_ids_len']}")
    print(f"  latest snapshot date: {result['snapshot_date']}")
    print(f"  filesets at snapshot: {result['fileset_dirs_len']}")
    print()

    total_elapsed = sum(p['elapsed'] for p in result['phases'])
    total_q = sum(p['stats'].count for p in result['phases'])
    total_q_time = sum(p['stats'].total_time for p in result['phases'])

    print(f"  {'phase':<46s} {'wall ms':>9s} {'queries':>8s} {'sql ms':>9s}")
    print(f"  {'-'*46} {'-'*9} {'-'*8} {'-'*9}")
    for phase in result['phases']:
        s = phase['stats']
        print(
            f"  {phase['name']:<46s} {phase['elapsed']*1000:>9.1f} "
            f"{s.count:>8d} {s.total_time*1000:>9.1f}"
        )
    print(f"  {'-'*46} {'-'*9} {'-'*8} {'-'*9}")
    print(
        f"  {'TOTAL (phases sum, P3 overlaps P1+P2)':<46s} "
        f"{total_elapsed*1000:>9.1f} {total_q:>8d} {total_q_time*1000:>9.1f}"
    )

    # Fanout ratio: queries during build_disk_subtree per descendant.
    p1 = result['phases'][0]
    if result['node_count'] > 0:
        ratio = p1['stats'].count / result['node_count']
        flag = '  ← FANOUT SUSPECT' if ratio > 3.0 else ''
        print(
            f"\n  build_disk_subtree fanout: "
            f"{p1['stats'].count} queries / {result['node_count']} nodes = "
            f"{ratio:.2f} q/node{flag}"
        )

    for phase in result['phases']:
        print(f"\n  --- {phase['name']} ---")
        print(f"      summary: {phase['stats'].summary()}")
        _print_slowest(phase['stats'])
        _print_by_table(phase['stats'])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Profile /user/resource-details disk path.'
    )
    parser.add_argument(
        '--user',
        default=DEFAULT_USERNAME,
        help=f'Username to log in as (default: {DEFAULT_USERNAME})',
    )
    parser.add_argument(
        '--projcode',
        action='append',
        default=None,
        help='Project code to profile (repeatable). Defaults to NRAL0002 + P43713000.',
    )
    parser.add_argument(
        '--resource',
        default='Campaign_Store',
        help='Resource name (default: Campaign_Store). Applies to all --projcode args.',
    )
    parser.add_argument(
        '--no-cprofile',
        action='store_true',
        help='Skip the per-target cProfile breakdown (cuts noise for SQL-only diff).',
    )
    parser.add_argument(
        '--discover-roots',
        action='store_true',
        help='Auto-enumerate root projects with accounts on --resource and profile each.',
    )
    parser.add_argument(
        '--max-targets',
        type=int,
        default=10,
        help='Cap on number of targets when --discover-roots is set (default: 10).',
    )
    parser.add_argument(
        '--include',
        action='append',
        default=[],
        help='Force-include a projcode even when --discover-roots is set (repeatable).',
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        engine = db.engine

        if args.discover_roots:
            with app.test_request_context('/'):
                discovered = discover_root_projects(db.session, args.resource)
            # Keep --include projcodes at the front so they're profiled first
            # (coldest cache slot). De-dupe while preserving order.
            ordered = list(args.include)
            for pc in discovered:
                if pc not in ordered:
                    ordered.append(pc)
            ordered = ordered[:args.max_targets]
            targets = [(pc, args.resource) for pc in ordered]
            print(f"Discovered {len(discovered)} candidate roots; "
                  f"profiling {len(targets)} (cap={args.max_targets}).")
        elif args.projcode:
            targets = [(pc, args.resource) for pc in args.projcode]
        else:
            targets = list(DEFAULT_TARGETS)

        print(f"Profiling date  : {datetime.now().isoformat(timespec='seconds')}")
        print(f"Login user      : {args.user}")
        print(f"DB URL          : {engine.url}")
        print(f"Targets         : {targets}")
        print()
        print("NOTE: Phase 3 re-invokes build_disk_subtree +")
        print("      get_subtree_directory_usage_at internally, so its query")
        print("      count overlaps phases 1+2. The standalone phases 1 and 2")
        print("      are there to attribute SQL traffic to the specific call site.")
        print("      Cache caveat: only the FIRST target's P1 query reflects a")
        print("      truly cold InnoDB buffer pool. Subsequent targets reuse cached")
        print("      disk_activity pages for the same activity_date+resource_name.")

        all_results = []
        for projcode, resource_name in targets:
            label = f"{projcode}/{resource_name}"
            if args.no_cprofile:
                result = run_scenario(app, engine, args.user, projcode, resource_name)
            else:
                with _cprofiled() as pr:
                    result = run_scenario(
                        app, engine, args.user, projcode, resource_name,
                    )
            _print_report(result)
            if not args.no_cprofile:
                _print_cprofile(pr, label)
            all_results.append(result)

        # Compact cross-target summary
        print(f"\n{'='*72}")
        print("CROSS-TARGET SUMMARY")
        print(f"{'='*72}")
        print(
            f"  {'target':<32s} {'nodes':>6s} {'P1 q':>6s} {'P2 q':>6s} "
            f"{'P3 q':>6s} {'P3 ms':>9s}"
        )
        print(f"  {'-'*32} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*9}")
        for r in all_results:
            phases = r['phases']
            print(
                f"  {r['projcode'] + '/' + r['resource']:<32s} "
                f"{r['node_count']:>6d} "
                f"{phases[0]['stats'].count:>6d} "
                f"{phases[1]['stats'].count:>6d} "
                f"{phases[2]['stats'].count:>6d} "
                f"{phases[2]['elapsed']*1000:>9.1f}"
            )


if __name__ == '__main__':
    main()

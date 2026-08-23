"""Dashboard data aggregation queries.

Three surfaces, laid out by entry point with shared helpers above them:

* ``get_user_dashboard_data`` -- /user/. All of a user's active projects in a
  fixed-size set of batched queries, independent of project count.
* ``get_project_dashboard_data`` -- admin single-project search. No batching
  benefit at N=1, and it must coexist with the admin tree views that loop
  per-node for unrelated reasons.
* ``get_resource_detail_data`` -- the per-resource drilldown. Self-contained.

Two resource-dict builders coexist deliberately: ``_build_project_resources_data``
(per project) and ``_build_user_projects_resources_batched`` (many at once,
replacing an N+1 fanout). Merging them would either pessimize N=1 or complicate
N=many.

WARNING: both must produce identical fields.
``test_query_functions.py::TestDashboardQueries::test_user_dashboard_batched_matches_per_project``
compares them field-by-field on every CI run -- change one, keep it green or
change the other in lockstep.

This module does NOT import from ``sam.queries.fstree_access``; both depend
only on the durable ``Project`` class methods.
"""

from datetime import datetime, date, timedelta
from typing import Any, List, Dict, Optional, TypedDict

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from sam.core.users import User
from sam.core.organizations import Organization, ProjectOrganization
from sam.enums import ResourceTypeName
from sam.projects.projects import Project
from sam.projects.contracts import Contract, ContractSource, ProjectContract
from sam.accounting.accounts import Account, AccountUser
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource
from sam.resources.facilities import Facility, Panel
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary
from sam.queries.charges import get_adjustment_totals_by_date
from sam.queries.rolling_usage import get_project_rolling_usage


# ============================================================================
# Dashboard Query Helpers
# ============================================================================


class DashboardResource(TypedDict):
    """
    Per-resource row in the dashboard data structure.

    Single source of truth for the dict shape produced by BOTH
    _build_project_resources_data() (single-project path) and
    _build_user_projects_resources_batched() (multi-project batched path).
    Both producers must populate every field listed here; the equivalence
    test in tests/unit/test_query_functions.py compares them
    field-by-field at runtime.

    Note: this is a documentation/IDE annotation only. The project does
    not run mypy in CI, so type errors won't fail the build — but the
    annotation gives editors enough information to autocomplete field
    access, catch typos when reading, and serve as the canonical
    contract for downstream consumers (the project_card.html template,
    the API serializers, the CLI display helpers).

    If you add or rename a field, update BOTH producers AND the
    equivalence test's SCALAR_FIELDS / FLOAT_FIELDS tuples in lockstep.
    """
    # Identity / FK metadata
    resource_name: str
    allocation_id: Optional[int]
    parent_allocation_id: Optional[int]
    is_inheriting: bool
    account_id: Optional[int]

    # Numeric usage. For an inheriting (shared) allocation,
    # `used`/`remaining`/`percent_used` are the FULL tree's consumption against
    # the shared pool and `self_*` carry this project's own contribution, so a
    # two-tone bar can be drawn. `self_*` are None otherwise.
    allocated: float
    used: float
    remaining: float
    percent_used: float
    self_used: Optional[float]
    self_percent_used: Optional[float]
    root_projcode: Optional[str]
    charges_by_type: Dict[str, float]
    adjustments: float

    # Status / display
    status: str
    resource_type: str

    # Date metadata
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    days_until_expiration: Optional[int]
    date_group_key: str

    # Disk-only: latest snapshot date for the capacity bar's "as of" label, None
    # for non-disk resources and for disk accounts with no snapshot yet. When
    # set, `used` and `percent_used` are point-in-time capacity (TiB used / TiB
    # allocated), not cumulative TiB-year burn.
    activity_date: Optional[date]

    # Timeline progress (mirrors allocations dashboard project_table.html)
    elapsed_pct: float
    bar_state: str  # one of: 'no-dates', 'open-ended', 'expired', 'active', 'no-duration'

    # Rolling-window usage (only populated for projects whose accounts
    # have a non-null first_threshold or second_threshold; otherwise None)
    rolling_30: Optional[Dict]
    rolling_90: Optional[Dict]


def _build_project_resources_data(project: Project,
                                   active_at: Optional[datetime] = None) -> List[DashboardResource]:
    """
    Single-project resource-dict builder.

    Calls Project.get_detailed_allocation_usage() (which fires per-account
    charge / adjustment / job-statistics queries) and shapes the result
    into the dict format the dashboard templates consume. Coexists with
    _build_user_projects_resources_batched() — see this module's docstring
    for the why-two explanation. Both produce identical output and are
    locked in step by an equivalence test in test_query_functions.py.

    Used by:
      * get_project_dashboard_data() — admin single-project search
      * webapp/dashboards/admin/projects_routes.py — admin tree view (loops
        per node; OK because admin tree views are small and rare)

    The batched version is used by get_user_dashboard_data() instead.

    Args:
        project: Project object
        active_at: Reference datetime for determining which allocation is "active".
                   Defaults to now.

    Returns:
        List of resource dictionaries with usage details
    """
    resources = []
    # Note: disk-capacity overrides for DISK rows are applied inside
    # get_detailed_allocation_usage() — no per-row override needed here.
    usage_data = project.get_detailed_allocation_usage(include_adjustments=True,
                                                        active_at=active_at)

    now = active_at or datetime.now()

    # Rolling-window usage (30d/90d) only when an account has a non-null
    # threshold: the template gates the block on threshold_pct, so for the vast
    # majority of projects this would fetch data nothing renders.
    needs_rolling = any(
        (acct.first_threshold is not None or acct.second_threshold is not None)
        for acct in project.accounts
        if not acct.deleted
    )
    rolling_usage = (
        get_project_rolling_usage(project.session, project.projcode)
        if needs_rolling else {}
    )

    for resource_name, usage in usage_data.items():
        start_date = usage.get('start_date')
        end_date = usage.get('end_date')

        # Calculate days until expiration
        days_until_expiration = None
        if end_date:
            days_until_expiration = (end_date - now).days

        # Sortable group key for grouping resources with identical date bounds
        start_str = start_date.strftime('%Y-%m-%d') if start_date else '0000-00-00'
        end_str   = end_date.strftime('%Y-%m-%d')   if end_date   else 'open'
        date_group_key = f"{start_str}_{end_str}"

        # Timeline progress (mirrors allocations dashboard project_table.html logic)
        if not start_date:
            elapsed_pct = 0
            bar_state   = 'no-dates'
        elif not end_date:
            elapsed_pct = 50
            bar_state   = 'open-ended'
        elif end_date < now:
            elapsed_pct = 100
            bar_state   = 'expired'
        else:
            duration_days = (end_date - start_date).days
            if duration_days > 0:
                elapsed_pct = max(0.0, min(100.0, round((now - start_date).days / duration_days * 100, 1)))
                bar_state   = 'active'
            else:
                elapsed_pct = 0
                bar_state   = 'no-duration'

        rwin = rolling_usage.get(resource_name, {}).get('windows', {})
        resource_type = usage.get('resource_type', 'HPC')
        resource_dict = {
            'resource_name': resource_name,
            'allocation_id': usage.get('allocation_id'),  # Required for edit functionality
            'parent_allocation_id': usage.get('parent_allocation_id'),
            'is_inheriting': usage.get('is_inheriting', False),
            'account_id': usage.get('account_id'),  # Required for permission checks
            'allocated': usage.get('allocated', 0.0),
            'used': usage.get('used', 0.0),
            'remaining': usage.get('remaining', 0.0),
            'percent_used': usage.get('percent_used', 0.0),
            # When `is_inheriting`, `used`/`percent_used` reflect the shared
            # pool's full-tree consumption; `self_used`/`self_percent_used`
            # carry this project's contribution so the UI can render a
            # two-tone bar.
            'self_used': usage.get('self_used'),
            'self_percent_used': usage.get('self_percent_used'),
            'root_projcode': usage.get('root_projcode'),
            'charges_by_type': usage.get('charges_by_type', {}),
            'adjustments': usage.get('adjustments', 0.0),
            'status': usage.get('status', 'Unknown'),
            'start_date': start_date,
            'end_date': end_date,
            'days_until_expiration': days_until_expiration,
            'date_group_key': date_group_key,
            'elapsed_pct': elapsed_pct,
            'bar_state': bar_state,
            'resource_type': resource_type,
            'activity_date': usage.get('activity_date'),
            'rolling_30': rwin.get(30),
            'rolling_90': rwin.get(90),
        }

        resources.append(resource_dict)

    return resources


def _apply_disk_capacity_overrides(
    resource_dict: Dict[str, Any],
    cap: Optional[Dict[str, Any]],
) -> None:
    """Mutate resource_dict to swap cumulative TiB-yr for point-in-time
    capacity from ``cap`` (output of bulk_get_subtree_disk_capacity)."""
    if cap is None:
        return
    allocated = resource_dict.get('allocated', 0.0) or 0.0
    used_tib = cap['used_tib']
    pct = (used_tib / allocated * 100) if allocated > 0 else 0.0
    resource_dict['used'] = used_tib
    resource_dict['remaining'] = allocated - used_tib
    resource_dict['percent_used'] = pct
    resource_dict['activity_date'] = cap['activity_date']


def _select_query_alloc(account: Account, now: datetime):
    """
    Pick the allocation to display for an account, mirroring the logic in
    Project.get_detailed_allocation_usage(): prefer the active allocation,
    otherwise fall back to the most recent one if it expired within 90 days.

    Returns the chosen Allocation or None.
    """
    for alloc in account.allocations:
        if alloc.is_active_at(now):
            return alloc
    if account.allocations:
        most_recent = max(
            account.allocations,
            key=lambda a: a.end_date if a.end_date else datetime.max,
        )
        end = most_recent.end_date
        # Exact timedelta comparison, NOT (now - end).days <= 90: .days
        # truncates toward zero, keeping an allocation a full extra day in
        # [90d, 91d) after expiry and breaking the batched/per-project
        # equivalence with get_detailed_allocation_usage() at that boundary.
        if end is None or (now - end) <= timedelta(days=90):
            return most_recent
    return None


def _build_user_projects_resources_batched(
    session: Session,
    projects: List[Project],
    active_at: Optional[datetime] = None,
) -> Dict[int, List[DashboardResource]]:
    """
    Batched, multi-project equivalent of _build_project_resources_data().

    Returns a dict mapping ``project_id`` -> list of resource dicts in
    EXACTLY the same shape that _build_project_resources_data() produces
    per-project. The caller can plug each list straight into its existing
    project_data structure with no template changes.

    Why this exists
    ---------------
    The user dashboard renders many projects per request. The per-project
    helper calls ``Project.get_detailed_allocation_usage()``, which fires
    ~3-4 charge-aggregation queries per (project, account) pair plus a
    rolling-usage round trip per project. For a user on 11 projects with
    ~5 accounts each that became ~290 SQL queries and ~2.9 s wall time
    (verified via utils/profiling/profile_user_dashboard.py).

    This helper collapses that into a fixed number of batched queries:

      * ONE consolidated Project ``selectinload`` chain for template metadata
        (lead, admin, allocation_type -> panel -> facility, area_of_interest,
        contracts, organizations, directories), with cascade suppression
        on User to keep ``Project.lead/admin`` from dragging in every loaded
        User's selectin relationships.
      * ONE bulk Account fetch, joinedloaded with resource + resource_type
        and selectinloaded with allocations.
      * Calls to Project.batch_get_subtree_charges and
        Project.batch_get_account_charges — each issues
        N_resource_types × N_charge_models queries (typically 5-20 total).
      * Skipped get_project_rolling_usage() calls for projects whose
        accounts have no rolling thresholds set (the "C1 gate" — most
        projects in production qualify, eliminating ~80-90 queries).

    Coupling note
    -------------
    The batch primitives (Project.batch_get_*_charges) live on the Project
    class and are also consumed by sam.queries.fstree_access. This module
    does NOT import anything from fstree_access — fstree is treated as
    disposable; the durable interface is Project.batch_get_*_charges.

    Equivalence
    -----------
    Locked to _build_project_resources_data() field-by-field by
    tests/unit/test_query_functions.py::TestDashboardQueries
    ::test_user_dashboard_batched_matches_per_project. Any divergence
    between the two paths fails CI.
    """
    now = active_at or datetime.now()

    project_ids = [p.project_id for p in projects]
    if not project_ids:
        return {}

    # Phase 1: bulk-load every Project's template-required metadata in one
    # batched fetch. The Project rows are already in the identity map, so this
    # only attaches relationships.
    #
    # Cascade-suppression: joinedload(Project.lead/admin) loads a User, whose
    # lazy='selectin' relationships (led_projects, admin_projects, accounts,
    # email_addresses) would then fire for every lead/admin loaded. The template
    # reads only display_name and user_id, so .lazyload() suppresses all four.
    session.query(Project).options(
        joinedload(Project.lead).lazyload(User.led_projects),
        joinedload(Project.lead).lazyload(User.admin_projects),
        joinedload(Project.lead).lazyload(User.accounts),
        joinedload(Project.lead).lazyload(User.email_addresses),
        joinedload(Project.admin).lazyload(User.led_projects),
        joinedload(Project.admin).lazyload(User.admin_projects),
        joinedload(Project.admin).lazyload(User.accounts),
        joinedload(Project.admin).lazyload(User.email_addresses),
        joinedload(Project.allocation_type)
            .joinedload(AllocationType.panel)
            .joinedload(Panel.facility),
        joinedload(Project.area_of_interest),
        selectinload(Project.contracts)
            .joinedload(ProjectContract.contract)
            .joinedload(Contract.contract_source),
        selectinload(Project.organizations)
            .joinedload(ProjectOrganization.organization),
        selectinload(Project.directories),
    ).filter(Project.project_id.in_(project_ids)).all()

    # Phase 2: bulk-load every account (with allocations + resource) for every
    # project the user is on. Bucket by project_id explicitly -- iterating
    # `project.accounts` would defeat the bulk fetch, triggering a per-project
    # lazy load even though the rows are already in the identity map.
    accounts_for_user = session.query(Account).options(
        selectinload(Account.allocations),
        joinedload(Account.resource).joinedload(Resource.resource_type),
    ).filter(
        Account.project_id.in_(project_ids),
        Account.deleted == False,  # noqa: E712 — SQLAlchemy expression
    ).all()

    accounts_by_project: Dict[int, List[Account]] = {}
    for acct in accounts_for_user:
        accounts_by_project.setdefault(acct.project_id, []).append(acct)

    # Phase 3: per (project, account), pick the allocation to display and
    # collect a work unit for the batch charge methods. Pure Python, no DB;
    # `_select_query_alloc` mirrors get_detailed_allocation_usage()'s
    # active-or-recent selection.
    subtree_infos: List[Dict] = []
    account_infos: List[Dict] = []
    # (project_id, account_id) -> (project, account, query_alloc, resource_type, end_date)
    chosen: Dict[tuple, tuple] = {}

    for project in projects:
        # Leaf nodes route through the leaf-friendly batch primitive, which
        # groups by resource_type only and inlines per-anchor date ranges in the
        # VALUES CTE. The subtree primitive must date-group separately, fanning
        # out the query count for users whose projects span many distinct
        # allocation windows.
        leaf = project.is_leaf()
        for account in accounts_by_project.get(project.project_id, []):
            if account.deleted:
                continue
            if not account.resource:
                continue
            query_alloc = _select_query_alloc(account, now)
            if query_alloc is None:
                continue

            resource_type = (
                account.resource.resource_type.resource_type
                if account.resource.resource_type
                else 'UNKNOWN'
            )
            start_date = query_alloc.start_date
            end_date = query_alloc.end_date or now
            key = (project.project_id, account.account_id)

            chosen[key] = (project, account, query_alloc, resource_type, end_date)

            if leaf:
                account_infos.append({
                    'key':           key,
                    'account_id':    account.account_id,
                    'resource_type': resource_type,
                    'start_date':    start_date,
                    'end_date':      end_date,
                })
            else:
                subtree_infos.append({
                    'key':           key,
                    'resource_id':   account.resource_id,
                    'resource_type': resource_type,
                    'tree_root':     project.tree_root,
                    'tree_left':     project.tree_left,
                    'tree_right':    project.tree_right,
                    'start_date':    start_date,
                    'end_date':      end_date,
                })

    # Phase 3.5: for each inheriting allocation, also compute the root
    # allocation's subtree usage -- that total is the authoritative pool
    # consumption, and the per-project number becomes `self_used`. Piggy-backs
    # on batch_get_subtree_charges via parallel ('root',)-suffixed keys.
    root_project_by_key: Dict[tuple, 'Project'] = {}
    for key, (project, account, query_alloc, resource_type, end_date) in chosen.items():
        if not query_alloc.is_inheriting:
            continue
        root_alloc = query_alloc.root
        root_account = root_alloc.account
        root_project = root_account.project if root_account else None
        if root_project is None:
            continue
        if not (root_project.tree_root and root_project.tree_left and root_project.tree_right):
            continue
        root_project_by_key[key] = root_project
        subtree_infos.append({
            'key':           ('root', key),
            'resource_id':   account.resource_id,
            'resource_type': resource_type,
            'tree_root':     root_project.tree_root,
            'tree_left':     root_project.tree_left,
            'tree_right':    root_project.tree_right,
            'start_date':    query_alloc.start_date,
            'end_date':      end_date,
        })

    # Phase 4: ONE batched fetch per partition (subtree / leaf). Each call
    # issues N_resource_types x N_charge_models queries -- typically 5-20 for
    # the whole user, regardless of project count.
    raw_charges: Dict[Any, Dict] = {}
    if subtree_infos:
        raw_charges.update(
            Project.batch_get_subtree_charges(session, subtree_infos, include_adjustments=True)
        )
    if account_infos:
        raw_charges.update(
            Project.batch_get_account_charges(session, account_infos, include_adjustments=True)
        )

    # Phase 5: rolling-usage gate, the same rule `_build_project_resources_data`
    # applies so both paths agree. The template gates rendering on
    # threshold_pct, and most production projects have none set; skipping saves
    # ~8-9 queries per project, ~93 queries and ~1.2 s for one test user.
    rolling_usage_by_projcode: Dict[str, Dict] = {}
    for project in projects:
        needs_rolling = any(
            (acct.first_threshold is not None or acct.second_threshold is not None)
            for acct in accounts_by_project.get(project.project_id, [])
        )
        if needs_rolling:
            rolling_usage_by_projcode[project.projcode] = get_project_rolling_usage(
                session, project.projcode,
            )

    # Phase 6: assemble per-project resource dicts, mirroring
    # `_build_project_resources_data`'s shape exactly -- every field, every
    # default -- so templates are unaffected and the equivalence test can
    # compare dict-by-dict. A field added or renamed here must be mirrored
    # there AND added to that test's SCALAR_FIELDS / FLOAT_FIELDS.
    out: Dict[int, List[DashboardResource]] = {p.project_id: [] for p in projects}

    # Bulk-resolve disk-capacity overrides for every (project, resource)
    # pair on a disk resource, so the per-row loop below becomes a pure
    # dict lookup.
    disk_pairs = [
        (project, account.resource.resource_name)
        for project, account, _qa, rtype, _ed in chosen.values()
        if rtype == 'DISK'
    ]
    if disk_pairs:
        from sam.queries.disk_usage import bulk_get_subtree_disk_capacity
        disk_caps = bulk_get_subtree_disk_capacity(session, disk_pairs)
    else:
        disk_caps = {}

    for key, (project, account, query_alloc, resource_type, end_date) in chosen.items():
        resource_name = account.resource.resource_name
        start_date = query_alloc.start_date

        charges_data = raw_charges.get(key, {'charges_by_type': {}, 'adjustment': 0.0})
        charges_by_type = charges_data['charges_by_type']
        adjustments = charges_data['adjustment']

        allocated = float(query_alloc.amount)
        total_charges = sum(charges_by_type.values())
        self_used = total_charges + adjustments

        # For an inheriting allocation the authoritative pool consumption is the
        # root allocation's subtree, batched above under a ('root', key) entry.
        # That becomes `used`; this project's contribution becomes `self_used`.
        tree_used = self_used
        self_percent_used = None
        root_projcode = None
        if query_alloc.is_inheriting:
            root_data = raw_charges.get(('root', key), {'charges_by_type': {}, 'adjustment': 0.0})
            tree_used = sum(root_data['charges_by_type'].values()) + root_data['adjustment']
            self_percent_used = (self_used / allocated * 100) if allocated > 0 else 0.0
            root_proj = root_project_by_key.get(key)
            if root_proj is not None:
                root_projcode = root_proj.projcode

        effective_used = tree_used
        remaining = allocated - effective_used
        percent_used = (effective_used / allocated * 100) if allocated > 0 else 0.0

        days_until_expiration = None
        if query_alloc.end_date:
            days_until_expiration = (query_alloc.end_date - now).days

        start_str = start_date.strftime('%Y-%m-%d') if start_date else '0000-00-00'
        end_str = query_alloc.end_date.strftime('%Y-%m-%d') if query_alloc.end_date else 'open'
        date_group_key = f"{start_str}_{end_str}"

        if not start_date:
            elapsed_pct = 0
            bar_state = 'no-dates'
        elif not query_alloc.end_date:
            elapsed_pct = 50
            bar_state = 'open-ended'
        elif query_alloc.end_date < now:
            elapsed_pct = 100
            bar_state = 'expired'
        else:
            duration_days = (query_alloc.end_date - start_date).days
            if duration_days > 0:
                elapsed_pct = max(0.0, min(100.0, round((now - start_date).days / duration_days * 100, 1)))
                bar_state = 'active'
            else:
                elapsed_pct = 0
                bar_state = 'no-duration'

        rwin = rolling_usage_by_projcode.get(project.projcode, {}).get(resource_name, {}).get('windows', {})

        resource_dict = {
            'resource_name':        resource_name,
            'allocation_id':        query_alloc.allocation_id,
            'parent_allocation_id': query_alloc.parent_allocation_id,
            'is_inheriting':        query_alloc.is_inheriting,
            'account_id':           account.account_id,
            'allocated':            allocated,
            'used':                 effective_used,
            'remaining':            remaining,
            'percent_used':         percent_used,
            'self_used':            self_used if query_alloc.is_inheriting else None,
            'self_percent_used':    self_percent_used,
            'root_projcode':        root_projcode,
            'charges_by_type':      charges_by_type,
            'adjustments':          adjustments,
            'status':               'Unknown',
            'start_date':           start_date,
            'end_date':             query_alloc.end_date,
            'days_until_expiration': days_until_expiration,
            'date_group_key':       date_group_key,
            'elapsed_pct':          elapsed_pct,
            'bar_state':            bar_state,
            'resource_type':        resource_type,
            'activity_date':        None,
            'rolling_30':           rwin.get(30),
            'rolling_90':           rwin.get(90),
        }

        if resource_type == 'DISK':
            _apply_disk_capacity_overrides(
                resource_dict,
                disk_caps.get((project.project_id, resource_name)),
            )

        out[project.project_id].append(resource_dict)

    # Sort each project's resources by resource_name for stable ordering
    # (matches the implicit ordering of project.get_detailed_allocation_usage()
    # which iterates accounts in load order — we sort here to be deterministic).
    for pid in out:
        out[pid].sort(key=lambda r: r['resource_name'])

    return out


def get_project_dashboard_data(session: Session, projcode: str) -> Optional[Dict]:
    """
    Get dashboard data for a single project.

    Drives the admin single-project search route. Self-contained — does
    NOT call into _build_user_projects_resources_batched(). The user
    dashboard at /user/ uses that batched helper directly via
    get_user_dashboard_data() instead.

    Args:
        session: SQLAlchemy session
        projcode: Project code to fetch data for

    Returns:
        Dictionary with structure:
        {
            'project': Project object,
            'resources': List[DashboardResource],  # see TypedDict above
            'has_children': bool
        }
        Returns None if project not found.

    Example:
        >>> data = get_project_dashboard_data(session, 'SCSG0001')
        >>> if data:
        ...     proj = data['project']
        ...     print(f"{proj.projcode}: {len(data['resources'])} resources")
    """
    # An explicit per-call .options(...) chain rather than model-level
    # lazy='selectin', because this is the single-project path: at N=1 there is
    # no batching benefit, and joinedload holds it to one query plus a few
    # selectinload secondaries. The cascade that forced suppression in the
    # batched helper cannot bite here -- one lead/admin User, fired once.
    project = session.query(Project)\
        .options(
            joinedload(Project.lead),
            joinedload(Project.admin),
            joinedload(Project.allocation_type).joinedload(AllocationType.panel).joinedload(Panel.facility),
            joinedload(Project.area_of_interest),
            selectinload(Project.contracts).joinedload(ProjectContract.contract).joinedload(Contract.contract_source),
            selectinload(Project.organizations).joinedload(ProjectOrganization.organization),
            selectinload(Project.directories)
        )\
        .filter(Project.projcode == projcode)\
        .first()

    if not project:
        return None

    return {
        'project': project,
        'resources': _build_project_resources_data(project),
        'has_children': project.has_children if hasattr(project, 'has_children') else False
    }


def get_user_dashboard_data(session: Session, user_id: int) -> Dict:
    """
    Get all dashboard data for a user in one optimized query set.

    Loads user, their active projects, and allocation usage for each project.
    Optimized for server-side dashboard rendering with minimal database queries.

    Args:
        session: SQLAlchemy session
        user_id: User ID to fetch dashboard for

    Returns:
        Dictionary with structure:
        {
            'user': User object,
            'projects': [
                {
                    'project': Project object,
                    'resources': List[DashboardResource],  # see TypedDict above
                    'has_children': bool
                }
            ],
            'total_projects': int
        }

    Example:
        >>> data = get_user_dashboard_data(session, 12345)
        >>> print(f"User {data['user'].username} has {data['total_projects']} projects")
        >>> for proj_data in data['projects']:
        ...     proj = proj_data['project']
        ...     print(f"{proj.projcode}: {len(proj_data['resources'])} resources")
    """
    # Bare User fetch, no per-call eager-loading chain: the model-level
    # lazy='selectin' on User.led_projects / admin_projects / accounts /
    # email_addresses and AccountUser.account already makes the
    # active_projects() walk one batched query per relationship. The
    # template-required Project metadata is loaded by the consolidated
    # selectinload chain at the top of _build_user_projects_resources_batched.
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        return {
            'user': None,
            'projects': [],
            'total_projects': 0
        }

    # Get active projects, sorted by project code for consistent display order
    projects = sorted(user.active_projects(), key=lambda p: p.projcode)

    # Batched fetch: collect alloc work units across all projects and call
    # Project.batch_get_*_charges twice total instead of firing per-project
    # get_detailed_allocation_usage() (which would fire 3-4 queries per
    # (project, account)). See _build_user_projects_resources_batched docstring.
    project_resources_map: Dict[int, List[DashboardResource]] = (
        _build_user_projects_resources_batched(session, projects)
    )

    project_data_list = []
    for project in projects:
        project_data_list.append({
            'project': project,
            'resources': project_resources_map.get(project.project_id, []),
            'has_children': project.has_children if hasattr(project, 'has_children') else False
        })

    return {
        'user': user,
        'projects': project_data_list,
        'total_projects': len(projects)
    }


def get_resource_detail_data(
    session: Session,
    projcode: str,
    resource_name: str,
    start_date: datetime,
    end_date: datetime,
    include_adjustments: bool = True,
    scope_projcode: Optional[str] = None,
) -> Optional[Dict]:
    """
    Get resource usage details for charts and summary display.

    Fetches allocation summary and daily charge breakdown for a specific
    resource on a project within a date range.

    Args:
        session: SQLAlchemy session
        projcode: Root project code (determines allocation / Resource Summary card)
        resource_name: Resource name (e.g., 'Derecho', 'GLADE')
        start_date: Start of date range
        end_date: End of date range
        include_adjustments: If True (default), include manual charge adjustments
                             in both the resource_summary and daily_charges data.
        scope_projcode: Optional project code for scoping daily charges. When
                        provided (and different from projcode), the daily charge
                        trend uses this project's MPTT subtree. When the scope
                        project has children, subtree aggregation is used; when
                        it is a leaf, only its direct charges are included.
                        Defaults to projcode (root — include all descendants).

    Returns:
        Dictionary with structure:
        {
            'project': Project object,
            'resource_obj': Resource object,
                # ``resource_obj``, not ``resource``: everywhere else in the
                # query layer that key holds the resource NAME STRING, and a
                # scope filter keying off ``row['resource']`` assumes a string.
            'resource_summary': {
                'resource_name': str,
                'allocated': float,
                'used': float,
                'remaining': float,
                'percent_used': float,
                'charges_by_type': Dict[str, float],
                'start_date': datetime,
                'end_date': datetime,
                'status': str
            },
            'daily_charges': {
                  'dates': [],
                  'values': [],
            },
        }
        Returns None if project or resource not found.
    """
    # Find project
    project = Project.get_by_projcode(session, projcode)
    if not project:
        return None

    # Find resource
    resource = Resource.get_by_name(session, resource_name)
    if not resource:
        return None

    # Get allocation usage for this specific resource
    all_usage = project.get_detailed_allocation_usage(
        resource_name=resource_name,
        include_adjustments=include_adjustments
    )

    resource_summary = all_usage.get(resource_name)
    if not resource_summary:
        # No allocation for this resource
        resource_summary = {
            'resource_name': resource_name,
            'allocation_id': None,  # No allocation exists
            'account_id': None,
            'allocated': 0.0,
            'used': 0.0,
            'remaining': 0.0,
            'percent_used': 0.0,
            'charges_by_type': {},
            'start_date': None,
            'end_date': None,
            'status': 'No Allocation'
        }

    # Determine resource type to query appropriate tables
    resource_type = resource.resource_type.resource_type if resource.resource_type else ResourceTypeName.HPC

    # Surface the type so the template can label the allocation figure with
    # its unit (hours / TiB) via the alloc_unit filter.
    resource_summary['resource_type'] = str(resource_type)

    # Resolve the scope project (controls daily charge aggregation)
    if scope_projcode and scope_projcode != projcode:
        scope_proj = Project.get_by_projcode(session, scope_projcode)
        if not scope_proj:
            scope_proj = project
    else:
        scope_proj = project  # default: root project = include all descendants

    # Use subtree MPPT when the scope project has children and valid tree coords
    use_subtree = bool(
        scope_proj.has_children
        and scope_proj.tree_root
        and scope_proj.tree_left
        and scope_proj.tree_right
    )

    if use_subtree:
        # Use MPPT join pattern (same as Project.get_subtree_charges) to aggregate
        # daily charges across this project and all descendants.
        results = None
        if ResourceTypeName.is_compute(resource_type):
            # Pull num_jobs + core_hours alongside charges so the Usage
            # Trend pill selector can switch metrics without an extra
            # query. Disk/Archive summaries don't carry these columns.
            results = session.query(
                CompChargeSummary.activity_date,
                func.sum(CompChargeSummary.charges).label('charges'),
                func.sum(CompChargeSummary.num_jobs).label('num_jobs'),
                func.sum(CompChargeSummary.core_hours).label('core_hours'),
            ).join(Account, CompChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date,
            ).group_by(CompChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.DISK:
            results = session.query(
                DiskChargeSummary.activity_date,
                func.sum(DiskChargeSummary.charges).label('charges')
            ).join(Account, DiskChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date,
            ).group_by(DiskChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.ARCHIVE:
            results = session.query(
                ArchiveChargeSummary.activity_date,
                func.sum(ArchiveChargeSummary.charges).label('charges')
            ).join(Account, ArchiveChargeSummary.account_id == Account.account_id)\
             .join(Project, Account.project_id == Project.project_id)\
             .filter(
                Project.tree_root == scope_proj.tree_root,
                Project.tree_left  >= scope_proj.tree_left,
                Project.tree_right <= scope_proj.tree_right,
                Account.resource_id == resource.resource_id,
                Account.deleted == False,
                ArchiveChargeSummary.activity_date >= start_date,
                ArchiveChargeSummary.activity_date <= end_date,
            ).group_by(ArchiveChargeSummary.activity_date).all()

        daily_map = {}
        daily_jobs_map: dict = {}
        daily_core_hours_map: dict = {}
        if results:
            comp = ResourceTypeName.is_compute(resource_type)
            for row in results:
                d = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
                daily_map[d] = daily_map.get(d, 0.0) + float(row.charges or 0.0)
                if comp:
                    daily_jobs_map[d] = daily_jobs_map.get(d, 0) + int(row.num_jobs or 0)
                    daily_core_hours_map[d] = daily_core_hours_map.get(d, 0.0) + float(row.core_hours or 0.0)

        if include_adjustments:
            # Collect all subtree account IDs for adjustment lookup
            subtree_account_ids = [
                row.account_id for row in
                session.query(Account.account_id)
                .join(Project, Account.project_id == Project.project_id)
                .filter(
                    Project.tree_root == scope_proj.tree_root,
                    Project.tree_left  >= scope_proj.tree_left,
                    Project.tree_right <= scope_proj.tree_right,
                    Account.resource_id == resource.resource_id,
                    Account.deleted == False,
                ).all()
            ]
            if subtree_account_ids:
                for d, amount in get_adjustment_totals_by_date(
                    session, subtree_account_ids, start_date, end_date
                ).items():
                    daily_map[d] = daily_map.get(d, 0.0) + amount

    else:
        # Single-account path: use the scope project's account (may differ from root)
        account = Account.get_by_project_and_resource(
            session,
            scope_proj.project_id,
            resource.resource_id,
            exclude_deleted=True
        )

        if not account:
            return {
                'project': project,
                'resource_obj': resource,
                'resource_summary': resource_summary,
                'daily_charges': { 'dates': None, 'values': None },
                'daily_jobs': None,
                'daily_core_hours': None,
            }

        results = None

        if ResourceTypeName.is_compute(resource_type):
            results = session.query(
                CompChargeSummary.activity_date,
                func.sum(CompChargeSummary.charges).label('charges'),
                func.sum(CompChargeSummary.num_jobs).label('num_jobs'),
                func.sum(CompChargeSummary.core_hours).label('core_hours'),
            ).filter(
                CompChargeSummary.account_id == account.account_id,
                CompChargeSummary.activity_date >= start_date,
                CompChargeSummary.activity_date <= end_date
            ).group_by(CompChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.DISK:
            results = session.query(
                DiskChargeSummary.activity_date,
                func.sum(DiskChargeSummary.charges).label('charges')
            ).filter(
                DiskChargeSummary.account_id == account.account_id,
                DiskChargeSummary.activity_date >= start_date,
                DiskChargeSummary.activity_date <= end_date
            ).group_by(DiskChargeSummary.activity_date).all()

        elif resource_type == ResourceTypeName.ARCHIVE:
            results = session.query(
                ArchiveChargeSummary.activity_date,
                func.sum(ArchiveChargeSummary.charges).label('charges')
            ).filter(
                ArchiveChargeSummary.account_id == account.account_id,
                ArchiveChargeSummary.activity_date >= start_date,
                ArchiveChargeSummary.activity_date <= end_date
            ).group_by(ArchiveChargeSummary.activity_date).all()

        daily_map = {}
        daily_jobs_map = {}
        daily_core_hours_map = {}
        if results:
            comp = ResourceTypeName.is_compute(resource_type)
            for row in results:
                d = row.activity_date.date() if hasattr(row.activity_date, 'date') else row.activity_date
                daily_map[d] = daily_map.get(d, 0.0) + float(row.charges or 0.0)
                if comp:
                    daily_jobs_map[d] = daily_jobs_map.get(d, 0) + int(row.num_jobs or 0)
                    daily_core_hours_map[d] = daily_core_hours_map.get(d, 0.0) + float(row.core_hours or 0.0)

        if include_adjustments:
            for d, amount in get_adjustment_totals_by_date(
                session, [account.account_id], start_date, end_date
            ).items():
                daily_map[d] = daily_map.get(d, 0.0) + amount

    sorted_dates = sorted(daily_map.keys())
    daily_charges = { 'dates': sorted_dates, 'values': [daily_map[d] for d in sorted_dates] }
    # Jobs / core-hours series populate only for compute resources.
    # Disk/Archive summaries don't expose these columns; their series stay None
    # and the Usage Trend pill selector renders only the Charges pill.
    if daily_jobs_map:
        jobs_dates = sorted(daily_jobs_map.keys())
        daily_jobs = {'dates': jobs_dates, 'values': [daily_jobs_map[d] for d in jobs_dates]}
    else:
        daily_jobs = None
    if daily_core_hours_map:
        ch_dates = sorted(daily_core_hours_map.keys())
        daily_core_hours = {'dates': ch_dates, 'values': [daily_core_hours_map[d] for d in ch_dates]}
    else:
        daily_core_hours = None

    return {
        'project': project,
        'resource_obj': resource,
        'resource_summary': resource_summary,
        'daily_charges': daily_charges,
        'daily_jobs': daily_jobs,
        'daily_core_hours': daily_core_hours,
    }

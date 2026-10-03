"""
Allocations dashboard blueprint for admin/staff.

Provides drill-down allocation dashboard showing allocation summaries
grouped hierarchically by Resource -> Facility -> Allocation Type -> Projects.
"""


from flask import (
    render_template, request, flash, redirect, url_for, jsonify, Response,
)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
from typing import List, Dict

from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils import age_bands
from webapp.utils.htmx import (
    handle_htmx_form_post, read_flag, read_layout, read_page, read_switch,
    read_sort, read_tab, read_theme, register_typeahead,
)
from sam.projects.projects import Project
from sam.queries.allocations import (
    ALLOCATION_TRANSACTION_SORT_COLUMNS,
    count_recent_allocation_transactions,
    get_allocation_summary,
    get_recent_allocation_transactions,
    _aggregate_usage_to_total,
)
from sam.queries.charges import (
    CHARGE_ADJUSTMENT_SORT_COLUMNS,
    count_recent_charge_adjustments,
    get_recent_charge_adjustments,
)
from sam.queries.usage_cache import (
    cached_allocation_usage, cached_allocation_usage_rows, cached_charges_by_facility_type,
    purge_usage_cache, usage_cache_info,
)
from sam.queries.lookups import find_project_by_code
from sam.export import Column, build_workbook
from sam.schemas.forms import CreateChargeAdjustmentForm
from flask import abort
from webapp.utils.rbac import (
    apply_facility_scope, filter_rows_by_facility,
    has_permission_any_facility,
    require_permission, require_permission_any_facility, user_facility_scope,
    Permission, allowed_facility_names as _allowed_facility_names,
)
from webapp.api.access_control import require_project_access
from sam.resources.facilities import Facility
from sam.resources.resources import Resource
from ..charts import (
    generate_allocation_sunburst,
    generate_pace_chart_matplotlib,
    PACE_WINDOW_DAYS,
)
from ..charts.theme import facility_slots

from . import bp

# Resources to hide by default from the dashboard
HIDDEN_RESOURCES = ["CMIP Analysis Platform", "Data_Access", "HPC_Futures_Lab"]


def group_by_resource_facility(summary_data: List[Dict]) -> Dict:
    """
    Transform flat summary list into nested structure for tabs.

    Args:
        summary_data: List of allocation summary dicts from get_allocation_summary()

    Returns:
        Nested dict structure:
        {
            'Derecho': {
                'UNIV': [
                    {'allocation_type': 'NSC', 'total_amount': 641710650, 'count': 26, ...},
                    {'allocation_type': 'Small', 'total_amount': 177267070, 'count': 248, ...}
                ],
                'WNA': [...]
            },
            'Casper': {...}
        }
    """
    grouped = {}
    for row in summary_data:
        resource = row['resource']
        facility = row['facility']

        if resource not in grouped:
            grouped[resource] = {}
        if facility not in grouped[resource]:
            grouped[resource][facility] = []

        grouped[resource][facility].append(row)

    return grouped


def get_all_facility_usage_overviews(session, resource_names: List[str], active_at: datetime,
                                      force_refresh: bool = False, _usage=None) -> Dict[str, List[Dict]]:
    """
    Calculate facility-level usage summaries for multiple resources.

    Like get_all_facility_overviews() but aggregates total_used (actual charges)
    instead of total_amount (allocated). Used to build usage-based pie charts.

    Args:
        _usage: Optional pre-computed per-project usage list from cached_allocation_usage
                (projcode=None). When provided, skips the internal DB call.

    Returns:
        Dict mapping resource_name -> list of facility overview dicts with total_used
    """
    if not resource_names:
        return {}

    if _usage is not None:
        # Filter pre-fetched data to only the requested resources
        resource_set = set(resource_names)
        individual_allocations = [a for a in _usage if a.get('resource') in resource_set]
    else:
        individual_allocations = cached_allocation_usage(
            session=session,
            resource_name=resource_names,
            facility_name=None,
            allocation_type=None,
            projcode=None,
            active_only=True,
            active_at=active_at,
            force_refresh=force_refresh,
            root_only=True,  # Exclude inheriting child allocations — root amount == total
        )

    # Group by resource, then aggregate total_used by facility
    resource_facility_totals: Dict[str, Dict[str, Dict]] = {}
    for alloc in individual_allocations:
        resource = alloc['resource']
        facility = alloc['facility']
        if resource not in resource_facility_totals:
            resource_facility_totals[resource] = {}
        if facility not in resource_facility_totals[resource]:
            resource_facility_totals[resource][facility] = {
                'total_amount': 0.0, 'total_used': 0.0, 'count': 0
            }

        bucket = resource_facility_totals[resource][facility]
        bucket['total_amount'] += alloc.get('total_amount', 0.0)
        bucket['total_used'] += alloc.get('total_used', 0.0)
        bucket['count'] += alloc.get('count', 0)

    overviews = {}
    for resource, facilities in resource_facility_totals.items():
        grand_total_used = sum(f['total_used'] for f in facilities.values())
        overview = []
        for facility, data in facilities.items():
            percent = (data['total_used'] / grand_total_used * 100) if grand_total_used > 0 else 0
            overview.append({
                'facility': facility,
                'total_amount': data['total_amount'],
                'total_used': data['total_used'],
                'annualized_rate': data['total_used'],  # chart fn reads this field
                'count': data['count'],
                'percent': percent
            })
        overview.sort(key=lambda x: x['total_used'], reverse=True)
        overviews[resource] = overview

    return overviews


def get_all_facility_overviews(session, resource_names: List[str], active_at: datetime):
    """
    Calculate facility-level summaries for multiple resources in a single query.

    Fetches individual allocations for all requested resources at once, then
    aggregates by resource and facility. Avoids N+1 queries.

    Returns:
        Tuple of:
          - Dict mapping resource_name -> list of facility overview dicts
          - Dict mapping (resource, facility, allocation_type) -> annualized_rate float
            (summed from the same per-project rows; sum of type rates == facility rate)
    """
    if not resource_names:
        return {}, {}

    individual_allocations = get_allocation_summary(
        session=session,
        resource_name=resource_names,
        facility_name=None,
        allocation_type=None,
        projcode=None,
        active_only=True,
        active_at=active_at,
        root_only=True,  # Exclude inheriting child allocations — root amount == total
    )

    # Group by resource+facility (for pie charts / overview table)
    # and by resource+facility+type (for per-type annual rate column)
    resource_facility_totals: Dict[str, Dict[str, Dict]] = {}
    type_rate_totals: Dict[tuple, float] = {}

    for alloc in individual_allocations:
        resource = alloc['resource']
        facility = alloc['facility']
        alloc_type = alloc['allocation_type']

        if resource not in resource_facility_totals:
            resource_facility_totals[resource] = {}
        if facility not in resource_facility_totals[resource]:
            resource_facility_totals[resource][facility] = {
                'total_amount': 0.0, 'annualized_rate': 0.0, 'count': 0
            }

        bucket = resource_facility_totals[resource][facility]
        bucket['total_amount'] += alloc['total_amount']
        bucket['count'] += alloc['count']
        if alloc.get('annualized_rate') is not None:
            bucket['annualized_rate'] += alloc['annualized_rate']
            type_key = (resource, facility, alloc_type)
            type_rate_totals[type_key] = type_rate_totals.get(type_key, 0.0) + alloc['annualized_rate']

    overviews = {}
    for resource, facilities in resource_facility_totals.items():
        total_rate = sum(f['annualized_rate'] for f in facilities.values())
        overview = []
        for facility, data in facilities.items():
            percent = (data['annualized_rate'] / total_rate * 100) if total_rate > 0 else 0
            overview.append({
                'facility': facility,
                'total_amount': data['total_amount'],
                'annualized_rate': data['annualized_rate'],
                'count': data['count'],
                'percent': percent
            })
        overview.sort(key=lambda x: x['annualized_rate'], reverse=True)
        overviews[resource] = overview

    return overviews, type_rate_totals


def _share(part, whole):
    return part * 100 / whole if whole else None


def elapsed_weights(rows, active_at):
    """``{(resource, facility, type): (sum of amount x elapsed fraction, sum of amount)}``.

    Open-ended and undated rows are left out. A multi-allocation row spans its
    earliest start to its latest end, close enough for an aggregate tick.
    """
    weights = {}
    for r in rows:
        start, end = r.get('start_date'), r.get('end_date')
        if r.get('is_open_ended') or not start or not end or end <= start:
            continue
        frac = min(max((active_at - start) / (end - start), 0.0), 1.0)
        amount = r.get('total_amount') or 0.0
        key = (r['resource'], r['facility'], r['allocation_type'])
        w, a = weights.get(key, (0.0, 0.0))
        weights[key] = (w + amount * frac, a + amount)
    return weights


def _usage_cols(row, weights):
    """Remaining, % used and the amount-weighted % elapsed of a tree row, in place."""
    row['remaining'] = row['total_amount'] - row['used']
    row['pct_used'] = _share(row['used'], row['total_amount'])
    row['elapsed_w'], row['elapsed_amount'] = weights
    row['elapsed_pct'] = _share(row['elapsed_w'], row['elapsed_amount'])


def build_facility_trees(grouped_data, overviews, type_rates, usage_overviews,
                         usage_by_type, resource_types, facilities, elapsed_by_type=None):
    """{resource: [facility row with nested type rows]} for the tree table and sunbursts.

    ``facilities`` is ``[(facility_id, facility_name, is_active)]`` for every facility:
    slots come from the active ones, so a scoped user sees the same hues as everyone.
    A row's ``alloc`` is its annualized rate, or its data volume on storage; its
    shares are of the parent row (a facility of the resource, a type of its facility).
    ``elapsed_by_type`` is `elapsed_weights` output; storage rows get no elapsed tick.
    """
    elapsed_by_type = elapsed_by_type or {}
    ids = {name: fid for fid, name, _ in facilities}
    slots = facility_slots(fid for fid, _, active in facilities if active)
    trees = {}
    for rn, by_facility in grouped_data.items():
        storage = resource_types.get(rn) in ('DISK', 'ARCHIVE')
        overview = {o['facility']: o for o in overviews.get(rn, [])}
        used_by_fac = {o['facility']: o.get('total_used', 0.0) for o in usage_overviews.get(rn, [])}
        rows = []
        for fac, types in by_facility.items():
            ov = overview.get(fac, {})
            amount = ov.get('total_amount', sum(t['total_amount'] for t in types))
            count = ov.get('count', sum(t['count'] for t in types))
            fid = ids.get(fac)
            type_rows = []
            for t in sorted(types, key=lambda t: t['allocation_type']):
                name = t['allocation_type']
                row = {
                    'name': name, 'count': t['count'], 'total_amount': t['total_amount'],
                    'alloc': t['total_amount'] if storage else type_rates.get((rn, fac, name), 0.0),
                    'used': usage_by_type.get((rn, fac, name), 0.0),
                }
                _usage_cols(row, (0.0, 0.0) if storage else elapsed_by_type.get((rn, fac, name), (0.0, 0.0)))
                type_rows.append(row)
            row = {
                'id': fid, 'key': fid if fid is not None else fac, 'facility': fac,
                'slot': slots.get(fid), 'count': count, 'total_amount': amount,
                'alloc': amount if storage else ov.get('annualized_rate', 0.0),
                'used': used_by_fac.get(fac, 0.0), 'types': type_rows,
            }
            _usage_cols(row, (sum(t['elapsed_w'] for t in type_rows),
                              sum(t['elapsed_amount'] for t in type_rows)))
            rows.append(row)
        rows.sort(key=lambda r: (r['slot'] is None, r['id'] is None, r['id'] or 0, r['facility']))
        total_alloc = sum(r['alloc'] for r in rows)
        for r in rows:
            r['alloc_share'] = _share(r['alloc'], total_alloc)
            for t in r['types']:
                t['alloc_share'] = _share(t['alloc'], r['alloc'])
        trees[rn] = rows
    return trees


def sunburst_rows(tree, measure):
    """The two-ring chart input for one resource's tree, ``measure`` = 'alloc' | 'used'."""
    return [{'id': r['id'], 'facility': r['facility'], 'slot': r['slot'], 'value': r[measure],
             'types': [{'name': t['name'], 'value': t[measure]} for t in r['types']]}
            for r in tree]


def window_sunburst_rows(charges, facilities, scale):
    """Two-ring input from `get_charges_by_facility_type` rows, each value times ``scale``,
    facilities in `build_facility_trees` order so hues and positions match the rate ring."""
    ids = {name: fid for fid, name, _ in facilities}
    slots = facility_slots(fid for fid, _, active in facilities if active)
    by_facility = {}
    for row in charges:
        types = by_facility.setdefault(row['facility'], {})
        name = row['allocation_type'] or 'Unknown'
        types[name] = types.get(name, 0.0) + row['charges'] * scale
    rows = [{'id': ids.get(fac), 'facility': fac or 'Unknown', 'slot': slots.get(ids.get(fac)),
             'value': sum(types.values()),
             'types': [{'name': n, 'value': v} for n, v in sorted(types.items())]}
            for fac, types in by_facility.items()]
    rows.sort(key=lambda r: (r['slot'] is None, r['id'] is None, r['id'] or 0, r['facility']))
    return rows


def get_resource_types(session) -> Dict[str, str]:
    """
    Get mapping of resource name to resource type.

    Returns:
        Dict mapping resource_name -> resource_type string (e.g., 'Derecho' -> 'HPC')
    """
    from sam.resources.resources import ResourceType

    resources = session.query(Resource.resource_name, ResourceType.resource_type)\
        .join(Resource.resource_type)\
        .all()

    return {r.resource_name: r.resource_type for r in resources}


@bp.route('/')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def index():
    """Bare section URL — redirect to the default page (Projects)."""
    return redirect(url_for('allocations_dashboard.projects'))


#: Age ladder behind the window control on the three audit-style panels
#: (Transactions, Adjustments, XRAS action log). Cumulative upper bound in days,
#: ``None`` closing the last band — the shape ``webapp.utils.age_bands`` consumes.
#:
#: Byte-identical to ``webapp.jobs.service.JOBS_AGE_BANDS`` on purpose and
#: deliberately NOT imported from it: ladders are domain-owned, and importing
#: that module would couple three ungated allocations pages to a plugin-adjacent
#: one. The vocabulary matches so a viewer reads one ladder across the app; the
#: ownership does not.
#:
#: WARNING: band 1's upper bound (30) is the same 30 as the
#: ``timedelta(days=30)`` default in ``_parse_audit_filters`` /
#: ``_parse_xras_filters`` and in the two page contexts below. That coupling is
#: what makes the resting control land on a whole span rather than "Custom
#: range" on every first load. Change one, change all four --
#: ``test_the_default_audit_window_is_a_whole_span`` is the tripwire.
AUDIT_AGE_BANDS = (
    ('< 1 Week', 7),
    ('1-4 Weeks', 30),
    ('1-3 Months', 90),
    ('3-6 Months', 180),
    ('6-12 Months', 365),
    ('1-2 Years', 730),
    ('2+ Years', None),
)


def _window_control_context(anchor, start_str, end_str):
    """Ladder + current span for an audit-style panel's window control.

    The direct analogue of ``_age_band_ctx`` (``webapp/jobs/routes.py``), and it
    shares that function's load-bearing property: the control writes
    ``start_date``/``end_date`` **directly**, so it never interacts with the
    ``days`` field or with the absent-vs-empty rule the two parsers below
    implement. Nothing about the parsers changes because nothing about the
    submitted parameters changes — the panel already sent this exact pair.

    ``or None`` on both bounds is not cosmetic: :func:`age_bands.bands_for`
    tests ``after is None``, not falsiness, so handing it a raw ``''`` would
    render the "custom" state for what is actually the open-ended band.
    """
    return {
        'age_bands': age_bands.band_map(AUDIT_AGE_BANDS, anchor,
                                        'start_date', 'end_date'),
        'age_band_span': age_bands.bands_for(AUDIT_AGE_BANDS, anchor,
                                             start_str or None, end_str or None),
        'layout': read_layout(),
    }


def _audit_page_context():
    """Shared template context for the Transactions / Adjustments pages.

    Both pages are thin shells whose tables load via HTMX fragments; the
    page itself only needs the filter-form vocabulary: the default date
    window, the resource list, and the user's allowed facility set (for
    the Facilities multi-select — enforcement happens server-side in the
    fragment routes via apply_facility_scope).
    """
    audit_end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    audit_start_date = audit_end_date - timedelta(days=30)

    all_resources = [
        r.resource_name for r in db.session.query(Resource.resource_name)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    ]

    allowed_facility_names = _allowed_facility_names(
        current_user, Permission.VIEW_PROJECTS)

    start_str = audit_start_date.strftime('%Y-%m-%d')
    end_str = audit_end_date.strftime('%Y-%m-%d')

    return {
        'audit_start_date': start_str,
        'audit_end_date': end_str,
        'all_resources': all_resources,
        'allowed_facility_names': allowed_facility_names,
        **_window_control_context(audit_end_date, start_str, end_str),
    }


@bp.route('/transactions')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def transactions():
    """Allocation transactions audit log page."""
    return render_template(
        'dashboards/allocations/transactions.html',
        **_audit_page_context(),
    )


@bp.route('/adjustments')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def adjustments():
    """Charge adjustments audit log page."""
    return render_template(
        'dashboards/allocations/adjustments.html',
        **_audit_page_context(),
    )


@bp.route('/projects')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
@cache.cached(make_cache_key=user_aware_cache_key)
def projects():
    """
    Main allocations dashboard page.

    Shows allocation summaries grouped by Resource -> Facility -> Type.
    Active allocations only, with optional date filter and resource selector.

    Query parameters:
        active_at: Date to check for active status (YYYY-MM-DD), default: today
        resources: List of resource names to display
    """
    # Parse active_at parameter (default to today at midnight)
    active_at_str = request.args.get('active_at')
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'error')
            active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Allow cache bypass for debugging / stale data
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    # The table's row filter; the summaries and charts are always root-only.
    root_only = read_switch(request.args, 'root_only', default=True)

    # The sunbursts render inline (no htmx request), so the layout arrives on the
    # cookie and `user_aware_cache_key` partitions the cached HTML by it and by
    # theme, or the first visitor would pick phone-sized or dark charts for all.
    layout, theme = read_layout(), read_theme()

    # ``allowed_facility_names`` is the user's universe -- every active facility
    # if unscoped, their grant if scoped. ``selected_facilities`` is the
    # displayed subset from ``?facilities=...``, clamped against it, so a forged
    # out-of-scope value falls back to the full allowed set rather than widening
    # or erroring.
    allowed_facility_names = _allowed_facility_names(
        current_user, Permission.VIEW_PROJECTS)
    requested_facilities = request.args.getlist('facilities')
    selected_facilities = apply_facility_scope(
        requested_facilities, Permission.VIEW_PROJECTS,
        default=allowed_facility_names,
    )
    # Normalize: ``apply_facility_scope`` returns ``None`` for the
    # unscoped-with-no-request path; for row filtering we need the
    # effective allowed set either way.
    effective_facilities = (
        allowed_facility_names if selected_facilities is None
        else list(selected_facilities)
    )

    # Get all active resources for the selector
    all_resources = [
        r.resource_name for r in db.session.query(Resource.resource_name)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    ]

    # Parse selected resources
    selected_resources = request.args.getlist('resources')
    if not selected_resources:
        # Default subset: all active resources except HIDDEN_RESOURCES
        selected_resources = [r for r in all_resources if r not in HIDDEN_RESOURCES]

    # Get summary data grouped by Resource, Facility, Type (sum across projects)
    # We use projcode="TOTAL" to sum across all projects
    summary_data = get_allocation_summary(
        session=db.session,
        resource_name=selected_resources, # Filtered list
        facility_name=None,      # Group by all facilities
        allocation_type=None,    # Group by all types
        projcode="TOTAL",        # Sum across projects
        active_only=True,
        active_at=active_at,
        root_only=True,          # Exclude inheriting child allocations — root amount == total
    )

    # Drop rows for facilities outside the user's effective selection.
    # Every downstream aggregator keys off row['facility'], so one
    # filter at the source cascades through grouped_data, overviews,
    # pace charts, and allocation-type charts.
    summary_data = filter_rows_by_facility(summary_data, effective_facilities)

    # Group results hierarchically for tab structure
    grouped_data = group_by_resource_facility(summary_data)

    # Shareable resource tab: ?tab=<slug> selects the active #resourceTabs pane.
    # read_tab lowercases, so the slug set and template comparison use the
    # lowercased "name with spaces -> underscores" form. Default = first sorted.
    tab_slugs = {name.replace(' ', '_').lower() for name in grouped_data}
    default_tab = (sorted(grouped_data, key=str.lower)[0].replace(' ', '_').lower()
                   if grouped_data else '')
    active_tab = read_tab('tab', tab_slugs, default_tab)

    # Get resource type mapping for conditional display
    resource_types = get_resource_types(db.session)

    # Batch-fetch all facility overviews in a single query.
    # Also returns per-type annualized rates (same query, grouped one level deeper).
    # The helper issues an un-facility-filtered fetch internally, so we
    # post-filter both returns to respect ``effective_facilities``.
    all_overviews, type_annualized_rates = get_all_facility_overviews(
        db.session, list(grouped_data.keys()), active_at,
    )
    if effective_facilities is not None:
        _allowed_set = set(effective_facilities)
        all_overviews = {
            rn: [row for row in rows if row.get('facility') in _allowed_set]
            for rn, rows in all_overviews.items()
        }
        type_annualized_rates = {
            key: rate for key, rate in type_annualized_rates.items()
            if key[1] in _allowed_set  # key is (resource, facility, allocation_type)
        }

    # Build usage-based charts.
    # Compute per-project usage ONCE; derive projcode="TOTAL" grouping Python-side
    # to avoid a second _fetch_all_allocations + full charge query pass.
    per_project_usage = cached_allocation_usage(
        session=db.session,
        resource_name=selected_resources,
        facility_name=None,
        allocation_type=None,
        projcode=None,      # Per-project rows; covers both usage views
        active_only=True,
        active_at=active_at,
        force_refresh=force_refresh,
        root_only=True,     # Exclude inheriting child allocations — root amount == total
    )
    # Scope filter: the Used column and sunburst key off row['facility'].
    per_project_usage = filter_rows_by_facility(per_project_usage, effective_facilities)

    # Derive TOTAL grouping (resource+facility+type, no projcode) Python-side
    usage_type_data = _aggregate_usage_to_total(per_project_usage)

    usage_by_type = {(r['resource'], r['facility'], r['allocation_type']): r.get('total_used', 0.0)
                     for r in usage_type_data}
    all_usage_overviews = get_all_facility_usage_overviews(
        db.session, list(grouped_data.keys()), active_at,
        _usage=per_project_usage,
    )

    facilities = _facility_index()
    trees = build_facility_trees(grouped_data, all_overviews, type_annualized_rates,
                                 all_usage_overviews, usage_by_type, resource_types, facilities,
                                 elapsed_weights(per_project_usage, active_at))
    sunbursts = {}
    for rn, tree in trees.items():
        storage = resource_types.get(rn) in ('DISK', 'ARCHIVE')
        sunbursts[rn] = {
            'alloc': generate_allocation_sunburst(
                sunburst_rows(tree, 'alloc'), center='Volume' if storage else 'Annual\nrate',
                layout=layout, theme=theme),
            # HPC/DAV usage loads as `htmx_used_sunburst`, over a trailing window.
            'used': generate_allocation_sunburst(
                sunburst_rows(tree, 'used'), center='Used', layout=layout, theme=theme)
            if storage else None,
        }

    # Pace charts render via HTMX, one loader per resource. Deferring the SVG
    # render here lets the selector buttons live inside the swap target, and
    # lets `nav-view-persistence.js` replay the persisted `sort_by` on first
    # load without this route having to know it.

    return render_template(
        'dashboards/allocations/projects.html',
        grouped_data=grouped_data,
        trees=trees,
        sunbursts=sunbursts,
        active_at=active_at.strftime('%Y-%m-%d'),
        active_tab=active_tab,
        all_resources=all_resources,
        selected_resources=selected_resources,
        resource_types=resource_types,
        allowed_facility_names=allowed_facility_names,
        selected_facilities=effective_facilities,
        root_only=root_only,
    )


_VALID_PACE_SORT_BY = ('size', 'past', 'future')


def _facility_index():
    """``[(facility_id, facility_name, is_active)]`` for every facility, as `build_facility_trees` takes it."""
    return [(f.facility_id, f.facility_name, f.is_active) for f in db.session.query(Facility)]


def _fragment_scope():
    """``(active_at, requested_facilities, selected_facilities)`` for an htmx fragment.

    Same active_at semantics as index(), but bad input falls back to today silently:
    an HTMX swap into a pane is the wrong place for a top-level alert. The
    facility clamp matches index(), so a WNA-scoped user gets WNA-only rows even
    though the URL omits ?facilities=; unscoped users get None (no filter).
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        active_at = datetime.strptime(request.args.get('active_at') or '', '%Y-%m-%d')
    except ValueError:
        active_at = today
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    requested = request.args.getlist('facilities')
    selected = apply_facility_scope(requested, Permission.VIEW_PROJECTS,
                                    default=(sorted(allowed) if allowed is not None else None))
    return active_at, requested, selected


@bp.route('/htmx/pace-chart/<resource_name>')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def htmx_pace_chart(resource_name):
    """Render the per-resource Pace chart with selector buttons.

    Used by both the initial HTMX `load` trigger on the dashboard
    (one loader per resource) and subsequent selector-button clicks.
    Selector state (``sort_by``) is persisted client-side by
    ``nav-view-persistence.js`` keyed on ``data-chart-persist-id``.
    """
    sort_by = request.args.get('sort_by', 'size')
    if sort_by not in _VALID_PACE_SORT_BY:
        sort_by = 'size'

    active_at, requested_facilities, selected_facilities = _fragment_scope()

    # One row per allocation across the drawn window, so allocations that ended
    # or start inside it get their bands. Disk keeps the active-only summary:
    # its "used" is current occupancy, which an ended allocation does not have.
    resource = Resource.get_by_name(db.session, resource_name)
    is_disk = (resource is not None and resource.resource_type is not None
               and resource.resource_type.resource_type == 'DISK')
    if is_disk:
        per_project_usage = cached_allocation_usage(
            session=db.session,
            resource_name=[resource_name],
            facility_name=None,
            allocation_type=None,
            projcode=None,
            active_only=True,
            active_at=active_at,
            root_only=True,
        )
    else:
        window = timedelta(days=PACE_WINDOW_DAYS)
        per_project_usage = cached_allocation_usage_rows(
            db.session, resource_name=[resource_name],
            window_start=active_at - window, window_end=active_at + window,
            as_of=active_at,
        )
    per_project_usage = filter_rows_by_facility(per_project_usage, selected_facilities)

    chart_svg = generate_pace_chart_matplotlib(
        per_project_usage, active_at, resource_name=resource_name,
        sort_by=sort_by, layout=read_layout(), theme=read_theme(),
    )

    # A stable HTML id, matching dashboard.html's
    # `data-resource="{{ resource_name|replace(' ', '_') }}"` convention. A
    # single-facility request includes the facility, so a per-facility card's
    # persisted sort_by does not collide with the resource-wide chart's.
    chart_dom_id = 'pace-chart-' + resource_name.replace(' ', '_')
    if len(requested_facilities) == 1:
        chart_dom_id += '-' + requested_facilities[0].replace(' ', '_')

    # Selector-button URLs MUST carry the original facility scope forward, or
    # clicking Sort-by on a per-facility card drops ?facilities= and the next
    # request un-narrows to the whole resource, leaking cross-facility projects
    # into a facility-scoped chart. Pass the requested list verbatim.
    selector_kwargs = {
        'sort_by': sort_by,
        'active_at': active_at.strftime('%Y-%m-%d'),
    }
    if requested_facilities:
        selector_kwargs['facilities'] = requested_facilities

    return render_template(
        'dashboards/allocations/partials/pace_chart.html',
        resource_name=resource_name,
        chart_svg=chart_svg,
        sort_by=sort_by,
        active_at=active_at.strftime('%Y-%m-%d'),
        chart_dom_id=chart_dom_id,
        selector_kwargs=selector_kwargs,
    )


#: The Used sunburst's trailing windows, in days; the last is the default.
_USED_WINDOW_DAYS = (30, 90, 180, 365)


@bp.route('/htmx/used-sunburst/<resource_name>')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def htmx_used_sunburst(resource_name):
    """An HPC/DAV resource's Used sunburst: charges over a trailing window, across
    allocation renewals, at an annual rate so it reads against the Annual rate ring."""
    days = request.args.get('days', type=int)
    if days not in _USED_WINDOW_DAYS:
        days = _USED_WINDOW_DAYS[-1]
    active_at, requested_facilities, selected_facilities = _fragment_scope()

    charges = cached_charges_by_facility_type(
        db.session, resource_names=[resource_name],
        start=active_at - timedelta(days=days - 1), end=active_at)
    charges = filter_rows_by_facility(charges, selected_facilities)
    facilities = _facility_index()
    chart_svg = generate_allocation_sunburst(
        window_sunburst_rows(charges, facilities, 365 / days), center='Use\nrate',
        layout=read_layout(), theme=read_theme())

    selector_kwargs = {'active_at': active_at.strftime('%Y-%m-%d')}
    if requested_facilities:   # carried forward, or a click widens a scoped chart
        selector_kwargs['facilities'] = requested_facilities
    return render_template(
        'dashboards/allocations/partials/used_sunburst.html',
        resource_name=resource_name, chart_svg=chart_svg, days=days,
        window_days=_USED_WINDOW_DAYS, active_at=active_at,
        chart_dom_id='used-sunburst-' + resource_name.replace(' ', '_'),
        selector_kwargs=selector_kwargs,
    )


@bp.route('/htmx/project_table')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
@cache.cached(make_cache_key=user_aware_cache_key)
def projects_fragment():
    """
    AJAX fragment showing individual projects for a specific Resource/Facility/Type.

    Query parameters:
        resource: Resource name (required)
        facility: Facility name (required)
        allocation_type: Allocation type (required)
        active_at: Date to check for active status (YYYY-MM-DD)
        root_only: 0/1, default on -- tree roots only, the rule the page's
            summaries and charts always apply

    Returns:
        HTML table fragment of projects
    """
    resource = request.args.get('resource')
    facility = request.args.get('facility')
    allocation_type = request.args.get('allocation_type')
    active_at_str = request.args.get('active_at')
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    root_only = read_switch(request.args, 'root_only', default=True)

    # Validate required params
    if not resource or not facility or not allocation_type:
        return '<p class="text-danger mb-0">Missing required parameters</p>'

    # 403, not clamp: unlike the index route the caller asked for exactly one
    # facility, so out-of-scope is a forged URL rather than a narrowing choice.
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if allowed is not None and facility not in allowed:
        abort(403)

    # Parse date
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            return '<p class="text-danger mb-0">Invalid date format</p>'
    else:
        active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Fetch projects with usage data
    projects = cached_allocation_usage(
        session=db.session,
        resource_name=resource,
        facility_name=facility,
        allocation_type=allocation_type,
        projcode=None,
        active_only=True,
        active_at=active_at,
        force_refresh=force_refresh,
        root_only=root_only,
    )

    if not projects:
        return '<p class="text-muted mb-0">No active projects found</p>'

    # Enrich with project titles
    from sam.projects.projects import Project
    for project_data in projects:
        project = find_project_by_code(db.session, project_data['projcode'])
        project_data['title'] = project.title if project else None

    # Sort by used descending
    projects.sort(key=lambda p: p.get('total_used', 0.0), reverse=True)

    # Get resource type for conditional display
    resource_types = get_resource_types(db.session)
    resource_type = resource_types.get(resource, 'HPC')  # Default to HPC if not found

    return render_template(
        'dashboards/allocations/partials/project_table.html',
        projects=projects,
        resource=resource,
        facility=facility,
        allocation_type=allocation_type,
        active_at=active_at.strftime('%Y-%m-%d'),
        active_at_dt=active_at,
        resource_type=resource_type,
        can_view_projects=True,  # route requires VIEW_PROJECTS
    )


# Per-project detail columns for the xlsx export (one sheet per resource).
_EXPORT_COLUMNS = [
    Column('facility', 'Facility', 14, 'text'),
    Column('allocation_type', 'Allocation Type', 18, 'text'),
    Column('projcode', 'Project', 12, 'text'),
    Column('title', 'Title', 44, 'text'),
    Column('pi', 'PI', 22, 'text'),
    Column('total_allocated', 'Allocated', 16, 'num'),
    Column('total_used', 'Used', 16, 'num'),
    Column('remaining', 'Remaining', 16, 'num'),
    Column('percent_used', '% Used', 10, 'pct'),
    Column('start_date', 'Start', 12, 'date'),
    Column('end_date', 'End', 12, 'date'),
]


@bp.route('/projects/export')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def projects_export():
    """Download the projects allocation view as xlsx: one sheet per resource."""
    active_at_str = request.args.get('active_at')
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Facility scope enforced at the source, exactly as projects() does: a
    # forged out-of-scope facility falls back to the user's full allowed set.
    allowed_facility_names = _allowed_facility_names(current_user, Permission.VIEW_PROJECTS)
    selected_facilities = apply_facility_scope(
        request.args.getlist('facilities'), Permission.VIEW_PROJECTS,
        default=allowed_facility_names,
    )
    effective_facilities = (
        allowed_facility_names if selected_facilities is None
        else list(selected_facilities)
    )

    all_resources = [
        r.resource_name for r in db.session.query(Resource.resource_name)
        .filter(Resource.is_active).order_by(Resource.resource_name).all()
    ]
    selected_resources = request.args.getlist('resources')
    if not selected_resources:
        selected_resources = [r for r in all_resources if r not in HIDDEN_RESOURCES]
    root_only = read_switch(request.args, 'root_only', default=True)

    # Enumerate the (resource, facility, type) combos in scope, then fetch
    # per-project detail per combo — the same cached call the detail fragment
    # makes, so a full export equals expanding every row on the page.
    summary_data = get_allocation_summary(
        session=db.session, resource_name=selected_resources,
        facility_name=None, allocation_type=None, projcode="TOTAL",
        active_only=True, active_at=active_at, root_only=True,
    )
    summary_data = filter_rows_by_facility(summary_data, effective_facilities)

    rows_by_resource: Dict[str, List[Dict]] = {}
    for combo in summary_data:
        detail = cached_allocation_usage(
            session=db.session, resource_name=combo['resource'],
            facility_name=combo['facility'], allocation_type=combo['allocation_type'],
            projcode=None, active_only=True, active_at=active_at, root_only=root_only,
        )
        rows_by_resource.setdefault(combo['resource'], []).extend(detail or [])

    _enrich_project_info(rows_by_resource)

    sheets = []
    for resource in selected_resources:
        rows = rows_by_resource.get(resource)
        if not rows:
            continue
        for row in rows:
            row['remaining'] = (row.get('total_allocated') or 0.0) - (row.get('total_used') or 0.0)
        rows.sort(key=lambda p: (
            p.get('facility') or '', p.get('allocation_type') or '', -(p.get('total_used') or 0.0)))
        sheets.append((resource, _EXPORT_COLUMNS, rows))
    if not sheets:
        sheets = [('Allocations', _EXPORT_COLUMNS, [])]

    filename = f'allocations_{active_at.strftime("%Y-%m-%d")}.xlsx'
    return Response(
        build_workbook(sheets),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


def _enrich_project_info(rows_by_resource: Dict[str, List[Dict]]) -> None:
    """Attach project title + PI name in one query (joinedload lead, no N+1)."""
    codes = {row['projcode'] for rows in rows_by_resource.values()
             for row in rows if row.get('projcode')}
    if not codes:
        return
    info = {
        p.projcode: (p.title, p.lead.display_name if p.lead else None)
        for p in db.session.query(Project).options(joinedload(Project.lead))
        .filter(Project.projcode.in_(codes)).all()
    }
    for rows in rows_by_resource.values():
        for row in rows:
            row['title'], row['pi'] = info.get(row.get('projcode'), (None, None))


def _parse_audit_filters(request_args, sort_whitelist):
    """Parse shared filter + sort + pagination params for the audit fragments.

    Returns ``(filters, sort, page)``:

    - ``filters``: dict of filter kwargs forwarded verbatim to the query/count
      function (``projcode``, ``resource_name``, ``username``, ``start_date``,
      ``end_date``). Blank values normalize to ``None`` so the query treats
      them as no-ops.
    - ``sort``: ``{'sort_by': str|None, 'sort_dir': 'asc'|'desc'}``.
    - ``page``: ``{'n': int ≥ 1, 'per_page': int clamped to [10, 200]}``.

    Default 30-day window is applied iff **neither** ``start_date`` nor
    ``end_date`` appears in the query string (empty bounds explicitly = all
    time).
    """
    projcode = (request_args.get('projcode') or '').strip() or None
    resource_names = request_args.getlist('resource_name') or None
    username = (request_args.get('username') or '').strip() or None
    start_date_str = (request_args.get('start_date') or '').strip()
    end_date_str = (request_args.get('end_date') or '').strip()

    if 'start_date' not in request_args and 'end_date' not in request_args:
        # First-load default: last 30 days, ending now.
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                      - timedelta(days=30))
        end_date = datetime.now()
    else:
        try:
            start_date = (datetime.strptime(start_date_str, '%Y-%m-%d')
                          if start_date_str else None)
        except ValueError:
            start_date = None
        try:
            end_date = (datetime.strptime(end_date_str, '%Y-%m-%d')
                        .replace(hour=23, minute=59, second=59)
                        if end_date_str else None)
        except ValueError:
            end_date = None

    filters = {
        'projcode': projcode,
        'resource_name': resource_names,
        'username': username,
        'start_date': start_date,
        'end_date': end_date,
    }

    return (filters,
            read_sort(request_args, sort_whitelist),
            read_page(request_args))


@bp.route('/transactions_fragment')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def transactions_fragment():
    """HTMX fragment: sortable, paginated table of recent allocation transactions."""
    filters, sort, page = _parse_audit_filters(
        request.args, ALLOCATION_TRANSACTION_SORT_COLUMNS,
    )
    # Intersect the user-chosen facility filter (shared with the index
    # route) against their scope. ``None`` -> unrestricted (unscoped
    # user, no ``?facilities=`` param); a list is passed through to
    # the query's ``facility_name`` kwarg for SQL-time filtering.
    filters['facility_name'] = apply_facility_scope(
        request.args.getlist('facilities'), Permission.VIEW_PROJECTS,
    )
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_allocation_transactions(
        db.session,
        **filters,
        sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
        offset=offset, limit=page['per_page'],
    )
    total = count_recent_allocation_transactions(db.session, **filters)

    return render_template(
        'dashboards/allocations/partials/transactions_table.html',
        rows=rows, total=total,
        page=page, sort=sort, filters=filters,
        fragment_url=url_for('allocations_dashboard.transactions_fragment'),
        target_id='alloc-transactions-fragment',
        form_id='tx-filters',
        sortable_columns=sorted(ALLOCATION_TRANSACTION_SORT_COLUMNS),
        can_view_projects=True,  # route requires VIEW_PROJECTS
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS),
    )


@bp.route('/adjustments_fragment')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def adjustments_fragment():
    """HTMX fragment: sortable, paginated table of recent charge adjustments."""
    filters, sort, page = _parse_audit_filters(
        request.args, CHARGE_ADJUSTMENT_SORT_COLUMNS,
    )
    filters['facility_name'] = apply_facility_scope(
        request.args.getlist('facilities'), Permission.VIEW_PROJECTS,
    )
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_charge_adjustments(
        db.session,
        **filters,
        sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
        offset=offset, limit=page['per_page'],
    )
    total = count_recent_charge_adjustments(db.session, **filters)

    return render_template(
        'dashboards/allocations/partials/adjustments_table.html',
        rows=rows, total=total,
        page=page, sort=sort, filters=filters,
        fragment_url=url_for('allocations_dashboard.adjustments_fragment'),
        target_id='alloc-adjustments-fragment',
        form_id='adj-filters',
        sortable_columns=sorted(CHARGE_ADJUSTMENT_SORT_COLUMNS),
        can_view_projects=True,  # route requires VIEW_PROJECTS
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS),
    )


@bp.route('/transaction_details/<int:transaction_id>')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def transaction_details(transaction_id: int):
    """HTMX fragment: full detail for a single allocation transaction.

    Ignores ``include_deleted`` / ``include_propagated`` at the user-facing
    filter level because we always want to render the row the user just
    clicked, even if its parent allocation has since been soft-deleted.
    """
    rows = get_recent_allocation_transactions(
        db.session,
        transaction_id=transaction_id,
        include_deleted=True,
        include_propagated=True,
    )
    if not rows:
        return '<p class="text-danger mb-0">Transaction not found.</p>'
    # Facility-scope the lookup: deny inspecting a transaction whose
    # project lives outside the user's allowed set.
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if allowed is not None and rows[0].get('facility') not in allowed:
        abort(403)
    return render_template(
        'dashboards/allocations/partials/transaction_details_modal.html',
        r=rows[0],
    )


@bp.route('/adjustment_details/<int:adjustment_id>')
@login_required
@require_permission_any_facility(Permission.VIEW_PROJECTS)
def adjustment_details(adjustment_id: int):
    """HTMX fragment: full detail for a single charge adjustment."""
    rows = get_recent_charge_adjustments(
        db.session,
        adjustment_id=adjustment_id,
        include_deleted=True,
    )
    if not rows:
        return '<p class="text-danger mb-0">Adjustment not found.</p>'
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if allowed is not None and rows[0].get('facility') not in allowed:
        abort(403)
    return render_template(
        'dashboards/allocations/partials/adjustment_details_modal.html',
        r=rows[0],
    )


@bp.route('/usage/<projcode>/<resource>')
@login_required
@require_project_access(include_ancestors=True)
def usage_modal(project, resource: str):
    """
    AJAX fragment showing detailed usage for a specific project+resource.

    Access: system VIEW_PROJECTS, direct project affiliation, or
    lead/admin of any ancestor in the project tree.

    Returns:
        HTML fragment for Bootstrap modal body showing usage breakdown
    """
    active_at_str = request.args.get('active_at')

    # Parse date
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            return '<p class="text-danger mb-0">Invalid date format</p>'
    else:
        active_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Get allocation with usage details
    usage_data = cached_allocation_usage(
        session=db.session,
        resource_name=resource,
        projcode=project.projcode,
        active_only=True,
        active_at=active_at
    )

    if not usage_data:
        return '<p class="text-muted mb-0">No active allocation found</p>'

    # Should only be one result
    allocation_info = usage_data[0] if usage_data else None

    return render_template(
        'dashboards/allocations/partials/usage_modal.html',
        project=project,
        resource=resource,
        allocation=allocation_info,
        active_at=active_at.strftime('%Y-%m-%d')
    )


@bp.route('/cache/purge', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def purge_cache():
    """
    Purge the usage calculation cache (requires edit_allocations permission).

    Accepts JSON (returns JSON) or form POST (redirects with flash message).
    """
    n = purge_usage_cache()
    if request.is_json or request.headers.get('HX-Request'):
        return jsonify({'status': 'ok', 'entries_cleared': n})
    flash(f'Usage cache cleared ({n} entries removed).', 'success')
    return redirect(url_for('allocations_dashboard.projects'))


@bp.route('/cache/status')
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def cache_status():
    """Return usage cache statistics as JSON (admin/staff only)."""
    return jsonify(usage_cache_info())


# Create Charge Adjustment -- the staff-facing write path for the Adjustments
# tab. The user enters a positive amount and ChargeAdjustment.create() applies
# the sign by type (Credits/Refunds negative, Debits/Reservations positive).
# Exposed types come from sam.accounting.adjustments._SIGN_BY_TYPE, resolved to
# rows via ChargeAdjustment.supported_types(session).


_CREATE_ADJUSTMENT_FORM_TEMPLATE = (
    'dashboards/allocations/fragments/create_adjustment_form_htmx.html'
)


def _create_adjustment_form_context():
    """Build the context dict used for initial render + error re-render."""
    from sam.accounting.adjustments import ChargeAdjustment
    return {
        'types': ChargeAdjustment.supported_types(db.session),
    }


@bp.route('/htmx/create_adjustment_form')
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_create_adjustment_form():
    """Return the Create Adjustment form fragment (loaded into the modal)."""
    ctx = _create_adjustment_form_context()
    return render_template(
        _CREATE_ADJUSTMENT_FORM_TEMPLATE,
        errors=[],
        form={},
        **ctx,
    )


def _search_projects_for_adjustment(q, active_only):
    from sam.queries.projects import search_projects_by_code_or_title
    return search_projects_by_code_or_title(db.session, q, active=True)[:10]


# Search-as-you-type for the Create Adjustment project picker: mirrors
# admin_dashboard.htmx_project_search_for_parent but guarded by
# EDIT_ALLOCATIONS, the permission that also gates the button. Shares the
# results template so fk-picker.js populates the hidden project_id input.
register_typeahead(
    bp, rule='/htmx/project_search_for_adjustment',
    endpoint='htmx_project_search_for_adjustment',
    permission=Permission.EDIT_ALLOCATIONS,
    search=_search_projects_for_adjustment,
    template='dashboards/admin/fragments/project_search_results_fk_htmx.html',
    ctx_key='projects', min_len=1,
)


@bp.route('/htmx/resources_for_project')
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_resources_for_project():
    """Return <option> fragment for the Resource select, filtered to
    the given project's active HPC/DAV accounts.

    Query string: project_id=<int>. If absent/empty/unknown, returns a
    single placeholder option so the select remains usable.
    """
    from sam.accounting.accounts import Account
    from sam.projects.projects import Project
    from sam.resources.resources import ResourceType

    project_id_str = (request.args.get('project_id') or '').strip()
    if not project_id_str:
        return '<option value="">-- Select a project first --</option>'
    try:
        project_id = int(project_id_str)
    except ValueError:
        return '<option value="">-- Select a project first --</option>'

    project = db.session.get(Project, project_id)
    if project is None:
        return '<option value="">-- Unknown project --</option>'

    rows = (
        db.session.query(Resource.resource_id, Resource.resource_name)
        .join(Account, Account.resource_id == Resource.resource_id)
        .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)
        .filter(
            Account.project_id == project.project_id,
            Account.is_active,
            Resource.is_active,
            ResourceType.resource_type.in_(('HPC', 'DAV')),
            ~Resource.resource_name.in_(HIDDEN_RESOURCES),
        )
        .distinct()
        .order_by(Resource.resource_name)
        .all()
    )

    if not rows:
        return (
            '<option value="">-- No compute accounts for this project --</option>'
        )

    opts = ['<option value="">-- Select a resource --</option>']
    for resource_id, resource_name in rows:
        opts.append(f'<option value="{resource_id}">{resource_name}</option>')
    return '\n'.join(opts)


@bp.route('/htmx/create_adjustment', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_create_adjustment():
    """Create a ChargeAdjustment row. Server applies the sign by type."""
    from sam.accounting.accounts import Account
    from sam.accounting.adjustments import ChargeAdjustment
    from sam.projects.projects import Project

    def do_action(data):
        project = db.session.get(Project, data['project_id'])
        if project is None:
            raise ValueError(f"Project {data['project_id']} not found")

        account = Account.get_by_project_and_resource(
            db.session, project.project_id, data['resource_id'],
            exclude_deleted=True,
        )
        if account is None:
            raise ValueError(
                f"No active account for project {project.projcode} on the "
                f"selected resource"
            )

        return ChargeAdjustment.create(
            db.session,
            account_id=account.account_id,
            charge_adjustment_type_id=data['charge_adjustment_type_id'],
            amount=data['amount'],
            adjusted_by_id=current_user.user_id,
            comment=data.get('comment'),
        )

    return handle_htmx_form_post(
        schema_cls=CreateChargeAdjustmentForm,
        template=_CREATE_ADJUSTMENT_FORM_TEMPLATE,
        do_action=do_action,
        success_triggers={
            'closeActiveModal': {},
            'refreshAdjustmentsTab': {},
        },
        success_message='Charge adjustment saved.',
        error_prefix='Error creating adjustment',
        context_fn=_create_adjustment_form_context,
    )





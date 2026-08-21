"""
Allocations dashboard blueprint for admin/staff.

Provides drill-down allocation dashboard showing allocation summaries
grouped hierarchically by Resource → Facility → Allocation Type → Projects.
"""


from flask import (
    Blueprint, render_template, request, flash, redirect, url_for, jsonify,
    current_app,
)
from flask_login import login_required, current_user
from datetime import date, datetime, timedelta
from typing import List, Dict

from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils import age_bands
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_modal_not_found, htmx_not_found, htmx_success,
    htmx_success_message, modal_triggers, read_flag, read_layout, read_page,
    read_sort, read_theme, register_typeahead,
)
from webapp.api.xras.recheck import recheck_action
from sam.integration.xras import XrasActivationEvent
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project
from webapp.utils.notify import get_notifier, notify_summary
from webapp.utils.project_permissions import can_edit_project_governance
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
from sam.queries.xras_actions import (
    XRAS_ACTION_SORT_COLUMNS,
    XRAS_ACTION_STATUSES,
    XRAS_ACTION_TYPES,
    XRAS_REQUEST_TOKEN_EXAMPLE,
    count_recent_xras_actions,
    get_observed_action_types,
    get_projects_by_ids,
    get_recent_xras_actions,
    summarize_xras_actions,
)
from sam.queries.xras_activation import (
    ACTIVITY_TAGS,
    get_latest_xras_action_id,
    get_xras_activation_events,
    get_xras_activity,
    get_xras_pending_recipients,
)
# Full dotted path, never through `sam.queries` — that package imports its
# submodules eagerly, and this one imports `sam.notify`. See the module
# docstring; `tests/unit/test_notify_import_graph.py` is the gate.
from sam.queries.xras_notices import build_xras_messages, load_xras_action
from sam.queries.xras_accounts import (
    CLASSIFICATION_ABSENT,
    CLASSIFICATION_INACTIVE,
    enrich_worklist,
    get_account_worklist,
    stamp_waiting_days,
    worklist_counts,
)
from sam.queries.usage_cache import cached_allocation_usage, purge_usage_cache, usage_cache_info
from sam.queries.lookups import find_project_by_code
from sam.schemas.forms import CreateChargeAdjustmentForm, XrasActivationEventForm
from flask import abort
from webapp.utils.rbac import (
    apply_facility_scope, filter_rows_by_facility, has_permission,
    require_permission, require_permission_any_facility, user_facility_scope,
    Permission, allowed_facility_names as _allowed_facility_names,
)
from webapp.api.access_control import require_project_access
from sam.resources.resources import Resource
from ..charts import (
    generate_facility_pie_chart_matplotlib,
    generate_allocation_type_pie_chart_matplotlib,
    generate_pace_chart_matplotlib,
)

bp = Blueprint('allocations_dashboard', __name__, url_prefix='/allocations')

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


def get_resource_types(session) -> Dict[str, str]:
    """
    Get mapping of resource name to resource type.

    Returns:
        Dict mapping resource_name → resource_type string (e.g., 'Derecho' → 'HPC')
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
#: Byte-identical to ``webapp.jobs.service.JOBS_AGE_BANDS`` on purpose, and
#: deliberately NOT imported from it. Ladders are domain-owned here (cf. the
#: fs-scans ``ATIME_BUCKETS`` / ``JOBS_AGE_BANDS`` split): importing
#: ``webapp.jobs.service`` would couple three ungated allocations pages to a
#: plugin-adjacent module. The vocabulary matches so a viewer reads one ladder
#: across the app; the ownership does not.
#:
#: ⚠️ Band 1's upper bound (30) is the same 30 as the ``timedelta(days=30)``
#: default in ``_parse_audit_filters`` / ``_parse_xras_filters`` and in the two
#: page contexts below. That coupling is what makes the resting control land on
#: a whole span instead of rendering "Custom range" on every first load. Change
#: one and you must change all four — ``test_the_default_audit_window_is_a_whole_span``
#: is the tripwire.
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

    Shows allocation summaries grouped by Resource → Facility → Type.
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

    # This route renders four pies inline, so it is one of the nine chart call
    # sites with no htmx request behind them — the layout arrives on the
    # cookie. `user_aware_cache_key` partitions the cached HTML by it; without
    # that, the first visitor to warm this page would decide whether everyone
    # else got phone-sized or desktop-sized pies. The theme rides the same
    # cookie mechanism and the same cache-key partition, and its failure is the
    # louder one — a light pie on a dark page.
    layout, theme = read_layout(), read_theme()

    # Facility scope resolution.
    # ``allowed_facility_names`` is the user's universe — every facility
    # they may *ever* see on this dashboard. For unscoped users that's
    # every active facility; for scoped users it's their grant.
    # ``selected_facilities`` is the currently-displayed subset, built
    # from ``?facilities=...`` clamped against allowed — so a forged
    # out-of-scope value falls back to the full allowed set rather than
    # widening or erroring.
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

    # Generate facility pie chart SVGs (cached via lru_cache)
    resource_overviews = {}
    for rn in grouped_data.keys():
        overview_data = all_overviews.get(rn, [])
        resource_overviews[rn] = {
            'table_data': overview_data,
            'chart': generate_facility_pie_chart_matplotlib(
                overview_data, layout=layout, theme=theme),
        }

    # Generate allocation type pie chart SVGs per resource/facility
    allocation_type_charts = {}
    for resource_name, facilities in grouped_data.items():
        allocation_type_charts[resource_name] = {}
        for facility_name, types in facilities.items():
            if len(types) > 1:
                allocation_type_charts[resource_name][facility_name] = \
                    generate_allocation_type_pie_chart_matplotlib(
                        types, layout=layout, theme=theme)
            else:
                allocation_type_charts[resource_name][facility_name] = None

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
    # Scope filter: pace charts, usage pies, allocation-type usage
    # charts all iterate this list and key off row['facility'].
    per_project_usage = filter_rows_by_facility(per_project_usage, effective_facilities)

    # Derive TOTAL grouping (resource+facility+type, no projcode) Python-side
    usage_type_data = _aggregate_usage_to_total(per_project_usage)

    # Index by resource → facility for allocation-type chart generation
    usage_by_resource_facility: Dict[str, Dict[str, List]] = {}
    for row in usage_type_data:
        usage_by_resource_facility\
            .setdefault(row['resource'], {})\
            .setdefault(row['facility'], [])\
            .append(row)

    allocation_type_usage_charts = {}
    for resource_name, facilities in grouped_data.items():
        allocation_type_usage_charts[resource_name] = {}
        for facility_name, types in facilities.items():
            usage_rows = usage_by_resource_facility.get(resource_name, {}).get(facility_name, [])
            # Reuse allocation type chart fn — build minimal dicts with total_used as value
            # (must exclude non-hashable fields like charges_by_type)
            chartable = [
                {
                    'allocation_type': row['allocation_type'],
                    'total_amount': row.get('total_used', 0.0),
                    'count': row.get('count', 0),
                    'avg_amount': row.get('total_used', 0.0),
                }
                for row in usage_rows
                if row.get('total_used', 0.0) > 0
            ]
            if len(chartable) > 1:
                allocation_type_usage_charts[resource_name][facility_name] = \
                    generate_allocation_type_pie_chart_matplotlib(
                        chartable, layout=layout, theme=theme)
            else:
                allocation_type_usage_charts[resource_name][facility_name] = None

    # Build usage-based facility pie charts — reuse per_project_usage (no second DB call)
    all_usage_overviews = get_all_facility_usage_overviews(
        db.session, list(grouped_data.keys()), active_at,
        _usage=per_project_usage,
    )
    resource_usage_overviews = {}
    for rn in grouped_data.keys():
        usage_overview_data = all_usage_overviews.get(rn, [])
        # Only pass facilities that have actual usage (pie requires positive values)
        chartable = [d for d in usage_overview_data if d.get('total_used', 0.0) > 0]
        resource_usage_overviews[rn] = {
            'table_data': usage_overview_data,
            'chart': generate_facility_pie_chart_matplotlib(
                chartable, layout=layout, theme=theme)
                     if chartable else '<div class="text-center text-muted small py-3">No usage data yet</div>',
        }

    # Pace charts render via HTMX (one loader per resource in the template
    # below — see `dashboards/allocations/partials/pace_chart.html` and
    # the `htmx_pace_chart` route). Deferring the SVG render here lets the
    # selector buttons live inside the swap target, and lets
    # `nav-view-persistence.js` replay the persisted `sort_by` on initial
    # load without us having to know it up front.

    return render_template(
        'dashboards/allocations/projects.html',
        grouped_data=grouped_data,
        resource_overviews=resource_overviews,
        resource_usage_overviews=resource_usage_overviews,
        allocation_type_charts=allocation_type_charts,
        allocation_type_usage_charts=allocation_type_usage_charts,
        type_annualized_rates=type_annualized_rates,
        active_at=active_at.strftime('%Y-%m-%d'),
        all_resources=all_resources,
        selected_resources=selected_resources,
        resource_types=resource_types,
        allowed_facility_names=allowed_facility_names,
        selected_facilities=effective_facilities,
    )


_VALID_PACE_SORT_BY = ('size', 'past', 'future')


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

    # Same active_at semantics as index(), but with no flash on bad
    # input — an HTMX swap into a chart pane is the wrong place for a
    # top-level alert. Silent fallback to today matches what callers
    # would see if they hit the route with no arg at all.
    active_at_str = request.args.get('active_at')
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if active_at_str:
        try:
            active_at = datetime.strptime(active_at_str, '%Y-%m-%d')
        except ValueError:
            active_at = today
    else:
        active_at = today

    # Facility-scope clamp — identical shape to index() so a WNA-scoped
    # user gets WNA-only rows on the chart even though the URL omits
    # ?facilities=. Unscoped users get None → no filter.
    allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    requested_facilities = request.args.getlist('facilities')
    selected_facilities = apply_facility_scope(
        requested_facilities, Permission.VIEW_PROJECTS,
        default=(sorted(allowed) if allowed is not None else None),
    )

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
    per_project_usage = filter_rows_by_facility(per_project_usage, selected_facilities)

    chart_svg = generate_pace_chart_matplotlib(
        per_project_usage, active_at, resource_name=resource_name,
        sort_by=sort_by, layout=read_layout(), theme=read_theme(),
    )

    # Sanitize resource_name into a stable HTML id — matches the
    # `data-resource="{{ resource_name|replace(' ', '_') }}"` convention
    # used in dashboard.html for the panel toggle. When the request
    # narrows to a single facility (the per-facility Pace card in the
    # allocation-type tab), include the facility in the id so its
    # persisted sort_by doesn't collide with the resource-wide chart.
    chart_dom_id = 'pace-chart-' + resource_name.replace(' ', '_')
    if len(requested_facilities) == 1:
        chart_dom_id += '-' + requested_facilities[0].replace(' ', '_')

    # Selector-button URLs MUST carry the original facility scope
    # forward — otherwise clicking Sort-by on a per-facility card would
    # drop ?facilities= and the next request would un-narrow back to
    # the whole resource (leaking cross-facility projects into a
    # facility-scoped chart). Pass the verbatim requested list so a
    # facility-scoped reload follows the same path as the initial
    # loader in dashboard.html.
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

    Returns:
        HTML table fragment of projects
    """
    resource = request.args.get('resource')
    facility = request.args.get('facility')
    allocation_type = request.args.get('allocation_type')
    active_at_str = request.args.get('active_at')
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'

    # Validate required params
    if not resource or not facility or not allocation_type:
        return '<p class="text-danger mb-0">Missing required parameters</p>'

    # Enforce facility scope: a scoped user must not be able to drill
    # into a facility outside their grant by hand-crafting the URL.
    # Unlike the index route (where we clamp to the allowed set), here
    # the user asked for *exactly one* facility — if it's out of scope
    # that's a forged request, not a narrowing choice.
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
    )


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
    # route) against their scope. ``None`` → unrestricted (unscoped
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


# ── Create Charge Adjustment ──────────────────────────────────────────────
#
# Staff-facing write path for the Adjustments tab. The user enters a
# positive amount; ChargeAdjustment.create() applies the sign by type
# (Credits/Refunds → negative, Debits/Reservations → positive). The set of
# exposed types lives in sam.accounting.adjustments._SIGN_BY_TYPE; the
# route resolves it to ChargeAdjustmentType rows via
# ChargeAdjustment.supported_types(session).


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


# Search-as-you-type backend for the Create Adjustment project picker.
# Mirrors admin_dashboard.htmx_project_search_for_parent but guarded by
# EDIT_ALLOCATIONS (the permission that also gates the Create Adjustment
# button); shares the results template so fk-picker.js populates the
# hidden project_id input on selection.
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


# ============================================================================
# XRAS action log — the operator surface for POST /api/xras/v1/actions
#
# Gating, and why it is two permissions:
#   VIEW_XRAS    the page, the table, the filters, the error lists. Swept into
#                ALL_VIEW by name, so every operator bundle already has it.
#   MANAGE_XRAS  the raw-payload panel and the replay button. The payload is the
#                request body verbatim and carries participant names, emails,
#                phones and grant-officer contacts.
#
# Plain require_permission(), NOT require_permission_any_facility(): an XRAS
# action is not facility-scopable. It arrives before we know its facility (a New
# action has no project yet) and a malformed body has none at all — there is
# nothing to intersect a scope against. See the note in rbac.py's
# USER_FACILITY_PERMISSIONS.
# ============================================================================

_XRAS_FRAGMENT_TARGET = 'alloc-xras-fragment'
_XRAS_FORM_ID = 'xras-filters'

#: The activity card's own filter form and swap target. Separate from the
#: action-log table's pair above: the two tables filter independently, and
#: sharing a form id would make one table's chips silently re-scope the other.
_XRAS_ACTIVITY_FORM_ID = 'xras-activity-filters'
_XRAS_ACTIVITY_TARGET = 'alloc-xras-pending'

#: Close the modal, then reload the tab behind it. Built by ``modal_triggers``
#: rather than written as a literal, which is what the four admin route modules
#: already do — the close half is the shared convention and only the reload event
#: is ours.
_XRAS_MODAL_TRIGGERS = modal_triggers('refreshXrasTab')


def _parse_xras_filters(request_args):
    """Parse filter + sort + pagination params for the XRAS fragment.

    Deliberately a sibling of ``_parse_audit_filters`` rather than a
    generalisation of it. The sort/page halves are identical by convention (that
    is what makes the shared ``sort_link`` / ``pagination`` macros work) and now
    come from the shared ``read_sort`` / ``read_page``; the filter halves have
    nothing in common — projcode/resource/username/facility versus
    status/action-type/request-number. Merging *those* would mean a parameter
    for every field either page has.

    Returns ``(filters, sort, page)`` with the same shapes ``_parse_audit_filters``
    returns, because the table fragment renders through the same macros.

    Default 30-day window is applied iff **neither** ``start_date`` nor
    ``end_date`` appears in the query string — explicitly empty bounds mean
    "all time", which is a different intent from "I have not chosen".
    """
    statuses = request_args.getlist('status') or None
    action_types = request_args.getlist('action_type') or None
    request_number = (request_args.get('request_number') or '').strip() or None

    start_date_str = (request_args.get('start_date') or '').strip()
    end_date_str = (request_args.get('end_date') or '').strip()

    if 'start_date' not in request_args and 'end_date' not in request_args:
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                      - timedelta(days=30))
        # Deliberately UNBOUNDED above, where the sibling audit pages use
        # datetime.now(). There are no future rows, so an upper bound buys
        # nothing — and a sub-second one actively loses the newest row.
        # `received_time` is a MySQL DATETIME with second resolution and MySQL
        # ROUNDS rather than truncates, so a row written at 10:10:24.894 is
        # stored as 10:10:25 and lands *after* an end_date captured microseconds
        # earlier in the same request. On an audit surface whose whole job is
        # answering "did my action get recorded?", the row most worth seeing is
        # the one that just arrived. (_parse_audit_filters above still has the
        # sub-second bound; same latent bug, left alone as pre-existing.)
        end_date = None
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
        'status': statuses,
        'action_type': action_types,
        'request_number': request_number,
        'start_date': start_date,
        'end_date': end_date,
    }

    return (filters,
            read_sort(request_args, XRAS_ACTION_SORT_COLUMNS),
            read_page(request_args))


def _xras_action_types():
    """Filter vocabulary: the known types plus anything actually in the table.

    ``XrasActionSchema`` applies no enum to ``actionType`` on purpose — Transfer,
    Renewal and Advance still have zero samples and no co-PI role has ever been
    sampled — so a type we have never seen must still be filterable rather than
    invisible. Union, don't replace.

    Observed values are folded onto their canonical spelling first, so an alias pair
    offers **one** entry: ``Adjust`` and ``Adjustment`` are the same action and
    filtering on either returns both (``XRAS_ACTION_TYPE_ALIASES``). Two chips that
    filter identically would read as two distinct action types.
    """
    return sorted(set(XRAS_ACTION_TYPES) | set(get_observed_action_types(db.session)))


@bp.route('/xras')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras():
    """XRAS action-log page: the operator surface for the ingest endpoint."""
    end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=30)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    return render_template(
        'dashboards/allocations/xras.html',
        xras_start_date=start_str,
        xras_end_date=end_str,
        # The worklist tabs share ONE window control, rendered in the shell so
        # only one start_date/end_date pair exists (see the template).
        window=_parse_activity_window(request.args),
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        form_id='xras-window-filters',
        **_window_control_context(end_date, start_str, end_str),
        all_statuses=list(XRAS_ACTION_STATUSES),
        all_action_types=_xras_action_types(),
        # Site-specific, so it lives with the token family rather than in the
        # template — see XRAS_REQUEST_TOKEN_PREFIXES.
        request_example=XRAS_REQUEST_TOKEN_EXAMPLE,
    )


@bp.route('/xras_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_fragment():
    """HTMX fragment: sortable, paginated table of XRAS actions."""
    filters, sort, page = _parse_xras_filters(request.args)
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_xras_actions(
        db.session,
        **filters,
        sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
        offset=offset, limit=page['per_page'],
    )
    total = count_recent_xras_actions(db.session, **filters)
    # Facet counts, computed with SELF-EXCLUSION: each dimension's rollup omits
    # its OWN filter while honouring every other one.
    #
    # This is what makes the chips switchers rather than dead ends. Scoping a
    # dimension by itself drives every unselected value to zero the moment one is
    # picked — click "failed" and the other four statuses all read 0, so there is
    # no way to move to another status without first clearing the filter. The
    # jobs explorer's facet strip learned the same lesson (service.jobs_facets
    # passes self_exclude).
    #
    # Two GROUP BY queries instead of one; both are served by the
    # (status, action_type) triage index.
    _facet_common = dict(
        request_number=filters['request_number'],
        start_date=filters['start_date'], end_date=filters['end_date'],
    )
    status_facet = summarize_xras_actions(
        db.session, action_type=filters['action_type'], **_facet_common)
    type_facet = summarize_xras_actions(
        db.session, status=filters['status'], **_facet_common)

    # Every status renders, including at zero — an absent bucket would read as
    # "not measured" rather than "none". `summarize_xras_actions` already seeds
    # the five, so iterating its dict gives that for free, in vocabulary order.
    #
    # ⚠️ Iterated, not re-derived from XRAS_ACTION_STATUSES. That spelling dropped
    # any status outside the vocabulary — which the query layer goes out of its way
    # to keep, because it is a bug worth surfacing — while the headline total above
    # still counted it, so the strip disagreed with its own total.
    #
    # A stray appends rather than reshuffling: the five are a stable strip an
    # operator scans by position.
    #
    # Its chip filters even though `all_statuses` (line ~1295) still offers only the
    # five: `set-filter-submit` synthesizes a missing <option> before setting the
    # value (static/js/actions.js:152-160). The offer list is deliberately NOT
    # widened the way `_xras_action_types` widens its own — an unsampled action type
    # is normal traffic, a stray status is only ever a bad write, and presenting one
    # as a standing filter choice would dress a bug up as a category.
    status_facets = [{'value': s, 'count': n}
                     for s, n in status_facet['by_status'].items()]

    # A NULL action_type is a real count — a body that would not parse has none —
    # but it is not a filterable value: there is no way to express "IS NULL"
    # through the form's multi-select. Dropped rather than rendered as a chip
    # that cannot work, the same rule the jobs facet strip applies.
    action_type_facets = sorted(
        ({'value': t, 'count': n}
         for t, n in type_facet['by_action_type'].items() if t),
        key=lambda r: (-r['count'], r['value']),
    )

    return render_template(
        'dashboards/allocations/partials/xras_table.html',
        rows=rows, total=total,
        status_facets=status_facets, action_type_facets=action_type_facets,
        page=page, sort=sort, filters=filters,
        fragment_url=url_for('allocations_dashboard.xras_fragment'),
        target_id=_XRAS_FRAGMENT_TARGET,
        form_id=_XRAS_FORM_ID,
        sortable_columns=sorted(XRAS_ACTION_SORT_COLUMNS),
    )


#: Window pills for the activity card, and the default. `days` is free on this
#: blueprint — it means lookback days in the jobs family and legacy days→hours
#: on the status-history routes, and neither is reachable from here.
_ACTIVITY_WINDOW_PILLS = ((7, '7D'), (30, '30D'), (90, '90D'))
_ACTIVITY_DEFAULT_DAYS = 30
_ACTIVITY_MAX_DAYS = 365

#: Chip text for each tag. The tag itself is a slug that round-trips through
#: the form; an operator should never see it, so the two are kept apart rather
#: than the vocabulary being renamed to read nicely in both places.
_ACTIVITY_TAG_LABELS = {
    'needs_activation': 'Activation',
    'not_notified': 'Not notified',
    'notified': 'Notified',
    'failed': 'Delivery failed',
    'dismissed': 'Dismissed',
}


def _parse_activity_window(args) -> dict:
    """``days`` pill, or an explicit custom range. Never a 400.

    An explicit ``start_date``/``end_date`` **outranks** ``days`` — the Custom
    pill sets the dates and leaves ``days`` behind in the form, so reading
    ``days`` first would silently ignore the range the operator just typed.

    Returns the parsed bounds *and* the raw strings, because the same dict has
    to re-render the form controls.
    """
    start_raw = (args.get('start_date') or '').strip()
    end_raw = (args.get('end_date') or '').strip()

    def _date(raw, end_of_day=False):
        try:
            parsed = datetime.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return None
        return (parsed.replace(hour=23, minute=59, second=59)
                if end_of_day else parsed)

    since = _date(start_raw) if start_raw else None
    until = _date(end_raw, end_of_day=True) if end_raw else None
    if since is not None or until is not None:
        return {'days': None, 'since': since, 'until': until,
                'start_date': start_raw, 'end_date': end_raw, 'custom': True}

    days = args.get('days', type=int) or _ACTIVITY_DEFAULT_DAYS
    days = max(1, min(days, _ACTIVITY_MAX_DAYS))
    return {'days': days, 'since': datetime.now() - timedelta(days=days),
            'until': None, 'start_date': '', 'end_date': '', 'custom': False}


def _row_activity_type(row) -> str:
    """The chip value for the action-type dimension.

    ``action_type`` rather than ``service``, because it is the word the wire
    used and the one the action-log table below already shows. The two differ
    on exactly one case — a ``New`` against an existing project routes to the
    ``update`` service — and an operator scanning for "the New that came in"
    should find it under New.
    """
    return row.get('action_type') or '—'


def _filter_activity(rows, *, tags=None, types=None):
    """Apply the chip selections. Tags are ANDed with types, ORed within."""
    if tags:
        wanted = set(tags)
        rows = [r for r in rows if wanted & set(r['tags'])]
    if types:
        wanted_types = set(types)
        rows = [r for r in rows if _row_activity_type(r) in wanted_types]
    return rows


def _activity_facets(rows, dimension, *, tags=None, types=None) -> dict:
    """Counts for one chip dimension, **excluding that dimension's own filter**.

    Computed in Python rather than SQL because the rows are already assembled
    here — the notification rollup that produces the tags has no SQL form. The
    set is one window of processed actions, so this is a pass over a list, not
    a scan.
    """
    if dimension == 'tag':
        scoped = _filter_activity(rows, types=types)
        counts = {tag: 0 for tag in ACTIVITY_TAGS}
        for row in scoped:
            for tag in row['tags']:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    if dimension == 'activity_type':
        scoped = _filter_activity(rows, tags=tags)
        counts: dict = {}
        for row in scoped:
            key = _row_activity_type(row)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    raise ValueError(f'unknown activity facet dimension {dimension!r}')


@bp.route('/xras_pending_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_pending_fragment():
    """HTMX fragment: recent XRAS outcomes — what was communicated, what needs a human.

    One row per successfully processed action. See :func:`get_xras_activity` for
    why the key is the action rather than the project, and for what this can and
    cannot see.

    The endpoint keeps its old name. It is an internal URL, ~30 tests pin it, and
    renaming it would buy nothing over changing what it renders.

    Two gates, both enforced HERE rather than only in the template:

    - ``recipients`` (project lead/admin contact details) and the per-recipient
      delivery detail are assembled only for ``MANAGE_XRAS``, so a ``VIEW_XRAS``
      response never carries the addresses at all and a view-source cannot leak
      what the page chose not to draw. Same rule as the raw-payload panel.
    - ``may_activate`` is resolved **per project** through
      ``can_edit_project_governance``, not once for the card. The helper is flat
      over the user today, so this costs one extra query and buys nothing
      immediately — but the moment it becomes project- or facility-aware the card
      follows for free, whereas a card-level flag would quietly start lying. The
      POST route calls the same helper itself, so the authority stays in one
      place and this is only ever a rendering hint.
    """
    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    window = _parse_activity_window(request.args)
    selected_tags = [t for t in request.args.getlist('tag') if t]
    selected_types = [t for t in request.args.getlist('activity_type') if t]

    rows = get_xras_activity(db.session,
                             since=window['since'], until=window['until'])

    # Facets are computed over the *unfiltered* window set, each dimension
    # dropping its own selection — the same self-exclusion `facet_notifications`
    # and `xras_fragment` keep. Scope a dimension by itself and every unselected
    # value falls to zero the moment one is picked, and the chips stop being
    # switchers.
    tag_facets = _activity_facets(rows, 'tag', types=selected_types)
    type_facets = _activity_facets(rows, 'activity_type', tags=selected_tags)

    rows = _filter_activity(rows, tags=selected_tags, types=selected_types)

    recipients = {}
    may_activate = {}
    if may_manage:
        project_ids = sorted({r['project_id'] for r in rows})
        recipients = get_xras_pending_recipients(db.session, project_ids)
        may_activate = {
            p.project_id: can_edit_project_governance(current_user, p)
            for p in get_projects_by_ids(db.session, project_ids)
        }

    return render_template(
        'dashboards/allocations/partials/xras_activity_card.html',
        rows=rows,
        recipients=recipients,
        may_activate=may_activate,
        may_manage=may_manage,
        window=window,
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        # Every declared tag renders, including at zero: an absent chip reads
        # as "not measured", which is a different claim from "none".
        tag_values=[{'value': tag,
                     'label': _ACTIVITY_TAG_LABELS.get(tag, tag),
                     'count': tag_facets.get(tag, 0)}
                    for tag in ACTIVITY_TAGS],
        type_values=[{'value': k, 'count': v} for k, v in type_facets.items()],
        selected_tags=selected_tags,
        selected_types=selected_types,
        form_id=_XRAS_ACTIVITY_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_pending_fragment'),
        target_id=_XRAS_ACTIVITY_TARGET,
    )


# ---------------------------------------------------------------------------
# Account-creation worklist — read-only.
#
# Who must exist in SAM before an XRAS handoff can succeed. Unreconciled ARC
# placeholder identities are 55% of production XRAS failures, and account
# creation is manual, so this card is the operator's queue for the largest
# single cause of failure.
#
# Read-only in this PR by design. Operator notes and dismissal need storage
# that `XrasActivationEvent` cannot provide — its `project_id` is NOT NULL and
# project-scoped, while this worklist is username-keyed and for a New request
# the project does not exist yet. That table (`xras_account_event`) is the
# immediate follow-up; shipping the buttons before it would be dead UI.
# ---------------------------------------------------------------------------

#: Display labels for the classification facet. The slug is what round-trips
#: through the form; an operator should never see it.
#: ⚠️ These name the ARTIFACT, not an action SAM performs — and that is the
#: whole point of the wording. `users` is mirrored into SAM from the enterprise
#: directory by a process outside this codebase: there is no INSERT into
#: `users` anywhere in the tree, `User` alone among the models has no
#: `create()`, and nothing here ever writes `active` or `locked`. So both
#: remedies are somebody else's work, and the earlier labels — "Create
#: account" / "Reactivate account" — read as instructions to a SAM operator who
#: has no way to carry them out.
#:
#: Naming the artifact rather than the team is deliberate too: the owning group
#: can change without touching 26 rows and the CLI's JSON envelope, and the
#: banner names it once where it can be kept current.
_ACCOUNT_CLASSIFICATION_LABELS = {
    CLASSIFICATION_ABSENT: 'New account',
    CLASSIFICATION_INACTIVE: 'Reactivation',
}

_ACCOUNTS_FORM_ID = 'xras-accounts-filters'
_ACCOUNTS_TARGET = 'alloc-xras-accounts'

#: Bounds a cold-cache render. Each miss is one round trip to XRAS inside an
#: htmx request, so this is a latency bound, not a correctness one — the rows
#: past it still render, just without person detail.
_ACCOUNTS_ENRICH_BUDGET = 25

#: The ``placeholder`` facet's two values. Strings rather than a bool because a
#: form round-trips strings, and ``'false'`` is truthy on the way back in.
ORIGIN_PLACEHOLDER = 'placeholder'
ORIGIN_KNOWN = 'known'

_ORIGIN_LABELS = {
    ORIGIN_PLACEHOLDER: 'ARC placeholder',
    ORIGIN_KNOWN: 'Known identity',
}


def _filter_accounts(rows, *, classifications=None, roles=None, origins=None):
    """Facet filters: ANDed across dimensions, ORed within one.

    *origins* is the ``placeholder`` dimension, expressed as the two values a
    form can round-trip. It earns a facet because it separates the two
    populations that share this card: an ARC placeholder is a researcher who
    has never had a site account, while a non-placeholder is a real identity
    whose account lapsed. Those are different pieces of work for different
    people, and until now the only way to tell them apart was to read the shape
    of the username.

    ⚠️ Deliberately **not** defaulted. The rule on this card is *no selection =
    no filter*, and defaulting one dimension on would make an empty facet row
    mean something different here than on every other card.
    """
    out = rows
    if classifications:
        out = [r for r in out if r['classification'] in classifications]
    if roles:
        out = [r for r in out if any(role in roles for role in r['roles'])]
    if origins:
        wanted = {o == ORIGIN_PLACEHOLDER for o in origins}
        out = [r for r in out if bool(r['placeholder']) in wanted]
    return out


_PENDING_FORM_ID = 'xras-pending-filters'

#: Requests to offer as chips. A worklist spanning dozens of projects would
#: otherwise render a chip wall; the cap is on the CHIPS, not the rows, and
#: the rows all stay visible whether or not their request earned one.
_MAX_REQUEST_CHIPS = 12


def _submitted_since(row, since):
    """Did any request naming this person appear in XRAS within the window?

    The Feed-B analogue of Feed A's ``received_time`` filter — the same
    question ("when did this show up?") asked of a feed that has no arrival of
    its own, which is what lets one control span both tabs.

    A row with no usable submit date is kept: that is missing information, not
    evidence of age, and dropping it would silently shrink the queue.
    """
    if since is None:
        return True
    start = since.date() if hasattr(since, 'date') else since
    dates = [a.get('submit_date') for a in row.get('actions') or []]
    if not any(dates):
        return True
    for raw in dates:
        if not raw:
            return True
        try:
            if date.fromisoformat(str(raw)[:10]) >= start:
                return True
        except ValueError:
            return True
    return False


def _request_facets(rows, *, classifications=None):
    """Counts per XRAS request number, most-affected first.

    Self-excluding on its own dimension, like every other facet here. Rows
    naming no request number contribute nothing: a NULL cannot round-trip
    through the form, so it must not become a chip.
    """
    scoped = _filter_accounts(rows, classifications=classifications)
    counts = {}
    for row in scoped:
        for number in {a['request_number'] for a in row['actions'] if a['request_number']}:
            counts[number] = counts.get(number, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{'value': k, 'count': v} for k, v in ordered[:_MAX_REQUEST_CHIPS]]


def _account_facets(rows, dimension, *, classifications=None, roles=None):
    """Self-excluding counts for one dimension.

    A dimension's rollup omits its own filter — scope it by itself and every
    unselected value reads 0 the moment one is picked, which turns the chips
    from switchers into a dead end. Same rule as :func:`_activity_facets`.
    """
    if dimension == 'classification':
        scoped = _filter_accounts(rows, roles=roles)
        return {key: sum(1 for r in scoped if r['classification'] == key)
                for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)}

    if dimension == 'role':
        scoped = _filter_accounts(rows, classifications=classifications)
        counts = {}
        for row in scoped:
            for role in row['roles']:
                counts[role] = counts.get(role, 0) + 1
        return dict(sorted(counts.items()))

    if dimension == 'origin':
        scoped = _filter_accounts(rows, classifications=classifications,
                                  roles=roles)
        return {
            ORIGIN_PLACEHOLDER: sum(1 for r in scoped if r['placeholder']),
            ORIGIN_KNOWN: sum(1 for r in scoped if not r['placeholder']),
        }

    raise ValueError(f'unknown account facet dimension {dimension!r}')


def _pending_account_total():
    """How many accounts the *other* tab is holding, or ``None``.

    ⚠️ Counts only. Reading the sibling feed's rows into this card would undo
    the split the two tabs exist to draw — one is what has posted, the other is
    a lookahead at what XRAS may send. But a card that reports "8" while 18
    more sit one click away is a queue that reads as smaller than it is, and
    that is the failure this whole change is about.

    ``None`` means "could not look", which is the honest answer when the
    outbound API is off or no sweep has published — distinct from zero.
    """
    from sam.integration.xras_api import xras_api_configured

    if not xras_api_configured():
        return None
    try:
        from sam.integration.xras_api.cache import load_pending_worklist

        snapshot = load_pending_worklist()
    except Exception:                                # noqa: BLE001
        # Cache backends are infrastructure. A cross-reference is a courtesy;
        # it must never be the reason the worklist 500s.
        current_app.logger.warning(
            'xras accounts: could not read the pending worklist for the '
            'cross-reference', exc_info=True)
        return None
    if not snapshot:
        return None
    return (snapshot.get('counts') or {}).get('total')


@bp.route('/xras_accounts_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_accounts_fragment():
    """HTMX fragment: accounts that must be created or reactivated for XRAS.

    Two states here are **designed, not broken**, and both are what a reviewer
    will see first:

    - **Unconfigured.** With `XRAS_OUTGOING_ENABLED` off — the shipped state,
      and what staging shows — the worklist still renders in full from the
      inbound action log. Only the person detail and the `isReconciled`
      closure signal are unavailable, and a muted note says so.
    - **Empty.** Production has zero rows until ACCESS is repointed at SAM and
      `xras_action_log` starts filling. An empty card is a true report.

    PII is gated **here**, not in the template. Person detail — name, email,
    organization, academic status, residence country — is assembled only for a
    viewer holding MANAGE_XRAS, so a VIEW_XRAS response never carries it and a
    view-source cannot leak what the page chose not to draw. Same rule as the
    raw-payload panel and the notification recipients above.

    `is_reconciled` is deliberately on the VIEW_XRAS side of that line: it is a
    boolean about account state, not a personal detail, and it is the signal
    that tells an operator an item is about to close itself.
    """
    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    window = _parse_activity_window(request.args)
    selected_classes = [c for c in request.args.getlist('classification') if c]
    selected_roles = [r for r in request.args.getlist('role') if r]
    selected_origins = [o for o in request.args.getlist('origin') if o]

    rows = get_account_worklist(db.session,
                                since=window['since'], until=window['until'])

    # ⚠️ Feed A ONLY, on purpose — this tab is precisely the accounts blocking
    # actions that have already POSTED, which is a claim we can always make
    # from our own audit table. The lookahead at what XRAS has approved but not
    # yet sent is the sibling tab, and it is contingent on the outbound API
    # being configured. Merging them would trade a guarantee for a maybe. The
    # union is available where it is actually needed — `sam-admin xras
    # --accounts`, and whatever digest comes after it.
    stamp_waiting_days(rows)

    # Enrichment is best-effort and never fatal: an outage or an unconfigured
    # deployment leaves `person` None and flags the batch, so the card degrades
    # to counts and usernames rather than returning 500.
    enrichment = enrich_worklist(rows, max_lookups=_ACCOUNTS_ENRICH_BUDGET)

    if not may_manage:
        # Drop the PII before it can reach a template, a log line, or a
        # response body. `is_reconciled` survives — see the docstring.
        for row in rows:
            row['person'] = None

    selected_requests = [r for r in request.args.getlist('request_number') if r]

    class_facets = _account_facets(rows, 'classification', roles=selected_roles)
    role_facets = _account_facets(rows, 'role', classifications=selected_classes)
    origin_facets = _account_facets(rows, 'origin',
                                    classifications=selected_classes,
                                    roles=selected_roles)
    request_facets = _request_facets(rows, classifications=selected_classes)

    rows = _filter_accounts(rows, classifications=selected_classes,
                            roles=selected_roles, origins=selected_origins)
    if selected_requests:
        # The operator working one project's activation wants only its rows.
        rows = [r for r in rows
                if {a['request_number'] for a in r['actions']} & set(selected_requests)]

    return render_template(
        'dashboards/allocations/partials/xras_accounts_card.html',
        rows=rows,
        counts=worklist_counts(rows),
        may_manage=may_manage,
        enrichment=enrichment,
        window=window,
        window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        # Both classifications render even at zero: an absent chip reads as
        # "not measured", a different claim from "none".
        classification_values=[
            {'value': key,
             'label': _ACCOUNT_CLASSIFICATION_LABELS.get(key, key),
             'count': class_facets.get(key, 0)}
            for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)],
        role_values=[{'value': k, 'count': v} for k, v in role_facets.items()],
        origin_values=[{'value': k, 'label': _ORIGIN_LABELS[k],
                        'count': origin_facets.get(k, 0)}
                       for k in (ORIGIN_PLACEHOLDER, ORIGIN_KNOWN)],
        request_values=request_facets,
        # What the sibling tab holds, so neither reads as the whole queue.
        # Counts only — never its rows, which would defeat the tab split.
        pending_total=_pending_account_total(),
        selected_classifications=selected_classes,
        selected_roles=selected_roles,
        selected_origins=selected_origins,
        selected_requests=selected_requests,
        form_id=_ACCOUNTS_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_accounts_fragment'),
        target_id=_ACCOUNTS_TARGET,
    )


_PENDING_TARGET = 'alloc-xras-pending-requests'
_WINDOW_TARGET = 'alloc-xras-window'


@bp.route('/xras_window_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_window_fragment():
    """HTMX fragment: just the shared window pills.

    ⚠️ **The control has to re-render itself, and this is why the route
    exists.** `window_pills` marks the active pill server-side from the
    window it is handed. While the pills lived inside each worklist fragment
    that came free — a submit re-rendered the fragment and the pill state
    came with it. Sharing one control across three tabs moved it into the page
    shell, outside every swap target, so it kept whatever state the page load
    gave it: clicking 7D re-filtered the data and left the pill looking
    unchanged, which reads as a dead control.

    So the pills are their own swap target, listening for the same submit the
    panes do.
    """
    window = _parse_activity_window(request.args)
    return render_template(
        'dashboards/allocations/partials/xras_window_control.html',
        window=window, window_pill_choices=_ACTIVITY_WINDOW_PILLS,
        form_id='xras-window-filters')


@bp.route('/xras_pending_requests_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_pending_requests_fragment():
    """HTMX fragment: Feed B — XRAS requests SAM has no project for yet.

    **Read from a cache the `xras_sweep` task publishes, never computed
    here.** The enumeration behind this is 21 pages and 60-90 seconds against
    `api.xras.org`; no htmx round-trip can afford it, which is why the sweep
    is a producer and this is a consumer. The tab is therefore exactly as
    fresh as the last successful sweep, and it says so.

    Three distinct empty states, and conflating them would mislead:

    - **unconfigured** — `XRAS_OUTGOING_ENABLED` is off, so no sweep can run.
    - **no snapshot** — configured, but no sweep has published yet (the task
      may be disabled, or this is the first hour after a deploy).
    - **published and empty** — a real sweep found nothing pending, which is
      the healthy steady state.

    Same PII rule as the accounts tab: person detail only for MANAGE_XRAS,
    stripped in the route so a VIEW_XRAS response never carries it.
    """
    from sam.integration.xras_api import xras_api_configured
    from sam.integration.xras_api.cache import load_pending_worklist

    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    configured = xras_api_configured()
    snapshot = load_pending_worklist() if configured else None

    rows = list(snapshot.get('rows') or []) if snapshot else []
    # The snapshot is written by the task and read here, so the two can be on
    # different code — `stamp_waiting_days` backfills rather than assuming the
    # publisher already derived it. Stamped on read, never cached: an age is
    # the one field whose value depends on when you asked.
    stamp_waiting_days(rows)
    window = _parse_activity_window(request.args)
    selected_requests = [r for r in request.args.getlist('request_number') if r]
    selected_classes = [c for c in request.args.getlist('classification') if c]

    # ⚠️ A shared control that silently does nothing on one tab is worse than
    # no control, so the pills mean the same thing here as on the other two:
    # "what showed up in the last N days". For Feed B that is `submitDate`.
    #
    # Filtering on the period of performance was tried first and is wrong: a
    # pending request's allocation almost always ends a year out, so a
    # one-sided window keeps every row at every width and the pill looks dead
    # — the exact complaint this is fixing. The period of performance stays
    # where it belongs, bounding what the SWEEP collects; the header reports
    # that width so the two are never confused.
    rows = [r for r in rows if _submitted_since(r, window['since'])]

    if not may_manage:
        rows = [{**r, 'person': None} for r in rows]

    class_facets = _account_facets(rows, 'classification')
    request_facets = _request_facets(rows, classifications=selected_classes)

    rows = _filter_accounts(rows, classifications=selected_classes)
    if selected_requests:
        rows = [r for r in rows
                if {a['request_number'] for a in r['actions']} & set(selected_requests)]

    return render_template(
        'dashboards/allocations/partials/xras_pending_requests_card.html',
        rows=rows,
        snapshot=snapshot,
        configured=configured,
        may_manage=may_manage,
        counts=worklist_counts(rows),
        classification_values=[
            {'value': key,
             'label': _ACCOUNT_CLASSIFICATION_LABELS.get(key, key),
             'count': class_facets.get(key, 0)}
            for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)],
        request_values=request_facets,
        selected_classifications=selected_classes,
        selected_requests=selected_requests,
        form_id=_PENDING_FORM_ID,
        fragment_url=url_for(
            'allocations_dashboard.xras_pending_requests_fragment'),
        target_id=_PENDING_TARGET,
    )


# ---------------------------------------------------------------------------
# Pending-activation worklist — the operator write path.
#
# These are dashboard routes, NOT an API: session-cookie auth, CSRF via the
# hx-headers on <body>, and the card's own buttons are the only callers. No
# /api/v1/ or /api/xras/v1/ surface is added; webapp/api/xras/ stays the
# legacy-compat inbound blueprint it is.
#
# ⚠️ Every one of these writes runs INSIDE management_transaction, which is the
# OPPOSITE of what webapp/api/xras/recheck.py does one screen away — see the
# docstrings below for why, because the difference is deliberate and a reader
# who has just read recheck.py will expect the other answer.
# ---------------------------------------------------------------------------


def _load_pending_project(project_id):
    """Fetch the project an activation event is about, or None."""
    return db.session.get(Project, project_id)


def _record_activation_event(project, event_type, *, comment=None,
                             notified_to=None, action_log_id=None):
    """Append one operator event, with the prompting action as provenance.

    Runs inside ``management_transaction`` — deliberately unlike
    :func:`webapp.api.xras.recheck.recheck_action`, which commits its audit row on a
    private connection precisely so it survives a handler rollback. Its value is
    "we received this even though processing it blew up".

    An activation event is the inverse: it records an operator's *decision*, and
    if the decision does not apply the record must not survive. Because the card's
    state is **derived** from these events, an ``activated`` row that outlived its
    own effect would make the card go on showing the project as pending while the
    audit says it was activated — exactly the drift the append-only design exists
    to eliminate. Two connections mean two truths; the design's premise is one.
    """
    return XrasActivationEvent.create(
        db.session,
        project_id=project.project_id,
        event_type=event_type,
        created_by=current_user.username,
        comment=comment,
        notified_to=notified_to,
        # `action_log_id` names the action the operator acted on. It defaults
        # to the newest, which is right for Activate/Dismiss/Restore — those
        # are about the project's current situation. Notify passes one
        # explicitly, because working through a backlog means reporting an
        # older outcome and the timeline has to say which.
        xras_action_log_id=(action_log_id if action_log_id is not None
                            else get_latest_xras_action_id(
                                db.session, project.project_id)),
    )


def _xras_messages(project, people, *, action=None):
    """The route's binding of :func:`~sam.queries.xras_notices.build_xras_messages`.

    Two lines, and they are the two the scheduled task cannot supply: Flask's
    request-scoped session, and the operator who clicked. Everything else —
    the payload, the subject, and above all the dedup key — lives in
    ``sam.queries.xras_notices`` so that the button and ``xras_notices`` can
    never disagree about what has already been sent.
    """
    return build_xras_messages(db.session, project, people, action=action,
                               requested_by=current_user.username)


@bp.route('/xras_notify_form/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_notify_form(project_id: int):
    """Modal body: **what these people will actually receive**, plus Send.

    A real send is irreversible, so the one-click POST became two steps — the
    same reasoning that already puts an ``hx-confirm`` on ``xras_activate``.
    A preview beats a confirm dialog because it also answers "and what does
    it say", which is the question an operator actually has.

    ``preview()`` writes **no** ledger row: a preview is not an attempt, and a
    stray row would poison the dedup query for the send that follows.

    The ledger is attached here even though a preview does not need one: it
    answers *"would this send be suppressed as a duplicate"* **before** the
    operator clicks, so the modal can offer the override up front rather than
    reporting "nothing was sent" afterwards and leaving SQL as the only
    recovery. Asking is cheap — one indexed lookup per recipient — and it is
    the same predicate ``send_many`` will apply.

    ``?action_id=`` names *which* outcome to report, which is what lets a
    Supplement be notified separately from the New before it. It is a query
    param rather than a second path segment deliberately: absent means "the
    newest action", which is exactly the old behaviour, so no URL changed and
    no route-map entry moved.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')

    action = load_xras_action(db.session,
                              request.args.get('action_id', type=int))
    people = get_xras_pending_recipients(db.session, [project_id]).get(project_id, [])
    messages = _xras_messages(project, people, action=action)

    notifier = get_notifier()
    preview = None
    preview_error = None
    if messages:
        try:
            preview = notifier.preview(messages[0])
        except Exception as exc:            # a template problem, not a send
            current_app.logger.warning(
                'XRAS notify preview failed for %s: %s', project.projcode, exc)
            preview_error = str(exc)

    # A notifier without a ledger cannot answer "was this already sent", and
    # that is a legitimate configuration — `get_notifier(ledger=False)` exists
    # for a pure preview. No ledger means no duplicate to override, so the
    # force toggle simply does not appear.
    already_notified = [
        m.recipient for m in messages
        if m.dedup_key and notifier.ledger is not None
        and notifier.ledger.already_sent(m.dedup_key)
    ]

    return render_template(
        'dashboards/allocations/partials/xras_notify_form.html',
        project=project,
        people=people,
        preview=preview,
        preview_error=preview_error,
        already_notified=already_notified,
        notify_enabled=notifier.config.enabled,
        redirect_to=notifier.config.redirect_to or None,
        # Every one of these notices tells a PI their allocation is usable —
        # the activation one says "is now active" in as many words. Nothing
        # orders Notify after Activate, and in the pre-deploy smoke a notice
        # went out 64 seconds before the project was activated. The operator
        # keeps the choice; it just stops being invisible.
        project_inactive=not project.is_active,
        # The action travels to the POST so the send reports the same outcome
        # the operator just previewed — not whatever is newest by then.
        post_url=url_for('allocations_dashboard.xras_notify',
                         project_id=project_id,
                         **({'action_id': action.xras_action_log_id}
                            if action is not None else {})),
    )


@bp.route('/xras_notify/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_notify(project_id: int):
    """Send the handoff mail, then record what actually happened.

    **Send first, record second.** The activation event's ``notified_to``
    names the addresses that *succeeded*, so the card never claims a handoff
    that did not leave the building. The recipients are recomputed here,
    server-side, and never taken from the request: "the current lead" and
    "who we notified" are different questions, and only the second is an
    audit answer.

    No path may 500. ``Notifier.send_many`` never raises for a delivery
    failure, and the three outcomes are:

    * **all delivered** — success fragment naming who was mailed;
    * **partial** — success fragment naming the failures; the event records
      only the successes;
    * **nothing delivered** (relay down, or ``NOTIFY_ENABLED`` off) — the
      manual-fallback dialog, which hands the operator the addresses and says
      plainly that nothing was sent. **No activation event is written**,
      because none happened.

    ``suppressed`` counts as "nothing delivered" here on purpose: if everyone
    was already told about this same XRAS action, there is no new handoff to
    record, and writing another ``notified`` event would be the double-count
    the derive rule exists to prevent.

    **The force override.** Suppression is right by default and wrong in the
    cases that actually reach an operator: a bad address since corrected, a
    template fixed after the fact, a recipient who deleted the mail. Without
    an override the only recovery is a ``DELETE`` against ``notification_log``,
    which is not a thing to ask of someone at 3am. ``force`` is offered by the
    modal **only when a duplicate would actually be suppressed**, and it
    bypasses the dedup check alone — ``NOTIFY_ENABLED`` still fails closed, so
    this cannot be used to mail from a deployment that is meant to be silent.
    A forced send is stamped on the activation event, because "we told them
    twice" is exactly the kind of thing the timeline exists to explain.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    action = load_xras_action(db.session,
                              request.args.get('action_id', type=int))
    people = get_xras_pending_recipients(db.session, [project_id]).get(project_id, [])
    messages = _xras_messages(project, people, action=action)

    # Unchecked checkboxes are omitted from the request entirely, so presence
    # is the signal — never a value comparison. See CLAUDE.md § 10.
    force = 'force' in request.form

    results = get_notifier().send_many(messages, force=force) if messages else []
    summary = notify_summary(results)

    if not summary['ok']:
        current_app.logger.info(
            'XRAS notify sent nothing: project=%s by=%s statuses=%s',
            project.projcode, current_user.username,
            sorted({r.status for r in results}) or ['no recipients'])
        return htmx_success(
            'dashboards/allocations/partials/xras_notify_manual_fallback.html',
            {'refreshXrasTab': {}},
            project=project, people=people, summary=summary)

    notified_to = '; '.join(
        f"{r.message.recipient.name or r.message.recipient.address} "
        f"<{r.message.recipient.address}>" for r in summary['delivered']) or None

    with management_transaction(db.session):
        event = _record_activation_event(
            project, 'notified', notified_to=notified_to,
            # Stamp the action actually reported, not whatever is newest by
            # now — an operator working through a backlog notifies about an
            # older outcome, and the timeline must say which one.
            action_log_id=(action.xras_action_log_id
                           if action is not None else None),
            comment=('Re-sent with the duplicate check overridden.'
                     if force else None))

    current_app.logger.info(
        'XRAS notify sent: project=%s by=%s to=%s failed=%d forced=%s',
        project.projcode, current_user.username, notified_to,
        len(summary['failed']), force)

    return htmx_success(
        'dashboards/allocations/partials/xras_notify_sent.html',
        {'refreshXrasTab': {}},
        project=project, summary=summary, recorded_at=event.creation_time)


@bp.route('/xras_activate/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_activate(project_id: int):
    """Activate a pending XRAS project in one click.

    ⚠️ **Double-gated.** ``project.active`` is a GOVERNANCE_FIELD, and
    ``MANAGE_XRAS`` alone must not be enough to flip it.
    ``can_edit_project_governance`` is the single definition of who may — flat
    ``EDIT_PROJECTS`` with **no** steward override, so a project lead cannot.

    Deliberately not a §8 decorator: ``require_project_permission(EDIT_PROJECTS)``
    resolves a *projcode* and means "X **OR** project lead/admin", which is
    strictly too permissive here. Swapping this URL to a projcode to reach that
    decorator would introduce the very bug the gate exists to prevent.
    ``_ProjectUpdateHandler.form_input()`` calls the same helper in-body for the
    same reason.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')
    if not can_edit_project_governance(current_user, project):
        abort(403)

    # Idempotent: a double-click must not write two 'activated' events.
    if project.is_active:
        return htmx_success_message(
            {'refreshXrasTab': {}},
            f'{project.projcode} is already active.',
            detail='Nothing to do.')

    with management_transaction(db.session):
        # reactivate(), not update(active=True): the latter deliberately leaves
        # inactivate_time alone (see the method docstring for why widening it
        # would corrupt unrelated admin saves).
        project.reactivate()
        _record_activation_event(project, 'activated')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'Activated {project.projcode}.',
        detail=project.title or None)


@bp.route('/xras_dismiss_form/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dismiss_form(project_id: int):
    """Modal body: ask for the reason a project should not be activated."""
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')
    return render_template(
        'dashboards/allocations/partials/xras_pending_event_form.html',
        project=project,
        post_url=url_for('allocations_dashboard.xras_dismiss',
                         project_id=project_id),
    )


@bp.route('/xras_dismiss/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dismiss(project_id: int):
    """Hide a pending project from the card, with a required reason.

    Not permanent and not a delete: a dismissal is superseded by whichever comes
    later, a new XRAS action or an explicit Restore. See
    ``get_xras_pending_activation`` for the rule.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    return handle_htmx_form_post(
        schema_cls=XrasActivationEventForm,
        template='dashboards/allocations/partials/xras_pending_event_form.html',
        do_action=lambda data: _record_activation_event(
            project, 'dismissed', comment=data['comment']),
        success_triggers=_XRAS_MODAL_TRIGGERS,
        success_message=f'Dismissed {project.projcode}.',
        success_detail='It will reappear if a new XRAS action names it.',
        error_prefix='Error dismissing project',
        extra_context={
            'project': project,
            'post_url': url_for('allocations_dashboard.xras_dismiss',
                                project_id=project_id),
        },
    )


@bp.route('/xras_restore/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_restore(project_id: int):
    """Undo a dismissal.

    An append-only log has no DELETE, so this is a **superseding** event rather
    than the removal of the dismissal — the mistake and its correction both stay
    on the record, each with its own author and timestamp.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    with management_transaction(db.session):
        _record_activation_event(project, 'restored')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'Restored {project.projcode} to the worklist.')


@bp.route('/xras_history/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_history(project_id: int):
    """Modal body: the append-only operator timeline, plus an add-comment form.

    ``MANAGE_XRAS`` rather than ``VIEW_XRAS``, deliberately: the timeline surfaces
    ``notified_to``, which is project lead/admin contact detail — the same
    category of data the raw-payload gate was created for.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')
    return render_template(
        'dashboards/allocations/partials/xras_pending_history_modal.html',
        project=project,
        events=get_xras_activation_events(db.session, project_id),
        post_url=url_for('allocations_dashboard.xras_comment',
                         project_id=project_id),
    )


@bp.route('/xras_comment/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_comment(project_id: int):
    """Append a note to a pending project's timeline."""
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    return handle_htmx_form_post(
        schema_cls=XrasActivationEventForm,
        template='dashboards/allocations/partials/xras_pending_history_modal.html',
        do_action=lambda data: _record_activation_event(
            project, 'comment', comment=data['comment']),
        success_triggers=_XRAS_MODAL_TRIGGERS,
        success_message=f'Comment added to {project.projcode}.',
        error_prefix='Error adding comment',
        context_fn=lambda: {
            'project': project,
            'events': get_xras_activation_events(db.session, project_id),
            'post_url': url_for('allocations_dashboard.xras_comment',
                                project_id=project_id),
        },
    )


@bp.route('/xras_action_details/<int:action_id>')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_action_details(action_id: int):
    """HTMX fragment: full detail for a single XRAS action.

    ``include_payload`` is gated on MANAGE_XRAS at the *query* level, not just in
    the template: an operator without it never has the PII in their response body
    at all, so a view-source cannot leak what the page chose not to draw.
    """
    may_see_payload = has_permission(current_user, Permission.MANAGE_XRAS)
    rows = get_recent_xras_actions(
        db.session, action_log_id=action_id, include_payload=may_see_payload,
    )
    if not rows:
        # A bare string, not abort(404): this lands in a modal body, where a 404
        # error page would be worse than useless. text-danger-emphasis rather
        # than text-danger — the saturated brand red fails WCAG AA on the dark
        # card (3.35:1 measured); the -emphasis token is theme-aware.
        return htmx_modal_not_found('Action')
    return render_template(
        'dashboards/allocations/partials/xras_action_details_modal.html',
        r=rows[0], may_see_payload=may_see_payload,
    )


@bp.route('/xras_recheck/<int:action_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_recheck(action_id: int):
    """Re-validate a stored payload against today's code and data. Applies nothing.

    Reports the **verdict**, not the mechanism: an operator clicks this to learn
    whether a data fix took, so "Recorded as action #N" would answer a question
    nobody asked. The three outcomes map onto the ingest vocabulary — see
    ``webapp/api/xras/recheck.py``.
    """
    try:
        new_id, status = recheck_action(action_id, actor=current_user.username)
    except LookupError:
        return htmx_not_found('Action')
    except Exception:                              # pragma: no cover - defensive
        current_app.logger.exception('XRAS re-check failed for id=%s', action_id)
        # Deliberately does not interpolate the exception: this renders into the
        # operator's page, and an exception string is neither actionable nor
        # guaranteed to be free of internals. The traceback is in the log.
        return ('<div class="alert alert-danger mb-0">Re-check failed — '
                'see the application log.</div>', 500)

    headline = {
        'rechecked': 'Would succeed now.',
        'failed':    'Would still fail.',
        'manual':    'Nothing would run for this action.',
    }.get(status, 'Re-check complete.')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'{headline} (action #{action_id})',
        detail=f'Nothing was applied. Recorded as #{new_id}; open it for details.',
    )

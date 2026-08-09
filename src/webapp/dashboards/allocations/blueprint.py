"""
Allocations dashboard blueprint for admin/staff.

Provides drill-down allocation dashboard showing allocation summaries
grouped hierarchically by Resource → Facility → Allocation Type → Projects.
"""

import json

from flask import (
    Blueprint, render_template, request, flash, redirect, url_for, jsonify,
    current_app,
)
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from typing import List, Dict

from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_not_found, htmx_success_message, read_flag,
    read_layout, read_theme, register_typeahead,
)
from webapp.api.xras.replay import replay_action
from sam.integration.xras import XrasActivationEvent
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project
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
    get_latest_xras_action_id,
    get_observed_action_types,
    get_projects_by_ids,
    get_recent_xras_actions,
    get_xras_activation_events,
    get_xras_pending_activation,
    get_xras_pending_recipients,
    summarize_xras_actions,
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

    return {
        'audit_start_date': audit_start_date.strftime('%Y-%m-%d'),
        'audit_end_date': audit_end_date.strftime('%Y-%m-%d'),
        'all_resources': all_resources,
        'allowed_facility_names': allowed_facility_names,
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

    sort_by = request_args.get('sort_by') or None
    if sort_by and sort_by not in sort_whitelist:
        sort_by = None
    sort_dir = request_args.get('sort_dir', 'desc')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    try:
        page_n = max(1, int(request_args.get('page', 1)))
    except (TypeError, ValueError):
        page_n = 1
    try:
        per_page = int(request_args.get('per_page', 50))
    except (TypeError, ValueError):
        per_page = 50
    per_page = max(10, min(per_page, 200))

    return filters, {'sort_by': sort_by, 'sort_dir': sort_dir}, \
           {'n': page_n, 'per_page': per_page}


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


def _parse_xras_filters(request_args):
    """Parse filter + sort + pagination params for the XRAS fragment.

    Deliberately a sibling of ``_parse_audit_filters`` rather than a
    generalisation of it. The sort/page halves are identical by convention (that
    is what makes the shared ``sort_link`` / ``pagination`` macros work), but the
    filter halves have nothing in common — projcode/resource/username/facility
    versus status/action-type/request-number. Merging them would mean a parameter
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

    sort_by = request_args.get('sort_by') or None
    if sort_by and sort_by not in XRAS_ACTION_SORT_COLUMNS:
        sort_by = None
    sort_dir = request_args.get('sort_dir', 'desc')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    try:
        page_n = max(1, int(request_args.get('page', 1)))
    except (TypeError, ValueError):
        page_n = 1
    try:
        per_page = int(request_args.get('per_page', 50))
    except (TypeError, ValueError):
        per_page = 50
    per_page = max(10, min(per_page, 200))

    return filters, {'sort_by': sort_by, 'sort_dir': sort_dir}, \
           {'n': page_n, 'per_page': per_page}


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
    return render_template(
        'dashboards/allocations/xras.html',
        xras_start_date=start_date.strftime('%Y-%m-%d'),
        xras_end_date=end_date.strftime('%Y-%m-%d'),
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


@bp.route('/xras_pending_fragment')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_pending_fragment():
    """HTMX fragment: XRAS-touched projects still awaiting activation.

    Stands in for the success email legacy sends and SAM has no mailer for — which
    is what keeps SMTP deferred rather than a prerequisite for the POST cutover.
    See ``get_xras_pending_activation`` for what this can and cannot see, and for
    the timestamp rule that derives every state on this card.

    Two gates, both enforced HERE rather than only in the template:

    - ``recipients`` (project lead/admin contact details) is fetched only for
      ``MANAGE_XRAS``, so a ``VIEW_XRAS`` response never carries the addresses at
      all and a view-source cannot leak what the page chose not to draw. Same rule
      as the raw-payload panel.
    - ``may_activate`` is resolved **per project** through
      ``can_edit_project_governance``, not once for the card. The helper is flat
      over the user today, so this costs one extra query and buys nothing
      immediately — but the moment it becomes project- or facility-aware the card
      follows for free, whereas a card-level flag would quietly start lying. The
      POST route calls the same helper itself, so the authority stays in one
      place and this is only ever a rendering hint.
    """
    may_manage = has_permission(current_user, Permission.MANAGE_XRAS)
    include_dismissed = read_flag(request.args, 'include_dismissed') and may_manage

    # Fetched once with the dismissed rows in, then filtered here.
    #
    # `count_xras_dismissed_pending` runs this exact pipeline — the OR-join plus
    # `_activation_state` — so calling both ran it twice per render for a count
    # already present in the rows. The `include_dismissed` flag is only a `continue`
    # inside the loop, so filtering afterwards is what it would have done anyway.
    # (Safe because this route passes no `limit`; the query applies that after the
    # skip, so a limited call could not be reproduced this way.)
    all_pending = get_xras_pending_activation(db.session, include_dismissed=True)
    dismissed_count = sum(1 for p in all_pending if p['dismissed'])
    pending = (all_pending if include_dismissed
               else [p for p in all_pending if not p['dismissed']])

    project_ids = [p['project_id'] for p in pending]

    recipients = {}
    may_activate = {}
    if may_manage:
        recipients = get_xras_pending_recipients(db.session, project_ids)
        may_activate = {
            p.project_id: can_edit_project_governance(current_user, p)
            for p in get_projects_by_ids(db.session, project_ids)
        }

    return render_template(
        'dashboards/allocations/partials/xras_pending_card.html',
        pending=pending,
        recipients=recipients,
        may_activate=may_activate,
        include_dismissed=include_dismissed,
        dismissed_count=dismissed_count if may_manage else 0,
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
# OPPOSITE of what webapp/api/xras/replay.py does one screen away — see the
# docstrings below for why, because the difference is deliberate and a reader
# who has just read replay.py will expect the other answer.
# ---------------------------------------------------------------------------


def _load_pending_project(project_id):
    """Fetch the project an activation event is about, or None."""
    return db.session.get(Project, project_id)


def _record_activation_event(project, event_type, *, comment=None,
                             notified_to=None):
    """Append one operator event, with the prompting action as provenance.

    Runs inside ``management_transaction`` — deliberately unlike
    :func:`webapp.api.xras.replay.replay_action`, which commits its audit row on a
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
        xras_action_log_id=get_latest_xras_action_id(db.session,
                                                     project.project_id),
    )


@bp.route('/xras_notify/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_notify(project_id: int):
    """Record that an operator handed a pending project off. **Sends no mail.**

    SAM has no mailer — zero ``MAIL_*`` / ``flask_mail`` / ``smtplib`` wiring under
    ``src/webapp/``, and standing that up is its own follow-on change. So this
    route does the whole timestamp half (which is what the card's badge, the
    staleness rule and "notify again" are derived from) and answers with an
    explicit **Not implemented** dialog listing the addresses, rather than a
    success toast that would imply mail moved.

    The recipients are recomputed here, server-side, and stored in ``notified_to``
    — never taken from the request. "The current lead" and "who we notified" are
    different questions, and only the second one is an audit answer.

    No idempotency guard, deliberately: "notified 3 times, last by benkirk" is a
    feature of the derived design, not a bug to suppress.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    people = get_xras_pending_recipients(db.session, [project_id]).get(project_id, [])
    notified_to = '; '.join(f"{p['name']} <{p['email']}>" for p in people) or None

    with management_transaction(db.session):
        event = _record_activation_event(project, 'notified',
                                         notified_to=notified_to)

    current_app.logger.info(
        'XRAS notify recorded (no mail sent): project=%s by=%s to=%s',
        project.projcode, current_user.username, notified_to)

    return render_template(
        'dashboards/allocations/partials/xras_notify_not_implemented.html',
        project=project, people=people, recorded_at=event.creation_time,
    ), 200, {'HX-Trigger': json.dumps({'refreshXrasTab': {}})}


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
        return '<p class="text-danger-emphasis mb-0">Project not found.</p>'
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
        success_triggers={'closeActiveModal': {}, 'refreshXrasTab': {}},
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
        return '<p class="text-danger-emphasis mb-0">Project not found.</p>'
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
        success_triggers={'closeActiveModal': {}, 'refreshXrasTab': {}},
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
        return '<p class="text-danger-emphasis mb-0">Action not found.</p>'
    return render_template(
        'dashboards/allocations/partials/xras_action_details_modal.html',
        r=rows[0], may_see_payload=may_see_payload,
    )


@bp.route('/xras_replay/<int:action_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_replay(action_id: int):
    """Re-submit a stored payload's bytes as a new, linked audit row."""
    try:
        new_id = replay_action(action_id, actor=current_user.username)
    except LookupError:
        return htmx_not_found('Action')
    except Exception as exc:                       # pragma: no cover - defensive
        current_app.logger.exception('XRAS replay failed for id=%s', action_id)
        return (f'<div class="alert alert-danger mb-0">Replay failed: {exc}</div>', 500)

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'Replayed action #{action_id}.',
        detail=f'Recorded as action #{new_id}.',
    )

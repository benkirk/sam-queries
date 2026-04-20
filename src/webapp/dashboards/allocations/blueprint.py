"""
Allocations dashboard blueprint for admin/staff.

Provides drill-down allocation dashboard showing allocation summaries
grouped hierarchically by Resource → Facility → Allocation Type → Projects.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from typing import List, Dict

from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils.htmx import handle_htmx_form_post
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
from sam.queries.usage_cache import cached_allocation_usage, purge_usage_cache, usage_cache_info
from sam.queries.lookups import find_project_by_code
from sam.schemas.forms import CreateChargeAdjustmentForm
from webapp.utils.rbac import require_permission, Permission
from sam.resources.resources import Resource
from ..charts import generate_facility_pie_chart_matplotlib, generate_allocation_type_pie_chart_matplotlib

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
@require_permission(Permission.VIEW_PROJECTS)
@cache.cached(make_cache_key=user_aware_cache_key)
def index():
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

    # Group results hierarchically for tab structure
    grouped_data = group_by_resource_facility(summary_data)

    # Get resource type mapping for conditional display
    resource_types = get_resource_types(db.session)

    # Batch-fetch all facility overviews in a single query
    # Also returns per-type annualized rates (same query, grouped one level deeper)
    all_overviews, type_annualized_rates = get_all_facility_overviews(db.session, list(grouped_data.keys()), active_at)

    # Generate facility pie chart SVGs (cached via lru_cache)
    resource_overviews = {}
    for rn in grouped_data.keys():
        overview_data = all_overviews.get(rn, [])
        resource_overviews[rn] = {
            'table_data': overview_data,
            'chart': generate_facility_pie_chart_matplotlib(overview_data),
        }

    # Generate allocation type pie chart SVGs per resource/facility
    allocation_type_charts = {}
    for resource_name, facilities in grouped_data.items():
        allocation_type_charts[resource_name] = {}
        for facility_name, types in facilities.items():
            if len(types) > 1:
                allocation_type_charts[resource_name][facility_name] = \
                    generate_allocation_type_pie_chart_matplotlib(types)
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
                    generate_allocation_type_pie_chart_matplotlib(chartable)
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
            'chart': generate_facility_pie_chart_matplotlib(chartable)
                     if chartable else '<div class="text-center text-muted small py-3">No usage data yet</div>',
        }

    # Defaults for the shared audit filter (Transactions + Adjustments tabs).
    # Default window is last 30 days; each tab's filter form is pre-filled with these.
    audit_end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    audit_start_date = audit_end_date - timedelta(days=30)

    return render_template(
        'dashboards/allocations/dashboard.html',
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
        audit_start_date=audit_start_date.strftime('%Y-%m-%d'),
        audit_end_date=audit_end_date.strftime('%Y-%m-%d'),
    )


@bp.route('/projects')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
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
@require_permission(Permission.VIEW_PROJECTS)
def transactions_fragment():
    """HTMX fragment: sortable, paginated table of recent allocation transactions."""
    filters, sort, page = _parse_audit_filters(
        request.args, ALLOCATION_TRANSACTION_SORT_COLUMNS,
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
@require_permission(Permission.VIEW_PROJECTS)
def adjustments_fragment():
    """HTMX fragment: sortable, paginated table of recent charge adjustments."""
    filters, sort, page = _parse_audit_filters(
        request.args, CHARGE_ADJUSTMENT_SORT_COLUMNS,
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
@require_permission(Permission.VIEW_PROJECTS)
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
    return render_template(
        'dashboards/allocations/partials/transaction_details_modal.html',
        r=rows[0],
    )


@bp.route('/adjustment_details/<int:adjustment_id>')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def adjustment_details(adjustment_id: int):
    """HTMX fragment: full detail for a single charge adjustment."""
    rows = get_recent_charge_adjustments(
        db.session,
        adjustment_id=adjustment_id,
        include_deleted=True,
    )
    if not rows:
        return '<p class="text-danger mb-0">Adjustment not found.</p>'
    return render_template(
        'dashboards/allocations/partials/adjustment_details_modal.html',
        r=rows[0],
    )


@bp.route('/usage/<projcode>/<resource>')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def usage_modal(projcode: str, resource: str):
    """
    AJAX fragment showing detailed usage for a specific project+resource.

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

    # Get project
    project = find_project_by_code(db.session, projcode)
    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    # Get allocation with usage details
    usage_data = cached_allocation_usage(
        session=db.session,
        resource_name=resource,
        projcode=projcode,
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
    return redirect(url_for('allocations_dashboard.index'))


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


@bp.route('/htmx/project_search_for_adjustment')
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def htmx_project_search_for_adjustment():
    """Search-as-you-type backend for the Create Adjustment project picker.

    Mirrors ``admin_dashboard.htmx_project_search_for_parent`` but guarded
    by ``EDIT_ALLOCATIONS`` (the permission that also gates the Create
    Adjustment button). Returns the same results template so the shared
    ``fk-picker.js`` click handler populates the hidden ``project_id``
    input on selection.
    """
    from sam.queries.projects import search_projects_by_code_or_title

    query = (request.args.get('q') or '').strip()
    if len(query) < 1:
        return ''

    projects = search_projects_by_code_or_title(
        db.session, query, active=True,
    )[:10]

    return render_template(
        'dashboards/admin/fragments/project_search_results_fk_htmx.html',
        projects=projects,
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

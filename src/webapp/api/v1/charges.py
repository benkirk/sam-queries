"""
Charge and balance API endpoints (v1).

NEW endpoints for charge summaries and account balances.

Example usage:
    GET /api/v1/projects/<projcode>/charges?start_date=2024-01-01&end_date=2024-12-31
    GET /api/v1/projects/<projcode>/charges/summary
    GET /api/v1/accounts/<account_id>/balance
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from webapp.utils.rbac import require_permission, Permission
from webapp.extensions import db
from sam.schemas import (
    AllocationWithUsageSchema,
    CompChargeSummarySchema,
    DavChargeSummarySchema,
    DiskChargeSummarySchema,
    ArchiveChargeSummarySchema,
    HPCChargeDetailSchema,
    DavChargeDetailSchema,
    DiskChargeDetailSchema,
    ArchiveChargeDetailSchema
)
from webapp.api.helpers import register_error_handlers, get_project_or_404, parse_date_range
from datetime import datetime, timedelta
from sam.summaries.archive_summaries import *
from sam.integration.xras_views import *

from sam.queries import get_daily_charge_trends_for_accounts, get_raw_charge_summaries_for_accounts

bp = Blueprint('api_charges', __name__)
register_error_handlers(bp)


@bp.route('/projects/<projcode>/charges', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_charges(projcode):
    """
    GET /api/v1/projects/<projcode>/charges - Get detailed charge summaries by date range.

    Query parameters:
        start_date (str): Start date (YYYY-MM-DD) - defaults to 90 days ago
        end_date (str): End date (YYYY-MM-DD) - defaults to today
        resource (str): Optional filter by resource name
        group_by (str): Optional 'date' to get time series aggregated by date

    Returns:
        JSON with charge summaries grouped by resource type, or time series if group_by=date
    """
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sam.summaries.comp_summaries import CompChargeSummary
    from sam.summaries.dav_summaries import DavChargeSummary
    from sam.summaries.disk_summaries import DiskChargeSummary
    from sam.summaries.archive_summaries import ArchiveChargeSummary

    project, error = get_project_or_404(db.session, projcode)
    if error:
        return error

    # Parse date parameters
    start_date, end_date, error = parse_date_range(days_back=90)
    if error:
        return error

    resource_name = request.args.get('resource')
    group_by = request.args.get('group_by')

    # Get account for this project/resource
    accounts_query = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.deleted == False
    )

    if resource_name:
        resource = db.session.query(Resource).filter_by(resource_name=resource_name).first()
        if not resource:
            return jsonify({'error': 'Resource not found'}), 404
        accounts_query = accounts_query.filter(Account.resource_id == resource.resource_id)
        account = accounts_query.first()
        if not account:
            return jsonify({'error': 'No account found for this project/resource'}), 404
        account_ids = [account.account_id]
        resource_type = resource.resource_type.resource_type if resource.resource_type else 'UNKNOWN'
    else:
        account_ids = [acc.account_id for acc in accounts_query.all()]
        resource_type = None

    if not account_ids:
        return jsonify({
            'projcode': projcode,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'charges': {
                'comp': [],
                'dav': [],
                'disk': [],
                'archive': []
            }
        })

    # If group_by=date, return time series aggregated by date
    if group_by == 'date':
        daily_data = get_daily_charge_trends_for_accounts(
            db.session,
            account_ids=account_ids,
            start_date=start_date,
            end_date=end_date,
            resource_type=resource_type
        )

        # Convert to sorted list for charting
        sorted_data = sorted([
            {'date': date, **values}
            for date, values in daily_data.items()
        ], key=lambda x: x['date'])

        return jsonify({
            'projcode': projcode,
            'resource_name': resource_name,
            'resource_type': resource_type,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'data': sorted_data
        })

    # Otherwise, return raw charge summaries
    raw_charges_data = get_raw_charge_summaries_for_accounts(
        db.session,
        account_ids=account_ids,
        start_date=start_date,
        end_date=end_date,
        resource_type=resource_type
    )

    return jsonify({
        'projcode': projcode,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'charges': {
            'comp': CompChargeSummarySchema(many=True).dump(raw_charges_data.get('comp', [])),
            'dav': DavChargeSummarySchema(many=True).dump(raw_charges_data.get('dav', [])),
            'disk': DiskChargeSummarySchema(many=True).dump(raw_charges_data.get('disk', [])),
            'archive': ArchiveChargeSummarySchema(many=True).dump(raw_charges_data.get('archive', []))
        }
    })


@bp.route('/projects/<projcode>/charges/summary', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_charges_summary(projcode):
    """
    GET /api/v1/projects/<projcode>/charges/summary - Get aggregated charge totals by resource type.

    Query parameters:
        start_date (str): Start date (YYYY-MM-DD) - defaults to allocation start
        end_date (str): End date (YYYY-MM-DD) - defaults to today

    Returns:
        JSON with total charges by resource type for all active allocations
    """
    from sam.accounting.accounts import Account
    from sam.summaries.comp_summaries import CompChargeSummary
    from sam.summaries.dav_summaries import DavChargeSummary
    from sam.summaries.disk_summaries import DiskChargeSummary
    from sam.summaries.archive_summaries import ArchiveChargeSummary

    project, error = get_project_or_404(db.session, projcode)
    if error:
        return error

    # Get all accounts
    accounts = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.deleted == False
    ).all()

    summary_by_resource = {}

    for account in accounts:
        if not account.resource:
            continue

        resource_name = account.resource.resource_name
        resource_type = account.resource.resource_type.resource_type if account.resource.resource_type else 'UNKNOWN'

        # Find active allocation
        now = datetime.now()
        active_alloc = None
        for alloc in account.allocations:
            if alloc.is_active_at(now) and not alloc.deleted:
                active_alloc = alloc
                break

        if not active_alloc:
            continue

        # Use AllocationWithUsageSchema to get usage details
        schema = AllocationWithUsageSchema()
        schema.context = {
            'account': account,
            'session': db.session,
            'include_adjustments': True
        }
        usage_data = schema.dump(active_alloc)

        summary_by_resource[resource_name] = {
            'resource_type': resource_type,
            'allocated': usage_data['amount'],
            'used': usage_data['used'],
            'remaining': usage_data['remaining'],
            'percent_used': usage_data['percent_used'],
            'charges_by_type': usage_data['charges_by_type'],
            'adjustments': usage_data['adjustments'],
            'start_date': usage_data['start_date'],
            'end_date': usage_data['end_date'],
        }

    return jsonify({
        'projcode': projcode,
        'resources': summary_by_resource,
        'total_resources': len(summary_by_resource)
    })

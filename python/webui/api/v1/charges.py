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
from webui.utils.rbac import require_permission, Permission
from webui.extensions import db
from webui.schemas import (
    AllocationWithUsageSchema,
    CompChargeSummarySchema,
    DavChargeSummarySchema,
    DiskChargeSummarySchema,
    ArchiveChargeSummarySchema
)
from datetime import datetime, timedelta
from sqlalchemy import func

bp = Blueprint('api_charges', __name__)


@bp.route('/projects/<projcode>/charges', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_charges(projcode):
    """
    GET /api/v1/projects/<projcode>/charges - Get detailed charge summaries by date range.

    Query parameters:
        start_date (str): Start date (YYYY-MM-DD) - defaults to 30 days ago
        end_date (str): End date (YYYY-MM-DD) - defaults to today
        resource_id (int): Optional filter by specific resource

    Returns:
        JSON with charge summaries grouped by resource type
    """
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.summaries.comp_summaries import CompChargeSummary
    from sam.summaries.dav_summaries import DavChargeSummary
    from sam.summaries.disk_summaries import DiskChargeSummary
    from sam.summaries.archive_summaries import ArchiveChargeSummary

    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Parse date parameters
    try:
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d') if request.args.get('end_date') else datetime.now()
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d') if request.args.get('start_date') else end_date - timedelta(days=30)
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    resource_id = request.args.get('resource_id', type=int)

    # Get all accounts for this project
    accounts_query = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.deleted == False
    )
    if resource_id:
        accounts_query = accounts_query.filter(Account.resource_id == resource_id)

    account_ids = [acc.account_id for acc in accounts_query.all()]

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

    # Query each charge type
    comp_charges = db.session.query(CompChargeSummary).filter(
        CompChargeSummary.account_id.in_(account_ids),
        CompChargeSummary.activity_date >= start_date,
        CompChargeSummary.activity_date <= end_date
    ).all()

    dav_charges = db.session.query(DavChargeSummary).filter(
        DavChargeSummary.account_id.in_(account_ids),
        DavChargeSummary.activity_date >= start_date,
        DavChargeSummary.activity_date <= end_date
    ).all()

    disk_charges = db.session.query(DiskChargeSummary).filter(
        DiskChargeSummary.account_id.in_(account_ids),
        DiskChargeSummary.activity_date >= start_date,
        DiskChargeSummary.activity_date <= end_date
    ).all()

    archive_charges = db.session.query(ArchiveChargeSummary).filter(
        ArchiveChargeSummary.account_id.in_(account_ids),
        ArchiveChargeSummary.activity_date >= start_date,
        ArchiveChargeSummary.activity_date <= end_date
    ).all()

    return jsonify({
        'projcode': projcode,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'charges': {
            'comp': CompChargeSummarySchema(many=True).dump(comp_charges),
            'dav': DavChargeSummarySchema(many=True).dump(dav_charges),
            'disk': DiskChargeSummarySchema(many=True).dump(disk_charges),
            'archive': ArchiveChargeSummarySchema(many=True).dump(archive_charges)
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
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.summaries.comp_summaries import CompChargeSummary
    from sam.summaries.dav_summaries import DavChargeSummary
    from sam.summaries.disk_summaries import DiskChargeSummary
    from sam.summaries.archive_summaries import ArchiveChargeSummary

    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

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


@bp.route('/accounts/<int:account_id>/balance', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_account_balance(account_id):
    """
    GET /api/v1/accounts/<account_id>/balance - Get current account balance.

    Shows active allocation with usage details (real-time calculation).

    Query parameters:
        include_adjustments (bool): Include manual adjustments (default: true)

    Returns:
        JSON with allocation, used, remaining (matches sam_search.py output)
    """
    from sam.accounting.accounts import Account

    account = db.session.query(Account).filter(Account.account_id == account_id).first()
    if not account:
        return jsonify({'error': 'Account not found'}), 404

    include_adjustments = request.args.get('include_adjustments', 'true').lower() == 'true'

    # Find active allocation
    now = datetime.now()
    active_alloc = None
    for alloc in account.allocations:
        if alloc.is_active_at(now) and not alloc.deleted:
            active_alloc = alloc
            break

    if not active_alloc:
        return jsonify({
            'account_id': account_id,
            'error': 'No active allocation found'
        }), 404

    # Serialize with AllocationWithUsageSchema for full balance details
    schema = AllocationWithUsageSchema()
    schema.context = {
        'account': account,
        'session': db.session,
        'include_adjustments': include_adjustments
    }
    balance_data = schema.dump(active_alloc)

    return jsonify({
        'account_id': account_id,
        'project_code': account.project.projcode if account.project else None,
        'resource_name': account.resource.resource_name if account.resource else None,
        'balance': balance_data
    })


@bp.errorhandler(403)
def forbidden(e):
    """Handle forbidden access."""
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403


@bp.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access."""
    return jsonify({'error': 'Unauthorized - authentication required'}), 401

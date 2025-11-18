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


@bp.route('/allocations/changes', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_allocation_changes():
    """
    GET /api/v1/allocations/changes - Get allocation and charge adjustments history.

    Query parameters:
        projcode (str): Project code (required)
        resource (str): Resource name (required)

    Returns:
        JSON with list of allocation changes and charge adjustments
    """
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    from sam.accounting.adjustments import ChargeAdjustment

    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        return jsonify({'error': 'Both projcode and resource parameters are required'}), 400

    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Find the account for this project/resource
    from sam.resources.resources import Resource
    resource = db.session.query(Resource).filter(
        Resource.resource_name == resource_name
    ).first()

    if not resource:
        return jsonify({'error': 'Resource not found'}), 404

    account = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.resource_id == resource.resource_id,
        Account.deleted == False
    ).first()

    if not account:
        return jsonify({'changes': []})

    changes = []

    # Get allocation transactions (new allocations and adjustments)
    for allocation in account.allocations:
        if allocation.deleted:
            continue

        # Determine allocation type
        alloc_type = 'Allocation: New'
        comment = ''

        # Check if this is an adjustment (has parent)
        if allocation.parent_id:
            parent = db.session.query(Allocation).filter(
                Allocation.allocation_id == allocation.parent_id
            ).first()
            if parent:
                amount_diff = allocation.amount - parent.amount
                if amount_diff != 0:
                    alloc_type = 'Allocation: Adjustment'
                    comment = allocation.comment or ''

                    changes.append({
                        'date': allocation.start_date.strftime('%Y-%m-%d') if allocation.start_date else 'N/A',
                        'type': alloc_type,
                        'comment': comment,
                        'amount': amount_diff
                    })
                    continue

        # For new allocations
        if allocation.amount and allocation.amount != 0:
            changes.append({
                'date': allocation.start_date.strftime('%Y-%m-%d') if allocation.start_date else 'N/A',
                'type': alloc_type,
                'comment': allocation.comment or '',
                'amount': allocation.amount
            })

    # Get charge adjustments (credits, corrections, etc.)
    charge_adjustments = db.session.query(ChargeAdjustment).filter(
        ChargeAdjustment.account_id == account.account_id
    ).all()

    for adj in charge_adjustments:
        adj_type = 'Charge: Credit'
        if adj.adjustment_type:
            if 'reservation' in adj.adjustment_type.lower():
                adj_type = 'Charge: Reservation'
            elif 'credit' in adj.adjustment_type.lower():
                adj_type = 'Charge: Credit'
            elif 'correction' in adj.adjustment_type.lower():
                adj_type = 'Charge: Correction'
            else:
                adj_type = f'Charge: {adj.adjustment_type}'

        changes.append({
            'date': adj.adjustment_date.strftime('%Y-%m-%d') if adj.adjustment_date else 'N/A',
            'type': adj_type,
            'comment': adj.comment or '',
            'amount': adj.amount or 0
        })

    # Sort by date (most recent first)
    changes.sort(key=lambda x: x['date'], reverse=True)

    return jsonify({
        'projcode': projcode,
        'resource': resource_name,
        'changes': changes,
        'total_changes': len(changes)
    })


@bp.route('/charges/details', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_charge_details():
    """
    GET /api/v1/charges/details - Get detailed individual charge records.

    Query parameters:
        projcode (str): Project code (required)
        resource (str): Resource name (required)
        start_date (str): Start date (YYYY-MM-DD) - defaults to allocation start
        end_date (str): End date (YYYY-MM-DD) - defaults to today

    Returns:
        JSON with list of individual charge records including date, type, comment, user, amount
    """
    from sam.queries import find_project_by_code
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sam.activity.hpc_activity import HPCActivity, HPCCharge
    from sam.activity.dav_activity import DavActivity, DavCharge
    from sam.activity.disk_activity import DiskActivity, DiskCharge
    from sam.activity.archive_activity import ArchiveActivity, ArchiveCharge
    from sam.core.users import User

    projcode = request.args.get('projcode')
    resource_name = request.args.get('resource')

    if not projcode or not resource_name:
        return jsonify({'error': 'Both projcode and resource parameters are required'}), 400

    project = find_project_by_code(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    resource = db.session.query(Resource).filter(
        Resource.resource_name == resource_name
    ).first()

    if not resource:
        return jsonify({'error': 'Resource not found'}), 404

    account = db.session.query(Account).filter(
        Account.project_id == project.project_id,
        Account.resource_id == resource.resource_id,
        Account.deleted == False
    ).first()

    if not account:
        return jsonify({'charges': []})

    # Parse date parameters
    try:
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d') if request.args.get('end_date') else datetime.now()
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d') if request.args.get('start_date') else end_date - timedelta(days=90)
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    charges = []

    # Determine resource type and query appropriate charges
    resource_type = resource.resource_type.resource_type if resource.resource_type else 'UNKNOWN'

    if resource_type in ['HPC', 'DAV']:
        # Query HPC charges
        hpc_charges = db.session.query(HPCCharge, HPCActivity, User).join(
            HPCActivity, HPCCharge.hpc_activity_id == HPCActivity.hpc_activity_id
        ).outerjoin(
            User, HPCActivity.user_id == User.user_id
        ).filter(
            HPCCharge.account_id == account.account_id,
            HPCActivity.activity_date >= start_date,
            HPCActivity.activity_date <= end_date
        ).order_by(HPCActivity.activity_date.desc()).limit(1000).all()

        for hpc_charge, hpc_activity, user in hpc_charges:
            charges.append({
                'date': hpc_activity.activity_date.strftime('%Y-%m-%d') if hpc_activity.activity_date else 'N/A',
                'type': 'HPC Compute',
                'comment': f'Job {hpc_activity.job_id}' if hpc_activity.job_id else '-',
                'user': user.username if user else '-',
                'amount': float(hpc_charge.charge) if hpc_charge.charge else 0
            })

        # Query DAV charges if applicable
        dav_charges = db.session.query(DavCharge, DavActivity, User).join(
            DavActivity, DavCharge.dav_activity_id == DavActivity.dav_activity_id
        ).outerjoin(
            User, DavActivity.user_id == User.user_id
        ).filter(
            DavCharge.account_id == account.account_id,
            DavActivity.activity_date >= start_date,
            DavActivity.activity_date <= end_date
        ).order_by(DavActivity.activity_date.desc()).limit(1000).all()

        for dav_charge, dav_activity, user in dav_charges:
            charges.append({
                'date': dav_activity.activity_date.strftime('%Y-%m-%d') if dav_activity.activity_date else 'N/A',
                'type': 'DAV',
                'comment': f'Session {dav_activity.session_id}' if dav_activity.session_id else '-',
                'user': user.username if user else '-',
                'amount': float(dav_charge.charge) if dav_charge.charge else 0
            })

    elif resource_type == 'DISK':
        # Query Disk charges
        disk_charges = db.session.query(DiskCharge, DiskActivity, User).join(
            DiskActivity, DiskCharge.disk_activity_id == DiskActivity.disk_activity_id
        ).outerjoin(
            User, DiskActivity.user_id == User.user_id
        ).filter(
            DiskCharge.account_id == account.account_id,
            DiskActivity.activity_date >= start_date,
            DiskActivity.activity_date <= end_date
        ).order_by(DiskActivity.activity_date.desc()).limit(1000).all()

        for disk_charge, disk_activity, user in disk_charges:
            charges.append({
                'date': disk_activity.activity_date.strftime('%Y-%m-%d') if disk_activity.activity_date else 'N/A',
                'type': 'Disk Storage',
                'comment': f'{disk_activity.volume_gb} GB' if disk_activity.volume_gb else '-',
                'user': user.username if user else '-',
                'amount': float(disk_charge.charge) if disk_charge.charge else 0
            })

    elif resource_type == 'ARCHIVE':
        # Query Archive charges
        archive_charges = db.session.query(ArchiveCharge, ArchiveActivity, User).join(
            ArchiveActivity, ArchiveCharge.archive_activity_id == ArchiveActivity.archive_activity_id
        ).outerjoin(
            User, ArchiveActivity.user_id == User.user_id
        ).filter(
            ArchiveCharge.account_id == account.account_id,
            ArchiveActivity.activity_date >= start_date,
            ArchiveActivity.activity_date <= end_date
        ).order_by(ArchiveActivity.activity_date.desc()).limit(1000).all()

        for archive_charge, archive_activity, user in archive_charges:
            charges.append({
                'date': archive_activity.activity_date.strftime('%Y-%m-%d') if archive_activity.activity_date else 'N/A',
                'type': 'Archive',
                'comment': f'{archive_activity.volume_gb} GB' if archive_activity.volume_gb else '-',
                'user': user.username if user else '-',
                'amount': float(archive_charge.charge) if archive_charge.charge else 0
            })

    # Sort by date descending
    charges.sort(key=lambda x: x['date'], reverse=True)

    return jsonify({
        'projcode': projcode,
        'resource': resource_name,
        'charges': charges,
        'total_charges': len(charges)
    })


@bp.errorhandler(403)
def forbidden(e):
    """Handle forbidden access."""
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403


@bp.errorhandler(401)
def unauthorized(e):
    """Handle unauthorized access."""
    return jsonify({'error': 'Unauthorized - authentication required'}), 401

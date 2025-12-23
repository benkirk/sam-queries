"""
Allocation API endpoints (v1).

Provides RESTful API for allocation management with RBAC and audit logging.

Example usage:
    GET /api/v1/allocations/123
    PUT /api/v1/allocations/123
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from webapp.utils.rbac import require_permission, Permission
from webapp.extensions import db
from sam.schemas import AllocationWithUsageSchema
from sam import Allocation
from sam.manage import update_allocation, management_transaction
from webapp.api.helpers import register_error_handlers
from datetime import datetime

bp = Blueprint('api_allocations', __name__)
register_error_handlers(bp)


# ============================================================================
# Allocation Routes
# ============================================================================

@bp.route('/<int:allocation_id>', methods=['GET'])
@login_required
def get_allocation(allocation_id):
    """
    GET /api/v1/allocations/<allocation_id> - Get allocation details with usage data.

    Returns:
        JSON with allocation details including usage, charges, and balances

    Requires:
        User must be authenticated
        Note: Permission checks could be enhanced to verify project membership
    """
    allocation = db.session.get(Allocation, allocation_id)
    if not allocation:
        return jsonify({'error': f'Allocation {allocation_id} not found'}), 404

    # Get account for schema context
    account = allocation.account

    # Serialize with usage data
    schema = AllocationWithUsageSchema()
    schema.context = {
        'account': account,
        'session': db.session,
        'include_adjustments': request.args.get('include_adjustments', 'true').lower() == 'true'
    }

    return jsonify(schema.dump(allocation))


@bp.route('/<int:allocation_id>', methods=['PUT'])
@login_required
@require_permission(Permission.EDIT_ALLOCATIONS)
def update_allocation_endpoint(allocation_id):
    """
    PUT /api/v1/allocations/<allocation_id> - Update allocation with audit logging.

    JSON body (all fields optional):
        amount (float): New allocation amount
        start_date (str): New start date YYYY-MM-DD
        end_date (str): New end date YYYY-MM-DD (or null to clear)
        description (str): New description

    Returns:
        JSON with updated allocation details

    Requires:
        EDIT_ALLOCATIONS permission
    """
    allocation = db.session.get(Allocation, allocation_id)
    if not allocation:
        return jsonify({'error': f'Allocation {allocation_id} not found'}), 404

    # Get data from JSON body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body must be JSON'}), 400

    # Build updates dict
    updates = {}

    # Parse amount
    if 'amount' in data:
        try:
            updates['amount'] = float(data['amount'])
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid amount format. Must be a number.'}), 400

    # Parse dates
    try:
        if 'start_date' in data and data['start_date']:
            updates['start_date'] = datetime.strptime(data['start_date'], '%Y-%m-%d')

        if 'end_date' in data:
            # Allow null/empty to clear end date
            if data['end_date']:
                updates['end_date'] = datetime.strptime(data['end_date'], '%Y-%m-%d')
            else:
                updates['end_date'] = None
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    # Description
    if 'description' in data:
        updates['description'] = data['description']

    # Validate we have something to update
    if not updates:
        return jsonify({'error': 'No valid update fields provided'}), 400

    # Perform update with audit logging
    try:
        with management_transaction(db.session):
            updated_allocation = update_allocation(
                db.session,
                allocation_id,
                current_user.user_id,
                **updates
            )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except KeyError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to update allocation: {str(e)}'}), 500

    # Return updated allocation with usage data
    account = updated_allocation.account
    schema = AllocationWithUsageSchema()
    schema.context = {
        'account': account,
        'session': db.session,
        'include_adjustments': True
    }

    return jsonify({
        'success': True,
        'message': f'Allocation {allocation_id} updated successfully',
        'allocation': schema.dump(updated_allocation)
    })

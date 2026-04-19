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
from webapp.api.access_control import require_allocation_permission
from sam.schemas.forms import EditAllocationForm
from marshmallow import ValidationError
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
@require_allocation_permission(Permission.EDIT_ALLOCATIONS)
def update_allocation_endpoint(allocation):
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
        EDIT_ALLOCATIONS permission, OR project lead/admin of the
        allocation's project (or any ancestor in its tree).
    """
    allocation_id = allocation.allocation_id

    # Get data from JSON body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body must be JSON'}), 400

    # Validate and coerce input via form schema (partial=True: no field is required for PUT)
    try:
        form_data = EditAllocationForm().load(data, partial=True)
    except ValidationError as e:
        errors = EditAllocationForm.flatten_errors(e.messages)
        return jsonify({'error': errors[0] if errors else 'Invalid input'}), 400

    # Build updates dict — key on original data to avoid overwriting fields not in the request
    updates = {}
    if 'amount' in data:
        updates['amount'] = form_data['amount']
    if 'start_date' in data:
        raw = form_data.get('start_date')
        updates['start_date'] = datetime.combine(raw, datetime.min.time()) if raw else None
    if 'end_date' in data:
        updates['end_date'] = form_data.get('end_date')  # datetime or None
    if 'description' in data:
        updates['description'] = form_data.get('description')

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

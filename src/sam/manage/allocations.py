"""
Allocation management functions with integrated audit logging.

Administrative operations for managing allocations with automatic
audit trail creation in allocation_transaction table.
"""

from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from sam.accounting.allocations import Allocation, AllocationTransaction, AllocationTransactionType


__all__ = [
    'validate_allocation_dates',
    'log_allocation_transaction',
    'update_allocation',
]


def validate_allocation_dates(start_date: datetime, end_date: Optional[datetime] = None) -> None:
    """
    Validate allocation date ranges.

    Args:
        start_date: Allocation start date
        end_date: Allocation end date (optional)

    Raises:
        ValueError: If end_date is before start_date
    """
    if end_date is not None and end_date < start_date:
        raise ValueError(f"End date ({end_date}) cannot be before start date ({start_date})")


def log_allocation_transaction(
    session: Session,
    allocation: Allocation,
    user_id: int,
    transaction_type: str,
    comment: Optional[str] = None,
    old_values: Optional[Dict[str, Any]] = None
) -> AllocationTransaction:
    """
    Create an audit log entry for an allocation change.

    Creates an AllocationTransaction record tracking who made what changes to
    an allocation and when. Captures the current state of the allocation after
    the change.

    NOTE: This function does NOT commit the session. The caller is responsible
    for calling session.commit() or session.flush() as appropriate.

    Args:
        session: SQLAlchemy session
        allocation: Allocation object being modified
        user_id: User making the change (from flask_login.current_user)
        transaction_type: One of AllocationTransactionType constants
        comment: Optional custom comment
        old_values: Dict with previous values (for EDIT type) - keys: amount, start_date, end_date, description

    Returns:
        AllocationTransaction: The created transaction record

    Example:
        old_values = {
            'amount': 1000.0,
            'start_date': datetime(2024, 1, 1),
            'end_date': datetime(2024, 12, 31),
            'description': 'Original allocation'
        }
        log_allocation_transaction(
            session, allocation, user_id,
            AllocationTransactionType.EDIT,
            old_values=old_values
        )
    """
    # Build change description for EDIT transactions
    auto_comment_parts = []
    if transaction_type == AllocationTransactionType.EDIT and old_values:
        if 'amount' in old_values and old_values['amount'] != allocation.amount:
            auto_comment_parts.append(
                f"Amount: {old_values['amount']} → {allocation.amount}"
            )
        if 'start_date' in old_values and old_values['start_date'] != allocation.start_date:
            auto_comment_parts.append(
                f"Start date: {old_values['start_date'].strftime('%Y-%m-%d')} → "
                f"{allocation.start_date.strftime('%Y-%m-%d')}"
            )
        if 'end_date' in old_values:
            old_end = old_values['end_date'].strftime('%Y-%m-%d') if old_values['end_date'] else 'None'
            new_end = allocation.end_date.strftime('%Y-%m-%d') if allocation.end_date else 'None'
            if old_values['end_date'] != allocation.end_date:
                auto_comment_parts.append(
                    f"End date: {old_end} → {new_end}"
                )
        if 'description' in old_values and old_values['description'] != allocation.description:
            auto_comment_parts.append(
                f"Description updated"
            )

    # Combine auto-generated comment with custom comment
    final_comment = '; '.join(auto_comment_parts) if auto_comment_parts else None
    if comment:
        final_comment = f"{final_comment}; {comment}" if final_comment else comment

    # Create transaction record
    transaction = AllocationTransaction(
        allocation_id=allocation.allocation_id,
        user_id=user_id,
        transaction_type=transaction_type,
        alloc_start_date=allocation.start_date,
        alloc_end_date=allocation.end_date,
        transaction_amount=allocation.amount,
        requested_amount=allocation.amount,  # Same as transaction_amount for EDIT
        transaction_comment=final_comment,
        propagated=False
    )

    session.add(transaction)
    session.flush()

    return transaction


def update_allocation(
    session: Session,
    allocation_id: int,
    user_id: int,
    **updates
) -> Allocation:
    """
    Update allocation fields with automatic audit logging.

    Updates the specified allocation and creates an audit trail entry in
    allocation_transaction table.

    NOTE: This function does NOT commit the session. The caller is responsible
    for calling session.commit() or session.flush() as appropriate.

    Args:
        session: SQLAlchemy session
        allocation_id: ID of allocation to update
        user_id: User making the change (for audit trail)
        **updates: Fields to update (amount, start_date, end_date, description)

    Returns:
        Allocation: The updated allocation object

    Raises:
        ValueError: If allocation not found, invalid dates, or invalid amount
        KeyError: If unknown update field provided

    Example:
        from sam.manage.transaction import management_transaction

        with management_transaction(session):
            allocation = update_allocation(
                session,
                allocation_id=123,
                user_id=456,
                amount=1500.0,
                end_date=datetime(2025, 12, 31)
            )
    """
    # Load allocation
    allocation = session.get(Allocation, allocation_id)
    if not allocation:
        raise ValueError(f"Allocation {allocation_id} not found")

    # Validate update fields
    allowed_fields = {'amount', 'start_date', 'end_date', 'description'}
    provided_fields = set(updates.keys())
    unknown_fields = provided_fields - allowed_fields
    if unknown_fields:
        raise KeyError(f"Unknown update fields: {unknown_fields}")

    # Store old values for audit trail
    old_values = {
        'amount': allocation.amount,
        'start_date': allocation.start_date,
        'end_date': allocation.end_date,
        'description': allocation.description
    }

    # Validate amount if provided
    if 'amount' in updates:
        if updates['amount'] <= 0:
            raise ValueError(f"Amount must be greater than 0, got {updates['amount']}")

    # Extract dates for validation
    new_start = updates.get('start_date', allocation.start_date)
    new_end = updates.get('end_date', allocation.end_date)

    # Validate dates
    validate_allocation_dates(new_start, new_end)

    # Apply updates
    for field, value in updates.items():
        setattr(allocation, field, value)

    session.flush()

    # Create audit trail entry
    log_allocation_transaction(
        session,
        allocation,
        user_id,
        AllocationTransactionType.EDIT,
        old_values=old_values
    )

    return allocation

"""
Allocation management functions with integrated audit logging.

Administrative operations for managing allocations with automatic
audit trail creation in allocation_transaction table.
"""

from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation, AllocationTransaction, AllocationTransactionType,
    InheritingAllocationException,
)


__all__ = [
    'validate_allocation_dates',
    'log_allocation_transaction',
    'create_allocation',
    'update_allocation',
    'propagate_allocation_to_subprojects',
    'detach_allocation',
    'get_partitioned_descendant_sum',
    'InheritingAllocationException',
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
    old_values: Optional[Dict[str, Any]] = None,
    propagated: bool = False,
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
        propagated=propagated,
    )

    session.add(transaction)
    session.flush()

    return transaction


def create_allocation(
    session: Session,
    *,
    project_id: int,
    resource_id: int,
    amount: float,
    start_date: datetime,
    end_date: Optional[datetime] = None,
    description: Optional[str] = None,
    user_id: int,
) -> 'Allocation':
    """Create a new allocation for a project + resource pair.

    Gets or creates the Account linking project ↔ resource, instantiates
    the Allocation record, and logs an AllocationTransaction(CREATE) for
    audit purposes.

    NOTE: Does NOT commit the session.  The caller is responsible for
    wrapping the call in ``management_transaction`` (or committing).

    Args:
        session:     SQLAlchemy session.
        project_id:  FK to Project.
        resource_id: FK to Resource.
        amount:      Allocation amount (must be > 0).
        start_date:  Start of allocation period.
        end_date:    End of allocation period (None = open-ended).
        description: Optional human-readable note.
        user_id:     FK to User performing the action (for audit log).

    Returns:
        Newly created and flushed Allocation instance.

    Example::

        with management_transaction(session):
            alloc = create_allocation(
                session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=500_000.0,
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                user_id=current_user.user_id,
            )
    """
    validate_allocation_dates(start_date, end_date)

    allocation = Allocation.create(
        session,
        project_id=project_id,
        resource_id=resource_id,
        amount=amount,
        start_date=start_date,
        end_date=end_date,
        description=description,
    )

    log_allocation_transaction(
        session,
        allocation,
        user_id,
        AllocationTransactionType.CREATE,
        comment='Allocation created',
        old_values={},
        propagated=False,
    )

    return allocation


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

    # Block direct mutation of inheriting (child) allocations
    if allocation.is_inheriting:
        raise InheritingAllocationException(
            f"Allocation {allocation_id} is a child (inheriting) allocation. "
            "Updates must be applied to the master parent allocation."
        )

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
        old_values=old_values,
    )

    # Cascade amount and date changes to all inheriting descendants.
    # description is NOT cascaded — children belong to different projects.
    cascadable = {'amount', 'start_date', 'end_date'} & provided_fields
    if cascadable and allocation.children:
        child_updates = {f: updates[f] for f in cascadable}
        child_old = {f: old_values[f] for f in cascadable}

        def _cascade_to_child(child: Allocation) -> None:
            for field, value in child_updates.items():
                setattr(child, field, value)
            log_allocation_transaction(
                session, child, user_id,
                AllocationTransactionType.EDIT,
                old_values=child_old,
                propagated=True,
            )

        for child in allocation.children:
            child._walk_tree(_cascade_to_child)
        session.flush()

    return allocation


def propagate_allocation_to_subprojects(
    session: Session,
    parent_allocation: Allocation,
    descendants,
    user_id: int,
    skip_existing: bool = True,
):
    """
    Create child allocations for each active project in ``descendants``,
    mirroring the deep-tree topology: each allocation's parent_allocation_id
    points to its immediate project-parent's allocation (not the root).

    ``descendants`` MUST be in tree_left (DFS pre-order) order so that a
    parent node always appears before its children.  project.get_descendants()
    satisfies this constraint.

    Runs inside the caller's management_transaction() — does NOT commit.

    Args:
        session:           SQLAlchemy session.
        parent_allocation: Root allocation to propagate from.
        descendants:       Ordered list of Project objects (DFS pre-order).
        user_id:           FK to User performing the action (for audit log).
        skip_existing:     When True, projects that already have a non-deleted
                           allocation for this resource are skipped (default).

    Returns:
        Tuple[List[Allocation], List[Project]]: (created, skipped)
    """
    from sam.accounting.accounts import Account

    resource_id = parent_allocation.account.resource_id
    root_project_id = parent_allocation.account.project_id

    # Seed map: root project → root allocation_id
    alloc_map = {root_project_id: parent_allocation.allocation_id}

    created, skipped = [], []

    for child_proj in descendants:
        if not child_proj.active:
            continue

        account = Account.get_by_project_and_resource(
            session, child_proj.project_id, resource_id
        )
        existing = (
            [a for a in account.allocations if not a.deleted]
            if account else []
        )

        if existing:
            if skip_existing:
                # Register in alloc_map so this project's children resolve correctly
                alloc_map[child_proj.project_id] = existing[0].allocation_id
                skipped.append(child_proj)
                continue
            else:
                raise ValueError(
                    f"Project {child_proj.projcode} already has an allocation "
                    f"for resource_id={resource_id}"
                )

        # Immediate parent's allocation_id (None if parent was inactive/missing)
        proj_parent_alloc_id = alloc_map.get(child_proj.parent_id)

        new_alloc = Allocation.create(
            session,
            project_id=child_proj.project_id,
            resource_id=resource_id,
            amount=parent_allocation.amount,
            start_date=parent_allocation.start_date,
            end_date=parent_allocation.end_date,
            parent_allocation_id=proj_parent_alloc_id,
        )

        log_allocation_transaction(
            session, new_alloc, user_id,
            AllocationTransactionType.CREATE,
            comment=f"Propagated from parent allocation #{parent_allocation.allocation_id}",
            propagated=True,
        )

        alloc_map[child_proj.project_id] = new_alloc.allocation_id
        created.append(new_alloc)

    return created, skipped


def detach_allocation(session: Session, allocation_id: int, user_id: int) -> Allocation:
    """
    Break the parent_allocation_id link on a child (inheriting) allocation.

    Sets parent_allocation_id to None, flushes, and logs a DETACH transaction.
    After this call the allocation is fully independent — future edits to the
    former parent will NOT cascade here.

    NOTE: Detaching does NOT decouple usage roll-up, which operates on the
    project tree (MPPT) regardless of allocation linkage.

    Does NOT commit; caller must wrap in management_transaction().

    Args:
        session:       SQLAlchemy session.
        allocation_id: ID of the inheriting allocation to detach.
        user_id:       FK to User performing the action (for audit log).

    Returns:
        The detached Allocation instance.

    Raises:
        ValueError: If the allocation is not found or is not inheriting.
    """
    allocation = session.get(Allocation, allocation_id)
    if not allocation or not allocation.is_inheriting:
        raise ValueError(
            f"Allocation {allocation_id} not found or is not an inheriting allocation"
        )
    old_parent_id = allocation.parent_allocation_id
    allocation.parent_allocation_id = None
    session.flush()
    log_allocation_transaction(
        session, allocation, user_id,
        AllocationTransactionType.DETACH,
        comment=f"Detached from parent allocation #{old_parent_id}",
    )
    return allocation


def get_partitioned_descendant_sum(session: Session, allocation: Allocation) -> float:
    """
    Sum the amounts of non-deleted, non-inherited (parent_allocation_id IS NULL)
    allocations on descendant projects for the same resource as ``allocation``.

    This is the correct "Case 2b" check: descendant projects that were given
    their own standalone allocation rather than a linked (shared-pool) copy.

    Returns 0.0 if allocation has no account, the project has no children,
    or no descendants have standalone allocations for this resource.

    NOTE: Do NOT use allocation.children for this — those are shared-pool copies
    with the same amount as the parent; summing them always gives a false overage
    (n × parent.amount for n children, always > parent.amount when n > 1).
    """
    from sam.accounting.accounts import Account

    if not allocation.account:
        return 0.0

    resource_id = allocation.account.resource_id
    project = allocation.account.project
    if not project or not project.has_children:
        return 0.0

    total = 0.0
    for desc in project.get_descendants():
        if not desc.active:
            continue
        acct = Account.get_by_project_and_resource(session, desc.project_id, resource_id)
        if not acct:
            continue
        for a in acct.allocations:
            if not a.deleted and a.parent_allocation_id is None:
                total += a.amount
    return total

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
    'exchange_allocations',
    'propagate_allocation_to_subprojects',
    'detach_allocation',
    'link_allocation_to_parent',
    'get_partitioned_descendant_sum',
    'date_ranges_overlap',
    'InheritingAllocationException',
]


def date_ranges_overlap(a, b) -> bool:
    """True if two allocation-like objects' [start_date, end_date] ranges overlap.

    NULL bounds are treated as open-ended. Either argument may be an Allocation
    or any object with ``start_date`` / ``end_date`` attributes.
    """
    a_start, a_end = a.start_date, a.end_date
    b_start, b_end = b.start_date, b.end_date
    if a_end is not None and b_start is not None and b_start > a_end:
        return False
    if a_start is not None and b_end is not None and b_end < a_start:
        return False
    return True


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
    *,
    comment: Optional[str] = None,
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
        comment: Optional context for the audit trail (appended after
                 the auto-generated "Amount: X → Y" diff). Use this for
                 the *reason* for the edit; do NOT smuggle it into
                 ``description=`` — that field describes what the
                 allocation is for, not why it was last edited.
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
        comment=comment,
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
                comment=comment,
                old_values=child_old,
                propagated=True,
            )

        for child in allocation.children:
            child._walk_tree(_cascade_to_child)
        session.flush()

    return allocation


def exchange_allocations(
    session: Session,
    from_allocation_id: int,
    to_allocation_id: int,
    amount: float,
    user_id: int,
    comment: Optional[str] = None,
) -> tuple:
    """Move ``amount`` from one dedicated allocation to another.

    Conservative "exchange": preserves the combined amount across the two
    allocations, never touches dates, operates only on dedicated (non-
    inheriting) allocations on the same resource. Inheriting children of
    either side cascade automatically via ``update_allocation``.

    Writes two paired ``AllocationTransaction(TRANSFER)`` audit rows so
    the operation is greppable as a single logical event, in addition to
    the ``EDIT`` rows produced by the underlying updates.

    Does NOT commit — caller wraps in ``management_transaction``.

    Args:
        session:             SQLAlchemy session.
        from_allocation_id:  Source allocation (debited).
        to_allocation_id:    Destination allocation (credited).
        amount:              Positive amount to transfer.
        user_id:             User performing the exchange (audit trail).
        comment:             Optional human-readable note appended to both
                             TRANSFER audit rows.

    Returns:
        Tuple[Allocation, Allocation]: (from_allocation, to_allocation)
        after the amount updates have been applied and flushed.

    Raises:
        ValueError: If allocations missing/deleted, same id, non-positive
            amount, cross-resource, or amount > from.amount.
        InheritingAllocationException: If either allocation is inheriting.
    """
    if amount <= 0:
        raise ValueError(f"Exchange amount must be positive, got {amount}")
    if from_allocation_id == to_allocation_id:
        raise ValueError("FROM and TO allocations must differ")

    from_alloc = session.get(Allocation, from_allocation_id)
    to_alloc = session.get(Allocation, to_allocation_id)
    if from_alloc is None or from_alloc.deleted:
        raise ValueError(f"FROM allocation {from_allocation_id} not found")
    if to_alloc is None or to_alloc.deleted:
        raise ValueError(f"TO allocation {to_allocation_id} not found")
    if from_alloc.is_inheriting:
        raise InheritingAllocationException(
            f"FROM allocation {from_allocation_id} is inheriting; "
            "exchanges operate only on dedicated allocations."
        )
    if to_alloc.is_inheriting:
        raise InheritingAllocationException(
            f"TO allocation {to_allocation_id} is inheriting; "
            "exchanges operate only on dedicated allocations."
        )

    from_resource_id = from_alloc.account.resource_id if from_alloc.account else None
    to_resource_id = to_alloc.account.resource_id if to_alloc.account else None
    if from_resource_id is None or to_resource_id is None:
        raise ValueError("Exchange endpoints must have valid accounts.")
    if from_resource_id != to_resource_id:
        raise ValueError("Exchange endpoints must be on the same resource.")

    if amount > from_alloc.amount:
        raise ValueError(
            f"Exchange amount ({amount}) exceeds FROM allocation amount "
            f"({from_alloc.amount})."
        )

    from_proj = from_alloc.account.project if from_alloc.account else None
    to_proj = to_alloc.account.project if to_alloc.account else None
    from_code = from_proj.projcode if from_proj else f"#{from_allocation_id}"
    to_code = to_proj.projcode if to_proj else f"#{to_allocation_id}"
    transfer_comment = f"Exchange: -{amount} {from_code} / +{amount} {to_code}"
    if comment:
        transfer_comment = f"{transfer_comment}; {comment}"

    new_from = from_alloc.amount - amount
    new_to = to_alloc.amount + amount

    update_allocation(session, from_allocation_id, user_id, amount=new_from)
    update_allocation(session, to_allocation_id, user_id, amount=new_to)

    # Paired TRANSFER audit rows (in addition to the EDIT rows that
    # update_allocation writes). They cross-reference each other via
    # related_transaction_id so the exchange is greppable as a single
    # logical operation.
    debit = AllocationTransaction(
        allocation_id=from_alloc.allocation_id,
        user_id=user_id,
        transaction_type=AllocationTransactionType.TRANSFER,
        alloc_start_date=from_alloc.start_date,
        alloc_end_date=from_alloc.end_date,
        transaction_amount=-amount,
        requested_amount=amount,
        transaction_comment=transfer_comment,
        propagated=False,
    )
    session.add(debit)
    session.flush()

    credit = AllocationTransaction(
        allocation_id=to_alloc.allocation_id,
        user_id=user_id,
        transaction_type=AllocationTransactionType.TRANSFER,
        alloc_start_date=to_alloc.start_date,
        alloc_end_date=to_alloc.end_date,
        transaction_amount=amount,
        requested_amount=amount,
        transaction_comment=transfer_comment,
        propagated=False,
        related_transaction_id=debit.allocation_transaction_id,
    )
    session.add(credit)
    session.flush()

    debit.related_transaction_id = credit.allocation_transaction_id
    session.flush()

    return from_alloc, to_alloc


def propagate_allocation_to_subprojects(
    session: Session,
    parent_allocation: Allocation,
    descendants,
    user_id: int,
    skip_existing: bool = True,
    *,
    transaction_type: AllocationTransactionType = AllocationTransactionType.CREATE,
    transaction_comment: Optional[str] = None,
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
            transaction_type,
            comment=transaction_comment or (
                f"Propagated from parent allocation #{parent_allocation.allocation_id}"
            ),
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

    # Only descendant allocations whose date range overlaps the edit target
    # count as "partitioned siblings" — allocations in other fiscal years are
    # unrelated.
    total = 0.0
    for desc in project.get_descendants():
        if not desc.active:
            continue
        acct = Account.get_by_project_and_resource(session, desc.project_id, resource_id)
        if not acct:
            continue
        for a in acct.allocations:
            if (not a.deleted
                    and a.parent_allocation_id is None
                    and date_ranges_overlap(a, allocation)):
                total += a.amount
    return total


def link_allocation_to_parent(
    session: Session,
    allocation_id: int,
    parent_allocation_id: int,
    user_id: int,
) -> Allocation:
    """
    Re-link a standalone child allocation to a parent-project allocation.

    Mirrors the parent's amount/start_date/end_date onto the child so the
    re-linked allocation is functionally indistinguishable from one created
    originally via propagate_allocation_to_subprojects(). Flushes, then
    logs a single LINK transaction.

    Raises:
        ValueError: child not found / already inheriting; parent not found /
                    itself inheriting; resource mismatch; parent project is
                    not the immediate project-parent of the child's project.
    """
    from sam.accounting.accounts import Account  # noqa: F401 — cycle guard

    child = session.get(Allocation, allocation_id)
    if not child:
        raise ValueError(f"Allocation {allocation_id} not found")
    if child.is_inheriting:
        raise ValueError(
            f"Allocation {allocation_id} is already inheriting; detach first"
        )

    parent = session.get(Allocation, parent_allocation_id)
    if not parent:
        raise ValueError(f"Parent allocation {parent_allocation_id} not found")
    if parent.deleted:
        raise ValueError(f"Parent allocation {parent_allocation_id} is deleted")
    # Note: parent MAY itself be inheriting. The deep-tree design points each
    # allocation at its *immediate* project-parent's allocation, not the root
    # (see propagate_allocation_to_subprojects' alloc_map). A grandchild
    # correctly links to an inheriting middle-tier parent.

    if not child.account or not parent.account:
        raise ValueError("Both allocations must be bound to an account")
    if child.account.resource_id != parent.account.resource_id:
        raise ValueError(
            "Cannot link allocations for different resources "
            f"(child: {child.account.resource_id}, parent: {parent.account.resource_id})"
        )

    child_proj = child.account.project
    parent_proj = parent.account.project
    if not child_proj or not parent_proj:
        raise ValueError("Both allocations' accounts must have a project")
    if child_proj.parent_id != parent_proj.project_id:
        raise ValueError(
            f"Project {parent_proj.projcode} is not the immediate parent of "
            f"{child_proj.projcode}; deep-tree links must point to the "
            f"immediate ancestor"
        )

    child.parent_allocation_id = parent.allocation_id
    child.amount = parent.amount
    child.start_date = parent.start_date
    child.end_date = parent.end_date
    session.flush()

    log_allocation_transaction(
        session, child, user_id,
        AllocationTransactionType.LINK,
        comment=f"Re-linked to parent allocation #{parent.allocation_id}",
    )
    return child

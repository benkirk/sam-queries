"""
Allocation management functions with integrated audit logging.

Administrative operations for managing allocations with automatic
audit trail creation in allocation_transaction table.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation, AllocationTransaction, AllocationTransactionType,
    InheritingAllocationException,
    LEGACY_TYPE_MAP,
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
    'get_carveout_frontier',
    'allocate_residual_to_child',
    'CarveoutFrontier',
    'date_ranges_overlap',
    'InheritingAllocationException',
    # The XRAS integration primitives. `manage/extend.py` added its own on the way
    # in and this module did not, so the stated public surface was three names
    # short of the real one.
    'log_integration_transaction',
    'supplement_allocation',
    'adjust_allocation',
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
    user_id: Optional[int],
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
        user_id: User making the change (from flask_login.current_user), or
            **None for an integration actor** — see below
        transaction_type: One of AllocationTransactionType constants
        comment: Optional custom comment
        old_values: Dict with previous values (for EDIT type) - keys: amount, start_date, end_date, description

    ``user_id=None`` means *no human did this* — an integration wrote the row on its
    own authority. ``allocation_transaction.user_id`` is nullable
    (``sam/accounting/allocations.py``) and nothing here validates or dereferences it,
    so ``None`` writes SQL NULL.

    That is not a loophole, it is the established convention for this column: legacy
    Java SAM writes NULL for every XRAS-driven transaction, and **25,048 rows in
    production carry it** (measured 2026-08-07). Reproducing it is what keeps the
    XRAS port's audit rows diffable against the ones legacy has been writing for
    years. The type hint said ``int`` only because every caller so far has been a
    web request with a logged-in user.

    Do not invent a service account to avoid the NULL. A synthetic user id would be
    indistinguishable from a real person in every report that joins this column.

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

    # B3: translate Python-side intent → legacy DB string (legacy SAM's
    # Java enum throws on anything outside {NEW, ADJUSTMENT, SUPPLEMENT,
    # EXTENSION, TRANSFER}). Tagged intents prepend "[TAG] " to the
    # comment so parse_intent() can recover the original meaning.
    #
    # TRANSITIONAL — REMOVE WHEN LEGACY SAM IS RETIRED. Once the Java
    # codebase is gone, write transaction_type directly from the enum
    # value and stop emitting [TAG] prefixes. See the retirement note
    # on AllocationTransactionType in sam.accounting.allocations.
    db_type, tag = LEGACY_TYPE_MAP[transaction_type]
    if tag is not None:
        final_comment = f"[{tag}] {final_comment}" if final_comment else f"[{tag}]"

    # transaction_amount semantics depend on the *legacy* replay rules
    # (see DateBoundedAllocationAmount.java + AllocationTransactionType.java):
    #   - NEW: setAmount(transaction_amount); field is the new total.
    #   - EXTENSION: end_date only; amount field is informational.
    #   - ADJUSTMENT / SUPPLEMENT / TRANSFER: addAmount(transaction_amount);
    #     field is a SIGNED DELTA.
    #
    # EDIT maps to ADJUSTMENT (additive), so transaction_amount = (new − old).
    # DELETE / DETACH / LINK also map to ADJUSTMENT but don't change the
    # amount — write 0.0 so legacy replay's addAmount is a no-op.
    no_amount_change = transaction_type in (
        AllocationTransactionType.DELETE,
        AllocationTransactionType.DETACH,
        AllocationTransactionType.LINK,
    )
    if no_amount_change:
        txn_amount = 0.0
    elif (transaction_type == AllocationTransactionType.EDIT
            and old_values is not None and 'amount' in old_values):
        old_amount = old_values['amount'] or 0.0
        txn_amount = float(allocation.amount) - float(old_amount)
    else:
        txn_amount = allocation.amount

    # Create transaction record (transaction_type is the legacy DB string)
    transaction = AllocationTransaction(
        allocation_id=allocation.allocation_id,
        user_id=user_id,
        transaction_type=db_type,
        alloc_start_date=allocation.start_date,
        alloc_end_date=allocation.end_date,
        transaction_amount=txn_amount,
        requested_amount=allocation.amount,
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
    comment: Optional[str] = None,
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
        comment:     Optional audit-trail comment (defaults to
                     ``'Allocation created'``).

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
        comment=comment or 'Allocation created',
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
    log_audit_row: bool = True,
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
        log_audit_row: When True (default), log this allocation's own
                 EDIT→ADJUSTMENT audit row. Set False when the *caller*
                 records this allocation's change itself with a more
                 specific row (e.g. ``exchange_allocations`` writes a
                 paired TRANSFER) — this avoids a double-counted, additive
                 row in legacy replay. Inheriting-child cascade rows are
                 ALWAYS written regardless of this flag.
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

    # Create audit trail entry for THIS allocation. Skipped when the caller
    # records the change itself with a more specific row (e.g. exchange's
    # paired TRANSFER) — otherwise legacy replay would sum two additive rows
    # for one change. The child cascade below is unaffected.
    if log_audit_row:
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

        def _cascade_to_child(child: Allocation) -> None:
            # Capture *this child's* pre-mutation values (NOT the parent's)
            # so the audit row records the child's actual delta. In a
            # divergent tree, child.amount may differ from parent.amount,
            # and the child's ADJUSTMENT.transaction_amount must be
            # (new − child_old), not (new − parent_old) — otherwise legacy
            # replay of the child's history doesn't reproduce its amount.
            child_specific_old = {f: getattr(child, f) for f in cascadable}
            for field, value in child_updates.items():
                setattr(child, field, value)
            log_allocation_transaction(
                session, child, user_id,
                AllocationTransactionType.EDIT,
                comment=comment,
                old_values=child_specific_old,
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

    Writes two paired ``AllocationTransaction(TRANSFER)`` audit rows (cross-
    linked via ``related_transaction_id``) — the sole audit row for each of
    the two dedicated allocations. The underlying ``update_allocation`` calls
    are told NOT to log their own EDIT row (``log_audit_row=False``), since a
    ``TRANSFER`` and an ``ADJUSTMENT`` are both additive in legacy replay and
    two rows per side would double-count the amount. (Inheriting children of
    either side still get their propagated cascade rows.)

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

    # log_audit_row=False: the paired TRANSFER rows below are the audit record
    # for these two allocations. Letting update_allocation also log an EDIT→
    # ADJUSTMENT would double-count under legacy replay (both are additive).
    # The inheriting-child cascade rows are still written.
    update_allocation(session, from_allocation_id, user_id, amount=new_from,
                      log_audit_row=False)
    update_allocation(session, to_allocation_id, user_id, amount=new_to,
                      log_audit_row=False)

    # Paired TRANSFER audit rows — the single additive row per allocation.
    # They cross-reference each other via related_transaction_id so the
    # exchange is greppable as a single logical operation.
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

    NOTE: Sums standalone allocations at ALL descendant depths. For the
    per-node direct-frontier decomposition used by nested trees (each node's
    residual against its own immediate carve-outs), use
    :func:`get_carveout_frontier` instead.
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


@dataclass
class CarveoutFrontier:
    """Direct-frontier decomposition of one parent allocation, per resource.

    The *frontier* is, for each direct child branch of the parent's project,
    the nearest descendant project carrying an overlapping allocation on the
    same resource — the level that actually draws from ``parent``.  Deeper
    allocations draw from their own frontier node, not from ``parent``, so
    they are deliberately not represented here (nested trees compute their
    own frontier per node).
    """
    parent: Allocation
    #: Frontier standalone allocations with a distinct amount — they carve
    #: out of (consume) the parent's total.
    carve_children: List[Allocation] = field(default_factory=list)
    #: Frontier allocations classified as pool members (linked via
    #: parent_allocation_id, or equal-amount fallback). They mirror the
    #: pool and do not consume the parent's amount.
    pool_children: List[Allocation] = field(default_factory=list)
    #: Direct child Projects whose ENTIRE branch has no overlapping
    #: allocation on this resource — candidates for a brand-new carve-out.
    open_projects: list = field(default_factory=list)
    carve_total: float = 0.0

    @property
    def raw_residual(self) -> float:
        """amount − Σ carve-outs; negative means over-carved (audit deficit)."""
        return float(self.parent.amount) - self.carve_total

    @property
    def residual(self) -> float:
        """Unallocated remainder, clamped ≥ 0 (FAIRSHARE_TREE.md leaf_weight)."""
        return max(self.raw_residual, 0.0)


def get_carveout_frontier(session: Session, allocation: Allocation) -> 'CarveoutFrontier':
    """
    Decompose ``allocation``'s direct frontier into carve-outs, pool members,
    and open (uncovered) branches, and compute its unallocated residual.

    Classification uses :func:`sam.queries.tree_audit.is_pool_member` — the
    single pool-vs-carve judgement site.  A frontier allocation linked to some
    *other* parent allocation (dirty data) is treated as a pool member: it
    mirrors another pool, counting it here would double-count, and it can
    never be a bump target (``update_allocation`` rejects inheriting rows).

    Only allocations whose date range overlaps ``allocation``'s window count
    (allocations in other fiscal years are unrelated); multiple overlapping
    rows on one frontier node all count.  Inactive projects are skipped.

    A direct child project with no allocation of its own but with deeper
    carve-outs below it is *not* an open branch — creating an allocation
    there would re-anchor the deeper carves under it.  Only fully-uncovered
    branches are offered for a new allocation.

    Callers should pass a dedicated (non-inheriting) allocation; a pool
    copy has no residual of its own.
    """
    from sam.accounting.accounts import Account
    from sam.queries.tree_audit import is_pool_member

    frontier = CarveoutFrontier(parent=allocation)

    if not allocation.account:
        return frontier

    resource_id = allocation.account.resource_id
    project = allocation.account.project
    if not project or not project.has_children:
        return frontier

    def _walk(node) -> bool:
        """Classify node's branch; True if any allocation was found in it."""
        acct = Account.get_by_project_and_resource(session, node.project_id, resource_id)
        cands = [
            a for a in (acct.allocations if acct else [])
            if not a.deleted and date_ranges_overlap(a, allocation)
        ]
        if cands:
            # This node is the frontier for its branch — deeper allocations
            # draw from it, not from ``allocation``. Stop descending.
            for a in cands:
                linked = a.parent_allocation_id is not None
                if is_pool_member(linked=linked,
                                  child_amount=float(a.amount),
                                  parent_amount=float(allocation.amount)):
                    frontier.pool_children.append(a)
                else:
                    frontier.carve_children.append(a)
                    frontier.carve_total += float(a.amount)
            return True

        covered = False
        for grandchild in node.children:
            if grandchild.is_active:
                covered = _walk(grandchild) or covered
        return covered

    for child in project.children:
        if not child.is_active:
            continue
        if not _walk(child):
            frontier.open_projects.append(child)

    return frontier


def allocate_residual_to_child(
    session: Session,
    parent_allocation_id: int,
    user_id: int,
    *,
    amount: float,
    target_allocation_id: Optional[int] = None,
    target_project_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> Allocation:
    """
    Allocate part of a parent allocation's carve-out residual downward.

    The parent's amount is UNCHANGED — under subdivided-award semantics its
    amount is already the subtree total, so giving a sub-project more shrinks
    the unallocated residual implicitly (residual = amount − Σ carve-outs).
    Exactly one target must be given:

      * ``target_allocation_id`` — bump an existing frontier carve-out
        allocation (one EDIT→ADJUSTMENT audit row, signed delta);
      * ``target_project_id`` — create a new standalone allocation on a
        fully-uncovered direct child branch, mirroring the parent's dates
        (one CREATE→NEW audit row).

    No transaction row is written for the parent (its amount does not
    change; an additive row would corrupt legacy replay).  The child row's
    comment carries the cross-reference instead.

    NOTE: Does NOT commit — wrap in ``management_transaction``.

    Raises:
        InheritingAllocationException: parent is a pool copy (no residual).
        ValueError: invalid amount/target, over-carved parent, or amount
            exceeding the unallocated residual.
    """
    if amount is None or amount <= 0:
        raise ValueError(f"Amount must be positive, got {amount}")

    if (target_allocation_id is None) == (target_project_id is None):
        raise ValueError(
            "Exactly one of target_allocation_id or target_project_id is required")

    parent = session.get(Allocation, parent_allocation_id)
    if not parent or parent.deleted:
        raise ValueError(f"Allocation {parent_allocation_id} not found")
    if parent.is_inheriting:
        raise InheritingAllocationException(
            f"Allocation {parent_allocation_id} is a shared (inheriting) copy; "
            "the unallocated residual is defined only for dedicated allocations."
        )

    frontier = get_carveout_frontier(session, parent)

    if frontier.raw_residual < 0:
        raise ValueError(
            f"Sub-project carve-outs ({frontier.carve_total:g}) already exceed "
            f"this allocation ({float(parent.amount):g}). Resolve the deficit "
            "first — see 'sam-admin project --audit-trees'."
        )
    if amount > frontier.residual:
        raise ValueError(
            f"Amount ({amount:g}) exceeds the unallocated residual "
            f"({frontier.residual:g})."
        )

    parent_projcode = parent.account.project.projcode
    sub_comment = (f"Sub-allocated +{amount:g} from {parent_projcode} "
                   f"allocation #{parent.allocation_id}")
    if comment:
        sub_comment = f"{sub_comment}; {comment}"

    def _guard_equal_amount(resulting_amount: float) -> None:
        # A child whose amount EQUALS the parent's is classified as a pool
        # member (is_pool_member's equal-amount fallback), so landing on
        # exact equality would silently flip this carve-out into a shared
        # pool in the audit and the fairshare tree. Refuse the ambiguous
        # end state rather than special-case it downstream.
        if resulting_amount == float(parent.amount):
            raise ValueError(
                f"Resulting amount ({resulting_amount:g}) would equal the "
                f"parent allocation's ({float(parent.amount):g}), which reads "
                "as a shared-pool member rather than a carve-out. Choose a "
                "different amount."
            )

    if target_allocation_id is not None:
        target = next((a for a in frontier.carve_children
                       if a.allocation_id == target_allocation_id), None)
        if target is None:
            raise ValueError(
                f"Allocation {target_allocation_id} is not a carve-out on "
                f"{parent_projcode}'s direct frontier for this resource."
            )
        _guard_equal_amount(float(target.amount) + amount)
        return update_allocation(
            session, target.allocation_id, user_id,
            amount=float(target.amount) + amount,
            comment=sub_comment,
        )

    target_project = next((p for p in frontier.open_projects
                           if p.project_id == target_project_id), None)
    if target_project is None:
        raise ValueError(
            f"Project {target_project_id} is not an uncovered direct "
            f"sub-project branch of {parent_projcode} for this resource."
        )
    _guard_equal_amount(amount)
    return create_allocation(
        session,
        project_id=target_project.project_id,
        resource_id=parent.account.resource_id,
        amount=amount,
        start_date=parent.start_date,
        end_date=parent.end_date,
        user_id=user_id,
        comment=sub_comment,
    )


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
            f"{child_proj.projcode}; an allocation can only link to its "
            f"immediate parent project's allocation"
        )

    child.parent_allocation_id = parent.allocation_id
    child.amount = parent.amount
    child.start_date = parent.start_date
    child.end_date = parent.end_date
    session.flush()

    # LINK is a 0.0 topology marker, not an amount event: a re-linked child
    # becomes an inheriting (shared-pool) member whose amount is, by definition,
    # the parent's — kept in sync by the parent's cascades. The child's prior
    # standalone amount is intentionally adopted-as-is, so no delta is recorded.
    log_allocation_transaction(
        session, child, user_id,
        AllocationTransactionType.LINK,
        comment=f"Re-linked to parent allocation #{parent.allocation_id}",
    )
    return child


def log_integration_transaction(
    session: Session,
    allocation: Allocation,
    transaction_type: str,
    *,
    comment: Optional[str] = None,
    transaction_amount: Optional[float] = None,
    requested_amount: Optional[float] = None,
    alloc_start_date: Optional[datetime] = None,
    alloc_end_date: Optional[datetime] = None,
    auth_at_panel_mtg: Optional[bool] = None,
    propagated: bool = False,
) -> AllocationTransaction:
    """Write an audit row with the **exact** column shape an integration needs.

    :func:`log_allocation_transaction` snapshots the allocation's current state into
    every row: ``alloc_start_date``, ``alloc_end_date``, ``requested_amount`` and a
    ``transaction_amount`` derived from the type. That is the right default for a
    human-driven edit, where the snapshot is the record of what the operator saw.

    It is the wrong shape for the XRAS and AMIE integrations, whose rows have a
    different and long-established convention — measured, not assumed:

    ==============  =====================================================================
    ``EXTENSION``   20,603 of 20,618 production rows carry NULL ``transaction_amount``,
                    ``requested_amount`` *and* ``alloc_start_date``. Only the new
                    ``alloc_end_date`` is meaningful.
    ``SUPPLEMENT``  all 3,203 integration-written rows carry NULL ``alloc_start_date``
                    and ``alloc_end_date``; ``transaction_amount`` is the **increment**,
                    not the new total; 2,752 also carry NULL ``requested_amount``.
    ==============  =====================================================================

    So this delegates to the shared logger — keeping one insert site, one place for the
    ``LEGACY_TYPE_MAP`` translation, and one place any future audit hook would go — and
    then sets the informational columns explicitly. Every column it can write is a
    keyword, defaulting to ``None``, so a caller states the whole shape at the call site
    rather than inheriting a default it did not think about.

    ``user_id`` is always ``NULL``: that is the integration-actor convention, and the
    reason this function exists rather than a flag on the shared one. See
    :func:`log_allocation_transaction` for why a synthetic service account is the wrong
    answer.

    Does NOT commit; the caller owns the transaction.
    """
    txn = log_allocation_transaction(
        session, allocation, None, transaction_type,
        comment=comment, propagated=propagated,
    )
    txn.transaction_amount = transaction_amount
    txn.requested_amount = requested_amount
    txn.alloc_start_date = alloc_start_date
    txn.alloc_end_date = alloc_end_date
    if auth_at_panel_mtg is not None:
        txn.auth_at_panel_mtg = auth_at_panel_mtg
    session.flush()
    return txn


def _add_to_subtree(
    session: Session,
    allocation: Allocation,
    *,
    amount: float,
    comment: Optional[str],
    transaction_type: AllocationTransactionType,
    auth_at_panel_mtg: Optional[bool] = None,
) -> List[Allocation]:
    """Add *amount* to an allocation and every inheriting child, logging one row each.

    The mechanism shared by :func:`supplement_allocation` and
    :func:`adjust_allocation`, whose bodies were byte-identical apart from the
    transaction type and the panel flag. They stay as two public functions because
    the *policy* differs and their docstrings are where that is recorded — this is
    only the part that cannot be allowed to drift.

    Legacy shape, reproduced: ``disinherit()`` before ``supplement()``, routed
    through :func:`detach_allocation` so it leaves the audit row legacy never wrote;
    then ``TreeWalker.walk`` so every inheriting child receives the same increment
    and its own row, with ``propagated`` false on all of them.

    ⚠️ **``auth_at_panel_mtg`` defaults to ``None`` and callers must leave it that
    way when the column should be NULL.** :func:`log_integration_transaction` sets
    it only ``if auth_at_panel_mtg is not None``, so passing ``False`` "to be
    explicit" writes 0 where legacy writes NULL — different bytes on an audit row.
    ``buildAdjustAllocationCommand`` never sets it; the supplement one does. Pinned
    by ``test_auth_at_panel_mtg_is_null_not_zero``.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.
    """
    if allocation.is_inheriting:
        detach_allocation(session, allocation.allocation_id, None)

    modified: List[Allocation] = []
    subtree: List[Allocation] = []
    allocation._walk_tree(lambda node: subtree.append(node))

    for node in subtree:
        node.amount = float(node.amount or 0.0) + float(amount)
        log_integration_transaction(
            session, node, transaction_type,
            comment=comment,
            transaction_amount=float(amount),
            auth_at_panel_mtg=auth_at_panel_mtg,
        )
        modified.append(node)

    session.flush()
    return modified


def supplement_allocation(
    session: Session,
    allocation: Allocation,
    *,
    amount: float,
    comment: Optional[str] = None,
    auth_at_panel_mtg: bool = False,
) -> List[Allocation]:
    """Add ``amount`` to an allocation and its child subtree. Additive, not absolute.

    ⚠️ **The single most important porting semantic in the XRAS integration.**
    ``awardedAmount`` on a Supplement action is the **increment**, not the new total —
    legacy's ``AllocationTransactionType.SUPPLEMENT`` replays as
    ``addAmount(transaction_amount)``. :func:`update_allocation` *sets* ``amount``, so
    using it here would overwrite a 4,000,000-core-hour allocation with a 250,000-hour
    supplement. There was no additive primitive before this one.

    The subtree walk is legacy's: ``Allocation.supplement`` calls
    ``TreeWalker.walk``, so every inheriting child receives the same increment and its
    own ``SUPPLEMENT`` row. ``propagated`` stays false on all of them, matching the
    3,203 integration-written rows in production.

    Detaching first is also legacy's (``disinherit()`` before ``supplement()``), routed
    through :func:`detach_allocation` so it leaves the audit row legacy never wrote.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Args:
        session: SQLAlchemy session.
        allocation: the allocation to supplement; its child subtree comes too.
        amount: the increment. Legacy's factories gate on ``> 0`` before calling;
            the Adjustment handler deliberately does not (see its module docstring).
        comment: ``transaction_comment``, normally the normalized
            ``resources[].comments`` from the wire, or ``None``.
        auth_at_panel_mtg: the CSL/CHAP rule's answer. Set on 1,264 of the 3,203
            integration rows, so it is not vestigial.

    Returns:
        Every node modified, root first.
    """
    return _add_to_subtree(session, allocation, amount=amount, comment=comment,
                           transaction_type=AllocationTransactionType.SUPPLEMENT,
                           auth_at_panel_mtg=auth_at_panel_mtg)


def adjust_allocation(
    session: Session,
    allocation: Allocation,
    *,
    amount: float,
    comment: Optional[str] = None,
) -> List[Allocation]:
    """Apply a signed correction to an allocation and its child subtree.

    The sibling of :func:`supplement_allocation`, and structurally near-identical —
    legacy's ``AdjustProjectAllocationActionCommandsFactory`` is a near-verbatim copy of
    the supplement one. Two differences, both real:

    * ``ADJUSTMENT`` rather than ``SUPPLEMENT``. Both replay as
      ``addAmount(transaction_amount)``, so the arithmetic is the same; the type is what
      an operator filters on.
    * **No ``auth_at_panel_mtg``.** ``buildAdjustAllocationCommand`` never sets it,
      where the supplement one does.

    ⚠️ **``amount`` may be negative, and that is the point.** Legacy gates its adjust
    factory on ``> 0`` — the same copy-pasted guard as supplement — which silently drops
    the one thing an adjustment exists to do. Combined with legacy defect 4 (it tests
    ``"Adjust"`` while the wire says ``"Adjustment"``) that handler has never serviced a
    single action, so nothing depends on the guard. Callers gate as they see fit; this
    primitive does not.

    It does **not** guard the resulting amount either — that is the handler's decision,
    because "may this allocation go negative" is a policy question and this is a
    mechanism. Legacy's ``verifyValidateState`` checks only the end date, so it has no
    such guard anywhere.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Returns:
        Every node modified, root first.
    """
    return _add_to_subtree(session, allocation, amount=amount, comment=comment,
                           transaction_type=AllocationTransactionType.ADJUSTMENT)

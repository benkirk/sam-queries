"""What a handler decided to do, as records rather than positional tuples.

Assembly plans; execution applies. Carried as bare tuples that meant seven
construction sites, seven unpack sites, and three different field orders for
the same five values -- two of which describe the identical operation and
disagree on where the comment goes. A mis-ordered five-tuple type-checks,
unpacks, and writes the comment into the start-date slot; nothing but
positional luck kept them apart.

Same failure mode as the ``auth_at_panel_mtg`` bug, caught one step earlier.
Named fields cannot be reordered by accident, and ``frozen=True`` means a plan
cannot be mutated between the check and the write.

WARNING: a handler keeps its plan as ONE flat ordered list, not a bucket per
kind. ``UpdateHandler`` emits up to three steps for a single resource and
applies them in the order the factory emitted them, which is legacy-faithful.
Grouping by type here would silently reorder writes against one allocation.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sam.manage.allocations import adjust_allocation, supplement_allocation
from sam.manage.extend import extend_account_allocation

__all__ = [
    'PlannedCreate',
    'PlannedSupplement',
    'PlannedAdjust',
    'PlannedExtend',
    'apply_plan',
]


@dataclass(frozen=True)
class PlannedCreate:
    """Create a new allocation for *resource*.

    ``panel_authorized`` rides on the record rather than being read off the handler,
    and that is load-bearing: Update decides it **per resource** (only its ADD branch
    marks), so a handler-level read would mark rows the plan said not to.
    """

    resource: Any
    amount: float
    comment: Optional[str]
    start: datetime
    end: Optional[datetime]
    panel_authorised: bool = False


@dataclass(frozen=True)
class PlannedSupplement:
    """Add to an existing allocation and its inheriting subtree."""

    allocation: Any
    amount: float
    comment: Optional[str]
    panel_authorised: bool = False


@dataclass(frozen=True)
class PlannedAdjust:
    """Apply a signed correction to an existing allocation.

    WARNING: Carries **no** ``panel_authorized`` field, deliberately.
    ``buildAdjustAllocationCommand`` never sets ``auth_at_panel_mtg`` where the
    supplement one does, and ``log_integration_transaction`` writes the column only
    when the value is not ``None`` — so the absence of the field here is what keeps
    the column NULL rather than 0. Do not add it "for symmetry".
    """

    allocation: Any
    amount: float
    comment: Optional[str]


@dataclass(frozen=True)
class PlannedExtend:
    """Move an existing allocation's end date out."""

    allocation: Any
    new_end: datetime
    comment: Optional[str]


def _apply_create(handler, project, step: PlannedCreate) -> None:
    handler.create_allocation_for(
        project, step.resource, amount=step.amount, start=step.start,
        end=step.end, comment=step.comment,
        panel_authorised=step.panel_authorised)


def _apply_supplement(handler, project, step: PlannedSupplement) -> None:
    supplement_allocation(handler.session, step.allocation, amount=step.amount,
                          comment=step.comment,
                          auth_at_panel_mtg=step.panel_authorised)


def _apply_adjust(handler, project, step: PlannedAdjust) -> None:
    # No `auth_at_panel_mtg` — see PlannedAdjust's docstring.
    adjust_allocation(handler.session, step.allocation, amount=step.amount,
                      comment=step.comment)


def _apply_extend(handler, project, step: PlannedExtend) -> None:
    extend_account_allocation(handler.session, step.allocation,
                              new_end=step.new_end, comment=step.comment)


#: Plan type -> how to apply it. A table rather than an ``elif`` chain on a string
#: tag: adding a plan kind that nothing can apply is then a ``KeyError`` naming the
#: type, not a step silently skipped by a chain that fell through.
_APPLIERS = {
    PlannedCreate: _apply_create,
    PlannedSupplement: _apply_supplement,
    PlannedAdjust: _apply_adjust,
    PlannedExtend: _apply_extend,
}


def apply_plan(handler, step, *, project=None) -> None:
    """Apply one planned step through *handler*.

    *project* is explicit rather than taken from ``handler.project`` because the New
    handler creates its project inside the transaction and passes that one — the same
    reason :meth:`ActionHandler.create_allocation_for` takes it explicitly.
    """
    try:
        applier = _APPLIERS[type(step)]
    except KeyError:                                 # pragma: no cover - programming error
        raise TypeError(
            f'no applier registered for plan step {type(step).__name__}; '
            f'add one to sam.xras.handlers._plans._APPLIERS'
        ) from None
    applier(handler, project, step)
